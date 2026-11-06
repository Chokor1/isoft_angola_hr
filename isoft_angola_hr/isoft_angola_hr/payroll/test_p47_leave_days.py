# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Leave-day count: Saturdays and Sundays never count towards a leave request.

Support request, October 2026: a GUIA DE FÉRIAS request for 01-10-2026 → 30-10-2026 showed
30 days. It must be 22: thirty calendar days, minus eight Saturdays and Sundays. The rule is
services/leave_days.py, installed over ERPNext's get_number_of_leave_days.

SAFETY. Same contract as the other phases. Every record is prefixed ``_TEST AHR``: two
employees, a Holiday List, a six-day Shift Type, two Leave Types and a user. Each is
deleted explicitly in tearDownClass, and each test runs inside a savepoint. The console's
leave endpoints commit, so their commit is suppressed while a test runs (the
test_p6_hr_operated pattern). Every leave request is also cancelled and deleted on cleanup.
No site employee, holiday or shift is read or written.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, _company
from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import leave_days
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

LA_MODULE = "erpnext.hr.doctype.leave_application.leave_application"


class LeaveDaysFixture(FrappeTestCase):
	"""Two employees on the same test Holiday List: one with no shift, one whose shift works
	Saturday mornings. The Holiday List holds a Monday (02-11-2026) and a Saturday
	(07-11-2026) and nothing in October, so October shows the weekend rule alone."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._created = []
		cls.company = _company()
		gender = frappe.get_all("Gender", pluck="name")[0]

		cls.holiday_list = cls._track(frappe.get_doc({
			"doctype": "Holiday List", "holiday_list_name": PREFIX + " Leave Holidays 2026",
			"from_date": "2026-01-01", "to_date": "2026-12-31",
			"holidays": [
				{"holiday_date": "2026-11-02", "description": PREFIX + " weekday holiday"},
				{"holiday_date": "2026-11-07", "description": PREFIX + " Saturday holiday"},
			],
		}).insert(ignore_permissions=True)).name

		# Monday-Friday full days, a working Saturday morning, Sunday off: the pattern that
		# payroll counts as a working Saturday and leave must not.
		week = [{"weekday": d, "is_working_day": 1, "start_time": "08:00:00", "end_time": "17:00:00"}
		        for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")]
		week.append({"weekday": "Saturday", "is_working_day": 1, "start_time": "08:00:00",
		             "end_time": "12:00:00"})
		week.append({"weekday": "Sunday", "is_working_day": 0})
		shift = frappe.get_doc({
			"doctype": "Shift Type", "start_time": "08:00:00", "end_time": "17:00:00",
			"holiday_list": cls.holiday_list, "weekday_hours": week,
		})
		shift.name = PREFIX + " Six Day Shift"
		cls.shift = cls._track(shift.insert(ignore_permissions=True)).name

		def employee(last, **values):
			return cls._track(frappe.get_doc(dict({
				"doctype": "Employee", "first_name": PREFIX, "last_name": last,
				"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
				"gender": gender, "date_of_birth": "1990-01-01",
			}, **values)).insert(ignore_permissions=True)).name

		cls.weekday_employee = employee("Leave Weekdays", holiday_list=cls.holiday_list)
		cls.saturday_employee = employee("Leave Saturday Shift", default_shift=cls.shift)

		def leave_type(suffix, include_holiday):
			# allow_negative: a request needs no submitted Leave Allocation behind it. Submitting
			# one commits outside the savepoint (see test_p6_hr_operated.leave_type).
			return cls._track(frappe.get_doc({
				"doctype": "Leave Type", "leave_type_name": PREFIX + " " + suffix,
				"max_leaves_allowed": 60, "is_carry_forward": 0, "allow_negative": 1,
				"include_holiday": include_holiday,
			}).insert(ignore_permissions=True)).name

		cls.excl = leave_type("Ferias Excl Holidays", 0)   # as GUIA DE FÉRIAS on the site
		cls.incl = leave_type("Ferias Incl Holidays", 1)

		cls.ess_user = cls._track(frappe.get_doc({
			"doctype": "User", "email": "_test.ahr.leavedays@example.invalid",
			"first_name": PREFIX + " Leave Days", "send_welcome_email": 0,
			"roles": [{"role": "Employee"}],
		}).insert(ignore_permissions=True)).name
		frappe.db.set_value("Employee", cls.weekday_employee, "user_id", cls.ess_user)

	@classmethod
	def _track(cls, doc):
		cls._created.append((doc.doctype, doc.name))
		return doc

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		for name in frappe.get_all("Leave Application", pluck="name", filters={
				"employee": ("in", [cls.weekday_employee, cls.saturday_employee])}):
			_drop_leave(name)
		for doctype, name in reversed(cls._created):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True,
				                  delete_permanently=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("ahr_leave_days")
		original = frappe.db.commit
		frappe.db.commit = lambda *a, **k: None
		self.addCleanup(setattr, frappe.db, "commit", original)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="ahr_leave_days")

	def days(self, from_date, to_date, employee=None, leave_type=None, **kwargs):
		"""The count, through ERPNext's own entry point: what every caller gets."""
		count = frappe.get_attr(LA_MODULE + ".get_number_of_leave_days")
		return count(employee or self.weekday_employee, leave_type or self.excl,
		             from_date, to_date, **kwargs)

	def request(self, from_date, to_date, employee=None, leave_type=None, **kwargs):
		"""Create a leave request the way HR does in the console, registered for cleanup."""
		values = {"employee": employee or self.weekday_employee,
		          "leave_type": leave_type or self.excl,
		          "from_date": from_date, "to_date": to_date}
		values.update(kwargs)
		name = api.create_leave(values)
		self.addCleanup(_drop_leave, name)
		return name


def _drop_leave(name):
	"""Cancel (an approved request is submitted) and delete."""
	frappe.set_user("Administrator")
	if not frappe.db.exists("Leave Application", name):
		return
	doc = frappe.get_doc("Leave Application", name)
	if doc.docstatus == 1:
		doc.cancel()
	frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)


# --------------------------------------------------------------------------- #
# The reported case
# --------------------------------------------------------------------------- #
class TestReportedCase(LeaveDaysFixture):
	def test_leave_01_10_2026_to_30_10_2026_is_22_days_saturday_and_sunday_excluded(self):
		"""30 calendar days, 8 of them Saturdays or Sundays: 22 leave days, not 30."""
		self.assertEqual(self.days("2026-10-01", "2026-10-30"), 22)
		name = self.request("2026-10-01", "2026-10-30")
		self.assertEqual(frappe.db.get_value("Leave Application", name, "total_leave_days"), 22)
		listed = [r for r in api.list_leaves(employee=self.weekday_employee) if r.name == name]
		self.assertEqual(listed[0].total_leave_days, 22, "the console's Leave Requests list")

	def test_the_rule_is_installed_over_erpnext(self):
		module = frappe.get_module(LA_MODULE)
		self.assertIs(module.get_number_of_leave_days, leave_days.get_number_of_leave_days)
		self.assertIn(leave_days.get_number_of_leave_days, frappe.whitelisted,
		              "the Leave Application form calls it over HTTP")

	def test_saving_a_request_installs_the_rule_in_a_process_that_lacks_it(self):
		"""The rule is installed when this app is imported, and a worker can save a Leave
		Application before anything imports it (hooks come from Redis). The
		before_validate hook must install it ahead of ERPNext's count, or that worker stores
		30."""
		module = frappe.get_module(LA_MODULE)
		self.assertIn(leave_days.TARGET_FUNCTION, leave_days.UPSTREAM)
		self.addCleanup(leave_days.install_patches)
		setattr(module, leave_days.TARGET_FUNCTION, leave_days.UPSTREAM[leave_days.TARGET_FUNCTION])
		self.assertEqual(module.get_number_of_leave_days(
			self.weekday_employee, self.excl, "2026-10-01", "2026-10-30"), 30, "ERPNext's own count")
		name = self.request("2026-10-01", "2026-10-30")
		self.assertEqual(frappe.db.get_value("Leave Application", name, "total_leave_days"), 22)
		self.assertIs(module.get_number_of_leave_days, leave_days.get_number_of_leave_days)


# --------------------------------------------------------------------------- #
# Weekends — 05-10-2026 is a Monday
# --------------------------------------------------------------------------- #
class TestWeekends(LeaveDaysFixture):
	def test_monday_to_friday_is_5(self):
		self.assertEqual(self.days("2026-10-05", "2026-10-09"), 5)

	def test_monday_to_saturday_is_5(self):
		self.assertEqual(self.days("2026-10-05", "2026-10-10"), 5)

	def test_monday_to_sunday_is_5(self):
		self.assertEqual(self.days("2026-10-05", "2026-10-11"), 5)

	def test_saturday_to_sunday_is_0(self):
		self.assertEqual(self.days("2026-10-10", "2026-10-11"), 0)

	def test_saturday_to_monday_is_1(self):
		self.assertEqual(self.days("2026-10-10", "2026-10-12"), 1)

	def test_sunday_to_friday_is_5(self):
		self.assertEqual(self.days("2026-10-04", "2026-10-09"), 5)

	def test_single_saturday_is_0(self):
		self.assertEqual(self.days("2026-10-10", "2026-10-10"), 0)

	def test_single_sunday_is_0(self):
		self.assertEqual(self.days("2026-10-11", "2026-10-11"), 0)

	def test_single_monday_is_1(self):
		self.assertEqual(self.days("2026-10-12", "2026-10-12"), 1)

	def test_three_weekends_are_all_excluded(self):
		self.assertEqual(self.days("2026-10-05", "2026-10-25"), 15)   # 21 days, 6 weekend

	def test_a_full_month_counts_weekdays_only(self):
		self.assertEqual(self.days("2026-09-01", "2026-09-30"), 22)

	def test_weekends_are_excluded_even_where_holidays_count(self):
		"""A Leave Type that counts holidays as leave still never counts a weekend."""
		self.assertEqual(self.days("2026-10-01", "2026-10-30", leave_type=self.incl), 22)

	def test_from_date_after_to_date_stays_invalid(self):
		self.assertEqual(self.days("2026-10-09", "2026-10-05"), 0)
		with self.assertRaises(frappe.ValidationError):
			self.request("2026-10-09", "2026-10-05")

	def test_a_weekend_only_request_is_refused_not_stored_as_days(self):
		"""ERPNext's existing convention for a request with no leave day in it: refused at
		validation. It is never stored with the two calendar days in it."""
		before = frappe.db.count("Leave Application", {"employee": self.weekday_employee})
		with self.assertRaises(frappe.ValidationError):
			self.request("2026-10-10", "2026-10-11")
		self.assertEqual(frappe.db.count("Leave Application", {"employee": self.weekday_employee}),
		                 before)

	def test_half_days_follow_erpnext_and_only_on_a_counted_day(self):
		self.assertEqual(self.days("2026-10-12", "2026-10-12", half_day=1), 0.5)
		self.assertEqual(self.days("2026-10-05", "2026-10-09", half_day=1,
		                           half_day_date="2026-10-07"), 4.5)
		self.assertEqual(self.days("2026-10-05", "2026-10-11", half_day=1,
		                           half_day_date="2026-10-10"), 5, "half of a Saturday is nothing")


# --------------------------------------------------------------------------- #
# Shifts — payroll working time is not leave entitlement
# --------------------------------------------------------------------------- #
class TestShifts(LeaveDaysFixture):
	def test_an_employee_whose_shift_works_saturday_still_gets_22(self):
		"""Not 26 (Saturdays as days), 24.5 (Saturday mornings as halves) or 23."""
		self.assertEqual(self.days("2026-10-01", "2026-10-30", employee=self.saturday_employee), 22)
		name = self.request("2026-10-01", "2026-10-30", employee=self.saturday_employee)
		self.assertEqual(frappe.db.get_value("Leave Application", name, "total_leave_days"), 22)

	def test_an_employee_without_a_saturday_shift_gets_22(self):
		self.assertEqual(self.days("2026-10-01", "2026-10-30", employee=self.weekday_employee), 22)


# --------------------------------------------------------------------------- #
# Holidays — the Leave Type decides, as ERPNext always did; the list is resolved by
# work_calendar (Shift Type, then Employee, then Company)
# --------------------------------------------------------------------------- #
class TestHolidays(LeaveDaysFixture):
	def test_a_weekday_holiday_is_not_a_leave_day_when_the_type_excludes_holidays(self):
		# November: 21 weekdays; the Monday holiday is out, the Saturday one was never in.
		self.assertEqual(self.days("2026-11-01", "2026-11-30"), 20)

	def test_a_holiday_counts_when_the_type_includes_holidays(self):
		self.assertEqual(self.days("2026-11-01", "2026-11-30", leave_type=self.incl), 21)

	def test_the_shift_holiday_list_applies_to_an_employee_with_no_list_of_their_own(self):
		self.assertFalse(frappe.db.get_value("Employee", self.saturday_employee, "holiday_list"))
		self.assertEqual(self.days("2026-11-01", "2026-11-30", employee=self.saturday_employee), 20)

	def test_a_single_holiday_request_is_0(self):
		self.assertEqual(self.days("2026-11-02", "2026-11-02"), 0)


# --------------------------------------------------------------------------- #
# Balance — requested, approved and deducted are the same number
# --------------------------------------------------------------------------- #
class TestBalance(LeaveDaysFixture):
	def test_approving_22_days_deducts_22_from_the_balance(self):
		name = self.request("2026-10-01", "2026-10-30")
		api.approve_leave(name)
		self.assertEqual(frappe.db.get_value("Leave Application", name, ["status", "total_leave_days"]),
		                 ("Approved", 22))
		ledger = frappe.get_all("Leave Ledger Entry", fields=["leaves"], filters={
			"transaction_type": "Leave Application", "transaction_name": name, "docstatus": 1})
		self.assertEqual(sum(r.leaves for r in ledger), -22, "the balance deduction")
		taken = frappe.get_attr(LA_MODULE + ".get_leaves_for_period")
		self.assertEqual(taken(self.weekday_employee, self.excl, "2026-10-01", "2026-10-31"), -22)
		# part of the leave, recounted the way balances and reports do: 01-15 Oct = 11
		self.assertEqual(taken(self.weekday_employee, self.excl, "2026-10-01", "2026-10-15"), -11)

	def test_self_service_preview_and_balance_use_the_same_count(self):
		frappe.set_user(self.ess_user)
		preview = ess.leave_preview(self.excl, "2026-10-01", "2026-10-30")
		self.assertEqual(preview["days"], 22)
		self.assertEqual(preview["balance_before"] - preview["balance_after"], 22)

	def test_the_whitelisted_api_returns_22(self):
		"""What the Desk Leave Application form calls to fill its Total Leave Days."""
		from frappe.handler import execute_cmd

		# the handler checks the HTTP method of the request it serves: a GET, as the form's
		# Put back exactly what was there: leaving request = None behind (rather than no
		# request at all) breaks every later frappe.get_print, which reads request.path.
		if hasattr(frappe.local, "request"):
			self.addCleanup(setattr, frappe.local, "request", frappe.local.request)
		else:
			self.addCleanup(lambda: frappe.local.__delattr__("request")
			                if hasattr(frappe.local, "request") else None)
		frappe.local.request = frappe._dict(method="GET")
		frappe.local.form_dict = frappe._dict(
			cmd=LA_MODULE + ".get_number_of_leave_days", employee=self.weekday_employee,
			leave_type=self.excl, from_date="2026-10-01", to_date="2026-10-30")
		self.assertEqual(execute_cmd(frappe.local.form_dict.cmd), 22)

	def test_a_stored_request_is_not_rewritten_until_its_workflow_saves_it(self):
		"""A request stored under the old count keeps it; approving recounts it."""
		name = self.request("2026-10-01", "2026-10-30")
		frappe.db.set_value("Leave Application", name, "total_leave_days", 30,
		                    update_modified=False)                  # as stored before the fix
		listed = [r for r in api.list_leaves(employee=self.weekday_employee) if r.name == name]
		self.assertEqual(listed[0].total_leave_days, 30)
		api.approve_leave(name)
		self.assertEqual(frappe.db.get_value("Leave Application", name, "total_leave_days"), 22)
