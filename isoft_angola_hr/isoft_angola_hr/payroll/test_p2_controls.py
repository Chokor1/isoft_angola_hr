# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 2 tests: governance, workflow, permissions, readiness, reports and amendment.

SAFETY — identical to the Phase 1 suite. Nothing here touches existing payroll: the
fixture creates its own company-scoped employees, users, salary profiles and payroll
runs, all prefixed ``_TEST AHR``, and deletes them explicitly in ``tearDownClass``.

The permission tests deliberately call the API as real users rather than checking button
visibility. Administrator holds every role in Frappe, so a test that ran as Administrator
would prove nothing about segregation of duties.
"""

import frappe
from frappe.utils import add_days, flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


class ControlFixture(PayrollFixture):
	"""Payroll fixture plus one user per role and a helper that walks the lifecycle."""

	USERS = {
		"officer": [perms.PAYROLL_OFFICER],
		"manager": [perms.PAYROLL_MANAGER],
		"finance": [perms.PAYROLL_FINANCE, perms.ACCOUNTS_MANAGER],
		"hruser": [perms.HR_USER],
		"employee_only": ["Employee"],
		"both": [perms.PAYROLL_OFFICER, perms.PAYROLL_MANAGER],
	}

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = {}
		for key, roles in cls.USERS.items():
			cls.users[key] = cls._make_user(key, roles)
		# Bank and statutory identifiers, so the fixture employee is payable by default.
		# Tests that are about a MISSING value clear it explicitly.
		frappe.db.set_value("Employee", cls.employee.name, {
			"custom_iban": "AO06000600000100037131174",
			"custom_nif": "5417000000",
			"custom_inss_number": "0123456789",
		})

	@classmethod
	def _make_user(cls, key, roles):
		email = "{0}.{1}@ahrtest.invalid".format(PREFIX.lower().replace(" ", "-"), key)
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		user = frappe.get_doc({
			"doctype": "User", "email": email, "first_name": "{0} {1}".format(PREFIX, key),
			"send_welcome_email": 0, "enabled": 1, "user_type": "System User",
			"roles": [{"role": r} for r in roles],
		}).insert(ignore_permissions=True)
		cls._created.append(("User", email))
		return email

	def setUp(self):
		super().setUp()
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	# ------------------------------------------------------------------ #
	def make_entry(self, group=None, employees=None, insert=True):
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": group or PREFIX,
			"employees": employees if employees is not None else [{
				"employee": self.employee.name, "employee_name": self.employee.employee_name,
				"salary_profile": self.profile.name,
			}],
		})
		if insert:
			entry.insert(ignore_permissions=True)
		return entry

	def calculated_entry(self, group=None):
		"""A payroll run with slips generated, prepared by the officer."""
		entry = self.make_entry(group=group)
		frappe.set_user(self.users["officer"])
		entry.create_salary_slips()
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def approved_entry(self, group=None):
		entry = self.calculated_entry(group=group)
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["manager"])
		api.payroll_action(entry.name, wf.APPROVE)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def posted_entry(self, group=None):
		entry = self.approved_entry(group=group)
		frappe.set_user(self.users["finance"])
		entry.submit_salary_slips()
		api.make_bulk_journal_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		return entry


# --------------------------------------------------------------------------- #
# Workflow state machine
# --------------------------------------------------------------------------- #
class TestPayrollWorkflow(ControlFixture):
	def test_new_entry_starts_as_draft(self):
		entry = self.make_entry()
		self.assertEqual(wf.state_of(entry), wf.DRAFT)

	def test_calculating_moves_draft_to_calculated(self):
		entry = self.calculated_entry()
		self.assertEqual(entry.status, wf.CALCULATED)
		self.assertEqual(entry.prepared_by, self.users["officer"])
		self.assertTrue(entry.prepared_at)

	def test_full_happy_path(self):
		entry = self.calculated_entry()

		frappe.set_user(self.users["officer"])
		self.assertEqual(api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)["status"],
		                 wf.PENDING_APPROVAL)

		frappe.set_user(self.users["manager"])
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)

		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		self.assertEqual(api.make_bulk_journal_entry(entry.name)["status"], wf.POSTED)
		self.assertEqual(api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)["status"],
		                 wf.PAYMENT_READY)
		self.assertEqual(api.make_bulk_payment_entry(entry.name)["status"], wf.PAID)
		self.assertEqual(api.payroll_action(entry.name, wf.CLOSE)["status"], wf.CLOSED)

		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.prepared_by, self.users["officer"])
		self.assertEqual(entry.submitted_by, self.users["officer"])
		self.assertEqual(entry.approved_by, self.users["manager"])
		self.assertEqual(entry.posted_by, self.users["finance"])
		self.assertEqual(entry.payment_authorized_by, self.users["finance"])
		self.assertEqual(entry.closed_by, self.users["finance"])

	def test_draft_cannot_jump_to_paid(self):
		entry = self.make_entry()
		frappe.set_user(self.users["finance"])
		for action in (wf.PAY, wf.CLOSE, wf.RELEASE_FOR_PAYMENT, wf.POST):
			with self.assertRaises(frappe.ValidationError):
				api.payroll_action(entry.name, action)

	def test_calculated_cannot_be_posted_without_approval(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_bulk_journal_entry(entry.name)
		self.assertIn("não foi aprovado", str(cm.exception))

	def test_approved_cannot_be_approved_again(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError):
			api.payroll_action(entry.name, wf.APPROVE)

	def test_rejection_requires_a_reason(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError):
			api.payroll_action(entry.name, wf.REJECT)
		with self.assertRaises(frappe.ValidationError):
			api.payroll_action(entry.name, wf.REJECT, reason="   ")

	def test_rejected_payroll_can_be_corrected_and_resubmitted(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)

		frappe.set_user(self.users["manager"])
		api.payroll_action(entry.name, wf.REJECT, reason="Overtime for two employees is wrong")

		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.status, wf.REJECTED)
		self.assertEqual(entry.rejected_by, self.users["manager"])
		self.assertTrue(entry.rejected_at)
		self.assertIn("Overtime", entry.rejection_reason)

		frappe.set_user(self.users["officer"])
		entry.reload()
		entry.create_salary_slips()          # correction + recalculation
		self.assertEqual(entry.status, wf.CALCULATED)
		self.assertEqual(api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)["status"],
		                 wf.PENDING_APPROVAL)

	def test_cancel_is_refused_while_the_ledger_still_holds_the_payroll(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.CANCEL)
		self.assertIn("accounting", str(cm.exception).lower())


# --------------------------------------------------------------------------- #
# Segregation of duties
# --------------------------------------------------------------------------- #
class TestSegregationOfDuties(ControlFixture):
	def test_preparer_cannot_approve_own_payroll(self):
		"""The core control of the phase: same person, both roles, still refused."""
		entry = self.calculated_entry()
		frappe.set_user(self.users["both"])
		entry.reload()
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.APPROVE)
		self.assertIn("próprio", str(cm.exception))
		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.status, wf.PENDING_APPROVAL, "the payroll must stay unapproved")

	def test_another_authorised_user_can_approve(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["manager"])
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)

	def test_approver_cannot_authorise_the_payment(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		api.make_bulk_journal_entry(entry.name)

		# Give the approver the finance role too — identity, not role, must block them.
		manager = frappe.get_doc("User", self.users["manager"])
		manager.append("roles", {"role": perms.PAYROLL_FINANCE})
		manager.save(ignore_permissions=True)
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		self.assertIn("aprovou", str(cm.exception))

	def test_segregation_can_be_relaxed_by_configuration(self):
		"""A company too small to separate the duties can switch the rule off — and the
		default must be the safe setting, not this one."""
		self.assertTrue(wf.requires_separate_approval(),
		                "separate approval must be required by default")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
		                    "require_separate_payroll_approval", 0)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			entry = self.calculated_entry()
			frappe.set_user(self.users["both"])
			api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
			self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
			                    "require_separate_payroll_approval", 1)
			frappe.clear_cache(doctype="Isoft HR Settings")


# --------------------------------------------------------------------------- #
# Approval integrity
# --------------------------------------------------------------------------- #
class TestApprovalIntegrity(ControlFixture):
	def test_approval_stores_a_snapshot(self):
		entry = self.approved_entry()
		self.assertTrue(entry.approval_fingerprint)
		self.assertEqual(entry.approved_employees, 1)
		self.assertAlmostEqual(flt(entry.approved_net), 232948.00, places=2)
		self.assertAlmostEqual(flt(entry.approved_employer_inss), 20800.00, places=2)

	def test_approved_payroll_cannot_be_recalculated(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["officer"])
		with self.assertRaises(frappe.ValidationError):
			entry.reload()
			entry.create_salary_slips()

	def test_approved_salary_slip_cannot_be_edited(self):
		entry = self.approved_entry()
		slip = frappe.get_doc("Isoft Salary Slip", entry.employees[0].salary_slip)
		slip.productivity_bonus = 50000
		with self.assertRaises(frappe.ValidationError) as cm:
			slip.save(ignore_permissions=True)
		message = frappe.utils.strip_html(str(cm.exception))
		self.assertIn("não pode ser alterado", message)
		self.assertIn(entry.name, message)

	def test_posting_is_refused_when_the_numbers_moved_after_approval(self):
		"""Belt and braces: even if a slip is changed by a route that bypasses the lock,
		the approved fingerprint no longer matches and posting stops."""
		entry = self.posted_entry_prep()
		frappe.db.set_value("Isoft Salary Slip", entry.employees[0].salary_slip,
		                    "net_pay", 999999, update_modified=False)
		entry.reload()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.make_bulk_journal_entry(entry.name)
		self.assertIn("alterado depois da aprovação", str(cm.exception))

	def posted_entry_prep(self):
		"""Approved, slips submitted, nothing posted yet."""
		entry = self.approved_entry()
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		frappe.set_user("Administrator")
		entry.reload()
		return entry


# --------------------------------------------------------------------------- #
# Duplicate protection
# --------------------------------------------------------------------------- #
class TestDuplicateProtection(ControlFixture):
	def test_two_runs_for_the_same_period_are_blocked(self):
		self.make_entry(group=PREFIX + " A")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.make_entry(group=PREFIX + " A")
		self.assertIn("already covers", str(cm.exception))

	def test_a_different_payroll_group_is_allowed(self):
		self.make_entry(group=PREFIX + " A")
		entry = self.make_entry(group=PREFIX + " B")
		self.assertTrue(entry.name)

	def test_a_cancelled_run_releases_the_period(self):
		first = self.make_entry(group=PREFIX + " A")
		first.db_set("status", wf.CANCELLED, update_modified=False)
		self.assertTrue(self.make_entry(group=PREFIX + " A").name)

	def test_employee_cannot_be_paid_twice_for_the_same_period(self):
		"""Across DIFFERENT payroll entries — the case the per-run check never saw."""
		first = self.approved_entry(group=PREFIX + " A")
		frappe.set_user(self.users["finance"])
		first.reload()
		first.submit_salary_slips()
		frappe.set_user("Administrator")

		# A second, independent slip for the same employee and period — the route a
		# second payroll run (or a manual slip) would take.
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.get_doc({
				"doctype": "Isoft Salary Slip", "employee": self.employee.name,
				"company": self.company, "posting_date": self.end,
				"start_date": add_days(self.start, 1), "end_date": self.end,
				"salary_profile": self.profile.name,
			}).insert(ignore_permissions=True)
		self.assertIn("already has a submitted Salary Slip", str(cm.exception))


# --------------------------------------------------------------------------- #
# Permissions — invoked through the API, as real users
# --------------------------------------------------------------------------- #
class TestPermissions(ControlFixture):
	SENSITIVE = (
		("payroll_preview", lambda s: dict(company=s.company, start_date=s.start, end_date=s.end)),
		("list_salary_profiles", lambda s: {}),
		("list_payroll_entries", lambda s: {}),
		("list_salary_slips", lambda s: {}),
		("save_settings", lambda s: dict(data="{}")),
		("get_irt_table", lambda s: {}),
	)

	def test_employee_role_is_denied_everywhere(self):
		frappe.set_user(self.users["employee_only"])
		for method, args in self.SENSITIVE:
			with self.assertRaises(frappe.PermissionError,
			                       msg="{0} must reject the Employee role".format(method)):
				getattr(api, method)(**args(self))

	def test_payroll_officer_cannot_approve_post_or_export(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		for action in (wf.APPROVE, wf.REJECT):
			with self.assertRaises(frappe.PermissionError):
				api.payroll_action(entry.name, action, reason="x")
		with self.assertRaises(frappe.PermissionError):
			api.make_bulk_journal_entry(entry.name)
		with self.assertRaises(frappe.PermissionError):
			api.export_bank_transfer(entry.name)

	def test_payroll_manager_cannot_post_accounting_or_pay(self):
		entry = self.approved_entry()
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.PermissionError):
			api.make_bulk_journal_entry(entry.name)
		with self.assertRaises(frappe.PermissionError):
			api.make_bulk_payment_entry(entry.name)

	def test_finance_cannot_approve_payroll(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.PermissionError):
			api.payroll_action(entry.name, wf.APPROVE)

	def test_hr_user_cannot_run_or_approve_payroll(self):
		entry = self.calculated_entry()
		frappe.set_user(self.users["hruser"])
		with self.assertRaises(frappe.PermissionError):
			api.payroll_action(entry.name, wf.APPROVE)
		with self.assertRaises(frappe.PermissionError):
			api.create_payroll_entry(self.company, self.start, self.end)

	def test_bank_payment_list_report_requires_a_finance_role(self):
		from isoft_angola_hr.isoft_angola_hr.report.payroll_bank_payment_list import (
			payroll_bank_payment_list as report,
		)

		filters = {"company": self.company, "from_date": self.start, "to_date": self.end}
		frappe.set_user(self.users["officer"])
		with self.assertRaises(frappe.PermissionError):
			report.execute(filters)
		frappe.set_user(self.users["finance"])
		report.execute(filters)  # must not raise

	def test_matrix_is_derived_from_enforcement(self):
		matrix = perms.permission_matrix()
		rows = {r["action"]: r["roles"] for r in matrix["rows"]}
		self.assertFalse(rows["Approve Payroll"][perms.PAYROLL_OFFICER])
		self.assertTrue(rows["Approve Payroll"][perms.PAYROLL_MANAGER])
		self.assertFalse(rows["Post Accounting"][perms.HR_MANAGER])
		self.assertTrue(rows["Post Accounting"][perms.PAYROLL_FINANCE])
		self.assertFalse(rows["Generate Bank File"][perms.PAYROLL_OFFICER])


# --------------------------------------------------------------------------- #
# Company isolation
# --------------------------------------------------------------------------- #
class TestCompanyIsolation(ControlFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		others = [c for c in frappe.get_all("Company", pluck="name") if c != cls.company]
		cls.other_company = others[0] if others else None

	def _restrict(self, user, company):
		frappe.get_doc({
			"doctype": "User Permission", "user": user, "allow": "Company",
			"for_value": company, "apply_to_all_doctypes": 1,
		}).insert(ignore_permissions=True)

	def test_restricted_user_cannot_touch_another_company(self):
		if not self.other_company:
			self.skipTest("site has a single company")
		self._restrict(self.users["manager"], self.other_company)
		entry = self.calculated_entry()
		frappe.set_user(self.users["officer"])
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)

		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.PermissionError):
			api.payroll_action(entry.name, wf.APPROVE)
		with self.assertRaises(frappe.PermissionError):
			api.get_payroll_entry(entry.name)

	def test_restricted_user_does_not_see_the_other_company_in_lists(self):
		if not self.other_company:
			self.skipTest("site has a single company")
		entry = self.calculated_entry()
		self._restrict(self.users["officer"], self.other_company)
		frappe.set_user(self.users["officer"])
		names = [r["name"] for r in api.list_payroll_entries()]
		self.assertNotIn(entry.name, names)

	def test_restricted_finance_cannot_export_another_company(self):
		if not self.other_company:
			self.skipTest("site has a single company")
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		frappe.set_user("Administrator")
		self._restrict(self.users["finance"], self.other_company)
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.PermissionError):
			api.export_bank_transfer(entry.name)


# --------------------------------------------------------------------------- #
# Bank export authorisation and audit
# --------------------------------------------------------------------------- #
class TestExportControls(ControlFixture):
	def test_export_requires_payment_ready(self):
		entry = self.posted_entry()
		self.assertEqual(entry.status, wf.POSTED)
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.export_bank_transfer(entry.name)
		self.assertIn("Posted", str(cm.exception))

	def test_release_is_blocked_while_an_iban_is_missing(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "")
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		self.assertIn("IBAN", str(cm.exception))

	def test_export_is_audited_and_does_not_mark_payroll_paid(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban",
		                    "AO06000600000100037131174")
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		api.export_bank_transfer(entry.name)
		frappe.response.pop("filecontent", None)

		frappe.set_user("Administrator")
		entry.reload()
		self.assertEqual(entry.status, wf.PAYMENT_READY, "generating a file is not payment")
		self.assertEqual(entry.exported_by, self.users["finance"])
		self.assertEqual(entry.export_count, 1)
		self.assertEqual(entry.export_employee_count, 1)
		self.assertAlmostEqual(flt(entry.export_total), 232948.00, places=2)


# --------------------------------------------------------------------------- #
# Amendment
# --------------------------------------------------------------------------- #
class TestSalarySlipAmendment(ControlFixture):
	def test_cancel_amend_resubmit_cycle(self):
		entry = self.posted_entry()
		slip_name = entry.employees[0].salary_slip
		slip = frappe.get_doc("Isoft Salary Slip", slip_name)

		# Correction path: cancel the accounting, cancel the payroll, then amend.
		frappe.get_doc("Journal Entry", slip.journal_entry).cancel()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.CANCEL)
		frappe.set_user("Administrator")
		slip.reload()
		slip.cancel()

		amended = frappe.copy_doc(slip)
		amended.amended_from = slip.name
		amended.docstatus = 0
		amended.payroll_entry = None
		amended.productivity_bonus = 10000
		amended.insert(ignore_permissions=True)

		self.assertNotEqual(amended.name, slip.name, "the amendment needs its own name")
		self.assertEqual(amended.name, slip.name + "-1")
		self.assertEqual(amended.amended_from, slip.name)
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip.name, "docstatus"), 2)

		amended.submit()
		self.assertEqual(amended.docstatus, 1)
		self.assertAlmostEqual(flt(amended.gross_pay), flt(slip.gross_pay) + 10000, places=2)

	def test_amendment_replaces_the_original_in_its_payroll_entry(self):
		entry = self.calculated_entry()
		original = entry.employees[0].salary_slip
		slip = frappe.get_doc("Isoft Salary Slip", original)

		amended = frappe.copy_doc(slip)
		amended.amended_from = slip.name
		amended.docstatus = 0
		# The original must be cancelled for a real amendment; here the entry link is
		# what is under test, so the draft is simply deleted after re-pointing.
		frappe.db.set_value("Isoft Salary Slip", original, "docstatus", 2, update_modified=False)
		amended.insert(ignore_permissions=True)

		row = frappe.db.get_value("Isoft Payroll Employee",
		                          {"parent": entry.name, "employee": self.employee.name},
		                          "salary_slip")
		self.assertEqual(row, amended.name,
		                 "the payroll run must follow the amendment, not the cancelled slip")


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
class TestPayrollReadiness(ControlFixture):
	def _evaluate(self):
		return readiness.evaluate(self.company, self.start, self.end, include_variance=False)

	def _codes_for(self, report, employee):
		return {e["code"] for e in report["exceptions"] if e["employee"] == employee}

	def test_a_valid_employee_is_ready(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "AO0600060000010003713")
		frappe.db.set_value("Employee", self.employee.name, "custom_nif", "5417000000")
		frappe.db.set_value("Employee", self.employee.name, "custom_inss_number", "0123456789")
		report = self._evaluate()
		self.assertEqual(self._codes_for(report, self.employee.name), set())

	def test_missing_profile_blocks(self):
		employee = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "NoProfile",
			"company": self.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True)
		report = self._evaluate()
		self.assertIn("EXC-001", self._codes_for(report, employee.name))
		self.assertFalse(report["can_calculate"])

	def test_ambiguous_profile_blocks(self):
		employee = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Ambiguous",
			"company": self.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True)
		# Reproduces how the live conflict arose: two profiles created with different
		# effective dates, one of them later edited onto the other's date. The names were
		# already assigned, so both records survive with the same from_date.
		for from_date, base in (("2020-01-01", 100000), ("2020-02-01", 160000)):
			doc = frappe.get_doc({
				"doctype": "Isoft Salary Profile", "employee": employee.name,
				"company": self.company, "from_date": from_date, "base": base,
			})
			# ignore_validate reproduces data that predates the overlap guard. The guard
			# now refuses to CREATE this state; readiness must still DETECT it where it
			# already exists, which is what this test asserts.
			doc.flags.ignore_validate = True
			doc.insert(ignore_permissions=True)
			doc.db_set("from_date", "2020-01-01", update_modified=False)
		report = self._evaluate()
		self.assertIn("EXC-002", self._codes_for(report, employee.name))

	def test_missing_iban_is_a_payment_blocker_not_a_calculation_blocker(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "")
		report = self._evaluate()
		codes = self._codes_for(report, self.employee.name)
		self.assertIn("EXC-007", codes)
		blocking = {e["code"] for e in report["exceptions"] if e["severity"] == readiness.BLOCKING}
		self.assertNotIn("EXC-007", blocking)
		self.assertEqual(
			[e["severity"] for e in report["exceptions"] if e["code"] == "EXC-007"][0],
			readiness.PAYMENT)

	def test_missing_nif_and_inss_are_warnings(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_nif", "")
		frappe.db.set_value("Employee", self.employee.name, "custom_inss_number", "")
		report = self._evaluate()
		codes = self._codes_for(report, self.employee.name)
		self.assertIn("EXC-008", codes)
		self.assertIn("EXC-009", codes)
		for exc in report["exceptions"]:
			if exc["code"] in ("EXC-008", "EXC-009"):
				self.assertEqual(exc["severity"], readiness.WARNING)

	def test_mid_period_salary_change_is_reported_as_a_blocker(self):
		# A mid-period profile stacked on an open one is exactly what the overlap guard
		# now prevents; ignore_validate recreates the legacy state so the readiness
		# engine can be shown still reporting it.
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": add_days(self.start, 10),
			"base": 400000, "food_allowance": 30000, "transport_allowance": 30000,
		})
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		report = self._evaluate()
		self.assertIn("EXC-011", self._codes_for(report, self.employee.name))

	def test_configuration_gap_is_detected_before_payroll_runs(self):
		backup = frappe.db.get_single_value("Isoft HR Settings", "payroll_payable_account")
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
		                    "payroll_payable_account", None)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			config = readiness.configuration_status(self.company, on_date=self.end)
			payable = [c for c in config if c["key"] == "payroll_payable_account"][0]
			self.assertFalse(payable["ok"])
			self.assertEqual(payable["status"], "Missing")
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings",
			                    "payroll_payable_account", backup)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_submission_for_approval_is_blocked_by_a_negative_net(self):
		entry = self.calculated_entry()
		frappe.db.set_value("Isoft Salary Slip", entry.employees[0].salary_slip,
		                    "net_pay", -100, update_modified=False)
		frappe.set_user(self.users["officer"])
		entry.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		self.assertIn("negative net pay", str(cm.exception))

	def test_readiness_is_denied_to_the_employee_role(self):
		frappe.set_user(self.users["employee_only"])
		with self.assertRaises(frappe.PermissionError):
			readiness.evaluate(self.company, self.start, self.end)


# --------------------------------------------------------------------------- #
# Reports reconcile with the payroll they report on
# --------------------------------------------------------------------------- #
class TestReports(ControlFixture):
	def _filters(self):
		return {"company": self.company, "from_date": self.start, "to_date": self.end,
		        "docstatus": "Submitted"}

	def test_irt_report_total_matches_the_submitted_slips(self):
		from isoft_angola_hr.isoft_angola_hr.report.irt_payroll_report import (
			irt_payroll_report as report,
		)

		entry = self.posted_entry()
		slip = frappe.get_doc("Isoft Salary Slip", entry.employees[0].salary_slip)
		columns, data = report.execute(self._filters())
		total = data[-1]
		self.assertAlmostEqual(flt(total["irt_amount"]), flt(slip.irt_amount), places=2)
		self.assertAlmostEqual(flt(total["taxable_income"]), flt(slip.taxable_income), places=2)
		# The bracket is reported from the snapshot, not recomputed.
		digits = "".join(c for c in data[0]["bracket"] if c.isdigit())
		self.assertIn("15000000", digits, "the bracket must be reported from the snapshot")

	def test_inss_report_totals_match_the_submitted_slips(self):
		from isoft_angola_hr.isoft_angola_hr.report.inss_contribution_report import (
			inss_contribution_report as report,
		)

		entry = self.posted_entry()
		slip = frappe.get_doc("Isoft Salary Slip", entry.employees[0].salary_slip)
		columns, data = report.execute(self._filters())
		total = data[-1]
		self.assertAlmostEqual(flt(total["ss_employee_amount"]), flt(slip.ss_employee_amount), places=2)
		self.assertAlmostEqual(flt(total["ss_employer_amount"]), flt(slip.ss_employer_amount), places=2)
		self.assertAlmostEqual(flt(total["total_contribution"]),
		                       flt(slip.ss_employee_amount) + flt(slip.ss_employer_amount), places=2)

	def test_payroll_register_net_matches_the_payroll_entry(self):
		from isoft_angola_hr.isoft_angola_hr.report.payroll_register import (
			payroll_register as report,
		)

		entry = self.posted_entry()
		columns, data = report.execute(self._filters())
		total = data[-1]
		self.assertAlmostEqual(flt(total["net_pay"]), flt(entry.total_net_pay), places=2)
		self.assertAlmostEqual(flt(total["employer_cost"]),
		                       flt(total["gross_pay"]) + flt(total["ss_employer_amount"]), places=2)

	def test_historical_report_does_not_follow_a_later_rate_change(self):
		"""Section 36: submitted payroll is reported from its snapshot.

		A new statutory rate taking effect today must not retro-change what the INSS
		report says about payroll that has already been calculated.
		"""
		from isoft_angola_hr.isoft_angola_hr.report.inss_contribution_report import (
			inss_contribution_report as report,
		)

		entry = self.posted_entry()
		before = report.execute(self._filters())[1][-1]
		frappe.get_doc({
			"doctype": "Isoft Statutory Rate", "effective_from": self.start,
			"company": self.company, "ss_employee_rate": 11, "ss_employer_rate": 21,
			"food_allowance_exemption": 30000, "transport_allowance_exemption": 30000,
		}).insert(ignore_permissions=True)
		after = report.execute(self._filters())[1][-1]
		self.assertAlmostEqual(flt(before["ss_employee_amount"]),
		                       flt(after["ss_employee_amount"]), places=2)

	def test_audit_trail_shows_who_did_each_step(self):
		from isoft_angola_hr.isoft_angola_hr.report.payroll_audit_trail import (
			payroll_audit_trail as report,
		)

		entry = self.posted_entry()
		columns, data = report.execute({"company": self.company})
		row = [r for r in data if r.get("payroll_entry") == entry.name][0]
		self.assertEqual(row["submitted_by"], self.users["officer"])
		self.assertEqual(row["approved_by"], self.users["manager"])
		self.assertEqual(row["posted_by"], self.users["finance"])
		self.assertEqual(row["segregated"], 1)

	def test_statutory_rate_audit_flags_in_use_configuration(self):
		from isoft_angola_hr.isoft_angola_hr.report.statutory_rate_audit import (
			statutory_rate_audit as report,
		)

		entry = self.posted_entry()
		table = frappe.db.get_value("Isoft Salary Slip", entry.employees[0].salary_slip,
		                            "irt_table")
		columns, data = report.execute({})
		row = [r for r in data if r["rule"] == table][0]
		self.assertGreaterEqual(row["used_by_submitted_payroll"], 1)
		self.assertEqual(row["locked"], 1)


# --------------------------------------------------------------------------- #
# Dead settings are now live settings
# --------------------------------------------------------------------------- #
class TestComponentToggles(ControlFixture):
	def test_a_disabled_component_cannot_be_paid(self):
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 0)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			with self.assertRaises(frappe.ValidationError) as cm:
				self.make_slip(submit=False, overtime_amount=50000)
			self.assertIn("Horas Extras", str(cm.exception))
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 1)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_an_unchanged_historical_amount_is_never_rewritten(self):
		"""Switching a component off must not silently zero payroll that already carries
		an amount — it governs what may be entered from now on."""
		slip = self.make_slip(submit=False, overtime_amount=50000)
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 0)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			slip.reload()
			slip.save(ignore_permissions=True)
			self.assertAlmostEqual(flt(slip.overtime_amount), 50000.00, places=2)
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 1)
			frappe.clear_cache(doctype="Isoft HR Settings")

	def test_family_allowance_toggle_is_honoured_by_the_engine(self):
		from isoft_angola_hr.isoft_angola_hr.payroll import engine

		self.assertFalse(engine.component_enabled({"enable_family_allowance": 0}, "AF"))
		self.assertTrue(engine.component_enabled({"enable_family_allowance": 1}, "AF"))
		# An absent key must mean enabled, so a caller supplying its own settings never
		# loses a component it did not know about.
		self.assertTrue(engine.component_enabled({}, "AF"))
