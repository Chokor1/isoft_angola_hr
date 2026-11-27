# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Production, November 2026: payroll run IPE-03034 was cancelled, and the same period could
not be calculated again. The new slips collided with the names of the cancelled ones
("Duplicate Name: Isoft Salary Slip ISS-HR-EMP-00123-2026-10-23 already exists"). A cancelled
slip keeps its name for good, so a new slip for the period now takes the next free -2, -3….
That fallback is only ever reached because of cancelled slips. A second live slip for the
same employee and period is still refused.
"""

import frappe

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

SLIP = "Isoft Salary Slip"


class TestRecalculateAfterCancel(ControlFixture):
	def base_name(self):
		return "ISS-{0}-{1}".format(self.employee.name, frappe.utils.getdate(self.start))

	def cancelled_run(self):
		"""A run whose slips were submitted and then cancelled with cancel_payroll_entry."""
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		entry.submit_salary_slips()
		api.cancel_payroll_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def new_run_slip(self, group=None):
		entry = self.calculated_entry(group=group)
		return frappe.get_doc(SLIP, entry.employees[0].salary_slip)

	def test_the_period_is_calculated_again_after_cancel_payroll_entry(self):
		old = self.cancelled_run()
		old_slip = old.employees[0].salary_slip
		self.assertEqual(old_slip, self.base_name())
		self.assertEqual(frappe.db.get_value(SLIP, old_slip, "docstatus"), 2)

		slip = self.new_run_slip()
		self.assertEqual(slip.name, self.base_name() + "-2")
		self.assertEqual(slip.docstatus, 0)
		self.assertNotEqual(slip.payroll_entry, old.name)
		# the cancelled slip is untouched
		self.assertEqual(frappe.db.get_value(SLIP, old_slip, ["docstatus", "payroll_entry"]),
		                 (2, old.name))

	def test_the_next_free_suffix_is_used_when_minus_one_and_two_are_taken(self):
		# Production renamed the cancelled slips to …-1 to unblock the run; with the base,
		# -1 and -2 all held by cancelled slips the new slip takes -3.
		self.cancelled_run()
		base = self.base_name()
		frappe.rename_doc(SLIP, base, base + "-1", force=True)
		self.cancelled_run()
		self.assertEqual(frappe.db.get_value(SLIP, base, "docstatus"), 2)
		frappe.rename_doc(SLIP, base, base + "-2", force=True)
		self.cancelled_run()
		self.assertEqual([frappe.db.get_value(SLIP, n, "docstatus") for n in
		                  (base, base + "-1", base + "-2")], [2, 2, 2])

		self.assertEqual(self.new_run_slip().name, base + "-3")

	def test_the_base_name_is_used_again_when_it_is_free(self):
		# The production fix left the base names free (cancelled slips now on -1).
		self.cancelled_run()
		base = self.base_name()
		frappe.rename_doc(SLIP, base, base + "-1", force=True)
		self.assertEqual(self.new_run_slip().name, base)

	def test_a_second_live_slip_for_the_period_is_still_refused(self):
		entry = self.calculated_entry()
		live = frappe.get_doc(SLIP, entry.employees[0].salary_slip)
		self.assertEqual(live.docstatus, 0)
		twin = frappe.copy_doc(live)
		twin.docstatus = 0
		with self.assertRaises(frappe.DuplicateEntryError):
			twin.insert(ignore_permissions=True)

		# submitted, and with a cancelled slip already holding the base name
		frappe.db.set_value(SLIP, live.name, "docstatus", 2, update_modified=False)
		second = self.new_run_slip(group=PREFIX + " second run")  # the first run is still open
		self.assertEqual(second.name, self.base_name() + "-2")
		frappe.db.set_value(SLIP, second.name, "docstatus", 1, update_modified=False)
		third = frappe.copy_doc(second)
		third.docstatus = 0
		with self.assertRaises(frappe.DuplicateEntryError):
			third.insert(ignore_permissions=True)

	def test_a_live_run_reuses_its_slip_instead_of_creating_another(self):
		entry = self.calculated_entry()
		first = entry.employees[0].salary_slip
		frappe.set_user(self.users["officer"])
		entry.create_salary_slips()
		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.employees[0].salary_slip, first)
		self.assertEqual(frappe.db.count(SLIP, {"employee": self.employee.name,
		                                        "start_date": self.start}), 1)
