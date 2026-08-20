# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The employee lifecycle: onboarding readiness, HR readiness, timeline, HR dashboard.

Two readiness questions already exist in this app — *is this payroll period clean?*
(payroll_readiness) and *is this installation fit for production?* (production_readiness).
This module adds the third: **is the HR data behind those two actually complete?**

The timeline is deliberately DERIVED, never stored. Every event is read from the document
that already records it — the contract, the promotion, the salary change, the leave — so
there is no second copy of the truth to drift out of date, and deleting a document
removes its event rather than orphaning it.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

READY = "READY"
WARNING = "WARNING"
BLOCKED = "BLOCKED"
#: Neither ready nor a problem: something that is simply not switched on. Used for
#: optional self-service, which most employees on this site deliberately do not have.
OPTIONAL = "OPTIONAL"


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #
def onboarding_checklist(employee):
	"""What is still missing before this person can be fully administered and paid.

	Severity is honest about consequence: no Salary Profile means no pay (BLOCKED); no
	NIF means an incomplete tax report (WARNING). Employee creation is never blocked by
	any of it — a new joiner exists before their paperwork does.
	"""
	perms.require(perms.EMPLOYEE_READ)
	row = frappe.db.get_value(
		"Employee", employee,
		["name", "employee_name", "company", "department", "designation", "branch",
		 "date_of_joining", "reports_to", "holiday_list", "default_shift",
		 "payroll_cost_center", "custom_nif", "custom_inss_number", "custom_iban",
		 "cell_number", "personal_email", "emergency_phone_number", "person_to_be_contacted",
		 "date_of_birth", "gender", "status", "user_id"], as_dict=True)
	if not row:
		frappe.throw(_("Employee {0} does not exist.").format(employee))
	perms.require_company(row.company)

	items = []

	def add(key, label, ok, severity=WARNING, hint=None):
		items.append({"key": key, "label": label, "ok": bool(ok),
		              "status": READY if ok else severity, "hint": None if ok else hint})

	# Identity
	add("identity", _("Date of birth and gender"), row.date_of_birth and row.gender)
	add("contact", _("Mobile number"), row.cell_number)
	add("emergency", _("Emergency contact"),
	    row.emergency_phone_number and row.person_to_be_contacted)

	# Employment
	add("department", _("Department"), row.department)
	add("designation", _("Designation"), row.designation)
	add("manager", _("Reports to"), row.reports_to,
	    hint=_("Used for the org chart and for team views. HR can record and decide every "
	           "request for this employee without it."))
	add("holiday_list", _("Holiday list"), row.holiday_list,
	    hint=_("Working days fall back to the company default without one."))
	add("shift", _("Working schedule"), row.default_shift,
	    hint=_("Attendance and overtime use the Angola default calendar without a shift."))
	add("cost_center", _("Payroll cost center"), row.payroll_cost_center)

	contract = contracts.active_contract(employee)
	add("contract", _("Employment contract"), contract, severity=BLOCKED,
	    hint=_("No active employment contract is recorded for this employee."))

	profile = frappe.db.exists("Isoft Salary Profile", {"employee": employee})
	add("salary_profile", _("Salary profile"), profile, severity=BLOCKED,
	    hint=_("Payroll cannot be calculated without one."))

	# Statutory and banking
	add("nif", _("NIF"), (row.custom_nif or "").strip(),
	    hint=_("Needed for the IRT report, not for payment."))
	add("inss", _("Social Security number"), (row.custom_inss_number or "").strip(),
	    hint=_("Needed for the INSS report, not for payment."))
	add("iban", _("IBAN"), (row.custom_iban or "").strip(),
	    hint=_("The employee cannot be included in the bank transfer file without it."))

	# Mandatory documents
	mandatory = frappe.get_all("Isoft Document Type",
	                           filters={"is_mandatory": 1, "disabled": 0}, pluck="name")
	if mandatory:
		held = set(frappe.db.sql_list(
			"""select document_type from `tabIsoft Employee Document`
			where employee = %s and status != 'Superseded'""", employee))
		missing = [d for d in mandatory if d not in held]
		add("documents", _("Required documents"), not missing,
		    hint=_("Missing: {0}").format(", ".join(missing)) if missing else None)

	# NOT a readiness item. The product is operated by HR on the employee's behalf, so a
	# missing User ID means "self-service is not available to this person", never "this
	# employee is not ready". It used to be counted as a warning, which is how 127
	# perfectly administrable employees looked incomplete on the onboarding screen.
	add("ess_access", _("Self-service access (User ID)"), row.user_id, severity=OPTIONAL,
	    hint=_("Optional. Without a linked user the employee cannot open /ess to see their "
	           "own payslips — HR administers everything for them either way."))

	blocked = [i for i in items if i["status"] == BLOCKED]
	warnings = [i for i in items if i["status"] == WARNING]
	# Optional items are excluded from the completeness ratio for the same reason they are
	# excluded from the status: an employee is not 11/12 complete because they do not have
	# a login the company never intended to give them.
	counted = [i for i in items if i["status"] != OPTIONAL]
	return {
		"employee": row.name, "employee_name": row.employee_name, "company": row.company,
		"status": BLOCKED if blocked else (WARNING if warnings else READY),
		"complete": len([i for i in counted if i["ok"]]), "total": len(counted),
		"blocked": len(blocked), "warnings": len(warnings), "items": items,
		"self_service": bool(row.user_id),
	}


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #
def timeline(employee, limit=200):
	"""The employment history, assembled from the documents that already record it."""
	perms.require(perms.EMPLOYEE_READ)
	company = frappe.db.get_value("Employee", employee, "company")
	perms.require_company(company)
	show_pay = perms.can(perms.SALARY_PROFILE_READ)

	events = []

	def add(date, kind, title, detail=None, doctype=None, name=None):
		if not date:
			return
		events.append({"date": str(getdate(date)), "kind": kind, "title": title,
		               "detail": detail, "doctype": doctype, "name": name})

	row = frappe.db.get_value("Employee", employee,
	                          ["date_of_joining", "final_confirmation_date", "relieving_date",
	                           "status", "employee_name"], as_dict=True) or {}
	add(row.get("date_of_joining"), "join", _("Joined the company"))
	add(row.get("final_confirmation_date"), "probation", _("Employment confirmed"))

	for c in frappe.get_all("Isoft Employment Contract", filters={"employee": employee},
	                        fields=["name", "contract_type", "start_date", "end_date",
	                                "status", "probation_end", "probation_decision",
	                                "probation_decision_date", "previous_contract",
	                                "terminated_on", "termination_reason"],
	                        order_by="start_date"):
		add(c.start_date, "contract",
		    _("Contract renewed") if c.previous_contract else _("Contract started"),
		    _("{0} — {1}").format(c.contract_type, _(c.status)),
		    "Isoft Employment Contract", c.name)
		if c.probation_decision and c.probation_decision_date:
			add(c.probation_decision_date, "probation",
			    _("Probation {0}").format(_(c.probation_decision)), None,
			    "Isoft Employment Contract", c.name)
		if c.terminated_on:
			add(c.terminated_on, "termination", _("Contract terminated"),
			    c.termination_reason, "Isoft Employment Contract", c.name)

	for t in frappe.get_all("Employee Transfer",
	                        filters={"employee": employee, "docstatus": 1},
	                        fields=["name", "transfer_date", "new_company"]):
		add(t.transfer_date, "transfer", _("Transferred"),
		    _("to {0}").format(t.new_company) if t.new_company else None,
		    "Employee Transfer", t.name)

	for p in frappe.get_all("Employee Promotion",
	                        filters={"employee": employee, "docstatus": 1},
	                        fields=["name", "promotion_date"]):
		add(p.promotion_date, "promotion", _("Promoted"), None, "Employee Promotion", p.name)

	for s in frappe.get_all("Isoft Salary Change",
	                        filters={"employee": employee, "status": "Applied"},
	                        fields=["name", "effective_date", "change_type", "new_base",
	                                "current_base", "percentage_change", "reason"]):
		# The amounts are only included for users who may see compensation; everyone else
		# sees that a change happened, which is legitimate HR history.
		detail = (_("{0} → {1} ({2}%)").format(flt(s.current_base, 2), flt(s.new_base, 2),
		                                       flt(s.percentage_change))
		          if show_pay else _("Amount hidden"))
		add(s.effective_date, "salary", _("Salary change — {0}").format(_(s.change_type)),
		    detail, "Isoft Salary Change", s.name)

	for a in frappe.get_all("Isoft Salary Advance",
	                        filters={"employee": employee,
	                                 "status": ["in", ["Disbursed", "Recovering", "Settled"]]},
	                        fields=["name", "request_date", "approved_amount", "status"]):
		add(a.request_date, "advance", _("Salary advance"),
		    _("{0} — {1}").format(flt(a.approved_amount, 2), _(a.status)) if show_pay
		    else _(a.status), "Isoft Salary Advance", a.name)

	for d in frappe.get_all("Isoft Employee Document",
	                        filters={"employee": employee, "confidential": 0},
	                        fields=["name", "document_type", "issue_date", "expiry_date"]):
		add(d.issue_date, "document", _("Document recorded"), d.document_type,
		    "Isoft Employee Document", d.name)

	if row.get("relieving_date"):
		add(row["relieving_date"], "termination", _("Left the company"))

	events.sort(key=lambda e: e["date"], reverse=True)
	return events[:cint(limit)]


# --------------------------------------------------------------------------- #
# HR readiness
# --------------------------------------------------------------------------- #
def hr_readiness(company=None):
	"""The HR-data counterpart of Payroll Readiness."""
	perms.require(perms.HR_READINESS)
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	perms.require_company(company)

	employees = frappe.get_all(
		"Employee", filters={"status": "Active", "company": company},
		fields=["name", "employee_name", "custom_nif", "custom_inss_number", "custom_iban",
		        "reports_to", "department", "designation"], limit_page_length=5000)
	names = [e.name for e in employees]

	with_contract = set()
	if names:
		with_contract = set(frappe.db.sql_list(
			"""select distinct employee from `tabIsoft Employment Contract`
			where employee in ({0}) and status in ('Active', 'Expiring')""".format(
				", ".join(["%s"] * len(names))), names))
	with_profile = set()
	if names:
		with_profile = set(frappe.db.sql_list(
			"""select distinct employee from `tabIsoft Salary Profile`
			where employee in ({0})""".format(", ".join(["%s"] * len(names))), names))

	def count(predicate):
		return sum(1 for e in employees if predicate(e))

	blockers, warnings = [], []

	def add(bucket, code, label, n, action):
		if n:
			bucket.append({"code": code, "label": label, "count": n, "action": action})

	add(blockers, "HR-001", _("Employees without an employment contract"),
	    len([e for e in employees if e.name not in with_contract]),
	    _("Create and approve an Isoft Employment Contract."))
	add(blockers, "HR-002", _("Employees without a Salary Profile"),
	    len([e for e in employees if e.name not in with_profile]),
	    _("Create a Salary Profile, or set the employee Inactive."))
	add(warnings, "HR-003", _("Employees without a NIF"),
	    count(lambda e: not (e.custom_nif or "").strip()), _("Collect the NIF."))
	add(warnings, "HR-004", _("Employees without a Social Security number"),
	    count(lambda e: not (e.custom_inss_number or "").strip()),
	    _("Collect the INSS number."))
	add(warnings, "HR-005", _("Employees without an IBAN"),
	    count(lambda e: not (e.custom_iban or "").strip()), _("Collect the IBAN."))
	add(warnings, "HR-006", _("Employees without a manager"),
	    count(lambda e: not e.reports_to),
	    _("Set Reports To for the org chart and team views. HR can already record and "
	      "decide every request for these employees without it."))
	add(warnings, "HR-007", _("Employees without a department"),
	    count(lambda e: not e.department), _("Set the department."))

	expiring = contracts.expiring_contracts(company=company)
	add(warnings, "HR-008", _("Contracts expiring"), len(expiring),
	    _("Renew or let them expire deliberately."))
	probations = contracts.probation_reviews_due(company=company)
	due = [p for p in probations if cint(p.days_left) <= contracts.probation_review_window()]
	add(warnings, "HR-009", _("Probation reviews due or overdue"), len(due),
	    _("Confirm, extend or end the probation."))

	docs_expiring = frappe.db.sql(
		"""select count(*) from `tabIsoft Employee Document` d
		join `tabEmployee` e on e.name = d.employee
		where e.company = %s and d.status in ('Expiring', 'Expired')""", company)[0][0]
	add(warnings, "HR-010", _("Employee documents expiring or expired"), docs_expiring,
	    _("Collect the renewed document."))

	pending = pending_approvals(company)
	ready = len([e for e in employees
	             if e.name in with_contract and e.name in with_profile])

	return {
		"company": company,
		"total_employees": len(employees),
		"ready": ready,
		"blocked": sum(b["count"] for b in blockers),
		"status": BLOCKED if blockers else (WARNING if warnings else READY),
		"blockers": blockers,
		"warnings": warnings,
		"pending_approvals": pending,
	}


def pending_approvals(company=None):
	"""The HR approval inbox — everything waiting on a person, in one list.

	Made of one query per document type rather than a union, because each carries a
	different notion of "what is this about", and a generic inbox that only shows
	document IDs is an inbox nobody uses.

	Every row now also carries WHO ASKED, WHO RECORDED IT and WHO DECIDES. In HR-operated
	mode the employee named on the request is usually not the person who typed it, and an
	approver looking at a list that shows only the employee cannot tell whether they are
	about to approve their own work.
	"""
	perms.require(perms.HR_READINESS)
	out = []

	#: Which role clears each queue. Read from the enforcement table rather than written
	#: out here, so the inbox cannot advertise an approver the code does not require.
	def approver_for(action):
		return ", ".join(sorted(perms.ACTION_ROLES[action]))

	def collect(doctype, label, status, fields, detail, action, view):
		conditions = ["status = %s"]
		values = [status]
		if company:
			conditions.append("company = %s")
			values.append(company)
		scope, scope_values = perms.company_filter_sql()
		if scope:
			conditions.append(scope)
			values.extend(scope_values)
		for row in frappe.db.sql(
			"""select {0} from `tab{1}` where {2} order by modified desc limit 50""".format(
				fields, doctype, " and ".join(conditions)), values, as_dict=True):
			out.append({"doctype": doctype, "type": label, "name": row["name"],
			            "employee": row.get("employee"),
			            "employee_name": row.get("employee_name"),
			            "detail": detail(row), "status": status,
			            "requested_by": row.get("requested_by") or row.get("owner"),
			            "recorded_by": row.get("owner"),
			            "request_source": row.get("request_source"),
			            "approver": approver_for(action), "view": view,
			            "modified": str(row.get("modified") or "")})

	collect("Isoft Employment Contract", _("Employment contract"), "Pending Approval",
	        "name, employee, employee_name, contract_type, start_date, owner, modified",
	        lambda r: "{0} — {1}".format(r.get("contract_type"), r.get("start_date")),
	        perms.CONTRACT_APPROVE, "contracts")
	collect("Isoft Salary Change", _("Salary change"), "Pending Approval",
	        "name, employee, employee_name, change_type, effective_date, requested_by, "
	        "request_source, owner, modified",
	        lambda r: "{0} — {1}".format(_(r.get("change_type") or ""), r.get("effective_date")),
	        perms.SALARY_CHANGE_APPROVE, "salarychanges")
	collect("Isoft Salary Advance", _("Salary advance"), "Pending Approval",
	        "name, employee, employee_name, requested_amount, requested_by, request_source, "
	        "owner, modified",
	        lambda r: flt(r.get("requested_amount"), 2),
	        perms.ADVANCE_APPROVE, "advances")
	collect("Isoft Bank Change Request", _("Bank details"), "Pending Approval",
	        "name, employee, employee_name, current_iban_masked, requested_by, "
	        "request_source, owner, modified",
	        lambda r: r.get("current_iban_masked") or "",
	        perms.BANK_CHANGE_APPROVE, "bankchanges")

	# Leave lives in ERPNext and has its own status vocabulary.
	leave_conditions, leave_values = ["docstatus = 0", "status = 'Open'"], []
	if company:
		leave_conditions.append("company = %s")
		leave_values.append(company)
	for row in frappe.db.sql(
		"""select name, employee, employee_name, leave_type, from_date, to_date, owner,
			modified
		from `tabLeave Application` where {0} order by modified desc limit 50""".format(
			" and ".join(leave_conditions)), leave_values, as_dict=True):
		out.append({"doctype": "Leave Application", "type": _("Leave"), "name": row.name,
		            "employee": row.employee, "employee_name": row.employee_name,
		            "detail": "{0}: {1} → {2}".format(row.leave_type, row.from_date, row.to_date),
		            "status": "Open", "requested_by": row.owner, "recorded_by": row.owner,
		            "request_source": None,
		            "approver": approver_for(perms.LEAVE_APPROVE), "view": "leaves",
		            "modified": str(row.modified)})

	out.sort(key=lambda r: r["modified"], reverse=True)
	return out


# --------------------------------------------------------------------------- #
# HR dashboard
# --------------------------------------------------------------------------- #
def hr_dashboard(company=None):
	"""Operational HR metrics — the numbers somebody acts on, not vanity charts."""
	perms.require(perms.HR_READINESS)
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	perms.require_company(company)
	today = getdate(nowdate())
	month_start = frappe.utils.get_first_day(today)

	active = frappe.db.count("Employee", {"status": "Active", "company": company})
	new_hires = frappe.db.count("Employee", {"company": company,
	                                         "date_of_joining": [">=", month_start]})
	leavers = frappe.db.count("Employee", {"company": company,
	                                       "relieving_date": [">=", month_start]})
	on_leave = frappe.db.sql(
		"""select count(distinct la.employee) from `tabLeave Application` la
		join `tabEmployee` e on e.name = la.employee
		where e.company = %s and la.docstatus = 1 and la.status = 'Approved'
		  and la.from_date <= %s and la.to_date >= %s""", (company, today, today))[0][0]
	exceptions = frappe.db.sql(
		"""select count(*) from `tabIsoft Attendance Occurrence` o
		join `tabEmployee` e on e.name = o.employee
		where e.company = %s
		  and o.status in ('Pending Justification', 'Unjustified')""", company)[0][0]

	headcount = {
		"by_department": frappe.db.sql(
			"""select ifnull(department, '—') as label, count(*) as n from `tabEmployee`
			where status = 'Active' and company = %s group by department
			order by n desc""", company, as_dict=True),
		"by_designation": frappe.db.sql(
			"""select ifnull(designation, '—') as label, count(*) as n from `tabEmployee`
			where status = 'Active' and company = %s group by designation
			order by n desc limit 15""", company, as_dict=True),
		"by_employment_type": frappe.db.sql(
			"""select ifnull(employment_type, '—') as label, count(*) as n from `tabEmployee`
			where status = 'Active' and company = %s group by employment_type
			order by n desc""", company, as_dict=True),
	}

	return {
		"company": company,
		"active_employees": active,
		"new_hires_this_month": new_hires,
		"leavers_this_month": leavers,
		"on_leave_today": on_leave,
		"attendance_exceptions": exceptions,
		"contracts_expiring": len(contracts.expiring_contracts(company=company)),
		"probations_due": len(contracts.probation_reviews_due(company=company)),
		"documents_expiring": frappe.db.sql(
			"""select count(*) from `tabIsoft Employee Document` d
			join `tabEmployee` e on e.name = d.employee
			where e.company = %s and d.status in ('Expiring', 'Expired')""", company)[0][0],
		"open_advances": len(advances.open_advances(company=company)),
		"pending_approvals": len(pending_approvals(company)),
		"headcount": headcount,
	}
