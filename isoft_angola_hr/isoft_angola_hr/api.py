# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
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
	add_days, add_months, cint, date_diff, flt, formatdate, get_first_day, get_last_day, getdate,
	now, now_datetime, nowdate,
)

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.services import payroll_reconciliation as reconciliation
from isoft_angola_hr.isoft_angola_hr.services import production_readiness
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

HR_ROLES = {"HR Manager"}


def _guard(action=perms.HR_ACCESS, company=None):
	"""Authorise the caller for an ACTION rather than for a single blanket role.

	Every endpoint names the action it performs, so segregation of duties is decided by
	the one table in ``services/permissions.py`` instead of being re-implemented (and
	re-weakened) per endpoint. Company scope is checked here too whenever the endpoint
	knows which company it is acting on.
	"""
	perms.require(action)
	if company:
		perms.require_company(company)


def _companies():
	"""Companies the caller may act for. A user restricted by User Permission never sees
	another company's payroll in any list, count or export."""
	names = [c.name for c in frappe.get_all("Company", fields=["name"], order_by="name")]
	allowed = perms.allowed_companies()
	if allowed is None:
		return names
	return [n for n in names if n in allowed]


def _default_company(company=None):
	return company or frappe.db.get_single_value("Isoft HR Settings", "default_company") or (
		_companies()[0] if _companies() else None
	)


def _cycle_period(anchor=None, start_day=None):
	"""Payroll period for the given anchor date, honouring the configured cycle start day.
	start_day=1 -> calendar month; start_day=23 -> [23 prev month .. 22 current], etc.
	Returns the period that contains the anchor date."""
	anchor = getdate(anchor or nowdate())
	if start_day is None:
		start_day = cint(frappe.db.get_single_value("Isoft HR Settings", "payroll_cycle_start_day")) or 1
	start_day = max(1, min(28, cint(start_day)))
	if start_day <= 1:
		return get_first_day(anchor), get_last_day(anchor)
	start = anchor.replace(day=start_day) if anchor.day >= start_day \
		else add_months(anchor.replace(day=start_day), -1)
	end = add_days(add_months(getdate(start), 1), -1)
	return getdate(start), getdate(end)


def _default_cycle_period():
	"""The period to default the payroll form to — the most recently CLOSED period, since
	HR processes a period after it ends (day 22). Calendar months default to this month."""
	start_day = cint(frappe.db.get_single_value("Isoft HR Settings", "payroll_cycle_start_day")) or 1
	today = getdate(nowdate())
	if start_day <= 1:
		return get_first_day(today), get_last_day(today)
	open_start, _ = _cycle_period(today, start_day)
	return _cycle_period(add_days(open_start, -1), start_day)


def _period_for_month(month, start_day=None):
	"""Payroll period whose END falls in the given month ('YYYY-MM'). With the cycle start
	day = 1 that's the whole calendar month; with 23 it's [23 prev month .. 22 this month]."""
	parts = str(month).split("-")
	y, m = cint(parts[0]), cint(parts[1])
	if start_day is None:
		start_day = cint(frappe.db.get_single_value("Isoft HR Settings", "payroll_cycle_start_day")) or 1
	start_day = max(1, min(28, start_day))
	if start_day <= 1:
		start = getdate(f"{y}-{m:02d}-01")
		end = get_last_day(start)
	else:
		end = getdate(f"{y}-{m:02d}-{start_day - 1:02d}")
		start = add_months(getdate(f"{y}-{m:02d}-{start_day:02d}"), -1)
	return getdate(start), getdate(end)


@frappe.whitelist()
def payroll_period_for_month(month):
	"""Resolve a 'YYYY-MM' month to the payroll period start/end (honours the cycle setting)."""
	_guard(perms.PAYROLL_PREVIEW)
	start, end = _period_for_month(month)
	return {"start": str(start), "end": str(end)}


_MONTHS_PT = ["Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
              "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _salary_reference(period_end):
	"""Bank-transfer reference, e.g. 'Sal_Marco_2026' — from the payroll period's month/year."""
	d = getdate(period_end)
	return "Sal_{0}_{1}".format(_MONTHS_PT[d.month - 1], d.year)


def _je_is_posted(name, known_docstatus=None):
	"""True only when a linked Journal Entry actually exists in the ledger.

	A link alone proves nothing: entries used to be created as drafts, so a slip could
	display as Paid while no GL Entry existed. Only docstatus 1 counts.
	"""
	if not name:
		return False
	docstatus = known_docstatus if known_docstatus is not None else frappe.db.get_value(
		"Journal Entry", name, "docstatus")
	return cint(docstatus) == 1


#: Every value :func:`_slip_status` can return, in lifecycle order. Anything that
#: counts slips by status seeds itself from this, so the two cannot drift apart —
#: they already had: the overview counter was still seeded with "Accrued", a name
#: this status carried before it became "Posted", so the first slip ever to reach
#: Posted made the whole Overview screen fail with KeyError: 'Posted'.
SLIP_STATUSES = ("Draft", "Submitted", "Posted", "Paid", "Cancelled")


def _slip_status(docstatus, journal_entry=None, payment_entry=None,
                 je_docstatus=None, pe_docstatus=None):
	"""Lifecycle status of a salary slip, derived from what is provably true.

	    Draft      not yet submitted
	    Submitted  approved payroll, nothing in the ledger yet
	    Posted     the accrual Journal Entry is submitted (expense and liabilities booked)
	    Paid       the payment Journal Entry is submitted (the payable has been cleared)
	    Cancelled

	"Posted" and "Paid" are deliberately distinct: booking the payroll liability is not
	evidence that anyone was paid.
	"""
	if cint(docstatus) == 2:
		return "Cancelled"
	if cint(docstatus) == 0:
		return "Draft"
	if _je_is_posted(payment_entry, pe_docstatus):
		return "Paid"
	if _je_is_posted(journal_entry, je_docstatus):
		return "Posted"
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
	counts = {k: 0 for k in SLIP_STATUSES}
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

	cyc_start, cyc_end = _default_cycle_period()
	return {
		"companies": _companies(),
		"company": company,
		"period": {"start": str(start), "end": str(end)},
		"default_period": {"start": str(cyc_start), "end": str(cyc_end)},
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
		 "custom_iban", "custom_insurance", "default_shift", "cell_number", "personal_email"],
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
	_guard(perms.ATTENDANCE_WRITE)
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
def get_attendance(name):
	"""Fetch a single Attendance record for the dashboard edit dialog."""
	_guard()
	return frappe.db.get_value(
		"Attendance", name,
		["name", "employee", "employee_name", "attendance_date", "status", "working_hours",
		 "coalesce(custom_overtime_hours,0) as overtime_hours", "shift", "docstatus"],
		as_dict=True,
	)


@frappe.whitelist()
def update_attendance(name, status=None, working_hours=None, overtime_hours=None):
	"""Edit an existing Attendance record from the dashboard.

	Overtime hours (and worked hours) can be corrected even after the record is submitted —
	HR often only learns the overtime the next day — via a direct field update that keeps the
	submitted document intact. Changing the *status* still requires a draft record (cancel the
	submitted one first), since status drives the payroll deduction logic.
	"""
	_guard(perms.ATTENDANCE_WRITE)
	doc = frappe.get_doc("Attendance", name)
	if doc.docstatus == 2:
		frappe.throw(_("This attendance is cancelled and cannot be edited."))

	if doc.docstatus == 0:
		if status is not None:
			doc.status = status
		if working_hours is not None:
			doc.working_hours = flt(working_hours)
		if overtime_hours is not None:
			doc.custom_overtime_hours = flt(overtime_hours)
		doc.save()
	else:
		# Submitted: allow the operational corrections (hours / overtime) without a cancel.
		if status is not None and status != doc.status:
			frappe.throw(_("Cancel this attendance before changing its status (status affects payroll)."))
		updates = {}
		if working_hours is not None:
			updates["working_hours"] = flt(working_hours)
		if overtime_hours is not None:
			updates["custom_overtime_hours"] = flt(overtime_hours)
		if updates:
			frappe.db.set_value("Attendance", name, updates)
	frappe.db.commit()
	return True


# --------------------------------------------------------------------------- #
# Attendance Occurrences (lateness / early exit / partial / half / full) + justification
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_occurrences(company=None, employee=None, status=None, from_date=None, to_date=None):
	_guard()
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if employee:
		conds.append("employee=%s"); vals.append(employee)
	if status:
		conds.append("status=%s"); vals.append(status)
	if from_date:
		conds.append("occurrence_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("occurrence_date<=%s"); vals.append(getdate(to_date))
	return frappe.db.sql(
		"""select name, employee, employee_name, occurrence_date, occurrence_type, hours,
		status, justification_reason, justification_deadline, justification_date, remarks,
		authorized, approved_by, is_extraordinary, justification_source,
		justification_document
		from `tabIsoft Attendance Occurrence` where {} order by occurrence_date desc, modified desc
		limit 500""".format(" and ".join(conds)),
		vals, as_dict=True,
	)


@frappe.whitelist()
def create_occurrence(data):
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	authorized = cint(d.get("authorized"))
	doc = frappe.new_doc("Isoft Attendance Occurrence")
	doc.update({
		"employee": d.get("employee"),
		"occurrence_date": d.get("occurrence_date"),
		"occurrence_type": d.get("occurrence_type"),
		"hours": flt(d.get("hours")),
		"remarks": d.get("remarks"),
		# Authorized/pre-approved occurrences are created already Justified (see controller).
		"authorized": authorized,
		"approved_by": d.get("approved_by") if authorized else None,
		"justification_reason": d.get("justification_reason") if authorized else None,
		"status": "Justified" if authorized else "Pending Justification",
	})
	doc.insert()
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def justify_occurrence(name, reason, document=None, remarks=None, extraordinary=0, note=None,
                       justification_source=None):
	"""Mark an occurrence as Justified with a reason (and optional supporting document).
	After the 5-day window this requires the Extraordinary override (HR Manager).

	``justification_source`` records how the explanation reached HR — the employee handed
	over a certificate, e-mailed it, or uploaded it through self-service. Without it the
	trail cannot distinguish HR excusing an absence from HR recording that somebody
	produced evidence for it.
	"""
	_guard()
	from isoft_angola_hr.isoft_angola_hr.services import hr_operations as hr_ops

	doc = frappe.get_doc("Isoft Attendance Occurrence", name)
	doc.status = "Justified"
	doc.justification_reason = reason
	source = hr_ops.validate_source(justification_source, label="Justification Source")
	if source:
		doc.justification_source = source
	if document:
		doc.justification_document = document
	if remarks:
		doc.remarks = remarks
	if cint(extraordinary):
		doc.is_extraordinary = 1
		doc.extraordinary_note = note
	doc.save()  # controller enforces the 5-day lock / override rules
	frappe.db.commit()
	return True


@frappe.whitelist()
def set_occurrence_status(name, status):
	"""Manually set the status (e.g. back to Pending, or straight to Unjustified)."""
	_guard()
	doc = frappe.get_doc("Isoft Attendance Occurrence", name)
	doc.status = status
	if status != "Justified":
		doc.justification_reason = None
	doc.save()
	frappe.db.commit()
	return True


@frappe.whitelist()
def delete_occurrence(name):
	_guard()
	frappe.delete_doc("Isoft Attendance Occurrence", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def list_absence_reasons(active_only=0):
	_guard()
	filters = {"is_active": 1} if cint(active_only) else {}
	return frappe.get_all("Isoft Absence Reason", filters=filters,
	                      fields=["name", "reason", "is_active"], order_by="reason")


@frappe.whitelist()
def save_absence_reason(reason, is_active=1, old_name=None):
	_guard()
	if old_name and frappe.db.exists("Isoft Absence Reason", old_name):
		doc = frappe.get_doc("Isoft Absence Reason", old_name)
		doc.is_active = cint(is_active)
		if old_name != reason:
			frappe.rename_doc("Isoft Absence Reason", old_name, reason, force=True)
		doc = frappe.get_doc("Isoft Absence Reason", reason)
		doc.is_active = cint(is_active)
		doc.save()
	elif not frappe.db.exists("Isoft Absence Reason", reason):
		frappe.get_doc({"doctype": "Isoft Absence Reason", "reason": reason,
		                "is_active": cint(is_active)}).insert()
	frappe.db.commit()
	return reason


@frappe.whitelist()
def delete_absence_reason(name):
	_guard()
	frappe.delete_doc("Isoft Absence Reason", name, force=1)
	frappe.db.commit()
	return True


# --------------------------------------------------------------------------- #
# Leave Applications (ERPNext core, managed from the dashboard)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_leave_types():
	_guard()
	return frappe.get_all(
		"Leave Type",
		fields=["name", "is_lwp", "is_carry_forward", "max_leaves_allowed", "is_compensatory"],
		order_by="name",
	)


@frappe.whitelist()
def save_leave_type(data, old_name=None):
	"""Create or update a Leave Type from the dashboard."""
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	name = d.get("leave_type_name")
	if old_name and frappe.db.exists("Leave Type", old_name):
		doc = frappe.get_doc("Leave Type", old_name)
		if old_name != name and name:
			frappe.rename_doc("Leave Type", old_name, name, force=True)
			doc = frappe.get_doc("Leave Type", name)
	elif frappe.db.exists("Leave Type", name):
		doc = frappe.get_doc("Leave Type", name)
	else:
		doc = frappe.new_doc("Leave Type")
		doc.leave_type_name = name
	for f in ("is_lwp", "is_carry_forward", "is_compensatory"):
		doc.set(f, cint(d.get(f)))
	doc.max_leaves_allowed = cint(d.get("max_leaves_allowed"))
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def delete_leave_type(name):
	_guard()
	frappe.delete_doc("Leave Type", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def bulk_allocate_leave(leave_type, from_date, to_date, new_leaves_allocated,
                        carry_forward=0, employees=None, company=None):
	"""Allocate the same leave entitlement to many employees at once. Skips employees who
	already have an allocation for this leave type overlapping the period."""
	_guard()
	sel = json.loads(employees) if isinstance(employees, str) and employees else (employees or None)
	if sel:
		emps = list(sel)
	else:
		f = {"status": "Active"}
		if company:
			f["company"] = company
		emps = frappe.get_all("Employee", filters=f, pluck="name")
	frm, to = getdate(from_date), getdate(to_date)
	created, skipped, errors = 0, 0, []
	for emp in emps:
		exists = frappe.db.sql(
			"""select name from `tabLeave Allocation`
			where employee=%s and leave_type=%s and docstatus<2
			and from_date<=%s and to_date>=%s limit 1""",
			(emp, leave_type, to, frm),
		)
		if exists:
			skipped += 1
			continue
		try:
			doc = frappe.new_doc("Leave Allocation")
			doc.update({
				"employee": emp, "leave_type": leave_type, "from_date": frm, "to_date": to,
				"new_leaves_allocated": flt(new_leaves_allocated), "carry_forward": cint(carry_forward),
				"company": frappe.db.get_value("Employee", emp, "company"),
			})
			doc.insert(ignore_permissions=True)
			doc.submit()
			created += 1
		except Exception as e:
			errors.append(f"{emp}: {str(e)}")
	frappe.db.commit()
	return {"created": created, "skipped": skipped, "errors": errors}


@frappe.whitelist()
def leave_balances(leave_type, as_of=None, company=None):
	"""Per-employee balance for a leave type as of a date: allocated / used / remaining."""
	_guard()
	from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on

	as_of = getdate(as_of) if as_of else getdate(nowdate())
	f = {"status": "Active"}
	if company:
		f["company"] = company
	emps = frappe.get_all("Employee", filters=f, fields=["name", "employee_name"], order_by="employee_name")
	out = []
	for e in emps:
		allocated = flt(frappe.db.sql(
			"""select coalesce(sum(total_leaves_allocated),0) from `tabLeave Allocation`
			where employee=%s and leave_type=%s and docstatus=1 and from_date<=%s and to_date>=%s""",
			(e.name, leave_type, as_of, as_of),
		)[0][0])
		try:
			remaining = flt(get_leave_balance_on(e.name, leave_type, str(as_of)))
		except Exception:
			remaining = 0.0
		if not allocated and not remaining:
			continue  # skip employees with no entitlement for this type
		out.append({
			"employee": e.name, "employee_name": e.employee_name,
			"allocated": allocated, "used": flt(allocated - remaining, 2), "remaining": remaining,
		})
	return out


@frappe.whitelist()
def list_leaves(company=None, employee=None, status=None, from_date=None, to_date=None):
	_guard()
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if employee:
		conds.append("employee=%s"); vals.append(employee)
	if status:
		conds.append("status=%s"); vals.append(status)
	if from_date:
		conds.append("to_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("from_date<=%s"); vals.append(getdate(to_date))
	return frappe.db.sql(
		"""select name, employee, employee_name, leave_type, from_date, to_date,
		total_leave_days, half_day, status, docstatus, description
		from `tabLeave Application` where {} order by from_date desc, modified desc limit 500""".format(
			" and ".join(conds)),
		vals, as_dict=True,
	)


@frappe.whitelist()
def create_leave(data):
	"""Create a Leave Application (Open/draft) from the dashboard.

	This is the PRIMARY way leave is recorded. The employee tells HR — in person, by
	email, on paper — and HR enters it here; the employee is not required to hold a login.
	``/ess`` remains available for employees who do, and produces the same record.

	``leave_approver`` defaults to the HR user entering it because on this site HR decides
	leave. It is not an approval: the request is created Open and still has to be approved
	or rejected, which requires LEAVE_APPROVE.
	"""
	_guard()
	from isoft_angola_hr.isoft_angola_hr.services import hr_operations as hr_ops

	d = json.loads(data) if isinstance(data, str) else data
	company = d.get("company") or frappe.db.get_value("Employee", d.get("employee"), "company")
	doc = frappe.new_doc("Leave Application")
	doc.update({
		"employee": d.get("employee"),
		"leave_type": d.get("leave_type"),
		"from_date": d.get("from_date"),
		"to_date": d.get("to_date"),
		"half_day": cint(d.get("half_day")),
		"half_day_date": d.get("half_day_date") or None,
		"description": d.get("description"),
		"company": company,
		"posting_date": nowdate(),
		"status": "Open",
		"leave_approver": d.get("leave_approver") or frappe.session.user,
	})
	source = hr_ops.validate_source(d.get("request_source"))
	if source and doc.meta.has_field("custom_request_source"):
		doc.custom_request_source = source
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _finalize_leave(name, status):
	doc = frappe.get_doc("Leave Application", name)
	doc.status = status
	if doc.docstatus == 0:
		doc.submit()  # Approved/Rejected leave applications are submitted
	else:
		doc.save(ignore_permissions=True)
	frappe.db.commit()
	return True


@frappe.whitelist()
def approve_leave(name):
	_guard(perms.LEAVE_APPROVE)
	return _finalize_leave(name, "Approved")


@frappe.whitelist()
def reject_leave(name):
	_guard(perms.LEAVE_APPROVE)
	return _finalize_leave(name, "Rejected")


@frappe.whitelist()
def cancel_leave(name):
	_guard()
	doc = frappe.get_doc("Leave Application", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.db.commit()
	return True


@frappe.whitelist()
def delete_leave(name):
	_guard()
	doc = frappe.get_doc("Leave Application", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Leave Application", name, force=1)
	frappe.db.commit()
	return True


# --------------------------------------------------------------------------- #
# Leave Allocations (the balance/entitlement a leave request draws from)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_leave_allocations(company=None, employee=None, leave_type=None):
	_guard()
	conds = ["docstatus<2"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if employee:
		conds.append("employee=%s"); vals.append(employee)
	if leave_type:
		conds.append("leave_type=%s"); vals.append(leave_type)
	return frappe.db.sql(
		"""select name, employee, employee_name, leave_type, from_date, to_date,
		total_leaves_allocated, carry_forward, docstatus
		from `tabLeave Allocation` where {} order by from_date desc, modified desc limit 500""".format(
			" and ".join(conds)),
		vals, as_dict=True,
	)


@frappe.whitelist()
def create_leave_allocation(data):
	"""Create + submit a Leave Allocation (gives the employee a balance to request against)."""
	_guard()
	d = json.loads(data) if isinstance(data, str) else data
	company = d.get("company") or frappe.db.get_value("Employee", d.get("employee"), "company")
	doc = frappe.new_doc("Leave Allocation")
	doc.update({
		"employee": d.get("employee"),
		"leave_type": d.get("leave_type"),
		"from_date": d.get("from_date"),
		"to_date": d.get("to_date"),
		"new_leaves_allocated": flt(d.get("new_leaves_allocated")),
		"carry_forward": cint(d.get("carry_forward")),
		"company": company,
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def delete_leave_allocation(name):
	_guard()
	doc = frappe.get_doc("Leave Allocation", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Leave Allocation", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def bulk_mark_attendance(attendance_date, rows, company=None):
	"""Mark attendance for many employees at once for a single day. `rows` is a list of
	{employee, status, working_hours, overtime_hours}. Everyone defaults to Present in the
	UI; HR only changes the absentees/incidents. Skips employees already marked (submitted)
	that day, and skips Sundays."""
	_guard(perms.ATTENDANCE_WRITE)
	date = getdate(attendance_date)
	if date.weekday() == 6:  # Sunday
		frappe.throw(_("{0} is a Sunday (non-working day).").format(date))
	rows = json.loads(rows) if isinstance(rows, str) else rows
	created, skipped, errors = 0, 0, []
	for r in rows:
		emp = r.get("employee")
		if not emp:
			continue
		if frappe.db.exists("Attendance", {"employee": emp, "attendance_date": date, "docstatus": ("<", 2)}):
			skipped += 1
			continue
		try:
			doc = frappe.new_doc("Attendance")
			doc.employee = emp
			doc.attendance_date = date
			doc.status = r.get("status") or "Present"
			doc.working_hours = flt(r.get("working_hours"))
			doc.custom_overtime_hours = flt(r.get("overtime_hours"))
			doc.company = company or frappe.db.get_value("Employee", emp, "company")
			doc.save()
			doc.submit()
			created += 1
		except Exception as e:
			errors.append(f"{emp}: {str(e)}")
	frappe.db.commit()
	return {"created": created, "skipped": skipped, "errors": errors}


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
def list_salary_profiles(company=None, search=None):
	_guard(perms.SALARY_PROFILE_READ)
	filters = {}
	if company:
		filters["company"] = company
	or_filters = None
	if search:
		or_filters = {"employee_name": ("like", f"%{search}%"), "employee": ("like", f"%{search}%")}
	return frappe.get_all(
		"Isoft Salary Profile", filters=filters, or_filters=or_filters,
		fields=["name", "employee", "employee_name", "from_date", "base", "food_allowance",
		        "transport_allowance", "family_allowance"],
		order_by="employee_name", limit_page_length=500,
	)


@frappe.whitelist()
def list_salary_history(employee=None, salary_profile=None):
	"""Salary-change log for an employee (or a specific profile), newest first."""
	_guard(perms.SALARY_PROFILE_READ)
	filters = {}
	if employee:
		filters["employee"] = employee
	if salary_profile:
		filters["salary_profile"] = salary_profile
	return frappe.get_all(
		"Isoft Salary History", filters=filters,
		fields=["name", "employee", "employee_name", "salary_profile", "change_date", "changed_by",
		        "change_type", "base", "food_allowance", "transport_allowance", "family_allowance"],
		order_by="change_date desc", limit_page_length=200,
	)


@frappe.whitelist()
def save_salary_profile(data):
	_guard(perms.SALARY_PROFILE_WRITE)
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
	_guard(perms.PAYROLL_READ)
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if from_date:
		conds.append("start_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("end_date<=%s"); vals.append(getdate(to_date))
	# Company isolation: a user restricted by User Permission never sees another
	# company's payroll runs, whatever company they ask for.
	scope, scope_vals = perms.company_filter_sql()
	if scope:
		conds.append(scope); vals.extend(scope_vals)
	return frappe.db.sql(
		"""select name, company, start_date, end_date, number_of_employees, total_net_pay,
		salary_slips_created, salary_slips_submitted, ifnull(status,'Draft') as status,
		payroll_group, approved_by, posted_by
		from `tabIsoft Payroll Entry` where {} order by start_date desc limit 100""".format(
			" and ".join(conds)
		),
		vals, as_dict=True,
	)


@frappe.whitelist()
def create_payroll_entry(company, start_date, end_date, posting_date=None, department=None):
	_guard(perms.PAYROLL_PREPARE, company)
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


# Canonical payroll-preview columns. `money` = currency value; `input` = editable in the
# preview (overtime/bonus/advance/vacation/natal). Order/visibility is configurable and saved.
PREVIEW_COLUMNS = [
	{"key": "employee_name", "label": "Employee", "money": 0, "input": 0},
	{"key": "department", "label": "Department", "money": 0, "input": 0},
	{"key": "days", "label": "Paid/Total Days", "money": 0, "input": 0},
	{"key": "base", "label": "Base", "money": 1, "input": 0},
	{"key": "food", "label": "Food Allowance", "money": 1, "input": 0},
	{"key": "transport", "label": "Transport Allowance", "money": 1, "input": 0},
	{"key": "vacation", "label": "Férias", "money": 1, "input": 1},
	{"key": "christmas", "label": "Natal", "money": 1, "input": 1},
	{"key": "overtime_amount", "label": "Overtime", "money": 1, "input": 1},
	{"key": "productivity_bonus", "label": "Bonus", "money": 1, "input": 1},
	{"key": "adiantamento", "label": "Advance", "money": 1, "input": 1},
	{"key": "absence", "label": "Absence Value", "money": 1, "input": 0},
	{"key": "taxable_income", "label": "Taxable", "money": 1, "input": 0},
	{"key": "ss", "label": "INSS", "money": 1, "input": 0},
	{"key": "ss_employer", "label": "INSS Entidade Patronal", "money": 1, "input": 0},
	{"key": "irt", "label": "IRT", "money": 1, "input": 0},
	{"key": "gross_pay", "label": "Gross", "money": 1, "input": 0},
	{"key": "net_pay", "label": "Net", "money": 1, "input": 0},
]
_PREVIEW_META = {c["key"]: c for c in PREVIEW_COLUMNS}


@frappe.whitelist()
def get_preview_columns():
	"""Ordered preview columns with a `visible` flag, merging the saved config with the
	canonical list (new columns are appended, visible by default)."""
	_guard(perms.PAYROLL_PREVIEW)
	saved = frappe.db.get_single_value("Isoft HR Settings", "payroll_preview_columns")
	try:
		saved = json.loads(saved) if saved else []
	except Exception:
		saved = []
	ordered, seen = [], set()
	for c in saved:
		key = c.get("key")
		if key in _PREVIEW_META and key not in seen:
			m = dict(_PREVIEW_META[key])
			m["visible"] = 0 if c.get("visible") in (0, "0", False) else 1
			ordered.append(m)
			seen.add(key)
	for c in PREVIEW_COLUMNS:
		if c["key"] not in seen:
			m = dict(c)
			m["visible"] = 1
			ordered.append(m)
	return ordered


@frappe.whitelist()
def save_preview_columns(columns):
	"""Persist the preview column order + visibility (list of {key, visible})."""
	_guard(perms.PAYROLL_PREVIEW)
	cols = json.loads(columns) if isinstance(columns, str) else columns
	clean = [{"key": c["key"], "visible": 1 if c.get("visible") else 0}
	         for c in cols if c.get("key") in _PREVIEW_META]
	settings = frappe.get_single("Isoft HR Settings")
	settings.payroll_preview_columns = json.dumps(clean)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return True


def _exception_row(employee, start, end, message):
	"""A preview row for an employee payroll cannot be calculated for.

	The row is deliberately still returned: HR needs to SEE who is blocked and why,
	rather than have the person silently vanish from the run.
	"""
	return {
		"employee": employee.name, "employee_name": employee.employee_name,
		"department": employee.get("department"), "designation": employee.get("designation"),
		"base": 0.0, "food": 0.0, "transport": 0.0, "absence": 0.0,
		"total_working_days": 0.0, "payment_days": 0.0,
		"productivity_bonus": 0.0, "overtime_amount": 0.0, "adiantamento": 0.0,
		"vacation": 0.0, "ferias_full": 0.0, "christmas": 0.0, "natal_default": 0.0,
		"taxable_income": 0.0, "ss": 0.0, "ss_employer": 0.0, "irt": 0.0,
		"gross_pay": 0.0, "total_deduction": 0.0, "net_pay": 0.0,
		"already": 0, "existing_slip": None, "existing_status": None,
		"blocked": 1, "exception": message,
	}


@frappe.whitelist()
def payroll_preview(company, start_date, end_date, department=None, branch=None,
                    designation=None, inputs=None, validate_attendance=0, based_on_timesheet=0):
	"""Dry-run a payroll batch: returns one computed row per eligible employee, honouring
	any per-employee variable inputs (overtime / bonus / advance) passed back from the UI."""
	_guard(perms.PAYROLL_PREVIEW, company)
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
		assert_single_profile_for_period,
	)
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
		attendance_overtime_amount, compute_working_days,
	)
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
	                      fields=["name", "employee_name", "department", "designation", "date_of_joining"],
	                      order_by="employee_name", limit_page_length=2000)
	settings = engine.get_settings()
	out = []
	for e in emps:
		# One employee's data problem must not blank the whole preview: capture it as an
		# exception row so HR can see exactly who is blocking the run and why.
		try:
			prof = assert_single_profile_for_period(e.name, start, end, company=company,
			                                        employee_name=e.employee_name)
		except frappe.ValidationError as exc:
			out.append(_exception_row(e, start, end, frappe.utils.strip_html(str(exc))))
			continue
		if not prof:
			out.append(_exception_row(e, start, end,
			                          _("No Salary Profile is effective on {0}.").format(end)))
			continue
		if not prof.irt_table:
			prof.irt_table = settings.default_irt_table
		try:
			twd, pay_days = compute_working_days(e.name, start, end,
			                                     validate_attendance=validate_attendance,
			                                     based_on_timesheet=based_on_timesheet)
		except frappe.ValidationError as exc:
			out.append(_exception_row(e, start, end, frappe.utils.strip_html(str(exc))))
			continue
		# Defaults: Férias is paid only when HR ticks the employee (default 0); the tick
		# fills the full amount (ferias_full). Natal defaults to the December-prorated
		# amount and is editable per employee.
		ferias_full = engine.ferias_full(prof.base, settings.ferias_rate)
		natal_default = engine.default_natal(prof.base, settings.natal_rate, e.date_of_joining, end,
		                                     settings.get("natal_payment_month"))
		# Overtime defaults to the amount computed from logged Attendance overtime hours
		# (when validating attendance); HR can override it in the preview.
		ot_default = (attendance_overtime_amount(e.name, prof.base, twd, start, end, settings.overtime_multiplier)
		              if cint(validate_attendance) else 0.0)
		inp = inputs.get(e.name)
		if inp is None:
			ferias_amt, natal_amt = 0.0, natal_default
			overtime_amt = ot_default
		else:
			ferias_amt = flt(inp.get("ferias_amount"))
			natal_amt = flt(inp.get("natal_amount"))
			overtime_amt = flt(inp.get("overtime_amount"))
		try:
			res = engine.compute_slip(prof, {
				"productivity_bonus": flt((inp or {}).get("productivity_bonus")),
				"overtime_amount": overtime_amt,
				"adiantamento": flt((inp or {}).get("adiantamento")),
				"ferias_amount": ferias_amt, "natal_amount": natal_amt,
				"payment_days": pay_days, "total_working_days": twd,
				"start_date": start, "end_date": end,
			}, settings=settings, on_date=end, employee=e.employee_name)
		except frappe.ValidationError as exc:
			out.append(_exception_row(e, start, end, frappe.utils.strip_html(str(exc))))
			continue
		ded = {d["abbr"]: d["amount"] for d in res["deductions"]}
		# Base / food / transport are shown NET of absences (already prorated by the engine).
		# Absence value = base_day/8 × absent hours = base × (1 − paid/total), base-only.
		earn = {x["abbr"]: x["amount"] for x in res["earnings"]}
		base_net = flt(earn.get("SB"))
		food_net = flt(earn.get("SDA"))
		transport_net = flt(earn.get("SDT"))
		absence_value = flt(flt(prof.base) - base_net, 2)
		# Already processed? A non-cancelled salary slip whose period overlaps this one.
		ex = frappe.db.sql(
			"""select name, docstatus, journal_entry, payment_entry from `tabIsoft Salary Slip`
			where employee=%s and docstatus<2 and start_date<=%s and end_date>=%s limit 1""",
			(e.name, end, start), as_dict=True,
		)
		already = bool(ex)
		out.append({
			"employee": e.name, "employee_name": e.employee_name,
			"department": e.department, "designation": e.designation,
			"base": base_net, "food": food_net,
			"transport": transport_net, "absence": absence_value,
			"total_working_days": twd, "payment_days": pay_days,
			"productivity_bonus": flt((inp or {}).get("productivity_bonus")),
			"overtime_amount": flt(overtime_amt),
			"adiantamento": flt((inp or {}).get("adiantamento")),
			"vacation": flt(ferias_amt), "ferias_full": flt(ferias_full),
			"christmas": flt(natal_amt), "natal_default": flt(natal_default),
			"taxable_income": res["taxable_income"], "ss": flt(ded.get("CTSS3")),
			"ss_employer": flt(res.get("ss_employer_amount")),
			"employer_cost": flt(res.get("employer_cost")),
			"irt": flt(ded.get("IRT")), "gross_pay": res["gross_pay"],
			"total_deduction": res["total_deduction"], "net_pay": res["net_pay"],
			# Calculated so HR can see it, but blocked from submission and bank export.
			"blocked": 1 if res.get("has_negative_net") else 0,
			"exception": (_("Net pay is negative ({0}). Reduce the deductions before "
			                "submitting.").format(flt(res["net_pay"], 2))
			              if res.get("has_negative_net") else None),
			"already": 1 if already else 0,
			"existing_slip": ex[0].name if already else None,
			"existing_status": _slip_status(ex[0].docstatus, ex[0].journal_entry, ex[0].payment_entry) if already else None,
		})
	return out


@frappe.whitelist()
def create_payroll_from_preview(company, start_date, end_date, rows, posting_date=None,
                                validate_attendance=0, based_on_timesheet=0):
	"""Create the Isoft Payroll Entry + Salary Slips from the previewed/edited rows."""
	_guard(perms.PAYROLL_PREPARE, company)
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import get_active_profile

	rows = json.loads(rows) if isinstance(rows, str) else rows
	if not rows:
		frappe.throw(_("No employees to process."))

	# Rows the preview flagged as uncalculable or unpayable must not become payroll.
	blocked = [r for r in rows if cint(r.get("blocked"))]
	if blocked:
		frappe.throw(
			_("{0} employee(s) still have unresolved payroll exceptions and cannot be "
			  "processed:<br><br>{1}").format(
				len(blocked),
				"<br>".join("<b>{0}</b>: {1}".format(
					frappe.utils.escape_html(r.get("employee_name") or r.get("employee")),
					frappe.utils.escape_html(r.get("exception") or _("unknown error")))
					for r in blocked[:20]),
			),
			title=_("Payroll Blocked"),
		)

	entry = frappe.new_doc("Isoft Payroll Entry")
	entry.company = company
	entry.start_date, entry.end_date = getdate(start_date), getdate(end_date)
	entry.posting_date = getdate(posting_date) if posting_date else getdate(end_date)
	entry.validate_attendance = cint(validate_attendance)
	entry.based_on_timesheet = cint(based_on_timesheet)
	for r in rows:
		prof = get_active_profile(r["employee"], entry.end_date, company=company,
		                          employee_name=r.get("employee_name"))
		entry.append("employees", {
			"employee": r["employee"], "employee_name": r.get("employee_name"),
			"salary_profile": prof.name if prof else None,
			"productivity_bonus": flt(r.get("productivity_bonus")),
			"overtime_amount": flt(r.get("overtime_amount")),
			"adiantamento": flt(r.get("adiantamento")),
			"subsidio_ferias": flt(r.get("subsidio_ferias")),
			"subsidio_natal": flt(r.get("subsidio_natal")),
		})
	entry.number_of_employees = len(entry.employees)
	entry.insert()
	entry.create_salary_slips()
	return {"name": entry.name, "employees": entry.number_of_employees,
	        "total_net_pay": flt(entry.total_net_pay)}


@frappe.whitelist()
def export_payroll_preview(company, start_date, end_date, department=None, branch=None,
                           designation=None, inputs=None, validate_attendance=0,
                           based_on_timesheet=0, file_format="excel"):
	"""Export the payroll preview (recomputed with the same filters/inputs) as a formatted
	.xlsx or .pdf. Returns {filename, mime, content} (content is base64) for client download."""
	_guard(perms.PAYROLL_PREVIEW)
	import base64

	rows = payroll_preview(company, start_date, end_date, department, branch, designation,
	                       inputs, validate_attendance, based_on_timesheet)
	currency = frappe.db.get_single_value("Isoft HR Settings", "currency") or "AOA"

	# Follow the saved column config: only visible columns, in the chosen order.
	cols = [(c["label"], c["key"], bool(c["money"]))
	        for c in get_preview_columns() if c.get("visible")]

	def cell(r, key):
		if key == "days":
			return "{0}/{1}".format(flt(r.get("payment_days")), flt(r.get("total_working_days")))
		if key in ("employee_name", "department"):
			return r.get(key) or ""
		return flt(r.get(key), 2)

	totals = {k: 0.0 for (_l, k, m) in cols if m}
	for r in rows:
		for k in totals:
			totals[k] += flt(r.get(k))

	period = "{0} - {1}".format(formatdate(start_date), formatdate(end_date))
	title = "{0} — {1}".format(company or "", period)
	fname = "PayrollPreview_{0}_{1}".format(start_date, end_date)

	if file_format == "pdf":
		from frappe.utils.pdf import get_pdf

		def money(v):
			return frappe.utils.fmt_money(flt(v), currency=currency)

		head = "".join("<th style='{0}'>{1}</th>".format(
			"text-align:right;" if m else "text-align:left;", _(l)) for (l, k, m) in cols)
		body = ""
		for r in rows:
			tds = ""
			for (l, k, m) in cols:
				v = cell(r, k)
				tds += "<td style='{0}'>{1}</td>".format(
					"text-align:right;" if m else "text-align:left;",
					money(v) if m else frappe.utils.escape_html(str(v)))
			body += "<tr>{0}</tr>".format(tds)
		tfoot = ""
		for i, (l, k, m) in enumerate(cols):
			if i == 0:
				tfoot += "<td><b>{0}</b></td>".format(_("TOTAL"))
			elif m:
				tfoot += "<td style='text-align:right;'><b>{0}</b></td>".format(money(totals[k]))
			else:
				tfoot += "<td></td>"
		html = """
		<div style="font-family:Arial,sans-serif;font-size:11px;">
			<h3 style="margin:0 0 2px;">{title}</h3>
			<div style="color:#666;margin-bottom:10px;">{sub} — {n} {emp}</div>
			<table style="width:100%;border-collapse:collapse;" border="1" cellpadding="5">
				<thead style="background:#f0f4fa;">{head}</thead>
				<tbody>{body}</tbody>
				<tfoot style="background:#f0f4fa;"><tr>{tfoot}</tr></tfoot>
			</table>
		</div>""".format(title=frappe.utils.escape_html(title), sub=_("Payroll Preview"),
		                 n=len(rows), emp=_("employees"), head=head, body=body, tfoot=tfoot)
		content = get_pdf(html, options={"orientation": "Landscape"})
		mime, ext = "application/pdf", "pdf"
	else:
		from frappe.utils.xlsxutils import make_xlsx

		data = [[title], [_(l) for (l, k, m) in cols]]
		for r in rows:
			data.append([cell(r, k) for (l, k, m) in cols])
		total_row = []
		for i, (l, k, m) in enumerate(cols):
			total_row.append("TOTAL" if i == 0 else (flt(totals[k], 2) if m else ""))
		data.append(total_row)
		content = make_xlsx(data, "Payroll Preview").getvalue()
		mime, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"

	return {"filename": "{0}.{1}".format(fname, ext), "mime": mime,
	        "content": base64.b64encode(content).decode()}


def _payroll_cost_center(slip):
	"""Employee payroll cost centre, then the department's, then the company default —
	the same resolution ERPNext's own payroll uses. Previously every payroll line
	inherited the company default, so departmental cost was unrecoverable from the GL."""
	cc = frappe.db.get_value("Employee", slip.employee, "payroll_cost_center")
	if not cc and slip.get("department"):
		cc = frappe.db.get_value("Department", slip.department, "payroll_cost_center")
	return cc or frappe.db.get_value("Company", slip.company, "cost_center")


def _party_fields(account, employee):
	"""Employee party details, but ONLY when the account is genuinely a Payable account.

	ERPNext requires Party Type/Party on Receivable and Payable accounts and ignores
	them elsewhere, so posting a party on an untyped liability account would add noise
	without producing a sub-ledger. Doing it conditionally also means a site that
	configures a proper Payable account keeps posting successfully instead of failing
	validation on submit.
	"""
	if frappe.db.get_value("Account", account, "account_type") == "Payable":
		return {"party_type": "Employee", "party": employee}
	return {}


def _payable_account(slip, settings):
	"""Per-employee Payroll Payable account overrides the Settings default."""
	emp_payable = frappe.db.get_value("Employee", slip.employee, "custom_payroll_payable_account")
	account = emp_payable or settings.get("payroll_payable_account")
	if not account:
		frappe.throw(_("Configure the Payroll Payable account (Settings or Employee) first."))
	return account


def _payroll_entry_of(slip):
	return frappe.get_doc("Isoft Payroll Entry", slip.payroll_entry) if slip.payroll_entry else None


def _assert_posting_authorized(slip):
	"""Only APPROVED payroll may reach the general ledger.

	The role check has already happened; this is the workflow half. A direct API call to
	post a slip whose payroll is still being prepared now fails here rather than quietly
	booking unapproved payroll — which was possible before Phase 2, because posting only
	ever looked at the slip's own docstatus.
	"""
	entry = _payroll_entry_of(slip)
	if not entry:
		# A standalone slip (no payroll run) has no approval to check. It still requires
		# the posting role and a submitted slip, and it is not the normal path.
		return None
	perms.require_company(entry.company)
	state = wf.state_of(entry)
	if state not in (wf.APPROVED, wf.POSTED, wf.PAYMENT_READY, wf.PAID):
		frappe.throw(
			_("O processamento salarial {0} ainda não foi aprovado (estado: {1}). Não é "
			  "possível contabilizar payroll não aprovado.").format(frappe.bold(entry.name), _(state)),
			title=_("Payroll Not Approved"))
	if state == wf.APPROVED:
		wf.assert_approval_intact(entry)
	return entry


def _assert_payment_authorized(slip):
	"""Payment requires an approved, posted and released payroll — plus a payment
	authoriser who is not the person who approved it."""
	entry = _payroll_entry_of(slip)
	if not entry:
		return None
	wf.assert_can_pay(entry)
	return entry


def _existing_entry(slip_name, fieldname):
	"""The live accounting document linked to a slip, if it is not cancelled.

	Reads with a row lock so two concurrent posting requests cannot both decide that
	no entry exists and each create one.
	"""
	name = frappe.db.get_value("Isoft Salary Slip", slip_name, fieldname, for_update=True)
	if not name:
		return None
	docstatus = frappe.db.get_value("Journal Entry", name, "docstatus")
	if docstatus is None or cint(docstatus) == 2:
		return None  # deleted or cancelled — a fresh entry may be posted
	return name


@frappe.whitelist()
def make_journal_entry(salary_slip):
	"""Post the payroll accrual for a submitted salary slip and SUBMIT it.

	    Dr each earning component      (gross)
	    Dr employer Social Security    (employer cost)
	      Cr each deduction component  (employee INSS, IRT, advances)
	      Cr employer Social Security payable
	      Cr Payroll Payable           (net pay)

	The entry was previously only inserted, never submitted, so it stayed at
	docstatus=0 and produced no GL Entry at all while the UI reported the slip as
	posted. Creation and submission now happen in the caller's transaction: if
	submission fails nothing is written, so a slip can never be marked posted without
	a matching ledger entry.
	"""
	_guard(perms.PAYROLL_POST)
	slip = frappe.get_doc("Isoft Salary Slip", salary_slip)
	if slip.docstatus != 1:
		frappe.throw(_("Submit the salary slip before posting a Journal Entry."))
	_assert_posting_authorized(slip)
	existing = _existing_entry(slip.name, "journal_entry")
	if existing:
		return existing
	if flt(slip.net_pay) < 0:
		frappe.throw(
			_("Net pay for {0} is negative ({1}). Correct the slip before posting it.").format(
				frappe.bold(slip.employee_name or slip.employee), flt(slip.net_pay))
		)

	s = frappe.get_single("Isoft HR Settings")
	payable_account = _payable_account(slip, s)
	comp_acc = {r.abbr: r.account for r in s.component_accounts if r.account}
	cost_center = _payroll_cost_center(slip)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = slip.company
	je.posting_date = slip.posting_date or slip.end_date
	je.user_remark = _("Payroll: {0}").format(slip.name)

	missing = []

	def line(abbr, label, amount, side):
		account = comp_acc.get(abbr)
		if not account:
			missing.append(label)
			return
		je.append("accounts", {
			"account": account, "cost_center": cost_center,
			side: flt(amount), **_party_fields(account, slip.employee),
		})

	# Earnings -> debit each component's expense account.
	for e in slip.earnings:
		if e.do_not_include_in_total or not flt(e.amount):
			continue
		line(e.abbr, e.salary_component, e.amount, "debit_in_account_currency")
	# Deductions -> credit each component's liability account.
	for d in slip.deductions:
		if not flt(d.amount):
			continue
		line(d.abbr, d.salary_component, d.amount, "credit_in_account_currency")

	# Employer social security: an employer cost, booked expense/liability. It is not
	# part of gross and never touches net pay. Slips calculated before employer
	# contributions existed carry 0 and post nothing.
	employer_ss = flt(slip.get("ss_employer_amount"))
	if employer_ss:
		line("CTSSE", engine.COMPONENTS["CTSSE"]["name"], employer_ss, "debit_in_account_currency")
		line("CTSSP", engine.COMPONENTS["CTSSP"]["name"], employer_ss, "credit_in_account_currency")

	# Net pay -> credit Payroll Payable.
	je.append("accounts", {
		"account": payable_account, "cost_center": cost_center,
		"credit_in_account_currency": flt(slip.net_pay),
		**_party_fields(payable_account, slip.employee),
	})

	if missing:
		frappe.throw(
			_("No account is configured for these payroll components: {0}. "
			  "Set them under Settings -> Account per Component.").format(", ".join(sorted(set(missing))))
		)

	je.insert()
	je.submit()
	slip.db_set("journal_entry", je.name)
	return je.name


@frappe.whitelist()
def make_payment_entry(salary_slip, payment_account=None, posting_date=None,
                       bank_reference=None):
	"""Post and SUBMIT the salary payment (a Bank Entry Journal):
	Dr Payroll Payable (net) ; Cr Bank/Cash. Clears the payable booked by the accrual.
	"""
	_guard(perms.PAYROLL_CONFIRM_PAYMENT)
	slip = frappe.get_doc("Isoft Salary Slip", salary_slip)
	if slip.docstatus != 1:
		frappe.throw(_("Submit the salary slip before posting a Payment Entry."))
	_assert_payment_authorized(slip)
	existing = _existing_entry(slip.name, "payment_entry")
	if existing:
		return existing
	if flt(slip.net_pay) <= 0:
		frappe.throw(_("Net pay is zero or negative — nothing to pay for {0}.").format(
			slip.employee_name or slip.employee))
	if not _existing_entry(slip.name, "journal_entry"):
		frappe.throw(
			_("Post the accrual Journal Entry for {0} before paying it, so the payment "
			  "clears a payable that actually exists in the ledger.").format(
				frappe.bold(slip.employee_name or slip.employee))
		)

	s = frappe.get_single("Isoft HR Settings")
	pay_acc = payment_account or s.get("salary_payment_account")
	if not pay_acc:
		frappe.throw(_("Configure the Salary Payment (Bank/Cash) account in Settings first."))
	payable_account = _payable_account(slip, s)
	cost_center = _payroll_cost_center(slip)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Bank Entry"
	je.company = slip.company
	je.posting_date = getdate(posting_date) if posting_date else (slip.posting_date or slip.end_date)
	je.user_remark = _("Salary payment: {0}").format(slip.name)
	# ERPNext requires a reference on a Bank Entry. Use the same salary reference the
	# bank transfer file carries, so the ledger and the bank file reconcile.
	# ERPNext refuses to submit a Bank Entry without a reference, so one is always
	# generated. When Finance has the bank's own reference they pass it here and it
	# is used instead — the field never ends up empty either way (§18).
	je.cheque_no = (bank_reference or "").strip() or _salary_reference(slip.end_date)
	je.cheque_date = je.posting_date
	je.append("accounts", {
		"account": payable_account, "cost_center": cost_center,
		"debit_in_account_currency": flt(slip.net_pay),
		**_party_fields(payable_account, slip.employee),
	})
	je.append("accounts", {
		"account": pay_acc, "cost_center": cost_center,
		"credit_in_account_currency": flt(slip.net_pay),
	})
	je.insert()
	je.submit()
	slip.db_set("payment_entry", je.name)
	return je.name


def unlink_cancelled_payroll_entry(doc, method=None):
	"""Clear payroll links when their Journal Entry is cancelled (``on_cancel`` hook).

	Without this a submitted salary slip holding a Link to the entry blocks the entry
	from being cancelled, while the slip in turn refuses to cancel until the entry is
	gone — a deadlock with no way out of the correction workflow. Frappe runs
	``on_cancel`` before the back-link check, which is the same mechanism ERPNext's own
	``unlink_ref_doc_from_salary_slip`` relies on.

	The audit trail survives on the entry itself: its ``user_remark`` names the slip.
	"""
	for field in ("journal_entry", "payment_entry"):
		for slip in frappe.db.sql_list(
			"""select name from `tabIsoft Salary Slip`
			where {0}=%s and docstatus < 2""".format(field), doc.name):
			frappe.db.set_value("Isoft Salary Slip", slip, field, None, update_modified=False)


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
	"""Post the payroll: create the accrual Journal Entry for every submitted slip.

	This is the workflow's POST transition, not just a loop over slips — the payroll must
	be Approved, its numbers must still match the approval snapshot, and the entry moves
	to Posted once every slip carries a submitted ledger entry.
	"""
	_guard(perms.PAYROLL_POST)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	if wf.state_of(entry) == wf.APPROVED:
		wf.assert_can_post(entry)
	elif wf.state_of(entry) != wf.POSTED:
		frappe.throw(
			_("O processamento salarial ainda não foi aprovado (estado: {0}).").format(
				_(wf.state_of(entry))), title=_("Payroll Not Approved"))

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

	# A run that produced nothing but errors must fail loudly. Returning "created: 0"
	# with the reasons buried in a list is how a payroll silently does not get posted.
	if errors and not created:
		frappe.throw(_("No payroll could be posted.<br><br>{0}").format("<br>".join(errors[:10])),
		             title=_("Posting Failed"))

	entry.reload()
	if wf.state_of(entry) == wf.APPROVED and not errors and _all_slips_posted(entry):
		wf.perform(entry, wf.POST)
	return {"created": created, "skipped": skipped, "errors": errors,
	        "status": wf.state_of(entry)}


def _all_slips_posted(entry):
	rows = wf.slip_rows(entry)
	if not rows:
		return False
	return all(cint(r["docstatus"]) == 1 and _je_is_posted(r.get("journal_entry")) for r in rows)


@frappe.whitelist()
def make_bulk_payment_entry(name, payment_account=None, posting_date=None, employees=None,
                            bank_reference=None):
	"""Pay the payroll: create the payment Journal Entry for every posted slip.

	Requires a payroll that Finance has explicitly released for payment; the entry moves
	to Paid once every payable slip has a submitted payment entry.
	"""
	_guard(perms.PAYROLL_CONFIRM_PAYMENT)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	wf.assert_can_pay(entry)
	created, skipped, total, errors = 0, 0, 0.0, []
	for sname in _entry_slip_names(name, employees):
		slip = frappe.get_doc("Isoft Salary Slip", sname)
		if slip.docstatus != 1 or (slip.payment_entry and frappe.db.exists("Journal Entry", slip.payment_entry)):
			skipped += 1
			continue
		try:
			make_payment_entry(sname, payment_account=payment_account, posting_date=posting_date,
			                   bank_reference=bank_reference)
			created += 1
			total += flt(slip.net_pay)
		except Exception as e:
			errors.append(f"{slip.employee_name}: {str(e)}")

	if errors and not created:
		frappe.throw(_("No salary payment could be posted.<br><br>{0}").format(
			"<br>".join(errors[:10])), title=_("Payment Failed"))

	entry.reload()
	status = wf.refresh_payment_state(entry)
	return {"created": created, "skipped": skipped, "total": total, "errors": errors,
	        "status": status}


@frappe.whitelist()
def get_payroll_entry(name):
	_guard(perms.PAYROLL_READ)
	doc = frappe.get_doc("Isoft Payroll Entry", name)
	perms.require_company(doc.company)
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
		                                "salary_slips_submitted", "status", "payroll_group",
		                                "rejection_reason", "prepared_by", "submitted_by",
		                                "approved_by", "posted_by", "payment_authorized_by",
		                                "exported_by", "exported_at", "export_count"]},
		"status": wf.state_of(doc),
		"allowed_actions": wf.allowed_actions(doc),
		"totals": wf.compute_totals(doc),
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
	"""Turn the approved calculation into submitted salary slips."""
	_guard(perms.PAYROLL_READ)
	doc = frappe.get_doc("Isoft Payroll Entry", name)
	n = doc.submit_salary_slips()
	return {"submitted": n, "total_net_pay": flt(doc.total_net_pay), "status": wf.state_of(doc)}


# --------------------------------------------------------------------------- #
# Payroll workflow
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def payroll_action(name, action, reason=None):
	"""Perform a payroll lifecycle transition.

	One endpoint for every transition, so authorisation, state legality, self-approval
	and the audit stamp are applied identically no matter which button was pressed. The
	dashboard decides nothing: it asks for an action and the server accepts or refuses it.
	"""
	entry = frappe.get_doc("Isoft Payroll Entry", name)

	if action == wf.SUBMIT_FOR_APPROVAL:
		# Blocking exceptions must be cleared BEFORE an approver is asked to look at it.
		wf.assert_transition(entry, action)
		readiness.assert_ready_to_submit(entry)
	elif action == wf.RELEASE_FOR_PAYMENT:
		wf.assert_transition(entry, action)
		blockers = readiness.payment_blockers(entry)
		if blockers:
			frappe.throw(
				_("Não é possível libertar o pagamento enquanto existirem colaboradores sem "
				  "IBAN ({0}): {1}.").format(
					len(blockers), ", ".join(b["employee_name"] or b["employee"] for b in blockers[:10])),
				title=_("Payment Blocked"))
	elif action == wf.CLOSE:
		# Closing freezes the period, so it may only happen once the payroll is complete
		# and reconciles with the ledger.
		wf.assert_transition(entry, action)
		reconciliation.assert_ready_to_close(entry)
	elif action == wf.CANCEL:
		wf.assert_transition(entry, action)
		_assert_payroll_cancellable(entry)

	status = wf.perform(entry, action, reason=reason)
	return {"name": entry.name, "status": status,
	        "allowed_actions": wf.allowed_actions(entry)}


def _assert_payroll_cancellable(entry):
	"""A payroll still represented in the ledger cannot be cancelled.

	The correction path is explicit: cancel the payment entries, cancel the accrual
	entries, then cancel the payroll — never silently detach documents that a ledger
	still refers to.
	"""
	live = []
	for row in wf.slip_rows(entry):
		for field, label in (("payment_entry", _("payment")), ("journal_entry", _("accrual"))):
			name = row.get(field)
			if name and cint(frappe.db.get_value("Journal Entry", name, "docstatus")) == 1:
				live.append("{0} — {1} {2}".format(row["employee_name"] or row["employee"], label, name))
	if live:
		frappe.throw(
			_("This payroll still has {0} live accounting entr(ies). Cancel them first (that "
			  "reverses their GL entries) and then cancel the payroll:<br>{1}").format(
				len(live), "<br>".join(live[:10])),
			title=_("Cancel the Accounting First"))


@frappe.whitelist()
def payroll_approval_summary(name):
	"""Everything the approver needs on one screen — totals, variance against the previous
	run, exception counts and the full audit trail."""
	_guard(perms.PAYROLL_READ)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	perms.require_company(entry.company)
	summary = entry.approval_summary()
	summary["payment_blockers"] = readiness.payment_blockers(entry)
	return summary


@frappe.whitelist()
def payroll_readiness(company=None, start_date=None, end_date=None, department=None, branch=None,
                      designation=None, validate_attendance=0, based_on_timesheet=0,
                      include_variance=1):
	"""Server-authoritative pre-flight for a payroll period."""
	company = _default_company(company)
	if not (start_date and end_date):
		start_date, end_date = _default_cycle_period()
	return readiness.evaluate(
		company, start_date, end_date, department=department, branch=branch,
		designation=designation, validate_attendance=cint(validate_attendance),
		based_on_timesheet=cint(based_on_timesheet), include_variance=cint(include_variance),
	)


@frappe.whitelist()
def payroll_configuration_status(company=None, on_date=None):
	"""Which payroll configuration is present, missing or pointing at something deleted."""
	_guard(perms.PAYROLL_READ)
	company = _default_company(company)
	perms.require_company(company)
	return readiness.configuration_status(company, on_date=on_date)


@frappe.whitelist()
def get_production_readiness(company=None, on_date=None):
	"""Deployment-level readiness: configuration, statutory setup, roles and data."""
	return production_readiness.get_production_readiness(company=_default_company(company),
	                                                     on_date=on_date)


@frappe.whitelist()
def payroll_reconciliation(name):
	"""Month-end reconciliation of one payroll run against the general ledger."""
	return reconciliation.payroll_reconciliation(name)


@frappe.whitelist()
def get_permission_matrix():
	"""The permission matrix as actually enforced, derived from the enforcement table."""
	_guard(perms.PAYROLL_READ)
	return perms.permission_matrix()


@frappe.whitelist()
def export_bank_transfer(name, adapter=None):
	"""Download the payroll payment file for a payroll entry.

	Phase 5 moved the mechanics into ``services.bank_export`` so the format became an
	adapter rather than a hard-coded spreadsheet, and so the checks that actually matter
	could be added: IBAN *format* (not just presence), duplicate lines, a mixed-currency
	run, and a file total that must equal the payroll total. The generated file is now
	fingerprinted and recorded in Isoft Bank Export.

	None of that changes what this endpoint is. Only APPROVED, released payroll may reach
	the bank, and generating a file is an export, not a payment: no slip status changes.
	"""
	_guard(perms.PAYROLL_EXPORT_BANK)
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	result = bank_export.generate(name, adapter=adapter)
	frappe.response["filename"] = result["filename"]
	frappe.response["filecontent"] = result["content"]
	frappe.response["type"] = "binary"


@frappe.whitelist()
def bank_export_preflight(name, adapter=None):
	"""Everything wrong with this payment run, before a file exists.

	Finance can see every problem at once instead of discovering them one throw at a
	time, which is the difference between one correction round and five.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	return bank_export.validate_export(name, adapter=adapter)


@frappe.whitelist()
def bank_export_history(payroll_entry=None, company=None):
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	return bank_export.history(payroll_entry=payroll_entry, company=company)


@frappe.whitelist()
def record_bank_response(name, bank_reference, status="Submitted to Bank",
                         submitted_on=None, executed_on=None, notes=None):
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	return bank_export.record_bank_response(
		name, bank_reference, status=status, submitted_on=submitted_on,
		executed_on=executed_on, notes=notes)


@frappe.whitelist()
def bank_formats():
	_guard(perms.REPORT_BANK)
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	return bank_export.available_formats()


@frappe.whitelist()
def audit_employee_ibans(company=None):
	"""Which employee IBANs would be rejected by the bank. Read-only."""
	from isoft_angola_hr.isoft_angola_hr.services import bank_export

	return bank_export.audit_ibans(company=company)


# --------------------------------------------------------------------------- #
# Salary Slips
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def list_salary_slips(company=None, payroll_entry=None, employee=None, from_date=None, to_date=None,
                      status=None):
	_guard(perms.PAYROLL_READ)
	conds = ["1=1"]
	vals = []
	for field, val in (("company", company), ("payroll_entry", payroll_entry), ("employee", employee)):
		if val:
			conds.append("s.{0}=%s".format(field)); vals.append(val)
	if from_date:
		conds.append("s.start_date>=%s"); vals.append(getdate(from_date))
	if to_date:
		conds.append("s.end_date<=%s"); vals.append(getdate(to_date))
	# Lifecycle status is derived from the docstatus of the LINKED entries, not from the
	# link merely existing — a draft entry has no ledger effect.
	posted = "je.docstatus=1"
	paid = "pe.docstatus=1"
	status_sql = {
		"Draft": "s.docstatus=0",
		"Submitted": "s.docstatus=1 and ifnull({0},0)=0 and ifnull({1},0)=0".format(posted, paid),
		"Posted": "s.docstatus=1 and {0} and ifnull({1},0)=0".format(posted, paid),
		"Paid": "s.docstatus=1 and {0}".format(paid),
		"Cancelled": "s.docstatus=2",
	}
	if status and status in status_sql:
		conds.append(status_sql[status])
	rows = frappe.db.sql(
		"""select s.name, s.employee_name, s.start_date, s.end_date, s.gross_pay,
		s.total_deduction, s.net_pay, s.docstatus, s.journal_entry, s.payment_entry,
		je.docstatus as je_docstatus, pe.docstatus as pe_docstatus
		from `tabIsoft Salary Slip` s
		left join `tabJournal Entry` je on je.name=s.journal_entry
		left join `tabJournal Entry` pe on pe.name=s.payment_entry
		where {} order by s.start_date desc limit 300""".format(" and ".join(conds)),
		vals, as_dict=True,
	)
	for r in rows:
		r["status"] = _slip_status(r.get("docstatus"), r.get("journal_entry"), r.get("payment_entry"),
		                           r.get("je_docstatus"), r.get("pe_docstatus"))
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
	_guard(perms.PAYROLL_CANCEL)
	doc = frappe.get_doc("Isoft Salary Slip", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.db.commit()
	return True


@frappe.whitelist()
def delete_salary_slip(name):
	_guard(perms.PAYROLL_CANCEL)
	doc = frappe.get_doc("Isoft Salary Slip", name)
	_assert_no_entries(doc)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Isoft Salary Slip", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def cancel_payroll_entry(name):
	"""Cancel the payroll run: cancel its submitted salary slips and mark it Cancelled.

	This is the entry point of the correction process. The ledger has to be cleared
	first — a cancelled payroll whose accrual is still posted would leave the books
	claiming a liability the payroll no longer recognises.
	"""
	_guard(perms.PAYROLL_CANCEL)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	perms.require_company(entry.company)
	_assert_payroll_cancellable(entry)

	slips = [frappe.get_doc("Isoft Salary Slip", r.salary_slip) for r in entry.employees
	         if r.salary_slip and frappe.db.exists("Isoft Salary Slip", r.salary_slip)]
	# Block if any slip has a posted accrual/payment — remove those first.
	locked = [s.employee_name for s in slips if s.docstatus == 1 and (s.get("journal_entry") or s.get("payment_entry"))]
	if locked:
		frappe.throw(_("Remove the Journal Entry / Payment of these slips before cancelling: {0}").format(
			", ".join(locked)))
	n = 0
	with wf.unlocked():
		for s in slips:
			if s.docstatus == 1:
				s.cancel()
				n += 1
		entry.db_set("salary_slips_submitted", 0)
		entry.reload()
		if wf.state_of(entry) != wf.CANCELLED:
			wf.perform(entry, wf.CANCEL)
	frappe.db.commit()
	return n


@frappe.whitelist()
def delete_payroll_entry(name):
	"""Cancel + delete the entry's salary slips, then delete the entry.

	Only ever available for payroll nobody has approved: once approval exists the record
	is evidence and must be cancelled, not erased (enforced again in ``on_trash``).
	"""
	_guard(perms.PAYROLL_CANCEL)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	perms.require_company(entry.company)
	if wf.state_of(entry) not in (wf.DRAFT, wf.CALCULATED, wf.REJECTED, wf.CANCELLED):
		frappe.throw(
			_("A payroll that has been {0} cannot be deleted — cancel it instead so the audit "
			  "trail survives.").format(_(wf.state_of(entry))))
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
	_guard(perms.PAYROLL_READ)
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
# Final Settlement (termination / end-of-contract)
# --------------------------------------------------------------------------- #
def _settlement_working_days(employee, start, end):
	"""Working days in [start, end] for the settlement salary: every day except Sundays and
	public holidays (Saturday counts as a full day, matching the ITEC settlement drafts)."""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import get_holiday_dates
	start, end = getdate(start), getdate(end)
	if end < start:
		return 0.0
	holidays = get_holiday_dates(employee, start, end)
	n, d = 0, start
	while d <= end:
		if d.weekday() != 6 and d not in holidays:  # Sunday = 6
			n += 1
		d = add_days(d, 1)
	return float(n)


def _vested_untaken_leave_days(employee, as_of):
	"""Leave already **vested and not taken** — artigo 212.º n.º 1.

	Approximated by the remaining balance across the paid leave types, which is what this
	app records. It is a starting figure, not an authority: leave carried over under
	artigo 208.º, or a leave plan kept outside the system, will not be visible here, so
	HR can correct it on the form and the settlement stores whatever HR confirmed.
	"""
	try:
		from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on
	except Exception:
		return 0.0
	total = 0.0
	for lt in frappe.get_all("Leave Type", filters={"is_lwp": 0, "is_compensatory": 0}, pluck="name"):
		try:
			bal = flt(get_leave_balance_on(employee, lt, str(getdate(as_of))))
		except Exception:
			bal = 0.0
		if bal > 0:
			total += bal
	return flt(total, 2)


#: Kept under its old name because other code imports it.
_untaken_leave_days = _vested_untaken_leave_days


def _active_contract(employee, on_date):
	"""The contract in force on the termination date, for the fixed-term and notice rules."""
	rows = frappe.get_all(
		"Isoft Employment Contract",
		filters={"employee": employee, "docstatus": ("<", 2)},
		fields=["name", "contract_type", "is_open_ended", "start_date", "end_date",
		        "notice_days"],
		order_by="start_date desc", limit=5)
	on_date = getdate(on_date)
	for r in rows:
		if r.start_date and getdate(r.start_date) <= on_date and (
			not r.end_date or getdate(r.end_date) >= on_date):
			return r
	return rows[0] if rows else None


def _settlement_prefill(employee, termination_date):
	"""Everything the corrected Final Settlement form needs, derived rather than typed."""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import get_active_profile
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
	from isoft_angola_hr.isoft_angola_hr.services import advances

	term = getdate(termination_date)
	emp = frappe.db.get_value("Employee", employee,
	                          ["employee_name", "company", "date_of_joining"], as_dict=True) or frappe._dict()
	prof = get_active_profile(employee, term)
	s = frappe.get_single("Isoft HR Settings")

	p_start, _p_end = _cycle_period(term)
	if p_start > term:
		p_start = term
	days_worked = _settlement_working_days(employee, p_start, term)
	period_days = _settlement_working_days(employee, p_start, _p_end)

	contract = _active_contract(employee, term)
	fixed_term_short = False
	if contract and not cint(contract.get("is_open_ended")) and contract.get("end_date"):
		months = law.months_between(contract.get("start_date"), contract.get("end_date"))
		fixed_term_short = months <= 12

	return {
		"employee": employee, "employee_name": emp.get("employee_name"),
		"company": emp.get("company"),
		"date_of_joining": str(emp.get("date_of_joining") or ""),
		"termination_date": str(term),
		"contract": contract.get("name") if contract else None,
		"fixed_term_under_one_year": 1 if fixed_term_short else 0,
		"notice_required_days": cint(contract.get("notice_days")) if contract else 0,
		"notice_given_days": "",
		"base": flt(prof.base) if prof else 0.0,
		"technical_supplement": 0.0,
		"availability_supplement": 0.0,
		"food_allowance": flt(prof.food_allowance) if prof else 0.0,
		"transport_allowance": flt(prof.transport_allowance) if prof else 0.0,
		"salary_profile": prof.name if prof else None,
		"salary_period_start": str(p_start), "salary_period_end": str(term),
		"salary_days_worked": days_worked,
		"period_days": period_days,
		"salary_days": cint(s.get("settlement_salary_days")) or 26,
		"salary_method": "auto",
		"weekly_hours": flt(s.get("settlement_weekly_hours")),
		"working_days_per_week": flt(s.get("settlement_working_days_per_week")) or 5,
		"reason_key": "",
		"leave_vested": "Auto",
		"vested_untaken_days": _vested_untaken_leave_days(employee, term),
		"leave_days": cint(s.get("settlement_leave_days")) or law.ANNUAL_LEAVE_WORKING_DAYS,
		"leave_rate_method": "company_divisor",
		"leave_base_includes_allowances": cint(s.get("settlement_leave_includes_allowances")),
		"ferias_rate": flt(s.get("ferias_rate")), "natal_rate": flt(s.get("natal_rate")),
		"compensation_tax_position": (s.get("settlement_compensation_tax_position")
		                              or "verification_required"),
		"advance_outstanding": advances.outstanding_for(employee),
		"recover_advance": 1,
		"agreed_compensation": 0.0,
		"currency": frappe.db.get_single_value("Isoft HR Settings", "currency") or "AOA",
		"calc_version": fs.CALC_VERSION,
	}


@frappe.whitelist()
def settlement_defaults(employee, termination_date):
	"""Prefill a new Final Settlement and compute the first preview from it."""
	_guard(perms.PAYROLL_PREVIEW)
	data = _settlement_prefill(employee, termination_date)
	data["computed"] = settlement_preview(data)
	return data


def _engine_inputs(d):
	"""Map form fields onto the settlement engine's inputs."""
	vested = d.get("leave_vested") or "Auto"
	given = d.get("notice_given_days")
	return {
		"employee": d.get("employee"), "company": d.get("company"),
		"contract": d.get("contract"),
		"joining_date": d.get("date_of_joining") or None,
		"termination_date": d.get("termination_date"),
		"reason_key": d.get("reason_key") or None,
		"base": flt(d.get("base")),
		"technical_supplement": flt(d.get("technical_supplement")),
		"availability_supplement": flt(d.get("availability_supplement")),
		"food_allowance": flt(d.get("food_allowance")),
		"transport_allowance": flt(d.get("transport_allowance")),
		"salary_profile": d.get("salary_profile"),
		"period_start": d.get("salary_period_start"),
		"period_end": d.get("salary_period_end"),
		"period_days": flt(d.get("period_days")),
		"days_worked": flt(d.get("salary_days_worked")),
		"salary_method": d.get("salary_method") or "auto",
		"salary_divisor": cint(d.get("salary_days")) or 26,
		"weekly_hours": flt(d.get("weekly_hours")),
		"working_days_per_week": flt(d.get("working_days_per_week")) or 5,
		"vested_untaken_days": flt(d.get("vested_untaken_days")),
		"leave_vested": None if vested == "Auto" else (vested == "Yes"),
		"leave_divisor": cint(d.get("leave_days")) or law.ANNUAL_LEAVE_WORKING_DAYS,
		"leave_rate_method": d.get("leave_rate_method") or "company_divisor",
		"leave_base_includes_allowances": cint(d.get("leave_base_includes_allowances")),
		"fixed_term_under_one_year": cint(d.get("fixed_term_under_one_year")),
		"ferias_rate": flt(d.get("ferias_rate")), "natal_rate": flt(d.get("natal_rate")),
		"supplement_months_override": d.get("supplement_months_override") or None,
		"seniority_years_override": d.get("seniority_years_override") or None,
		"agreed_compensation": flt(d.get("agreed_compensation")),
		"compensation_tax_position": d.get("compensation_tax_position"),
		"notice_required_days": cint(d.get("notice_required_days")),
		"notice_given_days": None if given in (None, "") else cint(given),
		"employer_missed_renewal_notice": cint(d.get("employer_missed_renewal_notice")),
		"advance_outstanding": flt(d.get("advance_outstanding")),
		"recover_advance": cint(d.get("recover_advance", 1)),
	}


@frappe.whitelist()
def settlement_preview(data):
	"""Live-recompute the settlement from the (edited) form inputs."""
	_guard(perms.PAYROLL_PREVIEW)
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs

	d = json.loads(data) if isinstance(data, str) else data
	res = fs.compute(_engine_inputs(d))
	res["payment_deadline"] = fs.payment_deadline(d.get("termination_date"),
	                                              d.get("reason_key") or None)
	return res


@frappe.whitelist()
def settlement_legal_reference():
	"""The Lei 12/23 reference behind the Final Settlement, for the UI help panel."""
	_guard()
	return law.settlement_reference()


@frappe.whitelist()
def settlement_period_days(employee, start_date, end_date):
	"""Working days in a settlement salary period (for the UI to refresh when dates change)."""
	_guard()
	return _settlement_working_days(employee, start_date, end_date)


_SETTLEMENT_FIELDS = [
	"employee", "company", "date_of_joining", "termination_date", "reason", "reason_key",
	"notice_served", "contract", "fixed_term_under_one_year", "notice_required_days",
	"notice_given_days", "employer_missed_renewal_notice",
	"base", "technical_supplement", "availability_supplement", "food_allowance",
	"transport_allowance", "salary_period_start", "salary_period_end",
	"salary_days_worked", "period_days", "salary_days", "salary_method", "weekly_hours",
	"working_days_per_week", "months_worked", "supplement_months_override",
	"seniority_years_override", "override_reason", "ferias_rate", "natal_rate",
	"untaken_leave_days", "vested_untaken_days", "leave_vested", "leave_days",
	"leave_rate_method", "leave_base_includes_allowances", "agreed_compensation",
	"compensation_tax_position", "advance_outstanding", "recover_advance", "notes",
]

#: Derived fields returned alongside the inputs.
_SETTLEMENT_DERIVED = [
	"name", "employee_name", "monthly_remuneration", "salary_daily_rate", "period_salary",
	"vacation_monthly", "vacation_allowance", "christmas_monthly", "christmas_bonus",
	"leave_daily_rate", "untaken_leave_amount", "total_gross", "docstatus",
	"calc_version", "proportional_leave_days", "total_leave_days",
	"leave_remuneration_base", "vested_leave_amount", "proportional_leave_amount",
	"supplement_months", "seniority_years", "compensation_amount", "compensation_article",
	"compensation_status", "compensation_formula", "notice_amount", "inss_base_amount",
	"inss_amount", "irt_base_amount", "irt_amount", "advance_recovered",
	"advance_deferred", "total_deductions", "net_payable", "shortfall",
	"settlement_due_date", "payment_deadline_article", "workflow_status",
	"journal_entry", "payment_entry", "paid_on", "rejection_reason", "approved_by",
	"override_by", "override_at",
]


@frappe.whitelist()
def create_settlement(data):
	"""Create a draft Final Settlement from the dashboard form."""
	_guard(perms.PAYROLL_PREPARE)
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
	d = json.loads(data) if isinstance(data, str) else data
	doc = frappe.new_doc("Isoft Final Settlement")
	for f in _SETTLEMENT_FIELDS:
		if f in d and d.get(f) not in (None, ""):
			doc.set(f, d.get(f))
	doc.notice_served = cint(d.get("notice_served"))
	doc.recover_advance = cint(d.get("recover_advance", 1))
	doc.leave_base_includes_allowances = cint(d.get("leave_base_includes_allowances"))
	doc.calc_version = fs.CALC_VERSION
	doc.workflow_status = "Draft"
	doc.insert()  # controller.recompute() fills the derived amounts
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def update_settlement(name, data):
	"""Edit a draft (or rejected) settlement and recompute it."""
	_guard(perms.PAYROLL_PREPARE)
	d = json.loads(data) if isinstance(data, str) else data
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if doc.docstatus != 0 or doc.workflow_status not in ("Draft", "Rejected"):
		frappe.throw(_("Only a draft or rejected settlement can be edited. This one is {0}.")
		             .format(doc.workflow_status or _("submitted")))
	for f in _SETTLEMENT_FIELDS:
		if f in d:
			doc.set(f, d.get(f))
	doc.workflow_status = "Draft"
	doc.rejection_reason = None
	doc.save()
	return doc.name


@frappe.whitelist()
def list_settlements(company=None, employee=None):
	_guard()
	conds = ["1=1"]
	vals = []
	if company:
		conds.append("company=%s"); vals.append(company)
	if employee:
		conds.append("employee=%s"); vals.append(employee)
	rows = frappe.db.sql(
		"""select name, employee, employee_name, termination_date, reason, reason_key,
		total_gross, net_payable, workflow_status, calc_version, docstatus
		from `tabIsoft Final Settlement` where {} order by termination_date desc, modified desc
		limit 300""".format(" and ".join(conds)),
		vals, as_dict=True,
	)
	for r in rows:
		r["status"] = r.get("workflow_status") or {0: "Draft", 1: "Approved", 2: "Cancelled"}.get(
			cint(r.docstatus), "Draft")
		r["reason_label"] = _settlement_reason_label(r.get("reason_key"), r.get("reason"))
	return rows


def _settlement_reason_label(reason_key, legacy=None):
	spec = law.TERMINATION_REASONS.get(reason_key or "")
	if spec:
		return spec["label"]
	return legacy or ""


@frappe.whitelist()
def get_settlement(name):
	"""One settlement, with the reconciling breakdown that produced its amounts."""
	_guard()
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs

	doc = frappe.get_doc("Isoft Final Settlement", name)
	out = {f: doc.get(f) for f in _SETTLEMENT_FIELDS + _SETTLEMENT_DERIVED}
	for k in ("termination_date", "date_of_joining", "salary_period_start",
	          "salary_period_end", "settlement_due_date", "paid_on"):
		out[k] = str(out[k]) if out.get(k) else None
	out["status"] = doc.workflow_status or {0: "Draft", 1: "Approved", 2: "Cancelled"}.get(
		cint(doc.docstatus), "Draft")
	out["reason_label"] = _settlement_reason_label(doc.reason_key, doc.reason)
	out["currency"] = frappe.db.get_single_value("Isoft HR Settings", "currency") or "AOA"
	out["is_legacy"] = cint(doc.calc_version or 0) < fs.CALC_VERSION

	if out["is_legacy"]:
		# A pre-audit settlement is shown exactly as it was calculated, with the reason
		# it cannot be re-derived, rather than being quietly restated under new rules.
		out["lines"] = _legacy_lines(doc)
		out["flags"] = [{
			"code": "LEGACY", "level": "warning",
			"message": _("This settlement was calculated before the Lei n.º 12/23 audit "
			             "(calculation version {0}). Its stored amounts are shown "
			             "unchanged. Use Recalculate to restate it under the corrected "
			             "rules — that will change the amounts.").format(
				             cint(doc.calc_version or 1))}]
	else:
		res = fs.compute(doc.inputs())
		out["lines"] = res["lines"]
		out["flags"] = res["flags"]
		out["leave"] = res["leave"]
		out["supplements"] = res["supplements"]
	out["payment_deadline"] = fs.payment_deadline(
		doc.termination_date, doc.reason_key or law.LEGACY_REASON_MAP.get(doc.reason or ""))
	out["actions"] = _settlement_actions(doc)
	return out


def _legacy_lines(doc):
	"""Version-1 amounts rendered as lines, with no invented formulas.

	The old screen printed ``daily rate × days`` for the proportional salary even when
	the engine had paid a whole month, which is the arithmetic that did not add up. Here
	the calculation column is left empty rather than printing something false.
	"""
	rate, worked = flt(doc.salary_daily_rate), flt(doc.salary_days_worked)
	reconciles = abs(rate * worked - flt(doc.period_salary)) <= 0.01
	return [
		{"key": "salary", "section": "salary", "label": _("Proportional salary"),
		 "amount": flt(doc.period_salary), "sign": 1, "basis_kind": "company",
		 "formula": ("{0} × {1}".format(rate, worked) if reconciles else None),
		 "note": None if reconciles else _(
			 "The stored amount is a whole month's remuneration; the daily rate × days "
			 "shown by the old screen did not equal it, so it is not repeated here."),
		 "article": None, "status": "ok", "irt_taxable": True, "inss_base": True},
		{"key": "vacation_allowance", "section": "supplements",
		 "label": _("Vacation Allowance"), "amount": flt(doc.vacation_allowance), "sign": 1,
		 "basis_kind": "company",
		 "formula": "{0} × {1}".format(flt(doc.vacation_monthly), cint(doc.months_worked)),
		 "article": None, "status": "ok", "note": None, "irt_taxable": True, "inss_base": False},
		{"key": "christmas_bonus", "section": "supplements", "label": _("Christmas Bonus"),
		 "amount": flt(doc.christmas_bonus), "sign": 1, "basis_kind": "company",
		 "formula": "{0} × {1}".format(flt(doc.christmas_monthly), cint(doc.months_worked)),
		 "article": None, "status": "ok", "note": None, "irt_taxable": True, "inss_base": True},
		{"key": "leave", "section": "leave", "label": _("Untaken Annual Leave"),
		 "amount": flt(doc.untaken_leave_amount), "sign": 1, "basis_kind": "company",
		 "formula": "{0} × {1}".format(flt(doc.leave_daily_rate), flt(doc.untaken_leave_days)),
		 "article": None, "status": "ok", "note": None, "irt_taxable": True, "inss_base": True},
	]


# --------------------------------------------------------------------------- #
# Final Settlement workflow — reuses the payroll roles, invents none
# --------------------------------------------------------------------------- #
#: state -> (action label, permission action, next state)
_FS_TRANSITIONS = {
	"submit_for_approval": (("Draft", "Rejected"), perms.PAYROLL_SUBMIT_FOR_APPROVAL, "Pending Approval"),
	"approve": (("Pending Approval",), perms.PAYROLL_APPROVE, "Approved"),
	"reject": (("Pending Approval",), perms.PAYROLL_REJECT, "Rejected"),
	"post": (("Approved",), perms.PAYROLL_POST, "Posted"),
	"pay": (("Posted",), perms.PAYROLL_CONFIRM_PAYMENT, "Paid"),
}


def _settlement_actions(doc):
	"""Which transitions this user may perform on this settlement, right now."""
	state = doc.workflow_status or "Draft"
	out = []
	for action, (states, permission, _nxt) in _FS_TRANSITIONS.items():
		if state in states and perms.can(permission):
			out.append(action)
	if state in ("Draft", "Rejected") and perms.can(perms.PAYROLL_PREPARE):
		out.append("edit")
		out.append("recalculate")
	# Cancel and delete need the same authority. Offering a button that the server will
	# always refuse is its own kind of dishonesty, so neither is rendered without it.
	if perms.can(perms.PAYROLL_CANCEL):
		if state not in ("Paid", "Cancelled"):
			out.append("cancel")
		out.append("delete")
	return out


def _fs_transition(name, action, **kw):
	doc = frappe.get_doc("Isoft Final Settlement", name)
	states, permission, nxt = _FS_TRANSITIONS[action]
	_guard(permission, company=doc.company)
	state = doc.workflow_status or "Draft"
	if state not in states:
		frappe.throw(_("A settlement in {0} cannot be {1}. Expected {2}.").format(
			state, action.replace("_", " "), " or ".join(states)))
	# Nobody approves their own settlement.
	if action == "approve" and doc.owner == frappe.session.user 		and not _fs_self_approval_allowed():
		frappe.throw(_("The settlement was prepared by you. Approval must be given by "
		               "somebody else."))
	if action == "approve" and doc.employee 		and frappe.db.get_value("Employee", doc.employee, "user_id") == frappe.session.user:
		frappe.throw(_("You cannot approve your own final settlement."))
	doc.workflow_status = nxt
	for k, v in kw.items():
		doc.set(k, v)
	doc.save(ignore_permissions=True)
	return doc


def _fs_self_approval_allowed():
	from isoft_angola_hr.isoft_angola_hr.services import advances
	try:
		return not advances.requires_separate_approval()
	except Exception:
		return False


@frappe.whitelist()
def submit_settlement_for_approval(name):
	"""HR hands a completed settlement to an approver."""
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if cint(doc.calc_version or 0) >= fs.CALC_VERSION:
		res = fs.compute(doc.inputs())
		if res["blocking"]:
			frappe.throw(_("This settlement is not ready for approval: {0}").format(
				" ".join(f["message"] for f in res["blocking"])))
	_fs_transition(name, "submit_for_approval",
	               submitted_for_approval_by=frappe.session.user,
	               submitted_for_approval_at=now_datetime())
	return True


@frappe.whitelist()
def approve_settlement(name):
	_fs_transition(name, "approve", approved_by=frappe.session.user,
	               approved_at=now_datetime())
	return True


@frappe.whitelist()
def reject_settlement(name, reason=None):
	if not (reason or "").strip():
		frappe.throw(_("Record why the settlement is being rejected."))
	_fs_transition(name, "reject", rejection_reason=reason)
	return True


@frappe.whitelist()
def recalculate_settlement(name, confirm=0):
	"""Restate a settlement under the current engine — explicitly, never silently.

	A version-1 settlement keeps its stored amounts until somebody asks for this.
	"""
	_guard(perms.PAYROLL_PREPARE)
	from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if doc.docstatus != 0 or doc.workflow_status not in ("Draft", "Rejected"):
		frappe.throw(_("Only a draft or rejected settlement can be recalculated."))
	before = flt(doc.total_gross)
	if not cint(confirm):
		res = fs.compute(doc.inputs())
		return {"confirm_required": True, "before": before, "after": res["gross"],
		        "net": res["net"], "flags": res["flags"]}
	doc.calc_version = fs.CALC_VERSION
	doc.save()
	return {"confirm_required": False, "before": before, "after": flt(doc.total_gross),
	        "net": flt(doc.net_payable)}


@frappe.whitelist()
def submit_settlement(name):
	"""Finalise the settlement (submits it; the controller marks the employee as Left)."""
	_guard()
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if doc.docstatus == 0:
		doc.submit()
	frappe.db.commit()
	return True


@frappe.whitelist()
def cancel_settlement(name):
	_guard()
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if doc.docstatus == 1:
		doc.cancel()
	else:
		doc.workflow_status = "Cancelled"
		doc.save(ignore_permissions=True)
	frappe.db.commit()
	return True


@frappe.whitelist()
def delete_settlement(name):
	_guard(perms.PAYROLL_CANCEL)
	doc = frappe.get_doc("Isoft Final Settlement", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Isoft Final Settlement", name, force=1)
	frappe.db.commit()
	return True


@frappe.whitelist()
def export_settlement(name, file_format="pdf"):
	"""Export a Final Settlement as a formatted PDF or Excel.

	The export prints the SAME line set the screen shows — the engine's own formula
	strings and articles — so a settlement handed to an employee reconciles line by line
	with the one HR approved. Nothing is re-derived here.
	"""
	_guard()
	import base64

	s = get_settlement(name)
	currency = s.get("currency") or "AOA"

	def money(v):
		return frappe.utils.fmt_money(flt(v), currency=currency)

	lines = [ln for ln in (s.get("lines") or []) if flt(ln.get("amount")) or ln.get("status") != "ok"]
	fname = "FinalSettlement_{0}".format(name)
	law_line = "{0} — {1}".format(law.LAW, _("amounts and articles as calculated"))

	if file_format == "excel":
		from frappe.utils.xlsxutils import make_xlsx
		data = [
			[_("Final Settlement")],
			[law.LAW],
			[_("Employee"), s.get("employee_name")],
			[_("Date of Joining"), s.get("date_of_joining")],
			[_("Termination Date"), s.get("termination_date")],
			[_("Reason"), s.get("reason_label") or _("Not recorded")],
			[_("Seniority (artigo 311.º)"), cint(s.get("seniority_years"))],
			[_("Complete months (artigo 238.º)"), cint(s.get("supplement_months"))],
			[_("Settlement due"), s.get("settlement_due_date") or "",
			 s.get("payment_deadline_article") or ""],
			[],
			[_("Description"), _("Calculation"), _("Legal basis"), _("Amount")],
		]
		for ln in lines:
			data.append([
				ln.get("label"), ln.get("formula") or "",
				ln.get("article") or (_("Company Calculation Basis")
				                      if ln.get("basis_kind") == "company" else ""),
				flt(ln.get("amount"), 2) * (-1 if cint(ln.get("sign", 1)) < 0 else 1),
			])
		data += [
			[],
			[_("Gross settlement"), "", "", flt(s.get("total_gross"), 2)],
			[_("Total deductions"), "", "", flt(s.get("total_deductions"), 2)],
			[_("Net final settlement"), "", "", flt(s.get("net_payable"), 2)],
		]
		for f in (s.get("flags") or []):
			data.append([f.get("level", "").upper(), f.get("message")])
		content = make_xlsx(data, "Final Settlement").getvalue()
		mime, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
	else:
		from frappe.utils.pdf import get_pdf
		esc = frappe.utils.escape_html

		def basis_cell(ln):
			bits = []
			if ln.get("article"):
				bits.append("<span style='color:#3730a3;'>{0}</span>".format(esc(ln["article"])))
			if ln.get("rate_basis_kind") == "company" or (
				ln.get("basis_kind") == "company" and not ln.get("article")):
				bits.append("<span style='color:#9a3412;'>{0}</span>".format(
					esc(_("Company Calculation Basis"))))
			if ln.get("status") == "legal_input_required":
				bits.append("<b style='color:#991b1b;'>{0}</b>".format(esc(_("LEGAL INPUT REQUIRED"))))
			elif ln.get("status") == "verify":
				bits.append("<b style='color:#854d0e;'>{0}</b>".format(esc(law.REVIEW_MARKER)))
			return "<br>".join(bits)

		body = "".join(
			"<tr><td>{0}{1}</td><td style='font-family:monospace;font-size:10px;'>{2}</td>"
			"<td style='font-size:9px;'>{3}</td>"
			"<td style='text-align:right;'>{4}{5}</td></tr>".format(
				esc(ln.get("label") or ""),
				("<div style='color:#666;font-size:9px;'>%s</div>" % esc(ln["note"]))
				if ln.get("note") else "",
				esc(ln.get("formula") or "\u2014"),
				basis_cell(ln),
				"\u2212" if cint(ln.get("sign", 1)) < 0 else "",
				money(ln.get("amount")))
			for ln in lines)
		flags = "".join(
			"<li><b>{0}</b> {1}</li>".format(esc((f.get("level") or "").upper()),
			                                 esc(f.get("message") or ""))
			for f in (s.get("flags") or []))
		notes = esc(s.get("notes") or "")
		html = """
		<div style="font-family:Arial,sans-serif;font-size:11px;color:#222;">
			<h2 style="margin:0 0 2px;">{title}</h2>
			<div style="color:#666;font-size:10px;margin-bottom:10px;">{law}</div>
			<table style="margin-bottom:12px;">
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_emp}</td><td><b>{emp}</b></td></tr>
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_hire}</td><td>{hire}</td></tr>
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_term}</td><td>{term}</td></tr>
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_reason}</td><td>{reason}</td></tr>
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_service}</td><td>{service}</td></tr>
				<tr><td style="padding:2px 16px 2px 0;color:#666;">{l_due}</td><td>{due}</td></tr>
			</table>
			<table style="width:100%;border-collapse:collapse;" border="1" cellpadding="5">
				<thead style="background:#f0f4fa;"><tr>
					<th style="text-align:left;">{h_desc}</th><th style="text-align:left;">{h_calc}</th>
					<th style="text-align:left;">{h_basis}</th>
					<th style="text-align:right;">{h_amt}</th></tr></thead>
				<tbody>{body}</tbody>
				<tfoot style="background:#f0f4fa;">
					<tr><td colspan="3">{gross_lbl}</td><td style="text-align:right;">{gross}</td></tr>
					<tr><td colspan="3">{ded_lbl}</td><td style="text-align:right;">{ded}</td></tr>
					<tr><td colspan="3"><b>{net_lbl}</b></td><td style="text-align:right;"><b>{net}</b></td></tr>
				</tfoot>
			</table>
			{notes_block}
			{flags_block}
		</div>""".format(
			title=_("Final Settlement"), law=esc(law_line),
			l_emp=_("Employee") + ":", emp=esc(s.get("employee_name") or ""),
			l_hire=_("Date of Joining") + ":", hire=esc(s.get("date_of_joining") or ""),
			l_term=_("Termination Date") + ":", term=esc(s.get("termination_date") or ""),
			l_reason=_("Reason") + ":", reason=esc(s.get("reason_label") or _("Not recorded")),
			l_service=_("Service") + ":",
			service="{0} {1} \u00b7 {2} {3}".format(
				cint(s.get("seniority_years")), _("years (artigo 311.º)"),
				cint(s.get("supplement_months")), _("complete months (artigo 238.º)")),
			l_due=_("Settlement due") + ":",
			due="{0} {1}".format(esc(s.get("settlement_due_date") or "\u2014"),
			                     esc(s.get("payment_deadline_article") or "")),
			h_desc=_("Description"), h_calc=_("Calculation"), h_basis=_("Legal basis"),
			h_amt=_("Amount"), body=body,
			gross_lbl=_("Gross settlement"), gross=money(s.get("total_gross")),
			ded_lbl=_("Total deductions"), ded=money(s.get("total_deductions")),
			net_lbl=_("Net final settlement"), net=money(s.get("net_payable")),
			notes_block=("<p style='margin-top:10px;'><b>{0}:</b> {1}</p>".format(_("Notes"), notes)
			             if notes else ""),
			flags_block=("<div style='margin-top:12px;font-size:9px;color:#555;'>"
			             "<b>{0}</b><ul>{1}</ul></div>".format(_("Notes on this calculation"), flags)
			             if flags else ""),
		)
		content = get_pdf(html)
		mime, ext = "application/pdf", "pdf"

	return {"filename": "{0}.{1}".format(fname, ext), "mime": mime,
	        "content": base64.b64encode(content).decode()}


# --------------------------------------------------------------------------- #
# IRT Table (managed as the single default table) + Settings
# --------------------------------------------------------------------------- #
def _default_irt_name():
	return frappe.db.get_single_value("Isoft HR Settings", "default_irt_table") or "Tabela IRT (Angola)"


@frappe.whitelist()
def get_irt_table():
	_guard(perms.STATUTORY_READ)
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
def save_irt_table(brackets, effective_from=None, title=None):
	"""Save the IRT brackets.

	When ``effective_from`` names a date the current table does not already use, a NEW
	effective-dated IRT Table is created instead of overwriting the existing one, so a
	statutory change never rewrites the rules that produced past payroll. Editing the
	current table in place stays possible only while it has not been used by submitted
	payroll (enforced by IRTTable.validate).
	"""
	_guard(perms.STATUTORY_WRITE)
	rows = json.loads(brackets) if isinstance(brackets, str) else brackets
	current = frappe.get_doc("IRT Table", _default_irt_name())

	new_version = bool(effective_from) and getdate(effective_from) != getdate(current.effective_from)
	if new_version:
		doc = frappe.new_doc("IRT Table")
		doc.title = title or "{0} ({1})".format(_("Tabela IRT"), getdate(effective_from).year)
		doc.company = current.company
		doc.currency = current.currency
		doc.effective_from = getdate(effective_from)
	else:
		doc = current

	doc.set("brackets", [])
	for r in rows:
		doc.append("brackets", {
			"from_amount": flt(r.get("from_amount")), "to_amount": flt(r.get("to_amount")),
			"excess_over": flt(r.get("excess_over")), "rate": flt(r.get("rate")),
			"parcela_fixa": flt(r.get("parcela_fixa")),
		})
	doc.save()

	if new_version:
		# New payroll should resolve the new table by effective date; the default pointer
		# follows it so the dashboard edits the current one next time.
		frappe.db.set_value("Isoft HR Settings", None, "default_irt_table", doc.name)
		frappe.msgprint(
			_("Created IRT Table {0} effective {1}. Payroll before that date keeps using {2}.").format(
				frappe.bold(doc.name), doc.effective_from, current.name),
			indicator="green",
		)
	return {"name": doc.name, "brackets": len(doc.brackets), "new_version": 1 if new_version else 0}


@frappe.whitelist()
def get_settings(company=None):
	_guard()
	from isoft_angola_hr.isoft_angola_hr.payroll import engine

	s = frappe.get_single("Isoft HR Settings")
	out = {f: s.get(f) for f in [
		"default_company", "default_irt_table", "currency", "payroll_cycle_start_day",
		"ss_employee_rate", "ss_employer_rate",
		"food_allowance_exemption", "transport_allowance_exemption", "standard_daily_hours",
		"overtime_multiplier", "working_days_basis", "standard_working_days",
		"ferias_rate", "natal_rate", "natal_payment_month",
		"settlement_salary_days", "settlement_leave_days",
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
	_guard(perms.SETTINGS_WRITE)
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
	_guard(perms.EMPLOYEE_WRITE)
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
		"custom_iban": d.get("custom_iban") or None,
		"custom_insurance": d.get("custom_insurance") or None,
		"default_shift": d.get("default_shift") or None,
		"status": "Active",
	})
	emp.insert()
	frappe.db.commit()
	return {"name": emp.name, "employee_name": emp.employee_name}


@frappe.whitelist()
def update_employee(name, data):
	"""Update the Angola-HR editable fields of an existing Employee from the dashboard."""
	_guard(perms.EMPLOYEE_WRITE)
	d = json.loads(data) if isinstance(data, str) else data
	emp = frappe.get_doc("Employee", name)
	for f in ("designation", "department", "custom_nif", "custom_inss_number", "custom_iban",
	          "custom_insurance", "default_shift"):
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


# --------------------------------------------------------------------------- #
# Accounting vouchers — read only
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def payroll_voucher(name):
	"""One Journal Entry, as Finance needs to read it, without leaving Angola HR.

	Read only. It creates nothing, changes nothing and holds no accounting logic — the
	accrual and the payment are still built exclusively by :func:`make_journal_entry`
	and :func:`make_payment_entry`. This exists so that "View Journal Entry" can open a
	panel in this application instead of routing the user into the ERPNext Accounting
	module to confirm what was posted.

	The docstatus is returned as a word rather than a number because "Draft" and
	"Submitted" are the distinction that matters here: only a submitted voucher has
	reached the general ledger, and only then does a salary slip read as Posted or Paid.
	"""
	_guard(perms.PAYROLL_READ)
	doc = frappe.get_doc("Journal Entry", name)
	perms.require_company(doc.company)

	rows = []
	for a in doc.accounts:
		rows.append({
			"account": a.account,
			"party_type": a.get("party_type"),
			"party": a.get("party"),
			"cost_center": a.get("cost_center"),
			"debit": flt(a.get("debit_in_account_currency")),
			"credit": flt(a.get("credit_in_account_currency")),
		})

	# Proof that the ledger actually carries it: the GL rows are counted from the
	# ledger itself, not inferred from the voucher's docstatus.
	gl_rows = frappe.db.count("GL Entry", {"voucher_type": "Journal Entry",
	                                       "voucher_no": doc.name, "is_cancelled": 0})

	return {
		"name": doc.name,
		"company": doc.company,
		"voucher_type": doc.voucher_type,
		"posting_date": str(doc.posting_date) if doc.posting_date else None,
		"docstatus": cint(doc.docstatus),
		"status": {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(cint(doc.docstatus), "Draft"),
		"total_debit": flt(doc.total_debit),
		"total_credit": flt(doc.total_credit),
		"balanced": abs(flt(doc.total_debit) - flt(doc.total_credit)) < 0.005,
		"remark": doc.get("user_remark") or doc.get("remark"),
		"cheque_no": doc.get("cheque_no"),
		"cheque_date": str(doc.get("cheque_date")) if doc.get("cheque_date") else None,
		"gl_entries": gl_rows,
		"in_ledger": cint(doc.docstatus) == 1 and gl_rows > 0,
		"accounts": rows,
		"desk_url": "/app/journal-entry/" + doc.name,
	}


@frappe.whitelist()
def payroll_capabilities():
	"""What this user may do with payroll, according to the permission model itself.

	The client uses this to decide what to RENDER — not what is permitted. Every
	action re-checks on the server when it is invoked, so this cannot grant anything;
	it only stops the screen offering, or silently attempting, work the user will be
	refused. The Payroll screen used to resolve the payroll period on load through an
	endpoint that requires `payroll.preview`, which a Payroll Finance Approver does not
	hold — so Finance opened the screen into a permission error and could not reach the
	payroll run they were supposed to post.
	"""
	_guard(perms.PAYROLL_READ)
	keys = ("PAYROLL_PREVIEW", "PAYROLL_PREPARE", "PAYROLL_CALCULATE",
	        "PAYROLL_SUBMIT_FOR_APPROVAL", "PAYROLL_APPROVE", "PAYROLL_POST",
	        "PAYROLL_EXPORT_BANK", "PAYROLL_CONFIRM_PAYMENT", "PAYROLL_CANCEL",
	        "PAYROLL_CLOSE")
	return {k[len("PAYROLL_"):].lower(): bool(perms.can(getattr(perms, k))) for k in keys}
