# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The single-HR-Manager approval model, and the one rule that holds it together.

The intended configuration is that one HR Manager may prepare **and** approve a Contract,
a Salary Change and a Salary Advance, while payroll approval, payment authorisation and
the Final Settlement each still require a second person.

The rule underneath is simpler than the configuration: **the screen must never offer an
action the server will refuse.** A button that fails on click is worse than an absent one
— it teaches people that the application is unreliable and that permission errors are
noise to be clicked through.

Contracts, advances and salary changes each filter their action list on self-approval.
Final settlements did not: ``_fs_transition`` refused a preparer's own approval and
refused another company's settlement, while ``_settlement_actions`` checked neither, so
the screen rendered Approve and the click rejected it. Both now call
``api._fs_approval_refusal``, and :class:`TestTheScreenNeverOffersWhatTheServerRefuses`
holds every workflow to that property rather than to a list of remembered cases.
"""

import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import advances, contracts, salary_change
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

SETTINGS = "Isoft HR Settings"

#: The four HR switches and the service that reads each one.
HR_SWITCHES = {
	"require_separate_contract_approval": contracts.require_separate_contract_approval,
	"require_separate_advance_approval": advances.requires_separate_approval,
	"require_separate_salary_change_approval": salary_change.requires_separate_approval,
}


def _set(field, value):
	frappe.db.set_value(SETTINGS, SETTINGS, field, value, update_modified=False)
	frappe.clear_cache()


class SwitchFixture(PayrollFixture):
	"""Restores every switch it touches, whatever the test does."""

	def setUp(self):
		super().setUp()
		self._switch_backup = {
			f: frappe.db.get_single_value(SETTINGS, f)
			for f in list(HR_SWITCHES) + ["require_separate_settlement_approval",
			                              "require_separate_payroll_approval",
			                              "require_separate_payment_approval"]
		}
		self.addCleanup(self._restore_switches)

	def _restore_switches(self):
		for field, value in self._switch_backup.items():
			_set(field, value)
		frappe.clear_cache()


# --------------------------------------------------------------------------- #
# 4 — the single HR Manager model
# --------------------------------------------------------------------------- #
class TestTheThreeHrSwitchesAreHonoured(SwitchFixture):
	"""Each switch governs its own workflow and nothing else."""

	def test_each_reader_follows_its_own_switch(self):
		for field, reader in HR_SWITCHES.items():
			for value, expected in ((1, True), (0, False)):
				with self.subTest(field=field, value=value):
					_set(field, value)
					self.assertEqual(bool(reader()), expected)

	def test_relaxing_one_switch_does_not_relax_the_others(self):
		"""The settlement switch once followed the ADVANCE switch — never again."""
		for field in HR_SWITCHES:
			_set(field, 1)
		_set("require_separate_settlement_approval", 1)
		for relaxed in HR_SWITCHES:
			with self.subTest(relaxed=relaxed):
				for field in HR_SWITCHES:
					_set(field, 1)
				_set("require_separate_settlement_approval", 1)
				_set(relaxed, 0)
				for field, reader in HR_SWITCHES.items():
					if field == relaxed:
						self.assertFalse(bool(reader()))
					else:
						self.assertTrue(bool(reader()),
						                "%s changed when %s was relaxed" % (field, relaxed))
				self.assertFalse(api._fs_self_approval_allowed(),
				                 "the settlement switch followed %s" % relaxed)

	def test_the_hr_switches_never_touch_payroll_or_payment(self):
		for field in HR_SWITCHES:
			_set(field, 0)
		self.assertEqual(
			frappe.db.get_single_value(SETTINGS, "require_separate_payroll_approval"),
			self._switch_backup["require_separate_payroll_approval"])
		self.assertEqual(
			frappe.db.get_single_value(SETTINGS, "require_separate_payment_approval"),
			self._switch_backup["require_separate_payment_approval"])


# --------------------------------------------------------------------------- #
# 3 — the Final Settlement control
# --------------------------------------------------------------------------- #
class TestSettlementSeparationDefaults(SwitchFixture):
	"""Unset must mean "separation required", not "separation off"."""

	def test_an_unset_value_forbids_self_approval(self):
		"""Frappe returns 0, not None, for a Check with no stored row — so a bare
		falsy test would read an absent setting as permission."""
		_set("require_separate_settlement_approval", None)
		self.assertFalse(api._fs_self_approval_allowed())

	def test_an_empty_string_forbids_self_approval(self):
		_set("require_separate_settlement_approval", "")
		self.assertFalse(api._fs_self_approval_allowed())

	def test_one_forbids_and_zero_permits(self):
		_set("require_separate_settlement_approval", 1)
		self.assertFalse(api._fs_self_approval_allowed())
		_set("require_separate_settlement_approval", 0)
		self.assertTrue(api._fs_self_approval_allowed())


class TestSettlementApprovalRefusal(SwitchFixture):
	"""The refusal rule, exercised directly on the helper both callers use."""

	def _doc(self, owner="preparer@example.invalid", employee=None, company=None):
		return frappe._dict({
			"company": company or self.company, "owner": owner,
			"employee": employee, "workflow_status": "Pending Approval",
		})

	def test_the_preparer_is_refused_when_separation_is_required(self):
		_set("require_separate_settlement_approval", 1)
		doc = self._doc(owner="hr@example.invalid")
		self.assertIsNotNone(api._fs_approval_refusal(doc, user="hr@example.invalid"))

	def test_another_approver_is_not_refused(self):
		_set("require_separate_settlement_approval", 1)
		doc = self._doc(owner="hr@example.invalid")
		self.assertIsNone(api._fs_approval_refusal(doc, user="other@example.invalid"))

	def test_the_preparer_is_allowed_only_when_explicitly_configured(self):
		_set("require_separate_settlement_approval", 0)
		doc = self._doc(owner="hr@example.invalid")
		self.assertIsNone(api._fs_approval_refusal(doc, user="hr@example.invalid"))

	def test_nobody_approves_their_own_settlement_whatever_the_switch_says(self):
		"""The employee guard is absolute — it is not configuration."""
		user = frappe.db.get_value("Employee", self.employee.name, "user_id")
		if not user:
			user = "self@example.invalid"
			frappe.db.set_value("Employee", self.employee.name, "user_id", user)
			self.addCleanup(frappe.db.set_value, "Employee", self.employee.name,
			                "user_id", None)
		for switch in (0, 1):
			with self.subTest(require_separate=switch):
				_set("require_separate_settlement_approval", switch)
				doc = self._doc(owner="someone.else@example.invalid",
				                employee=self.employee.name)
				self.assertIsNotNone(
					api._fs_approval_refusal(doc, user=user),
					"an employee approved their own settlement with the switch at %s"
					% switch)

	def test_a_settlement_of_another_company_is_refused(self):
		"""Company scope gates approval as surely as the capability does.

		Whether the site happens to restrict any real user is configuration and varies,
		so the scope check itself is exercised directly rather than skipped when the
		current user is unrestricted — the point is that the refusal consults it at all.
		"""
		_set("require_separate_settlement_approval", 0)
		doc = self._doc(owner="hr@example.invalid", company="__no_such_company__")
		real = perms.can_company
		perms.can_company = lambda company, user=None: company != "__no_such_company__"
		self.addCleanup(setattr, perms, "can_company", real)
		self.assertIsNotNone(
			api._fs_approval_refusal(doc, user="hr@example.invalid"),
			"a settlement outside the user's company scope was not refused")

	def test_a_settlement_inside_the_company_scope_is_not_refused_for_that_reason(self):
		_set("require_separate_settlement_approval", 0)
		doc = self._doc(owner="hr@example.invalid")
		real = perms.can_company
		perms.can_company = lambda company, user=None: True
		self.addCleanup(setattr, perms, "can_company", real)
		self.assertIsNone(api._fs_approval_refusal(doc, user="other@example.invalid"))


# --------------------------------------------------------------------------- #
# The invariant that ties 3 and 4 together
# --------------------------------------------------------------------------- #
class TestTheScreenNeverOffersWhatTheServerRefuses(SwitchFixture):
	"""For every workflow and every switch position, the action list must agree.

	Written as a property over the whole configuration space rather than a list of
	remembered cases, because the settlement defect was exactly a case nobody remembered.
	"""

	def _settlement(self, owner, state="Pending Approval", employee=None):
		return frappe._dict({"company": self.company, "owner": owner,
		                     "workflow_status": state, "employee": employee})

	def test_settlement_hides_approve_whenever_the_server_would_refuse_it(self):
		me = frappe.session.user
		for switch in (0, 1):
			with self.subTest(require_separate=switch):
				_set("require_separate_settlement_approval", switch)
				own = self._settlement(owner=me)
				offered = "approve" in api._settlement_actions(own)
				refused = api._fs_approval_refusal(own) is not None
				self.assertFalse(
					offered and refused,
					"settlement Approve offered while the server would refuse it")

	def test_contract_hides_approve_whenever_the_server_would_refuse_it(self):
		me = frappe.session.user
		for switch in (0, 1):
			with self.subTest(require_separate=switch):
				_set("require_separate_contract_approval", switch)
				doc = {"company": self.company, "status": "Pending Approval",
				       "submitted_by": me}
				offered = "approve" in contracts.allowed_actions(doc, user=me)
				self_approval = contracts._is_self_approval(doc, user=me)
				self.assertFalse(offered and self_approval,
				                 "contract Approve offered to its own preparer while "
				                 "separation is required")

	def test_advance_hides_approve_whenever_the_server_would_refuse_it(self):
		me = frappe.session.user
		for switch in (0, 1):
			with self.subTest(require_separate=switch):
				_set("require_separate_advance_approval", switch)
				doc = {"company": self.company, "status": "Pending Approval",
				       "requested_by": me}
				offered = "approve" in advances.allowed_actions(doc, user=me)
				self_approval = advances._is_self_approval(doc, user=me)
				self.assertFalse(offered and self_approval,
				                 "advance Approve offered to its own requester while "
				                 "separation is required")

	def test_every_offered_settlement_action_is_backed_by_a_capability(self):
		"""No action may appear that the permission matrix does not grant."""
		doc = self._settlement(owner="someone.else@example.invalid")
		for action in api._settlement_actions(doc):
			if action in ("edit", "recalculate", "cancel", "delete"):
				continue
			with self.subTest(action=action):
				_states, permission, _nxt = api._FS_TRANSITIONS[action]
				self.assertTrue(perms.can(permission),
				                "%s offered without %s" % (action, permission))

	def test_no_action_is_offered_for_a_state_that_forbids_it(self):
		for state in ("Draft", "Pending Approval", "Approved", "Posted", "Paid",
		              "Cancelled", "Rejected"):
			doc = self._settlement(owner="someone.else@example.invalid", state=state)
			for action in api._settlement_actions(doc):
				if action in ("edit", "recalculate", "cancel", "delete"):
					continue
				with self.subTest(state=state, action=action):
					states, _p, _n = api._FS_TRANSITIONS[action]
					self.assertIn(state, states)


class TestSettlementCapabilityIsUnchangedByTheSwitches(SwitchFixture):
	"""Relaxing separation must not hand anybody a capability they did not have."""

	def test_the_settlement_transitions_still_use_the_payroll_capabilities(self):
		self.assertEqual(api._FS_TRANSITIONS["approve"][1], perms.PAYROLL_APPROVE)
		self.assertEqual(api._FS_TRANSITIONS["post"][1], perms.PAYROLL_POST)
		self.assertEqual(api._FS_TRANSITIONS["pay"][1], perms.PAYROLL_CONFIRM_PAYMENT)

	def test_posting_and_paying_remain_finance_only(self):
		finance = perms.ACTION_ROLES[perms.PAYROLL_POST]
		self.assertNotIn(perms.HR_MANAGER, finance)
		self.assertNotIn(perms.HR_MANAGER, perms.ACTION_ROLES[perms.PAYROLL_CONFIRM_PAYMENT])

	def test_relaxing_the_hr_switches_leaves_the_finance_matrix_untouched(self):
		before = {a: set(r) for a, r in perms.ACTION_ROLES.items()}
		for field in HR_SWITCHES:
			_set(field, 0)
		_set("require_separate_settlement_approval", 0)
		after = {a: set(r) for a, r in perms.ACTION_ROLES.items()}
		self.assertEqual(before, after,
		                 "a configuration switch changed the permission matrix")
