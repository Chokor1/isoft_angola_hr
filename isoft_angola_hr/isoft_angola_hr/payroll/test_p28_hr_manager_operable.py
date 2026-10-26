# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""One HR Manager must be able to run the HR processes end to end — and only those.

The department operating this application has a single HR Manager. Every HR process was
refusing that manager at the approval step with "a different user must approve", and the
switches that govern those refusals were enforced by the server, defaulted to ON, and
appeared in neither ``get_settings`` nor the Settings screen — so the refusal could be
read but never answered.

What these tests pin, in both directions:

  * with the separation cleared, ONE HR Manager can prepare AND approve a contract, a
    salary change, an advance and a final settlement;
  * with it set (the shipped default), the same attempt is still refused — the control
    is configuration, not something that was deleted;
  * clearing a switch never widens anything else: capability, company scope and workflow
    state are still checked, and a user without the capability is still refused;
  * money still belongs to Finance. Disbursing an advance and posting payroll are role
    restrictions, unaffected by any of these switches.
"""

import frappe

from isoft_angola_hr.isoft_angola_hr import api, hr_api
from isoft_angola_hr.isoft_angola_hr.install import _has_perm
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (
	PREFIX, PayrollFixture, _stored_setting, _write_setting)
from isoft_angola_hr.isoft_angola_hr.services import advances, contracts, salary_change
from isoft_angola_hr.isoft_angola_hr.services import hr_operations as hr_ops
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

SWITCHES = (
	"require_separate_contract_approval",
	"require_separate_salary_change_approval",
	"require_separate_advance_approval",
	"require_separate_settlement_approval",
	"require_separate_payroll_approval",
	"require_separate_payment_approval",
)


class HrManagerFixture(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# the stored rows, not get_single_value(): that casts a missing Check to 0, and
		# writing the 0 back turned an unset (safe-default) switch into an explicit OFF
		cls._before = {f: _stored_setting(f) for f in SWITCHES}
		cls.users = {}
		for role in ("HR Manager", "HR User", "Payroll Finance Approver"):
			email = "hrm.%s@example.invalid" % role.lower().replace(" ", "")
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email, "first_name": PREFIX + " " + role,
					"send_welcome_email": 0, "roles": [{"role": role}],
				}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.users[role] = email
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for f, v in cls._before.items():
			_write_setting(f, v)
		frappe.db.commit()
		frappe.clear_cache()
		super().tearDownClass()

	def tearDown(self):
		try:
			super().tearDown()
		except Exception:
			frappe.db.rollback()

	def separation(self, field, required):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Isoft HR Settings", field, 1 if required else 0)
		frappe.db.commit()
		frappe.clear_cache()

	def next_boundary(self):
		"""A salary change must start on a payroll period boundary - the engine cannot
		split one period across two salary profiles."""
		frappe.set_user("Administrator")
		out = hr_api.next_payroll_boundary()
		return (out or {}).get("next_start") or frappe.utils.add_months(
			frappe.utils.nowdate(), 1)

	def as_role(self, role):
		frappe.set_user(self.users[role])
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	# -- the records each process starts from ---------------------------------------
	def a_contract(self):
		"""A draft contract for the shared fixture employee.

		Deliberately NOT committed, and removed at the end of the test: an employee may
		not hold two contracts covering the same period, so a contract left behind here
		breaks every other module that uses the same fixture employee.
		"""
		frappe.set_user("Administrator")
		ct = frappe.db.get_value("Isoft Contract Type", {})
		doc = frappe.get_doc({
			"doctype": "Isoft Employment Contract", "employee": self.employee.name,
			"company": self.company, "contract_type": ct, "start_date": "2026-01-01",
			"notice_days": 30, "is_open_ended": 1,
		}).insert(ignore_permissions=True)
		self.addCleanup(self._remove_contract, doc.name)
		return doc.name

	@staticmethod
	def _remove_contract(name):
		frappe.set_user("Administrator")
		if frappe.db.exists("Isoft Employment Contract", name):
			frappe.delete_doc("Isoft Employment Contract", name, force=True,
			                  ignore_permissions=True)


class TestOneHrManagerCanApproveWhatTheyPrepared(HrManagerFixture):
	def test_contract(self):
		self.separation("require_separate_contract_approval", False)
		name = self.a_contract()
		self.as_role("HR Manager")
		hr_api.contract_action(name=name, action="submit_for_approval")
		hr_api.contract_action(name=name, action="approve")

		frappe.set_user("Administrator")
		doc = frappe.get_doc("Isoft Employment Contract", name)
		self.assertEqual(doc.status, "Active")
		self.assertEqual(doc.approved_by, self.users["HR Manager"],
		                 "the approval must still record who gave it")

	def test_contract_is_still_refused_when_the_separation_is_required(self):
		self.separation("require_separate_contract_approval", True)
		name = self.a_contract()
		self.as_role("HR Manager")
		hr_api.contract_action(name=name, action="submit_for_approval")
		with self.assertRaises(frappe.ValidationError):
			hr_api.contract_action(name=name, action="approve")

	def test_salary_change(self):
		self.separation("require_separate_salary_change_approval", False)
		boundary = self.next_boundary()
		self.as_role("HR Manager")
		name = hr_api.create_salary_change(data=frappe.as_json({
			"employee": self.employee.name, "company": self.company,
			"new_base": 321000, "change_type": "Merit Increase",
			"effective_date": boundary, "reason": PREFIX,
		}))
		hr_api.salary_change_action(name=name, action="submit_for_approval")
		hr_api.salary_change_action(name=name, action="approve")

		frappe.set_user("Administrator")
		self._created.append(("Isoft Salary Change", name))
		self.assertEqual(frappe.db.get_value("Isoft Salary Change", name, "status"), "Approved")

	def test_salary_change_is_still_refused_when_required(self):
		self.separation("require_separate_salary_change_approval", True)
		boundary = self.next_boundary()
		self.as_role("HR Manager")
		name = hr_api.create_salary_change(data=frappe.as_json({
			"employee": self.employee.name, "company": self.company,
			"new_base": 322000, "change_type": "Merit Increase",
			"effective_date": boundary, "reason": PREFIX,
		}))
		self._created.append(("Isoft Salary Change", name))
		hr_api.salary_change_action(name=name, action="submit_for_approval")
		with self.assertRaises(frappe.ValidationError):
			hr_api.salary_change_action(name=name, action="approve")

	def test_advance(self):
		self.separation("require_separate_advance_approval", False)
		self.as_role("HR Manager")
		name = hr_api.create_advance(data=frappe.as_json({
			"employee": self.employee.name, "company": self.company,
			"requested_amount": 40000, "request_date": frappe.utils.nowdate(),
			"installments": 1, "reason": PREFIX,
		}))
		hr_api.advance_action(name=name, action="submit_for_approval")
		hr_api.advance_action(name=name, action="approve")

		frappe.set_user("Administrator")
		self._created.append(("Isoft Salary Advance", name))
		self.assertEqual(frappe.db.get_value("Isoft Salary Advance", name, "status"), "Approved")

	def test_advance_is_still_refused_when_required(self):
		self.separation("require_separate_advance_approval", True)
		self.as_role("HR Manager")
		name = hr_api.create_advance(data=frappe.as_json({
			"employee": self.employee.name, "company": self.company,
			"requested_amount": 41000, "request_date": frappe.utils.nowdate(),
			"installments": 1, "reason": PREFIX,
		}))
		self._created.append(("Isoft Salary Advance", name))
		hr_api.advance_action(name=name, action="submit_for_approval")
		with self.assertRaises(frappe.ValidationError):
			hr_api.advance_action(name=name, action="approve")


class TestMoneyStillBelongsToFinance(HrManagerFixture):
	"""Clearing an HR switch must not hand the HR Manager the chequebook."""

	def test_disbursing_an_advance_is_a_role_restriction_not_a_switch(self):
		for required in (False, True):
			self.separation("require_separate_advance_approval", required)
			self.assertNotIn("HR Manager", perms.ACTION_ROLES[perms.ADVANCE_DISBURSE],
			                 "advance.disburse must stay with Finance")
		self.as_role("HR Manager")
		self.assertFalse(perms.can(perms.ADVANCE_DISBURSE))

	def test_posting_payroll_is_a_role_restriction(self):
		self.assertNotIn("HR Manager", perms.ACTION_ROLES[perms.PAYROLL_POST])
		self.as_role("HR Manager")
		self.assertFalse(perms.can(perms.PAYROLL_POST))
		self.assertFalse(perms.can(perms.PAYROLL_CONFIRM_PAYMENT))

	def test_the_payroll_switches_are_left_alone_by_the_hr_ones(self):
		"""Payroll preparation/approval/payment stay three roles: a financial control."""
		self.assertFalse(any(
			row["configurable"] for row in hr_ops.SELF_APPROVAL_POLICY
			if row["process"] == "Payroll"))


class TestClearingASwitchDoesNotWidenAnythingElse(HrManagerFixture):
	def test_a_user_without_the_capability_is_still_refused(self):
		self.separation("require_separate_contract_approval", False)
		name = self.a_contract()
		frappe.set_user("Administrator")
		hr_api.contract_action(name=name, action="submit_for_approval")
		self.as_role("HR User")          # holds contract.write, not contract.approve
		with self.assertRaises(frappe.PermissionError):
			hr_api.contract_action(name=name, action="approve")

	def test_the_workflow_state_is_still_checked(self):
		self.separation("require_separate_contract_approval", False)
		name = self.a_contract()         # still Draft - not submitted
		self.as_role("HR Manager")
		with self.assertRaises(frappe.ValidationError):
			hr_api.contract_action(name=name, action="approve")

	def test_approving_your_own_settlement_is_refused_whatever_the_switch(self):
		"""The one self-approval that is never configurable: your own final settlement."""
		self.separation("require_separate_settlement_approval", False)
		frappe.set_user("Administrator")
		emp = frappe.db.get_value("Employee", self.employee.name, "name")
		previous = frappe.db.get_value("Employee", emp, "user_id")
		frappe.db.set_value("Employee", emp, "user_id", self.users["HR Manager"],
		                    update_modified=False)
		self.addCleanup(frappe.db.set_value, "Employee", emp, "user_id", previous,
		                update_modified=False)
		doc = frappe.get_doc({
			"doctype": "Isoft Final Settlement", "employee": emp, "company": self.company,
			"termination_date": "2026-12-31", "reason": "Resignation",
			"workflow_status": "Pending Approval",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Final Settlement", doc.name))
		frappe.db.commit()
		self.as_role("HR Manager")
		with self.assertRaises(frappe.ValidationError):
			api.approve_settlement(name=doc.name)


class TestTheSwitchesAreReachable(HrManagerFixture):
	"""They were enforced but invisible: not returned by the API, not on the screen."""

	def test_get_settings_returns_every_switch(self):
		self.as_role("HR Manager")
		out = api.get_settings()
		for field in SWITCHES:
			self.assertIn(field, out, "%s is not reachable from the console" % field)

	def test_save_settings_persists_a_change(self):
		self.as_role("HR Manager")
		api.save_settings(data=frappe.as_json({"require_separate_contract_approval": 0}))
		frappe.set_user("Administrator")
		frappe.clear_cache()
		self.assertEqual(
			frappe.utils.cint(frappe.db.get_single_value(
				"Isoft HR Settings", "require_separate_contract_approval")), 0)

	def test_the_settlement_has_its_own_switch(self):
		"""It used to follow the ADVANCE switch — a control moving on its own."""
		self.separation("require_separate_advance_approval", True)
		self.separation("require_separate_settlement_approval", False)
		self.assertTrue(advances.requires_separate_approval())
		self.assertTrue(api._fs_self_approval_allowed(),
		                "the settlement switch is still shadowing the advance one")

	def test_the_published_policy_matches_the_live_settings(self):
		self.separation("require_separate_contract_approval", False)
		rows = {r["process"]: r for r in hr_ops.self_approval_policy()}
		self.assertTrue(rows["Contract"]["same_user"],
		                "the policy table still reports the shipped default, not the setting")
		self.assertTrue(rows["Contract"]["configurable"])
		self.assertIn("Final settlement", rows)


class TestTheRolesOwnDoctypesAreProvisioned(HrManagerFixture):
	"""A capability the app grants is useless if Frappe's DocPerms refuse the write."""

	def test_recruitment_reaches_the_offer(self):
		for doctype in ("Job Opening", "Job Applicant", "Job Offer"):
			self.assertTrue(_has_perm(doctype, "HR Manager", 0, "create"),
			                "HR Manager cannot create %s" % doctype)

	def test_performance_reaches_the_cycle(self):
		self.assertIn("HR Manager", perms.ACTION_ROLES[perms.PERFORMANCE_OPERATE])
		for doctype in ("Appraisal Template", "Appraisal"):
			self.assertTrue(_has_perm(doctype, "HR Manager", 0, "create"),
			                "HR Manager cannot create %s - the cycle needs it" % doctype)
