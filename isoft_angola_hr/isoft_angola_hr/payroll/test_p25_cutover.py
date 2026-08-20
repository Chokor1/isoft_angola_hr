# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 2.5 tests: configuration validation, production readiness, segregation
conflict detection, month-end reconciliation, closure control and the health check.

SAFETY — same discipline as Phase 1 and 2. The fixture creates its own company-scoped
records prefixed ``_TEST AHR`` and deletes them explicitly; every test additionally runs
inside a savepoint. Nothing here reads or writes existing payroll.
"""

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, _account
from isoft_angola_hr.isoft_angola_hr.services import payroll_reconciliation as recon
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr


# --------------------------------------------------------------------------- #
# Account mapping validation — configuration errors must fail when SAVED
# --------------------------------------------------------------------------- #
class TestAccountValidation(ControlFixture):
	def _settings(self):
		return frappe.get_single("Isoft HR Settings")

	def _set_component_account(self, abbr, account):
		s = self._settings()
		for row in s.component_accounts:
			if row.abbr == abbr:
				row.account = account
				break
		else:
			s.append("component_accounts", {"abbr": abbr, "account": account})
		s.save(ignore_permissions=True)
		return s

	def test_valid_configuration_still_saves(self):
		self._settings().save(ignore_permissions=True)   # must not raise

	def test_expense_component_rejects_a_liability_account(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._set_component_account("SB", self.acc["IRT"])
		self.assertIn("Expense", str(cm.exception))

	def test_employer_liability_rejects_an_expense_account(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._set_component_account("CTSSP", self.acc["SB"])
		self.assertIn("Liability", str(cm.exception))

	def test_group_account_is_rejected(self):
		group = frappe.db.get_value("Account", {"company": self.company, "is_group": 1,
		                                        "root_type": "Expense"}, "name")
		with self.assertRaises(frappe.ValidationError) as cm:
			self._set_component_account("SB", group)
		self.assertIn("group account", str(cm.exception))

	def test_disabled_account_is_rejected(self):
		frappe.db.set_value("Account", self.acc["SDA"], "disabled", 1)
		try:
			with self.assertRaises(frappe.ValidationError) as cm:
				self._set_component_account("SDA", self.acc["SDA"])
			self.assertIn("disabled", str(cm.exception))
		finally:
			frappe.db.set_value("Account", self.acc["SDA"], "disabled", 0)

	def test_account_of_another_company_is_rejected(self):
		"""The mapping that would post one company's payroll into another's ledger."""
		other = [c for c in frappe.get_all("Company", pluck="name") if c != self.company]
		if not other:
			self.skipTest("site has a single company")
		foreign = frappe.db.get_value("Account", {"company": other[0], "is_group": 0,
		                                          "root_type": "Expense", "disabled": 0}, "name")
		if not foreign:
			self.skipTest("no usable account in the other company")
		with self.assertRaises(frappe.ValidationError) as cm:
			self._set_component_account("SB", foreign)
		self.assertIn(other[0], str(cm.exception))

	def test_nonexistent_account_is_rejected(self):
		s = self._settings()
		s.payroll_payable_account = "_TEST AHR does not exist"
		with self.assertRaises(Exception):
			s.save(ignore_permissions=True)

	def test_account_query_only_offers_valid_accounts(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
			account_query, component_root_types,
		)

		self.assertEqual(component_root_types("CTSSE"), ["Expense"])
		self.assertEqual(component_root_types("CTSSP"), ["Liability"])
		rows = account_query("Account", "", "name", 0, 50,
		                     {"company": self.company, "root_types": ["Liability"]})
		names = [r[0] for r in rows]
		self.assertNotIn(self.acc["SB"], names, "an expense account must not be offered")
		for name in names:
			row = frappe.db.get_value("Account", name, ["is_group", "disabled", "company",
			                                            "root_type"], as_dict=True)
			self.assertEqual(row.is_group, 0)
			self.assertFalse(row.disabled)
			self.assertEqual(row.company, self.company)
			self.assertEqual(row.root_type, "Liability")


# --------------------------------------------------------------------------- #
# Production readiness
# --------------------------------------------------------------------------- #
class TestProductionReadiness(ControlFixture):
	def _sections(self, report):
		return {s["key"]: s for s in report["sections"]}

	def _check(self, report, key):
		for section in report["sections"]:
			for c in section["checks"]:
				if c["key"] == key:
					return c
		return None

	def test_report_has_every_section_and_an_overall_status(self):
		report = pr.get_production_readiness(self.company)
		sections = self._sections(report)
		for key in ("accounts", "organisation", "statutory", "security", "data"):
			self.assertIn(key, sections)
		self.assertIn(report["status"], (pr.READY, pr.WARNING, pr.BLOCKED))

	def test_a_missing_component_account_blocks_production(self):
		s = frappe.get_single("Isoft HR Settings")
		for row in s.component_accounts:
			if row.abbr == "CTSSE":
				row.account = None
		s.save(ignore_permissions=True)
		report = pr.get_production_readiness(self.company)
		check = self._check(report, "account:CTSSE")
		self.assertEqual(check["status"], pr.BLOCKED)
		self.assertEqual(check["owner"], pr.FINANCE)
		self.assertEqual(report["status"], pr.BLOCKED)

	def test_unstaffed_payroll_roles_block_production(self):
		"""Roles that exist but that nobody holds cannot move payroll through its workflow."""
		report = pr.get_production_readiness(self.company)
		# The fixture assigns each role to a test user, so these must be staffed here.
		for role in perms.APP_ROLES:
			self.assertEqual(self._check(report, "role:" + role)["status"], pr.READY)
		self.assertGreaterEqual(report["role_staffing"][perms.PAYROLL_OFFICER], 1)

	def test_currency_mismatch_is_blocking(self):
		s = frappe.get_single("Isoft HR Settings")
		backup = s.currency
		other = frappe.db.get_value("Currency", {"name": ("!=", backup)}, "name")
		if not other:
			self.skipTest("only one currency on the site")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "currency", other)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			check = self._check(pr.get_production_readiness(self.company), "currency")
			self.assertEqual(check["status"], pr.BLOCKED)
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "currency", backup)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_irt_effective_date_is_checked_against_the_law_not_the_title(self):
		"""A table holding the 150.000 exemption is the OGE 2026 table, which entered into
		force on 1 January 2026 (Lei n.º 14/25, art. 43.º)."""
		table = frappe.get_doc({
			"doctype": "IRT Table", "title": PREFIX + " IRT wrong date",
			"effective_from": "2024-01-01",
			"brackets": [
				{"from_amount": 0, "to_amount": 150000, "excess_over": 0, "rate": 0,
				 "parcela_fixa": 0},
				{"from_amount": 150001, "to_amount": 0, "excess_over": 150000, "rate": 16,
				 "parcela_fixa": 12500},
			],
		}).insert(ignore_permissions=True)
		self.assertEqual(pr._expected_irt_effective_from(table.name), getdate("2026-01-01"))

		backup = frappe.db.get_single_value("Isoft HR Settings", "default_irt_table")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "default_irt_table", table.name)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			check = self._check(pr.get_production_readiness(self.company), "irt_effective_from")
			self.assertIn(check["status"], (pr.WARNING, pr.BLOCKED))
			self.assertEqual(check["required"], "2026-01-01")
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "default_irt_table", backup)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_a_correctly_dated_irt_table_raises_nothing(self):
		table = frappe.get_doc({
			"doctype": "IRT Table", "title": PREFIX + " IRT right date",
			"effective_from": "2026-01-01",
			"brackets": [
				{"from_amount": 0, "to_amount": 150000, "excess_over": 0, "rate": 0,
				 "parcela_fixa": 0},
				{"from_amount": 150001, "to_amount": 0, "excess_over": 150000, "rate": 16,
				 "parcela_fixa": 12500},
			],
		}).insert(ignore_permissions=True)
		backup = frappe.db.get_single_value("Isoft HR Settings", "default_irt_table")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "default_irt_table", table.name)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			check = self._check(pr.get_production_readiness(self.company), "irt_effective_from")
			self.assertEqual(check["status"], pr.READY)
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "default_irt_table", backup)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_a_slip_outside_any_payroll_run_is_reported(self):
		"""The app always creates slips inside a run, so a slip without one never passed
		through approval. It must not be invisible."""
		before = self._check(pr.get_production_readiness(self.company), "orphan_slips")
		self.assertEqual(before["status"], pr.READY)

		self.make_slip()          # standalone: no payroll_entry
		after = self._check(pr.get_production_readiness(self.company), "orphan_slips")
		self.assertEqual(after["status"], pr.WARNING)
		self.assertEqual(after["count"], 1)
		self.assertIn(self.employee.employee_name, after["action"])

		health = {r["check"]: r for r in pr.health_check(company=self.company, quiet=True)}
		self.assertEqual(health["Payroll outside the workflow"]["status"], "WARNING")

	def test_readiness_is_denied_to_the_employee_role(self):
		frappe.set_user(self.users["employee_only"])
		with self.assertRaises(frappe.PermissionError):
			pr.get_production_readiness(self.company)


# --------------------------------------------------------------------------- #
# Segregation conflict detection
# --------------------------------------------------------------------------- #
class TestSegregationConflicts(ControlFixture):
	def _codes_for(self, user):
		return {c["code"] for c in pr.segregation_conflicts() if c["user"] == user}

	def test_single_role_users_raise_nothing(self):
		self.assertEqual(self._codes_for(self.users["officer"]), set())
		self.assertEqual(self._codes_for(self.users["manager"]), set())

	def test_prepare_and_approve_is_seg_001(self):
		self.assertIn("SEG-001", self._codes_for(self.users["both"]))

	def test_approve_and_pay_is_seg_002(self):
		user = frappe.get_doc("User", self.users["manager"])
		user.append("roles", {"role": perms.PAYROLL_FINANCE})
		user.save(ignore_permissions=True)
		self.assertIn("SEG-002", self._codes_for(self.users["manager"]))

	def test_prepare_and_pay_is_seg_003(self):
		user = frappe.get_doc("User", self.users["officer"])
		user.append("roles", {"role": perms.PAYROLL_FINANCE})
		user.save(ignore_permissions=True)
		self.assertIn("SEG-003", self._codes_for(self.users["officer"]))

	def test_hr_manager_alone_already_concentrates_prepare_and_approve(self):
		"""HR Manager may both prepare and approve, so holding it is itself a SEG-001."""
		user = frappe.get_doc("User", self.users["hruser"])
		user.append("roles", {"role": perms.HR_MANAGER})
		user.save(ignore_permissions=True)
		self.assertIn("SEG-001", self._codes_for(self.users["hruser"]))

	def test_conflicts_never_remove_a_role(self):
		before = set(frappe.get_roles(self.users["both"]))
		pr.segregation_conflicts()
		self.assertEqual(before, set(frappe.get_roles(self.users["both"])))


# --------------------------------------------------------------------------- #
# Month-end reconciliation and closure
# --------------------------------------------------------------------------- #
class TestReconciliationAndClosure(ControlFixture):
	def _paid_entry(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		api.make_bulk_payment_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def test_a_fully_posted_payroll_reconciles(self):
		entry = self._paid_entry()
		report = recon.reconcile(entry)
		self.assertTrue(report["reconciled"],
		                [l for l in report["lines"] if not l["reconciled"]])
		by_key = {l["key"]: l for l in report["lines"]}
		self.assertAlmostEqual(by_key["net_payable"]["expected"], 232948.00, places=2)
		self.assertAlmostEqual(by_key["employer_inss"]["actual"], 20800.00, places=2)
		self.assertAlmostEqual(by_key["employee_inss"]["actual"], 7800.00, places=2)
		self.assertAlmostEqual(by_key["irt"]["actual"], 19252.00, places=2)
		self.assertAlmostEqual(by_key["payment"]["actual"], 232948.00, places=2)

	def test_reconciliation_detects_a_ledger_that_does_not_match(self):
		"""The whole point: if the ledger and the payroll disagree, say so."""
		entry = self._paid_entry()
		frappe.db.set_value("Isoft Salary Slip", entry.employees[0].salary_slip,
		                    "net_pay", 999999, update_modified=False)
		report = recon.reconcile(entry)
		self.assertFalse(report["reconciled"])
		failed = {l["key"] for l in report["lines"] if not l["reconciled"]}
		self.assertIn("net_payable", failed)

	def test_closing_is_refused_while_the_payroll_does_not_reconcile(self):
		entry = self._paid_entry()
		frappe.db.set_value("Isoft Salary Slip", entry.employees[0].salary_slip,
		                    "net_pay", 555555, update_modified=False)
		frappe.set_user(self.users["finance"])
		entry.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.CLOSE)
		self.assertIn("reconcilia", str(cm.exception))

	def test_closing_is_refused_while_accounting_is_incomplete(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		entry.db_set("status", wf.PAID, update_modified=False)
		entry.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.CLOSE)
		self.assertIn("contabilizados", str(cm.exception))

	def test_a_complete_payroll_closes(self):
		entry = self._paid_entry()
		frappe.set_user(self.users["finance"])
		self.assertEqual(api.payroll_action(entry.name, wf.CLOSE)["status"], wf.CLOSED)

	def test_a_closed_payroll_cannot_be_reopened_only_cancelled(self):
		entry = self._paid_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.CLOSE)
		entry.reload()
		# There is no transition back to an editable state...
		for action in (wf.CALCULATE, wf.SUBMIT_FOR_APPROVAL, wf.APPROVE, wf.POST,
		               wf.RELEASE_FOR_PAYMENT, wf.PAY):
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				api.payroll_action(entry.name, action)
		# ...and cancelling still requires the ledger to be cleared first.
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.CANCEL)
		self.assertIn("accounting", str(cm.exception).lower())

	def test_every_live_state_has_a_cancellation_path(self):
		"""A payroll must never become uncorrectable. The ledger guard decides whether the
		cancellation may proceed — the transition itself is always reachable."""
		entry = self.make_entry()
		for state in (wf.DRAFT, wf.CALCULATED, wf.PENDING_APPROVAL, wf.REJECTED, wf.APPROVED,
		              wf.POSTED, wf.PAYMENT_READY, wf.PAID, wf.CLOSED):
			entry.status = state
			self.assertIn(wf.CANCEL, wf.allowed_actions(entry, user="Administrator"),
			              "no cancellation path from {0}".format(state))

	def test_a_paid_payroll_cannot_be_cancelled_while_payments_are_in_the_ledger(self):
		entry = self._paid_entry()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.CANCEL)
		self.assertIn("accounting", str(cm.exception).lower())

	def test_reconciliation_endpoint_requires_a_payroll_role(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["employee_only"])
		with self.assertRaises(frappe.PermissionError):
			recon.payroll_reconciliation(entry.name)


# --------------------------------------------------------------------------- #
# Next-step guidance
# --------------------------------------------------------------------------- #
class TestNextStep(ControlFixture):
	def test_each_state_names_the_action_and_the_responsible_party(self):
		entry = self.calculated_entry()
		step = wf.next_step(entry)
		self.assertEqual(step["state"], wf.CALCULATED)
		self.assertEqual(step["next_action"], wf.SUBMIT_FOR_APPROVAL)
		self.assertEqual(step["responsible"], "Payroll Officer")

		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		entry.reload()
		step = wf.next_step(entry)
		self.assertEqual(step["next_action"], wf.APPROVE)
		self.assertEqual(step["responsible"], "Payroll Manager")

	def test_the_blocker_is_reported_to_the_user_who_cannot_act(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		entry.reload()
		frappe.set_user(self.users["officer"])       # the preparer cannot approve
		step = wf.next_step(entry)
		self.assertFalse(step["can_act_now"])
		self.assertTrue(step["blockers"])

	def test_a_closed_payroll_offers_no_next_action(self):
		entry = self.make_entry()
		entry.db_set("status", wf.CLOSED, update_modified=False)
		entry.reload()
		step = wf.next_step(entry)
		self.assertIsNone(step["next_action"])
		self.assertIn("closed", step["description"].lower())


# --------------------------------------------------------------------------- #
# Deployment health check
# --------------------------------------------------------------------------- #
class TestHealthCheck(ControlFixture):
	def _by_check(self):
		return {r["check"]: r for r in pr.health_check(company=self.company, quiet=True)}

	def test_health_check_reports_every_required_area(self):
		results = self._by_check()
		for label in ("App installed", "Required DocTypes", "Required Reports",
		              "Required Roles exist", "Payroll roles assigned", "Required Accounts",
		              "Statutory configuration", "Workflow controls", "Payroll pipeline"):
			self.assertIn(label, results)
		self.assertEqual(results["App installed"]["status"], "PASS")
		self.assertEqual(results["Required DocTypes"]["status"], "PASS")
		self.assertEqual(results["Required Reports"]["status"], "PASS")
		self.assertEqual(results["Required Roles exist"]["status"], "PASS")

	def test_health_check_fails_when_an_account_is_unmapped(self):
		s = frappe.get_single("Isoft HR Settings")
		for row in s.component_accounts:
			if row.abbr == "CTSSP":
				row.account = None
		s.save(ignore_permissions=True)
		self.assertEqual(self._by_check()["Required Accounts"]["status"], "FAIL")

	def test_health_check_writes_nothing(self):
		before = {dt: frappe.db.count(dt) for dt in
		          ("Isoft Salary Slip", "Isoft Payroll Entry", "Isoft Salary Profile",
		           "Journal Entry", "GL Entry", "Role", "Report")}
		pr.health_check(company=self.company, quiet=True)
		after = {dt: frappe.db.count(dt) for dt in before}
		self.assertEqual(before, after)
