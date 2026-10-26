# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""September 2026, two employees who must NOT get the same answer, and the payroll cycle.

Two separate things are pinned here, both of which have been got wrong before.

**Working days are per employee.** An earlier defect made every employee resolve to the
same calendar, and the fix for it could just as easily have been "return 22 for
everybody". It was not: the working-day count is derived from *that employee's* effective
Shift Type, the Holiday List that shift points at, and whether that list actually covers
the payroll period. Two employees on different shifts must therefore be able to differ,
legitimately, in the same period — and :class:`TestTwoEmployeesLegitimatelyDiffer` proves
they do, so a future "make everyone equal" shortcut fails the suite.

September 2026 is the worked example, because it is the month the original complaint was
about and because it contains two consecutive public holidays: **17 September (Dia do
Herói Nacional)** and **18 September**, the company *ponte*. Both are Thursday and Friday,
so both cost a full day to anyone whose calendar contains them and nothing at all to
anyone whose calendar does not.

**The salary-change boundary follows the configured cycle.** ``payroll_cycle_start_day``
is site configuration, not a constant, so the "next valid effective date" must be derived
from it. Cycle start days 1, 15 and 23 are all exercised; no date is hard-coded.
"""

import unittest

import frappe
from frappe.utils import getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import salary_change, work_calendar
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

PREFIX = "_P36"
SEP_START, SEP_END = "2026-09-01", "2026-09-30"
#: Dia do Herói Nacional and the company ponte — Thursday and Friday.
SEP_HOLIDAYS = ["2026-09-17", "2026-09-18"]


class CalendarFixture(PayrollFixture):
	"""Two shifts, two holiday lists, two employees — deliberately different."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# The counts below assume the ITEC pattern: five weekdays and a short Saturday.
		# A second shift without a Saturday exists on dev since 2026-10-16 (the NDA demo),
		# and frappe.get_all's order is not by name, so the pattern is chosen, not the
		# first shift that happens to have one.
		cls.template_shift = next(
			(n for n in frappe.get_all("Shift Type", pluck="name", order_by="name")
			 if any(r.weekday == "Saturday" and r.is_working_day
			        for r in frappe.get_doc("Shift Type", n).get("weekday_hours") or [])), None)
		if not cls.template_shift:
			raise unittest.SkipTest("no Shift Type with a working Saturday on this site")
		cls.list_with = cls._holiday_list(PREFIX + " With Sept Holidays", SEP_HOLIDAYS)
		cls.list_without = cls._holiday_list(PREFIX + " Without Sept Holidays", [])
		cls.shift_with = cls._shift(PREFIX + " Shift With", cls.list_with)
		cls.shift_without = cls._shift(PREFIX + " Shift Without", cls.list_without)
		cls.emp_with = cls._staff("WithHols", cls.shift_with)
		cls.emp_without = cls._staff("WithoutHols", cls.shift_without)

	@classmethod
	def _holiday_list(cls, name, dates):
		if frappe.db.exists("Holiday List", name):
			frappe.delete_doc("Holiday List", name, force=1, ignore_permissions=True)
		doc = frappe.get_doc({
			"doctype": "Holiday List", "holiday_list_name": name,
			"from_date": "2026-01-01", "to_date": "2026-12-31",
			"holidays": [{"holiday_date": d, "description": "QA holiday"} for d in dates],
		}).insert(ignore_permissions=True)
		cls._created.append(("Holiday List", doc.name))
		return doc.name

	@classmethod
	def _weekly_schedule(cls):
		"""Copy a real shift's weekly pattern.

		A Shift Type with no weekday rows schedules every day, so a hand-built shift
		would count 30 days in September and prove nothing about holidays.
		"""
		src = frappe.get_doc("Shift Type", cls.template_shift)
		return [{"weekday": r.weekday, "is_working_day": r.is_working_day,
		         "start_time": r.start_time, "end_time": r.end_time}
		        for r in src.get("weekday_hours")]

	@classmethod
	def _shift(cls, name, holiday_list):
		if frappe.db.exists("Shift Type", name):
			frappe.delete_doc("Shift Type", name, force=1, ignore_permissions=True)
		doc = frappe.get_doc({
			"doctype": "Shift Type", "__newname": name,
			"start_time": "08:00:00", "end_time": "17:30:00",
			"holiday_list": holiday_list,
			"weekday_hours": cls._weekly_schedule(),
		}).insert(ignore_permissions=True)
		cls._created.append(("Shift Type", doc.name))
		return doc.name

	@classmethod
	def _staff(cls, label, shift):
		doc = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": label,
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01", "default_shift": shift,
		}).insert(ignore_permissions=True)
		cls._created.append(("Employee", doc.name))
		return doc.name


class TestSeptember2026Holidays(CalendarFixture):
	"""17 and 18 September are holidays *where the resolved calendar contains them*."""

	def test_both_dates_are_on_the_list_that_declares_them(self):
		dates = set(work_calendar.list_holiday_dates(
			self.list_with, SEP_START, SEP_END))
		for d in SEP_HOLIDAYS:
			with self.subTest(date=d):
				self.assertIn(getdate(d), dates)

	def test_they_are_weekdays_so_they_actually_cost_a_working_day(self):
		"""A holiday that falls at a weekend costs nothing; these do not."""
		for d in SEP_HOLIDAYS:
			with self.subTest(date=d):
				self.assertLess(getdate(d).weekday(), 5)

	def test_the_employee_whose_shift_declares_them_loses_exactly_those_two_days(self):
		got = work_calendar.holiday_dates(self.emp_with, SEP_START, SEP_END)
		self.assertEqual(sorted(str(d) for d in got), SEP_HOLIDAYS)

	def test_the_employee_whose_shift_does_not_declare_them_loses_none(self):
		got = work_calendar.holiday_dates(self.emp_without, SEP_START, SEP_END)
		self.assertEqual(list(got), [])

	def test_the_resolution_reports_the_shift_as_the_source(self):
		name, source = work_calendar.resolve_holiday_list(self.emp_with, SEP_START)
		self.assertEqual(name, self.list_with)
		self.assertEqual(source, work_calendar.FROM_SHIFT)


class TestTwoEmployeesLegitimatelyDiffer(CalendarFixture):
	"""The point of the whole calendar mechanism: the answer is per employee."""

	def setUp(self):
		super().setUp()
		# the day counts asserted below are Calendar Working Days (whole days), the basis
		# this site chose on 2026-08-24; pinned so the test does not inherit another one
		self.pin_settings(working_days_basis="Calendar Working Days")

	def _days(self, employee):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip \
			import compute_working_days
		total, _paid = compute_working_days(employee, SEP_START, SEP_END)
		return total

	def test_their_working_days_are_not_the_same(self):
		with_hols = self._days(self.emp_with)
		without = self._days(self.emp_without)
		self.assertNotEqual(
			with_hols, without,
			"two employees on different shift calendars produced the same working-day "
			"count — the calculation is being forced to a single number")

	def test_the_difference_is_exactly_the_two_september_holidays(self):
		self.assertAlmostEqual(
			self._days(self.emp_without) - self._days(self.emp_with), 2.0, places=2,
			msg="the gap between the two employees must be the two holidays, no more")

	def test_neither_number_is_invented(self):
		"""Each number is that employee's own schedule minus that employee's own holidays.

		September 2026 has 22 weekdays and 4 Saturdays. Under the Calendar Working Days
		basis a full weekday is 1 and the short Saturday is 0.5, so an unrestricted month
		is 24.0. The 17th and 18th are a Thursday and a Friday, worth a full day each.
		"""
		unrestricted = self._days(self.emp_without)
		self.assertAlmostEqual(unrestricted, 24.0, places=2)
		self.assertAlmostEqual(self._days(self.emp_with), 22.0, places=2)

	def test_a_holiday_list_is_never_shared_by_accident(self):
		a, _s1 = work_calendar.resolve_holiday_list(self.emp_with, SEP_START)
		b, _s2 = work_calendar.resolve_holiday_list(self.emp_without, SEP_START)
		self.assertNotEqual(a, b)


class TestResolutionPrecedence(CalendarFixture):
	"""Shift list, then employee list, then company default — and never an expired one."""

	def test_the_shift_list_wins_over_the_employee_list(self):
		frappe.db.set_value("Employee", self.emp_with, "holiday_list", self.list_without)
		self.addCleanup(frappe.db.set_value, "Employee", self.emp_with,
		                "holiday_list", None)
		name, source = work_calendar.resolve_holiday_list(self.emp_with, SEP_START)
		self.assertEqual(name, self.list_with)
		self.assertEqual(source, work_calendar.FROM_SHIFT)

	def test_an_expired_list_is_not_used_as_the_calendar(self):
		expired = self._holiday_list(PREFIX + " Expired 2023", [])
		frappe.db.set_value("Holiday List", expired, "from_date", "2023-01-01")
		frappe.db.set_value("Holiday List", expired, "to_date", "2023-12-31")
		self.assertFalse(work_calendar.covers(expired, SEP_START))

	def test_a_list_that_covers_the_period_is_accepted(self):
		self.assertTrue(work_calendar.covers(self.list_with, SEP_START))
		self.assertTrue(work_calendar.covers(self.list_with, SEP_END))

	def test_diagnose_reports_the_list_actually_used_and_where_it_came_from(self):
		info = work_calendar.diagnose(self.emp_with, SEP_START, SEP_END)
		self.assertEqual(info.get("resolved_holiday_list"), self.list_with)
		self.assertEqual(info.get("source"), work_calendar.FROM_SHIFT)
		self.assertEqual(info.get("shift_holiday_list"), self.list_with)
		self.assertTrue(info.get("any_covers"),
		                "a list covering the period must not be reported as uncovered")


# --------------------------------------------------------------------------- #
# 6 — the salary-change boundary follows the configured cycle
# --------------------------------------------------------------------------- #
class TestSalaryChangeCycleBoundaries(unittest.TestCase):
	"""Cycle start day is configuration; the next valid date is derived from it."""

	SETTINGS = "Isoft HR Settings"
	FIELD = "payroll_cycle_start_day"

	def setUp(self):
		self._backup = frappe.db.get_single_value(self.SETTINGS, self.FIELD)
		self.addCleanup(self._restore)

	def _restore(self):
		frappe.db.set_value(self.SETTINGS, self.SETTINGS, self.FIELD, self._backup,
		                    update_modified=False)
		frappe.clear_cache()

	def _cycle(self, day):
		frappe.db.set_value(self.SETTINGS, self.SETTINGS, self.FIELD, day,
		                    update_modified=False)
		frappe.clear_cache()

	def test_day_23_the_configured_production_cycle(self):
		self._cycle(23)
		# a boundary date is already valid, so the NEXT one is a month later
		self.assertEqual(str(salary_change.next_period_start("2026-08-23")), "2026-09-23")
		# mid-period rolls forward to the next boundary
		self.assertEqual(str(salary_change.next_period_start("2026-08-25")), "2026-09-23")
		self.assertEqual(str(salary_change.next_period_start("2026-09-01")), "2026-09-23")
		# the day before a boundary lands on that boundary
		self.assertEqual(str(salary_change.next_period_start("2026-08-22")), "2026-08-23")

	def test_day_1_a_calendar_month_cycle(self):
		self._cycle(1)
		self.assertEqual(str(salary_change.next_period_start("2026-08-23")), "2026-09-01")
		self.assertEqual(str(salary_change.next_period_start("2026-08-01")), "2026-09-01")
		self.assertEqual(str(salary_change.next_period_start("2026-08-31")), "2026-09-01")

	def test_day_15_a_mid_month_cycle(self):
		self._cycle(15)
		self.assertEqual(str(salary_change.next_period_start("2026-08-15")), "2026-09-15")
		self.assertEqual(str(salary_change.next_period_start("2026-08-20")), "2026-09-15")
		self.assertEqual(str(salary_change.next_period_start("2026-08-14")), "2026-08-15")

	def test_the_answer_moves_with_the_setting(self):
		"""The same date gives different answers under different cycles — proving the
		boundary is read from configuration and not hard-coded."""
		answers = set()
		for day in (1, 15, 23):
			self._cycle(day)
			answers.add(str(salary_change.next_period_start("2026-08-25")))
		self.assertEqual(len(answers), 3, "got %s" % sorted(answers))

	def test_every_supported_cycle_day_produces_a_future_boundary(self):
		for day in (1, 5, 10, 15, 20, 23, 28):
			self._cycle(day)
			for effective in ("2026-01-05", "2026-06-17", "2026-08-25", "2026-12-30"):
				with self.subTest(cycle_day=day, effective=effective):
					nxt = getdate(salary_change.next_period_start(effective))
					self.assertGreater(nxt, getdate(effective),
					                   "the next valid date must be in the future")

	def test_the_returned_date_is_itself_a_period_start(self):
		"""Offering a date that is not a boundary would just fail validation again."""
		for day in (1, 15, 23):
			self._cycle(day)
			nxt = getdate(salary_change.next_period_start("2026-08-25"))
			start, _end = api._cycle_period(nxt)
			with self.subTest(cycle_day=day):
				self.assertEqual(getdate(start), nxt)

	def test_a_boundary_date_is_accepted_as_valid(self):
		self._cycle(23)
		start, _end = api._cycle_period(getdate("2026-09-23"))
		self.assertEqual(str(start), "2026-09-23")
