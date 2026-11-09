# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Finance, November 2026: paying a payroll posts ONE bank Journal Entry for the whole run
— a debit per employee's payable, one credit to the bank for the total — instead of one
entry per employee (81 entries CBL2026/756…836 for the October run).
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestOnePaymentEntryPerRun(ControlFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.second = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Segundo",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls.second_profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.second.name, "company": cls.company,
			"from_date": "2020-01-01", "base": 150000, "food_allowance": 20000,
			"transport_allowance": 20000,
		}).insert(ignore_permissions=True))
		frappe.db.set_value("Employee", cls.second.name, {
			"custom_iban": "AO06000600000100037131174", "custom_nif": "5417000003",
			"custom_inss_number": "0123456782"})

	def released_entry(self):
		entry = self.make_entry(group=PREFIX + " one payment", employees=[
			{"employee": self.employee.name, "employee_name": self.employee.employee_name,
			 "salary_profile": self.profile.name},
			{"employee": self.second.name, "employee_name": self.second.employee_name,
			 "salary_profile": self.second_profile.name}])
		frappe.set_user(self.users["officer"])
		entry.create_salary_slips()
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["manager"])
		api.payroll_action(entry.name, wf.APPROVE)
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		api.make_bulk_journal_entry(entry.name)
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def slips(self, entry):
		return [frappe.get_doc("Isoft Salary Slip", r.salary_slip) for r in entry.employees]

	def test_paying_the_run_posts_one_entry(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		res = api.make_bulk_payment_entry(entry.name)
		frappe.set_user("Administrator")
		self.assertEqual(res["status"], wf.PAID)
		self.assertEqual(res["created"], 2)
		slips = self.slips(entry)
		self.assertEqual(len({s.payment_entry for s in slips}), 1)
		self.assertEqual(res["journal_entry"], slips[0].payment_entry)

		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		self.assertEqual((je.docstatus, je.voucher_type), (1, "Bank Entry"))
		debits = sorted(flt(a.debit_in_account_currency) for a in je.accounts if flt(a.debit_in_account_currency))
		credits = [a for a in je.accounts if flt(a.credit_in_account_currency)]
		self.assertEqual(debits, sorted(flt(s.net_pay) for s in slips))
		self.assertEqual(len(credits), 1)
		self.assertEqual(credits[0].account, self.bank)
		self.assertAlmostEqual(flt(credits[0].credit_in_account_currency),
		                       sum(flt(s.net_pay) for s in slips), places=2)

	def test_cancelling_the_entry_unpays_every_slip(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		name = api.make_bulk_payment_entry(entry.name)["journal_entry"]
		frappe.set_user("Administrator")
		je = frappe.get_doc("Journal Entry", name)
		je.flags.ignore_permissions = True
		je.cancel()
		self.assertEqual([s.payment_entry for s in self.slips(entry)], [None, None])

	def test_a_single_slip_can_still_be_paid_on_its_own(self):
		entry = self.released_entry()
		slip = self.slips(entry)[0]
		frappe.set_user(self.users["finance"])
		name = api.make_payment_entry(slip.name)
		frappe.set_user("Administrator")
		je = frappe.get_doc("Journal Entry", name)
		self.assertEqual(len(je.accounts), 2)
		self.assertEqual(flt(je.total_debit), flt(slip.net_pay))
