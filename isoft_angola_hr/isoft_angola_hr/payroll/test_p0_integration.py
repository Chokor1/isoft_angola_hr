# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 1 / P0 integration tests: accounting, idempotency, cancellation, salary-profile
ambiguity, bank export and effective-dated statutory rules.

SAFETY
------
These tests never touch existing payroll: they create their own employee, salary
profile, accounts and slips, all prefixed ``_TEST AHR``.

Cleanup is EXPLICIT, not merely transactional. Rollback alone is not sufficient here —
Account is a NestedSet, and its tree maintenance issues an implicit commit that makes
anything created earlier in the transaction durable. So the class fixture records every
document it creates and deletes them in ``tearDownClass``, while individual tests are
additionally isolated by a savepoint.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, get_first_day, get_last_day, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
	get_active_profile,
)

PREFIX = "_TEST AHR"


def _company():
	"""A company with a chart of accounts to post against."""
	name = frappe.db.get_single_value("Isoft HR Settings", "default_company")
	return name or frappe.get_all("Company", pluck="name")[0]


_ACCOUNT_SEQ = [0]


def _account(company, name, root_type):
	"""Create (or reuse) a leaf account under the company's first matching group.

	An account_number is always supplied: this site makes it mandatory via a Property
	Setter, and relying on the ERPNext default would make the fixture site-dependent.
	"""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	_ACCOUNT_SEQ[0] += 1
	number = "99999{0:02d}".format(_ACCOUNT_SEQ[0])
	full = "{0} - {1} - {2}".format(number, name, abbr)
	if frappe.db.exists("Account", full):
		return full
	parent = frappe.db.get_value(
		"Account", {"company": company, "root_type": root_type, "is_group": 1, "parent_account": ("!=", "")},
		"name",
	) or frappe.db.get_value("Account", {"company": company, "root_type": root_type, "is_group": 1}, "name")
	doc = frappe.get_doc({
		"doctype": "Account", "account_name": name, "account_number": number,
		"company": company, "parent_account": parent, "root_type": root_type, "is_group": 0,
	}).insert(ignore_permissions=True)
	return doc.name


class PayrollFixture(FrappeTestCase):
	"""Shared, self-contained payroll fixture."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._created = []          # (doctype, name), deleted in reverse in tearDownClass
		cls.company = _company()
		# Post in the current month: the site has an accounting freeze date, and a fixed
		# past period would fail to post as soon as the books are closed past it.
		cls.start = get_first_day(getdate(frappe.utils.nowdate()))
		cls.end = get_last_day(cls.start)

		cls.employee = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Employee",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))

		cls.profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.employee.name,
			"company": cls.company, "from_date": "2020-01-01",
			"base": 200000, "food_allowance": 30000, "transport_allowance": 30000,
		}).insert(ignore_permissions=True))

		# Dedicated posting accounts, tracked for explicit deletion.
		def acct(label, root_type):
			name = _account(cls.company, label, root_type)
			cls._created.append(("Account", name))
			return name

		cls.acc = {
			"SB": acct(PREFIX + " Salario Base", "Expense"),
			"SDA": acct(PREFIX + " Sub Alimentacao", "Expense"),
			"SDT": acct(PREFIX + " Sub Transporte", "Expense"),
			"CTSS3": acct(PREFIX + " INSS Trabalhador", "Liability"),
			"IRT": acct(PREFIX + " IRT a Pagar", "Liability"),
			"ADT": acct(PREFIX + " Adiantamentos", "Asset"),
			"CTSSE": acct(PREFIX + " INSS Patronal Custo", "Expense"),
			"CTSSP": acct(PREFIX + " INSS Patronal a Pagar", "Liability"),
		}
		cls.payable = acct(PREFIX + " Salarios a Pagar", "Liability")
		cls.bank = acct(PREFIX + " Banco", "Asset")

		# Point Settings at the test accounts, remembering the previous configuration so
		# it can be restored even if an implicit commit makes the change durable.
		s = frappe.get_single("Isoft HR Settings")
		cls._settings_backup = {
			"payroll_payable_account": s.payroll_payable_account,
			"salary_payment_account": s.salary_payment_account,
			"component_accounts": [{"abbr": r.abbr, "component": r.component,
			                        "kind": r.get("kind"), "account": r.account}
			                       for r in s.component_accounts],
		}
		s.payroll_payable_account = cls.payable
		s.salary_payment_account = cls.bank
		existing = {r.abbr: r for r in s.component_accounts}
		for abbr, account in cls.acc.items():
			if abbr in existing:
				existing[abbr].account = account
			else:
				s.append("component_accounts", {"abbr": abbr, "account": account})
		s.save(ignore_permissions=True)

	@classmethod
	def _track(cls, doc):
		cls._created.append((doc.doctype, doc.name))
		return doc

	@classmethod
	def tearDownClass(cls):
		"""Delete the fixture explicitly, newest first, then roll back whatever is left.

		Explicit deletion is required because an implicit commit during the class setup
		can make these records durable; relying on the rollback alone would leave test
		employees and salary profiles behind on the site.
		"""
		frappe.db.rollback()

		# Restore the settings singleton to exactly what it was before the fixture ran.
		backup = getattr(cls, "_settings_backup", None)
		if backup:
			s = frappe.get_single("Isoft HR Settings")
			s.payroll_payable_account = backup["payroll_payable_account"]
			s.salary_payment_account = backup["salary_payment_account"]
			s.set("component_accounts", [])
			for row in backup["component_accounts"]:
				s.append("component_accounts", row)
			s.save(ignore_permissions=True)

		# Salary History rows are written by the profile's on_update hook and hold a Link
		# to the Employee, so they must go FIRST — otherwise the link check refuses to
		# delete the employee and a test record survives on the site. That is exactly how
		# the Phase 1.5 leak happened.
		employees = [name for doctype, name in cls._created if doctype == "Employee"]
		if employees:
			# Employee is a NestedSet. Deleting a manager before their report leaves the
			# report pointing at a parent that no longer exists, and the tree maintenance
			# then dies with an IndexError — which silently aborts the whole cleanup and
			# leaves test employees on the site. Flatten the tree first.
			frappe.db.sql(
				"""update `tabEmployee` set reports_to = null where name in ({0})
				or reports_to in ({0})""".format(", ".join(["%s"] * len(employees))),
				employees + employees)
			for name in frappe.db.sql_list(
				"""select name from `tabIsoft Salary History` where employee in ({0})""".format(
					", ".join(["%s"] * len(employees))), employees):
				frappe.delete_doc("Isoft Salary History", name, force=True,
				                  ignore_permissions=True)

		failed = []
		for doctype, name in reversed(cls._created):
			try:
				if frappe.db.exists(doctype, name):
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True,
					                  delete_permanently=True)
			except Exception as exc:
				failed.append("{0} {1}: {2}".format(doctype, name,
				                                    frappe.utils.strip_html(str(exc))[:160]))
		# Any orphan history left by an employee created outside cls._created.
		for name in frappe.db.sql_list(
			"""select name from `tabIsoft Salary History` where employee not in
			(select name from `tabEmployee`)"""):
			frappe.delete_doc("Isoft Salary History", name, force=True, ignore_permissions=True)
		if failed:
			# Printed, not logged: writing an Error Log inside a broken transaction fails
			# silently, which is how the previous leak went unnoticed.
			print("\n!! isoft_angola_hr TEST CLEANUP FAILED — records left on the site:")
			for line in failed:
				print("   ", line)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		"""Isolate each test: everything it writes is rolled back to this savepoint, so
		records created by one test cannot leak into the next."""
		frappe.db.savepoint("ahr_test")

	def tearDown(self):
		frappe.db.rollback(save_point="ahr_test")

	def make_slip(self, submit=True, **kwargs):
		values = {
			"doctype": "Isoft Salary Slip", "employee": self.employee.name,
			"company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end,
			"salary_profile": self.profile.name,
		}
		values.update(kwargs)
		slip = frappe.get_doc(values).insert(ignore_permissions=True)
		if submit:
			slip.submit()
		return slip


# --------------------------------------------------------------------------- #
# P0-01 — payroll accounting actually reaches the ledger
# --------------------------------------------------------------------------- #
class TestPayrollAccounting(PayrollFixture):
	def test_accrual_is_submitted_and_produces_gl_entries(self):
		slip = self.make_slip()
		je_name = api.make_journal_entry(slip.name)
		je = frappe.get_doc("Journal Entry", je_name)

		self.assertEqual(je.docstatus, 1, "the accrual Journal Entry must be submitted")
		gl = frappe.get_all("GL Entry", filters={"voucher_no": je_name},
		                    fields=["account", "debit", "credit"])
		self.assertTrue(gl, "submitting the accrual must produce GL Entries")
		self.assertAlmostEqual(sum(flt(g.debit) for g in gl), sum(flt(g.credit) for g in gl), places=2)
		self.assertAlmostEqual(flt(je.total_debit), flt(je.total_credit), places=2)

	def test_employer_social_security_is_posted(self):
		slip = self.make_slip()
		self.assertAlmostEqual(flt(slip.ss_employer_amount), 20800.00, places=2)
		je_name = api.make_journal_entry(slip.name)
		gl = frappe.get_all("GL Entry", filters={"voucher_no": je_name},
		                    fields=["account", "debit", "credit"])
		by_account = {}
		for g in gl:
			by_account.setdefault(g.account, [0.0, 0.0])
			by_account[g.account][0] += flt(g.debit)
			by_account[g.account][1] += flt(g.credit)

		self.assertAlmostEqual(by_account[self.acc["CTSSE"]][0], 20800.00, places=2,
		                       msg="employer contribution must be debited as expense")
		self.assertAlmostEqual(by_account[self.acc["CTSSP"]][1], 20800.00, places=2,
		                       msg="employer contribution must be credited as a liability")
		# Employee-side liabilities and net pay
		self.assertAlmostEqual(by_account[self.acc["CTSS3"]][1], 7800.00, places=2)
		self.assertAlmostEqual(by_account[self.acc["IRT"]][1], 19252.00, places=2)
		self.assertAlmostEqual(by_account[self.payable][1], 232948.00, places=2)
		# ...and it did NOT reduce net pay
		self.assertAlmostEqual(flt(slip.net_pay), 232948.00, places=2)

	def test_cost_center_is_set_on_every_line(self):
		slip = self.make_slip()
		je = frappe.get_doc("Journal Entry", api.make_journal_entry(slip.name))
		for row in je.accounts:
			self.assertTrue(row.cost_center, "every payroll GL line needs a cost centre")

	def test_payment_clears_the_payable(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		pe = frappe.get_doc("Journal Entry", api.make_payment_entry(slip.name))
		self.assertEqual(pe.docstatus, 1)

		balance = frappe.db.sql(
			"""select sum(debit) - sum(credit) from `tabGL Entry`
			where account=%s and voucher_no in (%s, %s)""",
			(self.payable, slip.journal_entry, slip.payment_entry))[0][0]
		self.assertAlmostEqual(flt(balance), 0.0, places=2,
		                       msg="accrual + payment must leave the payable at zero")

	def test_payment_requires_an_accrual_first(self):
		slip = self.make_slip()
		with self.assertRaises(frappe.ValidationError):
			api.make_payment_entry(slip.name)

    # ---- idempotency ----
	def test_posting_twice_creates_one_entry(self):
		slip = self.make_slip()
		first = api.make_journal_entry(slip.name)
		second = api.make_journal_entry(slip.name)
		self.assertEqual(first, second)
		self.assertEqual(
			frappe.db.count("Journal Entry", {"user_remark": ("like", "%" + slip.name + "%"),
			                                  "voucher_type": "Journal Entry"}), 1)

	def test_paying_twice_creates_one_entry(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		self.assertEqual(api.make_payment_entry(slip.name), api.make_payment_entry(slip.name))

	def test_cannot_post_a_draft_slip(self):
		slip = self.make_slip(submit=False)
		with self.assertRaises(frappe.ValidationError):
			api.make_journal_entry(slip.name)

    # ---- cancellation ----
	def test_cancelling_the_entry_reverses_the_ledger(self):
		slip = self.make_slip()
		je_name = api.make_journal_entry(slip.name)
		frappe.get_doc("Journal Entry", je_name).cancel()
		net = frappe.db.sql("select sum(debit) - sum(credit) from `tabGL Entry` where voucher_no=%s",
		                    je_name)[0][0]
		self.assertAlmostEqual(flt(net), 0.0, places=2,
		                       msg="a cancelled entry must leave no net ledger effect")

	def test_slip_cannot_be_cancelled_while_posted(self):
		slip = self.make_slip()
		api.make_journal_entry(slip.name)
		slip.reload()
		with self.assertRaises(frappe.ValidationError):
			slip.cancel()

	def test_slip_can_be_cancelled_after_the_entry_is_cancelled(self):
		slip = self.make_slip()
		je_name = api.make_journal_entry(slip.name)
		frappe.get_doc("Journal Entry", je_name).cancel()
		slip.reload()
		slip.cancel()
		self.assertEqual(slip.docstatus, 2)

    # ---- status semantics ----
	def test_status_distinguishes_posted_from_paid(self):
		slip = self.make_slip()
		self.assertEqual(api._slip_status(slip.docstatus, None, None), "Submitted")
		api.make_journal_entry(slip.name)
		slip.reload()
		self.assertEqual(api._slip_status(slip.docstatus, slip.journal_entry, slip.payment_entry), "Posted")
		api.make_payment_entry(slip.name)
		slip.reload()
		self.assertEqual(api._slip_status(slip.docstatus, slip.journal_entry, slip.payment_entry), "Paid")

	def test_draft_entry_never_reads_as_paid(self):
		"""The original defect: a link alone was treated as proof of payment."""
		slip = self.make_slip()
		draft = frappe.get_doc({
			"doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": self.company,
			"posting_date": self.end,
			"accounts": [
				{"account": self.payable, "debit_in_account_currency": 100},
				{"account": self.bank, "credit_in_account_currency": 100},
			],
		}).insert(ignore_permissions=True)
		self.assertEqual(draft.docstatus, 0)
		self.assertEqual(api._slip_status(1, None, draft.name), "Submitted")


# --------------------------------------------------------------------------- #
# P0-06 — negative net pay
# --------------------------------------------------------------------------- #
class TestNegativeNetBlocked(PayrollFixture):
	def test_draft_may_show_it_but_submission_is_blocked(self):
		slip = self.make_slip(submit=False, adiantamento=500000)
		self.assertLess(flt(slip.net_pay), 0)   # visible to HR
		with self.assertRaises(frappe.ValidationError):
			slip.submit()


# --------------------------------------------------------------------------- #
# P0-04 — salary profile ambiguity
# --------------------------------------------------------------------------- #
class TestSalaryProfileResolution(PayrollFixture):
	def close_profile(self, name, to_date):
		"""Close a profile the way the application does — without re-running validation,
		which would object to the very state being set up."""
		frappe.db.set_value("Isoft Salary Profile", name, "to_date", to_date,
		                    update_modified=False)

	def legacy_profile(self, from_date, base):
		"""A profile created the way pre-guard data reached the live site: saved without
		the overlap check that now exists. Used only to prove payroll REFUSES such data."""
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": from_date, "base": base,
		})
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_single_profile_resolves(self):
		prof = get_active_profile(self.employee.name, self.end, company=self.company)
		self.assertEqual(prof.name, self.profile.name)

	def test_latest_effective_profile_wins(self):
		# Valid salary history: the earlier profile is CLOSED the day before the next
		# one starts. Two open-ended profiles are refused now, which is the point.
		self.close_profile(self.profile.name, "2025-12-31")
		newer = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": "2026-01-01", "base": 300000,
		}).insert(ignore_permissions=True)
		self.assertEqual(get_active_profile(self.employee.name, self.end).name, newer.name)
		# a date before it still resolves to the older profile
		self.assertEqual(
			get_active_profile(self.employee.name, getdate("2025-06-30")).name, self.profile.name)

	def test_future_profile_is_ignored(self):
		self.close_profile(self.profile.name, "2026-12-31")
		frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": "2027-01-01", "base": 900000,
		}).insert(ignore_permissions=True)
		self.assertEqual(get_active_profile(self.employee.name, self.end).name, self.profile.name)

	def test_duplicate_effective_date_is_rejected_on_save(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Isoft Salary Profile", "employee": self.employee.name,
				"company": self.company, "from_date": self.profile.from_date, "base": 999999,
			}).insert(ignore_permissions=True)

	def test_ambiguous_existing_data_stops_payroll(self):
		"""Legacy sites already contain duplicates; resolution must refuse to guess."""
		dupe = self.legacy_profile("2026-01-01", 300000)
		# bypass validation exactly the way historic bad data got there
		frappe.db.set_value("Isoft Salary Profile", dupe.name, "from_date",
		                    self.profile.from_date, update_modified=False)
		with self.assertRaises(frappe.ValidationError):
			get_active_profile(self.employee.name, self.end)

	def test_closed_period_overlap_is_rejected(self):
		# Close the open-ended base profile first, otherwise it legitimately covers
		# every later period and would itself be the clash.
		base = frappe.get_doc("Isoft Salary Profile", self.profile.name)
		base.to_date = "2025-12-31"
		base.save(ignore_permissions=True)

		frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": "2026-01-01", "to_date": "2026-06-30",
			"base": 250000,
		}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Isoft Salary Profile", "employee": self.employee.name,
				"company": self.company, "from_date": "2026-03-01", "to_date": "2026-04-30",
				"base": 260000,
			}).insert(ignore_permissions=True)

	def test_salary_change_inside_the_period_is_rejected(self):
		"""P15-BUG-001: a profile boundary inside the payroll period must not be resolved
		to a single rate — that silently pays the whole month at the later salary."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
			assert_single_profile_for_period,
		)
		base = frappe.get_doc("Isoft Salary Profile", self.profile.name)
		base.to_date = add_days(self.start, 14)      # ends mid-period
		base.save(ignore_permissions=True)
		frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": add_days(self.start, 15), "base": 400000,
		}).insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			assert_single_profile_for_period(self.employee.name, self.start, self.end,
			                                 company=self.company)
		# ...and a slip for that period must refuse to calculate rather than overpay
		with self.assertRaises(frappe.ValidationError):
			self.make_slip(submit=False, salary_profile=None)

	def test_new_hire_mid_period_is_not_rejected(self):
		"""An employee with no profile at the start of the period is a normal new hire."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
			assert_single_profile_for_period,
		)
		emp = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Newhire",
			"company": self.company, "date_of_joining": add_days(self.start, 15),
			"status": "Active", "gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True)
		frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": emp.name, "company": self.company,
			"from_date": add_days(self.start, 15), "base": 150000,
		}).insert(ignore_permissions=True)
		prof = assert_single_profile_for_period(emp.name, self.start, self.end,
		                                        company=self.company)
		self.assertIsNotNone(prof)
		self.assertEqual(flt(prof.base), 150000.0)

	def test_profile_for_a_nonexistent_employee_is_rejected(self):
		"""P15-BUG-002: Frappe's link validation misses this because the Link field also
		carries a fetch_from, so it is enforced explicitly."""
		self.assertFalse(frappe.db.exists("Employee", "__no_such_employee__"))
		with self.assertRaises(frappe.LinkValidationError):
			frappe.get_doc({
				"doctype": "Isoft Salary Profile", "employee": "__no_such_employee__",
				"from_date": "2026-01-01", "base": 1,
			}).insert(ignore_permissions=True)

	def test_api_rejects_profile_for_a_nonexistent_employee(self):
		import json
		with self.assertRaises(frappe.LinkValidationError):
			api.save_salary_profile(json.dumps({
				"employee": "__no_such_employee__", "from_date": "2026-02-01", "base": 1}))

	def test_company_is_populated_automatically(self):
		self.close_profile(self.profile.name, "2026-01-31")
		prof = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"from_date": "2026-02-01", "base": 210000,
		}).insert(ignore_permissions=True)
		self.assertEqual(prof.company, self.company)


# --------------------------------------------------------------------------- #
# P0-07 — bank export
# --------------------------------------------------------------------------- #
class TestBankExport(PayrollFixture):
	def _entry(self, slip, status="Payment Ready"):
		"""A payroll entry holding one slip, already released for payment.

		Phase 2 requires an approved, posted and released payroll before a bank file may
		be produced (that gate has its own tests in test_p2_workflow). These tests are
		about what the EXPORT itself refuses, so the state is set directly rather than
		re-walking the whole lifecycle in every case.
		"""
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"employees": [{
				"employee": self.employee.name, "employee_name": self.employee.employee_name,
				"salary_profile": self.profile.name, "salary_slip": slip.name,
			}],
		}).insert(ignore_permissions=True)
		entry.db_set("status", status, update_modified=False)
		entry.reload()
		return entry

	def test_unapproved_payroll_is_refused(self):
		"""A bank file must never be generated for payroll nobody approved."""
		entry = self._entry(self.make_slip(), status="Calculated")
		with self.assertRaises(frappe.ValidationError) as cm:
			api.export_bank_transfer(entry.name)
		self.assertIn("Calculated", str(cm.exception))

	def test_draft_slip_is_refused(self):
		entry = self._entry(self.make_slip(submit=False))
		with self.assertRaises(frappe.ValidationError) as cm:
			api.export_bank_transfer(entry.name)
		self.assertIn("not submitted", str(cm.exception))

	def test_missing_iban_is_refused(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "")
		entry = self._entry(self.make_slip())
		with self.assertRaises(frappe.ValidationError):
			api.export_bank_transfer(entry.name)

	def test_valid_payroll_exports(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "AO06000600000100037131174")
		entry = self._entry(self.make_slip())
		api.export_bank_transfer(entry.name)
		self.assertTrue(frappe.response.get("filecontent"))
		self.assertEqual(frappe.response.get("type"), "binary")
		frappe.response.pop("filecontent", None)

	def test_export_does_not_mark_anything_paid(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "AO06000600000100037131174")
		slip = self.make_slip()
		entry = self._entry(slip)
		api.export_bank_transfer(entry.name)
		frappe.response.pop("filecontent", None)
		slip.reload()
		self.assertFalse(slip.payment_entry)
		self.assertEqual(
			api._slip_status(slip.docstatus, slip.journal_entry, slip.payment_entry), "Submitted")


# --------------------------------------------------------------------------- #
# Effective-dated statutory configuration
# --------------------------------------------------------------------------- #
class TestEffectiveDatedStatutory(PayrollFixture):
	def test_irt_table_is_chosen_by_payroll_date(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import get_active_irt_table

		def table(title, effective_from, rate):
			return frappe.get_doc({
				"doctype": "IRT Table", "title": title, "effective_from": effective_from,
				"brackets": [
					{"from_amount": 0, "to_amount": 150000, "excess_over": 0, "rate": 0, "parcela_fixa": 0},
					{"from_amount": 150001, "to_amount": 0, "excess_over": 150000,
					 "rate": rate, "parcela_fixa": 12500},
				],
			}).insert(ignore_permissions=True)

		a = table(PREFIX + " IRT 2025", "2025-01-01", 16)
		b = table(PREFIX + " IRT 2026", "2026-01-01", 20)
		self.assertEqual(get_active_irt_table(on_date="2025-12-31").name, a.name)
		self.assertEqual(get_active_irt_table(on_date="2026-01-01").name, b.name)

	def test_statutory_rates_are_chosen_by_payroll_date(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_statutory_rate.isoft_statutory_rate import (
			get_statutory_rates,
		)

		frappe.get_doc({
			"doctype": "Isoft Statutory Rate", "effective_from": "2026-06-01",
			"ss_employee_rate": 5, "ss_employer_rate": 10,
			"food_allowance_exemption": 30000, "transport_allowance_exemption": 30000,
		}).insert(ignore_permissions=True)

		before = get_statutory_rates(on_date="2026-05-31")
		after = get_statutory_rates(on_date="2026-06-30")
		self.assertIsNone(before.statutory_rate, "before the effective date the settings apply")
		self.assertEqual(flt(after.ss_employee_rate), 5.0)
		self.assertEqual(flt(after.ss_employer_rate), 10.0)

	def test_duplicate_effective_date_is_rejected(self):
		frappe.get_doc({
			"doctype": "Isoft Statutory Rate", "effective_from": "2026-07-01",
			"ss_employee_rate": 3, "ss_employer_rate": 8,
		}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Isoft Statutory Rate", "effective_from": "2026-07-01",
				"ss_employee_rate": 4, "ss_employer_rate": 9,
			}).insert(ignore_permissions=True)

	def test_submitted_slip_keeps_its_statutory_snapshot(self):
		"""Changing the rules must not alter a slip that is already submitted."""
		slip = self.make_slip()
		original_irt = flt(slip.irt_amount)
		original_rate = flt(slip.ss_employee_rate)

		frappe.get_doc({
			"doctype": "Isoft Statutory Rate", "effective_from": "2020-01-01",
			"ss_employee_rate": 11, "ss_employer_rate": 22,
			"food_allowance_exemption": 0, "transport_allowance_exemption": 0,
		}).insert(ignore_permissions=True)

		slip.reload()
		self.assertEqual(flt(slip.irt_amount), original_irt)
		self.assertEqual(flt(slip.ss_employee_rate), original_rate)


# --------------------------------------------------------------------------- #
# IRT Table validation
# --------------------------------------------------------------------------- #
class TestIRTTableValidation(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("ahr_irt_test")

	def tearDown(self):
		frappe.db.rollback(save_point="ahr_irt_test")

	def _table(self, brackets, effective_from="2026-01-01"):
		return frappe.get_doc({
			"doctype": "IRT Table", "title": PREFIX + " " + frappe.generate_hash(length=8),
			"effective_from": effective_from, "brackets": brackets,
		})

	def test_valid_table_saves(self):
		self._table([
			{"from_amount": 0, "to_amount": 150000, "rate": 0, "parcela_fixa": 0},
			{"from_amount": 150001, "to_amount": 0, "excess_over": 150000, "rate": 16,
			 "parcela_fixa": 12500},
		]).insert(ignore_permissions=True)

	def test_gap_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([
				{"from_amount": 0, "to_amount": 150000, "rate": 0, "parcela_fixa": 0},
				{"from_amount": 160000, "to_amount": 0, "rate": 16, "parcela_fixa": 12500},
			]).insert(ignore_permissions=True)

	def test_overlap_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([
				{"from_amount": 0, "to_amount": 200000, "rate": 0, "parcela_fixa": 0},
				{"from_amount": 150000, "to_amount": 0, "rate": 16, "parcela_fixa": 12500},
			]).insert(ignore_permissions=True)

	def test_closed_last_bracket_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([
				{"from_amount": 0, "to_amount": 150000, "rate": 0, "parcela_fixa": 0},
				{"from_amount": 150001, "to_amount": 200000, "rate": 16, "parcela_fixa": 12500},
			]).insert(ignore_permissions=True)

	def test_table_not_starting_at_zero_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([
				{"from_amount": 1000, "to_amount": 0, "rate": 16, "parcela_fixa": 0},
			]).insert(ignore_permissions=True)

	def test_decreasing_fixed_portion_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([
				{"from_amount": 0, "to_amount": 150000, "rate": 0, "parcela_fixa": 20000},
				{"from_amount": 150001, "to_amount": 0, "rate": 16, "parcela_fixa": 12500},
			]).insert(ignore_permissions=True)

	def test_missing_effective_date_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._table([{"from_amount": 0, "to_amount": 0, "rate": 0, "parcela_fixa": 0}],
			            effective_from=None).insert(ignore_permissions=True)

	def test_live_default_table_still_validates(self):
		"""The table configured on this site must survive the new validation unchanged."""
		name = frappe.db.get_single_value("Isoft HR Settings", "default_irt_table")
		if not name or not frappe.db.exists("IRT Table", name):
			self.skipTest("no default IRT Table configured")
		doc = frappe.get_doc("IRT Table", name)
		doc.validate_brackets()   # must not raise
