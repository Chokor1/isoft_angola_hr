# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Payroll Finance Approver can do its job — and only its job.

The app creates this role specifically to post payroll and disburse advances
(``PAYROLL_POST``, ``PAYROLL_CONFIRM_PAYMENT`` and ``ADVANCE_DISBURSE`` are all
finance-side in ``permissions.ACTION_ROLES``), but granted it no accounting access at
all. Every one of those actions builds a Journal Entry, so the role could not complete a
single action it was created for.

The fix is deliberately narrow and these tests pin both halves of it:

  * the role CAN post payroll and disburse an advance through the payroll flow;
  * the role still CANNOT open Journal Entry or Payment Entry and post to the ledger
    directly, and cannot write to the chart of accounts.

If a later change grants the role blanket accounting rights to "fix" something, the
negative tests below fail.
"""

import frappe
from frappe.utils import flt, nowdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr import hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

FINANCE = "Payroll Finance Approver"


class FinanceAccessFixture(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.role_users = {}
		for role in (FINANCE, "Accounts Manager", "Payroll Officer", "HR Manager",
		             "Payroll Manager"):
			email = "fin.%s@example.invalid" % role.lower().replace(" ", "")
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email,
					"first_name": PREFIX + " " + role, "send_welcome_email": 0,
					"roles": [{"role": role}],
				}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.role_users[role] = email

	def as_role(self, role):
		frappe.set_user(self.role_users[role])
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def approved_advance(self):
		adv = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"company": self.company, "request_date": nowdate(),
			"requested_amount": 25000, "approved_amount": 25000,
			"installments": 1, "installment_amount": 25000,
			"recovery_start_date": nowdate(), "reason": PREFIX + " finance",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("Isoft Salary Advance", adv.name,
		                    {"status": "Approved", "requested_by": "Administrator"})
		return adv.name


class TestFinanceCanDoItsJob(FinanceAccessFixture):
	def test_finance_approver_can_post_the_payroll_accrual(self):
		slip = self.make_slip()
		self.as_role(FINANCE)
		je = api.make_journal_entry(slip.name)

		self.assertTrue(je)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1,
		                 "the accrual must be submitted, not left in draft")

	def test_finance_approver_can_disburse_an_advance(self):
		name = self.approved_advance()
		self.as_role(FINANCE)
		hr_api.perform_document_action("Isoft Salary Advance", name, "disburse")

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Isoft Salary Advance", name, "status"),
		                 "Disbursed")
		self.assertTrue(frappe.db.get_value("Isoft Salary Advance", name,
		                                    "disbursement_entry"))

	def test_accounts_manager_can_still_disburse(self):
		name = self.approved_advance()
		self.as_role("Accounts Manager")
		hr_api.perform_document_action("Isoft Salary Advance", name, "disburse")

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Isoft Salary Advance", name, "status"),
		                 "Disbursed")


class TestFinanceGotNothingElse(FinanceAccessFixture):
	"""The grant must be the minimum. These are the tests that stop it widening."""

	def test_finance_approver_cannot_post_to_the_ledger_directly(self):
		self.as_role(FINANCE)
		self.assertFalse(frappe.has_permission("Journal Entry", "create"),
		                 "posting payroll must not become blanket accounting access")
		self.assertFalse(frappe.has_permission("Journal Entry", "submit"))
		self.assertFalse(frappe.has_permission("Payment Entry", "create"))

	def test_finance_approver_may_read_but_not_change_the_chart_of_accounts(self):
		self.as_role(FINANCE)
		self.assertTrue(frappe.has_permission("Account", "read"),
		                "reading account balances is what the voucher validation needs")
		self.assertFalse(frappe.has_permission("Account", "write"))
		self.assertFalse(frappe.has_permission("Account", "create"))

	def test_a_hand_written_journal_entry_is_refused(self):
		# The capability layer authorises payroll vouchers; it does not authorise the user
		# to invent one of their own.
		self.as_role(FINANCE)
		je = frappe.get_doc({
			"doctype": "Journal Entry", "voucher_type": "Journal Entry",
			"company": self.company, "posting_date": nowdate(),
			"accounts": [
				{"account": self.acc["SB"], "debit_in_account_currency": 100},
				{"account": self.payable, "credit_in_account_currency": 100},
			],
		})
		with self.assertRaises(frappe.PermissionError):
			je.insert()


class TestOnlyFinancePosts(FinanceAccessFixture):
	def test_hr_and_payroll_roles_cannot_post_payroll(self):
		slip = self.make_slip()
		for role in ("HR Manager", "Payroll Officer", "Payroll Manager"):
			with self.subTest(role=role):
				self.as_role(role)
				with self.assertRaises(Exception):
					api.make_journal_entry(slip.name)
				frappe.set_user("Administrator")

	def test_hr_and_payroll_roles_cannot_disburse(self):
		for role in ("HR Manager", "Payroll Officer", "Payroll Manager"):
			with self.subTest(role=role):
				name = self.approved_advance()
				self.as_role(role)
				with self.assertRaises(Exception):
					hr_api.perform_document_action("Isoft Salary Advance", name, "disburse")
				frappe.set_user("Administrator")

	def test_the_capability_matrix_still_says_finance_only(self):
		self.assertEqual(
			perms.ACTION_ROLES[perms.ADVANCE_DISBURSE],
			frozenset({FINANCE, "Accounts Manager"}))


class TestFinanceCanFindWhatItMustAct(FinanceAccessFixture):
	"""A role that may act on a record must be able to list it.

	Regression: list_advances was guarded on advance.request, which Payroll Finance
	Approver does not hold — so the disburser could disburse through the API but the
	screen they were meant to work from returned a permission error and came up empty.
	"""

	def test_the_disburser_can_list_advances(self):
		self.approved_advance()
		self.as_role(FINANCE)
		rows = hr_api.list_advances(company=self.company)
		self.assertTrue(rows, "the disburser must be able to see what it has to disburse")

	def test_the_approver_and_the_requester_can_still_list_advances(self):
		self.approved_advance()
		for role in ("HR Manager", "Payroll Officer"):
			with self.subTest(role=role):
				self.as_role(role)
				self.assertTrue(hr_api.list_advances(company=self.company))
				frappe.set_user("Administrator")

	def test_a_role_with_no_advance_capability_is_still_refused(self):
		email = "noadv." + PREFIX.lower().replace(" ", "") + "@example.invalid"
		if not frappe.db.exists("User", email):
			u = frappe.get_doc({
				"doctype": "User", "email": email, "first_name": PREFIX + " NoAdv",
				"send_welcome_email": 0, "roles": [{"role": "Employee"}],
			}).insert(ignore_permissions=True)
			self._created.append(("User", u.name))
		frappe.set_user(email)
		try:
			with self.assertRaises(frappe.PermissionError):
				hr_api.list_advances()
		finally:
			frappe.set_user("Administrator")


class TestEmployerAccountsAreRequired(FinanceAccessFixture):
	"""A component with no GL account must be a BLOCKING readiness item.

	The live site ran with the employer social-security accounts (CTSSE, CTSSP) unmapped.
	Readiness did report it, and these tests keep that true: an unmapped component is the
	difference between posting payroll and a failed posting halfway through a run.
	"""

	def test_every_journal_component_is_checked(self):
		from isoft_angola_hr.isoft_angola_hr.payroll import engine
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as pr

		keys = {i["key"] for i in pr.configuration_status(company=self.company)}
		for comp in engine.journal_components():
			self.assertIn("account:" + comp["abbr"], keys,
			              "%s has no readiness check, so a missing account would be "
			              "invisible until posting failed" % comp["abbr"])

	def test_the_employer_social_security_components_are_checked(self):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as pr

		keys = {i["key"] for i in pr.configuration_status(company=self.company)}
		self.assertIn("account:CTSSE", keys)
		self.assertIn("account:CTSSP", keys)

	def test_an_unmapped_component_is_reported_as_blocking(self):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as pr

		settings = frappe.get_single("Isoft HR Settings")
		row = next((r for r in settings.component_accounts if r.abbr == "CTSSE"), None)
		self.assertIsNotNone(row, "the fixture maps CTSSE")
		previous = row.account
		frappe.db.set_value("Isoft Component Account", row.name, "account", None)
		frappe.clear_cache(doctype="Isoft HR Settings")
		self.addCleanup(lambda: (
			frappe.db.set_value("Isoft Component Account", row.name, "account", previous),
			frappe.clear_cache(doctype="Isoft HR Settings")))

		item = next(i for i in pr.configuration_status(company=self.company)
		            if i["key"] == "account:CTSSE")
		self.assertFalse(item["ok"])
		self.assertEqual(item["severity"], pr.BLOCKING)

	def test_the_employer_contribution_reaches_both_sides_of_the_ledger(self):
		"""Employer social security is a cost AND a liability — never a pay deduction."""
		slip = self.make_slip()
		self.as_role(FINANCE)
		je_name = api.make_journal_entry(slip.name)
		frappe.set_user("Administrator")

		gl = frappe.get_all("GL Entry", filters={"voucher_no": je_name},
		                    fields=["account", "debit", "credit"])
		self.assertAlmostEqual(sum(flt(g.debit) for g in gl),
		                       sum(flt(g.credit) for g in gl), places=2)

		employer = flt(slip.ss_employer_amount)
		self.assertGreater(employer, 0, "the fixture must produce an employer contribution")
		debited = sum(flt(g.debit) for g in gl if g.account == self.acc["CTSSE"])
		credited = sum(flt(g.credit) for g in gl if g.account == self.acc["CTSSP"])
		self.assertAlmostEqual(debited, employer, places=2)
		self.assertAlmostEqual(credited, employer, places=2)
		# and it did not reduce what the employee is paid
		self.assertAlmostEqual(flt(slip.gross_pay) - flt(slip.total_deduction),
		                       flt(slip.net_pay), places=2)
