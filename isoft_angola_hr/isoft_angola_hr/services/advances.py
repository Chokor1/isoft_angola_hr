# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Salary advances: requested, approved, disbursed, recovered from payroll, settled.

Before this, an advance was a number typed into the Adiantamento box on a salary slip.
Nothing recorded who authorised it, nothing tracked the outstanding balance, nothing
stopped it being deducted twice, and nothing connected the cash that left the bank to the
deduction that recovered it. This module makes the advance a document with a balance.

    request → approve (different person) → disburse (Finance, posts to the ledger)
            → recover over N payroll periods → settled

WHY THERE IS NO SEPARATE LOAN MODULE
------------------------------------
ERPNext v13 already ships a complete Loan Management module (principal, interest,
schedules, security). Reimplementing it here would be exactly the accidental
reinvention the brief warns against. A salary advance is the simpler thing HR actually
asks for — no interest, recovered from pay — so that is what this implements, and
interest-bearing lending stays with the module that already does it properly.

NEGATIVE NET IS IMPOSSIBLE BY CONSTRUCTION. The engine caps the recovery at whatever
remains after the statutory deductions; anything it cannot take stays outstanding and is
reported. An advance must never stop somebody's salary.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

DRAFT = "Draft"
PENDING_APPROVAL = "Pending Approval"
APPROVED = "Approved"
REJECTED = "Rejected"
DISBURSED = "Disbursed"
RECOVERING = "Recovering"
SETTLED = "Settled"
CANCELLED = "Cancelled"

#: States in which an advance still has money to collect.
OPEN_STATES = (DISBURSED, RECOVERING)

SUBMIT = "submit_for_approval"
APPROVE = "approve"
REJECT = "reject"
DISBURSE = "disburse"
CANCEL = "cancel"

_TRANSITIONS = {
	SUBMIT: {"from": (DRAFT, REJECTED), "to": PENDING_APPROVAL,
	         "permission": perms.ADVANCE_REQUEST,
	         "stamp": ("requested_by", "requested_at"), "label": _("Submit for Approval")},
	APPROVE: {"from": (PENDING_APPROVAL,), "to": APPROVED,
	          "permission": perms.ADVANCE_APPROVE, "stamp": ("approved_by", "approved_at"),
	          "label": _("Approve Advance")},
	REJECT: {"from": (PENDING_APPROVAL,), "to": REJECTED,
	         "permission": perms.ADVANCE_APPROVE, "stamp": (None, None),
	         "label": _("Reject Advance")},
	DISBURSE: {"from": (APPROVED,), "to": DISBURSED,
	           "permission": perms.ADVANCE_DISBURSE, "stamp": (None, None),
	           "label": _("Disburse Advance")},
	CANCEL: {"from": (DRAFT, PENDING_APPROVAL, APPROVED, REJECTED), "to": CANCELLED,
	         "permission": perms.ADVANCE_REQUEST, "stamp": (None, None), "label": _("Cancel")},
}


def state_of(advance):
	return advance.get("status") or DRAFT


def allowed_actions(advance, user=None):
	out = []
	for action, spec in _TRANSITIONS.items():
		if state_of(advance) not in spec["from"]:
			continue
		if not perms.can(spec["permission"], user=user):
			continue
		if not perms.can_company(advance.get("company"), user=user):
			continue
		if action == APPROVE and _is_self_approval(advance, user):
			continue
		out.append(action)
	return out


def _is_self_approval(advance, user=None):
	if not requires_separate_approval():
		return False
	requester = advance.get("requested_by")
	return bool(requester) and requester == (user or frappe.session.user)


def requires_separate_approval():
	value = frappe.db.get_single_value("Isoft HR Settings", "require_separate_advance_approval")
	return cint(1 if value is None or value == "" else value)


def assert_transition(advance, action, user=None):
	spec = _TRANSITIONS.get(action)
	if not spec:
		frappe.throw(_("Unknown advance action {0}.").format(action))
	perms.require(spec["permission"], user=user)
	perms.require_company(advance.get("company"), user=user)
	if state_of(advance) not in spec["from"]:
		frappe.throw(
			_("Cannot {0} an advance that is {1}. Allowed only when it is: {2}.").format(
				spec["label"], _(state_of(advance)), ", ".join(_(s) for s in spec["from"])),
			title=_("Invalid State"))
	if action == APPROVE and _is_self_approval(advance, user):
		frappe.throw(
			_("Não pode aprovar um adiantamento que pediu. An advance requested by {0} must be "
			  "approved by a different user.").format(advance.get("requested_by")),
			title=_("Self-Approval Blocked"))
	return spec


def perform(advance, action, user=None, reason=None, save=True):
	spec = assert_transition(advance, action, user=user)
	user = user or frappe.session.user

	if action == REJECT:
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is mandatory."))
		advance.rejection_reason = reason.strip()

	if action == APPROVE:
		if flt(advance.approved_amount) <= 0:
			advance.approved_amount = flt(advance.requested_amount)
		if flt(advance.approved_amount) > flt(advance.requested_amount):
			frappe.throw(_("The approved amount cannot exceed the requested amount."))
		advance.build_schedule()

	by_field, at_field = spec["stamp"]
	if by_field:
		advance.set(by_field, user)
	if at_field:
		advance.set(at_field, now())

	advance.status = spec["to"]
	if save:
		advance.save(ignore_permissions=True)

	if action == DISBURSE:
		disburse(advance)
	return advance.status


# --------------------------------------------------------------------------- #
# Disbursement
# --------------------------------------------------------------------------- #
def _advance_account(advance, settings):
	"""Never a hard-coded account name. Falls back to the Adiantamento component account,
	which is the same asset the payroll recovery credits, so the two sides always meet."""
	if advance.advance_account:
		return advance.advance_account
	for row in settings.get("component_accounts") or []:
		if row.abbr == "ADT" and row.account:
			return row.account
	frappe.throw(
		_("No Employee Advance account is configured. Set one on the advance, or map the "
		  "Adiantamento component in Isoft HR Settings → Account per Component."),
		title=_("Advance Account Missing"))


def disburse(advance):
	"""Pay the advance out and book it:  DR Employee Advance / CR Bank.

	Idempotent — a second call returns the existing entry rather than paying twice.
	"""
	if advance.disbursement_entry:
		docstatus = frappe.db.get_value("Journal Entry", advance.disbursement_entry, "docstatus")
		if docstatus is not None and cint(docstatus) != 2:
			return advance.disbursement_entry

	settings = frappe.get_single("Isoft HR Settings")
	advance_account = _advance_account(advance, settings)
	bank = settings.get("salary_payment_account")
	if not bank:
		frappe.throw(_("Configure the Salary Payment (Bank/Cash) account in Settings first."))

	from isoft_angola_hr.isoft_angola_hr.api import _payroll_cost_center

	cost_center = _payroll_cost_center(frappe._dict(
		employee=advance.employee, company=advance.company,
		department=frappe.db.get_value("Employee", advance.employee, "department")))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Bank Entry"
	je.company = advance.company
	je.posting_date = getdate(nowdate())
	je.cheque_no = advance.name
	je.cheque_date = je.posting_date
	je.user_remark = _("Salary advance: {0} — {1}").format(
		advance.name, advance.employee_name or advance.employee)
	je.append("accounts", {"account": advance_account, "cost_center": cost_center,
	                       "debit_in_account_currency": flt(advance.approved_amount)})
	je.append("accounts", {"account": bank, "cost_center": cost_center,
	                       "credit_in_account_currency": flt(advance.approved_amount)})
	je.insert()
	je.submit()
	advance.db_set("disbursement_entry", je.name, update_modified=False)
	advance.db_set("outstanding_amount", flt(advance.approved_amount), update_modified=False)
	return je.name


# --------------------------------------------------------------------------- #
# Payroll recovery
# --------------------------------------------------------------------------- #
def due_recovery(employee, period_start, period_end, exclude_slip=None):
	"""How much advance to recover from one payroll period, and from which advances.

	EXACTLY ONE installment per advance per payroll run — the oldest one that has come
	due. Two rules that both look reasonable are wrong here:

	* matching every installment whose period OVERLAPS the payroll period takes two
	  instalments in one month whenever the recovery schedule and the payroll cycle are
	  not aligned (a 23rd-to-22nd payroll against a calendar schedule), so the employee
	  is deducted twice for one scheduled instalment;
	* matching only the installment starting inside the period silently skips any
	  instalment whose period was never processed, and the advance is never repaid.

	Taking the oldest due instalment is deterministic, self-correcting after a missed
	run, and can never deduct two instalments at once. Recovering the same instalment
	twice is prevented by keying on the instalment itself, not on the amount.
	"""
	rows = frappe.db.sql(
		"""select a.name as advance, i.name as installment, i.amount, i.recovered, i.status,
			i.salary_slip, i.period_start
		from `tabIsoft Salary Advance` a
		join `tabIsoft Advance Installment` i
		  on i.parent = a.name and i.parenttype = 'Isoft Salary Advance'
		where a.employee = %s and a.status in ('Disbursed', 'Recovering')
		  and i.status in ('Pending', 'Partial')
		  and i.period_start <= %s
		order by i.period_start, i.idx""",
		(employee, getdate(period_end)), as_dict=True)

	out, total, seen = [], 0.0, set()
	for r in rows:
		if r.advance in seen:
			continue          # one instalment per advance per run
		if r.salary_slip and r.salary_slip != exclude_slip:
			continue          # already taken by another slip
		remaining = flt(flt(r.amount) - flt(r.recovered), 2)
		if remaining <= 0:
			continue
		seen.add(r.advance)
		out.append({"advance": r.advance, "installment": r.installment, "amount": remaining})
		total += remaining
	return flt(total, 2), out


def record_recovery(slip, recovered_amount, plan):
	"""Write back what payroll actually recovered, installment by installment.

	The engine may have capped the recovery, so the amount taken is distributed over the
	planned installments in order and anything left stays outstanding.
	"""
	remaining = flt(recovered_amount, 2)
	touched = set()
	for item in plan:
		if remaining <= 0:
			break
		take = min(remaining, flt(item["amount"]))
		row = frappe.db.get_value("Isoft Advance Installment", item["installment"],
		                          ["amount", "recovered"], as_dict=True)
		if not row:
			continue
		new_recovered = flt(flt(row.recovered) + take, 2)
		status = "Recovered" if new_recovered >= flt(row.amount) - 0.005 else "Partial"
		frappe.db.set_value("Isoft Advance Installment", item["installment"], {
			"recovered": new_recovered, "status": status, "salary_slip": slip.name,
		}, update_modified=False)
		remaining = flt(remaining - take, 2)
		touched.add(item["advance"])

	for advance in touched:
		refresh_balance(advance)
	return touched


def release_recovery(slip):
	"""Give the installments back when a salary slip is cancelled or recalculated.

	Without this, cancelling payroll would leave the advance looking repaid while the
	employee never actually had the deduction taken.
	"""
	rows = frappe.db.sql(
		"""select name, parent from `tabIsoft Advance Installment`
		where salary_slip = %s""", slip.name, as_dict=True)
	for row in rows:
		frappe.db.set_value("Isoft Advance Installment", row.name, {
			"recovered": 0, "status": "Pending", "salary_slip": None,
		}, update_modified=False)
	for advance in {r.parent for r in rows}:
		refresh_balance(advance)
	return len(rows)


def refresh_balance(advance_name):
	"""Recompute recovered / outstanding and settle the advance when it is fully repaid."""
	doc = frappe.get_doc("Isoft Salary Advance", advance_name)
	recovered = flt(sum(flt(i.recovered) for i in doc.schedule), 2)
	outstanding = flt(flt(doc.approved_amount) - recovered, 2)
	doc.db_set("recovered_amount", recovered, update_modified=False)
	doc.db_set("outstanding_amount", max(0.0, outstanding), update_modified=False)
	if doc.status in OPEN_STATES:
		if outstanding <= 0.005:
			doc.db_set("status", SETTLED, update_modified=False)
		elif recovered > 0:
			doc.db_set("status", RECOVERING, update_modified=False)
		else:
			doc.db_set("status", DISBURSED, update_modified=False)
	return outstanding


def outstanding_for(employee):
	"""Total still owed by an employee — used by the final settlement and the reports."""
	value = frappe.db.sql(
		"""select sum(outstanding_amount) from `tabIsoft Salary Advance`
		where employee = %s and status in ('Disbursed', 'Recovering')""", employee)
	return flt(value[0][0] if value and value[0][0] else 0.0, 2)


def open_advances(company=None):
	conditions, values = ["a.status in ('Disbursed', 'Recovering')"], []
	if company:
		conditions.append("a.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="a")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.company, a.approved_amount,
			a.recovered_amount, a.outstanding_amount, a.status, a.request_date
		from `tabIsoft Salary Advance` a where {0}
		order by a.request_date""".format(" and ".join(conditions)), values, as_dict=True)
