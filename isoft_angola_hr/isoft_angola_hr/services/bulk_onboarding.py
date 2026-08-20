# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Bulk contract creation, and the onboarding/offboarding checklists around it.

The live site has 85 active employees and no employment contracts, because the module is
new. Creating those one at a time is the single largest piece of manual work Phase 3 left
behind, and it is exactly the kind of task where a well-meaning bulk tool does real
damage.

Three rules make this safe:

1. **Preview before execute.** :func:`preview` never writes. It reports, per employee,
   whether they would be created, skipped or blocked, and why. Nothing is hidden behind
   a summary count (§36).
2. **Per-employee transactions.** Each row runs inside its own savepoint. One employee
   with a broken date cannot roll back the ninety that already worked (§38).
3. **Idempotent.** An employee who already has an active or pending contract covering the
   period is SKIPPED, not duplicated. Running the tool twice produces the same result as
   running it once, which is the property that makes a retry safe after a partial run.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import contract_documents as cd
from isoft_angola_hr.isoft_angola_hr.services import contracts as contract_service
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: Outcomes a preview row can carry. "Blocked" and "Skipped" are different on purpose:
#: skipped means "already done", blocked means "cannot be done and needs a human".
CREATE, SKIP, BLOCK = "Create", "Skipped", "Blocked"


def _existing_contract(employee):
	"""Any contract that would collide with a new one."""
	return frappe.db.get_value(
		"Isoft Employment Contract",
		{"employee": employee,
		 "status": ("in", ("Draft", "Pending Approval", "Active", "Expiring"))},
		["name", "status", "start_date", "end_date"], as_dict=True)


def candidates(company=None, department=None, without_contract=1):
	"""Employees the assistant could act on."""
	perms.require(perms.CONTRACT_READ)
	conditions, values = ["e.status = 'Active'"], []
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	if department:
		conditions.append("e.department = %s")
		values.append(department)
	scope, scope_values = perms.company_filter_sql(alias="e")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	if cint(without_contract):
		conditions.append(
			"""not exists (select 1 from `tabIsoft Employment Contract` c
			where c.employee = e.name
			  and c.status in ('Draft', 'Pending Approval', 'Active', 'Expiring'))""")
	return frappe.db.sql(
		"""select e.name, e.employee_name, e.company, e.department, e.designation,
			e.date_of_joining, e.employment_type, e.holiday_list, e.branch
		from `tabEmployee` e where {0}
		order by e.employee_name""".format(" and ".join(conditions)), values, as_dict=True)


def preview(employees, contract_type, start_date=None, end_date=None, is_open_ended=0,
            probation_months=None, template=None, use_joining_date=0):
	"""Say exactly what would happen to each employee. Writes nothing (§37)."""
	perms.require(perms.CONTRACT_WRITE)
	employees = frappe.parse_json(employees) if isinstance(employees, str) else (employees or [])
	if not employees:
		frappe.throw(_("Select at least one employee."))
	if not contract_type:
		frappe.throw(_("Choose a contract type."))
	if not frappe.db.exists("Isoft Contract Type", contract_type):
		frappe.throw(_("Contract type {0} does not exist.").format(contract_type))
	if not (cint(is_open_ended) or end_date):
		frappe.throw(_("Give an end date, or mark the contracts as open-ended."))

	rows = []
	for employee in employees:
		emp = frappe.db.get_value(
			"Employee", employee,
			["name", "employee_name", "company", "department", "designation",
			 "date_of_joining", "employment_type", "status", "holiday_list"], as_dict=True)
		if not emp:
			rows.append({"employee": employee, "employee_name": employee, "action": BLOCK,
			             "reason": _("Employee record not found.")})
			continue

		row = {
			"employee": emp.name, "employee_name": emp.employee_name,
			"company": emp.company, "department": emp.department,
			"designation": emp.designation, "action": CREATE, "reason": "",
		}

		# Start date: either one date for everybody, or each person's own joining date.
		start = getdate(emp.date_of_joining) if cint(use_joining_date) else (
			getdate(start_date) if start_date else None)
		if not start:
			row.update({"action": BLOCK,
			            "reason": _("No start date — this employee has no date of joining.")})
			rows.append(row)
			continue
		row["start_date"] = str(start)

		end = None
		if not cint(is_open_ended):
			end = getdate(end_date)
			if cint(use_joining_date):
				# A single end date makes no sense against varying start dates, so when
				# starting from each person's joining date the duration is what is fixed.
				months = _months_between(getdate(start_date or start), getdate(end_date))
				end = add_days(add_months(start, months), -1) if months else end
			if end < start:
				row.update({"action": BLOCK,
				            "reason": _("End date falls before the start date.")})
				rows.append(row)
				continue
		row["end_date"] = str(end) if end else None
		row["is_open_ended"] = cint(is_open_ended)

		if emp.status != "Active":
			row.update({"action": BLOCK,
			            "reason": _("Employee is {0}, not Active.").format(emp.status)})
			rows.append(row)
			continue

		existing = _existing_contract(emp.name)
		if existing:
			row.update({"action": SKIP, "existing": existing.name,
			            "reason": _("Already has a {0} contract ({1}).").format(
				            existing.status, existing.name)})
			rows.append(row)
			continue

		if probation_months:
			row["probation_start"] = str(start)
			row["probation_end"] = str(add_days(add_months(start, cint(probation_months)), -1))

		# Warnings do not block — they travel with the row so HR decides.
		missing = [label for field, label in (
			("custom_nif", _("NIF")), ("custom_inss_number", _("Social security number")),
			("custom_iban", _("IBAN")), ("designation", _("Designation")))
			if not frappe.db.get_value("Employee", emp.name, field)]
		if missing:
			row["warning"] = _("Missing: {0}").format(", ".join(missing))

		rows.append(row)

	summary = {"total": len(rows)}
	for action in (CREATE, SKIP, BLOCK):
		summary[action.lower()] = sum(1 for r in rows if r["action"] == action)
	summary["with_warnings"] = sum(1 for r in rows if r.get("warning"))
	return {"rows": rows, "summary": summary,
	        "template": template or (_template_name(contract_type) or None)}


def _template_name(contract_type):
	row = frappe.db.get_value(
		"Isoft Contract Template",
		{"is_active": 1, "contract_type": contract_type}, "name")
	return row or frappe.db.get_value(
		"Isoft Contract Template", {"is_active": 1, "contract_type": ("in", ("", None))}, "name")


def _months_between(start, end):
	if not (start and end):
		return 0
	start, end = getdate(start), getdate(end)
	months = (end.year - start.year) * 12 + (end.month - start.month)
	if end.day >= start.day:
		months += 1
	return max(0, months)


def execute(employees, contract_type, start_date=None, end_date=None, is_open_ended=0,
            probation_months=None, template=None, use_joining_date=0, generate_documents=0):
	"""Create the contracts the preview said it would create.

	Re-runs the preview rather than trusting anything the browser sends back: the world
	may have changed between the two calls, and a stale preview must not be able to
	create a duplicate contract.
	"""
	perms.require(perms.CONTRACT_WRITE)
	plan = preview(employees, contract_type, start_date=start_date, end_date=end_date,
	               is_open_ended=is_open_ended, probation_months=probation_months,
	               template=template, use_joining_date=use_joining_date)

	created, skipped, blocked, failed = [], [], [], []
	for row in plan["rows"]:
		if row["action"] == SKIP:
			skipped.append({"employee": row["employee"], "reason": row["reason"],
			                "existing": row.get("existing")})
			continue
		if row["action"] == BLOCK:
			blocked.append({"employee": row["employee"], "reason": row["reason"]})
			continue

		# One savepoint per employee. A failure rolls back only that employee's work,
		# leaving every contract already created intact and committed at the end.
		savepoint = "bulk_{0}".format(abs(hash(row["employee"])) % 10 ** 8)
		frappe.db.savepoint(savepoint)
		try:
			doc = frappe.get_doc({
				"doctype": "Isoft Employment Contract",
				"employee": row["employee"],
				"company": row["company"],
				"contract_type": contract_type,
				"start_date": row["start_date"],
				"end_date": row.get("end_date"),
				"is_open_ended": cint(is_open_ended),
				"probation_start": row.get("probation_start"),
				"probation_end": row.get("probation_end"),
				"department": row.get("department"),
				"designation": row.get("designation"),
			})
			doc.insert()
			entry = {"employee": row["employee"], "contract": doc.name}
			if cint(generate_documents):
				try:
					result = cd.generate(doc.name, template=plan.get("template"))
					entry["document"] = result["name"]
				except Exception as exc:
					# A missing template must not lose the contract that was just created.
					entry["document_error"] = str(exc)
			created.append(entry)
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			failed.append({"employee": row["employee"],
			               "employee_name": row.get("employee_name"),
			               "error": str(exc)})

	return {
		"created": created, "skipped": skipped, "blocked": blocked, "failed": failed,
		"summary": {"created": len(created), "skipped": len(skipped),
		            "blocked": len(blocked), "failed": len(failed)},
	}


# --------------------------------------------------------------------------- #
# Offboarding (§41)
# --------------------------------------------------------------------------- #
def exit_checklist(employee):
	"""What must still happen before this person's file can be closed.

	Derived, not stored. A stored checklist goes stale the moment somebody settles an
	advance outside it; this reads the same records payroll and finance read, so it
	cannot disagree with them.
	"""
	perms.require(perms.EMPLOYEE_READ)
	emp = frappe.db.get_value(
		"Employee", employee,
		["name", "employee_name", "company", "status", "relieving_date", "date_of_joining",
		 "user_id"], as_dict=True)
	if not emp:
		frappe.throw(_("Employee {0} not found.").format(employee))
	perms.require_company(emp.company)

	from isoft_angola_hr.isoft_angola_hr.services import advances as advance_service

	items = []

	def add(key, label, ok, detail="", blocking=True, link=None):
		items.append({"key": key, "label": label, "status": "Done" if ok else (
			"Blocking" if blocking else "Pending"), "detail": detail, "link": link})

	add("relieving_date", _("Last working date recorded"), bool(emp.relieving_date),
	    str(emp.relieving_date or ""))

	contract = frappe.db.get_value(
		"Isoft Employment Contract",
		{"employee": employee, "status": ("in", ("Active", "Expiring"))},
		["name", "status", "end_date"], as_dict=True)
	add("contract", _("Employment contract closed"), not contract,
	    _("{0} is still {1} — terminate or let it expire.").format(
		    contract.name, contract.status) if contract else _("No open contract."),
	    link=contract.name if contract else None)

	outstanding = advance_service.outstanding_for(employee)
	add("advance", _("Salary advance recovered"), not outstanding,
	    _("{0} still outstanding.").format(outstanding) if outstanding else _("Nothing outstanding."))

	# ERPNext Loan Management, if the site uses it. Read-only — this app does not manage
	# loans and must not pretend to close one.
	loan = 0
	if frappe.db.table_exists("Loan"):
		loan = frappe.db.sql(
			"""select count(*) from `tabLoan`
			where applicant_type = 'Employee' and applicant = %s and docstatus = 1
			  and status not in ('Closed', 'Loan Closure Requested')""", employee)[0][0]
	add("loan", _("ERPNext loans closed"), not loan,
	    _("{0} open loan(s) in Loan Management.").format(loan) if loan else _("None."),
	    blocking=False)

	pending_leave = frappe.db.count("Leave Application",
	                                {"employee": employee, "docstatus": 0, "status": "Open"})
	add("leave", _("Leave requests decided"), not pending_leave,
	    _("{0} request(s) still open.").format(pending_leave) if pending_leave else _("None."),
	    blocking=False)

	draft_slips = frappe.db.count("Isoft Salary Slip", {"employee": employee, "docstatus": 0})
	add("payroll", _("No unprocessed payroll"), not draft_slips,
	    _("{0} draft salary slip(s).").format(draft_slips) if draft_slips else _("None."))

	settlement = frappe.db.get_value(
		"Isoft Final Settlement", {"employee": employee, "docstatus": 1}, "name") \
		if frappe.db.table_exists("Isoft Final Settlement") else None
	add("settlement", _("Final settlement processed"), bool(settlement),
	    settlement or _("Not yet produced."), link=settlement)

	expiring_docs = frappe.db.count("Isoft Employee Document",
	                                {"employee": employee, "status": ("in", ("Valid", "Expiring"))})
	add("documents", _("HR documents returned or archived"), True,
	    _("{0} document(s) on file.").format(expiring_docs), blocking=False)

	separation = frappe.db.get_value(
		"Employee Separation", {"employee": employee, "docstatus": 1}, "name") \
		if frappe.db.table_exists("Employee Separation") else None
	add("separation", _("Employee Separation completed"), bool(separation),
	    separation or _("ERPNext Employee Separation not created."), blocking=False)

	# Deliberately not automated: this app does not manage assets or accounts, and a
	# checklist item that silently disables somebody's login would be worse than a
	# reminder to do it deliberately.
	add("equipment", _("Equipment and access cards returned"), False,
	    _("Manual — record in ERPNext Asset / Employee Separation."), blocking=False)
	add("accounts", _("System access disabled"),
	    not (emp.user_id and frappe.db.get_value("User", emp.user_id, "enabled")),
	    _("User {0} is still enabled.").format(emp.user_id) if emp.user_id
	    else _("No linked user."), blocking=False)
	add("interview", _("Exit interview held"), False, _("Manual."), blocking=False)

	blocking = [i for i in items if i["status"] == "Blocking"]
	return {
		"employee": emp.name, "employee_name": emp.employee_name, "status": emp.status,
		"relieving_date": str(emp.relieving_date or ""), "items": items,
		"blocking": len(blocking), "complete": len([i for i in items if i["status"] == "Done"]),
		"total": len(items),
		"can_close": not blocking,
		# §42: the sequence matters. Marking somebody Left before their settlement is
		# produced strands the payroll that still has to be run for them.
		"guidance": _("Produce the final settlement and close the contract BEFORE setting the "
		              "employee to Left — payroll cannot process an inactive employee."),
	}


def readiness_for_work_and_payroll(employee):
	"""Two different questions, answered separately (§40).

	Somebody can legitimately start work before their IBAN is on file; they cannot be
	paid. Collapsing the two into one "onboarding complete" flag is what makes HR chase
	the wrong things on a new hire's first day.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle

	checklist = lifecycle.onboarding_checklist(employee)
	items = {i["key"]: i for i in checklist.get("items", [])}

	#: What must be true before somebody can start work, and separately before they can
	#: be PAID. An employee with no IBAN can legitimately start on Monday; they simply
	#: cannot be included in the bank file at the end of the month.
	WORK_KEYS = ("contract", "department", "designation", "manager", "holiday_list")
	PAYROLL_KEYS = ("salary_profile", "iban", "nif", "inss")

	def check(keys):
		missing = [items[k] for k in keys if k in items and not items[k].get("ok")]
		return (not missing), [m["label"] for m in missing]

	work_ok, work_missing = check(WORK_KEYS)
	pay_ok, pay_missing = check(PAYROLL_KEYS)

	return {
		"employee": employee,
		"employee_name": checklist.get("employee_name"),
		"ready_for_work": work_ok,
		"ready_for_payroll": pay_ok,
		"work_missing": work_missing,
		"payroll_missing": pay_missing,
		"status": "Ready for Work" if work_ok else "Incomplete",
		"payroll_status": "Ready for Payroll" if pay_ok else "Payment Blocked",
		"checklist": checklist,
	}
