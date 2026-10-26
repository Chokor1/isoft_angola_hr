# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Paid/Total Days counts DAYS: a full scheduled day is 1, a short day is 0.5.

Two wrong answers preceded this one, and both are pinned here so neither can return:

  * ``shift_hours / standard_daily_hours`` made a 9.5-hour ITEC weekday worth 1.1875 days
    and reported a month as **28.625** — hours wearing the label "Days". It also docked an
    employee marked Present with 8 hours against her 9.5-hour shift by
    (9.5-8)/9.5 x 1.1875, paying 99,333.33 instead of 100,000.00.
  * counting every scheduled date as exactly 1 removed the inflation but paid a 4-hour
    Saturday as a whole day, reporting **27**.

The rule the payroll runs on:

    full scheduled weekday (9.5h or 8h alike) = 1.0
    short scheduled day     (the 4h Saturday) = 0.5
    non-working day                           = 0

so 22 weekdays + 5 Saturdays = **24.5**. What makes a day "full" is the shift's OWN longest
scheduled day, never ``standard_daily_hours`` — that setting is unset on this site and has
no business deciding how many days a month contains.
"""

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
	compute_working_days,
	counts_in_days,
	day_value,
	eligible_range,
	shift_day_units,
	shift_day_weight,
	shift_full_day_hours,
)
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

#: 23 Jul - 22 Aug 2026: 22 weekdays, 5 Saturdays, 4 Sundays, no holidays.
START, END = "2026-07-23", "2026-08-22"
PERIOD_DAYS = 24.5              # 22 x 1.0  +  5 x 0.5

MONDAY = getdate("2026-07-27")
SATURDAY = getdate("2026-08-01")
SUNDAY = getdate("2026-08-02")

SHIFT_95 = PREFIX + " 9.5h"     # 08:00-17:30 Mon-Fri, 09:00-13:00 Sat - the ITEC shape
SHIFT_8 = PREFIX + " 8h"        # 09:00-17:00 Mon-Fri, 09:00-13:00 Sat

#: Captured ONCE for the module: a class that restored its own "before" value could leave
#: the site configured differently from how it was found.
_ORIGINAL_BASIS = []


class CalendarDaysFixture(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not _ORIGINAL_BASIS:
			_ORIGINAL_BASIS.append(frappe.db.get_single_value(
				"Isoft HR Settings", "working_days_basis"))
		cls._set_basis("Calendar Working Days")
		cls._shift(SHIFT_95, "08:00:00", "17:30:00")
		cls._shift(SHIFT_8, "09:00:00", "17:00:00")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._set_basis(_ORIGINAL_BASIS[0])
		super().tearDownClass()

	def tearDown(self):
		"""Tolerate the commits these tests need.

		Changing the basis and submitting Attendance both commit, as they must in
		production, and a commit discards the fixture's per-test savepoint. A plain
		rollback is then the correct close-out; the class-level cleanup removes whatever
		the commit made durable.
		"""
		try:
			super().tearDown()
		except Exception:
			frappe.db.rollback()

	@classmethod
	def _set_basis(cls, basis):
		frappe.db.set_single_value("Isoft HR Settings", "working_days_basis", basis)
		frappe.db.commit()
		frappe.clear_cache()

	@classmethod
	def _shift(cls, name, start, end):
		if frappe.db.exists("Shift Type", name):
			return
		doc = frappe.get_doc({
			"doctype": "Shift Type", "__newname": name,
			"start_time": start, "end_time": end,
			"weekday_hours": [
				{"weekday": d, "is_working_day": 1, "start_time": start, "end_time": end}
				for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
			] + [
				{"weekday": "Saturday", "is_working_day": 1,
				 "start_time": "09:00:00", "end_time": "13:00:00"},
				{"weekday": "Sunday", "is_working_day": 0},
			],
		}).insert(ignore_permissions=True)
		cls._created.append(("Shift Type", doc.name))
		frappe.db.commit()

	def on_shift(self, shift):
		frappe.db.set_value("Employee", self.employee.name, "default_shift", shift,
		                    update_modified=False)
		self.addCleanup(frappe.db.set_value, "Employee", self.employee.name,
		                "default_shift", None, update_modified=False)


class TestOneDayIsOneDay(CalendarDaysFixture):
	"""(1)(2)(3) what a single date is worth."""

	def test_a_nine_and_a_half_hour_weekday_is_one_day(self):
		self.assertEqual(shift_day_units(SHIFT_95, MONDAY), 1.0)
		self.assertEqual(day_value(SHIFT_95, MONDAY, 8, "Calendar Working Days"), 1.0)

	def test_an_eight_hour_weekday_is_also_one_day(self):
		"""Shift length decides FULL vs SHORT, never how much more than one day."""
		self.assertEqual(shift_day_units(SHIFT_8, MONDAY), 1.0)
		self.assertEqual(shift_day_units(None, MONDAY), 1.0)

	def test_a_four_hour_saturday_is_half_a_day(self):
		self.assertEqual(shift_day_units(SHIFT_95, SATURDAY), 0.5)
		self.assertEqual(shift_day_units(SHIFT_8, SATURDAY), 0.5)
		self.assertEqual(shift_day_units(None, SATURDAY), 0.5)
		self.assertEqual(day_value(SHIFT_95, SATURDAY, 8, "Calendar Working Days"), 0.5)

	def test_a_non_working_sunday_is_nothing(self):
		self.assertEqual(shift_day_units(SHIFT_95, SUNDAY), 0.0)
		self.assertEqual(day_value(SHIFT_95, SUNDAY, 8, "Calendar Working Days"), 0.0)

	def test_a_full_day_is_the_shifts_own_longest_day(self):
		self.assertEqual(shift_full_day_hours(SHIFT_95), 9.5)
		self.assertEqual(shift_full_day_hours(SHIFT_8), 8.0)
		self.assertEqual(shift_full_day_hours(None), 8.0)

	def test_standard_daily_hours_cannot_change_a_day(self):
		"""The canary for the first wrong answer: 9.5/8 = 1.1875 must never return."""
		for std in (0, 8, 9.5, 12):
			self.assertEqual(day_value(SHIFT_95, MONDAY, std, "Calendar Working Days"), 1.0,
			                 "standard_daily_hours=%s changed a working day" % std)
			self.assertEqual(day_value(SHIFT_95, SATURDAY, std, "Calendar Working Days"), 0.5)

	def test_the_hour_equivalent_basis_is_still_a_separate_labelled_unit(self):
		self.assertFalse(counts_in_days("Auto (Holiday List)"))
		self.assertAlmostEqual(shift_day_weight(SHIFT_95, MONDAY, 8), 1.1875, places=4)


class TestThePeriodTotal(CalendarDaysFixture):
	"""(4) 22 weekdays + 5 Saturdays = 24.5, whatever the shift."""

	def test_the_itec_shift_totals_twenty_four_and_a_half(self):
		self.on_shift(SHIFT_95)
		twd, paid = compute_working_days(self.employee.name, START, END)
		self.assertEqual(twd, PERIOD_DAYS)
		self.assertEqual(paid, PERIOD_DAYS)

	def test_neither_previous_wrong_answer_returns(self):
		self.on_shift(SHIFT_95)
		twd, _p = compute_working_days(self.employee.name, START, END)
		self.assertNotAlmostEqual(twd, 28.625, places=3, msg="hour-equivalents are back")
		self.assertNotAlmostEqual(twd, 27.0, places=3, msg="Saturday is being paid whole")

	def test_every_shift_shape_agrees(self):
		seen = {}
		for shift in (SHIFT_95, SHIFT_8, None):
			frappe.db.set_value("Employee", self.employee.name, "default_shift", shift,
			                    update_modified=False)
			seen[shift or "no shift"] = compute_working_days(
				self.employee.name, START, END)[0]
		frappe.db.set_value("Employee", self.employee.name, "default_shift", None,
		                    update_modified=False)
		self.assertEqual(set(seen.values()), {PERIOD_DAYS},
		                 "shift length changed the day count: %s" % seen)


class TestAttendanceDeductsInDays(CalendarDaysFixture):
	"""(5)(6)(7)(8) what attendance does to the total."""

	def _mark(self, day, status, hours=None, overtime=None):
		doc = frappe.get_doc({
			"doctype": "Attendance", "employee": self.employee.name,
			"attendance_date": day, "status": status, "company": self.company,
		})
		if hours is not None:
			doc.working_hours = hours
		doc.insert(ignore_permissions=True)
		if overtime is not None and frappe.get_meta("Attendance").get_field(
				"custom_overtime_hours"):
			doc.db_set("custom_overtime_hours", overtime, update_modified=False)
		doc.submit()
		self._created.append(("Attendance", doc.name))
		self.addCleanup(self._cancel, doc.name)
		return doc.name

	@staticmethod
	def _cancel(name):
		frappe.set_user("Administrator")
		if frappe.db.get_value("Attendance", name, "docstatus") == 1:
			frappe.get_doc("Attendance", name).cancel()
			frappe.db.commit()

	def test_present_with_eight_hours_on_a_nine_and_a_half_hour_shift_loses_nothing(self):
		"""Teresa's case. She was docked 0.1875 of a day and 666.67 of her salary."""
		self.on_shift(SHIFT_95)
		self._mark("2026-07-27", "Present", hours=8)
		twd, paid = compute_working_days(self.employee.name, START, END,
		                                 validate_attendance=1)
		self.assertEqual(twd, PERIOD_DAYS)
		self.assertEqual(paid, PERIOD_DAYS, "a Present day was docked for its hours")

	def test_one_absent_weekday_costs_one_day(self):
		self.on_shift(SHIFT_95)
		self._mark("2026-07-27", "Absent")          # a Monday
		twd, paid = compute_working_days(self.employee.name, START, END,
		                                 validate_attendance=1)
		self.assertEqual(twd - paid, 1.0)
		self.assertEqual(paid, PERIOD_DAYS - 1.0)

	def test_one_absent_saturday_is_one_absence(self):
		# the short Saturday counts 0.5 in the total; missing it is one absence (HR's
		# "Absence and Late Arrival Deductions", October 2026)
		self.on_shift(SHIFT_95)
		self._mark("2026-08-01", "Absent")          # a Saturday
		twd, paid = compute_working_days(self.employee.name, START, END,
		                                 validate_attendance=1)
		self.assertEqual(twd - paid, 1.0)
		self.assertEqual(paid, PERIOD_DAYS - 1.0)
	def test_a_half_day_on_a_weekday_costs_half_a_day(self):
		self.on_shift(SHIFT_95)
		self._mark("2026-07-28", "Half Day")
		twd, paid = compute_working_days(self.employee.name, START, END,
		                                 validate_attendance=1)
		self.assertEqual(twd - paid, 0.5)

	def test_overtime_changes_neither_figure(self):
		self.on_shift(SHIFT_95)
		self._mark("2026-07-29", "Present", hours=9.5, overtime=4)
		twd, paid = compute_working_days(self.employee.name, START, END,
		                                 validate_attendance=1)
		self.assertEqual(twd, PERIOD_DAYS, "overtime inflated total days")
		self.assertEqual(paid, PERIOD_DAYS, "overtime inflated paid days")


class TestOnlyEmployedDatesAreCounted(CalendarDaysFixture):
	"""(9) joining and leaving still clamp the period."""

	def _employed(self, joining=None, relieving=None):
		if joining:
			frappe.db.set_value("Employee", self.employee.name, "date_of_joining",
			                    joining, update_modified=False)
		if relieving:
			frappe.db.set_value("Employee", self.employee.name, "relieving_date",
			                    relieving, update_modified=False)
		self.addCleanup(self._restore)

	def _restore(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee.name,
		                    {"date_of_joining": "2020-01-01", "relieving_date": None},
		                    update_modified=False)

	def test_eligible_range_narrows_to_the_employment_dates(self):
		self._employed(joining="2026-08-03", relieving="2026-08-14")
		start, end = eligible_range(self.employee.name, getdate(START), getdate(END))
		self.assertEqual(str(start), "2026-08-03")
		self.assertEqual(str(end), "2026-08-14")

	def test_joining_mid_period_counts_only_from_the_joining_date(self):
		self.on_shift(SHIFT_95)
		self._employed(joining="2026-08-03")
		twd, _paid = compute_working_days(self.employee.name, START, END)
		# 3-22 Aug: 15 weekdays x 1 + 3 Saturdays x 0.5
		self.assertEqual(twd, 16.5)

	def test_leaving_mid_period_counts_only_up_to_the_relieving_date(self):
		self.on_shift(SHIFT_95)
		self._employed(relieving="2026-08-05")
		twd, _paid = compute_working_days(self.employee.name, START, END)
		# 23 Jul - 5 Aug: 10 weekdays x 1 + 2 Saturdays x 0.5
		self.assertEqual(twd, 11.0)

	def test_an_employee_who_joins_after_the_period_earns_nothing(self):
		self._employed(joining="2027-01-01")
		self.assertEqual(compute_working_days(self.employee.name, START, END), (0.0, 0.0))


class TestHolidaysAreNotPayableDays(CalendarDaysFixture):
	def test_a_holiday_on_a_weekday_removes_one_day(self):
		self.on_shift(SHIFT_95)
		before, _p = compute_working_days(self.employee.name, START, END)
		hl = PREFIX + " CalHolidays"
		if not frappe.db.exists("Holiday List", hl):
			doc = frappe.get_doc({
				"doctype": "Holiday List", "__newname": hl, "holiday_list_name": hl,
				"from_date": "2026-01-01", "to_date": "2026-12-31",
				"holidays": [{"holiday_date": "2026-08-05", "description": PREFIX}],
			}).insert(ignore_permissions=True)
			self._created.append(("Holiday List", doc.name))
		previous = frappe.db.get_value("Employee", self.employee.name, "holiday_list")
		frappe.db.set_value("Employee", self.employee.name, "holiday_list", hl,
		                    update_modified=False)
		self.addCleanup(frappe.db.set_value, "Employee", self.employee.name,
		                "holiday_list", previous, update_modified=False)
		after, _p2 = compute_working_days(self.employee.name, START, END)
		self.assertEqual(before - after, 1.0)


class TestThePayrollPreviewEndpointTheBrowserCalls(CalendarDaysFixture):
	"""Green unit tests on compute_working_days do not prove the SCREEN is right.

	The console's payroll preview calls
	``isoft_angola_hr.isoft_angola_hr.api.payroll_preview`` — confirmed from the web
	server's own access log — so that is what this exercises.
	"""

	def _preview_row(self, employee, **kw):
		from isoft_angola_hr.isoft_angola_hr import api
		rows = api.payroll_preview(company=self.company, start_date=START, end_date=END, **kw)
		return next((r for r in rows if r.get("employee") == employee), None)

	def test_the_preview_reports_twenty_four_and_a_half_days(self):
		self.on_shift(SHIFT_95)
		row = self._preview_row(self.employee.name)
		self.assertIsNotNone(row, "the employee did not appear in the preview at all")
		self.assertEqual(flt(row["total_working_days"]), PERIOD_DAYS)
		self.assertEqual(flt(row["payment_days"]), PERIOD_DAYS)

	def test_the_preview_pays_the_full_base_for_a_complete_period(self):
		self.on_shift(SHIFT_95)
		row = self._preview_row(self.employee.name, validate_attendance=1)
		self.assertEqual(flt(row["payment_days"]), flt(row["total_working_days"]))
		self.assertAlmostEqual(flt(row["base"]), flt(self.profile.base), places=2,
		                       msg="the base was pro-rated by an hours ratio")
		self.assertAlmostEqual(flt(row.get("absence") or 0), 0.0, places=2)

	def test_the_preview_agrees_across_shift_shapes(self):
		seen = {}
		for shift in (SHIFT_95, SHIFT_8, None):
			frappe.db.set_value("Employee", self.employee.name, "default_shift", shift,
			                    update_modified=False)
			seen[shift or "no shift"] = flt(self._preview_row(
				self.employee.name)["total_working_days"])
		frappe.db.set_value("Employee", self.employee.name, "default_shift", None,
		                    update_modified=False)
		self.assertEqual(set(seen.values()), {PERIOD_DAYS},
		                 "the preview disagreed across shifts: %s" % seen)

	def test_the_column_is_named_after_its_unit(self):
		from isoft_angola_hr.isoft_angola_hr.api import get_preview_columns
		self._set_basis("Calendar Working Days")
		label = [c["label"] for c in get_preview_columns() if c["key"] == "days"][0]
		self.assertEqual(label, "Paid/Total Days")

		self._set_basis("Auto (Holiday List)")
		self.addCleanup(self._set_basis, "Calendar Working Days")
		label = [c["label"] for c in get_preview_columns() if c["key"] == "days"][0]
		self.assertIn("equivalent", label.lower(),
		              "hour-equivalents are still being presented as Days")
