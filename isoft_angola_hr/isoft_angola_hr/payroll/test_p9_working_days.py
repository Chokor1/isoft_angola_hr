# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Working Days Basis: what the total counts, and that absences are counted the same way.

The bug this file exists to prevent is a unit mismatch. ``total_working_days`` and every
absence deduction are divided by one another to make the proration factor, so they must
be measured in the same unit. They were not: the total could be a flat calendar-day count
(Standard basis) while deductions stayed in standard-day equivalents (hours ÷ 8), which
silently mis-priced every absence for anyone whose day is not exactly 8 hours.

SAFETY — same contract as the rest of this folder: records are prefixed ``_TEST AHR``,
registered for deletion, and each test runs in a savepoint. The Working Days Basis is a
Single, so each test that changes it restores the previous value in a cleanup.
"""

import frappe
from frappe.utils import add_days, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
	compute_working_days,
	day_value,
	shift_day_hours,
)
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

AUTO = "Auto (Holiday List)"
CALENDAR = "Calendar Working Days"
FIXED = "Standard (Fixed)"


class WorkingDaysFixture(PayrollFixture):
	"""Two employees over the same period: one on the default 8h calendar, one on a shift
	whose weekdays are 9.5h and whose Saturday is 4h — the shape that exposed the bug."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		cls.shift = cls._track(frappe.get_doc({
			"doctype": "Shift Type", "name": PREFIX + " 9h30",
			"start_time": "08:00:00", "end_time": "17:30:00",
			"weekday_hours": [
				{"weekday": d, "is_working_day": 1,
				 "start_time": "08:00:00", "end_time": "17:30:00"}
				for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
			] + [
				{"weekday": "Saturday", "is_working_day": 1,
				 "start_time": "09:00:00", "end_time": "13:00:00"},
				{"weekday": "Sunday", "is_working_day": 0},
			],
		}).insert(ignore_permissions=True))

		cls.shifted = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Shifted",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01", "default_shift": cls.shift.name,
		}).insert(ignore_permissions=True))

	def use_basis(self, basis):
		"""Switch the Working Days Basis for one test and put it back afterwards."""
		previous = frappe.db.get_single_value("Isoft HR Settings", "working_days_basis")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
		                    "working_days_basis", basis)
		frappe.clear_cache(doctype="Isoft HR Settings")

		def restore():
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
			                    "working_days_basis", previous)
			frappe.clear_cache(doctype="Isoft HR Settings")

		self.addCleanup(restore)

	def _saturday_in_period(self):
		d = getdate(self.start)
		while d <= getdate(self.end):
			if d.weekday() == 5:
				return d
			d = add_days(d, 1)
		raise AssertionError("the fixture period contains no Saturday")

	def _weekday_in_period(self):
		d = getdate(self.start)
		while d <= getdate(self.end):
			if d.weekday() < 5:
				return d
			d = add_days(d, 1)
		raise AssertionError("the fixture period contains no weekday")

	def absent(self, employee, date):
		doc = frappe.get_doc({
			"doctype": "Attendance", "employee": employee, "company": self.company,
			"attendance_date": getdate(date), "status": "Absent",
		})
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc


# --------------------------------------------------------------------------- #
# What the total counts
# --------------------------------------------------------------------------- #
class TestTotalWorkingDays(WorkingDaysFixture):
	def test_calendar_basis_counts_days_not_hours(self):
		self.use_basis(CALENDAR)
		plain, _ = compute_working_days(self.employee.name, self.start, self.end)
		shifted, _ = compute_working_days(self.shifted.name, self.start, self.end)

		# A day is a day: the length of the shift must not change the count. Both shapes
		# schedule the same dates, and a short Saturday is half a day for both.
		self.assertEqual(plain, shifted)
		self.assertEqual(flt(plain) * 2, flt(int(plain * 2)),
		                 "a calendar count moves in whole and half days")

	def test_auto_basis_still_counts_standard_day_equivalents(self):
		self.use_basis(AUTO)
		plain, _ = compute_working_days(self.employee.name, self.start, self.end)
		shifted, _ = compute_working_days(self.shifted.name, self.start, self.end)

		# The 9.5h shift is worth more than the 8h calendar under this basis — that is
		# the whole point of it, and it is what produced the 28.125 HR queried.
		self.assertGreater(shifted, plain)

	def test_calendar_total_equals_the_number_of_scheduled_days(self):
		self.use_basis(CALENDAR)
		twd, _ = compute_working_days(self.shifted.name, self.start, self.end)

		expected, d = 0, getdate(self.start)
		while d <= getdate(self.end):
			if shift_day_hours(self.shift.name, d) > 0:
				expected += 1
			d = add_days(d, 1)
		# Holidays are excluded by compute_working_days, so this is an upper bound.
		self.assertLessEqual(twd, expected)
		self.assertGreater(twd, 0)


# --------------------------------------------------------------------------- #
# The unit mismatch this module exists for
# --------------------------------------------------------------------------- #
class TestAbsenceIsCountedInTheSameUnit(WorkingDaysFixture):
	def test_a_full_day_absence_costs_exactly_one_day_under_calendar(self):
		self.use_basis(CALENDAR)
		before_twd, before_pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self.absent(self.shifted.name, self._weekday_in_period())
		twd, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertEqual(twd, before_twd, "an absence must not change the total")
		self.assertAlmostEqual(before_pay - pay, 1.0, places=6)

	def test_an_absent_short_saturday_is_one_absence_under_calendar(self):
		# HR's "Absence and Late Arrival Deductions" (October 2026) counts ABSENCES: a day
		# the employee was due and did not come is one absence, worth 44/6 hours of the
		# hourly wage, whatever that day's clock length. The short Saturday still counts
		# 0.5 in the period total; as an absence it is 1. services/absences.py prices it.
		self.use_basis(CALENDAR)
		_, before_pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self.absent(self.shifted.name, self._saturday_in_period())
		_, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertAlmostEqual(before_pay - pay, 1.0, places=6)
	def test_an_absent_short_saturday_is_one_absence_under_auto_too(self):
		# the basis changes how the total is counted, never what an absence is
		self.use_basis(AUTO)
		_, before_pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self.absent(self.shifted.name, self._saturday_in_period())
		_, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertAlmostEqual(before_pay - pay, 1.0, places=6)
	def test_fixed_basis_deducts_whole_days_not_hour_equivalents(self):
		"""Regression: the total was a flat day count while the deduction stayed in
		standard-day equivalents, so one absent day removed 1.1875 days from a 9.5h
		employee and 1.0 from an 8h one, on the same fixed 30-day denominator."""
		self.use_basis(FIXED)
		_, plain_before = compute_working_days(
			self.employee.name, self.start, self.end, validate_attendance=1)
		_, shifted_before = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.absent(self.employee.name, self._weekday_in_period())
		self.absent(self.shifted.name, self._weekday_in_period())

		_, plain_after = compute_working_days(
			self.employee.name, self.start, self.end, validate_attendance=1)
		_, shifted_after = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertAlmostEqual(plain_before - plain_after, 1.0, places=6)
		self.assertAlmostEqual(shifted_before - shifted_after, 1.0, places=6,
		                       msg="a 9.5h day must still cost one day, not 1.1875")

	def test_payment_days_never_exceed_the_total(self):
		for basis in (AUTO, CALENDAR, FIXED):
			with self.subTest(basis=basis):
				self.use_basis(basis)
				twd, pay = compute_working_days(
					self.shifted.name, self.start, self.end, validate_attendance=1)
				self.assertLessEqual(pay, twd)
				self.assertGreaterEqual(pay, 0)


# --------------------------------------------------------------------------- #
# day_value is the single definition of the unit
# --------------------------------------------------------------------------- #
class TestDayValue(WorkingDaysFixture):
	def test_calendar_and_fixed_value_a_full_day_at_one_and_a_short_day_at_half(self):
		sat, weekday = self._saturday_in_period(), self._weekday_in_period()
		for basis in (CALENDAR, FIXED):
			# a 9.5h weekday is the shift's FULL day, so it is one day - not 1.1875
			self.assertEqual(day_value(self.shift.name, weekday, 8, basis), 1.0)
			# a 4h Saturday is a SHORT day: half a working day
			self.assertEqual(day_value(self.shift.name, sat, 8, basis), 0.5)

	def test_auto_values_a_day_by_its_hours(self):
		sat, weekday = self._saturday_in_period(), self._weekday_in_period()
		self.assertAlmostEqual(day_value(self.shift.name, weekday, 8, AUTO), 9.5 / 8, places=6)
		self.assertAlmostEqual(day_value(self.shift.name, sat, 8, AUTO), 4.0 / 8, places=6)

	def test_a_non_working_day_is_worth_nothing_under_every_basis(self):
		sunday = self._saturday_in_period()
		sunday = add_days(sunday, 1)
		for basis in (AUTO, CALENDAR, FIXED):
			self.assertEqual(day_value(self.shift.name, sunday, 8, basis), 0.0)


# --------------------------------------------------------------------------- #
# The Benvindo case — QA reproduction, isolated from live payroll
# --------------------------------------------------------------------------- #
class TestSaturdayAbsenceCase(WorkingDaysFixture):
	"""Reproduces the case raised in review: one unjustified FULL DAY occurrence on a
	Saturday, for an employee whose Saturday is a short (4h) working day.

	Under the old Auto basis that Saturday was worth 0.5 standard-day equivalents, so the
	absence cost half a day. Under Calendar Working Days a scheduled day is a day, so it
	costs one. Both are internally consistent; they are different policies, and this test
	pins the one now in force so it cannot change again unnoticed.

	No live salary slip is read or written — the fixture employee is used throughout.
	"""

	def _reason(self):
		"""A justification reason — required before an occurrence may be Justified."""
		name = PREFIX + " Motivo"
		if not frappe.db.exists("Isoft Absence Reason", name):
			frappe.get_doc({"doctype": "Isoft Absence Reason",
			                "reason": name}).insert(ignore_permissions=True)
			self._created.append(("Isoft Absence Reason", name))
		return name

	def _occurrence(self, employee, date, occurrence_type="Full Day", status="Unjustified"):
		values = {
			"doctype": "Isoft Attendance Occurrence", "employee": employee,
			"occurrence_date": getdate(date), "occurrence_type": occurrence_type,
			"hours": 0, "status": status,
		}
		if status == "Justified":
			values["justification_reason"] = self._reason()
		doc = frappe.get_doc(values)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_full_day_saturday_absence_is_one_absence_under_calendar(self):
		self.use_basis(CALENDAR)
		sat = self._saturday_in_period()
		self.assertGreater(shift_day_hours(self.shift.name, sat), 0,
		                   "the fixture Saturday must be a working day")
		self.assertLess(shift_day_hours(self.shift.name, sat), 8,
		                "the point of the case is that Saturday is SHORT")

		twd_before, pay_before = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self._occurrence(self.shifted.name, sat)
		twd, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertEqual(twd, twd_before, "an absence must not change the total")
		# HR's document (October 2026): an unjustified day is one absence, short or not
		self.assertAlmostEqual(pay_before - pay, 1.0, places=6)
	def test_the_same_absence_is_one_absence_under_the_old_basis(self):
		# Kept as the counterpart: the basis used to decide the weight of a Saturday
		# absence (0.5 under Auto); since October 2026 the basis decides nothing about
		# absences, so both bases agree.
		self.use_basis(AUTO)
		sat = self._saturday_in_period()
		_, pay_before = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self._occurrence(self.shifted.name, sat)
		_, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertAlmostEqual(pay_before - pay, 1.0, places=6)
	def test_the_pay_effect_is_one_absence_of_the_hourly_wage(self):
		"""The money follows HR's rule, not the day count: one absence costs the hourly
		wage (basic × 12 / 2288) × 44/6 hours, whatever the period's working days."""
		from isoft_angola_hr.isoft_angola_hr.payroll import engine
		from isoft_angola_hr.isoft_angola_hr.services import absences

		self.use_basis(CALENDAR)
		sat = self._saturday_in_period()
		twd, _pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self._occurrence(self.shifted.name, sat)
		full = engine.compute_slip(self.profile, {"total_working_days": twd, "payment_days": twd})
		cut = engine.compute_slip(self.profile, {"total_working_days": twd, "payment_days": twd,
		                                         "absent_days": 1})
		line = lambda res: sum(e["amount"] for e in res["earnings"] if e["abbr"] == "SB")
		lost = flt(line(full) - line(cut), 2)
		self.assertEqual(lost, float(absences.basic_deduction(self.profile.base, 1)))
		self.assertNotEqual(lost, flt(flt(self.profile.base) * 0.5 / twd, 2),
		                    "base ÷ working days no longer prices an absence")
	def test_a_justified_saturday_absence_costs_nothing(self):
		self.use_basis(CALENDAR)
		sat = self._saturday_in_period()
		_, pay_before = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)
		self._occurrence(self.shifted.name, sat, status="Justified")
		_, pay = compute_working_days(
			self.shifted.name, self.start, self.end, validate_attendance=1)

		self.assertAlmostEqual(pay, pay_before, places=6,
		                       msg="a justified absence is not deducted")
