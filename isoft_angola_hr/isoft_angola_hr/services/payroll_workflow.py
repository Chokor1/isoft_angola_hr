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
from frappe.utils import cint, flt, now

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
		"label": _("Calculate Payroll"),
	},
	SUBMIT_FOR_APPROVAL: {
		"from": (CALCULATED,),
		"to": PENDING_APPROVAL,
		"permission": perms.PAYROLL_SUBMIT_FOR_APPROVAL,
		"stamp": ("submitted_by", "submitted_at"),
		"label": _("Submit for Approval"),
	},
	APPROVE: {
		"from": (PENDING_APPROVAL,),
		"to": APPROVED,
		"permission": perms.PAYROLL_APPROVE,
		"stamp": ("approved_by", "approved_at"),
		"label": _("Approve Payroll"),
	},
	REJECT: {
		"from": (PENDING_APPROVAL,),
		"to": REJECTED,
		"permission": perms.PAYROLL_REJECT,
		"stamp": ("rejected_by", "rejected_at"),
		"label": _("Reject Payroll"),
	},
	POST: {
		"from": (APPROVED,),
		"to": POSTED,
		"permission": perms.PAYROLL_POST,
		"stamp": ("posted_by", "posted_at"),
		"label": _("Post Accounting"),
	},
	RELEASE_FOR_PAYMENT: {
		"from": (POSTED,),
		"to": PAYMENT_READY,
		"permission": perms.PAYROLL_CONFIRM_PAYMENT,
		"stamp": ("payment_authorized_by", "payment_authorized_at"),
		"label": _("Release for Payment"),
	},
	PAY: {
		"from": (PAYMENT_READY,),
		"to": PAID,
		"permission": perms.PAYROLL_CONFIRM_PAYMENT,
		"stamp": (None, "paid_at"),
		"label": _("Confirm Payment"),
	},
	CLOSE: {
		"from": (PAID,),
		"to": CLOSED,
		"permission": perms.PAYROLL_CLOSE,
		"stamp": ("closed_by", "closed_at"),
		"label": _("Close Payroll"),
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
		"label": _("Cancel Payroll"),
	},
}


def transition_labels():
	return {a: spec["label"] for a, spec in _TRANSITIONS.items()}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _flag(fieldname, default=1):
	"""Read a control checkbox, treating "never configured" as the SAFE default.

	A Check field added by a migration has no stored value on an existing Singles record,
	and an unset value must not read as "segregation disabled" — that would silently
	switch the control off on precisely the sites that upgrade into it.
	"""
	value = frappe.db.get_single_value("Isoft HR Settings", fieldname)
	if value is None or value == "":
		return cint(default)
	return cint(value)


def requires_separate_approval():
	return _flag("require_separate_payroll_approval", 1)


def requires_separate_payment_approval():
	return _flag("require_separate_payment_approval", 1)


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #
def state_of(entry):
	"""Current lifecycle state. Entries created before Phase 2 carry no status; they are
	read as Draft (nothing was ever approved) so no historical record silently gains an
	approval it never had."""
	return entry.get("status") or DRAFT


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
		try:
			_check_actor(entry, action, user=user)
		except frappe.ValidationError:
			continue
		out.append(action)
	return out


#: What has to happen next, and who has to do it. Users should never have to translate a
#: status code into an action — the document should say what it is waiting for.
_NEXT_STEP = {
	DRAFT: ("calculate", _("Payroll Officer"), _("Calculate the payroll to generate the salary slips.")),
	CALCULATED: ("submit_for_approval", _("Payroll Officer"),
	             _("Review the calculation and submit it for approval.")),
	PENDING_APPROVAL: ("approve", _("Payroll Manager"),
	                   _("Review the totals and approve or reject the payroll.")),
	REJECTED: ("calculate", _("Payroll Officer"),
	           _("Correct what the approver reported and recalculate.")),
	APPROVED: ("post", _("Finance"), _("Submit the salary slips and post the accounting.")),
	POSTED: ("release_for_payment", _("Finance"),
	         _("Check the bank details and release the payroll for payment.")),
	PAYMENT_READY: ("pay", _("Finance"),
	                _("Generate the bank file and post the salary payments.")),
	PAID: ("close", _("Finance"), _("Reconcile and close the payroll period.")),
	CLOSED: (None, None, _("This payroll is closed. Corrections require cancellation.")),
	CANCELLED: (None, None, _("This payroll was cancelled.")),
}


def next_step(entry):
	"""The state, the next action, who must take it, and what currently blocks it."""
	state = state_of(entry)
	action, who, description = _NEXT_STEP.get(state, (None, None, None))
	blockers = []
	if action:
		try:
			assert_transition(entry, action)
		except (frappe.ValidationError, frappe.PermissionError) as exc:
			blockers.append(frappe.utils.strip_html(str(exc)))
	return {
		"state": state,
		"state_label": _(state),
		"next_action": action,
		"next_action_label": _TRANSITIONS[action]["label"] if action else None,
		"responsible": who,
		"description": description,
		"blockers": blockers,
		"can_act_now": bool(action) and not blockers,
	}


def _check_actor(entry, action, user=None):
	"""Segregation of duties. Roles alone are not enough — one person can hold several,
	so the identity that prepared the payroll is compared with the identity approving it."""
	user = user or frappe.session.user

	if action == APPROVE and requires_separate_approval():
		preparer = entry.get("submitted_by") or entry.get("prepared_by")
		if preparer and preparer == user:
			frappe.throw(
				_("Não pode aprovar um processamento salarial preparado por si próprio. "
				  "(Payroll submitted by {0} must be approved by a different user.)").format(preparer),
				title=_("Self-Approval Blocked"),
			)

	if action in (RELEASE_FOR_PAYMENT, PAY) and requires_separate_payment_approval():
		approver = entry.get("approved_by")
		if approver and approver == user:
			frappe.throw(
				_("Não pode autorizar o pagamento de um processamento que aprovou. "
				  "(Payroll approved by {0} must be paid by a different user.)").format(approver),
				title=_("Payment Segregation"),
			)


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
				spec["label"], _(state), ", ".join(_(s) for s in spec["from"])),
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
		_("O processamento foi alterado depois da aprovação ({0}). Submeta-o novamente para "
		  "aprovação.").format(why), title=_("Approval Invalidated"), indicator="orange")
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
			_("O processamento foi alterado depois da aprovação. Approved net was {0} for {1} "
			  "employee(s); the payroll now totals {2} for {3}. Submit it again for approval.").format(
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
			_("O processamento salarial {0} está {1} e não pode ser alterado. Cancele a "
			  "aprovação (ou o processamento) antes de corrigir o recibo de {2}.").format(
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


def assert_can_export(entry):
	"""Bank files may only be produced for payroll that is approved, posted and released."""
	state = state_of(entry)
	perms.require(perms.PAYROLL_EXPORT_BANK)
	perms.require_company(entry.get("company"))
	if state not in (PAYMENT_READY, PAID):
		frappe.throw(
			_("O ficheiro bancário só pode ser gerado depois do processamento ser aprovado, "
			  "contabilizado e libertado para pagamento. Estado actual: {0}.").format(_(state)),
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
			_("Não é possível pagar um processamento no estado {0}. Aprove e contabilize o "
			  "processamento primeiro.").format(_(state)), title=_("Payment Not Authorised"))
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
		entry.save(ignore_permissions=True)
	return entry.status
