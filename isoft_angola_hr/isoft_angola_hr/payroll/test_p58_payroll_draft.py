# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The payroll preview's draft (HR, November 2026).

HR fills the preview — overtime hours, bonuses, advances, holiday and Christmas amounts,
who is included — over more than a day, and everything lived only in the browser tab:
closing the ERP lost it. Isoft Payroll Draft keeps those edits on the server, one per
company and period, until the salary slips are created or the draft is discarded.
"""

import frappe

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestPayrollDraft(ControlFixture):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.sql("delete from `tabIsoft Payroll Draft` where company=%s", self.company)

	def as_user(self, key, fn, *args, **kwargs):
		frappe.set_user(self.users[key])
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def payload(self, hours="6"):
		return {
			"inputs": {self.employee.name: {"overtime_hours": hours, "productivity_bonus": 15000,
			                                 "adiantamento": 0, "ferias_amount": 0, "natal_amount": 0}},
			"excluded": ["HR-EMP-NOT-THIS-MONTH"],
			"filters": {"validate_attendance": 1, "based_on_timesheet": 0},
		}

	def save(self, key="officer", **kw):
		p = self.payload(**kw)
		return self.as_user(key, api.save_payroll_draft, self.company, str(self.start), str(self.end),
		                    frappe.as_json(p["inputs"]), frappe.as_json(p["excluded"]),
		                    frappe.as_json(p["filters"]))

	def load(self, key="officer"):
		return self.as_user(key, api.get_payroll_draft, self.company, str(self.start), str(self.end))

	def test_nothing_saved_means_no_draft(self):
		self.assertIsNone(self.load())

	def test_a_saved_draft_comes_back(self):
		self.save()
		draft = self.load()
		self.assertEqual(draft["inputs"][self.employee.name]["overtime_hours"], "6")
		self.assertEqual(draft["inputs"][self.employee.name]["productivity_bonus"], 15000)
		self.assertEqual(draft["excluded"], ["HR-EMP-NOT-THIS-MONTH"])
		self.assertEqual(draft["filters"]["validate_attendance"], 1)
		self.assertEqual(draft["saved_by"], self.users["officer"])
		self.assertTrue(draft["saved_at"])

	def test_saving_again_updates_the_one_draft(self):
		self.save()
		self.save(hours="9")
		self.assertEqual(frappe.db.count("Isoft Payroll Draft", {"company": self.company,
		                                                         "start_date": self.start}), 1)
		self.assertEqual(self.load()["inputs"][self.employee.name]["overtime_hours"], "9")

	def test_another_preparer_sees_and_continues_it(self):
		self.save("officer")
		self.assertIsNotNone(self.load("both"))

	def test_discarding_removes_it(self):
		self.save()
		self.as_user("officer", api.discard_payroll_draft, self.company, str(self.start), str(self.end))
		self.assertIsNone(self.load())

	def test_creating_the_slips_clears_it(self):
		self.save()
		rows = [{"employee": self.employee.name, "employee_name": self.employee.employee_name,
		         "overtime_hours": "0", "productivity_bonus": 0, "adiantamento": 0,
		         "subsidio_ferias": 0, "subsidio_natal": 0}]
		res = self.as_user("officer", api.create_payroll_from_preview, self.company, str(self.start),
		                   str(self.end), frappe.as_json(rows))
		self.addCleanup(frappe.db.sql, "delete from `tabIsoft Payroll Employee` where parent=%s", res["name"])
		self.addCleanup(frappe.db.sql, "delete from `tabIsoft Payroll Entry` where name=%s", res["name"])
		self.assertIsNone(self.load())

	def test_only_a_preparer_may_save_or_discard(self):
		with self.assertRaises(frappe.PermissionError):
			self.save("employee_only")
		with self.assertRaises(frappe.PermissionError):
			self.save("finance")
