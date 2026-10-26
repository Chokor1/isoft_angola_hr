# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 4 tests: self-service UX, contract documents, bulk onboarding, recruitment,
analytics, statutory filing and the security boundary around all of it.

SAFETY — unchanged from every earlier phase. Every record this file creates is prefixed
``_TEST AHR``, is registered for explicit deletion, and each test runs inside a savepoint.
No existing employee, salary profile or payroll slip is written to.

The security tests are the point of this file. Phase 3's self-service was safe because
its services took no employee parameter; Phase 4 adds a payslip PDF and a private file
download, both served by Frappe rather than by those services, so the boundary had to
move down to the record. :class:`TestSelfServiceIsolation` and
:class:`TestRecordPermissions` are what prove it actually did.
"""

import re
import unittest

import frappe
from frappe.utils import cint, add_days, add_months, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr import doc_permissions
from isoft_angola_hr.isoft_angola_hr import hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p3_hr import HRFixture
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law
from isoft_angola_hr.isoft_angola_hr.services import bulk_onboarding
from isoft_angola_hr.isoft_angola_hr.services import contract_documents as cd
from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss
from isoft_angola_hr.isoft_angola_hr.services import org_analytics
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import recruitment
from isoft_angola_hr.isoft_angola_hr.services import statutory_filing
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class UXFixture(HRFixture):
	"""HR fixture plus a second, unrelated employee — the one nobody may reach."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# An employee in no relationship with anyone: not the caller, not their report.
		# Every isolation test is "can the caller see THIS person", so it has to exist.
		cls.stranger_user = cls._make_user("stranger", ["Employee"])
		cls.stranger = cls._employee("Stranger", user=cls.stranger_user)

	def submitted_entry(self, group=None):
		"""A payroll run whose slips are actually submitted.

		Statutory declarations are built from docstatus = 1 slips only — an approved
		payroll entry whose slips are still drafts must not produce a declaration.
		"""
		entry = self.approved_entry(group=group)
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def make_template(self, body=None, include_salary=0, contract_type=None, active=1):
		name = "{0} Template {1}".format(PREFIX, frappe.generate_hash(length=6))
		doc = frappe.get_doc({
			"doctype": "Isoft Contract Template", "template_name": name,
			"contract_type": contract_type, "language": "pt", "is_active": active,
			"include_salary": include_salary,
			"body": body if body is not None else
			"<p>Contrato entre {{ company }} e {{ employee_name }} ({{ employee_id }}), "
			"com início em {{ start_date }} e termo em {{ end_date }}.</p>",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Contract Template", doc.name))
		self.addCleanup(lambda: None)
		return doc


# --------------------------------------------------------------------------- #
# §85 — Employee Self-Service
# --------------------------------------------------------------------------- #
class TestEmployeeSelfService(UXFixture):
	def test_own_profile_is_returned_and_iban_is_masked(self):
		frappe.set_user(self.employee_user)
		profile = ess.my_profile()
		self.assertEqual(profile["name"], self.report_employee)
		# The full account number must never leave the server for a web page.
		self.assertNotIn("custom_iban", profile)
		self.assertIn("iban_masked", profile)

	def test_profile_edit_is_limited_to_the_whitelist(self):
		frappe.set_user(self.employee_user)
		ess.update_my_profile({"cell_number": "+244 900 000 000"})
		self.assertEqual(
			frappe.db.get_value("Employee", self.report_employee, "cell_number"),
			"+244 900 000 000")

	def test_employee_cannot_edit_salary_department_or_statutory_fields(self):
		frappe.set_user(self.employee_user)
		# Each of these would be a privilege escalation if it worked.
		for field, value in (("designation", "CEO"), ("custom_nif", "999"),
		                     ("custom_iban", "AO0600000000000000000"),
		                     ("reports_to", self.stranger), ("department", "Anything")):
			with self.assertRaises(frappe.PermissionError):
				ess.update_my_profile({field: value})

	def test_attendance_summary_counts_open_occurrences(self):
		frappe.set_user(self.employee_user)
		summary = ess.attendance_summary()
		self.assertIn("open_occurrences", summary)
		self.assertIsInstance(summary["open_occurrences"], int)

	def test_leave_balance_delegates_to_erpnext(self):
		frappe.set_user(self.employee_user)
		rows = ess.my_leave_balance()
		self.assertIsInstance(rows, list)
		for row in rows:
			# The four numbers the screen shows must all be present, or the UI would
			# silently render blanks where an entitlement should be.
			for key in ("leave_type", "entitlement", "used", "pending", "available"):
				self.assertIn(key, row)

	def test_dashboard_is_one_call_and_carries_every_panel(self):
		frappe.set_user(self.employee_user)
		data = ess.dashboard()
		for key in ("profile", "latest_payslips", "leave_balance", "open_requests",
		            "advances", "open_leave", "expiring_documents", "attendance_summary"):
			self.assertIn(key, data)

	def test_advance_request_records_the_caller_not_a_supplied_employee(self):
		frappe.set_user(self.employee_user)
		result = ess.request_advance(50000, reason="Test advance")
		self._created.append(("Isoft Salary Advance", result["name"]))
		self.assertEqual(
			frappe.db.get_value("Isoft Salary Advance", result["name"], "employee"),
			self.report_employee)

	def test_advance_request_refuses_a_zero_amount_or_a_missing_reason(self):
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess.request_advance(0, reason="x")
		with self.assertRaises(frappe.ValidationError):
			ess.request_advance(1000, reason="   ")

	def test_bank_change_is_a_request_and_does_not_touch_the_employee(self):
		frappe.set_user(self.employee_user)
		before = frappe.db.get_value("Employee", self.report_employee, "custom_iban")
		name = ess.request_bank_change("AO06000600000100037131174")
		self._created.append(("Isoft Bank Change Request", name))
		self.assertEqual(
			frappe.db.get_value("Employee", self.report_employee, "custom_iban"), before,
			"A bank change request must not change the account until HR approves it.")


# --------------------------------------------------------------------------- #
# §78, §79, §85 — the isolation boundary
# --------------------------------------------------------------------------- #
class TestSelfServiceIsolation(UXFixture):
	def _slip_of(self, employee):
		"""A submitted salary slip belonging to somebody else."""
		slip = frappe.db.get_value(
			"Isoft Salary Slip", {"employee": employee, "docstatus": 1}, "name")
		return slip

	def test_employee_cannot_read_another_employees_payslip(self):
		entry = self.submitted_entry()
		slip = frappe.db.get_value(
			"Isoft Salary Slip", {"payroll_entry": entry.name}, "name")
		frappe.db.set_value("Isoft Salary Slip", slip, "docstatus", 1)
		frappe.set_user(self.employee_user)
		# The slip belongs to cls.employee, not to the logged-in report_employee.
		with self.assertRaises(frappe.PermissionError):
			ess.my_payslip(slip)

	def test_draft_payslip_is_refused_even_to_its_owner(self):
		entry = self.calculated_entry()
		slip = frappe.db.get_value(
			"Isoft Salary Slip", {"payroll_entry": entry.name}, "name")
		owner = frappe.db.get_value("Isoft Salary Slip", slip, "employee")
		user = frappe.db.get_value("Employee", owner, "user_id")
		if not user:
			frappe.db.set_value("Employee", owner, "user_id", self.employee_user)
			user = self.employee_user
			self.addCleanup(
				lambda: frappe.db.set_value("Employee", owner, "user_id", None))
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			ess.my_payslip(slip)

	def test_employee_cannot_read_another_employees_document(self):
		doc = frappe.get_doc({
			"doctype": "Isoft Employee Document", "employee": self.stranger,
			"document_type": self._document_type(), "issue_date": nowdate(),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Employee Document", doc.name))
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess.my_document(doc.name)

	def test_confidential_document_is_hidden_from_its_own_subject(self):
		# Confidentiality is a property of the document TYPE, enforced server-side —
		# setting the flag on the record would be overwritten, which is the point.
		doc = frappe.get_doc({
			"doctype": "Isoft Employee Document", "employee": self.report_employee,
			"document_type": self._document_type(confidential=True),
			"issue_date": nowdate(),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Employee Document", doc.name))
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess.my_document(doc.name)
		self.assertNotIn(doc.name, [r["name"] for r in ess.my_documents()])

	def test_employee_cannot_withdraw_another_employees_leave(self):
		leave = self._leave_for(self.stranger)
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess.cancel_leave(leave)

	def test_self_service_context_never_reports_manager_for_a_plain_employee(self):
		frappe.set_user(self.stranger_user)
		ctx = hr_api.self_service_context()
		self.assertEqual(ctx["employee"], self.stranger)
		self.assertFalse(ctx["is_manager"])
		self.assertEqual(ctx["team_size"], 0)
		self.assertFalse(ctx["can_see_compensation"])

	# -- helpers ---------------------------------------------------------- #
	def _document_type(self, confidential=False):
		name = PREFIX + (" DocType P4 Conf" if confidential else " DocType P4")
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({
				"doctype": "Isoft Document Type", "document_type": name,
				"is_confidential": 1 if confidential else 0,
			}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		return name

	def _leave_for(self, employee):
		leave_type = frappe.get_all("Leave Type", pluck="name")[0]
		doc = frappe.get_doc({
			"doctype": "Leave Application", "employee": employee,
			"leave_type": leave_type, "from_date": add_days(nowdate(), 40),
			"to_date": add_days(nowdate(), 40), "status": "Open",
			"company": self.company,
		})
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True)
		self._created.append(("Leave Application", doc.name))
		return doc.name


# --------------------------------------------------------------------------- #
# §81 — record-level permission, which is what guards PDFs and private files
# --------------------------------------------------------------------------- #
class TestRecordPermissions(UXFixture):
	def test_query_condition_scopes_a_list_to_the_caller(self):
		condition = doc_permissions.salary_slip_query(self.employee_user)
		self.assertIn(self.report_employee, condition)
		self.assertIn("docstatus = 1", condition,
		              "A list of payslips must exclude drafts, not just other people.")

	def test_query_condition_returns_nothing_for_a_user_with_no_employee(self):
		# `1=0`, not None. Returning None would mean "no restriction" — every payslip.
		self.assertEqual(doc_permissions.salary_slip_query("Guest"), "1=0")

	def test_staff_roles_are_not_restricted_by_the_hook(self):
		self.assertIsNone(doc_permissions.salary_slip_query(self.users["hruser"]))
		self.assertIsNone(doc_permissions.employee_document_query(self.users["officer"]))

	def test_has_permission_refuses_another_employees_record(self):
		doc = {"employee": self.stranger, "docstatus": 1}
		self.assertFalse(
			doc_permissions.salary_slip_permission(doc, "read", self.employee_user))
		doc = {"employee": self.report_employee, "docstatus": 1}
		self.assertTrue(
			doc_permissions.salary_slip_permission(doc, "read", self.employee_user))

	def test_has_permission_refuses_a_draft_slip(self):
		doc = {"employee": self.report_employee, "docstatus": 0}
		self.assertFalse(
			doc_permissions.salary_slip_permission(doc, "read", self.employee_user))

	def test_confidential_document_permission_is_false_for_the_subject(self):
		doc = {"employee": self.report_employee, "confidential": 1}
		self.assertFalse(
			doc_permissions.employee_document_permission(doc, "read", self.employee_user))
		doc["confidential"] = 0
		self.assertTrue(
			doc_permissions.employee_document_permission(doc, "read", self.employee_user))

	def test_unapproved_contract_is_not_visible_to_the_employee(self):
		for status in ("Draft", "Pending Approval"):
			self.assertFalse(
				doc_permissions.contract_permission(
					{"employee": self.report_employee, "status": status}, "read",
					self.employee_user),
				"An unapproved contract is a proposal, not something the employee holds.")
		self.assertTrue(
			doc_permissions.contract_permission(
				{"employee": self.report_employee, "status": "Active"}, "read",
				self.employee_user))

	def test_employee_role_has_read_but_not_export_on_payroll(self):
		"""§82 — an employee must not be able to pull the table through the report API."""
		perms_rows = frappe.get_all(
			"DocPerm", filters={"parent": "Isoft Salary Slip", "role": "Employee"},
			fields=["read", "export", "report", "write", "create", "delete"])
		self.assertTrue(perms_rows, "The Employee role needs read for the payslip PDF.")
		row = perms_rows[0]
		self.assertTrue(row["read"])
		for forbidden in ("export", "report", "write", "create", "delete"):
			self.assertFalse(row[forbidden],
			                 "Employee must not hold {0} on Isoft Salary Slip.".format(
				                 forbidden))


# --------------------------------------------------------------------------- #
# §86 — Manager Self-Service
# --------------------------------------------------------------------------- #
class TestManagerSelfService(UXFixture):
	def test_direct_report_is_visible(self):
		frappe.set_user(self.manager_user)
		self.assertIn(self.report_employee, mss.team())

	def test_indirect_report_is_excluded_unless_asked_for(self):
		grandchild = self._employee("Grandchild", reports_to=self.report_employee)
		frappe.set_user(self.manager_user)
		self.assertNotIn(grandchild, mss.team())
		self.assertIn(grandchild, mss.team(include_indirect=True))

	def test_unrelated_employee_is_refused(self):
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.PermissionError):
			mss.team_member(self.stranger)

	def test_manager_cannot_see_compensation(self):
		frappe.set_user(self.manager_user)
		self.assertFalse(mss.can_see_compensation())
		member = mss.team_member(self.report_employee)
		self.assertFalse(member["compensation_visible"])
		for leaked in ("base", "salary", "net_pay", "custom_iban", "irt_amount"):
			self.assertNotIn(leaked, member)

	def test_manager_cannot_reach_payroll_endpoints(self):
		frappe.set_user(self.manager_user)
		for action in (perms.PAYROLL_READ, perms.REPORT_PAYROLL, perms.REPORT_BANK,
		               perms.SALARY_PROFILE_READ, perms.REPORT_STATUTORY):
			self.assertFalse(perms.can(action),
			                 "A line manager must not hold {0}.".format(action))

	def test_manager_approves_only_their_own_teams_leave(self):
		outsider_leave = TestSelfServiceIsolation._leave_for(self, self.stranger)
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.PermissionError):
			mss.leave_decision(outsider_leave, "approve")

	def test_probation_recommendation_records_but_does_not_decide(self):
		contract = self.approved_contract(
			employee=self.report_employee, start=add_days(nowdate(), -30),
			end=add_days(nowdate(), 300))
		frappe.db.set_value("Isoft Employment Contract", contract.name, {
			"probation_start": add_days(nowdate(), -30),
			"probation_end": add_days(nowdate(), 10)})
		frappe.set_user(self.manager_user)
		mss.probation_recommendation(contract.name, "Confirm", notes="Doing well")
		contract.reload()
		self.assertEqual(contract.manager_recommendation, "Confirm")
		self.assertEqual(contract.manager_recommendation_by, self.manager_user)
		# The recommendation must NOT have decided the probation.
		self.assertFalse(contract.probation_decision,
		                 "A manager recommendation must never confirm a probation.")

	def test_recommendation_is_refused_for_somebody_elses_contract(self):
		contract = self.approved_contract(employee=self.stranger)
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.PermissionError):
			mss.probation_recommendation(contract.name, "Confirm")

	def test_invalid_recommendation_is_rejected(self):
		contract = self.approved_contract(employee=self.report_employee)
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.ValidationError):
			mss.probation_recommendation(contract.name, "Promote Immediately")

	def test_approval_inbox_only_contains_the_team(self):
		frappe.set_user(self.manager_user)
		team = set(mss.team())
		for row in mss.approval_inbox():
			self.assertIn(row["employee"], team)


# --------------------------------------------------------------------------- #
# §88 — contract templates and the rendering engine
# --------------------------------------------------------------------------- #
class TestContractDocuments(UXFixture):
	def test_placeholders_are_substituted(self):
		contract = self.approved_contract()
		template = self.make_template()
		result = cd.generate(contract.name, template=template.name)
		self._created.append(("Isoft Contract Document", result["name"]))
		body = frappe.db.get_value("Isoft Contract Document", result["name"], "body")
		self.assertIn(self.employee.employee_name, body)
		self.assertIn(self.company, body)
		self.assertNotIn("{{", body)

	def test_jinja_expressions_are_never_executed(self):
		"""The security property this whole design exists for.

		Frappe's Jinja environment exposes ``frappe``. If templates were rendered with it,
		a template body written by an HR user would be arbitrary code execution.
		"""
		dangerous = (
			"{{ frappe.db.sql('select name from tabUser') }}",
			"{{ frappe.get_doc('User', 'Administrator').api_secret }}",
			"{{ self.__class__.__mro__ }}",
			"{{ ''.__class__.__base__.__subclasses__() }}",
			"{% for u in frappe.get_all('User') %}{{ u.name }}{% endfor %}",
			"{{ config.items() }}",
			"{{ 7 * 7 }}",
		)
		context = cd.build_context(self.approved_contract().name)
		for payload in dangerous:
			body, _unresolved = cd.render(payload, context)
			# The strongest statement available: the output is byte-identical to the
			# input, so nothing was parsed, evaluated or substituted.
			self.assertEqual(body, payload,
			                 "A dangerous expression must be left exactly as written.")

		# And the specific evaluations, in case the equality above is ever loosened.
		body, _unresolved = cd.render("{{ 7 * 7 }}", context)
		self.assertNotIn("49", body, "Arithmetic must not be evaluated.")
		body, _unresolved = cd.render("{{ ''.__class__.__base__.__subclasses__() }}", context)
		self.assertNotIn("<class", body, "Python objects must not be reachable.")
		secret = frappe.db.get_value("User", "Administrator", "api_secret")
		body, _unresolved = cd.render(
			"{{ frappe.get_doc('User', 'Administrator').api_secret }}", context)
		if secret:
			self.assertNotIn(secret, body, "A credential must never be reachable.")

	def test_unknown_placeholder_is_left_visible_rather_than_blanked(self):
		context = cd.build_context(self.approved_contract().name)
		body, unresolved = cd.render("Salário: {{ secret_salary }}", context)
		self.assertIn("{{ secret_salary }}", body)
		self.assertIn("secret_salary", unresolved)

	def test_template_with_code_is_refused_on_save(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_template(body="{% if 1 %}x{% endif %}")

	def test_salary_placeholder_needs_both_the_template_flag_and_the_permission(self):
		contract = self.approved_contract()
		template = self.make_template(
			body="Base: {{ base_salary }}", include_salary=1)
		# HR User holds SALARY_PROFILE_READ, so it resolves.
		frappe.set_user(self.users["hruser"])
		result = cd.generate(contract.name, template=template.name)
		self._created.append(("Isoft Contract Document", result["name"]))
		self.assertTrue(result["salary_included"])
		body = frappe.db.get_value("Isoft Contract Document", result["name"], "body")
		self.assertNotIn("{{ base_salary }}", body)

	def test_version_increases_when_the_body_changes_and_issued_text_is_untouched(self):
		"""§32 — an issued contract must stay exactly as it was issued."""
		contract = self.approved_contract()
		template = self.make_template(body="<p>Versão 1 — {{ employee_name }}</p>")
		self.assertEqual(template.version, 1)
		first = cd.generate(contract.name, template=template.name)
		self._created.append(("Isoft Contract Document", first["name"]))
		original_body = frappe.db.get_value("Isoft Contract Document", first["name"], "body")

		template.body = "<p>Versão 2 — {{ employee_name }}</p>"
		template.save(ignore_permissions=True)
		self.assertEqual(template.version, 2)

		self.assertEqual(
			frappe.db.get_value("Isoft Contract Document", first["name"], "body"),
			original_body,
			"Editing a template must not rewrite a document already issued from it.")
		self.assertEqual(
			frappe.db.get_value("Isoft Contract Document", first["name"], "template_version"),
			1)

	def test_regenerating_supersedes_rather_than_overwrites(self):
		contract = self.approved_contract()
		template = self.make_template()
		first = cd.generate(contract.name, template=template.name)
		second = cd.generate(contract.name, template=template.name)
		self._created.extend([("Isoft Contract Document", first["name"]),
		                      ("Isoft Contract Document", second["name"])])
		self.assertNotEqual(first["name"], second["name"])
		self.assertEqual(
			frappe.db.get_value("Isoft Contract Document", first["name"], "status"),
			"Superseded")

	def test_issued_body_cannot_be_edited(self):
		contract = self.approved_contract()
		template = self.make_template()
		result = cd.generate(contract.name, template=template.name)
		self._created.append(("Isoft Contract Document", result["name"]))
		doc = frappe.get_doc("Isoft Contract Document", result["name"])
		doc.body = "<p>Something else entirely</p>"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_finalise_then_sign(self):
		contract = self.approved_contract()
		template = self.make_template()
		result = cd.generate(contract.name, template=template.name)
		self._created.append(("Isoft Contract Document", result["name"]))
		self.assertEqual(cd.finalise(result["name"]), "Final")
		with self.assertRaises(frappe.ValidationError):
			cd.finalise(result["name"])
		self.assertEqual(
			cd.attach_signed(result["name"], "/private/files/fake-signed.pdf"), "Signed")

	def test_generation_without_a_template_says_so(self):
		contract = self.approved_contract()
		for name in frappe.get_all("Isoft Contract Template",
		                           filters={"is_active": 1}, pluck="name"):
			frappe.db.set_value("Isoft Contract Template", name, "is_active", 0)
		with self.assertRaises(frappe.ValidationError):
			cd.generate(contract.name)

	def test_html_in_a_value_is_escaped(self):
		"""A stored XSS through an employee name must not reach the rendered contract."""
		context = cd.build_context(self.approved_contract().name)
		context["employee_name"] = "<script>alert(1)</script>"
		body, _unresolved = cd.render("Nome: {{ employee_name }}", context)
		self.assertNotIn("<script>", body)
		self.assertIn("&lt;script&gt;", body)


# --------------------------------------------------------------------------- #
# §87 — bulk contract creation
# --------------------------------------------------------------------------- #
class TestBulkContracts(UXFixture):
	def _fresh(self, count):
		out = []
		for i in range(count):
			out.append(self._employee("Bulk{0}".format(i)))
		return out

	def test_preview_writes_nothing(self):
		employees = self._fresh(3)
		before = frappe.db.count("Isoft Employment Contract")
		plan = bulk_onboarding.preview(employees, self.contract_type,
		                               start_date="2026-01-01", end_date="2026-12-31")
		self.assertEqual(frappe.db.count("Isoft Employment Contract"), before)
		self.assertEqual(plan["summary"]["create"], 3)

	def test_execute_creates_one_contract_each(self):
		employees = self._fresh(4)
		result = bulk_onboarding.execute(employees, self.contract_type,
		                                 start_date="2026-01-01", end_date="2026-12-31")
		for row in result["created"]:
			self._created.append(("Isoft Employment Contract", row["contract"]))
		self.assertEqual(result["summary"]["created"], 4)
		self.assertEqual(result["summary"]["failed"], 0)

	def test_employee_with_an_existing_contract_is_skipped_not_duplicated(self):
		existing = self.approved_contract()
		fresh = self._fresh(1)
		plan = bulk_onboarding.preview([self.employee.name] + fresh, self.contract_type,
		                               start_date="2026-06-01", end_date="2026-12-31")
		actions = {r["employee"]: r["action"] for r in plan["rows"]}
		self.assertEqual(actions[self.employee.name], bulk_onboarding.SKIP)
		self.assertEqual(actions[fresh[0]], bulk_onboarding.CREATE)

	def test_running_twice_does_not_create_duplicates(self):
		"""§38 — a retry after a partial run must be safe."""
		employees = self._fresh(3)
		first = bulk_onboarding.execute(employees, self.contract_type,
		                                start_date="2026-01-01", end_date="2026-12-31")
		for row in first["created"]:
			self._created.append(("Isoft Employment Contract", row["contract"]))
		second = bulk_onboarding.execute(employees, self.contract_type,
		                                 start_date="2026-01-01", end_date="2026-12-31")
		self.assertEqual(second["summary"]["created"], 0)
		self.assertEqual(second["summary"]["skipped"], 3)
		self.assertEqual(
			frappe.db.count("Isoft Employment Contract",
			                {"employee": ("in", employees)}), 3)

	def test_blocked_rows_carry_a_reason_and_are_not_silently_dropped(self):
		"""§36 — nothing is hidden behind a summary count."""
		inactive = self._employee("Inactive")
		frappe.db.set_value("Employee", inactive, "status", "Left")
		plan = bulk_onboarding.preview([inactive], self.contract_type,
		                               start_date="2026-01-01", end_date="2026-12-31")
		row = plan["rows"][0]
		self.assertEqual(row["action"], bulk_onboarding.BLOCK)
		self.assertTrue(row["reason"])
		self.assertEqual(plan["summary"]["blocked"], 1)
		self.assertEqual(len(plan["rows"]), 1, "A blocked employee still appears in the preview.")

	def test_end_before_start_is_blocked(self):
		employees = self._fresh(1)
		plan = bulk_onboarding.preview(employees, self.contract_type,
		                               start_date="2026-12-01", end_date="2026-01-01")
		self.assertEqual(plan["rows"][0]["action"], bulk_onboarding.BLOCK)

	def test_missing_end_date_without_open_ended_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			bulk_onboarding.preview(self._fresh(1), self.contract_type,
			                        start_date="2026-01-01")

	def test_one_failure_does_not_roll_back_the_others(self):
		"""§38 — per-employee transactions."""
		good = self._fresh(3)
		ghost = "AHR-DOES-NOT-EXIST"
		result = bulk_onboarding.execute(good + [ghost], self.contract_type,
		                                 start_date="2026-01-01", end_date="2026-12-31")
		for row in result["created"]:
			self._created.append(("Isoft Employment Contract", row["contract"]))
		self.assertEqual(result["summary"]["created"], 3)
		self.assertEqual(result["summary"]["blocked"], 1)

	def test_warnings_travel_with_the_row_without_blocking(self):
		employee = self._employee("NoNif")
		plan = bulk_onboarding.preview([employee], self.contract_type,
		                               start_date="2026-01-01", end_date="2026-12-31")
		row = plan["rows"][0]
		self.assertEqual(row["action"], bulk_onboarding.CREATE)
		self.assertTrue(row.get("warning"), "Missing NIF/IBAN should warn, not block.")


# --------------------------------------------------------------------------- #
# §40, §41 — readiness and offboarding
# --------------------------------------------------------------------------- #
class TestOnboardingAndExit(UXFixture):
	def test_work_readiness_and_payroll_readiness_are_separate(self):
		result = bulk_onboarding.readiness_for_work_and_payroll(self.employee.name)
		self.assertIn("ready_for_work", result)
		self.assertIn("ready_for_payroll", result)
		self.assertIn(result["payroll_status"], ("Ready for Payroll", "Payment Blocked"))

	def test_missing_iban_blocks_payment_but_not_work(self):
		employee = self._employee("NoIban")
		self.approved_contract(employee=employee)
		frappe.db.set_value("Employee", employee, {
			"department": frappe.get_all("Department", pluck="name")[0],
			"designation": frappe.get_all("Designation", pluck="name")[0],
			"reports_to": self.manager_employee,
			"holiday_list": frappe.get_all("Holiday List", pluck="name")[0],
		})
		result = bulk_onboarding.readiness_for_work_and_payroll(employee)
		self.assertFalse(result["ready_for_payroll"])
		self.assertTrue(any("IBAN" in m for m in result["payroll_missing"]))

	def test_exit_checklist_blocks_while_a_contract_is_still_open(self):
		self.approved_contract()
		checklist = bulk_onboarding.exit_checklist(self.employee.name)
		keys = {i["key"]: i for i in checklist["items"]}
		self.assertEqual(keys["contract"]["status"], "Blocking")
		self.assertFalse(checklist["can_close"])

	def test_exit_checklist_states_the_required_sequence(self):
		"""§42 — the order matters, and the checklist has to say so."""
		checklist = bulk_onboarding.exit_checklist(self.employee.name)
		self.assertIn("Left", checklist["guidance"])
		self.assertTrue(checklist["total"] >= 10)

	# Equipment and access are deliberately manual — the app manages neither.
	def test_manual_items_are_marked_non_blocking(self):
		checklist = bulk_onboarding.exit_checklist(self.employee.name)
		keys = {i["key"]: i for i in checklist["items"]}
		self.assertNotEqual(keys["equipment"]["status"], "Blocking")
		self.assertNotEqual(keys["interview"]["status"], "Blocking")


# --------------------------------------------------------------------------- #
# §89 — recruitment
# --------------------------------------------------------------------------- #
class TestRecruitment(UXFixture):
	def test_pipeline_reports_the_stages(self):
		result = recruitment.pipeline(company=self.company)
		if not result.get("available"):
			self.skipTest("ERPNext recruitment is not installed")
		for stage in ("openings", "applicants", "interviews", "offers", "accepted", "hired"):
			self.assertIn(stage, result["stages"])

	def test_conversion_is_blocked_for_an_offer_that_was_not_accepted(self):
		offer = self._offer(status="Awaiting Response")
		check = recruitment.conversion_check(offer)
		self.assertFalse(check["can_convert"])
		self.assertTrue(check["blockers"])

	def test_duplicate_conversion_is_blocked(self):
		"""Pressing "Create Employee" twice must not produce two payable people."""
		offer, applicant = self._offer(status="Accepted", submit=True, with_applicant=True)
		employee = self._employee("FromApplicant")
		frappe.db.set_value("Employee", employee, "job_applicant", applicant)
		check = recruitment.conversion_check(offer)
		self.assertFalse(check["can_convert"])
		self.assertTrue(any("already" in b.lower() for b in check["blockers"]))
		with self.assertRaises(frappe.ValidationError):
			recruitment.convert_to_employee(offer)

	def test_performance_summary_never_creates_a_salary_change(self):
		"""§49 — an appraisal result must not become a pay rise by itself."""
		before = frappe.db.count("Isoft Salary Change")
		summary = recruitment.performance_summary(company=self.company)
		self.assertEqual(frappe.db.count("Isoft Salary Change"), before)
		self.assertIn("Salary Change", summary["note"])

	# -- helpers ---------------------------------------------------------- #
	def _applicant(self):
		doc = frappe.get_doc({
			"doctype": "Job Applicant",
			"applicant_name": "{0} Applicant".format(PREFIX),
			"email_id": "{0}.applicant@ahrtest.invalid".format(
				PREFIX.lower().replace(" ", "-")),
			"status": "Accepted",
		}).insert(ignore_permissions=True)
		self._created.append(("Job Applicant", doc.name))
		return doc.name

	def _offer(self, status="Accepted", submit=False, with_applicant=False):
		applicant = self._applicant()
		doc = frappe.get_doc({
			"doctype": "Job Offer", "job_applicant": applicant, "status": status,
			"offer_date": nowdate(), "company": self.company,
			"designation": frappe.get_all("Designation", pluck="name")[0],
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		if submit:
			try:
				doc.submit()
			except Exception:
				frappe.db.set_value("Job Offer", doc.name, "docstatus", 1)
		self._created.append(("Job Offer", doc.name))
		return (doc.name, applicant) if with_applicant else doc.name


# --------------------------------------------------------------------------- #
# §90 — analytics
# --------------------------------------------------------------------------- #
class TestAnalytics(UXFixture):
	def test_headcount_counts_a_joiner_in_the_month_they_joined(self):
		employee = self._employee("Joiner")
		joined = add_months(getdate(nowdate()), -2).replace(day=10)
		frappe.db.set_value("Employee", employee, "date_of_joining", joined)
		trend = org_analytics.headcount_trend(company=self.company, months=6)
		month = {r["month"]: r for r in trend["rows"]}[joined.strftime("%Y-%m")]
		self.assertGreaterEqual(month["joiners"], 1)

	def test_leaver_reduces_closing_headcount(self):
		employee = self._employee("Leaver")
		left = add_months(getdate(nowdate()), -1).replace(day=15)
		frappe.db.set_value("Employee", employee, {
			"date_of_joining": add_months(left, -12), "relieving_date": left,
			"status": "Left"})
		trend = org_analytics.headcount_trend(company=self.company, months=4)
		month = {r["month"]: r for r in trend["rows"]}[left.strftime("%Y-%m")]
		self.assertGreaterEqual(month["leavers"], 1)

	def test_opening_plus_joiners_minus_leavers_equals_closing(self):
		"""The identity that makes the whole table trustworthy."""
		trend = org_analytics.headcount_trend(company=self.company, months=12)
		for row in trend["rows"]:
			self.assertEqual(row["opening"] + row["joiners"] - row["leavers"],
			                 row["closing"], "Headcount does not balance for {0}".format(
				                 row["month"]))

	def test_turnover_states_its_method_and_its_limits(self):
		"""§55 — the calculation must be documented, not implied."""
		trend = org_analytics.headcount_trend(company=self.company, months=3)
		self.assertIn("Turnover %", trend["method"])
		self.assertTrue(trend["limitations"])
		self.assertFalse(trend["department_history_reliable"],
		                 "Historical department membership is not recorded on this site.")

	def test_turnover_period_matches_the_trend(self):
		period = org_analytics.turnover(company=self.company)
		self.assertEqual(period["opening"] + period["joiners"] - period["leavers"],
		                 period["closing"])

	def test_absenteeism_excludes_approved_leave_and_says_so(self):
		"""§56 — approved leave is an entitlement, not absenteeism."""
		result = org_analytics.absenteeism(company=self.company)
		excluded = " ".join(result["definition"]["excluded"]).lower()
		self.assertIn("approved leave", excluded)
		self.assertIn("formula", result["definition"])

	def test_org_chart_never_returns_a_salary_field(self):
		chart = org_analytics.org_chart(company=self.company)

		def walk(node):
			for forbidden in ("base", "salary", "net_pay", "custom_iban", "gross_pay"):
				self.assertNotIn(forbidden, node)
			for child in node.get("children", []):
				walk(child)

		for root in chart["roots"]:
			walk(root)

	def test_org_chart_detects_a_circular_reporting_line(self):
		a = self._employee("CycleA")
		b = self._employee("CycleB", reports_to=a)
		# NestedSet does not prevent this; an org chart that recursed on it would hang.
		frappe.db.set_value("Employee", a, "reports_to", b)
		quality = org_analytics.chart_quality()
		self.assertGreaterEqual(quality["cycle_count"], 1)
		frappe.db.set_value("Employee", a, "reports_to", None)

	def test_org_chart_survives_a_cycle_without_hanging(self):
		a = self._employee("LoopA")
		b = self._employee("LoopB", reports_to=a)
		frappe.db.set_value("Employee", a, "reports_to", b)
		try:
			chart = org_analytics.org_chart(company=self.company)
			self.assertIsInstance(chart["roots"], list)
		finally:
			frappe.db.set_value("Employee", a, "reports_to", None)


# --------------------------------------------------------------------------- #
# §61, §62 — statutory filing
# --------------------------------------------------------------------------- #
class TestStatutoryFiling(UXFixture):
	def test_missing_nif_fails_irt_validation_visibly(self):
		entry = self.submitted_entry()
		frappe.db.set_value("Employee", self.employee.name, "custom_nif", None)
		report = statutory_filing.validate_period(
			statutory_filing.IRT, self.company, self.start, self.end)
		self.assertFalse(report["valid"])
		self.assertTrue(any(e["code"] == "STF-001" for e in report["errors"]))

	def test_missing_inss_number_fails_inss_validation(self):
		entry = self.submitted_entry()
		frappe.db.set_value("Employee", self.employee.name, "custom_inss_number", None)
		report = statutory_filing.validate_period(
			statutory_filing.INSS, self.company, self.start, self.end)
		self.assertFalse(report["valid"])
		self.assertTrue(any(e["code"] == "STF-003" for e in report["errors"]))

	def test_generation_is_refused_while_validation_fails(self):
		entry = self.submitted_entry()
		frappe.db.set_value("Employee", self.employee.name, "custom_nif", None)
		with self.assertRaises(frappe.ValidationError):
			statutory_filing.build(statutory_filing.IRT, self.company,
			                       self.start, self.end)

	def test_a_period_with_no_approved_payroll_is_refused(self):
		report = statutory_filing.validate_period(
			statutory_filing.IRT, self.company, "2019-01-01", "2019-01-31")
		self.assertFalse(report["valid"])
		self.assertTrue(any(e["code"] == "STF-000" for e in report["errors"]))

	def test_register_records_generated_not_submitted(self):
		"""§62 — producing a file is not a submission."""
		entry = self.submitted_entry()
		result = statutory_filing.build(statutory_filing.IRT, self.company,
		                                self.start, self.end)
		self._created.append(("Isoft Statutory Submission", result["submission"]))
		self.assertEqual(
			frappe.db.get_value("Isoft Statutory Submission", result["submission"], "status"),
			"Generated")

	def test_marking_submitted_requires_the_portal_reference(self):
		entry = self.submitted_entry()
		result = statutory_filing.build(statutory_filing.IRT, self.company,
		                                self.start, self.end)
		self._created.append(("Isoft Statutory Submission", result["submission"]))
		with self.assertRaises(frappe.ValidationError):
			statutory_filing.record_submission(result["submission"], "")
		out = statutory_filing.record_submission(result["submission"], "AGT-2026-0001")
		self.assertEqual(out["status"], "Submitted")

	def test_working_file_carries_its_disclaimer(self):
		"""It must be impossible to mistake the working file for an official format."""
		self.assertIn("não substitui a entrega", statutory_filing.DISCLAIMER)
		entry = self.submitted_entry()
		out = statutory_filing.working_file(statutory_filing.IRT, self.company,
		                                    self.start, self.end)
		self._created.append(("Isoft Statutory Submission", out["submission"]))
		self.assertTrue(out["filename"].endswith(".xlsx"))
		self.assertTrue(out["content"])

	def test_no_bank_format_is_claimed_that_was_not_verified(self):
		"""§63 — a plausible-looking invented format is worse than none."""
		status = statutory_filing.BANK_FORMAT_STATUS
		for bank in ("BAI", "BFA", "BIC"):
			self.assertIn(bank, status["not_implemented"])
		self.assertTrue(status["reason"])


# --------------------------------------------------------------------------- #
# §96 — statutory limits are warnings, with citations
# --------------------------------------------------------------------------- #
class TestLabourLawReference(UXFixture):
	def test_reference_cites_the_current_statute(self):
		reference = law.reference()
		self.assertIn("12/23", reference["law"])
		self.assertIn("7/15", reference["replaced"])
		self.assertTrue(reference["sources"])
		self.assertIn(law.REVIEW_MARKER, reference["marker"])

	def test_probation_beyond_the_statutory_maximum_warns(self):
		warnings = law.check_contract({
			"is_open_ended": 1, "start_date": "2026-01-01",
			"probation_start": "2026-01-01", "probation_end": "2026-12-31",
		})
		self.assertTrue(any(w["code"] == "LGT-018-MAX" for w in warnings))
		self.assertTrue(all("Artigo" in w["article"] for w in warnings))

	def test_probation_within_sixty_days_is_silent(self):
		self.assertEqual(law.check_contract({
			"is_open_ended": 1, "start_date": "2026-01-01",
			"probation_start": "2026-01-01", "probation_end": "2026-02-28",
		}), [])

	def test_fixed_term_beyond_sixty_months_warns_about_conversion(self):
		warnings = law.check_contract({
			"is_open_ended": 0, "start_date": "2020-01-01", "end_date": "2026-12-31",
		})
		self.assertTrue(any(w["code"] == "LGT-016" for w in warnings))

	def test_notice_below_thirty_days_warns(self):
		warnings = law.check_contract({"is_open_ended": 1, "notice_days": 15})
		self.assertTrue(any(w["code"] == "LGT-NOTICE" for w in warnings))

	def test_severance_is_documented_but_never_calculated(self):
		"""§97 — no automatic statutory severance until legal review is complete."""
		self.assertIn("why_not_automated", law.SEVERANCE_METHOD)
		self.assertEqual(law.SEVERANCE_METHOD["marker"], law.REVIEW_MARKER)
		self.assertFalse(hasattr(law, "calculate_severance"),
		                 "Severance must not be computed automatically in Phase 4.")

	def test_a_long_probation_does_not_block_the_contract(self):
		"""Warnings inform; they must never stop lawful work being recorded."""
		doc = self.make_contract(start="2026-01-01", end="2026-12-31")
		doc.probation_start = "2026-01-01"
		doc.probation_end = "2026-11-30"
		doc.save(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Isoft Employment Contract", doc.name))


# --------------------------------------------------------------------------- #
# §74, §76, §77 — notifications
# --------------------------------------------------------------------------- #
class TestNotifications(UXFixture):
	def test_an_approved_payslip_notifies_the_employee_of_nothing(self):
		"""This used to assert that the payslip notification carried no amount. The
		application no longer notifies anybody at all, which subsumes it — so the
		assertion is now that NO notification is produced, against the same real
		submitted payroll. An employee with a login, a submitted slip and a net pay
		figure is precisely the case that used to send one."""
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

		entry = self.submitted_entry()
		slip_name = frappe.db.get_value(
			"Isoft Salary Slip", {"payroll_entry": entry.name}, "name")
		slip = frappe.get_doc("Isoft Salary Slip", slip_name)
		frappe.db.set_value("Employee", slip.employee, "user_id", self.employee_user)
		self.addCleanup(
			lambda: frappe.db.set_value("Employee", slip.employee, "user_id", None))
		slip.docstatus = 1

		before = frappe.db.sql("select count(*) from `tabNotification Log`")[0][0]
		hr_notifications.notify_payslip_available(slip)
		self.assertEqual(
			frappe.db.sql("select count(*) from `tabNotification Log`")[0][0], before,
			"submitting a payslip notified the employee")
		self.assertEqual(
			frappe.get_all("Notification Log",
			               filters={"for_user": self.employee_user}), [],
			"the employee has a notification from this application")

	def test_a_failing_notification_never_breaks_its_caller(self):
		"""The wrapper is the whole reason a payroll submission cannot be taken down
		by a notification."""
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

		class Exploding:
			name = "boom"

			def __getattr__(self, item):
				raise RuntimeError("deliberate")

		# Must not raise.
		hr_notifications.notify_payslip_available(Exploding())
		hr_notifications.notify_leave_decision(Exploding())
		hr_notifications.notify_advance_status(Exploding())

	def test_notification_centre_counts_what_the_user_may_see(self):
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

		frappe.set_user(self.users["hruser"])
		centre = hr_notifications.notification_centre()
		for key in ("unread", "approvals", "contracts", "probation", "documents"):
			self.assertIn(key, centre["counts"])

	def test_a_plain_employee_sees_no_hr_queues_in_the_centre(self):
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

		frappe.set_user(self.stranger_user)
		centre = hr_notifications.notification_centre()
		self.assertEqual(centre["pending_approvals"], [])
		self.assertEqual(centre["contract_expiry"], [])
		self.assertEqual(centre["documents"], [])


# --------------------------------------------------------------------------- #
# A controller must never GRANT permission
# --------------------------------------------------------------------------- #
class TestEmployeeDocumentPermissionStack(UXFixture):
	"""Regression for an unauthenticated data leak.

	IsoftEmployeeDocument defined ``has_permission()``, which OVERRIDES
	``Document.has_permission`` — the method ``check_permission()`` and ``save()`` both
	call. The whole Frappe permission stack (roles, DocPerms, the registered hook) was
	therefore skipped for this DocType, and the override returned True for every
	non-confidential document.

	Reproduced over HTTPS before the fix: GET and PUT on
	``/api/resource/Isoft Employee Document/<name>`` returned 200 for a user with no
	roles AND for an unauthenticated Guest, disclosing an employee's national ID and
	accepting writes to it. The list view stayed correctly protected throughout, which is
	what made it easy to miss.
	"""

	def _doc_type(self, confidential=False):
		name = PREFIX + (" DocType Sec Conf" if confidential else " DocType Sec")
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({
				"doctype": "Isoft Document Type", "document_type": name,
				"is_confidential": 1 if confidential else 0,
			}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		return name

	def _document(self, employee=None, confidential=False):
		doc = frappe.get_doc({
			"doctype": "Isoft Employee Document",
			"employee": employee or self.employee.name,
			"document_type": self._doc_type(confidential=confidential),
			"document_number": "REGRESSION-1",
			"issue_date": nowdate(),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Employee Document", doc.name))
		return doc

	# -- the shape of the bug ------------------------------------------------- #
	def test_the_controller_does_not_shadow_document_has_permission(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_employee_document import (
			isoft_employee_document as mod,
		)
		self.assertNotIn(
			"has_permission", vars(mod.IsoftEmployeeDocument),
			"defining has_permission on the controller replaces Frappe's entire "
			"permission stack for this DocType — restrict through the registered hook")

	def test_a_user_with_no_roles_cannot_read_or_write_a_document(self):
		doc = self._document()
		email = "noroles." + PREFIX.lower().replace(" ", "") + "@example.invalid"
		if not frappe.db.exists("User", email):
			u = frappe.get_doc({"doctype": "User", "email": email,
			                    "first_name": PREFIX + " NoRoles",
			                    "send_welcome_email": 0}).insert(ignore_permissions=True)
			self._created.append(("User", u.name))
		frappe.set_user(email)
		try:
			self.assertFalse(frappe.has_permission("Isoft Employee Document", "read", doc=doc.name))
			fresh = frappe.get_doc("Isoft Employee Document", doc.name)
			with self.assertRaises(frappe.PermissionError):
				fresh.check_permission("read")
			with self.assertRaises(Exception):
				fresh.document_number = "HACKED"
				fresh.save()
		finally:
			frappe.set_user("Administrator")

	def test_guest_cannot_read_a_document(self):
		doc = self._document()
		frappe.set_user("Guest")
		try:
			self.assertFalse(
				frappe.has_permission("Isoft Employee Document", "read", doc=doc.name))
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("Isoft Employee Document", doc.name).check_permission("read")
		finally:
			frappe.set_user("Administrator")

	# -- and the rules it was supposed to enforce still hold ------------------ #
	def test_hr_staff_can_still_read_an_ordinary_document(self):
		doc = self._document()
		self.assertTrue(doc_permissions.employee_document_permission(
			doc, "read", self.users["hruser"]))

	def test_a_confidential_document_stays_with_hr_managers(self):
		doc = self._document(confidential=True)
		self.assertTrue(cint(doc.confidential), "the fixture type must be confidential")
		self.assertFalse(
			doc_permissions.employee_document_permission(doc, "read", self.users["employee_only"]),
			"confidentiality must hold against ordinary staff")

	def test_a_confidential_document_is_hidden_from_its_own_subject(self):
		doc = self._document(confidential=True)
		frappe.db.set_value("Employee", self.employee.name, "user_id", self.employee_user)
		self.addCleanup(
			lambda: frappe.db.set_value("Employee", self.employee.name, "user_id", None))
		self.assertFalse(
			doc_permissions.employee_document_permission(doc, "read", self.employee_user))


# --------------------------------------------------------------------------- #
# A role-restricted Desk Page does not protect the endpoint behind it
# --------------------------------------------------------------------------- #
class TestPageEndpointsGuardThemselves(UXFixture):
	"""Regression for an unauthorised disclosure of aggregate payroll.

	``angola_hr_dashboard.get_dashboard_data`` is the Angola HR Dashboard's data source.
	The Page JSON restricts the page to the six HR/payroll roles, and that was the only
	thing standing in front of it — the whitelisted endpoint itself carried no check. A
	Page role list governs who may OPEN the page; it does nothing about a direct call to
	``/api/method/...get_dashboard_data``.

	Confirmed over HTTPS before the fix: an authenticated user holding NO roles received
	200 with the active headcount, the salary-profile count, the draft and submitted slip
	counts and ``net_paid_this_month`` — the company's total net payroll for the month.

	Every peer endpoint (``api.get_overview``, ``hr_api.hr_dashboard``) already guarded
	itself with an action from ``permissions.ACTION_ROLES``; this one was missed because
	it lives in a Page controller rather than in the app's API modules.
	"""

	@property
	def _endpoint(self):
		from isoft_angola_hr.isoft_angola_hr.page.angola_hr_dashboard import (
			angola_hr_dashboard as dash,
		)
		return dash

	def test_a_user_with_no_roles_is_refused(self):
		email = "nodash." + PREFIX.lower().replace(" ", "") + "@example.invalid"
		if not frappe.db.exists("User", email):
			u = frappe.get_doc({"doctype": "User", "email": email,
			                    "first_name": PREFIX + " NoDash",
			                    "send_welcome_email": 0}).insert(ignore_permissions=True)
			self._created.append(("User", u.name))
		frappe.set_user(email)
		try:
			with self.assertRaises(frappe.PermissionError):
				self._endpoint.get_dashboard_data()
		finally:
			frappe.set_user("Administrator")

	def test_an_ordinary_employee_is_refused(self):
		frappe.set_user(self.stranger_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				self._endpoint.get_dashboard_data()
		finally:
			frappe.set_user("Administrator")

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				self._endpoint.get_dashboard_data()
		finally:
			frappe.set_user("Administrator")

	def test_hr_staff_still_get_the_dashboard(self):
		frappe.set_user(self.users["hruser"])
		try:
			data = self._endpoint.get_dashboard_data()
		finally:
			frappe.set_user("Administrator")
		# the figures the page needs are all still there
		for key in ("active_employees", "salary_profiles", "draft_slips",
		            "submitted_slips", "net_paid_this_month", "currency"):
			self.assertIn(key, data)

	def test_the_endpoint_still_names_an_action_from_the_capability_table(self):
		"""The guard must be a capability check, not an ad-hoc role string, so that
		segregation of duties stays decided by ACTION_ROLES alone."""
		import inspect

		source = inspect.getsource(self._endpoint.get_dashboard_data)
		self.assertIn("perms.require(", source,
		              "the endpoint must authorise through services.permissions")
		self.assertNotIn('"HR Manager"', source,
		                 "hard-coding a role here would bypass the capability table")

	def test_company_scope_is_enforced_when_a_company_is_named(self):
		"""A caller restricted to one company must not read another company's totals by
		passing its name — the same rule every other company-aware endpoint applies."""
		import inspect

		source = inspect.getsource(self._endpoint.get_dashboard_data)
		self.assertIn("require_company", source)


# --------------------------------------------------------------------------- #
# State-changing endpoints must not be reachable by a cross-site GET
# --------------------------------------------------------------------------- #
class TestMutatingEndpointsRefuseGet(UXFixture):
	"""Regression for a cross-site request forgery vector.

	Frappe validates the CSRF token for POST, PUT and DELETE only — a GET is exempt
	(``auth.HTTPRequest.validate_csrf_token``). Frappe also discards a GET request's
	writes, because ``app.after_request`` commits only for POST/PUT. Together those two
	facts make an ordinary mutating endpoint harmless over GET.

	They stop being harmless the moment an endpoint calls ``frappe.db.commit()``
	itself: the explicit commit lands before the framework's rollback, so the change
	sticks. Thirty-three endpoints in ``api.py`` do exactly that — including
	``delete_salary_slip``, ``delete_payroll_entry``, ``cancel_settlement`` and
	``update_employee``.

	Confirmed over HTTPS before the fix: a plain GET carrying no CSRF token, issued on a
	logged-in HR Manager's session, created a record that survived the request. Any page
	an HR or payroll user could be induced to open — ``<img src="…/api/method/…">`` is
	enough — could therefore drive a destructive payroll operation with their authority.

	The fix restricts those endpoints to POST. The Desk app calls every one of them
	through ``frappe.call``, which is POST with the CSRF token by default, so the UI is
	unaffected.
	"""

	#: endpoints that commit their own transaction and therefore must refuse GET
	COMMITTING = (
		"mark_attendance", "update_attendance", "create_occurrence", "justify_occurrence",
		"set_occurrence_status", "delete_occurrence", "save_absence_reason",
		"delete_absence_reason", "save_leave_type", "delete_leave_type",
		"bulk_allocate_leave", "create_leave", "cancel_leave", "delete_leave",
		"create_leave_allocation", "delete_leave_allocation", "bulk_mark_attendance",
		"save_salary_profile", "save_preview_columns", "cancel_salary_slip",
		"delete_salary_slip", "cancel_payroll_entry", "delete_payroll_entry",
		"create_settlement", "submit_settlement", "cancel_settlement", "delete_settlement",
		"save_settings", "create_employee", "update_employee", "create_holiday_list",
		"add_holiday", "save_shift_type",
	)

	def test_every_committing_endpoint_is_restricted_to_post(self):
		from isoft_angola_hr.isoft_angola_hr import api

		allowed = frappe.allowed_http_methods_for_whitelisted_func
		missing = []
		for name in self.COMMITTING:
			fn = getattr(api, name, None)
			self.assertIsNotNone(fn, "api.{0} no longer exists".format(name))
			methods = allowed.get(fn)
			if not methods or "GET" in methods:
				missing.append(name)
		self.assertEqual(
			[], missing,
			"these endpoints commit their own transaction and still accept GET, so a "
			"cross-site request can drive them with a logged-in user's authority: "
			+ ", ".join(missing))

	def test_no_new_endpoint_commits_its_own_transaction_while_allowing_get(self):
		"""The rule, not just today's list — a new committing endpoint must declare POST."""
		import ast
		import inspect

		from isoft_angola_hr.isoft_angola_hr import api

		source = inspect.getsource(api)
		tree = ast.parse(source)
		allowed = frappe.allowed_http_methods_for_whitelisted_func
		offenders = []
		for node in ast.walk(tree):
			if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
				continue
			if not any("whitelist" in ast.dump(d) for d in node.decorator_list):
				continue
			body = ast.get_source_segment(source, node) or ""
			if "db.commit()" not in body:
				continue
			fn = getattr(api, node.name, None)
			if fn is None:
				continue
			methods = allowed.get(fn)
			if not methods or "GET" in methods:
				offenders.append(node.name)
		self.assertEqual(
			[], offenders,
			"a whitelisted endpoint that calls frappe.db.commit() persists its writes "
			"even on a GET, which Frappe exempts from CSRF validation. Declare "
			'@frappe.whitelist(methods=["POST"]) on: ' + ", ".join(offenders))

	def test_read_endpoints_are_left_alone(self):
		"""The restriction is aimed at writes; read endpoints must stay GET-able so
		nothing in the UI or an integration breaks."""
		from isoft_angola_hr.isoft_angola_hr import api

		allowed = frappe.allowed_http_methods_for_whitelisted_func
		for name in ("get_overview", "payroll_voucher", "payroll_period_for_month"):
			fn = getattr(api, name, None)
			self.assertIsNotNone(fn)
			self.assertIn("GET", allowed.get(fn, []),
			              "{0} is a read endpoint and must remain GET-able".format(name))


# --------------------------------------------------------------------------- #
# A denied screen must say so, not spin for ever
# --------------------------------------------------------------------------- #
class TestDeniedScreenTellsTheUser(UXFixture):
	"""Regression for a screen that never resolved.

	``render()`` writes a "Loading…" placeholder into ``.ahr-content`` and then hands
	over to ``view_<screen>``. Those views are written as ``this.call(…).then(render)``
	— 140 call sites against 52 ``.catch()`` — so when the call was refused the
	placeholder simply stayed. Frappe still raised its own dialog, and
	``permissions.require`` makes that dialog a good one (it names the action and every
	role that holds it), but once it was dismissed the user was left on a spinner that
	would never finish.

	Confirmed in Chromium before the fix: an HR User opening "IRT / INSS Declarations"
	(``report.statutory``, which HR User does not hold) was left with ``.ahr-content``
	reading exactly "Loading…" and a spinner still in the DOM.

	Fixed centrally in ``call()`` rather than by adding a catch to thirty-odd views: the
	placeholder is written in one place, so it is cleared in one place. The promise is
	still rejected, so the views that DO handle rejection are unaffected.
	"""

	@property
	def _dashboard_js(self):
		import os

		import isoft_angola_hr

		path = os.path.join(
			os.path.dirname(isoft_angola_hr.__file__), "isoft_angola_hr", "page",
			"angola_hr_dashboard", "angola_hr_dashboard.js")
		with open(path, encoding="utf-8") as fh:
			return fh.read()

	def test_the_failure_renderer_exists(self):
		self.assertIn("renderCallFailure", self._dashboard_js)

	def test_the_rejection_path_calls_it(self):
		"""It must run on the REJECT branch of call(), which is the only place that
		knows the screen never got its data."""
		js = self._dashboard_js
		start = js.index("\tcall(method, args = {}) {")
		body = js[start:start + 1400]
		self.assertIn("this.renderCallFailure(err)", body)
		self.assertIn("reject(err)", body,
		              "the promise must still reject so existing .catch() handlers work")

	def test_it_only_replaces_the_loading_placeholder(self):
		"""A view that has already rendered something must never be overwritten."""
		js = self._dashboard_js
		start = js.index("renderCallFailure(err) {")
		body = js[start:start + 1200]
		self.assertIn("fa-spinner", body,
		              "the guard must key off the loading placeholder, not fire blindly")

	def test_permission_failures_get_their_own_message(self):
		js = self._dashboard_js
		start = js.index("renderCallFailure(err) {")
		body = js[start:start + 1600]
		self.assertIn("403", body)
		self.assertIn("PermissionError", body)
		self.assertIn("do not have permission", body)


# --------------------------------------------------------------------------- #
# Payroll Preview: the editable amounts must be wide enough to read
# --------------------------------------------------------------------------- #
class TestPayrollPreviewInputsAreReadable(unittest.TestCase):
	"""Regression for Overtime / Bonus / Advance inputs too narrow to read.

	The inputs are ``form-control`` (width 100%) inside auto-sized columns and nothing
	gave them a width of their own, so each column shrank to its header. Measured in
	Chromium on the live preview before the fix: Overtime 65px, Bonus 44px, Advance 59px,
	and "1000" / "12500" typed into Bonus / Advance showed as "10" / "1250". Static: it
	reads the console source, so it needs no fixture.
	"""

	@staticmethod
	def _source(ext):
		import os

		import isoft_angola_hr

		path = os.path.join(
			os.path.dirname(isoft_angola_hr.__file__), "isoft_angola_hr", "page",
			"angola_hr_dashboard", "angola_hr_dashboard." + ext)
		with open(path, encoding="utf-8") as fh:
			return fh.read()

	def _rule(self):
		css = self._source("css")
		m = re.search(r"\.pv-table input\.pv-f,\s*\.pv-table input\.pv-natal\s*{([^}]*)}", css)
		self.assertTrue(m, "the Payroll Preview input rule is missing")
		return m.group(1)

	def _px(self, rule, prop):
		m = re.search(r"(?:^|[;\s])" + prop + r":\s*(\d+)px", rule)
		self.assertTrue(m, "%s is not set on the preview inputs" % prop)
		return int(m.group(1))

	def test_every_editable_amount_has_a_readable_minimum_width(self):
		rule = self._rule()
		# 12500.50 at 13px plus Chrome's number spinner needs ~92px
		self.assertGreaterEqual(self._px(rule, "min-width"), 90)
		self.assertGreaterEqual(self._px(rule, "max-width"), self._px(rule, "min-width"))
		self.assertGreaterEqual(self._px(rule, "font-size"), 12)

	def test_the_rule_covers_the_inputs_the_preview_renders(self):
		js = self._source("js")
		for key in ("overtime_amount", "productivity_bonus", "adiantamento"):
			self.assertIn('case "%s":' % key, js)
		self.assertIn('class="form-control input-xs pv-f" data-k="${c.key}"', js)
		self.assertIn('class="form-control input-xs pv-natal"', js)
