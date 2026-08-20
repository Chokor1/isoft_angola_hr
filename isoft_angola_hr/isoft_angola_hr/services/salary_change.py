# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Audited salary changes, and their effect on the payroll engine.

Before this module the only way to change somebody's pay was to edit or add a Salary
Profile directly — no request, no approval, no reason, no record of who decided. That is
the single highest-value unaudited action in an HR system.

The flow is:

    request  →  approve (different person)  →  apply on the effective date

"Apply" is the interesting part. It does exactly two things, atomically:

    close the current Salary Profile at (effective_date - 1)
    create a new Salary Profile from effective_date

which is precisely the shape the payroll engine already understands, so the change
becomes effective-dated for free and historical payroll stays reproducible.

MID-PERIOD CHANGES ARE REFUSED. Phase 1.5 established that the engine resolves one
profile per payroll period and cannot prorate two. A change effective inside a period
would silently pay the whole month at the new rate, so this module blocks it at request
time — the earliest and cheapest place to catch it — rather than letting payroll discover
it later.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

DRAFT = "Draft"
PENDING_APPROVAL = "Pending Approval"
APPROVED = "Approved"
APPLIED = "Applied"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

SUBMIT = "submit_for_approval"
APPROVE = "approve"
REJECT = "reject"
APPLY = "apply"
CANCEL = "cancel"

_TRANSITIONS = {
	SUBMIT: {"from": (DRAFT, REJECTED), "to": PENDING_APPROVAL,
	         "permission": perms.SALARY_CHANGE_REQUEST,
	         "stamp": ("requested_by", "requested_at"), "label": _("Submit for Approval")},
	APPROVE: {"from": (PENDING_APPROVAL,), "to": APPROVED,
	          "permission": perms.SALARY_CHANGE_APPROVE,
	          "stamp": ("approved_by", "approved_at"), "label": _("Approve Salary Change")},
	REJECT: {"from": (PENDING_APPROVAL,), "to": REJECTED,
	         "permission": perms.SALARY_CHANGE_APPROVE, "stamp": (None, None),
	         "label": _("Reject Salary Change")},
	APPLY: {"from": (APPROVED,), "to": APPLIED,
	        "permission": perms.SALARY_CHANGE_APPROVE, "stamp": (None, None),
	        "label": _("Apply Salary Change")},
	CANCEL: {"from": (DRAFT, PENDING_APPROVAL, APPROVED, REJECTED), "to": CANCELLED,
	         "permission": perms.SALARY_CHANGE_REQUEST, "stamp": (None, None),
	         "label": _("Cancel")},
}


def state_of(change):
	return change.get("status") or DRAFT


def allowed_actions(change, user=None):
	out = []
	for action, spec in _TRANSITIONS.items():
		if state_of(change) not in spec["from"]:
			continue
		if not perms.can(spec["permission"], user=user):
			continue
		if not perms.can_company(change.get("company"), user=user):
			continue
		if action == APPROVE and _is_self_approval(change, user):
			continue
		out.append(action)
	return out


def _is_self_approval(change, user=None):
	if not requires_separate_approval():
		return False
	requester = change.get("requested_by")
	return bool(requester) and requester == (user or frappe.session.user)


def requires_separate_approval():
	value = frappe.db.get_single_value("Isoft HR Settings",
	                                   "require_separate_salary_change_approval")
	return cint(1 if value is None or value == "" else value)


def assert_transition(change, action, user=None):
	spec = _TRANSITIONS.get(action)
	if not spec:
		frappe.throw(_("Unknown salary change action {0}.").format(action))
	perms.require(spec["permission"], user=user)
	perms.require_company(change.get("company"), user=user)
	if state_of(change) not in spec["from"]:
		frappe.throw(
			_("Cannot {0} a salary change that is {1}. Allowed only when it is: {2}.").format(
				spec["label"], _(state_of(change)), ", ".join(_(s) for s in spec["from"])),
			title=_("Invalid State"))
	if action == APPROVE and _is_self_approval(change, user):
		frappe.throw(
			_("Não pode aprovar uma alteração salarial que pediu. A salary change requested "
			  "by {0} must be approved by a different user.").format(change.get("requested_by")),
			title=_("Self-Approval Blocked"))
	return spec


def perform(change, action, user=None, reason=None, save=True):
	spec = assert_transition(change, action, user=user)
	user = user or frappe.session.user

	if action == REJECT:
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is mandatory."))
		change.rejection_reason = reason.strip()

	by_field, at_field = spec["stamp"]
	if by_field:
		change.set(by_field, user)
	if at_field:
		change.set(at_field, now())

	change.status = spec["to"]
	if save:
		change.save(ignore_permissions=True)

	if action == APPLY:
		apply_change(change)
	return change.status


# --------------------------------------------------------------------------- #
# Period safety
# --------------------------------------------------------------------------- #
def assert_effective_date_is_a_period_boundary(employee, effective_date, employee_name=None):
	"""Refuse an effective date that falls inside a payroll period.

	The payroll engine resolves ONE Salary Profile per period. A change effective on the
	16th would make the whole month pay at the new rate — an overpayment nobody asked
	for. Aligning the change with the payroll cycle is a one-line fix for HR; discovering
	it after payday is not.
	"""
	from isoft_angola_hr.isoft_angola_hr import api

	effective = getdate(effective_date)
	start, _end = api._cycle_period(effective)
	if getdate(start) != effective:
		frappe.throw(
			_("A alteração salarial de {0} tem de coincidir com o início de um período de "
			  "processamento. {1} cai a meio do período que começa em {2}; o motor de "
			  "processamento não consegue dividir um período entre dois Perfis Salariais. "
			  "Use {2} (ou o início do período seguinte).").format(
				frappe.bold(employee_name or employee), effective, getdate(start)),
			title=_("Effective Date Mid-Period"))
	return True


def assert_period_not_already_processed(employee, effective_date):
	"""A salary change cannot reach into a period that already produced payroll."""
	clash = frappe.db.sql(
		"""select name, start_date, end_date, docstatus from `tabIsoft Salary Slip`
		where employee = %s and docstatus < 2 and end_date >= %s limit 1""",
		(employee, getdate(effective_date)), as_dict=True)
	if clash:
		frappe.throw(
			_("Salary slip {0} already covers {1} to {2}, which starts on or after the "
			  "effective date. Cancel that payroll first, or choose a later effective "
			  "date.").format(clash[0].name, clash[0].start_date, clash[0].end_date),
			title=_("Period Already Processed"))
	return True


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
def apply_change(change):
	"""Close the old profile and open the new one — atomically, and only once.

	Both writes happen inside one savepoint. A half-applied salary change (old profile
	closed, new one missing) would leave the employee with no salary at all for the next
	payroll run, which is the worst possible failure mode for this operation.
	"""
	if change.created_profile and frappe.db.exists("Isoft Salary Profile", change.created_profile):
		# Idempotency: re-applying must never produce a second profile.
		return change.created_profile

	effective = getdate(change.effective_date)
	assert_effective_date_is_a_period_boundary(change.employee, effective, change.employee_name)
	assert_period_not_already_processed(change.employee, effective)

	# Row-lock the employee's profiles so two approvers cannot both apply a change.
	frappe.db.sql(
		"""select name from `tabIsoft Salary Profile` where employee = %s for update""",
		change.employee)

	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
		get_active_profile,
	)

	frappe.db.savepoint("isoft_salary_change")
	try:
		current = get_active_profile(change.employee, add_days(effective, -1),
		                             company=change.company,
		                             employee_name=change.employee_name)

		# Close the old profile BEFORE opening the new one. The order matters now that
		# overlapping effective periods are refused: inserting the new profile first
		# would collide with the still-open old one and the whole change would fail.
		# Both writes are inside this savepoint, so the pair is still all-or-nothing —
		# an employee is never left without a salary.
		if current:
			current.db_set("to_date", add_days(effective, -1), update_modified=False)
			change.db_set("closed_profile", current.name, update_modified=False)

		new_profile = frappe.new_doc("Isoft Salary Profile")
		new_profile.employee = change.employee
		new_profile.company = change.company
		new_profile.from_date = effective
		new_profile.base = flt(change.new_base)
		new_profile.food_allowance = flt(change.new_food_allowance)
		new_profile.transport_allowance = flt(change.new_transport_allowance)
		new_profile.family_allowance = flt(change.new_family_allowance)
		if current and current.get("irt_table"):
			new_profile.irt_table = current.irt_table
		# The previous agreement was closed above, the day before this one starts. The
		# record itself is kept — that is how the pay history stays readable.
		new_profile.insert(ignore_permissions=True)

		change.db_set("created_profile", new_profile.name, update_modified=False)
		change.db_set("status", APPLIED, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point="isoft_salary_change")
		raise

	_apply_position_change(change)
	return new_profile.name


def _apply_position_change(change):
	"""A promotion can move designation and department too.

	ERPNext's own Employee Promotion already records position history, so this only
	updates the Employee's CURRENT position when the salary change carries one — it does
	not duplicate ERPNext's history mechanism.
	"""
	updates = {}
	if change.get("new_designation"):
		updates["designation"] = change.new_designation
	if change.get("new_department"):
		updates["department"] = change.new_department
	if updates:
		frappe.db.set_value("Employee", change.employee, updates)


def pending_for_effective_date(company=None, on_date=None):
	"""Approved changes whose effective date has arrived but which are not applied yet."""
	conditions = ["status = 'Approved'", "effective_date <= %s"]
	values = [getdate(on_date or frappe.utils.nowdate())]
	if company:
		conditions.append("company = %s")
		values.append(company)
	return frappe.db.sql(
		"""select name, employee, employee_name, company, effective_date, new_base
		from `tabIsoft Salary Change` where {0} order by effective_date""".format(
			" and ".join(conditions)), values, as_dict=True)


def apply_due_changes():
	"""Scheduled: apply approved salary changes on their effective date.

	Deliberately conservative — it only applies changes that a human already approved,
	and one failure never stops the rest.
	"""
	applied, failed = [], []
	for row in pending_for_effective_date():
		try:
			doc = frappe.get_doc("Isoft Salary Change", row.name)
			apply_change(doc)
			applied.append(row.name)
		except Exception as exc:
			failed.append({"name": row.name, "error": frappe.utils.strip_html(str(exc))[:200]})
	if failed:
		frappe.log_error(title="Isoft HR: salary changes could not be applied",
		                 message=frappe.as_json(failed))
	return {"applied": applied, "failed": failed}
