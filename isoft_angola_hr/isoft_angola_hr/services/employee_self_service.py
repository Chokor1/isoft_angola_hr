# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Employee Self-Service — and, more importantly, its boundary.

Every function here answers the same question first: *which Employee is the logged-in
user?* Everything else is scoped to that one record. There is no employee parameter that
a curious user could change, because the parameter does not exist — the employee is
derived from the session, never accepted from the caller. That is the whole security
model, and it is why these functions are safe to expose to the Employee role.

Payroll is shown ONLY from submitted salary slips. A draft slip is a calculation
somebody is still working on; showing it would have employees querying a net pay that
has not been approved and may still change.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import contracts

#: Fields an employee may see about themselves. Deliberately a whitelist: a blacklist
#: would leak every future field somebody adds to Employee.
_PROFILE_FIELDS = (
	"name", "employee_name", "company", "department", "designation", "branch",
	"date_of_joining", "employment_type", "status", "cell_number", "personal_email",
	"company_email", "current_address", "emergency_phone_number",
	"person_to_be_contacted", "relation", "date_of_birth", "gender", "marital_status",
	"holiday_list", "reports_to", "custom_nif", "custom_inss_number",
)

#: Fields an employee may request to change about themselves. Everything else — salary,
#: department, designation, NIF, INSS, contract, manager — needs HR.
SELF_EDITABLE_FIELDS = (
	"cell_number", "personal_email", "current_address", "emergency_phone_number",
	"person_to_be_contacted", "relation",
)


def current_employee(user=None, raise_exception=True):
	"""The Employee record linked to the logged-in user."""
	user = user or frappe.session.user
	name = frappe.db.get_value("Employee", {"user_id": user, "status": ("!=", "Left")}, "name")
	if not name:
		name = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not name and raise_exception:
		frappe.throw(
			_("Your user account is not linked to an employee record. Ask HR to set the "
			  "User ID on your Employee record."), frappe.PermissionError)
	return name


def _own(employee):
	"""Assert that a record belongs to the caller. The last line of defence for every
	function that takes a document name rather than deriving it."""
	me = current_employee()
	if employee != me:
		frappe.throw(_("You may only access your own records."), frappe.PermissionError)
	return me


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
def my_profile():
	me = current_employee()
	row = frappe.db.get_value("Employee", me, list(_PROFILE_FIELDS), as_dict=True) or {}
	# The bank account is shown masked. An employee needs to recognise which account
	# their salary goes to; they do not need the full number rendered on a web page.
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_bank_change_request.isoft_bank_change_request import (
		mask_iban,
	)

	row["iban_masked"] = mask_iban(frappe.db.get_value("Employee", me, "custom_iban"))
	row["editable_fields"] = list(SELF_EDITABLE_FIELDS)
	contract = contracts.active_contract(me)
	if contract:
		row["contract"] = frappe.db.get_value(
			"Isoft Employment Contract", contract,
			["name", "contract_type", "start_date", "end_date", "is_open_ended", "status",
			 "probation_end", "probation_status"], as_dict=True)
	return row


def update_my_profile(values):
	"""Update only the contact fields an employee owns. Anything else is refused."""
	me = current_employee()
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	rejected = [f for f in values if f not in SELF_EDITABLE_FIELDS]
	if rejected:
		frappe.throw(
			_("These fields cannot be changed by you: {0}. Ask HR — salary, position, tax and "
			  "bank details all require approval.").format(", ".join(sorted(rejected))),
			frappe.PermissionError)
	clean = {f: values[f] for f in values if f in SELF_EDITABLE_FIELDS}
	if clean:
		frappe.db.set_value("Employee", me, clean)
	return clean


def request_bank_change(new_iban, bank_name=None, proof_document=None):
	"""An employee can ASK for a new IBAN; only HR can approve it."""
	me = current_employee()
	doc = frappe.get_doc({
		"doctype": "Isoft Bank Change Request", "employee": me,
		"new_iban": new_iban, "bank_name": bank_name, "proof_document": proof_document,
	}).insert(ignore_permissions=True)
	return doc.name


# --------------------------------------------------------------------------- #
# Payroll
# --------------------------------------------------------------------------- #
def my_payslips(limit=24):
	"""Submitted payslips only — never a draft."""
	me = current_employee()
	return frappe.db.sql(
		"""select name, start_date, end_date, posting_date, gross_pay, total_deduction,
			net_pay, irt_amount, ss_employee_amount
		from `tabIsoft Salary Slip`
		where employee = %s and docstatus = 1
		order by end_date desc limit %s""", (me, cint(limit)), as_dict=True)


def my_payslip(name):
	"""One payslip, explained in words rather than field names."""
	me = current_employee()
	slip = frappe.db.get_value(
		"Isoft Salary Slip", name,
		["name", "employee", "docstatus", "start_date", "end_date", "posting_date",
		 "payment_days", "total_working_days", "gross_pay", "total_deduction", "net_pay",
		 "taxable_income", "ss_base", "ss_employee_rate", "ss_employee_amount",
		 "irt_bracket_from", "irt_bracket_to", "irt_rate", "irt_parcela_fixa", "irt_amount",
		 "advance_recovery", "currency"], as_dict=True)
	if not slip:
		frappe.throw(_("Payslip not found."), frappe.DoesNotExistError)
	if slip.employee != me:
		frappe.throw(_("You may only view your own payslips."), frappe.PermissionError)
	if cint(slip.docstatus) != 1:
		# A draft slip is not payroll yet. Saying so is better than pretending it does
		# not exist, because the employee may have been told it was "being prepared".
		frappe.throw(_("This payslip has not been approved yet and cannot be viewed."),
		             frappe.PermissionError)

	slip["earnings"] = frappe.db.sql(
		"""select salary_component, amount from `tabIsoft Salary Detail`
		where parent = %s and parentfield = 'earnings' and ifnull(do_not_include_in_total,0)=0
		order by idx""", name, as_dict=True)
	slip["deductions"] = frappe.db.sql(
		"""select salary_component, amount from `tabIsoft Salary Detail`
		where parent = %s and parentfield = 'deductions' order by idx""", name, as_dict=True)

	# The statutory trace, phrased for a person rather than for an auditor.
	slip["explanation"] = {
		"social_security": {
			"label": _("Segurança Social"),
			"base": _("Base de incidência: {0}").format(flt(slip.ss_base, 2)),
			"rate": _("Taxa: {0}%").format(flt(slip.ss_employee_rate)),
			"amount": _("Valor descontado: {0}").format(flt(slip.ss_employee_amount, 2)),
		},
		"irt": {
			"label": _("IRT"),
			"taxable": _("Rendimento tributável: {0}").format(flt(slip.taxable_income, 2)),
			"bracket": _("Escalão: {0} – {1}").format(
				flt(slip.irt_bracket_from, 2),
				flt(slip.irt_bracket_to, 2) if flt(slip.irt_bracket_to) else _("acima")),
			"rate": _("Taxa: {0}%").format(flt(slip.irt_rate)),
			"fixed": _("Parcela fixa: {0}").format(flt(slip.irt_parcela_fixa, 2)),
			"amount": _("Valor descontado: {0}").format(flt(slip.irt_amount, 2)),
		},
		"days": _("Dias pagos: {0} de {1}").format(flt(slip.payment_days),
		                                           flt(slip.total_working_days)),
	}
	return slip


# --------------------------------------------------------------------------- #
# Leave, attendance, requests
# --------------------------------------------------------------------------- #
def my_leave(limit=50):
	me = current_employee()
	return frappe.db.sql(
		"""select name, leave_type, from_date, to_date, total_leave_days, status,
			description, half_day, docstatus
		from `tabLeave Application` where employee = %s and docstatus < 2
		order by from_date desc limit %s""", (me, cint(limit)), as_dict=True)


def leave_balance_for(employee, as_of=None):
	"""Entitlement, used, pending and available — the four numbers an employee asks for.

	The arithmetic is ERPNext's — ``get_leave_balance_on``, ``get_leaves_for_period`` and
	``get_leaves_pending_approval_for_period`` are the entitlement authority on this bench
	and already understand carry-forward, expiry and allocation periods. Phase 3 computed
	these numbers with its own SQL, which ignored carry-forward and expiry and therefore
	disagreed with the figure ERPNext shows on the Leave Application form itself. Two
	different balances for the same employee is worse than either being approximate.

	What is deliberately NOT reused is ERPNext's ``get_leave_details`` wrapper: its last
	line calls ``frappe.get_list("Leave Type")``, which applies role permissions, so the
	whole balance screen would fail with a bare PermissionError on any site whose Employee
	role has been trimmed. The functions called here read through ``frappe.qb`` and
	``get_all`` and have no such dependency.
	"""
	from erpnext.hr.doctype.leave_application.leave_application import (
		get_leave_allocation_records,
		get_leave_balance_on,
		get_leaves_for_period,
		get_leaves_pending_approval_for_period,
	)

	as_of = getdate(as_of or nowdate())
	rows = []
	allocations = get_leave_allocation_records(employee, as_of) or {}
	for leave_type in sorted(allocations):
		alloc = allocations.get(leave_type) or frappe._dict()
		remaining = get_leave_balance_on(
			employee, leave_type, as_of, to_date=alloc.to_date,
			consider_all_leaves_in_the_allocation_period=True)
		taken = get_leaves_for_period(employee, leave_type, alloc.from_date,
		                              alloc.to_date) * -1
		pending = get_leaves_pending_approval_for_period(
			employee, leave_type, alloc.from_date, alloc.to_date)
		expired = flt(alloc.total_leaves_allocated) - (flt(remaining) + flt(taken))
		rows.append({
			"leave_type": leave_type,
			"entitlement": flt(alloc.total_leaves_allocated, 2),
			"used": flt(taken, 2),
			"pending": flt(pending, 2),
			"expired": flt(max(0.0, expired), 2),
			"available": flt(remaining, 2),
		})
	return rows


def my_leave_balance(as_of=None):
	return leave_balance_for(current_employee(), as_of=as_of)


def my_attendance(from_date=None, to_date=None):
	me = current_employee()
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else frappe.utils.add_months(to_date, -1)
	attendance = frappe.db.sql(
		"""select attendance_date, status, working_hours, custom_overtime_hours, leave_type
		from `tabAttendance` where employee = %s and docstatus = 1
		  and attendance_date between %s and %s order by attendance_date desc""",
		(me, from_date, to_date), as_dict=True)
	occurrences = frappe.db.sql(
		"""select name, occurrence_date, status, hours, occurrence_type,
			justification_deadline, justification_reason, justification_document
		from `tabIsoft Attendance Occurrence`
		where employee = %s and occurrence_date between %s and %s
		order by occurrence_date desc""", (me, from_date, to_date), as_dict=True)
	return {"from_date": str(from_date), "to_date": str(to_date),
	        "attendance": attendance, "occurrences": occurrences}


def my_requests():
	"""Everything the employee has asked for and its current state — one list."""
	me = current_employee()
	out = []
	for doctype, label, fields in (
		("Isoft Bank Change Request", _("Bank details"),
		 "name, status, requested_at as when_, new_iban as detail"),
		("Isoft Salary Advance", _("Salary advance"),
		 "name, status, requested_at as when_, requested_amount as detail"),
	):
		for row in frappe.db.sql(
			"""select {0} from `tab{1}` where employee = %s
			order by creation desc limit 20""".format(fields, doctype), me, as_dict=True):
			row["type"] = label
			row["doctype"] = doctype
			# A bank request must not echo the full new IBAN back into a list view.
			if doctype == "Isoft Bank Change Request":
				from isoft_angola_hr.isoft_angola_hr.doctype.isoft_bank_change_request.isoft_bank_change_request import (
					mask_iban,
				)
				row["detail"] = mask_iban(row.get("detail"))
			out.append(row)
	out.sort(key=lambda r: str(r.get("when_") or ""), reverse=True)
	return out


def my_advances():
	me = current_employee()
	return frappe.db.sql(
		"""select name, request_date, approved_amount, recovered_amount, outstanding_amount,
			status, installments
		from `tabIsoft Salary Advance` where employee = %s
		order by request_date desc limit 20""", me, as_dict=True)


def my_documents():
	"""An employee sees their own non-confidential documents.

	Confidential and medical documents are deliberately excluded even from the person
	they belong to in this view: they are HR-held records (criminal record checks,
	medical certificates supplied to HR), and surfacing them here would create a second
	uncontrolled distribution channel.
	"""
	me = current_employee()
	return frappe.db.sql(
		"""select name, document_type, document_number, issue_date, expiry_date, status
		from `tabIsoft Employee Document`
		where employee = %s and ifnull(confidential, 0) = 0
		order by expiry_date is null, expiry_date""", me, as_dict=True)


def my_document(name):
	"""One of my documents, with the attachment URL — never somebody else's, never a
	confidential one."""
	row = frappe.db.get_value(
		"Isoft Employee Document", name,
		["name", "employee", "document_type", "document_number", "issue_date", "expiry_date",
		 "status", "attachment", "confidential", "issuing_authority"], as_dict=True)
	if not row:
		frappe.throw(_("Document not found."), frappe.DoesNotExistError)
	_own(row.employee)
	if cint(row.confidential):
		# Deliberately the same message as "not yours". Distinguishing the two would tell
		# an employee that a confidential document about them exists, which is itself the
		# disclosure HR is trying to control.
		frappe.throw(_("You may only access your own records."), frappe.PermissionError)
	row.pop("confidential", None)
	return row


# --------------------------------------------------------------------------- #
# Leave — ERPNext owns the rules; this owns the scope
# --------------------------------------------------------------------------- #
def leave_preview(leave_type, from_date, to_date, half_day=0, half_day_date=None):
	"""Days requested and the balance that would remain — shown before submitting (§13).

	Advisory only. The server validates the real thing on submission; this exists so an
	employee is not told "insufficient balance" only after filling in the whole form.
	"""
	from erpnext.hr.doctype.leave_application.leave_application import (
		get_leave_balance_on,
		get_number_of_leave_days,
	)

	me = current_employee()
	days = get_number_of_leave_days(me, leave_type, getdate(from_date), getdate(to_date),
	                                cint(half_day), half_day_date)
	balance = get_leave_balance_on(me, leave_type, getdate(from_date),
	                               consider_all_leaves_in_the_allocation_period=True)
	return {
		"leave_type": leave_type,
		"days": flt(days, 2),
		"balance_before": flt(balance, 2),
		"balance_after": flt(flt(balance) - flt(days), 2),
		"sufficient": flt(balance) >= flt(days),
	}


def apply_leave(leave_type, from_date, to_date, description=None, half_day=0,
                half_day_date=None, attachment=None):
	"""Raise a leave application for myself.

	Every rule — balance, overlap, block days, holidays, notice — belongs to ERPNext's
	own Leave Application controller and is left there. This function contributes exactly
	one thing: the employee is the caller, and cannot be anybody else.
	"""
	from erpnext.hr.doctype.leave_application.leave_application import get_leave_approver

	me = current_employee()
	doc = frappe.new_doc("Leave Application")
	doc.employee = me
	doc.leave_type = leave_type
	doc.from_date = getdate(from_date)
	doc.to_date = getdate(to_date)
	doc.half_day = cint(half_day)
	if doc.half_day:
		doc.half_day_date = getdate(half_day_date or from_date)
	doc.description = description
	doc.status = "Open"
	doc.company = frappe.db.get_value("Employee", me, "company")
	doc.leave_approver = get_leave_approver(me)
	if attachment:
		doc.leave_application_attachment = attachment
	# An employee has no write permission on Leave Application; authorisation here is
	# "it is my own record", which was established above and cannot be influenced by the
	# caller. The document's own validation still runs in full.
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status,
	        "total_leave_days": flt(doc.total_leave_days, 2)}


def cancel_leave(name):
	"""Withdraw my own leave request — only while it is still a draft.

	Once submitted it has been decided by somebody else, and unpicking that is a manager
	or HR action, not a self-service one.
	"""
	doc = frappe.get_doc("Leave Application", name)
	_own(doc.employee)
	if cint(doc.docstatus) != 0:
		frappe.throw(
			_("This request has already been decided and can no longer be withdrawn. "
			  "Ask your manager or HR."), frappe.ValidationError)
	doc.delete(ignore_permissions=True)
	return {"name": name, "deleted": True}


# --------------------------------------------------------------------------- #
# Salary advance
# --------------------------------------------------------------------------- #
def request_advance(requested_amount, reason, installments=None, recovery_start_date=None):
	"""Ask for a salary advance.

	The employee proposes; they do not decide. Amount approved, instalment count and
	recovery start are all set by whoever approves — this only records the request, in
	Draft, exactly as the HR-raised path does.
	"""
	me = current_employee()
	if flt(requested_amount) <= 0:
		frappe.throw(_("Enter the amount you are requesting."))
	if not (reason or "").strip():
		frappe.throw(_("A reason is required for a salary advance."))
	doc = frappe.get_doc({
		"doctype": "Isoft Salary Advance",
		"employee": me,
		"request_date": nowdate(),
		"requested_amount": flt(requested_amount),
		"reason": reason,
		"installments": cint(installments) or None,
		"recovery_start_date": getdate(recovery_start_date) if recovery_start_date else None,
	})
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


def dashboard():
	"""The single call the ESS home screen makes."""
	me = current_employee()
	payslips = my_payslips(limit=3)
	documents = my_documents()
	return {
		"employee": me,
		"profile": my_profile(),
		"latest_payslips": payslips,
		"leave_balance": my_leave_balance(),
		"open_requests": [r for r in my_requests()
		                  if r.get("status") in ("Pending Approval", "Draft", "Open")],
		"advances": [a for a in my_advances() if a["status"] in advances.OPEN_STATES],
		"open_leave": [r for r in my_leave(limit=10) if r.get("status") == "Open"],
		"expiring_documents": [d for d in documents
		                       if d.get("status") in ("Expiring", "Expired")],
		"attendance_summary": attendance_summary(),
	}


def attendance_summary(from_date=None, to_date=None):
	"""This month at a glance — present, absent, on leave, and open occurrences."""
	me = current_employee()
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else to_date.replace(day=1)
	rows = frappe.db.sql(
		"""select status, count(*) as n from `tabAttendance`
		where employee = %s and docstatus = 1 and attendance_date between %s and %s
		group by status""", (me, from_date, to_date), as_dict=True)
	summary = {r["status"]: cint(r["n"]) for r in rows}
	# Written as SQL rather than a Frappe filter dict: combining `in` with `between` in
	# db.count builds a row-constructor comparison that MariaDB 10.3 rejects outright
	# ("Illegal parameter data types date and row for operation '='").
	summary["open_occurrences"] = cint(frappe.db.sql(
		"""select count(*) from `tabIsoft Attendance Occurrence`
		where employee = %s and status in ('Pending Justification', 'Unjustified')
		  and occurrence_date between %s and %s""", (me, from_date, to_date))[0][0])
	summary["from_date"] = str(from_date)
	summary["to_date"] = str(to_date)
	return summary
