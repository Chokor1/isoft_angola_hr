# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The payroll lifecycle state machine.

Payroll Entry is the control document. Every operation that moves payroll forward —
calculating, submitting for approval, approving, rejecting, posting, releasing for
payment, paying, closing, cancelling — goes through :func:`perform`, which is the only
place allowed to change ``status``.

Why a single table instead of Frappe's Workflow doctype
------------------------------------------------------
Frappe Workflow decides transitions from role alone. Payroll needs three more things it
cannot express: *who prepared this document* (no self-approval), *whether the numbers
still match what was approved* (approval integrity), and *whether the ledger already
holds the entries* (safe cancellation). Encoding those as a Python transition table
keeps one readable definition and makes every rule directly testable.

    Draft ──calculate──► Calculated ──submit_for_approval──► Pending Approval
                              ▲                                   │
                              │                        ┌──approve─┴──reject──┐
                              └──────calculate─────────┤                     ▼
                                                       ▼                 Rejected
                                                   Approved
                                                       │ post
                                                       ▼
                                                    Posted ──release_for_payment──► Payment Ready
                                                                                          │ pay
                                                                                          ▼
                                                                                        Paid ──close──► Closed
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #
DRAFT = "Draft"
CALCULATED = "Calculated"
PENDING_APPROVAL = "Pending Approval"
REJECTED = "Rejected"
APPROVED = "Approved"
POSTED = "Posted"
PAYMENT_READY = "Payment Ready"
PAID = "Paid"
CLOSED = "Closed"
CANCELLED = "Cancelled"

STATES = (DRAFT, CALCULATED, PENDING_APPROVAL, REJECTED, APPROVED, POSTED, PAYMENT_READY,
          PAID, CLOSED, CANCELLED)

#: States in which the payroll result is frozen: its salary slips may no longer be
#: recalculated or edited, because somebody has already approved those exact numbers.
LOCKED_STATES = frozenset({APPROVED, POSTED, PAYMENT_READY, PAID, CLOSED})

#: States in which the entry still occupies its payroll period for duplicate detection.
ACTIVE_STATES = frozenset(set(STATES) - {CANCELLED})

# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
CALCULATE = "calculate"
SUBMIT_FOR_APPROVAL = "submit_for_approval"
APPROVE = "approve"
REJECT = "reject"
POST = "post"
RELEASE_FOR_PAYMENT = "release_for_payment"
PAY = "pay"
CLOSE = "close"
CANCEL = "cancel"

_TRANSITIONS = {
	CALCULATE: {
		"from": (DRAFT, CALCULATED, REJECTED),
		"to": CALCULATED,
		"permission": perms.PAYROLL_CALCULATE,
		"stamp": ("prepared_by", "prepared_at"),
		"label": "Calculate Payroll",
	},
	SUBMIT_FOR_APPROVAL: {
		"from": (CALCULATED,),
		"to": PENDING_APPROVAL,
		"permission": perms.PAYROLL_SUBMIT_FOR_APPROVAL,
		"stamp": ("submitted_by", "submitted_at"),
		"label": "Submit for Approval",
	},
	APPROVE: {
		"from": (PENDING_APPROVAL,),
		"to": APPROVED,
		"permission": perms.PAYROLL_APPROVE,
		"stamp": ("approved_by", "approved_at"),
		"label": "Approve Payroll",
	},
	REJECT: {
		"from": (PENDING_APPROVAL,),
		"to": REJECTED,
		"permission": perms.PAYROLL_REJECT,
		"stamp": ("rejected_by", "rejected_at"),
		"label": "Reject Payroll",
	},
	POST: {
		"from": (APPROVED,),
		"to": POSTED,
		"permission": perms.PAYROLL_POST,
		"stamp": ("posted_by", "posted_at"),
		"label": "Post Accounting",
	},
	RELEASE_FOR_PAYMENT: {
		"from": (POSTED,),
		"to": PAYMENT_READY,
		"permission": perms.PAYROLL_CONFIRM_PAYMENT,
		"stamp": ("payment_authorized_by", "payment_authorized_at"),
		"label": "Release for Payment",
	},
	PAY: {
		"from": (PAYMENT_READY,),
		"to": PAID,
		"permission": perms.PAYROLL_CONFIRM_PAYMENT,
		"stamp": (None, "paid_at"),
		"label": "Confirm Payment",
	},
	CLOSE: {
		"from": (PAID,),
		"to": CLOSED,
		"permission": perms.PAYROLL_CLOSE,
		"stamp": ("closed_by", "closed_at"),
		"label": "Close Payroll",
	},
	CANCEL: {
		# Cancellable from EVERY live state, including Payment Ready, Paid and Closed.
		# A payroll that turns out to be wrong must always have a way back, and the
		# controlled way is cancellation; the real protection is
		# ``_assert_payroll_cancellable``, which refuses while the ledger still holds the
		# payroll, so the correction order (cancel payments → cancel accrual → cancel
		# payroll) is enforced by the books rather than by hiding the button. There is no
		# path that simply reopens a run for editing.
		"from": (DRAFT, CALCULATED, PENDING_APPROVAL, REJECTED, APPROVED, POSTED,
		         PAYMENT_READY, PAID, CLOSED),
		"to": CANCELLED,
		"permission": perms.PAYROLL_CANCEL,
		"stamp": ("cancelled_by", "cancelled_at"),
		"label": "Cancel Payroll",
	},
}


def transition_labels():
	# Translated on read: a _() in the table freezes at import time.
	return {a: _(spec["label"]) for a, spec in _TRANSITIONS.items()}


# --------------------------------------------------------------------------- #
# Lifecycle fields — written by perform() and nothing else
# --------------------------------------------------------------------------- #
#: The state, every audit stamp and the approval snapshot. Frappe marks them read-only in
#: the form, but a read-only field is a presentation hint: ``frappe.client.set_value``,
#: a REST ``PUT`` and ``frappe.client.save`` all accepted them from anybody holding
#: *write* on the DocType. One such call marked a payroll Paid — or Approved, with a
#: forged approver — with no approval and no ledger entry behind it. These fields now
#: change only inside :class:`transition`, which only :func:`perform` and
#: :func:`refresh_payment_state` open.
LIFECYCLE_FIELDS = (
	"status",
	"prepared_by", "prepared_at",
	"submitted_by", "submitted_at",
	"approved_by", "approved_at",
	"rejected_by", "rejected_at",
	"posted_by", "posted_at",
	"payment_authorized_by", "payment_authorized_at",
	"paid_at",
	"closed_by", "closed_at",
	"cancelled_by", "cancelled_at",
	"approval_fingerprint",
	"approved_employees", "approved_gross", "approved_employee_inss", "approved_employer_inss",
	"approved_irt", "approved_net", "approved_employer_cost",
)

_NUMERIC_LIFECYCLE = frozenset({
	"approved_employees", "approved_gross", "approved_employee_inss", "approved_employer_inss",
	"approved_irt", "approved_net", "approved_employer_cost",
})
_DATETIME_LIFECYCLE = frozenset({f for f in LIFECYCLE_FIELDS if f.endswith("_at")})

_TRANSITION_FLAG = "isoft_payroll_transition"


class transition(object):
	"""Context manager under which a Payroll Entry may be saved with changed lifecycle
	fields. Opened by :func:`perform` and :func:`refresh_payment_state` only — never by an
	endpoint, never by the dashboard. It is a request flag, so it is unreachable over HTTP:
	the only way to set it is to run this module's own code."""

	def __enter__(self):
		self._previous = frappe.flags.get(_TRANSITION_FLAG)
		frappe.flags[_TRANSITION_FLAG] = True
		return self

	def __exit__(self, *exc):
		frappe.flags[_TRANSITION_FLAG] = self._previous
		return False


def in_transition():
	return bool(frappe.flags.get(_TRANSITION_FLAG))


def _lifecycle_value(field, value):
	"""Normalise for comparison: the incoming document may carry strings (REST), the stored
	one carries typed values, and Frappe casts only after ``validate`` has run."""
	if value in (None, ""):
		return None
	if field in _NUMERIC_LIFECYCLE:
		return flt(value, 2)
	if field in _DATETIME_LIFECYCLE:
		try:
			return str(get_datetime(value))
		except Exception:
			return str(value)
	return str(value)


def assert_lifecycle_untouched(doc):
	"""Refuse a save that changes a lifecycle field outside :func:`perform`.

	Called first thing in ``IsoftPayrollEntry.validate``, so it covers every channel that
	ends in ``Document.save``: the Desk form, ``frappe.client.set_value`` /
	``frappe.client.save`` / ``frappe.client.insert``, the REST resource API, and any
	Python that saves the document by hand. It is a channel guard, not a role guard —
	**Administrator is not exempt**: the only sanctioned writer is the workflow, whatever
	the user's roles. (Tests that need a state without walking the workflow write it with
	``frappe.db.set_value``, which is SQL and does not pass through ``validate``.)

	A brand-new entry may say Draft and name its creator as preparer (``set_defaults``
	does exactly that); it may not be born approved, posted or paid.
	"""
	if in_transition():
		return

	if doc.is_new():
		offending = []
		for field in LIFECYCLE_FIELDS:
			value = _lifecycle_value(field, doc.get(field))
			if value is None:
				continue
			if field == "status" and value == DRAFT:
				continue
			if field == "prepared_by" and value in (frappe.session.user, doc.get("owner")):
				continue
			offending.append(field)
	else:
		before = doc.get_doc_before_save()
		if before is None:
			# No stored copy to compare with (e.g. a stale document object): re-read.
			before = frappe.get_doc(doc.doctype, doc.name)
		offending = [
			field for field in LIFECYCLE_FIELDS
			if _lifecycle_value(field, doc.get(field)) != _lifecycle_value(field, before.get(field))
		]

	if offending:
		frappe.throw(
			_("The payroll lifecycle cannot be edited directly ({0}). Use the payroll "
			  "workflow actions — calculate, submit for approval, approve, post, release, "
			  "pay, close or cancel — which record who did what and when.").format(
				", ".join(offending)),
			frappe.PermissionError,
			title=_("Payroll Lifecycle Protected"),
		)


def assert_slip_in_run(entry_name, slip_name):
	"""A salary slip counts only if the payroll run lists it.

	The approval snapshot, the posting loop and the bank file all read the run's
	``employees`` table. A slip that merely *points at* an approved run — a Link field
	anybody with write access could set — was never approved by anyone, so it may not be
	submitted, posted or paid.
	"""
	if not entry_name or not slip_name:
		frappe.throw(
			_("Salary slip {0} does not belong to a payroll run.").format(slip_name or "?"),
			frappe.PermissionError, title=_("Not Part of a Payroll Run"))
	listed = frappe.db.exists("Isoft Payroll Employee", {
		"parenttype": "Isoft Payroll Entry", "parent": entry_name, "salary_slip": slip_name})
	if not listed:
		frappe.throw(
			_("Salary slip {0} is not listed in payroll {1}. Only slips generated by the run "
			  "are covered by its approval.").format(frappe.bold(slip_name), frappe.bold(entry_name)),
			frappe.PermissionError, title=_("Not Part of a Payroll Run"))
	return True


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _flag(fieldname, default=1):
	"""Read a control checkbox, treating "never configured" as the SAFE default.

	The guard used to be written here against ``get_single_value``, which casts a Check
	to 0 — so "never configured" and "switched off" were the same value and the safe
	default never applied. :func:`separation_required` reads the stored row instead.
	"""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import separation_required
	return separation_required(fieldname, default)


def hr_self_approval_allowed():
	"""The visible "Allow HR self-approval" switch (default OFF)."""
	return bool(_flag("allow_hr_self_approval", 0))


def requires_separate_approval():
	"""The submitter may not approve — unless HR self-approval has been switched on.

	Two switches, one rule: ``require_separate_payroll_approval`` is the original
	control (an explicit 0 relaxes it, see test_p39) and ``allow_hr_self_approval`` is
	the switch the operating model asks for by name. Either relaxation permits
	self-approval; the safe default of both requires a second person.
	"""
	if hr_self_approval_allowed():
		return False
	return _flag("require_separate_payroll_approval", 1)


def requires_separate_payment_approval():
	return _flag("require_separate_payment_approval", 1)


def posting_requires_finance():
	"""Posting is Finance work: the preparer may not post, and Accounts Manager held by
	HR staff does not count (permissions.is_payroll_finance). Default ON."""
	return bool(_flag("payroll_posting_requires_finance", 1))


def payment_requires_finance():
	"""Releasing, generating the bank file and confirming payment are Finance work.
	Default ON."""
	return bool(_flag("payroll_payment_requires_finance", 1))


#: Pseudo-action for the bank file: not a state transition, but a Finance action whose
#: actor rules are those of payment.
EXPORT_BANK = "export_bank"

#: Who prepared this payroll. ``prepared_by`` is stamped on creation (set_defaults) and
#: again by every calculation; ``submitted_by`` by the submission for approval.
_PREPARER_FIELDS = ("prepared_by", "submitted_by")


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #
def state_of(entry):
	"""Current lifecycle state. Entries created before Phase 2 carry no status; they are
	read as Draft (nothing was ever approved) so no historical record silently gains an
	approval it never had."""
	return entry.get("status") or DRAFT


class quiet(object):
	"""Discard the messages queued by a check whose refusal is caught, not shown.

	``frappe.throw`` queues its message for the client *before* raising. The screen
	helpers below ask "may this user do X?" by calling the same guards the endpoints use
	and catching the refusal — and every caught refusal still reached the browser as a
	pop-up ("You are not authorised to perform this operation (payroll.export_bank)")
	while an HR user was merely opening a run. Rendering a button is not an attempt to
	press it; the queued message is dropped. The rules themselves are untouched.
	"""

	def __enter__(self):
		self._n = len(frappe.message_log or [])
		return self

	def __exit__(self, *exc):
		log = frappe.message_log
		if log is not None and len(log) > self._n:
			del log[self._n:]
		return False


def allowed_actions(entry, user=None):
	"""Actions this user could perform on this entry right now — used to render buttons.
	The buttons are a convenience; :func:`perform` re-checks everything."""
	state = state_of(entry)
	out = []
	for action, spec in _TRANSITIONS.items():
		if state not in spec["from"]:
			continue
		if not perms.can(spec["permission"], user=user):
			continue
		if not perms.can_company(entry.get("company"), user=user):
			continue
		with quiet():
			try:
				_check_actor(entry, action, user=user)
			except frappe.ValidationError:
				continue
		out.append(action)
	return out


#: What has to happen next, and who has to do it. Users should never have to translate a
#: status code into an action — the document should say what it is waiting for.
_NEXT_STEP = {
	DRAFT: ("calculate", "Payroll Officer", "Calculate the payroll to generate the salary slips."),
	CALCULATED: ("submit_for_approval", "Payroll Officer",
	             "Review the calculation and submit it for approval."),
	PENDING_APPROVAL: ("approve", "Payroll Manager",
	                   "Review the totals and approve or reject the payroll."),
	REJECTED: ("calculate", "Payroll Officer",
	           "Correct what the approver reported and recalculate."),
	APPROVED: ("post", "Finance", "Submit the salary slips and post the accounting."),
	POSTED: ("release_for_payment", "Finance",
	         "Check the bank details and release the payroll for payment."),
	PAYMENT_READY: ("pay", "Finance",
	                "Generate the bank file and post the salary payments."),
	PAID: ("close", "Finance", "Reconcile and close the payroll period."),
	CLOSED: (None, None, "This payroll is closed. Corrections require cancellation."),
	CANCELLED: (None, None, "This payroll was cancelled."),
}


def next_step(entry):
	"""The state, the next action, who must take it, and what currently blocks it."""
	state = state_of(entry)
	action, who, description = _NEXT_STEP.get(state, (None, None, None))
	# Translated HERE: _NEXT_STEP is a module-level constant, so a _() inside it
	# would resolve once at import and freeze the guidance in the boot language.
	who = _(who) if who else who
	description = _(description) if description else description
	blockers = []
	if action:
		with quiet():
			try:
				assert_transition(entry, action)
			except (frappe.ValidationError, frappe.PermissionError) as exc:
				blockers.append(frappe.utils.strip_html(str(exc)))
	return {
		"state": state,
		"state_label": _(state),
		"next_action": action,
		"next_action_label": _(_TRANSITIONS[action]["label"]) if action else None,
		"responsible": who,
		"description": description,
		"blockers": blockers,
		"can_act_now": bool(action) and not blockers,
	}


def preparers_of(entry):
	"""Every identity that prepared this payroll: creator, calculator, submitter."""
	return {entry.get(f) for f in _PREPARER_FIELDS if entry.get(f)}


def _check_actor(entry, action, user=None):
	"""Segregation of duties. Roles alone are not enough — one person can hold several,
	so the identities that prepared and approved the payroll are compared with the
	identity acting now. Administrator is not exempt from any of these: if
	Administrator prepared the run, Administrator may not approve, post or pay it."""
	user = user or frappe.session.user

	if action == APPROVE and requires_separate_approval():
		preparer = entry.get("submitted_by") or entry.get("prepared_by")
		if preparer and preparer == user:
			frappe.throw(
				_("You cannot approve a payroll you prepared. Payroll submitted by {0} must "
				  "be approved by a different user.").format(preparer),
				title=_("Self-Approval Blocked"),
			)

	# The preparer never posts their own payroll (F-03). Holding Accounts Manager as
	# well changes nothing: this compares identities, not roles.
	if action == POST and posting_requires_finance() and user in preparers_of(entry):
		frappe.throw(
			_("You cannot post a payroll you prepared. Payroll {0} was prepared by {1}; "
			  "posting it to the ledger is a Finance action for a different user.").format(
				entry.get("name"), ", ".join(sorted(preparers_of(entry)))),
			title=_("Posting Segregation"),
		)

	if action in (RELEASE_FOR_PAYMENT, PAY, EXPORT_BANK):
		if requires_separate_payment_approval():
			approver = entry.get("approved_by")
			if approver and approver == user:
				frappe.throw(
					_("You cannot authorise payment of a payroll you approved. Payroll approved "
					  "by {0} must be paid by a different user.").format(approver),
					title=_("Payment Segregation"),
				)
		if payment_requires_finance() and user in preparers_of(entry):
			frappe.throw(
				_("You cannot release, export or pay a payroll you prepared. Payroll {0} was "
				  "prepared by {1}; payment is a Finance action for a different user.").format(
					entry.get("name"), ", ".join(sorted(preparers_of(entry)))),
				title=_("Payment Segregation"),
			)


def check_actor(entry, action, user=None):
	"""Public form of the identity rules, for the endpoints that act on a payroll
	without changing its state (posting one slip, generating the bank file, recording
	the bank's answer)."""
	_check_actor(entry, action, user=user)
	return True


def assert_transition(entry, action, user=None):
	"""Validate a transition without performing it: state, role, company and actor."""
	spec = _TRANSITIONS.get(action)
	if not spec:
		frappe.throw(_("Unknown payroll action {0}.").format(action))

	# Authorisation is checked BEFORE the state. A user who may not approve payroll at all
	# must be told that, not given a state hint about a document they cannot act on.
	perms.require(spec["permission"], user=user)
	perms.require_company(entry.get("company"), user=user)

	state = state_of(entry)
	if state not in spec["from"]:
		frappe.throw(
			_("Cannot {0} a payroll that is {1}. Allowed only when the payroll is: {2}.").format(
				_(spec["label"]), _(state), ", ".join(_(s) for s in spec["from"])),
			title=_("Invalid Payroll State"),
		)
	_check_actor(entry, action, user=user)
	return spec


def perform(entry, action, user=None, reason=None, save=True):
	"""Apply a transition and stamp the audit trail. The single writer of ``status``."""
	spec = assert_transition(entry, action, user=user)
	user = user or frappe.session.user
	stamp = now()

	if action == REJECT:
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is mandatory. Explain what the Payroll Officer "
			               "must correct."))
		entry.rejection_reason = reason.strip()

	by_field, at_field = spec["stamp"]
	if by_field:
		entry.set(by_field, user)
	if at_field:
		entry.set(at_field, stamp)

	entry.status = spec["to"]

	if action == APPROVE:
		store_approval_snapshot(entry)
	elif action == CALCULATE:
		clear_approval_snapshot(entry)
	elif action == REJECT:
		clear_approval_snapshot(entry)

	if save:
		entry.flags.ignore_permissions = True
		with transition():
			entry.save(ignore_permissions=True)
	return entry.status


def invalidate_approval(entry, why):
	"""Send an approved payroll back for re-approval because its numbers moved.

	Used when the integrity check fails: silently posting different numbers from those
	somebody approved is exactly the failure this phase exists to prevent.
	"""
	if state_of(entry) != APPROVED:
		return False
	entry.db_set("status", CALCULATED, update_modified=False)
	entry.db_set("approval_fingerprint", None, update_modified=False)
	entry.db_set("approved_by", None, update_modified=False)
	entry.db_set("approved_at", None, update_modified=False)
	frappe.msgprint(
		_("The payroll changed after it was approved ({0}). Submit it for approval "
		  "again.").format(why), title=_("Approval Invalidated"), indicator="orange")
	return True


# --------------------------------------------------------------------------- #
# Totals, snapshot and integrity
# --------------------------------------------------------------------------- #
_TOTAL_FIELDS = ("gross_pay", "ss_employee_amount", "ss_employer_amount", "irt_amount",
                 "total_deduction", "net_pay", "employer_cost")


def slip_rows(entry):
	"""The live salary slips of an entry (cancelled ones excluded)."""
	names = [r.salary_slip for r in entry.get("employees", []) if r.get("salary_slip")]
	if not names:
		return []
	return frappe.get_all(
		"Isoft Salary Slip",
		filters={"name": ["in", names], "docstatus": ["<", 2]},
		fields=["name", "employee", "employee_name", "docstatus", "modified",
		        "journal_entry", "payment_entry"] + list(_TOTAL_FIELDS),
		order_by="name",
	)


def compute_totals(entry, rows=None):
	"""Everything an approver needs on one screen, computed server-side."""
	rows = slip_rows(entry) if rows is None else rows
	totals = {
		"employees": len(rows),
		"gross": 0.0, "employee_inss": 0.0, "employer_inss": 0.0, "irt": 0.0,
		"other_deductions": 0.0, "net": 0.0, "employer_cost": 0.0,
	}
	for r in rows:
		totals["gross"] += flt(r.get("gross_pay"))
		totals["employee_inss"] += flt(r.get("ss_employee_amount"))
		totals["employer_inss"] += flt(r.get("ss_employer_amount"))
		totals["irt"] += flt(r.get("irt_amount"))
		totals["net"] += flt(r.get("net_pay"))
		totals["employer_cost"] += flt(r.get("employer_cost"))
		totals["other_deductions"] += (
			flt(r.get("total_deduction")) - flt(r.get("ss_employee_amount")) - flt(r.get("irt_amount"))
		)
	return {k: (v if k == "employees" else flt(v, 2)) for k, v in totals.items()}


def compute_fingerprint(entry, rows=None):
	"""Deterministic signature of the payroll result.

	Deliberately not cryptography: the threat is an accidental edit between approval and
	posting, not a forger. It covers exactly the values an approver is signing off — the
	slip identities and their money — so any change of substance changes the digest.

	``docstatus`` is deliberately NOT part of it. Submitting the approved slips is the
	very next step of the workflow; including it would make every payroll fail its own
	integrity check the moment it moved forward.
	"""
	rows = slip_rows(entry) if rows is None else rows
	payload = [
		[r.get("name")] + [flt(r.get(f), 2) for f in _TOTAL_FIELDS]
		for r in sorted(rows, key=lambda r: r.get("name") or "")
	]
	blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
	return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def store_approval_snapshot(entry):
	rows = slip_rows(entry)
	totals = compute_totals(entry, rows=rows)
	entry.approved_employees = totals["employees"]
	entry.approved_gross = totals["gross"]
	entry.approved_employee_inss = totals["employee_inss"]
	entry.approved_employer_inss = totals["employer_inss"]
	entry.approved_irt = totals["irt"]
	entry.approved_net = totals["net"]
	entry.approved_employer_cost = totals["employer_cost"]
	entry.approval_fingerprint = compute_fingerprint(entry, rows=rows)
	return entry.approval_fingerprint


def clear_approval_snapshot(entry):
	for f in ("approved_employees", "approved_gross", "approved_employee_inss",
	          "approved_employer_inss", "approved_irt", "approved_net",
	          "approved_employer_cost", "approval_fingerprint", "approved_by", "approved_at"):
		entry.set(f, None)


def assert_approval_intact(entry):
	"""Refuse to post payroll that no longer matches what was approved."""
	stored = entry.get("approval_fingerprint")
	if not stored:
		frappe.throw(
			_("This payroll has no approval snapshot. Submit it for approval and have it "
			  "approved before posting."), title=_("Not Approved"))
	current = compute_fingerprint(entry)
	if current != stored:
		totals = compute_totals(entry)
		frappe.throw(
			_("The payroll changed after it was approved. Approved net was {0} for {1} "
			  "employee(s); the payroll now totals {2} for {3}. Submit it again for "
			  "approval.").format(
				flt(entry.get("approved_net")), cint(entry.get("approved_employees")),
				totals["net"], totals["employees"]),
			title=_("Approval No Longer Valid"),
		)
	return True


# --------------------------------------------------------------------------- #
# Period locking
# --------------------------------------------------------------------------- #
def entry_state(name):
	if not name:
		return None
	return frappe.db.get_value("Isoft Payroll Entry", name, "status") or DRAFT


def assert_slip_not_locked(slip):
	"""Block edits to a salary slip whose payroll has already been approved.

	Without this the approved numbers could be recalculated in place — attendance
	changes, a corrected allowance, even a plain re-save — and the payroll that reaches
	the ledger would not be the payroll anybody approved.
	"""
	if frappe.flags.get("isoft_payroll_unlock"):
		return
	entry_name = slip.get("payroll_entry")
	if not entry_name:
		return
	state = entry_state(entry_name)
	if state in LOCKED_STATES:
		frappe.throw(
			_("Payroll {0} is {1} and cannot be changed. Cancel the approval (or the "
			  "payroll) before correcting the payslip for {2}.").format(
				frappe.bold(entry_name), _(state), slip.get("employee_name") or slip.get("employee")),
			title=_("Payroll Locked"),
		)


class unlocked(object):
	"""Context manager for the workflow's own writes to locked slips (posting stamps
	links, cancellation clears them). Never exposed to the dashboard API."""

	def __enter__(self):
		self._previous = frappe.flags.get("isoft_payroll_unlock")
		frappe.flags.isoft_payroll_unlock = True
		return self

	def __exit__(self, *exc):
		frappe.flags.isoft_payroll_unlock = self._previous
		return False


# --------------------------------------------------------------------------- #
# Guards used by the API endpoints
# --------------------------------------------------------------------------- #
def assert_can_post(entry):
	assert_transition(entry, POST)
	assert_approval_intact(entry)
	rows = slip_rows(entry)
	if not rows:
		frappe.throw(_("This payroll has no salary slips to post."))
	unsubmitted = [r["employee_name"] or r["employee"] for r in rows if cint(r["docstatus"]) != 1]
	if unsubmitted:
		frappe.throw(
			_("{0} salary slip(s) are still drafts and cannot be posted: {1}.").format(
				len(unsubmitted), ", ".join(unsubmitted[:8])))
	return rows


def can_export(entry, user=None):
	"""Whether the bank file button should be offered: the same checks as
	:func:`assert_can_export`, as a boolean, for the screen."""
	with quiet():
		try:
			user = user or frappe.session.user
			perms.require(perms.PAYROLL_EXPORT_BANK, user=user)
			perms.require_company(entry.get("company"), user=user)
			_check_actor(entry, EXPORT_BANK, user=user)
		except (frappe.ValidationError, frappe.PermissionError):
			return False
	return state_of(entry) in (PAYMENT_READY, PAID)


def finance_flags(entry, user=None):
	"""What Finance may do with this run right now — rendered, never trusted."""
	user = user or frappe.session.user
	state = state_of(entry)

	def ok(capability, action):
		with quiet():
			try:
				perms.require(capability, user=user)
				perms.require_company(entry.get("company"), user=user)
				_check_actor(entry, action, user=user)
			except (frappe.ValidationError, frappe.PermissionError):
				return False
		return True

	return {
		"can_post": state in (APPROVED, POSTED) and ok(perms.PAYROLL_POST, POST),
		"can_submit_slips": state in (APPROVED, POSTED, PAYMENT_READY) and ok(perms.PAYROLL_POST, POST),
		"can_pay": state == PAYMENT_READY and ok(perms.PAYROLL_CONFIRM_PAYMENT, PAY),
		"can_export_bank": can_export(entry, user=user),
	}


def assert_can_export(entry):
	"""Bank files may only be produced for payroll that is approved, posted and released,
	by Finance, and never by the person who prepared it."""
	state = state_of(entry)
	perms.require(perms.PAYROLL_EXPORT_BANK)
	perms.require_company(entry.get("company"))
	_check_actor(entry, EXPORT_BANK)
	if state not in (PAYMENT_READY, PAID):
		frappe.throw(
			_("The bank file can only be generated after the payroll has been approved, "
			  "posted and released for payment. Current state: {0}.").format(_(state)),
			title=_("Payroll Not Ready for Payment"),
		)
	return True


def assert_can_pay(entry):
	state = state_of(entry)
	perms.require(perms.PAYROLL_CONFIRM_PAYMENT)
	perms.require_company(entry.get("company"))
	_check_actor(entry, PAY)
	if state not in (PAYMENT_READY, PAID):
		frappe.throw(
			_("A payroll in state {0} cannot be paid. Approve and post the payroll "
			  "first.").format(_(state)), title=_("Payment Not Authorised"))
	return True


def refresh_payment_state(entry, save=True):
	"""Move a released payroll to Paid once every payable slip has a submitted payment.

	Generating a bank file is not payment; only a submitted payment Journal Entry is.
	"""
	if state_of(entry) != PAYMENT_READY:
		return state_of(entry)
	rows = slip_rows(entry)
	payable = [r for r in rows if cint(r["docstatus"]) == 1 and flt(r["net_pay"]) > 0]
	if not payable:
		return state_of(entry)
	for r in payable:
		if not r.get("payment_entry"):
			return state_of(entry)
		if cint(frappe.db.get_value("Journal Entry", r["payment_entry"], "docstatus")) != 1:
			return state_of(entry)
	entry.status = PAID
	entry.paid_at = now()
	if save:
		with transition():
			entry.save(ignore_permissions=True)
	return entry.status
