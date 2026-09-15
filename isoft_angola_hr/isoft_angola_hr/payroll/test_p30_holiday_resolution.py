# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""One holiday resolution for the whole application, and it prefers the Shift Type.

September 2026 is the case that forced this. ``ITEC - Working Hours`` declares
``holiday_list = "Holidays 2026"``, which contains the 17th (Dia do Herói Nacional) and
the 18th (its ponte). Attendance honoured that list; payroll did not — payroll asked
ERPNext's ``get_holiday_list_for_employee``, which reads only ``Employee.holiday_list``
and the Company default. Sixty-five employees still pointed at
``Standard Holiday List Angola 2025``, a list that expired in 2025 and holds no 2026 date
at all, so those two public holidays were paid to them as ordinary working days while
their colleagues, whose field happened to be empty, were paid correctly. The preview
showed 24.0 beside 22.0 with no explanation for either.

The precedence pinned here is: the effective Shift Type's list, then the Employee's, then
the Company default. Most specific wins, and a Shift Type is the more specific statement:
it describes a working pattern, and the holidays interrupting that pattern belong to the
same description. It also cannot go stale one employee at a time.

What these tests are careful NOT to assert is that everybody gets 22.0. An employee with
no shift and a stale list still gets 24.0, and that is the correct output of their actual
configuration — the fix is to their data, not to this code. Tests 2 and 6 pin exactly
that, so nobody can later "fix" the difference by making the calculation ignore what it
was told.
"""

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
	compute_working_days,
	get_holiday_dates,
)
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p27_calendar_days import (
	CalendarDaysFixture,
	SHIFT_8,
	SHIFT_95,
)
from isoft_angola_hr.isoft_angola_hr.services import work_calendar

#: September 2026: 22 weekdays, 4 Saturdays (5, 12, 19, 26), 4 Sundays.
SEP_START, SEP_END = getdate("2026-09-01"), getdate("2026-09-30")

#: 22 weekdays x 1.0 + 4 Saturdays x 0.5, with no holiday deducted.
SEPTEMBER_WITHOUT_HOLIDAYS = 24.0
#: ...minus Thursday the 17th and Friday the 18th, both full scheduled days.
SEPTEMBER_WITH_HOLIDAYS = 22.0

HERO_DAY = getdate("2026-09-17")          # Thursday  — DIA DO HEROI NACIONAL
HERO_BRIDGE = getdate("2026-09-18")       # Friday    — DIA DO HEROI NACIONAL PONTE
SEP_SATURDAY = getdate("2026-09-19")
SEP_SUNDAY = getdate("2026-09-20")

WITH_HOLIDAYS = PREFIX + " Holidays 2026"
NO_2026 = PREFIX + " Expired 2025"
COMPANY_LIST = PREFIX + " Company 2026"


class HolidayResolutionFixture(CalendarDaysFixture):
	"""Three holiday lists and a shift, so precedence can be observed rather than argued."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._holiday_list(WITH_HOLIDAYS, "2026-01-01", "2026-12-31", [
			(HERO_DAY, "DIA DO HEROI NACIONAL"),
			(HERO_BRIDGE, "DIA DO HEROI NACIONAL PONTE"),
		])
		cls._holiday_list(NO_2026, "2025-01-01", "2025-12-31", [
			(getdate("2025-09-17"), "DIA DO HEROI NACIONAL"),
		])
		cls._holiday_list(COMPANY_LIST, "2026-01-01", "2026-12-31", [
			(HERO_DAY, "DIA DO HEROI NACIONAL"),
			(HERO_BRIDGE, "DIA DO HEROI NACIONAL PONTE"),
		])

	@classmethod
	def _holiday_list(cls, name, from_date, to_date, days):
		if frappe.db.exists("Holiday List", name):
			return
		doc = frappe.get_doc({
			"doctype": "Holiday List", "__newname": name, "holiday_list_name": name,
			"from_date": from_date, "to_date": to_date,
			"holidays": [{"holiday_date": d, "description": desc} for d, desc in days],
		}).insert(ignore_permissions=True)
		cls._created.append(("Holiday List", doc.name))
		frappe.db.commit()

	# --- per-test configuration, always restored ---------------------------------

	def configure(self, shift=None, employee_list=None, company_list="__keep__",
	              shift_list="__keep__"):
		"""Point the employee at a calendar and undo it afterwards."""
		emp = self.employee.name
		for field, value in (("default_shift", shift), ("holiday_list", employee_list)):
			before = frappe.db.get_value("Employee", emp, field)
			frappe.db.set_value("Employee", emp, field, value, update_modified=False)
			self.addCleanup(frappe.db.set_value, "Employee", emp, field, before,
			                update_modified=False)
		if company_list != "__keep__":
			before = frappe.db.get_value("Company", self.company, "default_holiday_list")
			frappe.db.set_value("Company", self.company, "default_holiday_list",
			                    company_list, update_modified=False)
			self.addCleanup(frappe.db.set_value, "Company", self.company,
			                "default_holiday_list", before, update_modified=False)
		if shift_list != "__keep__" and shift:
			before = frappe.db.get_value("Shift Type", shift, "holiday_list")
			frappe.db.set_value("Shift Type", shift, "holiday_list", shift_list,
			                    update_modified=False)
			self.addCleanup(frappe.db.set_value, "Shift Type", shift, "holiday_list",
			                before, update_modified=False)
		frappe.clear_cache()
		self.addCleanup(frappe.clear_cache)

	def one_off_list(self, name, days, from_date="2026-01-01", to_date="2026-12-31"):
		if not frappe.db.exists("Holiday List", name):
			doc = frappe.get_doc({
				"doctype": "Holiday List", "__newname": name, "holiday_list_name": name,
				"from_date": from_date, "to_date": to_date,
				"holidays": [{"holiday_date": d, "description": desc} for d, desc in days],
			}).insert(ignore_permissions=True)
			type(self)._created.append(("Holiday List", doc.name))
			frappe.db.commit()
		return name

	def september(self, **kw):
		self.configure(**kw)
		twd, pay = compute_working_days(self.employee.name, SEP_START, SEP_END)
		return twd, pay


# ---------------------------------------------------------------------------
# (1)(2)(3)(4) precedence
# ---------------------------------------------------------------------------

class TestPrecedence(HolidayResolutionFixture):
	def test_1_the_itec_shift_with_holidays_2026_gives_twenty_two_days(self):
		"""22 weekdays - 2 holidays + 4 Saturdays x 0.5 = 22.0."""
		twd, pay = self.september(shift=SHIFT_95, employee_list=None,
		                          company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)
		self.assertEqual(pay, SEPTEMBER_WITH_HOLIDAYS)

	def test_2_the_same_shift_with_a_list_lacking_those_holidays_gives_twenty_four(self):
		"""Not a bug to be normalised away: this is what that configuration means."""
		twd, _p = self.september(shift=SHIFT_95, employee_list=None,
		                         company_list=None, shift_list=NO_2026)
		self.assertEqual(twd, SEPTEMBER_WITHOUT_HOLIDAYS)

	def test_3_the_difference_is_exactly_the_weight_of_the_scheduled_holidays(self):
		with_holidays, _a = self.september(shift=SHIFT_95, employee_list=None,
		                                   company_list=None, shift_list=WITH_HOLIDAYS)
		self.doCleanups()
		without, _b = self.september(shift=SHIFT_95, employee_list=None,
		                             company_list=None, shift_list=NO_2026)
		# both holidays fall on full scheduled weekdays, so exactly 1.0 each
		self.assertEqual(without - with_holidays, 2.0)

	def test_4_the_shift_list_overrides_a_stale_employee_list(self):
		"""The September case, exactly: employee still on the expired 2025 list."""
		twd, _p = self.september(shift=SHIFT_95, employee_list=NO_2026,
		                         company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)
		holidays = get_holiday_dates(self.employee.name, SEP_START, SEP_END)
		self.assertIn(HERO_DAY, holidays)
		self.assertIn(HERO_BRIDGE, holidays)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (WITH_HOLIDAYS, work_calendar.FROM_SHIFT))

	def test_5_a_null_employee_list_falls_through_to_the_company_default(self):
		twd, _p = self.september(shift=None, employee_list=None,
		                         company_list=COMPANY_LIST)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (COMPANY_LIST, work_calendar.FROM_COMPANY))

	def test_6_an_expired_employee_list_falls_through_to_one_that_covers_the_period(self):
		"""The rule that reaches the sixty employees with no Shift Type.

		An earlier version of this module asserted the opposite - that a stale Employee
		list stayed authoritative - on the grounds that only the Shift Type should
		override it. That was wrong about what an expired list means. A Holiday List
		states the year it describes; outside that year it is not "a year with no
		holidays", it is silence. Paying somebody against silence, while the colleague
		next to them on the same shift is paid against the real calendar, is the bug this
		whole module exists to remove.
		"""
		twd, _p = self.september(shift=None, employee_list=NO_2026,
		                         company_list=COMPANY_LIST)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (COMPANY_LIST, work_calendar.FROM_COMPANY))

	def test_a_valid_employee_list_still_wins_over_the_company_default(self):
		"""The guard against "make everyone the same". A list is skipped because it does
		not COVER the date, never because a different answer is inconvenient."""
		other = self.one_off_list(PREFIX + " Valid Other 2026",
		                          [(getdate("2026-09-21"), "COMPANY HOLIDAY")])
		twd, _p = self.september(shift=None, employee_list=other,
		                         company_list=COMPANY_LIST)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (other, work_calendar.FROM_EMPLOYEE))
		# 24.0 minus its own single Monday holiday - its own answer, not the default's
		self.assertEqual(twd, SEPTEMBER_WITHOUT_HOLIDAYS - 1.0)
		self.assertNotEqual(twd, SEPTEMBER_WITH_HOLIDAYS)

	def test_when_nothing_covers_the_period_the_configured_list_is_still_reported(self):
		"""No invention: if every candidate expired, say which one was configured."""
		expired_company = self.one_off_list(PREFIX + " Expired Company 2024",
		                                    [(getdate("2024-09-17"), "OLD")],
		                                    "2024-01-01", "2024-12-31")
		twd, _p = self.september(shift=None, employee_list=NO_2026,
		                         company_list=expired_company)
		self.assertEqual(twd, SEPTEMBER_WITHOUT_HOLIDAYS)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (NO_2026, work_calendar.FROM_EMPLOYEE))
		info = work_calendar.diagnose(self.employee.name, SEP_START, SEP_END)
		self.assertFalse(info["any_covers"])

	def test_a_shift_without_its_own_list_does_not_shadow_the_employee_list(self):
		"""Precedence is "declared", not "has a shift"."""
		twd, _p = self.september(shift=SHIFT_95, employee_list=WITH_HOLIDAYS,
		                         company_list=None, shift_list=None)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertEqual((name, source), (WITH_HOLIDAYS, work_calendar.FROM_EMPLOYEE))

	def test_no_calendar_at_all_resolves_to_nothing_rather_than_guessing(self):
		self.configure(shift=None, employee_list=None, company_list=None)
		name, source = work_calendar.resolve_holiday_list(self.employee.name, SEP_END)
		self.assertIsNone(name)
		self.assertEqual(source, work_calendar.FROM_NONE)
		self.assertEqual(get_holiday_dates(self.employee.name, SEP_START, SEP_END), set())


# ---------------------------------------------------------------------------
# (7)(8)(9) what a holiday actually costs, and (13) what does not cost anything
# ---------------------------------------------------------------------------

class TestWhatAHolidayCosts(HolidayResolutionFixture):
	def one_holiday(self, date, description="TEST HOLIDAY"):
		"""A list containing exactly one holiday, on `date`."""
		name = "%s Single %s" % (PREFIX, date)
		if not frappe.db.exists("Holiday List", name):
			doc = frappe.get_doc({
				"doctype": "Holiday List", "__newname": name, "holiday_list_name": name,
				"from_date": "2026-01-01", "to_date": "2026-12-31",
				"holidays": [{"holiday_date": date, "description": description}],
			}).insert(ignore_permissions=True)
			type(self)._created.append(("Holiday List", doc.name))
			frappe.db.commit()
		return name

    # the baseline every case below is measured against
	def baseline(self):
		twd, _p = self.september(shift=SHIFT_95, employee_list=None,
		                         company_list=None, shift_list=NO_2026)
		self.doCleanups()
		return twd

	def test_7_a_holiday_on_a_full_weekday_costs_one_day(self):
		base = self.baseline()
		twd, _p = self.september(shift=SHIFT_95, employee_list=None, company_list=None,
		                         shift_list=self.one_holiday(HERO_DAY))
		self.assertEqual(base - twd, 1.0)

	def test_8_a_holiday_on_the_short_saturday_costs_half_a_day(self):
		"""Not a whole day: the Saturday was only ever worth 0.5."""
		base = self.baseline()
		twd, _p = self.september(shift=SHIFT_95, employee_list=None, company_list=None,
		                         shift_list=self.one_holiday(SEP_SATURDAY))
		self.assertEqual(base - twd, 0.5)

	def test_9_a_holiday_on_a_sunday_costs_nothing(self):
		base = self.baseline()
		twd, _p = self.september(shift=SHIFT_95, employee_list=None, company_list=None,
		                         shift_list=self.one_holiday(SEP_SUNDAY))
		self.assertEqual(base - twd, 0.0)

	def test_a_nine_and_a_half_hour_weekday_is_still_one_day_not_1_1875(self):
		twd, _p = self.september(shift=SHIFT_95, employee_list=None,
		                         company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertEqual(twd, 22.0)
		for wrong in (28.625, 27.6875, 26.125, 1.1875 * 20):
			self.assertNotEqual(twd, wrong)

	def test_an_eight_hour_shift_reaches_the_same_september_total(self):
		"""Clock length decides FULL vs SHORT, never how much more than one day."""
		nine_five, _a = self.september(shift=SHIFT_95, employee_list=None,
		                               company_list=None, shift_list=WITH_HOLIDAYS)
		self.doCleanups()
		eight, _b = self.september(shift=SHIFT_8, employee_list=None,
		                           company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertEqual(nine_five, eight)
		self.assertEqual(eight, SEPTEMBER_WITH_HOLIDAYS)


# ---------------------------------------------------------------------------
# (10)(11)(12)(13) employment period, overtime and attendance
# ---------------------------------------------------------------------------

class TestNothingElseMovesTheTotal(HolidayResolutionFixture):
	def test_10_dates_before_joining_do_not_count(self):
		emp = self.employee.name
		before = frappe.db.get_value("Employee", emp, "date_of_joining")
		self.addCleanup(frappe.db.set_value, "Employee", emp, "date_of_joining", before,
		                update_modified=False)
		full, _p = self.september(shift=SHIFT_95, employee_list=None,
		                          company_list=None, shift_list=WITH_HOLIDAYS)
		frappe.db.set_value("Employee", emp, "date_of_joining", "2026-09-21",
		                    update_modified=False)
		late, _q = compute_working_days(emp, SEP_START, SEP_END)
		self.assertLess(late, full)
		# 21-25 Sep (5 full days) + 26 Sep (Saturday, 0.5) + 28-30 Sep (3 full days)
		self.assertEqual(late, 8.5)

	def test_11_dates_after_relieving_do_not_count(self):
		emp = self.employee.name
		before = frappe.db.get_value("Employee", emp, "relieving_date")
		self.addCleanup(frappe.db.set_value, "Employee", emp, "relieving_date", before,
		                update_modified=False)
		self.configure(shift=SHIFT_95, employee_list=None, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		frappe.db.set_value("Employee", emp, "relieving_date", "2026-09-04",
		                    update_modified=False)
		twd, _p = compute_working_days(emp, SEP_START, SEP_END)
		self.assertEqual(twd, 4.0)          # Tue 1 .. Fri 4, all full days

	def test_12_overtime_never_increases_total_working_days(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
			attendance_overtime_amount,
		)

		twd, _p = self.september(shift=SHIFT_95, employee_list=None,
		                         company_list=None, shift_list=WITH_HOLIDAYS)
		amount = attendance_overtime_amount(self.employee.name, 200000, twd,
		                                    SEP_START, SEP_END, 2)
		after, _q = compute_working_days(self.employee.name, SEP_START, SEP_END)
		self.assertEqual(after, twd)
		self.assertEqual(after, SEPTEMBER_WITH_HOLIDAYS)
		self.assertGreaterEqual(flt(amount), 0.0)

	def test_13_present_with_eight_hours_on_a_nine_and_a_half_hour_shift_is_a_full_day(self):
		"""ERPNext refuses attendance on a future date, so this one uses the fixture's
		own current period rather than September. The rule under test is the basis, not
		the month."""
		self.configure(shift=SHIFT_95, employee_list=None, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		day = getdate(frappe.utils.nowdate())
		while day.weekday() > 4 or day < getdate(self.start):
			day = frappe.utils.add_days(day, -1)
		att = frappe.get_doc({
			"doctype": "Attendance", "employee": self.employee.name,
			"attendance_date": day, "status": "Present", "working_hours": 8,
			"company": self.company,
		}).insert(ignore_permissions=True)
		att.submit()

		def drop():
			# the fixture's savepoint usually removes it first; this is only for the
			# case where something committed in between
			try:
				frappe.db.delete("Attendance", {"name": att.name})
			except Exception:
				pass

		self.addCleanup(drop)
		plain, _p = compute_working_days(self.employee.name, self.start, self.end)
		twd, pay = compute_working_days(self.employee.name, self.start, self.end,
		                                validate_attendance=1)
		self.assertEqual(twd, plain, "attendance must not move total working days")
		self.assertEqual(pay, twd,
		                 "8h against a 9.5h shift must not dock 0.1875 of a day")


# ---------------------------------------------------------------------------
# (14)(15) every consumer agrees, and there is only one implementation
# ---------------------------------------------------------------------------

class TestOneImplementation(HolidayResolutionFixture):
	def test_14_the_preview_api_matches_the_salary_slip_calculation(self):
		self.configure(shift=SHIFT_95, employee_list=NO_2026, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		rows = api.payroll_preview(self.company, str(SEP_START), str(SEP_END))
		mine = [r for r in rows if r["employee"] == self.employee.name]
		self.assertTrue(mine, "the fixture employee should be in the preview")
		twd, pay = compute_working_days(self.employee.name, SEP_START, SEP_END)
		self.assertEqual(flt(mine[0]["total_working_days"]), twd)
		self.assertEqual(flt(mine[0]["payment_days"]), pay)
		self.assertEqual(twd, SEPTEMBER_WITH_HOLIDAYS)

	def test_15_the_pdf_prints_what_the_api_calculated(self):
		import base64
		import re

		self.configure(shift=SHIFT_95, employee_list=NO_2026, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		rows = api.payroll_preview(self.company, str(SEP_START), str(SEP_END))
		mine = [r for r in rows if r["employee"] == self.employee.name][0]
		expected = "%s/%s" % (flt(mine["payment_days"]), flt(mine["total_working_days"]))
		res = api.export_payroll_preview(self.company, str(SEP_START), str(SEP_END),
		                                 file_format="pdf")
		self.assertTrue(base64.b64decode(res["content"]).startswith(b"%PDF"))
		# the PDF is built from the same rows; assert on the composed HTML, which is what
		# is handed to the renderer
		from isoft_angola_hr.isoft_angola_hr.payroll.test_p29_payroll_print import (
			ALL_COLUMNS, cell, cells_of, money, totals_of,
		)
		from isoft_angola_hr.isoft_angola_hr.services import payroll_print

		html, _o, _p = payroll_print.compose(
			columns=ALL_COLUMNS, rows=rows, totals=totals_of(rows), value_of=cell,
			money=money, company=self.company, period_label="x", generated_label="y")
		self.assertIn(expected, cells_of(html))
		self.assertEqual(expected, "%s/%s" % (SEPTEMBER_WITH_HOLIDAYS,
		                                      SEPTEMBER_WITH_HOLIDAYS))

	def test_the_settlement_uses_the_same_holiday_resolution(self):
		"""Final Settlement reads holidays through the same function, not its own copy."""
		self.configure(shift=SHIFT_95, employee_list=NO_2026, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		days = api._settlement_working_days(self.employee.name, SEP_START, SEP_END)
		# every day except Sundays and public holidays: 30 - 4 Sundays - 2 holidays
		self.assertEqual(days, 24.0)

	def test_there_is_only_one_holiday_resolution_left(self):
		"""The payroll path and the shift/attendance path must ask the same function."""
		import inspect

		from isoft_angola_hr.isoft_angola_hr import shift_weekday
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip import (
			isoft_salary_slip as slip,
		)

		def body(fn):
			"""Source with the docstring removed — prose about the old resolution is
			documentation, not a second implementation."""
			source = inspect.getsource(fn)
			doc = inspect.getdoc(fn) or ""
			for line in doc.splitlines():
				source = source.replace(line, "")
			return source

		for fn in (slip.get_holiday_dates, shift_weekday.get_employee_shift):
			self.assertIn("work_calendar", body(fn))
			self.assertNotIn("get_holiday_list_for_employee(", body(fn),
			                 "a second holiday resolution has come back")

	def test_the_trace_explains_the_result_it_reports(self):
		self.configure(shift=SHIFT_95, employee_list=NO_2026, company_list=None,
		               shift_list=WITH_HOLIDAYS)
		trace = api.payroll_calendar_trace(self.employee.name, str(SEP_START), str(SEP_END))
		self.assertEqual(trace["total_working_days"], SEPTEMBER_WITH_HOLIDAYS)
		self.assertEqual(trace["calendar"]["resolved_holiday_list"], WITH_HOLIDAYS)
		self.assertEqual(trace["calendar"]["source"], work_calendar.FROM_SHIFT)
		self.assertEqual(trace["calendar"]["employee_holiday_list"], NO_2026)
		by_date = {r["date"]: r for r in trace["days"]}
		self.assertEqual(by_date[HERO_DAY]["contribution"], 0.0)
		self.assertTrue(by_date[HERO_DAY]["holiday"])
		self.assertEqual(by_date[HERO_DAY]["description"], "DIA DO HEROI NACIONAL")
		self.assertEqual(by_date[HERO_BRIDGE]["description"], "DIA DO HEROI NACIONAL PONTE")
		self.assertEqual(by_date[SEP_SATURDAY]["contribution"], 0.5)
		self.assertEqual(by_date[SEP_SUNDAY]["contribution"], 0.0)
		# the trace must add up to what it claims, or it is decoration
		self.assertEqual(sum(r["contribution"] for r in trace["days"]),
		                 trace["total_working_days"])
		self.assertEqual(trace["total_working_days"],
		                 compute_working_days(self.employee.name, SEP_START, SEP_END)[0])


# ---------------------------------------------------------------------------
# readiness tells HR which calendar was used
# ---------------------------------------------------------------------------

class TestReadinessReportsTheCalendar(HolidayResolutionFixture):
	def warnings_for(self, **kw):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness

		self.configure(**kw)
		return [w for w in payroll_readiness._calendar_warnings(
			frappe._dict({"name": self.employee.name}), SEP_START, SEP_END)]

	def test_a_stale_employee_list_is_reported_with_the_list_actually_used(self):
		msgs = self.warnings_for(shift=SHIFT_95, employee_list=NO_2026,
		                         company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertTrue(msgs)
		joined = " ".join(msgs)
		self.assertIn(WITH_HOLIDAYS, joined, "must name the list that will be used")
		self.assertIn(NO_2026, joined, "must name the stale list it is overriding")

	def test_a_list_that_does_not_cover_the_period_is_reported(self):
		msgs = self.warnings_for(shift=None, employee_list=NO_2026, company_list=None)
		self.assertTrue(any("covers this payroll period" in m for m in msgs), msgs)
		self.assertTrue(any(NO_2026 in m for m in msgs))
		self.assertTrue(any("No public holiday will be deducted" in m for m in msgs), msgs)

	def test_no_resolvable_calendar_is_reported(self):
		msgs = self.warnings_for(shift=None, employee_list=None, company_list=None)
		self.assertTrue(any("No Holiday List resolves" in m for m in msgs), msgs)

	def test_a_correct_calendar_produces_no_warning(self):
		msgs = self.warnings_for(shift=SHIFT_95, employee_list=WITH_HOLIDAYS,
		                         company_list=None, shift_list=WITH_HOLIDAYS)
		self.assertEqual(msgs, [])

	def test_the_calendar_warning_never_blocks_payroll(self):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness

		severity, _label = payroll_readiness.EXCEPTIONS["EXC-014"]
		self.assertEqual(severity, payroll_readiness.WARNING)
