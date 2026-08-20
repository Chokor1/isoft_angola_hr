# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 6 tests: the HR-operated model.

THE CLAIM UNDER TEST
--------------------
An HR team can run the complete employee lifecycle without the employee or the line
manager ever logging in.

That is not something a screenshot can demonstrate, so every test here creates an
employee with **no ``user_id``** and, in the no-manager class, **no ``reports_to``**, and
then drives the real services as an HR User or HR Manager. If any process still needed a
self-service session, these tests would fail rather than the defect being discovered by a
customer whose staff have no e-mail addresses.

SAFETY — unchanged from every earlier phase. Every record is prefixed ``_TEST AHR``, is
registered for deletion, and each test runs inside a savepoint.

The tests that matter most are, as always, the ones that assert a REFUSAL. HR-operated
mode widens WHO may record a request; it must not widen who may grant one. So:

* an HR User may record a bank change and may NOT approve it;
* the person who records a salary change or an advance may not approve it;
* recording somebody else's evaluation without naming them is refused;
* a confidential document type cannot be filed by a plain HR User;
* an invented request channel is refused rather than silently stored.
"""

import base64

import frappe
from frappe.utils import add_days, add_months, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr import hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p5_release import PDF_B64, ReleaseFixture
from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle
from isoft_angola_hr.isoft_angola_hr.services import hr_operations as ops
from isoft_angola_hr.isoft_angola_hr.services import performance
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc


class HROperatedFixture(ReleaseFixture):
	"""An employee with NO login and NO manager, plus the two HR actors.

	The whole point of the phase is that this employee is fully administrable, so the
	fixture deliberately withholds both of the things the old model depended on.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.hr_user = cls._make_user("hronly", [perms.HR_USER])
		cls.hr_manager = cls._make_user("hrmgr", [perms.HR_MANAGER, perms.HR_USER])
		# No user_id, no reports_to. This person can never open /ess or appear in /mss.
		cls.offline = cls._employee("Offline")

	def as_hr(self):
		frappe.set_user(self.hr_user)

	def as_hr_manager(self):
		frappe.set_user(self.hr_manager)

	def bank_change(self, employee=None, iban="AO06000600000100037131900", **kwargs):
		self.as_hr()
		result = ops.create_bank_change(employee or self.offline, iban, **kwargs)
		frappe.set_user("Administrator")
		self._created.append(("Isoft Bank Change Request", result["name"]))
		return result

	def leave_type(self):
		"""A leave type this test owns, so the assertions do not depend on site data.

		``allow_negative`` is set deliberately. It is an ordinary ERPNext configuration,
		and it means a request does not first need a submitted Leave Allocation behind it.
		These tests are about whether HR can RECORD and DECIDE leave for somebody with no
		login — the allocation ledger is ERPNext's own, already-tested behaviour, and
		submitting an allocation here would commit outside the test savepoint and take the
		rest of the class down with it.
		"""
		name = PREFIX + " Annual"
		if not frappe.db.exists("Leave Type", name):
			frappe.get_doc({
				"doctype": "Leave Type", "leave_type_name": name,
				"max_leaves_allowed": 22, "is_carry_forward": 0, "allow_negative": 1,
			}).insert(ignore_permissions=True)
			self._created.append(("Leave Type", name))
		return name

	def no_commit(self):
		"""Suppress the ``frappe.db.commit()`` the leave endpoints call.

		``api.create_leave`` and ``api.approve_leave`` commit so the dashboard sees the
		row immediately. A commit ends the test savepoint, so without this the FIRST leave
		call would permanently write everything the test had created up to that point —
		which is exactly what happened, and it left an orphaned contract and two leave
		applications on the site before this was understood.

		Only the commit is suppressed. Every validation, ledger entry and state change the
		endpoint performs still runs, so the test still exercises the real path.
		"""
		original = frappe.db.commit
		frappe.db.commit = lambda *a, **k: None
		self.addCleanup(setattr, frappe.db, "commit", original)

	def record_leave(self, employee=None, offset=10, **kwargs):
		"""Record a leave request the way HR does, and register it for cleanup.

		Cancelled before deletion because an approved application is submitted, and Frappe
		refuses to delete a submitted document.
		"""
		self.no_commit()
		employee = employee or self.offline
		values = {
			"employee": employee, "leave_type": self.leave_type(),
			"from_date": add_days(nowdate(), offset), "to_date": add_days(nowdate(), offset),
		}
		values.update(kwargs)
		name = api.create_leave(values)
		self.addCleanup(self._drop_leave, name)
		return name

	def _drop_leave(self, name):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Leave Application", name):
			return
		doc = frappe.get_doc("Leave Application", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Leave Application", name, force=True, ignore_permissions=True)

	def make_contract(self, employee=None, **kwargs):
		"""As the HR fixture, but registered for deletion.

		The base helper relies on the test savepoint alone. That is fine until a test also
		touches something that commits, at which point the contract survives and blocks
		every later run with "already has a contract covering this period".
		"""
		doc = super().make_contract(employee=employee, **kwargs)
		self._created.append(("Isoft Employment Contract", doc.name))
		return doc

	def hr_doc_type(self, confidential=False, medical=False):
		name = PREFIX + (" HR Conf Type" if (confidential or medical) else " HR File Type")
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({
				"doctype": "Isoft Document Type", "document_type": name,
				"employee_may_upload": 0,
				"is_confidential": 1 if confidential else 0,
				"is_medical": 1 if medical else 0,
			}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		return name


# --------------------------------------------------------------------------- #
# §34 — the complete lifecycle, HR-operated
# --------------------------------------------------------------------------- #
class TestHROnlyLifecycle(HROperatedFixture):
	def test_hr_walks_an_employee_with_no_login_through_the_whole_lifecycle(self):
		"""One test, deliberately long: the claim is about the WHOLE chain.

		Split into a test per step it would still pass while the chain was broken in the
		middle, because each step would set up its own precondition instead of inheriting
		the previous step's real output.
		"""
		employee = self.offline
		self.assertFalse(frappe.db.get_value("Employee", employee, "user_id"),
		                 "the fixture employee must have no login, or this proves nothing")

		# 1. Salary profile ---------------------------------------------------
		self.as_hr()
		profile = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": employee, "company": self.company,
			"from_date": "2026-01-01", "base": 150000,
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Profile", profile.name))

		# 2. Contract: created by HR User, approved by HR Manager --------------
		contract = self.make_contract(employee=employee)
		contracts.perform(contract, contracts.SUBMIT)
		self.as_hr_manager()
		contract.reload()
		contracts.perform(contract, contracts.APPROVE)
		contract.reload()
		self.assertIn(contract.status, (contracts.ACTIVE, contracts.EXPIRING, contracts.EXPIRED))
		self.assertEqual(contract.approved_by, self.hr_manager)

		# 3. Leave: recorded by HR, decided by HR ------------------------------
		frappe.set_user("Administrator")
		self.as_hr()
		leave = self.record_leave(employee, description="recorded by HR",
		                          request_source="Employee verbal request")
		self.assertEqual(
			frappe.db.get_value("Leave Application", leave, "custom_request_source"),
			"Employee verbal request",
			"the channel must be recorded, or the trail says HR invented the absence")
		self.as_hr_manager()
		api.approve_leave(leave)
		self.assertEqual(frappe.db.get_value("Leave Application", leave, "status"), "Approved")

		# 4. Attendance justification: HR records the certificate --------------
		frappe.set_user("Administrator")
		occurrence = self.make_occurrence(employee=employee)
		self.as_hr()
		result = ops.record_justification(
			occurrence.name, self.absence_reason(), explanation="Handed in a sick note",
			justification_source="Written request", decision="justify")
		self.assertEqual(result["status"], "Justified")

		# 5. Salary advance: HR records, a different HR person approves --------
		self.as_hr()
		advance = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": employee, "company": self.company,
			"request_date": nowdate(), "requested_amount": 50000, "installments": 2,
			"reason": "asked at the desk", "request_source": "Employee verbal request",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Advance", advance.name))
		advances.perform(advance, advances.SUBMIT)
		self.as_hr_manager()
		advance.reload()
		advances.perform(advance, advances.APPROVE)
		advance.reload()
		self.assertEqual(advance.status, advances.APPROVED)

		# 6. Bank change: HR records, HR Manager approves, only then written ---
		self.bank_change(employee=employee)
		self.assertNotEqual(
			frappe.db.get_value("Employee", employee, "custom_iban"),
			"AO06000600000100037131900",
			"recording a bank change must not touch the employee record")

		# 7. Salary change: HR requests, a different HR person approves --------
		self.as_hr()
		change = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": employee, "company": self.company,
			"change_type": "Merit Increase", "effective_date": self.next_period_start(),
			"current_base": 150000, "new_base": 180000, "reason": "annual review",
			"request_source": "Management instruction",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Change", change.name))
		sc.perform(change, sc.SUBMIT)
		self.as_hr_manager()
		change.reload()
		sc.perform(change, sc.APPROVE)
		change.reload()
		sc.perform(change, sc.APPLY)
		change.reload()
		self.assertEqual(change.status, sc.APPLIED)
		self.assertTrue(change.created_profile)
		self._created.append(("Isoft Salary Profile", change.created_profile))
		# The old profile must have been closed, not left overlapping.
		self.assertEqual(
			getdate(frappe.db.get_value("Isoft Salary Profile", profile.name, "to_date")),
			add_days(getdate(self.next_period_start()), -1))

		# 8. Employee document: HR files what it was handed --------------------
		self.as_hr()
		doc = ops.add_employee_document(
			employee, self.hr_doc_type(), filename="bi.pdf", content=PDF_B64,
			document_number="000123LA041")
		self._created.append(("Isoft Employee Document", doc["name"]))
		self.assertTrue(doc["attachment"])
		self.assertEqual(
			frappe.db.get_value("Isoft Employee Document", doc["name"], "verification_status"),
			"Verified", "HR saw the original, so it is verified on filing")

		# 9. Termination ------------------------------------------------------
		self.as_hr_manager()
		contract.reload()
		contracts.perform(contract, contracts.TERMINATE, reason="end of project")
		contract.reload()
		self.assertEqual(contract.status, contracts.TERMINATED)
		self.assertEqual(getdate(contract.terminated_on), getdate(nowdate()))

		# Nothing above ever set the session to the employee or to a line manager.
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value("Employee", employee, "user_id"))


# --------------------------------------------------------------------------- #
# §35 — no user account
# --------------------------------------------------------------------------- #
class TestNoUserAccount(HROperatedFixture):
	def test_missing_user_id_is_optional_not_a_readiness_failure(self):
		self.as_hr()
		check = lifecycle.onboarding_checklist(self.offline)
		ess = [i for i in check["items"] if i["key"] == "ess_access"][0]
		self.assertEqual(ess["status"], lifecycle.OPTIONAL)
		self.assertFalse(check["self_service"])
		# And it must not drag the completeness ratio down: an employee is not incomplete
		# because of a login the company never intended to give them.
		self.assertEqual(check["total"], len([i for i in check["items"]
		                                      if i["status"] != lifecycle.OPTIONAL]))

	def test_hr_readiness_never_reports_a_missing_login(self):
		self.as_hr()
		readiness = lifecycle.hr_readiness(company=self.company)
		codes = [b["code"] for b in readiness["blockers"]] + \
			[w["code"] for w in readiness["warnings"]]
		labels = " ".join([b["label"] for b in readiness["blockers"]] +
		                  [w["label"] for w in readiness["warnings"]]).lower()
		self.assertNotIn("user id", labels)
		self.assertNotIn("self-service", labels)
		self.assertTrue(codes)

	def test_login_dependencies_reports_no_process_needing_a_login(self):
		self.as_hr()
		report = ops.login_dependencies(company=self.company)
		self.assertFalse(report["employee_login_required_anywhere"])
		self.assertFalse(report["manager_login_required_anywhere"])
		self.assertGreaterEqual(report["without_user_id"], 1)
		# Every process named in the report must have a real HR entry point.
		for row in report["processes"]:
			self.assertTrue(row["hr_entry_point"],
			                "{0} has no HR entry point".format(row["process"]))

	def test_hr_records_a_bank_change_for_somebody_with_no_login(self):
		result = self.bank_change()
		self.assertEqual(result["status"], "Pending Approval")
		self.assertEqual(
			frappe.db.get_value("Isoft Bank Change Request", result["name"], "requested_by"),
			self.hr_user, "the HR user who keyed it must be recorded")

	def test_hr_files_a_document_for_somebody_with_no_login(self):
		self.as_hr()
		doc = ops.add_employee_document(self.offline, self.hr_doc_type(),
		                                filename="cert.pdf", content=PDF_B64)
		self._created.append(("Isoft Employee Document", doc["name"]))
		self.assertEqual(
			frappe.db.get_value("Isoft Employee Document", doc["name"],
			                    "submitted_by_employee"), 0)


# --------------------------------------------------------------------------- #
# §36 — no line manager
# --------------------------------------------------------------------------- #
class TestNoManager(HROperatedFixture):
	def cycle(self):
		template = self.appraisal_template()
		doc = frappe.get_doc({
			"doctype": "Isoft Performance Cycle",
			"cycle_name": "{0} Cycle {1}".format(PREFIX, frappe.generate_hash(length=5)),
			"company": self.company, "period_type": "Annual",
			"start_date": add_months(getdate(nowdate()), -12),
			"end_date": getdate(nowdate()), "due_date": add_days(nowdate(), 30),
			"appraisal_template": template, "minimum_service_months": 0,
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Performance Cycle", doc.name))
		return doc

	def appraisal_template(self):
		name = PREFIX + " Perf Template"
		if not frappe.db.exists("Appraisal Template", name):
			frappe.get_doc({
				"doctype": "Appraisal Template", "kra_title": name,
				"goals": [{"kra": "Delivery", "per_weightage": 60},
				          {"kra": "Teamwork", "per_weightage": 40}],
			}).insert(ignore_permissions=True)
			self._created.append(("Appraisal Template", name))
		return name

	def test_employee_without_a_manager_is_included_in_a_review_cycle(self):
		"""Previously BLOCKED. On this site that excluded 43 active employees.

		The old reasoning — "nobody could review them" — only held while the review had
		to be entered by the manager's own session.
		"""
		self.assertFalse(frappe.db.get_value("Employee", self.offline, "reports_to"))
		self.as_hr()
		plan = performance.preview_cycle(self.cycle().name)
		row = [r for r in plan["rows"] if r["employee"] == self.offline]
		self.assertTrue(row, "the employee must appear in the plan at all")
		self.assertEqual(row[0]["action"], "Create")
		self.assertIn("HR", row[0]["reason"])

	def test_hr_records_the_evaluation_without_any_manager_session(self):
		self.as_hr()
		cycle = self.cycle()
		performance.generate_cycle(cycle.name)
		appraisal = frappe.db.get_value(
			"Appraisal", {"custom_performance_cycle": cycle.name, "employee": self.offline},
			"name")
		self.assertTrue(appraisal, "the cycle must have generated an appraisal")
		self._created.append(("Appraisal", appraisal))

		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name", "kra"])
		result = ops.record_evaluation(
			appraisal, goals={g.name: 4 for g in goals},
			comments="Reviewed by HR — no line manager",
			evaluation_source="HR Manager (no line manager)", submit=True)
		self.assertEqual(result["state"], performance.PENDING_EMPLOYEE)
		self.assertGreater(flt(result["total_score"]), 0,
		                   "ERPNext must have computed the weighted total")
		self.assertEqual(
			frappe.db.get_value("Appraisal", appraisal, "custom_evaluation_source"),
			"HR Manager (no line manager)")

	def test_hr_records_the_employee_acknowledgement_in_person(self):
		self.as_hr()
		cycle = self.cycle()
		performance.generate_cycle(cycle.name)
		appraisal = frappe.db.get_value(
			"Appraisal", {"custom_performance_cycle": cycle.name, "employee": self.offline},
			"name")
		self._created.append(("Appraisal", appraisal))
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal}, pluck="name")
		ops.record_evaluation(appraisal, goals={g: 4 for g in goals},
		                      evaluation_source="HR Manager (no line manager)")
		ops.record_acknowledgement(appraisal, comments="signed the printed copy",
		                           acknowledged_by="Offline Employee")
		row = frappe.db.get_value("Appraisal", appraisal,
		                          ["custom_review_state", "custom_employee_comments"],
		                          as_dict=True)
		self.assertEqual(row.custom_review_state, performance.PENDING_HR)
		self.assertIn("Offline Employee", row.custom_employee_comments)

		self.as_hr_manager()
		final = performance.hr_finalise(appraisal)
		self.assertEqual(final["state"], performance.FINALISED)

	def test_leave_is_recorded_and_decided_with_no_manager_in_the_chain(self):
		self.assertFalse(frappe.db.get_value("Employee", self.offline, "reports_to"))
		self.as_hr()
		leave = self.record_leave(offset=20)
		self.as_hr_manager()
		api.approve_leave(leave)
		self.assertEqual(frappe.db.get_value("Leave Application", leave, "status"), "Approved")


# --------------------------------------------------------------------------- #
# §6, §7, §38 — the controls that must NOT have been weakened
# --------------------------------------------------------------------------- #
class TestHROperatedControls(HROperatedFixture):
	def test_hr_user_may_record_a_bank_change_but_not_approve_it(self):
		result = self.bank_change()
		self.as_hr()
		doc = frappe.get_doc("Isoft Bank Change Request", result["name"])
		with self.assertRaises(frappe.PermissionError):
			doc.approve()
		# ...and the HR Manager can, which is what makes the refusal meaningful.
		self.as_hr_manager()
		doc.reload()
		doc.approve()
		self.assertEqual(
			frappe.db.get_value("Employee", self.offline, "custom_iban"),
			"AO06000600000100037131900",
			"only approval may write the employee record")

	def test_recording_a_bank_change_requires_its_own_permission(self):
		frappe.set_user(self.users["employee_only"])
		with self.assertRaises(frappe.PermissionError):
			ops.create_bank_change(self.offline, "AO06000600000100037131901")

	def test_the_hr_user_who_requests_a_salary_change_cannot_approve_it(self):
		self.as_hr_manager()
		change = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": self.offline, "company": self.company,
			"change_type": "Merit Increase", "effective_date": self.next_period_start(),
			"current_base": 150000, "new_base": 190000, "reason": "test",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Change", change.name))
		sc.perform(change, sc.SUBMIT)
		change.reload()
		# Same user, and they DO hold the approval role — the refusal is by identity.
		with self.assertRaises(frappe.ValidationError):
			sc.perform(change, sc.APPROVE)

	def test_the_hr_user_who_records_an_advance_cannot_approve_it(self):
		self.as_hr_manager()
		advance = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.offline,
			"company": self.company, "request_date": nowdate(),
			"requested_amount": 20000, "installments": 1, "reason": "test",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Advance", advance.name))
		advances.perform(advance, advances.SUBMIT)
		advance.reload()
		with self.assertRaises(frappe.ValidationError):
			advances.perform(advance, advances.APPROVE)

	def test_a_plain_hr_user_cannot_file_a_confidential_document(self):
		self.as_hr()
		with self.assertRaises(frappe.PermissionError):
			ops.add_employee_document(self.offline, self.hr_doc_type(confidential=True))

	def test_a_medical_document_type_is_treated_as_confidential(self):
		"""A sick note is not less private because somebody forgot the second checkbox."""
		name = PREFIX + " HR Medical Type"
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({"doctype": "Isoft Document Type", "document_type": name,
			                "is_medical": 1, "is_confidential": 0}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		self.as_hr()
		with self.assertRaises(frappe.PermissionError):
			ops.add_employee_document(self.offline, name)
		self.as_hr_manager()
		doc = ops.add_employee_document(self.offline, name)
		self._created.append(("Isoft Employee Document", doc["name"]))
		self.assertEqual(doc["confidential"], 1)

	def test_recording_a_managers_evaluation_requires_naming_the_manager(self):
		"""Otherwise the review reads as HR's own opinion of the employee."""
		self.as_hr()
		cycle = frappe.get_doc({
			"doctype": "Isoft Performance Cycle",
			"cycle_name": "{0} Attr {1}".format(PREFIX, frappe.generate_hash(length=5)),
			"company": self.company, "period_type": "Annual",
			"start_date": add_months(getdate(nowdate()), -12), "end_date": getdate(nowdate()),
			"due_date": add_days(nowdate(), 30), "minimum_service_months": 0,
			"appraisal_template": TestNoManager.appraisal_template(self),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Performance Cycle", cycle.name))
		performance.generate_cycle(cycle.name)
		appraisal = frappe.db.get_value(
			"Appraisal", {"custom_performance_cycle": cycle.name, "employee": self.report_employee},
			"name")
		self.assertTrue(appraisal)
		self._created.append(("Appraisal", appraisal))
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal}, pluck="name")
		with self.assertRaises(frappe.ValidationError):
			ops.record_evaluation(appraisal, goals={g: 4 for g in goals},
			                      evaluation_source="Line manager decision recorded by HR",
			                      decision_by="")

	def test_the_hr_operated_flag_alone_grants_nothing(self):
		"""The flag skips the manager check; the permission is what authorises it.

		Set by hand, by somebody without PERFORMANCE_OPERATE, it must do nothing — or the
		flag would be a bypass rather than an authorisation route.
		"""
		frappe.set_user(self.users["employee_only"])
		frappe.flags.isoft_hr_operated_review = True
		try:
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				performance.assert_manager_of(frappe._dict({
					"custom_manager": self.manager_employee,
					"employee": self.report_employee, "company": self.company}))
		finally:
			frappe.flags.isoft_hr_operated_review = False

	def test_an_invented_request_channel_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			ops.validate_source("Whatever I felt like typing")
		# ...and an empty one is fine: the channel is optional, not mandatory noise.
		self.assertIsNone(ops.validate_source(""))
		self.assertEqual(ops.validate_source("Email"), "Email")

	def test_the_self_approval_policy_matches_what_is_enforced(self):
		"""The published table must be derived, not written. A policy document that can
		drift away from the code is worse than none."""
		self.as_hr()
		rows = {r["process"]: r for r in ops.self_approval_policy()}
		self.assertFalse(rows["Salary change"]["same_user"])
		self.assertFalse(rows["Salary advance"]["same_user"])
		self.assertFalse(rows["Bank change"]["same_user"])
		self.assertTrue(rows["Leave"]["same_user"])
		# Payroll segregation is explicitly out of scope for HR-operated mode (§39).
		self.assertFalse(rows["Payroll"]["same_user"])
		for row in rows.values():
			self.assertTrue(row["guard"], "every row must name the guard that enforces it")
			self.assertTrue(row["reason"])


# --------------------------------------------------------------------------- #
# §28 — the action queue
# --------------------------------------------------------------------------- #
class TestActionQueue(HROperatedFixture):
	def test_the_queue_lists_a_pending_request_and_the_screen_that_clears_it(self):
		self.bank_change()
		self.as_hr()
		queue = ops.action_queue(company=self.company)
		bank = [i for i in queue["items"] if i["key"] == "approvals_bank"]
		self.assertTrue(bank, "a pending bank change must appear in the queue")
		self.assertGreaterEqual(bank[0]["count"], 1)
		self.assertEqual(bank[0]["view"], "bankchanges")
		self.assertTrue(bank[0]["hint"])

	def test_every_queue_row_names_a_destination(self):
		self.as_hr()
		queue = ops.action_queue(company=self.company)
		for row in queue["all"]:
			self.assertTrue(row["view"], "{0} has nowhere to go".format(row["label"]))
			self.assertTrue(row["hint"], "{0} does not say what to do".format(row["label"]))

	def test_the_approval_inbox_says_who_asked_and_who_decides(self):
		self.bank_change()
		self.as_hr()
		rows = [r for r in lifecycle.pending_approvals(self.company)
		        if r["doctype"] == "Isoft Bank Change Request"]
		self.assertTrue(rows)
		row = rows[0]
		self.assertEqual(row["recorded_by"], self.hr_user)
		self.assertIn(perms.HR_MANAGER, row["approver"])
		self.assertEqual(row["view"], "bankchanges")


# --------------------------------------------------------------------------- #
# The endpoints behind the new buttons
# --------------------------------------------------------------------------- #
class TestHROperatedEndpoints(HROperatedFixture):
	def test_every_new_endpoint_is_whitelisted(self):
		"""A button wired to a method Frappe will not expose is a dead button."""
		for name in ("create_bank_change", "add_employee_document", "record_justification",
		             "record_evaluation", "record_acknowledgement", "record_interview_result",
		             "hr_action_queue", "self_approval_policy", "login_dependencies",
		             "request_sources", "list_employee_documents", "document_type_options",
		             "open_appraisals", "appraisal_goals"):
			# Frappe records the decorated function in a module-level list rather than
			# tagging the function object, so membership is the only honest check.
			self.assertIn(getattr(hr_api, name), frappe.whitelisted,
			              "{0} is not whitelisted".format(name))

	def test_document_type_options_hides_what_the_caller_may_not_file(self):
		self.hr_doc_type(confidential=True)
		self.as_hr()
		rows = {r["name"]: r for r in hr_api.document_type_options()}
		confidential = rows.get(PREFIX + " HR Conf Type")
		self.assertTrue(confidential)
		self.assertFalse(confidential["allowed"])
		self.as_hr_manager()
		rows = {r["name"]: r for r in hr_api.document_type_options()}
		self.assertTrue(rows[PREFIX + " HR Conf Type"]["allowed"])

	def test_the_document_list_excludes_confidential_rows_from_an_hr_user(self):
		self.as_hr_manager()
		doc = hr_api.add_employee_document(self.offline, self.hr_doc_type(confidential=True))
		self._created.append(("Isoft Employee Document", doc["name"]))
		self.as_hr()
		names = [r["name"] for r in hr_api.list_employee_documents(company=self.company)]
		self.assertNotIn(doc["name"], names,
		                 "a confidential document must not reach an HR User's browser")
		self.as_hr_manager()
		names = [r["name"] for r in hr_api.list_employee_documents(company=self.company)]
		self.assertIn(doc["name"], names)

	# The recruitment result endpoint is covered here rather than in the lifecycle test
	# because it needs ERPNext's Interview, which not every site has installed.
	def test_interview_result_is_refused_for_an_unknown_outcome(self):
		self.as_hr()
		with self.assertRaises(frappe.ValidationError):
			ops.record_interview_result("nonexistent", "Maybe")
