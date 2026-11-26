# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Finance, November 2026: some employees are paid from a different bank, so an employee
can be taken off a payment entry. The entry is cancelled and re-issued for everybody
else (same bank, date and reference); the employee removed is unpaid and is then paid
from the other bank's account. The run screen shows each employee's bank.
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, _account
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestRemoveFromPayment(ControlFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.second = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Outro Banco",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls.second_profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.second.name, "company": cls.company,
			"from_date": "2020-01-01", "base": 150000, "food_allowance": 20000,
			"transport_allowance": 20000,
		}).insert(ignore_permissions=True))
		frappe.db.set_value("Employee", cls.second.name, {
			"custom_iban": "AO06000600000100037131174", "custom_nif": "5417000005",
			"custom_inss_number": "0123456784"})
		cls.other_bank = _account(cls.company, PREFIX + " Banco BFA", "Asset")
		cls._created.append(("Account", cls.other_bank))

	def released_entry(self):
		entry = self.make_entry(group=PREFIX + " remove payment", employees=[
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

	def paid_entry(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		res = api.make_bulk_payment_entry(entry.name, bank_reference="REF-NOV")
		frappe.set_user("Administrator")
		entry.reload()
		return entry, res["journal_entry"]

	def remove(self, entry, employees):
		frappe.set_user(self.users["finance"])
		try:
			return api.remove_from_payment(entry.name, frappe.as_json(employees))
		finally:
			frappe.set_user("Administrator")

	def by_employee(self, entry):
		return {s.employee: s for s in self.slips(entry)}

	def test_removing_an_employee_reissues_the_payment_for_the_others(self):
		entry, old = self.paid_entry()
		self.assertEqual(wf.state_of(entry), wf.PAID)
		res = self.remove(entry, [self.second.name])
		self.assertEqual(res["removed"], 1)
		self.assertEqual(res["cancelled"], [old])
		self.assertEqual(frappe.db.get_value("Journal Entry", old, "docstatus"), 2)
		self.assertEqual(res["status"], wf.PAYMENT_READY)

		slips = self.by_employee(entry)
		self.assertIsNone(slips[self.second.name].payment_entry)
		new = frappe.get_doc("Journal Entry", res["journal_entries"][0])
		self.assertEqual(slips[self.employee.name].payment_entry, new.name)
		self.assertEqual((new.docstatus, new.cheque_no, str(new.posting_date)),
		                 (1, "REF-NOV", str(frappe.db.get_value("Journal Entry", old, "posting_date"))))
		credit = [a for a in new.accounts if flt(a.credit_in_account_currency)]
		self.assertEqual([a.account for a in credit], [self.bank])
		self.assertAlmostEqual(flt(credit[0].credit_in_account_currency),
		                       flt(slips[self.employee.name].net_pay), places=2)

	def test_the_removed_employee_is_then_paid_from_the_other_bank(self):
		entry, _old = self.paid_entry()
		self.remove(entry, [self.second.name])
		frappe.set_user(self.users["finance"])
		res = api.make_bulk_payment_entry(entry.name, payment_account=self.other_bank,
		                                  employees=frappe.as_json([self.second.name]))
		frappe.set_user("Administrator")
		self.assertEqual(res["status"], wf.PAID)
		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		self.assertIn(self.other_bank, [a.account for a in je.accounts if flt(a.credit_in_account_currency)])

	def test_removing_everybody_leaves_no_payment(self):
		entry, old = self.paid_entry()
		res = self.remove(entry, [self.employee.name, self.second.name])
		self.assertEqual((res["removed"], res["journal_entries"]), (2, []))
		self.assertEqual([s.payment_entry for s in self.slips(entry)], [None, None])

	def test_nothing_paid_nothing_removed(self):
		entry = self.released_entry()
		with self.assertRaises(frappe.ValidationError):
			self.remove(entry, [self.second.name])

	def test_an_hr_user_cannot_remove(self):
		entry, _old = self.paid_entry()
		frappe.set_user(self.users["officer"])
		try:
			with self.assertRaises(frappe.PermissionError):
				api.remove_from_payment(entry.name, frappe.as_json([self.second.name]))
		finally:
			frappe.set_user("Administrator")

	def test_the_run_screen_shows_each_employees_bank(self):
		entry = self.released_entry()
		frappe.db.set_value("Employee", self.second.name, "bank_name", "")
		bank = lambda: {r["employee"]: r for r in api.get_payroll_entry(entry.name)["employees"]}[
			self.second.name]["bank"]
		self.assertEqual(bank(), "IBAN 0006")  # from the IBAN's bank code
		frappe.db.set_value("Employee", self.second.name, "bank_name", "bfa")
		self.assertEqual(bank(), "BFA")

	def test_the_button_is_offered_on_a_paid_run(self):
		entry, _old = self.paid_entry()
		self.assertEqual(wf.state_of(entry), wf.PAID)
		frappe.set_user(self.users["finance"])
		try:
			flags = wf.finance_flags(frappe.get_doc("Isoft Payroll Entry", entry.name))
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(flags["can_remove_from_payment"])
		self.assertFalse(flags["can_pay"])
		frappe.set_user(self.users["officer"])
		try:
			flags = wf.finance_flags(frappe.get_doc("Isoft Payroll Entry", entry.name))
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(flags["can_remove_from_payment"])
