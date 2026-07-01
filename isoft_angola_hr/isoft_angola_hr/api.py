# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt
"""Server API backing the Angola HR Dashboard single-page console.

All management happens inside the dashboard, so these methods return JSON data
for in-page rendering and perform the create/submit/save actions, instead of the
user navigating to the underlying doctype list views.
"""

import json

import frappe
from frappe import _
from frappe.utils import (
	add_months, cint, date_diff, flt, formatdate, get_first_day, get_last_day, getdate, nowdate,
)

HR_ROLES = {"HR Manager"}


def _guard():
	if not (HR_ROLES & set(frappe.get_roles())):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _companies():
	return [c.name for c in frappe.get_all("Company", fields=["name"], order_by="name")]


def _default_company(company=None):
	return company or frappe.db.get_single_value("Isoft HR Settings", "default_company") or (
		_companies()[0] if _companies() else None
	)


def _slip_status(docstatus, journal_entry=None, payment_entry=None):
	"""Smart lifecycle status of a salary slip:
	Draft -> Submitted -> Accrued (accrual JE created) -> Paid (payment JE created); Cancelled."""
	if cint(docstatus) == 2:
		return "Cancelled"
	if cint(docstatus) == 0:
		return "Draft"
	if payment_entry:
		return "Paid"
	if journal_entry:
		return "Accrued"
	return "Submitted"


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_overview(company=None):
	_guard()
	company = _default_company(company)
	start, end = get_first_day(nowdate()), get_last_day(nowdate())

	emp_filters = {"status": "Active"}
	if company:
		emp_filters["company"] = company

	net = frappe.db.sql(
		"""select coalesce(sum(net_pay),0) from `tabIsoft Salary Slip`
		where docstatus=1 and start_date>=%s and end_date<=%s {c}""".format(
			c="and company=%s" if company else ""
		),
		([start, end, company] if company else [start, end]),
	)[0][0]

	cflt = "and company=%s" if company else ""
	cargs = [company] if company else []

	# --- Net pay trend (last 6 months) ---
	base = getdate(nowdate())
	net_pay_trend = []
	for i in range(5, -1, -1):
		m_start = get_first_day(add_months(base, -i))
		m_end = get_last_day(m_start)
		total = frappe.db.sql(
			"""select coalesce(sum(net_pay),0) from `tabIsoft Salary Slip`
			where docstatus=1 and start_date>=%s and end_date<=%s {c}""".format(c=cflt),
			[m_start, m_end] + cargs,
		)[0][0]
		net_pay_trend.append({"label": formatdate(m_start, "MMM yy"), "total": flt(total)})

	# --- Salary slips by lifecycle status ---
	counts = {"Draft": 0, "Submitted": 0, "Accrued": 0, "Paid": 0, "Cancelled": 0}
	for ds, je, pe in frappe.db.sql(
		"""select docstatus, journal_entry, payment_entry from `tabIsoft Salary Slip`
		where 1=1 {c}""".format(c=cflt), cargs):
		counts[_slip_status(ds, je, pe)] += 1
	slip_status = [{"status": k, "count": v} for k, v in counts.items() if v]

	# --- Headcount by department (active) ---
	dept_rows = frappe.db.sql(
		"""select coalesce(nullif(department,''), 'No Department') dept, count(*) c
		from `tabEmployee` where status='Active' {c}
		group by department order by c desc limit 10""".format(c=cflt),
		cargs, as_dict=True,
	)
	headcount_by_dept = [{"department": (r.dept or "").split(" - ")[0] or r.dept, "count": r.c}
	                     for r in dept_rows]

	# --- Upcoming holidays from the company's default holiday list ---
	default_holiday_list = frappe.db.get_value("Company", company, "default_holiday_list") if company else None
	upcoming_holidays = []
	if default_holiday_list:
		today = getdate(nowdate())
		for h in frappe.db.sql(
			"""select holiday_date, description from `tabHoliday`
			where parent=%s and parenttype='Holiday List' and holiday_date>=%s
			order by holiday_date asc limit 6""",
			(default_holiday_list, today), as_dict=True):
			upcoming_holidays.append({
				"holiday_date": str(h.holiday_date), "description": h.description,
				"days_until": date_diff(h.holiday_date, today),
			})

	recent_entries = frappe.get_all(
		"Isoft Payroll Entry",
		filters={"company": company} if company else None,
		fields=["name", "start_date", "end_date", "number_of_employees", "total_net_pay",
		        "salary_slips_submitted"],
		order_by="creation desc",
		limit=8,
	)

	return {
		"companies": _companies(),
		"company": company,
		"period": {"start": str(start), "end": str(end)},
		"cards": {
			"active_employees": frappe.db.count("Employee", emp_filters),
			"salary_profiles": frappe.db.count("Isoft Salary Profile",
			                                   {"company": company} if company else None),
			"submitted_slips": frappe.db.count("Isoft Salary Slip", {
				"docstatus": 1, **({"company": company} if company else {})}),
			"net_paid_month": flt(net),
		},
		"net_pay_trend": net_pay_trend,
		"slip_status": slip_status,
		"headcount_by_dept": headcount_by_dept,
		"upcoming_holidays": upcoming_holidays,
		"default_holiday_list": default_holiday_list,
		"recent_entries": recent_entries,
		"currency": frappe.db.get_single_value("Isoft HR Settings", "currency") or "AOA",
	}


# --------------------------------------------------------------------------- #
# Employees / Attendance / Timesheets (reused ERPNext core, shown in-dashboard)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_employees(company=None, search=None):
	_guard()
	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	or_filters = None
	if search:
		or_filters = {"employee_name": ("like", f"%{search}%"), "name": ("like", f"%{search}%")}
	return frappe.get_all(
		"Employee", filters=filters, or_filters=or_filters,
		fields=["name", "employee_name", "designation", "department", "date_of_joining",
		        "custom_nif", "custom_inss_number", "custom_dependents"],
		order_by="employee_name", limit_page_length=500,
	)


@frappe.whitelist()
def get_filter_options(company=None):
	"""Distinct departments / branches / designations among active employees (for dropdowns)."""
	_guard()
	filters = {"status": "Active"}
	if company:
		filters["company"] = company

	def distinct(field):
		rows = frappe.get_all("Employee", filters=filters, fields=[field], pluck=field,
		                      distinct=True, limit_page_length=0)
		return sorted({v for v in rows if v})

	return {
		"departments": distinct("department"),
		"branches": distinct("branch"),
		"designations": distinct("designation"),
	}


@frappe.whitelist()
def get_employee(name):
	_guard()
	emp = frappe.db.get_value(
		"Employee", name,
		["name", "employee_name", "designation", "department", "company", "date_of_joining",
		 "custom_nif", "custom_inss_number", "custom_dependents", "custom_payroll_payable_account",
		 "cell_number", "personal_email"],
		as_dict=True,
	)
	profile = frappe.get_all(
		"Isoft Salary Profile", filters={"employee": name},
		fields=["name", "from_date", "base", "food_allowance", "transport_allowance", "family_allowance"],
		order_by="from_date desc", limit=1,
	)
	slips = frappe.get_all(
		"Isoft Salary Slip", filters={"employee": name},
		fields=["name", "start_date", "end_date", "gross_pay", "net_pay", "docstatus",
		        "journal_entry", "payment_entry"],
		order_by="start_date desc", limit=6,
	)
	for s in slips:
		s["status"] = _slip_status(s.get("docstatus"), s.get("journal_entry"), s.get("payment_entry"))
	return {"employee": emp, "profile": profile[0] if profile else None, "slips": slips}


@frappe.whitelist()
def list_attendance(company=None, employee=None, from_date=None, to_date=None):
	_guard()
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if employee:
		conds.append("employee=%s"); vals.append(employee)
	if from_date:
		conds.append("attendance_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("attendance_date<=%s"); vals.append(getdate(to_date))
	return frappe.db.sql(
		"""select name, employee, employee_name, attendance_date, status, working_hours,
		coalesce(custom_overtime_hours,0) as overtime_hours, shift
		from `tabAttendance` where {} and docstatus<2
		order by attendance_date desc limit 300""".format(" and ".join(conds)),
		vals, as_dict=True,
	)


@frappe.whitelist()
def mark_attendance(employee, attendance_date, status, working_hours=0, overtime_hours=0,
                    company=None, shift=None):
	"""Create (or update a draft) Attendance record from the dashboard, then submit it."""
	_guard()
	date = getdate(attendance_date)
	existing = frappe.db.exists(
		"Attendance", {"employee": employee, "attendance_date": date, "docstatus": ("<", 2)}
	)
	if existing:
		doc = frappe.get_doc("Attendance", existing)
		if doc.docstatus == 1:
			frappe.throw(_("Attendance for {0} on {1} is already submitted.").format(employee, date))
	else:
		doc = frappe.new_doc("Attendance")
		doc.employee = employee
		doc.attendance_date = date

	doc.status = status
	doc.working_hours = flt(working_hours)
	doc.custom_overtime_hours = flt(overtime_hours)
	if shift:
		doc.shift = shift
	doc.company = company or frappe.db.get_value("Employee", employee, "company")
	doc.save()
	doc.submit()
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def list_timesheets(company=None, employee=None):
	_guard()
	filters = {}
	if company:
		filters["company"] = company
	if employee:
		filters["employee"] = employee
	return frappe.get_all(
		"Timesheet", filters=filters,
		fields=["name", "employee_name", "start_date", "end_date", "total_hours", "status"],
		order_by="start_date desc", limit_page_length=200,
	)


# --------------------------------------------------------------------------- #
# Salary Profiles
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_salary_profiles(company=None):
	_guard()
	filters = {}
	if company:
		filters["company"] = company
	return frappe.get_all(
		"Isoft Salary Profile", filters=filters,
		fields=["name", "employee", "employee_name", "from_date", "base", "food_allowance",
		        "transport_allowance", "family_allowance"],
		order_by="employee_name", limit_page_length=500,
	)


@frappe.whitelist()
def save_salary_profile(data):
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	if d.get("name") and frappe.db.exists("Isoft Salary Profile", d["name"]):
		doc = frappe.get_doc("Isoft Salary Profile", d["name"])
	else:
		doc = frappe.new_doc("Isoft Salary Profile")
	doc.update({
		"employee": d.get("employee"),
		"from_date": d.get("from_date"),
		"base": flt(d.get("base")),
		"food_allowance": flt(d.get("food_allowance")),
		"transport_allowance": flt(d.get("transport_allowance")),
		"family_allowance": flt(d.get("family_allowance")),
	})
	doc.save()
	frappe.db.commit()
	return doc.name


# --------------------------------------------------------------------------- #
# Payroll Entries (create + history + filters)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_payroll_entries(company=None, from_date=None, to_date=None):
	_guard()
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if from_date:
		conds.append("start_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("end_date<=%s"); vals.append(getdate(to_date))
	return frappe.db.sql(
		"""select name, company, start_date, end_date, number_of_employees, total_net_pay,
		salary_slips_created, salary_slips_submitted
		from `tabIsoft Payroll Entry` where {} order by start_date desc limit 100""".format(
			" and ".join(conds)
		),
		vals, as_dict=True,
	)


@frappe.whitelist()
def create_payroll_entry(company, start_date, end_date, posting_date=None, department=None):
	_guard()
	entry = frappe.new_doc("Isoft Payroll Entry")
	entry.company = company
	entry.start_date = getdate(start_date)
	entry.end_date = getdate(end_date)
	entry.posting_date = getdate(posting_date) if posting_date else getdate(end_date)
	if department:
		entry.department = department
	entry.insert()
	count = entry.fill_employees()
	if not count:
		frappe.throw(_("No employees with a Salary Profile found for the selected filters."))
	entry.create_salary_slips()
	return {"name": entry.name, "employees": count, "total_net_pay": flt(entry.total_net_pay)}


def _working_days(employee, start, end):
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import get_holiday_count

	total = (getdate(end) - getdate(start)).days + 1
	twd = max(0, total - get_holiday_count(employee, start, end))
	absent = frappe.db.sql(
		"""select count(*) from `tabAttendance` where employee=%s and docstatus=1
		and status='Absent' and attendance_date between %s and %s""",
		(employee, getdate(start), getdate(end)),
	)[0][0]
	return twd, max(0, twd - absent)


@frappe.whitelist()
def payroll_preview(company, start_date, end_date, department=None, branch=None,
                    designation=None, inputs=None, validate_attendance=0, based_on_timesheet=0):
	"""Dry-run a payroll batch: returns one computed row per eligible employee, honouring
	any per-employee variable inputs (overtime / bonus / advance) passed back from the UI."""
	_guard()
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import get_active_profile
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import compute_working_days
	from isoft_angola_hr.isoft_angola_hr.payroll import engine

	inputs = (json.loads(inputs) if isinstance(inputs, str) else inputs) or {}
	start, end = getdate(start_date), getdate(end_date)
	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	for f, v in (("department", department), ("branch", branch), ("designation", designation)):
		if v:
			filters[f] = v
	emps = frappe.get_all("Employee", filters=filters,
	                      fields=["name", "employee_name", "department", "designation"],
	                      order_by="employee_name", limit_page_length=2000)
	settings = engine.get_settings()
	out = []
	for e in emps:
		prof = get_active_profile(e.name, end)
		if not prof:
			continue
		if not prof.irt_table:
			prof.irt_table = settings.default_irt_table
		twd, pay_days = compute_working_days(e.name, start, end,
		                                     validate_attendance=validate_attendance,
		                                     based_on_timesheet=based_on_timesheet)
		inp = inputs.get(e.name, {})
		res = engine.compute_slip(prof, {
			"productivity_bonus": flt(inp.get("productivity_bonus")),
			"overtime_amount": flt(inp.get("overtime_amount")),
			"adiantamento": flt(inp.get("adiantamento")),
			"payment_days": pay_days, "total_working_days": twd,
		}, settings=settings, on_date=end)
		ded = {d["abbr"]: d["amount"] for d in res["deductions"]}
		out.append({
			"employee": e.name, "employee_name": e.employee_name,
			"department": e.department, "designation": e.designation,
			"base": flt(prof.base), "total_working_days": twd, "payment_days": pay_days,
			"productivity_bonus": flt(inp.get("productivity_bonus")),
			"overtime_amount": flt(inp.get("overtime_amount")),
			"adiantamento": flt(inp.get("adiantamento")),
			"taxable_income": res["taxable_income"], "ss": flt(ded.get("CTSS3")),
			"irt": flt(ded.get("IRT")), "gross_pay": res["gross_pay"],
			"total_deduction": res["total_deduction"], "net_pay": res["net_pay"],
		})
	return out


@frappe.whitelist()
def create_payroll_from_preview(company, start_date, end_date, rows, posting_date=None,
                                validate_attendance=0, based_on_timesheet=0):
	"""Create the Isoft Payroll Entry + Salary Slips from the previewed/edited rows."""
	_guard()
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import get_active_profile

	rows = json.loads(rows) if isinstance(rows, str) else rows
	if not rows:
		frappe.throw(_("No employees to process."))
	entry = frappe.new_doc("Isoft Payroll Entry")
	entry.company = company
	entry.start_date, entry.end_date = getdate(start_date), getdate(end_date)
	entry.posting_date = getdate(posting_date) if posting_date else getdate(end_date)
	entry.validate_attendance = cint(validate_attendance)
	entry.based_on_timesheet = cint(based_on_timesheet)
	for r in rows:
		prof = get_active_profile(r["employee"], entry.end_date)
		entry.append("employees", {
			"employee": r["employee"], "employee_name": r.get("employee_name"),
			"salary_profile": prof.name if prof else None,
			"productivity_bonus": flt(r.get("productivity_bonus")),
			"overtime_amount": flt(r.get("overtime_amount")),
			"adiantamento": flt(r.get("adiantamento")),
		})
	entry.number_of_employees = len(entry.employees)
	entry.insert()
	entry.create_salary_slips()
	return {"name": entry.name, "employees": entry.number_of_employees,
	        "total_net_pay": flt(entry.total_net_pay)}


@frappe.whitelist()
def make_journal_entry(salary_slip):
	"""Create an accounting Journal Entry for a submitted salary slip.

	Each earning is debited and each deduction credited to its own per-component
	account (Settings -> Account per Component); the net pay is credited to the
	Payroll Payable account. Requires those accounts to be configured.
	"""
	_guard()
	slip = frappe.get_doc("Isoft Salary Slip", salary_slip)
	if slip.docstatus != 1:
		frappe.throw(_("Submit the salary slip before posting a Journal Entry."))
	if slip.get("journal_entry") and frappe.db.exists("Journal Entry", slip.journal_entry):
		return slip.journal_entry

	s = frappe.get_single("Isoft HR Settings")
	# Per-employee Payroll Payable account overrides the Settings default.
	emp_payable = frappe.db.get_value("Employee", slip.employee, "custom_payroll_payable_account")
	payable_account = emp_payable or s.get("payroll_payable_account")
	if not payable_account:
		frappe.throw(_("Configure the Payroll Payable account (Settings or Employee) first."))

	# Each component posts to its own account (Settings -> Account per Component).
	comp_acc = {r.abbr: r.account for r in s.component_accounts if r.account}

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = slip.company
	je.posting_date = slip.posting_date or slip.end_date
	je.user_remark = _("Payroll: {0}").format(slip.name)

	missing = []
	# Earnings (cash) -> debit each component's account.
	for e in slip.earnings:
		if e.do_not_include_in_total or not flt(e.amount):
			continue
		account = comp_acc.get(e.abbr)
		if not account:
			missing.append(e.salary_component)
			continue
		je.append("accounts", {"account": account, "debit_in_account_currency": flt(e.amount)})
	# Deductions -> credit each component's account.
	for d in slip.deductions:
		if not flt(d.amount):
			continue
		account = comp_acc.get(d.abbr)
		if not account:
			missing.append(d.salary_component)
			continue
		je.append("accounts", {"account": account, "credit_in_account_currency": flt(d.amount)})
	# Net pay -> credit Payroll Payable.
	je.append("accounts", {"account": payable_account, "credit_in_account_currency": flt(slip.net_pay)})

	if missing:
		frappe.throw(_("Set an account for these components in Settings: {0}").format(", ".join(set(missing))))

	je.insert()
	slip.db_set("journal_entry", je.name)
	frappe.db.commit()
	return je.name


@frappe.whitelist()
def make_payment_entry(salary_slip, payment_account=None, posting_date=None):
	"""Create the salary Payment Entry (a Bank Entry Journal):
	Dr Payroll Payable (net) ; Cr Bank/Cash. Clears the payable booked by the accrual.
	"""
	_guard()
	slip = frappe.get_doc("Isoft Salary Slip", salary_slip)
	if slip.docstatus != 1:
		frappe.throw(_("Submit the salary slip before posting a Payment Entry."))
	if slip.get("payment_entry") and frappe.db.exists("Journal Entry", slip.payment_entry):
		return slip.payment_entry
	if flt(slip.net_pay) <= 0:
		frappe.throw(_("Net pay is zero — nothing to pay for {0}.").format(slip.employee_name))

	s = frappe.get_single("Isoft HR Settings")
	pay_acc = payment_account or s.get("salary_payment_account")
	if not pay_acc:
		frappe.throw(_("Configure the Salary Payment (Bank/Cash) account in Settings first."))
	emp_payable = frappe.db.get_value("Employee", slip.employee, "custom_payroll_payable_account")
	payable_account = emp_payable or s.get("payroll_payable_account")
	if not payable_account:
		frappe.throw(_("Configure the Payroll Payable account (Settings or Employee) first."))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Bank Entry"
	je.company = slip.company
	je.posting_date = getdate(posting_date) if posting_date else (slip.posting_date or slip.end_date)
	je.user_remark = _("Salary payment: {0}").format(slip.name)
	je.append("accounts", {"account": payable_account, "debit_in_account_currency": flt(slip.net_pay)})
	je.append("accounts", {"account": pay_acc, "credit_in_account_currency": flt(slip.net_pay)})
	je.insert()
	slip.db_set("payment_entry", je.name)
	frappe.db.commit()
	return je.name


def _entry_slip_names(name, employees=None):
	"""Salary slip names for a payroll entry, optionally limited to selected employees."""
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	sel = set(json.loads(employees)) if isinstance(employees, str) and employees else (set(employees) if employees else None)
	out = []
	for r in entry.employees:
		if not r.salary_slip:
			continue
		if sel is not None and r.employee not in sel:
			continue
		out.append(r.salary_slip)
	return out


@frappe.whitelist()
def make_bulk_journal_entry(name, employees=None):
	"""Create the accrual Journal Entry for each submitted slip of the entry (or the
	selected employees) that does not already have one."""
	_guard()
	created, skipped, errors = 0, 0, []
	for sname in _entry_slip_names(name, employees):
		slip = frappe.get_doc("Isoft Salary Slip", sname)
		if slip.docstatus != 1 or (slip.journal_entry and frappe.db.exists("Journal Entry", slip.journal_entry)):
			skipped += 1
			continue
		try:
			make_journal_entry(sname)
			created += 1
		except Exception as e:
			errors.append(f"{slip.employee_name}: {str(e)}")
	return {"created": created, "skipped": skipped, "errors": errors}


@frappe.whitelist()
def make_bulk_payment_entry(name, payment_account=None, posting_date=None, employees=None):
	"""Create the Payment Entry for each submitted slip of the entry (or the selected
	employees) that does not already have one."""
	_guard()
	created, skipped, total, errors = 0, 0, 0.0, []
	for sname in _entry_slip_names(name, employees):
		slip = frappe.get_doc("Isoft Salary Slip", sname)
		if slip.docstatus != 1 or (slip.payment_entry and frappe.db.exists("Journal Entry", slip.payment_entry)):
			skipped += 1
			continue
		try:
			make_payment_entry(sname, payment_account=payment_account, posting_date=posting_date)
			created += 1
			total += flt(slip.net_pay)
		except Exception as e:
			errors.append(f"{slip.employee_name}: {str(e)}")
	return {"created": created, "skipped": skipped, "total": total, "errors": errors}


@frappe.whitelist()
def get_payroll_entry(name):
	_guard()
	doc = frappe.get_doc("Isoft Payroll Entry", name)
	# Per-slip docstatus + accrual/payment status for the detail grid.
	slip_names = [e.salary_slip for e in doc.employees if e.salary_slip]
	status = {}
	if slip_names:
		for s in frappe.get_all("Isoft Salary Slip", filters={"name": ["in", slip_names]},
		                        fields=["name", "docstatus", "journal_entry", "payment_entry"]):
			status[s.name] = s
	return {
		"doc": {f: doc.get(f) for f in ["name", "company", "start_date", "end_date", "posting_date",
		                                "number_of_employees", "total_net_pay", "salary_slips_created",
		                                "salary_slips_submitted"]},
		"employees": [{
			"employee": e.employee, "employee_name": e.employee_name,
			"salary_slip": e.salary_slip, "net_pay": flt(e.net_pay),
			"docstatus": (status.get(e.salary_slip) or {}).get("docstatus"),
			"journal_entry": (status.get(e.salary_slip) or {}).get("journal_entry"),
			"payment_entry": (status.get(e.salary_slip) or {}).get("payment_entry"),
			"status": _slip_status((status.get(e.salary_slip) or {}).get("docstatus"),
			                       (status.get(e.salary_slip) or {}).get("journal_entry"),
			                       (status.get(e.salary_slip) or {}).get("payment_entry")),
		} for e in doc.employees],
	}


@frappe.whitelist()
def submit_payroll_entry(name):
	_guard()
	doc = frappe.get_doc("Isoft Payroll Entry", name)
	n = doc.submit_salary_slips()
	return {"submitted": n, "total_net_pay": flt(doc.total_net_pay)}


# --------------------------------------------------------------------------- #
# Salary Slips
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_salary_slips(company=None, payroll_entry=None, employee=None, from_date=None, to_date=None,
                      status=None):
	_guard()
	conds = ["1=1"]
	vals = []
	for field, val in (("company", company), ("payroll_entry", payroll_entry), ("employee", employee)):
		if val:
			conds.append(f"{field}=%s"); vals.append(val)
	if from_date:
		conds.append("start_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("end_date<=%s"); vals.append(getdate(to_date))
	# Lifecycle status maps to docstatus + accrual/payment presence (computed, not a column).
	status_sql = {
		"Draft": "docstatus=0",
		"Submitted": "docstatus=1 and (journal_entry is null or journal_entry='') and (payment_entry is null or payment_entry='')",
		"Accrued": "docstatus=1 and journal_entry is not null and journal_entry!='' and (payment_entry is null or payment_entry='')",
		"Paid": "docstatus=1 and payment_entry is not null and payment_entry!=''",
		"Cancelled": "docstatus=2",
	}
	if status and status in status_sql:
		conds.append(status_sql[status])
	rows = frappe.db.sql(
		"""select name, employee_name, start_date, end_date, gross_pay, total_deduction,
		net_pay, docstatus, journal_entry, payment_entry from `tabIsoft Salary Slip`
		where {} order by start_date desc limit 300""".format(
			" and ".join(conds)
		),
		vals, as_dict=True,
	)
	for r in rows:
		r["status"] = _slip_status(r.get("docstatus"), r.get("journal_entry"), r.get("payment_entry"))
	return rows


def _linked_entries(slip_doc):
	"""Existing accrual / payment Journal Entries linked to a slip (skips dangling links)."""
	out = []
	for field, label in (("journal_entry", _("accrual Journal Entry")), ("payment_entry", _("Payment Entry"))):
		v = slip_doc.get(field)
		if v and frappe.db.exists("Journal Entry", v):
			out.append((label, v))
	return out


def _assert_no_entries(slip_doc):
	"""Block destructive ops while the slip is accounted for — accrual/payment must be
	removed from the ledger first, so the books and the slip never silently diverge."""
	linked = _linked_entries(slip_doc)
	if linked:
		parts = ", ".join(f"{label} {frappe.bold(v)}" for label, v in linked)
		frappe.throw(_("Cannot delete {0}: delete its {1} first.").format(slip_doc.name, parts))


@frappe.whitelist()
def cancel_salary_slip(name):
	_guard()
	doc = frappe.get_doc("Isoft Salary Slip", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.db.commit()
	return True


@frappe.whitelist()
def delete_salary_slip(name):
	_guard()
	doc = frappe.get_doc("Isoft Salary Slip", name)
	_assert_no_entries(doc)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Isoft Salary Slip", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def cancel_payroll_entry(name):
	"""Cancel all submitted salary slips of the entry (keeps the entry)."""
	_guard()
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	slips = [frappe.get_doc("Isoft Salary Slip", r.salary_slip) for r in entry.employees
	         if r.salary_slip and frappe.db.exists("Isoft Salary Slip", r.salary_slip)]
	# Block if any slip has a posted accrual/payment — remove those first.
	locked = [s.employee_name for s in slips if s.docstatus == 1 and (s.get("journal_entry") or s.get("payment_entry"))]
	if locked:
		frappe.throw(_("Remove the Journal Entry / Payment of these slips before cancelling: {0}").format(
			", ".join(locked)))
	n = 0
	for s in slips:
		if s.docstatus == 1:
			s.cancel()
			n += 1
	entry.db_set("salary_slips_submitted", 0)
	frappe.db.commit()
	return n


@frappe.whitelist()
def delete_payroll_entry(name):
	"""Cancel + delete the entry's salary slips, then delete the entry."""
	_guard()
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	slips = [frappe.get_doc("Isoft Salary Slip", r.salary_slip) for r in entry.employees
	         if r.salary_slip and frappe.db.exists("Isoft Salary Slip", r.salary_slip)]
	# Block if any slip is accounted for — its JE / Payment must be deleted first.
	locked = [s.employee_name for s in slips if _linked_entries(s)]
	if locked:
		frappe.throw(_("Delete the Journal Entry / Payment of these slips first: {0}").format(", ".join(locked)))
	for s in slips:
		if s.docstatus == 1:
			s.cancel()
		frappe.delete_doc("Isoft Salary Slip", s.name, force=1)
	frappe.delete_doc("Isoft Payroll Entry", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def get_salary_slip(name):
	_guard()
	doc = frappe.get_doc("Isoft Salary Slip", name)
	return {
		"name": doc.name, "employee_name": doc.employee_name, "start_date": str(doc.start_date),
		"end_date": str(doc.end_date), "docstatus": doc.docstatus,
		"journal_entry": doc.get("journal_entry"), "payment_entry": doc.get("payment_entry"),
		"status": _slip_status(doc.docstatus, doc.get("journal_entry"), doc.get("payment_entry")),
		"taxable_income": flt(doc.taxable_income), "gross_pay": flt(doc.gross_pay),
		"total_deduction": flt(doc.total_deduction), "net_pay": flt(doc.net_pay),
		"earnings": [{"abbr": e.abbr, "salary_component": e.salary_component, "amount": flt(e.amount),
		              "stat": e.do_not_include_in_total} for e in doc.earnings],
		"deductions": [{"abbr": d.abbr, "salary_component": d.salary_component, "amount": flt(d.amount)}
		               for d in doc.deductions],
	}


# --------------------------------------------------------------------------- #
# IRT Table (managed as the single default table) + Settings
# --------------------------------------------------------------------------- #
def _default_irt_name():
	return frappe.db.get_single_value("Isoft HR Settings", "default_irt_table") or "Tabela IRT (Angola)"


@frappe.whitelist()
def get_irt_table():
	_guard()
	name = _default_irt_name()
	if not frappe.db.exists("IRT Table", name):
		return {"name": None, "brackets": []}
	doc = frappe.get_doc("IRT Table", name)
	return {
		"name": doc.name, "effective_from": str(doc.effective_from), "currency": doc.currency,
		"brackets": [{"from_amount": flt(b.from_amount), "to_amount": flt(b.to_amount),
		              "excess_over": flt(b.excess_over), "rate": flt(b.rate),
		              "parcela_fixa": flt(b.parcela_fixa)} for b in doc.brackets],
	}


@frappe.whitelist()
def save_irt_table(brackets):
	_guard()
	rows = json.loads(brackets) if isinstance(brackets, str) else brackets
	doc = frappe.get_doc("IRT Table", _default_irt_name())
	doc.set("brackets", [])
	for r in rows:
		doc.append("brackets", {
			"from_amount": flt(r.get("from_amount")), "to_amount": flt(r.get("to_amount")),
			"excess_over": flt(r.get("excess_over")), "rate": flt(r.get("rate")),
			"parcela_fixa": flt(r.get("parcela_fixa")),
		})
	doc.save()
	frappe.db.commit()
	return len(doc.brackets)


@frappe.whitelist()
def get_settings(company=None):
	_guard()
	from isoft_angola_hr.isoft_angola_hr.payroll import engine

	s = frappe.get_single("Isoft HR Settings")
	out = {f: s.get(f) for f in [
		"default_company", "default_irt_table", "currency", "ss_employee_rate", "ss_employer_rate",
		"food_allowance_exemption", "transport_allowance_exemption", "standard_daily_hours",
		"enable_productivity_bonus", "enable_overtime", "enable_adiantamento", "enable_family_allowance",
		"payroll_payable_account", "salary_payment_account"]}
	# Default Holiday List lives on the Company; expose it for the current company.
	comp = _default_company(company)
	out["_company"] = comp
	out["default_holiday_list"] = frappe.db.get_value("Company", comp, "default_holiday_list") if comp else None
	# Merge stored accounts with the full code-defined component list (so every component shows).
	stored = {r.abbr: r.account for r in s.component_accounts}
	out["component_accounts"] = [
		{"abbr": jc["abbr"], "component": jc["component"], "kind": jc["kind"],
		 "account": stored.get(jc["abbr"])}
		for jc in engine.journal_components()
	]
	return out


@frappe.whitelist()
def save_settings(data):
	_guard()
	from isoft_angola_hr.isoft_angola_hr.payroll import engine

	d = json.loads(data) if isinstance(data, str) else data
	comp_accts = d.pop("component_accounts", None)
	# Default Holiday List is stored on the Company, not the single settings doctype.
	holiday_list = d.pop("default_holiday_list", "__keep__")
	holiday_company = d.pop("_company", None)
	s = frappe.get_single("Isoft HR Settings")
	s.update(d)
	if comp_accts is not None:
		meta = {jc["abbr"]: jc for jc in engine.journal_components()}
		incoming = {r.get("abbr"): r.get("account") for r in comp_accts}
		s.set("component_accounts", [])
		for abbr, jc in meta.items():
			s.append("component_accounts", {
				"abbr": abbr, "component": jc["component"], "kind": jc["kind"],
				"account": incoming.get(abbr) or None,
			})
	s.save()
	if holiday_list != "__keep__":
		comp = holiday_company or s.default_company
		if comp:
			frappe.db.set_value("Company", comp, "default_holiday_list", holiday_list or None)
	frappe.db.commit()
	return True


# --------------------------------------------------------------------------- #
# Employee creation
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_employee(data):
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	emp = frappe.new_doc("Employee")
	emp.update({
		"first_name": d.get("first_name"),
		"last_name": d.get("last_name"),
		"company": d.get("company"),
		"gender": d.get("gender"),
		"date_of_birth": d.get("date_of_birth"),
		"date_of_joining": d.get("date_of_joining"),
		"designation": d.get("designation"),
		"department": d.get("department"),
		"branch": d.get("branch"),
		"custom_nif": d.get("custom_nif"),
		"custom_inss_number": d.get("custom_inss_number"),
		"custom_dependents": cint(d.get("custom_dependents")),
		"custom_payroll_payable_account": d.get("custom_payroll_payable_account") or None,
		"status": "Active",
	})
	emp.insert()
	frappe.db.commit()
	return {"name": emp.name, "employee_name": emp.employee_name}


@frappe.whitelist()
def update_employee(name, data):
	"""Update the Angola-HR editable fields of an existing Employee from the dashboard."""
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	emp = frappe.get_doc("Employee", name)
	for f in ("designation", "department", "custom_nif", "custom_inss_number"):
		if f in d:
			emp.set(f, d.get(f) or None)
	if "custom_dependents" in d:
		emp.custom_dependents = cint(d.get("custom_dependents"))
	if "custom_payroll_payable_account" in d:
		emp.custom_payroll_payable_account = d.get("custom_payroll_payable_account") or None
	emp.save()
	frappe.db.commit()
	return {"name": emp.name, "employee_name": emp.employee_name}


# --------------------------------------------------------------------------- #
# Holiday Lists
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_holiday_lists():
	_guard()
	return frappe.get_all("Holiday List",
	                      fields=["name", "holiday_list_name", "from_date", "to_date", "total_holidays"],
	                      order_by="from_date desc", limit_page_length=200)


@frappe.whitelist()
def get_holiday_list(name):
	_guard()
	doc = frappe.get_doc("Holiday List", name)
	return {
		"name": doc.name, "from_date": str(doc.from_date), "to_date": str(doc.to_date),
		"weekly_off": doc.weekly_off, "total_holidays": doc.total_holidays,
		"holidays": [{"holiday_date": str(h.holiday_date), "description": h.description}
		             for h in sorted(doc.holidays, key=lambda x: x.holiday_date)],
	}


@frappe.whitelist()
def create_holiday_list(holiday_list_name, from_date, to_date, weekly_off=None):
	_guard()
	doc = frappe.new_doc("Holiday List")
	doc.holiday_list_name = holiday_list_name
	doc.from_date = getdate(from_date)
	doc.to_date = getdate(to_date)
	if weekly_off:
		doc.weekly_off = weekly_off
		doc.get_weekly_off_dates()
	doc.insert()
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def add_holiday(holiday_list, holiday_date, description):
	_guard()
	doc = frappe.get_doc("Holiday List", holiday_list)
	doc.append("holidays", {"holiday_date": getdate(holiday_date), "description": description})
	doc.save()
	frappe.db.commit()
	return len(doc.holidays)


# --------------------------------------------------------------------------- #
# Shift Types
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_shift_types():
	_guard()
	return frappe.get_all("Shift Type",
	                      fields=["name", "start_time", "end_time", "enable_auto_attendance"],
	                      order_by="name", limit_page_length=200)


@frappe.whitelist()
def get_shift_type(name):
	_guard()
	doc = frappe.get_doc("Shift Type", name)
	return {
		"name": doc.name,
		"start_time": str(doc.start_time) if doc.start_time else None,
		"end_time": str(doc.end_time) if doc.end_time else None,
		"enable_auto_attendance": doc.enable_auto_attendance,
		"working_hours_threshold_for_half_day": doc.working_hours_threshold_for_half_day,
		"working_hours_threshold_for_absent": doc.working_hours_threshold_for_absent,
		"weekday_hours": [{
			"weekday": r.weekday, "is_working_day": r.is_working_day,
			"start_time": str(r.start_time) if (r.start_time and r.is_working_day) else None,
			"end_time": str(r.end_time) if (r.end_time and r.is_working_day) else None,
		} for r in doc.get("weekday_hours")],
	}


@frappe.whitelist()
def save_shift_type(data):
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	if d.get("name") and frappe.db.exists("Shift Type", d["name"]):
		doc = frappe.get_doc("Shift Type", d["name"])
	else:
		doc = frappe.new_doc("Shift Type")
		doc.__newname = d.get("shift_name") or d.get("name")
	doc.start_time = d.get("start_time")
	doc.end_time = d.get("end_time")
	doc.enable_auto_attendance = cint(d.get("enable_auto_attendance"))
	doc.working_hours_threshold_for_half_day = flt(d.get("working_hours_threshold_for_half_day"))
	doc.working_hours_threshold_for_absent = flt(d.get("working_hours_threshold_for_absent"))

	wh = d.get("weekday_hours")
	if wh is not None:
		doc.set("weekday_hours", [])
		for r in wh:
			doc.append("weekday_hours", {
				"weekday": r.get("weekday"),
				"is_working_day": cint(r.get("is_working_day")),
				"start_time": r.get("start_time") or None,
				"end_time": r.get("end_time") or None,
			})
	doc.save()
	frappe.db.commit()
	return doc.name
