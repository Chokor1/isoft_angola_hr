# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Employment contracts and probation.

ERPNext v13 ships most of the HR lifecycle — Employee Transfer, Employee Promotion,
Employee Onboarding, Employee Separation, Leave and Attendance all exist and are reused.
It ships no concept of an employment CONTRACT, which is why this module exists: in
Angola the contract is the document the employment relationship rests on, and its type,
term, probation and renewal chain have to be recorded and kept.

Design follows the same rule the payroll phases established: **history is never
rewritten**. A renewal creates a new contract linked to the old one; it never edits the
old one's dates. A terminated contract keeps its termination reason. The chain of
contracts is the employment history.

LEGAL VERIFICATION REQUIRED — this module encodes no statutory rule. Maximum fixed
terms, maximum probation length, renewal limits, notice periods and severance are NOT
inferred from the contract type; they are configuration on Isoft Contract Type, entered
by whoever is accountable for getting them right.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #
DRAFT = "Draft"
PENDING_APPROVAL = "Pending Approval"
ACTIVE = "Active"
EXPIRING = "Expiring"
EXPIRED = "Expired"
RENEWED = "Renewed"
TERMINATED = "Terminated"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

#: A contract in one of these states governs employment right now, so it is what an
#: overlap check, payroll and the employee's current position must look at.
LIVE_STATES = (ACTIVE, EXPIRING)
#: States that still occupy the employee's timeline for overlap purposes.
OCCUPYING_STATES = (PENDING_APPROVAL, ACTIVE, EXPIRING, EXPIRED, RENEWED, TERMINATED)

SUBMIT = "submit_for_approval"
APPROVE = "approve"
REJECT = "reject"
ACTIVATE = "activate"
TERMINATE = "terminate"
CANCEL = "cancel"

_TRANSITIONS = {
	SUBMIT: {"from": (DRAFT, REJECTED), "to": PENDING_APPROVAL,
	         "permission": perms.CONTRACT_WRITE, "stamp": ("submitted_by", "submitted_at"),
	         "label": _("Submit for Approval")},
	APPROVE: {"from": (PENDING_APPROVAL,), "to": ACTIVE,
	          "permission": perms.CONTRACT_APPROVE, "stamp": ("approved_by", "approved_at"),
	          "label": _("Approve Contract")},
	REJECT: {"from": (PENDING_APPROVAL,), "to": REJECTED,
	         "permission": perms.CONTRACT_APPROVE, "stamp": (None, None),
	         "label": _("Reject Contract")},
	TERMINATE: {"from": (ACTIVE, EXPIRING), "to": TERMINATED,
	            "permission": perms.CONTRACT_APPROVE, "stamp": (None, None),
	            "label": _("Terminate Contract")},
	CANCEL: {"from": (DRAFT, PENDING_APPROVAL, REJECTED, ACTIVE, EXPIRING), "to": CANCELLED,
	         "permission": perms.CONTRACT_APPROVE, "stamp": (None, None),
	         "label": _("Cancel Contract")},
}

#: Expiry reminder thresholds, in days. Configurable through Isoft HR Settings.
DEFAULT_EXPIRY_THRESHOLDS = (90, 60, 30, 15, 7)


def expiry_thresholds():
	raw = frappe.db.get_single_value("Isoft HR Settings", "contract_expiry_thresholds")
	if not raw:
		return list(DEFAULT_EXPIRY_THRESHOLDS)
	try:
		values = sorted({cint(p) for p in str(raw).replace(";", ",").split(",") if cint(p) > 0},
		                reverse=True)
		return values or list(DEFAULT_EXPIRY_THRESHOLDS)
	except Exception:
		return list(DEFAULT_EXPIRY_THRESHOLDS)


def state_of(contract):
	return contract.get("status") or DRAFT


def allowed_actions(contract, user=None):
	out = []
	for action, spec in _TRANSITIONS.items():
		if state_of(contract) not in spec["from"]:
			continue
		if not perms.can(spec["permission"], user=user):
			continue
		if not perms.can_company(contract.get("company"), user=user):
			continue
		out.append(action)
	return out


def assert_transition(contract, action, user=None):
	spec = _TRANSITIONS.get(action)
	if not spec:
		frappe.throw(_("Unknown contract action {0}.").format(action))
	perms.require(spec["permission"], user=user)
	perms.require_company(contract.get("company"), user=user)
	if state_of(contract) not in spec["from"]:
		frappe.throw(
			_("Cannot {0} a contract that is {1}. Allowed only when it is: {2}.").format(
				spec["label"], _(state_of(contract)), ", ".join(_(s) for s in spec["from"])),
			title=_("Invalid Contract State"))
	# Preparing and approving an employment agreement should not be the same person.
	if action == APPROVE and require_separate_contract_approval():
		preparer = contract.get("submitted_by") or contract.get("prepared_by")
		if preparer and preparer == (user or frappe.session.user):
			frappe.throw(
				_("Não pode aprovar um contrato que preparou. A contract submitted by {0} must "
				  "be approved by a different user.").format(preparer),
				title=_("Self-Approval Blocked"))
	return spec


def require_separate_contract_approval():
	value = frappe.db.get_single_value("Isoft HR Settings", "require_separate_contract_approval")
	return cint(1 if value is None or value == "" else value)


def perform(contract, action, user=None, reason=None, save=True):
	spec = assert_transition(contract, action, user=user)
	user = user or frappe.session.user

	if action in (REJECT, TERMINATE):
		if not (reason or "").strip():
			frappe.throw(_("A reason is mandatory for this action."))
		if action == REJECT:
			contract.rejection_reason = reason.strip()
		else:
			contract.termination_reason = reason.strip()
			contract.terminated_on = getdate(nowdate())

	by_field, at_field = spec["stamp"]
	if by_field:
		contract.set(by_field, user)
	if at_field:
		contract.set(at_field, now())

	contract.status = spec["to"]
	if action == APPROVE:
		contract.refresh_derived_status()
	if save:
		contract.save(ignore_permissions=True)
	return contract.status


# --------------------------------------------------------------------------- #
# Derived status
# --------------------------------------------------------------------------- #
def derive_status(contract, on_date=None):
	"""Active / Expiring / Expired, worked out from the dates.

	Only ever applied to a contract that is already live — an unapproved contract is
	never promoted to Active by the passage of time.
	"""
	if state_of(contract) not in LIVE_STATES:
		return state_of(contract)
	today = getdate(on_date or nowdate())
	if contract.get("is_open_ended") or not contract.get("end_date"):
		return ACTIVE
	end = getdate(contract.end_date)
	if end < today:
		return EXPIRED
	warn_from = max(expiry_thresholds()) if expiry_thresholds() else 90
	return EXPIRING if date_diff(end, today) <= warn_from else ACTIVE


def refresh_contract_statuses(company=None):
	"""Scheduled sweep that moves live contracts to Expiring / Expired.

	Runs daily. It only ever changes a contract that is already Active or Expiring, so it
	can never approve, renew or resurrect anything on its own.
	"""
	filters = {"status": ["in", list(LIVE_STATES)]}
	if company:
		filters["company"] = company
	changed = 0
	for name in frappe.get_all("Isoft Employment Contract", filters=filters, pluck="name"):
		doc = frappe.get_doc("Isoft Employment Contract", name)
		new_status = derive_status(doc)
		if new_status != doc.status:
			doc.db_set("status", new_status, update_modified=False)
			changed += 1
		probation = derive_probation_status(doc)
		if probation != doc.probation_status:
			doc.db_set("probation_status", probation, update_modified=False)
	return changed


# --------------------------------------------------------------------------- #
# Probation
# --------------------------------------------------------------------------- #
def derive_probation_status(contract, on_date=None):
	"""Where the probation stands, from the dates and any decision already recorded."""
	if contract.get("probation_decision") == "Confirmed":
		return "Confirmed"
	if contract.get("probation_decision") == "Extended":
		return "Extended"
	if contract.get("probation_decision") == "Terminated":
		return "Failed"
	if not contract.get("probation_end"):
		return "Not Applicable"
	today = getdate(on_date or nowdate())
	end = getdate(contract.probation_end)
	if end < today:
		return "Overdue"
	if date_diff(end, today) <= probation_review_window():
		return "Review Due"
	return "In Progress"


def probation_review_window():
	value = frappe.db.get_single_value("Isoft HR Settings", "probation_review_window_days")
	return cint(value) or 30


def record_probation_decision(contract, decision, notes=None, new_end=None, user=None):
	"""Confirm, extend or fail a probation. Never automatic — somebody decides."""
	perms.require(perms.CONTRACT_APPROVE, user=user)
	perms.require_company(contract.company, user=user)
	if decision not in ("Confirmed", "Extended", "Terminated"):
		frappe.throw(_("Unknown probation decision {0}.").format(decision))
	if state_of(contract) not in LIVE_STATES:
		frappe.throw(_("Probation can only be decided on an active contract."))
	if not contract.probation_end:
		frappe.throw(_("This contract has no probation period."))

	if decision == "Extended":
		if not new_end:
			frappe.throw(_("Give the new probation end date when extending."))
		if getdate(new_end) <= getdate(contract.probation_end):
			frappe.throw(_("The extended probation must end after {0}.").format(
				contract.probation_end))
		# LEGAL VERIFICATION REQUIRED — no maximum probation length is enforced, because
		# no authoritative Angolan limit has been verified. The extension is recorded as
		# entered and the decision trail shows who authorised it.
		contract.probation_end = getdate(new_end)

	contract.probation_decision = decision
	contract.probation_decision_date = getdate(nowdate())
	contract.probation_decision_by = user or frappe.session.user
	if notes:
		contract.probation_notes = notes
	contract.probation_status = derive_probation_status(contract)

	if decision == "Confirmed":
		# ERPNext's own confirmation field, so the standard Employee form agrees with us.
		frappe.db.set_value("Employee", contract.employee, "final_confirmation_date",
		                    getdate(nowdate()))
	contract.save(ignore_permissions=True)
	return contract.probation_status


# --------------------------------------------------------------------------- #
# Renewal
# --------------------------------------------------------------------------- #
def renew(contract, start_date=None, end_date=None, contract_type=None, notes=None):
	"""Create the NEXT contract, linked to this one.

	The expiring contract is marked Renewed and keeps its own dates untouched. Editing
	the old agreement's end date to extend it would destroy the record of what was
	actually agreed, which is the one thing an employment contract exists to preserve.
	"""
	perms.require(perms.CONTRACT_WRITE)
	perms.require_company(contract.company)

	if state_of(contract) not in (ACTIVE, EXPIRING, EXPIRED):
		frappe.throw(_("Only an active, expiring or expired contract can be renewed "
		               "(this one is {0}).").format(_(state_of(contract))))
	if not cint(contract.renewal_allowed):
		frappe.throw(_("Contract {0} is marked as not renewable.").format(contract.name))
	if contract.renewed_to and frappe.db.exists("Isoft Employment Contract", contract.renewed_to):
		frappe.throw(_("Contract {0} has already been renewed by {1}.").format(
			contract.name, contract.renewed_to), title=_("Already Renewed"))

	start = getdate(start_date) if start_date else add_days(getdate(contract.end_date), 1) \
		if contract.end_date else getdate(nowdate())
	if contract.end_date and start <= getdate(contract.end_date):
		frappe.throw(_("The renewal must start after the current contract ends ({0}).").format(
			contract.end_date))

	new = frappe.new_doc("Isoft Employment Contract")
	for field in ("employee", "company", "department", "designation", "employment_type",
	              "work_location", "holiday_list", "shift_type", "notice_days",
	              "renewal_allowed"):
		new.set(field, contract.get(field))
	new.contract_type = contract_type or contract.contract_type
	new.start_date = start
	new.is_open_ended = contract.is_open_ended
	if end_date:
		new.end_date = getdate(end_date)
	elif not contract.is_open_ended:
		months = cint(frappe.db.get_value("Isoft Contract Type", new.contract_type,
		                                  "default_duration_months"))
		if months:
			new.end_date = add_days(frappe.utils.add_months(start, months), -1)
	new.previous_contract = contract.name
	new.notes = notes
	# A renewal is a continuation of employment, so there is no fresh probation unless HR
	# deliberately adds one.
	new.insert(ignore_permissions=True)

	contract.db_set("renewed_to", new.name, update_modified=False)
	contract.db_set("status", RENEWED, update_modified=False)
	return new


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def active_contract(employee, on_date=None):
	"""The contract governing an employee on a date, or None."""
	on_date = getdate(on_date or nowdate())
	rows = frappe.db.sql(
		"""select name from `tabIsoft Employment Contract`
		where employee = %s and status in ('Active', 'Expiring')
		  and start_date <= %s
		  and (ifnull(is_open_ended, 0) = 1 or ifnull(end_date, '2999-12-31') >= %s)
		order by start_date desc limit 1""",
		(employee, on_date, on_date))
	return rows[0][0] if rows else None


def expiring_contracts(company=None, within_days=None, as_of=None):
	"""Live fixed-term contracts ending within a window — the contract expiry work list."""
	within_days = cint(within_days) if within_days else max(expiry_thresholds())
	as_of = getdate(as_of or nowdate())
	conditions, values = ["c.status in ('Active', 'Expiring')",
	                      "ifnull(c.is_open_ended, 0) = 0",
	                      "c.end_date is not null",
	                      "c.end_date >= %s", "c.end_date <= %s"], [as_of, add_days(as_of, within_days)]
	if company:
		conditions.append("c.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="c")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select c.name, c.employee, c.employee_name, c.company, c.contract_type,
			c.start_date, c.end_date, c.status, c.renewal_allowed, c.renewed_to,
			datediff(c.end_date, %s) as days_left
		from `tabIsoft Employment Contract` c
		where {0} order by c.end_date""".format(" and ".join(conditions)),
		[as_of] + values, as_dict=True)


def probation_reviews_due(company=None, as_of=None):
	as_of = getdate(as_of or nowdate())
	conditions, values = ["c.status in ('Active', 'Expiring')",
	                      "c.probation_end is not null",
	                      "ifnull(c.probation_decision, '') = ''"], []
	if company:
		conditions.append("c.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="c")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select c.name, c.employee, c.employee_name, c.company, c.probation_start,
			c.probation_end, c.probation_status, datediff(c.probation_end, %s) as days_left
		from `tabIsoft Employment Contract` c
		where {0} order by c.probation_end""".format(" and ".join(conditions)),
		[as_of] + values, as_dict=True)
