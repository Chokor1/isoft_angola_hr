# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Each employee's own salary-advance account (Finance, November 2026).

ITEC keeps one advance account per employee (group 363 – Pessoal – Adiantamentos) and no
single "Salary Advance" account, so the ADT mapping in Settings stayed empty and blocked
payroll. Employee.custom_salary_advance_account now carries the employee's own account:
paying an advance debits it, recovering it in payroll credits it. The ADT mapping is the
fallback for an employee without one, and is no longer required on its own.
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture, _account
from isoft_angola_hr.isoft_angola_hr.services import advances, payroll_accounts
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestEmployeeAdvanceAccount(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.own = _account(cls.company, PREFIX + " Adiantamento Pessoal", "Liability")
		cls._created.append(("Account", cls.own))
		frappe.db.set_value("Isoft HR Settings", None, engine.COMPONENT_TOGGLES["ADT"], 1)
		frappe.clear_cache(doctype="Isoft HR Settings")

	def setUp(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_salary_advance_account", None)

	def tearDown(self):
		self.set_adt_mapping(self.acc["ADT"])
		for name in frappe.db.sql_list("select name from `tabIsoft Salary Slip` where employee=%s",
		                               self.employee.name):
			frappe.db.sql("delete from `tabIsoft Salary Detail` where parent=%s", name)
			frappe.db.sql("delete from `tabIsoft Payroll Employee` where salary_slip=%s", name)
			frappe.db.sql("delete from `tabIsoft Salary Slip` where name=%s", name)

	def set_adt_mapping(self, account):
		s = frappe.get_single("Isoft HR Settings")
		for r in s.component_accounts:
			if r.abbr == "ADT":
				r.account = account
		with payroll_accounts.unlogged_mapping():
			s.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Isoft HR Settings")

	def adt_credit(self, slip):
		je = frappe.get_doc("Journal Entry", api.make_journal_entry(slip.name))
		return [(a.account, flt(a.credit_in_account_currency)) for a in je.accounts
		        if flt(a.credit_in_account_currency) == flt(slip.adiantamento)]

	def test_the_employee_form_has_the_field(self):
		df = frappe.get_meta("Employee").get_field("custom_salary_advance_account")
		self.assertEqual((df.fieldtype, df.options), ("Link", "Account"))

	def test_payroll_recovers_the_advance_on_the_employees_own_account(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_salary_advance_account", self.own)
		self.set_adt_mapping(None)
		slip = self.make_slip(adiantamento=5000)
		self.assertIn((self.own, 5000.0), self.adt_credit(slip))

	def test_without_an_own_account_the_adt_mapping_is_used(self):
		slip = self.make_slip(adiantamento=5000)
		self.assertIn((self.acc["ADT"], 5000.0), self.adt_credit(slip))

	def test_with_neither_the_posting_is_refused(self):
		self.set_adt_mapping(None)
		slip = self.make_slip(adiantamento=5000)
		self.assertTrue([p for p in payroll_accounts.accrual_problems([slip]) if "ADT" in p["key"]])

	def test_an_empty_adt_mapping_no_longer_blocks_finance(self):
		self.set_adt_mapping(None)
		checklist = payroll_accounts.finance_checklist(self.company)
		items = {i["key"]: i for i in (checklist.get("items") if isinstance(checklist, dict) else checklist)}
		self.assertTrue(items["advance"]["ok"])
		self.assertNotIn("Adiantamento", items["mapping_complete"]["detail"])

	def test_paying_an_advance_debits_the_employees_own_account(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_salary_advance_account", self.own)
		adv = frappe._dict(advance_account=None, employee=self.employee.name)
		self.assertEqual(advances._advance_account(adv, frappe.get_single("Isoft HR Settings")), self.own)
