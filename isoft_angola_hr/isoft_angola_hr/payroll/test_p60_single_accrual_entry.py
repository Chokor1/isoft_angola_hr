# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Finance, November 2026: posting a payroll books ONE accrual Journal Entry for the whole
run — the totals on each expense and tax account (one line on 3491 INSS, not one per
employee), and a line per employee only on the accounts that are that employee's own
(personal 361 payable, 363 advance) — instead of one entry per employee.
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, _account
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestOneAccrualEntryPerRun(ControlFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.second = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Acrescimo",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls.second_profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.second.name, "company": cls.company,
			"from_date": "2020-01-01", "base": 150000, "food_allowance": 20000,
			"transport_allowance": 20000,
		}).insert(ignore_permissions=True))
		frappe.db.set_value("Employee", cls.second.name, {
			"custom_iban": "AO06000600000100037131174", "custom_nif": "5417000004",
			"custom_inss_number": "0123456783"})
		cls.own_payable = _account(cls.company, PREFIX + " Pessoal Acrescimo", "Liability")
		cls._created.append(("Account", cls.own_payable))

	def setUp(self):
		super().setUp()
		frappe.db.set_value("Employee", self.second.name, "custom_payroll_payable_account", None)

	def posted_entry(self):
		entry = self.make_entry(group=PREFIX + " one accrual", employees=[
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
		res = api.make_bulk_journal_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		return entry, res

	def slips(self, entry):
		return [frappe.get_doc("Isoft Salary Slip", r.salary_slip) for r in entry.employees]

	def lines(self, je, account):
		return [a for a in je.accounts if a.account == account]

	def test_posting_the_run_books_one_entry_with_totals(self):
		entry, res = self.posted_entry()
		self.assertEqual(res["status"], wf.POSTED)
		self.assertEqual(res["created"], 2)
		slips = self.slips(entry)
		self.assertEqual({s.journal_entry for s in slips}, {res["journal_entry"]})

		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		self.assertEqual(je.docstatus, 1)
		self.assertIn(entry.name, je.user_remark)
		# one line per account, carrying the run's total
		for account, side, expected in (
				(self.acc["SB"], "debit_in_account_currency",
				 sum(flt(e.amount) for s in slips for e in s.earnings if e.abbr == "SB")),
				(self.acc["CTSS3"], "credit_in_account_currency", sum(flt(s.ss_employee_amount) for s in slips)),
				(self.acc["CTSSP"], "credit_in_account_currency", sum(flt(s.ss_employer_amount) for s in slips)),
				(self.payable, "credit_in_account_currency", sum(flt(s.net_pay) for s in slips))):
			rows = self.lines(je, account)
			self.assertEqual(len(rows), 1, account)
			self.assertAlmostEqual(flt(rows[0].get(side)), expected, places=2)
		self.assertAlmostEqual(flt(je.total_debit), flt(je.total_credit), places=2)

	def test_an_employees_own_payable_account_keeps_its_own_line(self):
		frappe.db.set_value("Employee", self.second.name, "custom_payroll_payable_account", self.own_payable)
		entry, res = self.posted_entry()
		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		first, second = self.slips(entry)
		if first.employee == self.second.name:
			first, second = second, first
		self.assertEqual([flt(a.credit_in_account_currency) for a in self.lines(je, self.own_payable)],
		                 [flt(second.net_pay)])
		self.assertEqual([flt(a.credit_in_account_currency) for a in self.lines(je, self.payable)],
		                 [flt(first.net_pay)])
		self.assertEqual(len(self.lines(je, self.acc["CTSS3"])), 1)

	def test_cancelling_the_entry_unposts_every_slip(self):
		entry, res = self.posted_entry()
		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		je.flags.ignore_permissions = True
		je.cancel()
		self.assertEqual([s.journal_entry for s in self.slips(entry)], [None, None])

	def test_posting_again_creates_nothing(self):
		entry, res = self.posted_entry()
		frappe.set_user(self.users["finance"])
		again = api.make_bulk_journal_entry(entry.name)
		frappe.set_user("Administrator")
		self.assertEqual((again["created"], again["skipped"]), (0, 2))

	def test_the_run_reconciles_with_personal_payable_accounts(self):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_reconciliation as rec
		frappe.db.set_value("Employee", self.second.name, "custom_payroll_payable_account", self.own_payable)
		entry, res = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		api.make_bulk_payment_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		lines = {l["key"]: l for l in rec.reconcile(entry)["lines"]}
		self.assertTrue(lines["net_payable"]["reconciled"], lines["net_payable"])
		self.assertTrue(lines["payment"]["reconciled"], lines["payment"])
		self.assertTrue(lines["accrual_balance"]["reconciled"])

	def test_a_run_posted_per_employee_is_merged_into_one_entry(self):
		entry = self.make_entry(group=PREFIX + " one accrual", employees=[
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
		old = [api.make_journal_entry(r.salary_slip) for r in entry.employees]  # the old way
		frappe.set_user("Administrator")
		self.assertEqual(len(set(old)), 2)
		res = api.merge_accrual_entries(entry.name)
		self.assertEqual(sorted(res["cancelled"]), sorted(old))
		self.assertEqual([frappe.db.get_value("Journal Entry", n, "docstatus") for n in old], [2, 2])
		self.assertEqual({s.journal_entry for s in self.slips(entry)}, {res["journal_entry"]})
		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		self.assertEqual(len(self.lines(je, self.acc["CTSS3"])), 1)
		# merging again changes nothing
		self.assertEqual(api.merge_accrual_entries(entry.name)["cancelled"], [])

	def test_a_paid_run_is_not_merged(self):
		entry, res = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		api.make_bulk_payment_entry(entry.name)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			api.merge_accrual_entries(entry.name)

	def test_tax_accounts_stay_one_line_across_cost_centres(self):
		company_cc = frappe.db.get_value("Company", self.company, "cost_center")
		other = frappe.get_all("Cost Center", filters={"company": self.company, "is_group": 0,
		                                               "name": ("!=", company_cc)}, pluck="name", limit=1)
		if not other:
			self.skipTest("the company has a single cost centre")
		frappe.db.set_value("Employee", self.second.name, "payroll_cost_center", other[0])
		self.addCleanup(frappe.db.set_value, "Employee", self.second.name, "payroll_cost_center", None)
		entry, res = self.posted_entry()
		je = frappe.get_doc("Journal Entry", res["journal_entry"])
		for account in (self.acc["CTSS3"], self.acc["CTSSP"], self.payable):
			self.assertEqual(len(self.lines(je, account)), 1, account)
		# the cost itself is still split by cost centre
		self.assertEqual({a.cost_center for a in self.lines(je, self.acc["SB"])}, {company_cc, other[0]})
