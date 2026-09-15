# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Final production readiness: the Finance approval checklist, mapping governance, the
approval gate, bank payment validation and the immutability of paid payroll.

The eight cases the readiness brief asks for, each walked through the real workflow with
one user per role:

1. a missing account blocks posting, in business language;
2. a mapping change after posting is recorded (who, when, old, new) and announced;
3. historical vouchers are untouched by a mapping change;
4. a payroll cannot be submitted or approved while Finance configuration is missing;
5. with the configuration correct the manager approves and Finance posts and pays;
6. an HR user cannot bypass the Finance controls by any channel;
7. the bank file requires an approved, posted and released payroll, the accrual in the
   ledger, and refuses a second payment;
8. a paid payroll stays immutable — its entry, its slips, its vouchers.

SAFETY — the ControlFixture contract: every record is prefixed ``_TEST AHR``, registered
for deletion, and each test runs inside a savepoint. Settings are changed through the
controller (never committed) and restored by the fixture; the real site's mapping, runs
and vouchers are never written.
"""

from unittest import mock

import frappe
from frappe.client import set_value as client_set_value
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings import isoft_hr_settings as settings_module
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import bank_export
from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa
from isoft_angola_hr.isoft_angola_hr.services import payroll_approval as approval
from isoft_angola_hr.isoft_angola_hr.services import payroll_reconciliation as recon
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr

CONTACT = "Please contact Finance."
EMPLOYER_INSS_MISSING = "Employer Social Security expense account is not configured. " + CONTACT
ADVANCE_MISSING = "Salary advance account is not configured. " + CONTACT
CANNOT_PROCEED = "Payroll cannot proceed until Finance configuration is complete."


class ReadinessFixture(ControlFixture):
	USERS = dict(ControlFixture.USERS, **{
		"hrm": [perms.HR_MANAGER],
		"hrm_acc": [perms.HR_MANAGER, perms.ACCOUNTS_MANAGER],      # what ITEC HR users hold
	})

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# An alternative liability account, so a test can move a mapping to a valid one.
		parent = frappe.db.get_value("Account", cls.acc["CTSS3"], "parent_account")
		cls.alt_liability = cls._track(frappe.get_doc({
			"doctype": "Account", "account_name": PREFIX + " INSS 3pct alternativa",
			"account_number": "9999978", "company": cls.company, "root_type": "Liability",
			"is_group": 0, "parent_account": parent,
		}).insert(ignore_permissions=True)).name

	def setUp(self):
		super().setUp()
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "AO06000600000100037131174")
		frappe.clear_messages()

	# ------------------------------------------------------------------ #
	def as_user(self, key):
		frappe.set_user("Administrator" if key == "admin" else self.users[key])

	def unmap(self, abbr):
		"""Clear one component's account the way a direct edit would (SQL, no controller);
		rolled back with the test."""
		frappe.db.sql("""update `tabIsoft Component Account` set account = NULL
			where parent = 'Isoft HR Settings' and abbr = %s""", abbr)
		frappe.clear_cache(doctype="Isoft HR Settings")

	def remap(self, abbr, account):
		"""Restore a mapping by SQL — the reverse of :meth:`unmap`, and like it not a
		Finance decision, so the fixture's confirmation of its mapping stays current."""
		frappe.db.sql("""update `tabIsoft Component Account` set account = %s
			where parent = 'Isoft HR Settings' and abbr = %s""", (account, abbr))
		frappe.clear_cache(doctype="Isoft HR Settings")

	def remap_through_settings(self, abbr, account):
		"""Change a mapping the way Finance does: through the Settings controller, which is
		where governance lives. No commit — the fixture's savepoint undoes it."""
		s = frappe.get_single("Isoft HR Settings")
		for row in s.component_accounts:
			if row.abbr == abbr:
				row.account = account
		s.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Isoft HR Settings")
		return s

	def refused(self, exc_type, fn, *args, **kwargs):
		with self.assertRaises(exc_type) as cm:
			fn(*args, **kwargs)
		return frappe.utils.strip_html(str(cm.exception))

	def status(self, entry):
		return frappe.db.get_value("Isoft Payroll Entry", entry.name, "status")

	def messages(self):
		out = []
		for m in frappe.message_log or []:
			try:
				out.append(frappe.parse_json(m).get("message") if isinstance(m, str) else m.get("message"))
			except Exception:
				out.append(str(m))
		return [frappe.utils.strip_html(str(m or "")) for m in out]

	def je_lines(self, je):
		return frappe.db.sql("""select account, debit, credit from `tabJournal Entry Account`
			where parent = %s order by idx""", je, as_dict=True)

	def log_rows(self):
		return frappe.get_all("Isoft Payroll Account Change",
		                      filters={"company": self.company, "account_key": ("!=", pa.MAPPING_CONFIRMED)},
		                      fields=["account_key", "changed_by", "changed_on", "old_account",
		                              "new_account", "after_posting", "posted_payroll", "note"],
		                      order_by="changed_on desc, name desc")

	def paid_entry(self, group=None):
		entry = self.posted_entry(group=group)
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		api.make_bulk_payment_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.status, wf.PAID)
		return entry


# --------------------------------------------------------------------------- #
# 1. Missing account blocks posting
# --------------------------------------------------------------------------- #
class TestMissingAccountBlocksPosting(ReadinessFixture):

	def test_missing_account_blocks_posting_in_business_language(self):
		entry = self.approved_entry()
		self.unmap("CTSSE")
		frappe.set_user(self.users["finance"])
		entry.submit_salary_slips()
		msg = self.refused(frappe.ValidationError, api.make_bulk_journal_entry, entry.name)
		self.assertIn("Cannot post payroll", msg)
		self.assertIn(EMPLOYER_INSS_MISSING, msg)
		self.assertIn(CANNOT_PROCEED, msg)
		self.assertIn("CTSSE", msg, "the technical detail Finance needs follows the business sentence")
		self.assertNotIn("Missing account CTSSE", msg)
		entry.reload()
		self.assertEqual(self.status(entry), wf.APPROVED)
		for row in wf.slip_rows(entry):
			self.assertFalse(row.get("journal_entry"), "no voucher was written")

	def test_the_dashboard_checklist_says_blocked_with_exact_reasons(self):
		self.unmap("CTSSE")
		self.unmap("ADT")
		gate = api.finance_approval_checklist(company=self.company)
		self.assertEqual(gate["status"], "BLOCKED")
		self.assertEqual(gate["headline"], CANNOT_PROCEED)
		self.assertIn(EMPLOYER_INSS_MISSING, gate["reasons"])
		self.assertIn(ADVANCE_MISSING, gate["reasons"])
		self.assertEqual([i["key"] for i in gate["items"]], list(pa.CHECKLIST_ORDER))
		blocked = set(gate["blocked_items"])
		self.assertTrue({"mapping_complete", "employer_inss", "advance", "components"} <= blocked, blocked)
		self.assertTrue({"employee_inss", "payable", "bank"}.isdisjoint(blocked))


# --------------------------------------------------------------------------- #
# 2 / 3. Mapping governance and historical protection
# --------------------------------------------------------------------------- #
class TestMappingGovernance(ReadinessFixture):

	def test_mapping_change_after_posting_is_recorded_and_announced(self):
		entry = self.posted_entry()
		before = self.log_rows()
		old = self.acc["CTSS3"]
		frappe.set_user(self.users["hrm"])          # HR Manager owns the settings screen
		frappe.clear_messages()
		self.remap_through_settings("CTSS3", self.alt_liability)
		frappe.set_user("Administrator")

		rows = [r for r in self.log_rows() if r not in before]
		self.assertEqual(len(rows), 1, rows)
		row = rows[0]
		self.assertEqual(row.account_key, "CTSS3")
		self.assertEqual(row.changed_by, self.users["hrm"])
		self.assertTrue(row.changed_on)
		self.assertEqual(row.old_account, old)
		self.assertEqual(row.new_account, self.alt_liability)
		self.assertEqual(row.after_posting, 1)
		self.assertEqual(row.posted_payroll, entry.name)
		self.assertIn(settings_module.MAPPING_CHANGE_WARNING, row.note)
		# announced to the person saving
		self.assertTrue(any(settings_module.MAPPING_CHANGE_WARNING in m for m in self.messages()),
		                self.messages())
		# visible to the screen
		history = api.payroll_account_history(company=self.company)
		self.assertEqual(history["rows"][0]["account_key"], "CTSS3")
		self.assertTrue(history["drifted"])
		self.assertFalse(history["confirmed"])

	def test_mapping_drift_warning_appears_in_checklist_and_readiness(self):
		entry = self.posted_entry()
		self.remap_through_settings("CTSS3", self.alt_liability)
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertFalse(items["mapping_drift"]["ok"])
		self.assertIn(entry.name, items["mapping_drift"]["label"])
		self.assertIn("Finance must confirm the current accounts", items["mapping_drift"]["reasons"][0])
		report = pr.get_production_readiness(self.company)
		section = next(s for s in report["sections"] if s["key"] == "finance_checklist")
		by_key = {c["key"]: c for c in section["checks"]}
		self.assertEqual(by_key["checklist:mapping_drift"]["status"], pr.WARNING)
		self.assertIn("Confirm current mapping", by_key["checklist:mapping_drift"]["action"])
		self.assertEqual(by_key["checklist:last_mapping_change"]["status"], pr.WARNING)
		self.assertIn(self.alt_liability, by_key["checklist:last_mapping_change"]["current"])
		# and the run's own reconciliation names the accounts the vouchers hold
		drift = recon.reconcile(frappe.get_doc("Isoft Payroll Entry", entry.name))["mapping_drift"]
		self.assertTrue(drift["drifted"])
		self.assertIn(self.acc["CTSS3"], [d["account"] for d in drift["posted_to_unmapped_accounts"]])

	def test_historical_vouchers_remain_unchanged(self):
		entry = self.posted_entry()
		rows = wf.slip_rows(entry)
		je = rows[0]["journal_entry"]
		lines_before = self.je_lines(je)
		stamp_before = frappe.db.get_value("Journal Entry", je, ["modified", "docstatus"], as_dict=True)
		self.remap_through_settings("CTSS3", self.alt_liability)
		self.remap_through_settings("CTSSE", self.acc["SB"])
		self.assertEqual(self.je_lines(je), lines_before)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, ["modified", "docstatus"], as_dict=True),
		                 stamp_before)
		self.assertEqual(api.make_journal_entry(rows[0]["name"]), je, "re-posting creates no second voucher")
		self.assertEqual(self.status(entry), wf.POSTED)
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", rows[0]["name"], "journal_entry"), je)

	def test_finance_confirmation_clears_drift_and_a_stale_one_does_not(self):
		self.posted_entry()
		self.remap_through_settings("CTSS3", self.alt_liability)
		self.assertEqual(api.finance_approval_checklist(company=self.company)["status"], "BLOCKED")
		frappe.set_user(self.users["hrm_acc"])
		self.refused(frappe.PermissionError, api.confirm_account_mapping, "hr may not")
		frappe.set_user(self.users["finance"])
		with mock.patch.object(frappe.db, "commit"):      # the endpoint commits; the test must not
			out = api.confirm_account_mapping("Reviewed with the auditor")
		frappe.set_user("Administrator")
		self.assertEqual(out["confirmed_by"], self.users["finance"])
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertTrue(items["mapping_drift"]["ok"], items["mapping_drift"])
		self.assertIn(self.users["finance"], items["mapping_drift"]["detail"])
		# a later change (to yet another account) makes the confirmation stale
		self.remap_through_settings("CTSS3", self.acc["IRT"])
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertFalse(items["mapping_drift"]["ok"])
		# and moving back to the account the run was booked to removes the drift itself
		self.remap_through_settings("CTSS3", self.acc["CTSS3"])
		items = {i["key"]: i for i in pa.finance_checklist(self.company, include_readiness=False)}
		self.assertTrue(items["mapping_drift"]["ok"])

	def test_a_change_before_any_posting_is_logged_without_the_warning(self):
		with mock.patch.object(pa, "last_posted_run", return_value=None):
			frappe.clear_messages()
			self.remap_through_settings("CTSS3", self.alt_liability)
		row = self.log_rows()[0]
		self.assertEqual(row.new_account, self.alt_liability)
		self.assertEqual(row.after_posting, 0)
		self.assertFalse(row.posted_payroll)
		self.assertFalse(any(settings_module.MAPPING_CHANGE_WARNING in m for m in self.messages()))

	def test_the_log_is_history(self):
		self.remap_through_settings("CTSS3", self.alt_liability)
		name = frappe.get_all("Isoft Payroll Account Change", filters={"account_key": "CTSS3"},
		                      order_by="changed_on desc", limit=1, pluck="name")[0]
		self.refused(frappe.PermissionError, client_set_value, "Isoft Payroll Account Change", name,
		             "new_account", "forged")
		frappe.set_user(self.users["hrm"])
		self.refused(frappe.PermissionError, frappe.delete_doc, "Isoft Payroll Account Change", name)


# --------------------------------------------------------------------------- #
# 4 / 5. The approval gate
# --------------------------------------------------------------------------- #
class TestApprovalGate(ReadinessFixture):

	def test_payroll_cannot_be_submitted_or_approved_with_missing_configuration(self):
		entry = self.calculated_entry()
		self.unmap("CTSSE")
		frappe.set_user(self.users["officer"])
		msg = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.SUBMIT_FOR_APPROVAL)
		self.assertIn(EMPLOYER_INSS_MISSING, msg)
		self.assertEqual(self.status(entry), wf.CALCULATED)

		# submitted while complete, then the mapping is lost before approval
		frappe.set_user("Administrator")
		self.remap("CTSSE", self.acc["CTSSE"])
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user("Administrator")
		self.unmap("CTSSE")
		frappe.set_user(self.users["manager"])
		summary = api.payroll_approval_summary(entry.name)
		gate = summary["approval_gate"]
		self.assertEqual(gate["status"], "BLOCKED")
		self.assertEqual(gate["headline"], CANNOT_PROCEED)
		self.assertIn(EMPLOYER_INSS_MISSING, [b["message"] for b in gate["blockers"]])
		self.assertEqual({b["owner"] for b in gate["blockers"] if b["code"] == "APR-001"}, {"Finance"})
		msg = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.APPROVE)
		self.assertIn("cannot be approved", msg)
		self.assertIn(EMPLOYER_INSS_MISSING, msg)
		self.assertEqual(self.status(entry), wf.PENDING_APPROVAL)
		self.assertFalse(frappe.db.get_value("Isoft Payroll Entry", entry.name, "approved_by"))

	def test_approval_is_refused_for_an_orphan_slip_and_a_missing_profile(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user("Administrator")
		slip = wf.slip_rows(entry)[0]["name"]
		# the slip is silently detached from the run (a direct database edit)
		frappe.db.set_value("Isoft Salary Slip", slip, "payroll_entry", None, update_modified=False)
		blockers = approval.approval_blockers(frappe.get_doc("Isoft Payroll Entry", entry.name))
		self.assertTrue(any(b["code"] == "APR-006" and "another run" in b["message"] for b in blockers), blockers)
		frappe.db.set_value("Isoft Salary Slip", slip, "payroll_entry", entry.name, update_modified=False)
		# the salary profile stops governing the period
		frappe.db.set_value("Isoft Salary Profile", self.profile.name, "from_date", "2099-01-01",
		                    update_modified=False)
		blockers = approval.approval_blockers(frappe.get_doc("Isoft Payroll Entry", entry.name))
		codes = {b["code"] for b in blockers}
		self.assertIn("APR-004", codes, blockers)
		frappe.set_user(self.users["manager"])
		msg = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.APPROVE)
		self.assertIn("no salary profile", msg)

	def test_manager_approves_and_finance_posts_and_pays_when_configuration_is_correct(self):
		entry = self.calculated_entry()
		gate = approval.approval_gate(entry)
		self.assertEqual(gate["status"], "READY", gate)
		self.assertEqual(api.finance_approval_checklist(name=entry.name)["status"], "READY")
		frappe.set_user(self.users["officer"])
		self.assertEqual(api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)["status"], wf.PENDING_APPROVAL)
		frappe.set_user(self.users["manager"])
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		self.assertEqual(api.make_bulk_journal_entry(entry.name)["status"], wf.POSTED)
		self.assertEqual(api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)["status"], wf.PAYMENT_READY)
		self.assertTrue(api.bank_export_preflight(entry.name)["valid"])
		self.assertEqual(api.make_bulk_payment_entry(entry.name)["status"], wf.PAID)
		self.assertEqual(api.payroll_action(entry.name, wf.CLOSE)["status"], wf.CLOSED)
		frappe.set_user("Administrator")
		self.assertTrue(recon.reconcile(frappe.get_doc("Isoft Payroll Entry", entry.name))["reconciled"])


# --------------------------------------------------------------------------- #
# 6. HR cannot bypass the Finance controls
# --------------------------------------------------------------------------- #
class TestHRCannotBypass(ReadinessFixture):

	def test_hr_user_cannot_bypass_finance_controls(self):
		entry = self.approved_entry()
		for key in ("hrm", "hrm_acc", "officer", "manager", "hruser"):
			frappe.set_user(self.users[key])
			self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
			self.refused(frappe.PermissionError, api.submit_payroll_entry, entry.name)
			self.refused(frappe.PermissionError, api.make_bulk_payment_entry, entry.name)
			self.refused(frappe.PermissionError, api.export_bank_transfer, entry.name)
			self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
			self.refused(frappe.PermissionError, api.confirm_account_mapping)
			# the state cannot be written around the workflow either
			self.refused(frappe.PermissionError, client_set_value, "Isoft Payroll Entry", entry.name,
			             "status", wf.PAID)
			flags = frappe.get_doc("Isoft Payroll Entry", entry.name).approval_summary()["finance"]
			self.assertFalse(flags["can_post"] or flags["can_pay"] or flags["can_export_bank"]
			                 or flags["can_submit_slips"], (key, flags))
		frappe.set_user("Administrator")
		self.assertEqual(self.status(entry), wf.APPROVED)
		self.assertFalse(frappe.db.get_value("Isoft Payroll Entry", entry.name, "posted_by"))


# --------------------------------------------------------------------------- #
# 7. Bank payment validation
# --------------------------------------------------------------------------- #
class TestBankPaymentValidation(ReadinessFixture):

	def test_bank_payment_is_refused_for_a_calculated_payroll(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["finance"])
		self.assertIn("Calculated", self.refused(frappe.ValidationError, api.export_bank_transfer, entry.name))
		frappe.set_user("Administrator")
		self.assertEqual(self.status(entry), wf.CALCULATED)

	def test_bank_payment_requires_approved_posted_and_released_payroll(self):
		"""(One run per test: create_salary_slips adopts an existing slip for the same
		employee and period, and the approval gate rightly refuses a shared slip.)"""
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		msg = self.refused(frappe.ValidationError, api.export_bank_transfer, entry.name)
		self.assertIn("Approved", msg)
		entry.submit_salary_slips()
		api.make_bulk_journal_entry(entry.name)
		self.assertIn("Posted", self.refused(frappe.ValidationError, api.export_bank_transfer, entry.name))
		report = api.bank_export_preflight(entry.name)
		self.assertFalse(report["valid"])
		self.assertIn("BNK-011", [p["code"] for p in report["problems"]], "not released")

		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		report = api.bank_export_preflight(entry.name)
		self.assertTrue(report["valid"], report["problems"])
		self.assertEqual(report["total"], report["payroll_total"])
		api.export_bank_transfer(entry.name)
		frappe.response.pop("filecontent", None)
		frappe.set_user("Administrator")
		self.assertEqual(self.status(entry), wf.PAYMENT_READY, "a file is not a payment")

	def test_the_checks_read_the_record_not_the_status_word(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		entry.submit_salary_slips()
		frappe.set_user("Administrator")
		# forged: released without approval, posting or release stamps
		frappe.db.set_value("Isoft Payroll Entry", entry.name, {
			"status": wf.PAYMENT_READY, "approval_fingerprint": None, "approved_by": None,
			"payment_authorized_by": None}, update_modified=False)
		frappe.set_user(self.users["finance"])
		report = api.bank_export_preflight(entry.name)
		codes = {p["code"] for p in report["problems"]}
		self.assertTrue({"BNK-010", "BNK-011", "BNK-012"} <= codes, codes)
		self.refused(frappe.ValidationError, api.export_bank_transfer, entry.name)

	def test_no_duplicate_payment(self):
		entry = self.paid_entry()
		frappe.set_user(self.users["finance"])
		report = api.bank_export_preflight(entry.name)
		self.assertIn("BNK-014", [p["code"] for p in report["problems"]], "already paid")
		msg = self.refused(frappe.ValidationError, api.export_bank_transfer, entry.name)
		self.assertIn("pay them twice", msg)
		self.assertEqual(api.make_bulk_payment_entry(entry.name)["created"], 0, "no second payment voucher")

	def test_a_file_the_bank_executed_is_not_regenerated(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		first = bank_export.generate(entry.name)
		bank_export.record_bank_response(first["export"], "BAI-2026-77", status="Executed")
		report = api.bank_export_preflight(entry.name)
		self.assertIn("BNK-015", [p["code"] for p in report["problems"]])
		self.assertIn("duplicate payment", self.refused(frappe.ValidationError, bank_export.generate, entry.name))

	def test_the_file_must_match_the_payable_booked(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		slip = wf.slip_rows(entry)[0]["name"]
		# the slip's net moves after posting (a direct database edit)
		net = flt(frappe.db.get_value("Isoft Salary Slip", slip, "net_pay"))
		frappe.db.set_value("Isoft Salary Slip", slip, "net_pay", net + 1000, update_modified=False)
		report = api.bank_export_preflight(entry.name)
		self.assertIn("BNK-013", [p["code"] for p in report["problems"]])


# --------------------------------------------------------------------------- #
# 8. A paid payroll is immutable
# --------------------------------------------------------------------------- #
class TestPaidPayrollImmutable(ReadinessFixture):

	def test_previous_paid_payroll_remains_immutable(self):
		entry = self.paid_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.CLOSE)
		frappe.set_user("Administrator")
		row = wf.slip_rows(entry)[0]
		je, pe = row["journal_entry"], row["payment_entry"]
		lines = {v: self.je_lines(v) for v in (je, pe)}
		totals = wf.compute_totals(frappe.get_doc("Isoft Payroll Entry", entry.name))

		for key in ("hrm", "hrm_acc", "finance", "manager"):
			frappe.set_user(self.users[key])
			self.refused(frappe.PermissionError, client_set_value, "Isoft Payroll Entry", entry.name,
			             "status", wf.CALCULATED)
			self.refused(frappe.PermissionError, client_set_value, "Isoft Payroll Entry", entry.name,
			             "paid_at", None)
			# the slip is locked by its run
			slip = frappe.get_doc("Isoft Salary Slip", row["name"])
			slip.adiantamento = 5000
			self.refused((frappe.ValidationError, frappe.PermissionError), slip.save)
			self.refused((frappe.ValidationError, frappe.PermissionError), slip.cancel)
			# the run cannot be deleted or re-approved
			self.refused((frappe.ValidationError, frappe.PermissionError), frappe.delete_doc,
			             "Isoft Payroll Entry", entry.name)
			self.refused((frappe.ValidationError, frappe.PermissionError), api.payroll_action,
			             entry.name, wf.APPROVE)
		frappe.set_user("Administrator")
		# a later mapping change leaves the vouchers alone
		self.remap_through_settings("CTSS3", self.alt_liability)
		for v in (je, pe):
			self.assertEqual(self.je_lines(v), lines[v])
			self.assertEqual(frappe.db.get_value("Journal Entry", v, "docstatus"), 1)
		self.assertEqual(self.status(entry), wf.CLOSED)
		self.assertEqual(wf.compute_totals(frappe.get_doc("Isoft Payroll Entry", entry.name)), totals)
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", row["name"],
		                                     ["journal_entry", "payment_entry"]), (je, pe))
