# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Audit finding F-04: payroll account mapping integrity.

Posting reaches the ledger through one mapping (Isoft HR Settings → Account per
Component, plus the payable and the bank account). Two components were unmapped on the
ITEC site and a third had been re-pointed after a run was posted. These tests pin the
controls that now stand between the mapping and the ledger:

* a missing or unusable account never blocks the *calculation* — HR keeps working;
* it blocks *posting*, before any Journal Entry exists, naming the component, the
  account type it needs and where it is configured;
* a bulk run with one bad account posts nothing at all — never half a payroll;
* a wrong account nature is refused even when Settings was bypassed;
* the payment is refused without a usable payable and bank account;
* history is untouched: a mapping change after posting rewrites no voucher and is
  reported as drift, not silently as a shortfall;
* readiness and the Finance checklist say exactly what to configure.

Nothing here chooses an account; the fixture maps its own test accounts and the tests
only ever unmap or mis-map them inside a savepoint.

SAFETY — the PayrollFixture contract: every record is prefixed ``_TEST AHR``, registered
for deletion, and each test runs inside a savepoint. The real Settings mapping is backed
up and restored by the fixture.
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (
	PREFIX,
	PayrollFixture,
	free_account_number,
)
from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.services import payroll_reconciliation as recon
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class AccountsFixture(PayrollFixture):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# A second employee, so a run can hold two slips (one slip per employee and period).
		cls.employee_b = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Second",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1991-01-01",
		}).insert(ignore_permissions=True))
		cls.profile_b = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.employee_b.name,
			"company": cls.company, "from_date": "2020-01-01",
			"base": 150000, "food_allowance": 20000, "transport_allowance": 20000,
		}).insert(ignore_permissions=True))

	def second_slip(self, **kwargs):
		values = {
			"employee": self.employee_b.name, "company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end, "salary_profile": self.profile_b.name,
		}
		values.update(kwargs)
		return self.insert_slip(values, submit=True)

	def unmap(self, abbr):
		"""Remove one component's account from Settings (the way an admin clearing the
		row would). Rolled back with the test."""
		frappe.db.sql(
			"""update `tabIsoft Component Account` set account = NULL
			where parent = 'Isoft HR Settings' and abbr = %s""", abbr)
		frappe.clear_cache(doctype="Isoft HR Settings")

	def remap(self, abbr, account):
		"""Point a component at another account WITHOUT the Settings validation, as a
		direct database edit would."""
		frappe.db.sql(
			"""update `tabIsoft Component Account` set account = %s
			where parent = 'Isoft HR Settings' and abbr = %s""", (account, abbr))
		frappe.clear_cache(doctype="Isoft HR Settings")

	def set_setting(self, field, value):
		frappe.db.set_single_value("Isoft HR Settings", field, value)
		frappe.clear_cache(doctype="Isoft HR Settings")

	def slip_with_advance(self, submit=True):
		return self.make_slip(submit=submit, adiantamento=15000)

	def je_lines(self, je_name):
		return frappe.db.sql(
			"""select account, debit, credit from `tabJournal Entry Account`
			where parent = %s order by idx""", je_name, as_dict=True)


# --------------------------------------------------------------------------- #
# Missing accounts: calculation allowed, posting blocked
# --------------------------------------------------------------------------- #
class TestMissingAccounts(AccountsFixture):

	def test_missing_advance_account_allows_calculation_and_blocks_posting(self):
		self.unmap("ADT")
		slip = self.slip_with_advance()                 # calculated and submitted: fine
		self.assertEqual(slip.docstatus, 1)
		self.assertTrue(any(d.abbr == "ADT" for d in slip.deductions))
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip.name)
		msg = str(cm.exception)
		self.assertIn("Cannot post payroll", msg)
		self.assertIn("Salary Advance", msg)
		self.assertIn("not configured", msg)
		self.assertIn("Liability or Asset", msg)
		self.assertIn("Isoft HR Settings", msg)
		self.assertFalse(frappe.db.get_value("Isoft Salary Slip", slip.name, "journal_entry"))
		self.assertFalse(frappe.db.exists("Journal Entry", {"user_remark": "Payroll: " + slip.name,
		                                                    "creation": (">=", self.test_started)}))

	def test_missing_employer_inss_account_allows_calculation_and_blocks_posting(self):
		self.unmap("CTSSE")
		slip = self.make_slip()
		self.assertGreater(flt(slip.ss_employer_amount), 0)
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip.name)
		msg = str(cm.exception)
		self.assertIn("Employer INSS", msg)
		self.assertIn("Expense", msg)
		self.assertIn("Account per Component", msg)
		self.assertFalse(frappe.db.exists("Journal Entry", {"user_remark": "Payroll: " + slip.name,
		                                                    "creation": (">=", self.test_started)}))

	def test_a_slip_without_an_advance_does_not_need_the_advance_account(self):
		"""Only the components on the slip are required — an unmapped ADT does not stop a
		payroll in which nobody has an advance."""
		self.unmap("ADT")
		slip = self.make_slip()
		je = api.make_journal_entry(slip.name)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1)

	def test_bulk_posting_with_one_missing_account_posts_nothing(self):
		"""Previously the run posted employee by employee and failed on the first one that
		needed the missing account — half a payroll in the ledger."""
		self.unmap("ADT")
		plain = self.second_slip()
		with_advance = self.slip_with_advance()
		entry = frappe.get_doc("Isoft Payroll Entry", self.payroll_run())
		frappe.db.set_value("Isoft Payroll Entry", entry.name, "status", wf.POSTED, update_modified=False)
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_bulk_journal_entry(entry.name)
		self.assertIn("Salary Advance", str(cm.exception))
		for name in (plain.name, with_advance.name):
			self.assertFalse(frappe.db.get_value("Isoft Salary Slip", name, "journal_entry"),
			                 "nothing may be posted while one account is missing")

	def test_missing_payable_or_bank_blocks_the_payment_not_the_accrual(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		self.set_setting("salary_payment_account", None)
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_payment_entry(slip.name)
		self.assertIn("Cannot post the salary payment", str(cm.exception))
		self.assertIn("bank", str(cm.exception).lower())
		self.assertFalse(frappe.db.get_value("Isoft Salary Slip", slip.name, "payment_entry"))

	def test_missing_payable_blocks_the_accrual(self):
		self.set_setting("payroll_payable_account", None)
		slip = self.make_slip()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip.name)
		self.assertIn("Payroll Payable", str(cm.exception))


# --------------------------------------------------------------------------- #
# Wrong account nature
# --------------------------------------------------------------------------- #
class TestWrongAccountType(AccountsFixture):

	def test_settings_refuses_to_save_a_wrong_nature(self):
		s = frappe.get_single("Isoft HR Settings")
		for row in s.component_accounts:
			if row.abbr == "ADT":
				row.account = self.acc["SB"]            # an Expense account for a deduction
		with self.assertRaises(frappe.ValidationError):
			s.save(ignore_permissions=True)

	def test_posting_refuses_a_wrong_nature_even_when_settings_was_bypassed(self):
		self.remap("ADT", self.acc["SB"])           # Expense where Liability/Asset is required
		slip = self.slip_with_advance()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip.name)
		msg = str(cm.exception)
		self.assertIn("Salary Advance", msg)
		self.assertIn("Expense", msg)
		self.assertIn("required", msg)
		self.assertFalse(frappe.db.exists("Journal Entry", {"user_remark": "Payroll: " + slip.name,
		                                                    "creation": (">=", self.test_started)}))

	def test_posting_refuses_a_group_or_disabled_account(self):
		group = frappe.db.get_value("Account", {"company": self.company, "root_type": "Liability",
		                                        "is_group": 1}, "name")
		self.remap("CTSSP", group)
		slip = self.make_slip()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip.name)
		self.assertIn("group account", str(cm.exception))
		self.remap("CTSSP", self.acc["CTSSP"])
		frappe.db.set_value("Account", self.acc["IRT"], "disabled", 1)
		slip2 = self.second_slip(productivity_bonus=400000)   # enough to owe IRT
		self.assertTrue(any(d.abbr == "IRT" for d in slip2.deductions))
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_journal_entry(slip2.name)
		self.assertIn("disabled", str(cm.exception))


# --------------------------------------------------------------------------- #
# All accounts configured
# --------------------------------------------------------------------------- #
class TestFullyMapped(AccountsFixture):

	def test_posting_succeeds_balanced_and_on_the_right_sides(self):
		slip = self.slip_with_advance()
		je = api.make_journal_entry(slip.name)
		lines = self.je_lines(je)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1)
		self.assertAlmostEqual(sum(flt(l.debit) for l in lines), sum(flt(l.credit) for l in lines), places=2)
		by = {}
		for l in lines:
			by.setdefault(l.account, [0.0, 0.0])
			by[l.account][0] += flt(l.debit)
			by[l.account][1] += flt(l.credit)
		# earnings and employer cost are debits (expense), deductions / employer payable /
		# net pay are credits (liability, asset for the advance)
		self.assertGreater(by[self.acc["SB"]][0], 0)
		self.assertEqual(by[self.acc["SB"]][1], 0)
		self.assertAlmostEqual(by[self.acc["CTSSE"]][0], flt(slip.ss_employer_amount), places=2)
		self.assertAlmostEqual(by[self.acc["CTSSP"]][1], flt(slip.ss_employer_amount), places=2)
		self.assertAlmostEqual(by[self.acc["CTSS3"]][1], flt(slip.ss_employee_amount), places=2)
		self.assertAlmostEqual(by[self.acc["ADT"]][1], 15000.0, places=2)
		self.assertAlmostEqual(by[self.payable][1], flt(slip.net_pay), places=2)
		self.assertEqual(by[self.payable][0], 0)

	def test_payment_clears_exactly_the_payable(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		pe = api.make_payment_entry(slip.name)
		by = {l.account: (flt(l.debit), flt(l.credit)) for l in self.je_lines(pe)}
		self.assertAlmostEqual(by[self.payable][0], flt(slip.net_pay), places=2)
		self.assertAlmostEqual(by[self.bank][1], flt(slip.net_pay), places=2)

	def test_no_problems_are_reported_for_a_fully_mapped_chart(self):
		slip = self.slip_with_advance()
		self.assertEqual(pa.accrual_problems([slip]), [])
		self.assertEqual(pa.payment_problems([slip]), [])
		self.assertTrue(all(i["ok"] for i in pa.finance_checklist(self.company, include_readiness=False)),
		                pa.finance_checklist(self.company))


# --------------------------------------------------------------------------- #
# History is never rewritten
# --------------------------------------------------------------------------- #
class TestHistoricalProtection(AccountsFixture):

	def test_a_posted_voucher_survives_a_mapping_change_and_is_reported_as_drift(self):
		slip = self.make_slip()
		je = api.make_journal_entry(slip.name)
		before = self.je_lines(je)
		other = frappe.get_doc({
			"doctype": "Account", "account_name": PREFIX + " INSS 3pct alternativa",
			"account_number": free_account_number(self.company), "company": self.company,
			"root_type": "Liability",
			"is_group": 0, "parent_account": frappe.db.get_value(
				"Account", self.acc["CTSS3"], "parent_account"),
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Account", other.name))
		self.remap("CTSS3", other.name)                    # the mapping moves after posting

		# the voucher is untouched
		self.assertEqual(self.je_lines(je), before)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1)
		# posting again does not create a second voucher
		self.assertEqual(api.make_journal_entry(slip.name), je)
		# and the run's reconciliation says why the INSS line no longer matches
		entry = frappe.get_doc("Isoft Payroll Entry", slip.payroll_entry)
		report = recon.reconcile(entry)
		drift = report["mapping_drift"]
		self.assertTrue(drift["drifted"])
		self.assertIn(self.acc["CTSS3"], [d["account"] for d in drift["posted_to_unmapped_accounts"]])
		line = next(l for l in report["lines"] if l["key"] == "employee_inss")
		self.assertFalse(line["reconciled"])
		self.assertIn("mapping changed", line["note"])

	def test_no_drift_is_reported_while_the_mapping_is_stable(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		entry = frappe.get_doc("Isoft Payroll Entry", slip.payroll_entry)
		report = recon.reconcile(entry)
		self.assertFalse(report["mapping_drift"]["drifted"], report["mapping_drift"])
		self.assertTrue(next(l for l in report["lines"] if l["key"] == "employee_inss")["reconciled"])


# --------------------------------------------------------------------------- #
# Readiness and the Finance checklist
# --------------------------------------------------------------------------- #
class TestReadinessAndChecklist(AccountsFixture):

	def test_readiness_names_the_component_type_and_place(self):
		self.unmap("ADT")
		self.unmap("CTSSE")
		items = {i["key"]: i for i in readiness.configuration_status(self.company)}
		adt, ctsse = items["account:ADT"], items["account:CTSSE"]
		self.assertFalse(adt["ok"])
		self.assertEqual(adt["severity"], readiness.BLOCKING)
		self.assertIn("Salary Advance", adt["label"])
		self.assertEqual(adt["required"], "Liability or Asset")
		self.assertEqual(adt["where"], pa.WHERE_COMPONENTS)
		self.assertIn("Employer INSS", ctsse["label"])
		self.assertEqual(ctsse["required"], "Expense")

		report = readiness.evaluate(self.company, self.start, self.end)
		messages = [e["message"] for e in report["exceptions"] if e["code"] == "EXC-006"]
		self.assertTrue(any("Salary Advance" in m and "Liability or Asset" in m and
		                    "Account per Component" in m and m.startswith("BLOCKING") for m in messages), messages)
		self.assertTrue(any("Employer INSS" in m and "Expense" in m for m in messages), messages)

	def test_submission_for_approval_is_blocked_but_calculation_is_not(self):
		self.unmap("CTSSE")
		slip = self.make_slip(submit=False)                # calculation: fine
		self.assertEqual(slip.docstatus, 0)
		entry = frappe.get_doc("Isoft Payroll Entry", slip.payroll_entry)
		with self.assertRaises(frappe.ValidationError) as cm:
			readiness.assert_ready_to_submit(entry)
		self.assertIn("Employer INSS", str(cm.exception))

	def test_finance_checklist_lists_the_nine_items_and_their_state(self):
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertEqual(set(items), set(pa.CHECKLIST_ORDER) - {"readiness"})
		self.assertTrue(all(i["ok"] for i in items.values()), {k: v["detail"] for k, v in items.items() if not v["ok"]})
		self.unmap("ADT")
		self.unmap("CTSSE")
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertFalse(items["advance"]["ok"])
		self.assertIn("Liability or Asset", items["advance"]["detail"])
		self.assertIn("Salary advance account is not configured. Please contact Finance.",
		              items["advance"]["reasons"])
		self.assertFalse(items["employer_inss"]["ok"])
		self.assertIn("Expense", items["employer_inss"]["detail"])
		self.assertIn("Employer Social Security expense account is not configured. Please contact Finance.",
		              items["employer_inss"]["reasons"])
		self.assertFalse(items["mapping_complete"]["ok"])
		self.assertFalse(items["components"]["ok"])
		self.assertTrue(items["employee_inss"]["ok"] and items["payable"]["ok"] and items["bank"]["ok"])

	def test_production_readiness_carries_the_checklist_section(self):
		self.unmap("ADT")
		report = pr.get_production_readiness(self.company)
		section = next(s for s in report["sections"] if s["key"] == "finance_checklist")
		self.assertEqual(section["status"], pr.BLOCKED)
		by_key = {c["key"]: c for c in section["checks"]}
		self.assertEqual(by_key["checklist:advance"]["status"], pr.BLOCKED)
		self.assertEqual(by_key["checklist:advance"]["owner"], pr.FINANCE)
		self.assertIn("Account per Component", by_key["checklist:advance"]["action"])
		for key in ("checklist:employee_inss", "checklist:employer_inss", "checklist:payable", "checklist:bank"):
			self.assertEqual(by_key[key]["status"], pr.READY, key)
		for key in ("checklist:mapping_complete", "checklist:components"):
			self.assertEqual(by_key[key]["status"], pr.BLOCKED, key)
		self.assertEqual(report["status"], pr.BLOCKED)
