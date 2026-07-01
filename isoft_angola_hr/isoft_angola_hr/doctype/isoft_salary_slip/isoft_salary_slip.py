# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, flt, getdate

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

		inputs = {
			"productivity_bonus": flt(self.productivity_bonus),
			"overtime_amount": flt(self.overtime_amount),
			"adiantamento": flt(self.adiantamento),
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


def get_holiday_count(employee, start_date, end_date):
	from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return 0
	return frappe.db.sql(
		"""select count(*) from `tabHoliday`
		where parent=%s and parenttype='Holiday List'
		and holiday_date between %s and %s""",
		(holiday_list, getdate(start_date), getdate(end_date)),
	)[0][0]


def compute_working_days(employee, start_date, end_date, validate_attendance=0, based_on_timesheet=0):
	"""Return (total_working_days, payment_days).

	- total_working_days = calendar days in period minus holidays.
	- payment_days depends on the mode:
	    * based_on_timesheet: logged timesheet hours / standard daily hours (capped at total).
	    * validate_attendance: total minus Absent attendance records.
	    * otherwise: full total working days.
	"""
	start, end = getdate(start_date), getdate(end_date)
	total_days = date_diff(end, start) + 1
	twd = max(0, total_days - get_holiday_count(employee, start, end))

	if cint(based_on_timesheet):
		std_daily = flt(frappe.db.get_single_value("Isoft HR Settings", "standard_daily_hours")) or 8
		hours = frappe.db.sql(
			"""select coalesce(sum(total_hours),0) from `tabTimesheet`
			where employee=%s and docstatus=1 and start_date>=%s and end_date<=%s""",
			(employee, start, end),
		)[0][0]
		return twd, min(twd, flt(hours) / std_daily)

	if cint(validate_attendance):
		absent = frappe.db.sql(
			"""select count(*) from `tabAttendance` where employee=%s and docstatus=1
			and status='Absent' and attendance_date between %s and %s""",
			(employee, start, end),
		)[0][0]
		return twd, max(0, twd - flt(absent))

	return twd, twd
