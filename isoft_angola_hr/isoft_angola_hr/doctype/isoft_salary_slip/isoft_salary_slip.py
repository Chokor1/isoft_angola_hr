# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
	get_active_profile,
)
from isoft_angola_hr.isoft_angola_hr.payroll import engine


class IsoftSalarySlip(Document):
	def validate(self):
		self.resolve_profile()
		self.set_working_days()
		self.compute()

	def on_cancel(self):
		# A posted accrual / payment must be removed before the slip can be cancelled,
		# so the ledger and the slip never drift apart.
		if self.get("journal_entry") and frappe.db.exists("Journal Entry", self.journal_entry):
			frappe.throw(
				_("Cannot cancel: the accrual Journal Entry {0} exists. Delete it first.").format(
					frappe.bold(self.journal_entry)
				)
			)
		if self.get("payment_entry") and frappe.db.exists("Journal Entry", self.payment_entry):
			frappe.throw(
				_("Cannot cancel: the Payment Entry {0} exists (slip is paid). Delete it first.").format(
					frappe.bold(self.payment_entry)
				)
			)

	def resolve_profile(self):
		if not self.salary_profile:
			prof = get_active_profile(self.employee, self.end_date or self.posting_date)
			if not prof:
				frappe.throw(
					_("No Isoft Salary Profile found for {0} effective on or before {1}.").format(
						frappe.bold(self.employee), self.end_date
					)
				)
			self.salary_profile = prof.name

	def set_working_days(self):
		twd, pay_days = compute_working_days(
			self.employee, self.start_date, self.end_date,
			validate_attendance=self.validate_attendance,
			based_on_timesheet=self.based_on_timesheet,
		)
		self.total_working_days = twd
		if not self.payment_days:
			self.payment_days = pay_days

	def compute(self):
		profile = frappe.get_doc("Isoft Salary Profile", self.salary_profile)
		settings = engine.get_settings()
		if not profile.irt_table:
			profile.irt_table = settings.default_irt_table
		self.irt_table = profile.irt_table

		# Auto-fill the December Natal default on a brand-new slip when it wasn't set
		# (e.g. a slip created directly, not via the payroll preview). HR can still edit it.
		if self.is_new() and self.subsidio_natal is None:
			self.subsidio_natal = engine.default_natal(
				profile.base, settings.natal_rate,
				frappe.db.get_value("Employee", self.employee, "date_of_joining"), self.end_date,
				settings.get("natal_payment_month"),
			)

		inputs = {
			"productivity_bonus": flt(self.productivity_bonus),
			"overtime_amount": flt(self.overtime_amount),
			"adiantamento": flt(self.adiantamento),
			"ferias_amount": flt(self.subsidio_ferias),
			"natal_amount": flt(self.subsidio_natal),
			"payment_days": flt(self.payment_days),
			"total_working_days": flt(self.total_working_days),
		}
		res = engine.compute_slip(profile, inputs, settings=settings, on_date=self.end_date)

		self.set("earnings", [])
		for e in res["earnings"]:
			self.append("earnings", e)
		self.set("deductions", [])
		for d in res["deductions"]:
			self.append("deductions", d)

		self.taxable_income = res["taxable_income"]
		self.gross_pay = res["gross_pay"]
		self.total_deduction = res["total_deduction"]
		self.net_pay = res["net_pay"]


def get_holiday_dates(employee, start_date, end_date):
	"""Set of holiday dates for the employee's holiday list within the period."""
	from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return set()
	rows = frappe.db.sql(
		"""select holiday_date from `tabHoliday`
		where parent=%s and parenttype='Holiday List'
		and holiday_date between %s and %s""",
		(holiday_list, getdate(start_date), getdate(end_date)),
	)
	return {getdate(r[0]) for r in rows}


def get_holiday_count(employee, start_date, end_date):
	return len(get_holiday_dates(employee, start_date, end_date))


# Normal daily hours per weekday (Angola labour calendar): Mon–Fri 8h, Sat 4h, Sun off.
# Python weekday(): Mon=0 … Sat=5, Sun=6.
def normal_hours_for(day):
	wd = getdate(day).weekday()
	if wd == 6:
		return 0.0   # Sunday — not a working day
	if wd == 5:
		return 4.0   # Saturday — half journey
	return 8.0       # Monday–Friday


def compute_working_days(employee, start_date, end_date, validate_attendance=0, based_on_timesheet=0):
	"""Return (total_working_days, payment_days).

	total_working_days (TWD) depends on the "Working Days Basis" setting:
	    * Standard (Fixed): a fixed number from Settings (e.g. 30/26).
	    * Auto (Holiday List): days in the period that are NOT Sundays and NOT holidays
	      (Angola labour calendar — Mon–Sat are working days, ~26/period).

	payment_days (paid days):
	    * based_on_timesheet: logged timesheet hours / standard daily hours (capped at TWD).
	    * validate_attendance: TWD minus Absent, minus 0.5×Half-Day, minus unpaid-leave days
	      (Attendance "On Leave" whose Leave Type is Leave-Without-Pay). Paid leave is not deducted.
	    * otherwise: full TWD.
	"""
	start, end = getdate(start_date), getdate(end_date)
	settings = frappe.get_cached_doc("Isoft HR Settings")
	basis = settings.get("working_days_basis") or "Auto (Holiday List)"
	if basis == "Standard (Fixed)":
		twd = flt(settings.get("standard_working_days")) or 30.0
	else:
		holidays = get_holiday_dates(employee, start, end)
		twd = 0
		d = start
		while d <= end:
			if d.weekday() != 6 and d not in holidays:  # not Sunday, not holiday
				twd += 1
			d = add_days(d, 1)

	if cint(based_on_timesheet):
		std_daily = flt(settings.get("standard_daily_hours")) or 8
		hours = frappe.db.sql(
			"""select coalesce(sum(total_hours),0) from `tabTimesheet`
			where employee=%s and docstatus=1 and start_date>=%s and end_date<=%s""",
			(employee, start, end),
		)[0][0]
		return twd, min(twd, flt(hours) / std_daily)

	if cint(validate_attendance):
		# Hours-aware paid days: each day's shortfall is (normal_hours − worked)/normal_hours,
		# so proration reproduces HR's deduction formulas exactly:
		#   full day        -> 1 day          => daily_salary × days
		#   partial (Mon–Fri) -> hrs/8 of a day => (daily_salary ÷ 8) × missing hours
		#   partial (Sat)     -> hrs/4 of a day => (daily_salary ÷ 4) × missing hours
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence.isoft_attendance_occurrence import (
			occurrence_missing_by_date,
		)

		rows = frappe.db.sql(
			"""select attendance_date, status, leave_type, working_hours from `tabAttendance`
			where employee=%s and docstatus=1 and attendance_date between %s and %s""",
			(employee, start, end), as_dict=True,
		)
		lwp_types = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))
		att = {}  # date -> missing days from Attendance
		for r in rows:
			d = getdate(r.attendance_date)
			nh = normal_hours_for(d)
			if nh <= 0:
				continue  # Sunday / non-working day
			if r.status == "Absent":
				m = 1.0
			elif r.status == "Half Day":
				m = 0.5
			elif r.status == "On Leave":
				m = 1.0 if r.leave_type in lwp_types else 0.0
			elif r.status in ("Present", "Work From Home") and r.working_hours and flt(r.working_hours) < nh:
				m = (nh - flt(r.working_hours)) / nh
			else:
				m = 0.0
			att[d] = max(att.get(d, 0.0), m)

		# Attendance Occurrences are authoritative for the days they cover: a Justified/Pending
		# occurrence means no deduction; an Unjustified one deducts its missing portion.
		covered, occ_deduct = occurrence_missing_by_date(employee, start, end)
		missing = 0.0
		for d in set(att) | set(covered):
			missing += occ_deduct.get(d, 0.0) if d in covered else att.get(d, 0.0)
		return twd, max(0, twd - missing)

	return twd, twd


def attendance_overtime_amount(employee, base, working_days, start_date, end_date, multiplier):
	"""Overtime pay from logged Attendance overtime hours:
	Σ_day (daily_salary ÷ normal_hours(day)) × multiplier × overtime_hours(day),
	where daily_salary = base ÷ period working days. Rest days fall back to an 8h divisor."""
	twd = flt(working_days)
	if not twd:
		return 0.0
	daily = flt(base) / twd
	mult = flt(multiplier) or 2.0
	rows = frappe.db.sql(
		"""select attendance_date, custom_overtime_hours from `tabAttendance`
		where employee=%s and docstatus=1 and ifnull(custom_overtime_hours,0) > 0
		and attendance_date between %s and %s""",
		(employee, getdate(start_date), getdate(end_date)), as_dict=True,
	)
	total = 0.0
	for r in rows:
		nh = normal_hours_for(r.attendance_date) or 8.0
		total += (daily / nh) * mult * flt(r.custom_overtime_hours)
	return flt(total, 2)
