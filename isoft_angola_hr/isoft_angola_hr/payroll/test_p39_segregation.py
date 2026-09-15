# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Segregation of duties — the six controls, and the one way they are read.

The production configuration was found with all six switches at 0, including the two
Finance ones that are meant never to move. Restoring them is a configuration change;
what this module pins is that the switches MEAN something, that the same answer is given
to the screen and to the server, and that they cannot quietly read as "off".

**The defect these tests exist for.** Every reader in the app used
``frappe.db.get_single_value``, guarded like this::

    value = frappe.db.get_single_value("Isoft HR Settings", field)
    return cint(1 if value is None or value == "" else value)

``get_single_value`` casts a Check through ``cint(sbool(value))``, so an absent row comes
back as ``0`` — never ``None``. Every one of those fail-closed branches was therefore
unreachable, and a site that migrated into a new control read it as **switched off**.
For a control whose entire purpose is to require a second person, that is the wrong way
round. :func:`isoft_hr_settings.separation_required` reads the stored row instead, so
"nobody has decided" and "somebody switched it off" stay distinguishable.

The tests below drive real users holding real roles through a real payroll entry, in
QA. Nothing here touches production.
"""

import frappe
from frappe.utils import flt, now, nowdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
	SEPARATION_FIELDS, separation_required)
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (PREFIX,
                                                                        PayrollFixture)
from isoft_angola_hr.isoft_angola_hr.services import advances, contracts
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import salary_change

#: The production policy this release is gated on.
INTENDED = {
	"require_separate_contract_approval": 0,
	"require_separate_salary_change_approval": 0,
	"require_separate_advance_approval": 0,
	"require_separate_payroll_approval": 1,
	"require_separate_payment_approval": 1,
	"require_separate_settlement_approval": 1,
}


def _write(field, value):
	"""Set a switch the way the Settings screen does, and drop every cache behind it."""
	if value is None:
		frappe.db.sql("""delete from `tabSingles` where doctype = %s and field = %s""",
		              ("Isoft HR Settings", field))
	else:
		frappe.db.sql("""delete from `tabSingles` where doctype = %s and field = %s""",
		              ("Isoft HR Settings", field))
		frappe.db.sql("""insert into `tabSingles` (doctype, field, value)
		              values (%s, %s, %s)""", ("Isoft HR Settings", field, value))
	frappe.clear_document_cache("Isoft HR Settings", "Isoft HR Settings")
	frappe.db.value_cache.pop("Isoft HR Settings", None)
	frappe.clear_cache()


class SegregationFixture(PayrollFixture):
	"""Users for every role in the payroll chain, plus a payroll entry to move."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = {}
		for label, roles in (
			("officer", [perms.PAYROLL_OFFICER]),
			("officer2", [perms.PAYROLL_OFFICER]),
			("manager", [perms.PAYROLL_MANAGER]),
			("manager2", [perms.PAYROLL_MANAGER]),
			("finance", [perms.PAYROLL_FINANCE]),
			("finance2", [perms.PAYROLL_FINANCE]),
			("hrmanager", [perms.HR_MANAGER]),
			("norole", []),
		):
			email = "seg.%s@example.invalid" % label
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email, "send_welcome_email": 0,
					"first_name": "%s SEG %s" % (PREFIX, label),
					"roles": [{"role": r} for r in roles],
				}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.users[label] = email
		cls._switch_backup = {
			f: frappe.db.sql("""select value from `tabSingles`
			                 where doctype = %s and field = %s""",
			                 ("Isoft HR Settings", f)) for f in SEPARATION_FIELDS}

	@classmethod
	def tearDownClass(cls):
		for field, rows in getattr(cls, "_switch_backup", {}).items():
			_write(field, rows[0][0] if rows else None)
		frappe.set_user("Administrator")
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		for field, value in INTENDED.items():
			_write(field, value)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def as_user(self, label):
		frappe.set_user(self.users[label])

	_group_seq = [0]

	def entry(self, status="Pending Approval", **stamps):
		"""A payroll entry parked in a given state with a given audit trail.

		Each one gets its own Payroll Group: the DocType refuses two live runs covering
		the same period for the same company and group, which is correct behaviour and
		not something a test should be allowed to sidestep.
		"""
		self._group_seq[0] += 1
		doc = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": "%s SEG %03d" % (PREFIX, self._group_seq[0]),
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", doc.name))
		values = {"status": status}
		values.update(stamps)
		frappe.db.set_value("Isoft Payroll Entry", doc.name, values)
		return frappe.get_doc("Isoft Payroll Entry", doc.name)


# --------------------------------------------------------------------------- #
# 1. The reader: an absent switch must NOT read as "off"
# --------------------------------------------------------------------------- #
class TestAnAbsentSwitchFailsClosed(SegregationFixture):
	"""The whole point of the ``separation_required`` fix."""

	def test_get_single_value_cannot_tell_absent_from_zero(self):
		"""The premise. If this ever stops being true the guard can go back."""
		_write("require_separate_payroll_approval", None)
		self.assertEqual(
			frappe.db.get_single_value("Isoft HR Settings",
			                           "require_separate_payroll_approval"), 0,
			"get_single_value no longer casts an absent Check to 0")

	def test_an_absent_switch_still_requires_a_second_person(self):
		for field in SEPARATION_FIELDS:
			with self.subTest(field=field):
				_write(field, None)
				self.assertEqual(separation_required(field), 1,
				                 "%s read as switched off because nobody had set it" % field)

	def test_an_empty_switch_still_requires_a_second_person(self):
		for field in SEPARATION_FIELDS:
			with self.subTest(field=field):
				_write(field, "")
				self.assertEqual(separation_required(field), 1)

	def test_an_explicit_zero_is_the_only_thing_that_relaxes_it(self):
		for field in SEPARATION_FIELDS:
			with self.subTest(field=field):
				_write(field, 0)
				self.assertEqual(separation_required(field), 0)
				_write(field, 1)
				self.assertEqual(separation_required(field), 1)

	def test_every_service_reader_fails_closed_on_an_absent_switch(self):
		"""Not the helper in isolation — the functions the workflows actually call."""
		cases = [
			("require_separate_payroll_approval", wf.requires_separate_approval),
			("require_separate_payment_approval", wf.requires_separate_payment_approval),
			("require_separate_contract_approval",
			 contracts.require_separate_contract_approval),
			("require_separate_advance_approval", advances.requires_separate_approval),
			("require_separate_salary_change_approval",
			 salary_change.requires_separate_approval),
		]
		for field, reader in cases:
			with self.subTest(field=field):
				_write(field, None)
				self.assertTrue(reader(), "%s failed OPEN when unset" % field)

	def test_the_settlement_reader_fails_closed_on_an_absent_switch(self):
		_write("require_separate_settlement_approval", None)
		self.assertFalse(api._fs_self_approval_allowed())

	def test_no_reader_in_the_app_still_uses_get_single_value_for_these(self):
		"""A source check, so the fix cannot be undone one file at a time."""
		import io
		import os
		root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		offenders = []
		for base, dirs, files in os.walk(root):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
			for name in files:
				if not name.endswith(".py") or name.startswith("test_"):
					continue
				path = os.path.join(base, name)
				src = io.open(path, encoding="utf-8").read()
				for line_no, line in enumerate(src.split("\n"), 1):
					if "get_single_value" in line and "require_separate" in line:
						offenders.append("%s:%d" % (os.path.relpath(path, root), line_no))
				# the two-line form
				for field in SEPARATION_FIELDS:
					needle = 'get_single_value("Isoft HR Settings",\n\t                                   "%s"' % field
					if needle in src:
						offenders.append("%s (%s)" % (os.path.relpath(path, root), field))
		self.assertEqual(offenders, [],
		                 "these read a separation switch through get_single_value, which "
		                 "cannot distinguish absent from zero: %s" % offenders)


# --------------------------------------------------------------------------- #
# 2. Payroll approval — the creator may not approve their own
# --------------------------------------------------------------------------- #
class TestPayrollApprovalSeparation(SegregationFixture):

	def test_the_preparer_cannot_approve_their_own_payroll(self):
		doc = self.entry(prepared_by=self.users["hrmanager"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		with self.assertRaises(frappe.ValidationError) as caught:
			wf.perform(doc, wf.APPROVE)
		self.assertIn("cannot approve", str(caught.exception).lower())

	def test_a_different_approver_with_the_role_can_approve(self):
		doc = self.entry(prepared_by=self.users["officer"],
		                 submitted_by=self.users["officer"])
		self.as_user("manager")
		self.assertEqual(wf.perform(doc, wf.APPROVE), wf.APPROVED)

	def test_the_approval_is_stamped_with_who_gave_it(self):
		doc = self.entry(prepared_by=self.users["officer"],
		                 submitted_by=self.users["officer"])
		self.as_user("manager")
		wf.perform(doc, wf.APPROVE)
		self.assertEqual(doc.approved_by, self.users["manager"])
		self.assertTrue(doc.approved_at)

	def test_a_second_officer_is_not_blocked_by_the_first_ones_preparation(self):
		"""Separation is by identity, not by role."""
		doc = self.entry(prepared_by=self.users["officer"],
		                 submitted_by=self.users["officer"])
		self.as_user("manager2")
		self.assertEqual(wf.perform(doc, wf.APPROVE), wf.APPROVED)

	def test_switching_the_control_off_permits_self_approval(self):
		_write("require_separate_payroll_approval", 0)
		doc = self.entry(prepared_by=self.users["hrmanager"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		self.assertEqual(wf.perform(doc, wf.APPROVE), wf.APPROVED)

	def test_the_submitter_counts_even_when_somebody_else_prepared(self):
		doc = self.entry(prepared_by=self.users["officer"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		with self.assertRaises(frappe.ValidationError):
			wf.perform(doc, wf.APPROVE)

	def test_a_user_without_the_capability_is_refused_before_the_state_is_considered(self):
		doc = self.entry(status="Draft", prepared_by=self.users["officer"])
		self.as_user("norole")
		with self.assertRaises(frappe.PermissionError):
			wf.perform(doc, wf.APPROVE)

	def test_a_payroll_officer_may_not_approve_at_all(self):
		doc = self.entry(prepared_by=self.users["officer2"],
		                 submitted_by=self.users["officer2"])
		self.as_user("officer")
		with self.assertRaises(frappe.PermissionError):
			wf.perform(doc, wf.APPROVE)


# --------------------------------------------------------------------------- #
# 3. Payment authorisation — the approver may not release or pay
# --------------------------------------------------------------------------- #
class TestPaymentSeparation(SegregationFixture):

	def test_the_approver_cannot_release_the_payroll_they_approved(self):
		doc = self.entry(status="Posted", approved_by=self.users["finance"])
		self.as_user("finance")
		with self.assertRaises(frappe.ValidationError) as caught:
			wf.perform(doc, wf.RELEASE_FOR_PAYMENT)
		self.assertIn("payment", str(caught.exception).lower())

	def test_the_approver_cannot_confirm_the_payment_either(self):
		doc = self.entry(status="Payment Ready", approved_by=self.users["finance"])
		self.as_user("finance")
		with self.assertRaises(frappe.ValidationError):
			wf.perform(doc, wf.PAY)

	def test_a_different_finance_user_can_release_it(self):
		doc = self.entry(status="Posted", approved_by=self.users["manager"])
		self.as_user("finance")
		self.assertEqual(wf.perform(doc, wf.RELEASE_FOR_PAYMENT), wf.PAYMENT_READY)

	def test_a_different_finance_user_can_confirm_the_payment(self):
		doc = self.entry(status="Payment Ready", approved_by=self.users["manager"])
		self.as_user("finance2")
		self.assertEqual(wf.perform(doc, wf.PAY), wf.PAID)

	def test_the_release_is_stamped_with_who_authorised_it(self):
		doc = self.entry(status="Posted", approved_by=self.users["manager"])
		self.as_user("finance")
		wf.perform(doc, wf.RELEASE_FOR_PAYMENT)
		self.assertEqual(doc.payment_authorized_by, self.users["finance"])

	def test_hr_manager_can_never_authorise_a_payment(self):
		"""The Finance boundary is roles, not switches — relaxing every switch must not
		hand payment authority to HR."""
		for field in SEPARATION_FIELDS:
			_write(field, 0)
		doc = self.entry(status="Posted", approved_by=self.users["manager"])
		self.as_user("hrmanager")
		with self.assertRaises(frappe.PermissionError):
			wf.perform(doc, wf.RELEASE_FOR_PAYMENT)

	def test_a_user_without_a_role_cannot_pay(self):
		doc = self.entry(status="Payment Ready", approved_by=self.users["manager"])
		self.as_user("norole")
		with self.assertRaises(frappe.PermissionError):
			wf.perform(doc, wf.PAY)

	def test_switching_the_control_off_permits_the_approver_to_pay(self):
		_write("require_separate_payment_approval", 0)
		doc = self.entry(status="Posted", approved_by=self.users["finance"])
		self.as_user("finance")
		self.assertEqual(wf.perform(doc, wf.RELEASE_FOR_PAYMENT), wf.PAYMENT_READY)


# --------------------------------------------------------------------------- #
# 4. The screen and the server give the same answer
# --------------------------------------------------------------------------- #
class TestTheScreenNeverOffersWhatTheServerRefuses(SegregationFixture):
	"""There must never be a case where the UI offers Approve and the backend rejects it.

	The check is exhaustive rather than illustrative: every state × every actor × both
	settings of both controls, comparing what ``allowed_actions`` renders with what
	``assert_transition`` would do.
	"""

	STATES = (wf.DRAFT, wf.CALCULATED, wf.PENDING_APPROVAL, wf.REJECTED, wf.APPROVED,
	          wf.POSTED, wf.PAYMENT_READY, wf.PAID, wf.CLOSED)

	def test_every_offered_action_is_actually_permitted(self):
		mismatches = []
		for payroll_sep in (0, 1):
			_write("require_separate_payroll_approval", payroll_sep)
			for payment_sep in (0, 1):
				_write("require_separate_payment_approval", payment_sep)
				for state in self.STATES:
					doc = self.entry(status=state,
					                 prepared_by=self.users["hrmanager"],
					                 submitted_by=self.users["hrmanager"],
					                 approved_by=self.users["finance"])
					for label in ("officer", "manager", "finance", "hrmanager", "norole"):
						self.as_user(label)
						for action in wf.allowed_actions(doc):
							try:
								wf.assert_transition(doc, action)
							except Exception as exc:
								mismatches.append(
									"%s/%s sep=%d/%d -> %s refused: %s"
									% (state, label, payroll_sep, payment_sep, action,
									   frappe.utils.strip_html(str(exc))[:80]))
						frappe.set_user("Administrator")
		self.assertEqual(mismatches, [],
		                 "the screen offered an action the server refuses:\n  "
		                 + "\n  ".join(mismatches))

	def test_the_preparer_is_never_offered_approve_while_separation_is_on(self):
		_write("require_separate_payroll_approval", 1)
		doc = self.entry(prepared_by=self.users["hrmanager"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		self.assertNotIn(wf.APPROVE, wf.allowed_actions(doc))

	def test_the_preparer_is_offered_approve_once_separation_is_off(self):
		_write("require_separate_payroll_approval", 0)
		doc = self.entry(prepared_by=self.users["hrmanager"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		self.assertIn(wf.APPROVE, wf.allowed_actions(doc))

	def test_the_approver_is_never_offered_release_while_payment_separation_is_on(self):
		_write("require_separate_payment_approval", 1)
		doc = self.entry(status="Posted", approved_by=self.users["finance"])
		self.as_user("finance")
		self.assertNotIn(wf.RELEASE_FOR_PAYMENT, wf.allowed_actions(doc))


# --------------------------------------------------------------------------- #
# 5. No cache stands between the setting and the decision
# --------------------------------------------------------------------------- #
class TestNoStaleValueSurvivesAChange(SegregationFixture):

	def test_a_change_takes_effect_within_the_same_request(self):
		"""frappe.db.value_cache memoises get_single_value per request; a control that
		decides whether an approval is legal must not be answered from it."""
		for field in SEPARATION_FIELDS:
			with self.subTest(field=field):
				_write(field, 1)
				self.assertEqual(separation_required(field), 1)
				frappe.db.sql("""update `tabSingles` set value = '0'
				              where doctype = %s and field = %s""",
				              ("Isoft HR Settings", field))
				self.assertEqual(separation_required(field), 0,
				                 "%s was answered from a cache, not from the row" % field)

	def test_the_switches_are_not_frozen_at_import_time(self):
		"""A module-level constant would freeze the value for the worker's whole life,
		which is what ``bench restart`` would then appear to 'fix'."""
		import inspect
		src = inspect.getsource(separation_required)
		self.assertIn("frappe.db.sql", src)
		self.assertNotIn("=", src.split("def separation_required")[0])

	def test_reading_it_twice_in_a_row_gives_the_stored_row_both_times(self):
		_write("require_separate_payroll_approval", 1)
		first = wf.requires_separate_approval()
		_write("require_separate_payroll_approval", 0)
		second = wf.requires_separate_approval()
		self.assertEqual((first, second), (1, 0))


# --------------------------------------------------------------------------- #
# 6. The intended production policy, as a specification
# --------------------------------------------------------------------------- #
class TestTheIntendedPolicy(SegregationFixture):
	"""What the six switches are supposed to be on the production site. The values live
	here so a later change to the operating model is a visible edit to a named
	specification rather than an undocumented click in Settings."""

	def test_the_three_hr_controls_are_relaxed_and_the_three_others_are_not(self):
		relaxed = [f for f, v in INTENDED.items() if v == 0]
		required = [f for f, v in INTENDED.items() if v == 1]
		self.assertEqual(sorted(relaxed), sorted([
			"require_separate_advance_approval",
			"require_separate_contract_approval",
			"require_separate_salary_change_approval"]))
		self.assertEqual(sorted(required), sorted([
			"require_separate_payment_approval",
			"require_separate_payroll_approval",
			"require_separate_settlement_approval"]))

	def test_the_specification_covers_every_switch_the_app_has(self):
		self.assertEqual(sorted(INTENDED), sorted(SEPARATION_FIELDS))

	def test_under_the_intended_policy_one_hr_manager_runs_the_hr_processes(self):
		for field, value in INTENDED.items():
			_write(field, value)
		self.assertFalse(contracts.require_separate_contract_approval())
		self.assertFalse(advances.requires_separate_approval())
		self.assertFalse(salary_change.requires_separate_approval())

	def test_under_the_intended_policy_finance_is_still_two_people(self):
		for field, value in INTENDED.items():
			_write(field, value)
		self.assertTrue(wf.requires_separate_approval())
		self.assertTrue(wf.requires_separate_payment_approval())

	def test_under_the_intended_policy_a_settlement_still_needs_a_second_person(self):
		for field, value in INTENDED.items():
			_write(field, value)
		self.assertFalse(api._fs_self_approval_allowed())

	def test_relaxing_the_hr_controls_leaves_the_finance_matrix_untouched(self):
		for field in ("require_separate_contract_approval",
		              "require_separate_advance_approval",
		              "require_separate_salary_change_approval"):
			_write(field, 0)
		self.assertEqual(perms.ACTION_ROLES[perms.PAYROLL_POST],
		                 frozenset({perms.PAYROLL_FINANCE, perms.ACCOUNTS_MANAGER}))
		self.assertEqual(perms.ACTION_ROLES[perms.PAYROLL_CONFIRM_PAYMENT],
		                 frozenset({perms.PAYROLL_FINANCE, perms.ACCOUNTS_MANAGER}))
		self.assertNotIn(perms.HR_MANAGER, perms.ACTION_ROLES[perms.PAYROLL_POST])


# --------------------------------------------------------------------------- #
# 7. Final settlement
# --------------------------------------------------------------------------- #
class TestSettlementSeparation(SegregationFixture):

	def _doc(self, owner, employee=None):
		return frappe._dict({"company": self.company, "owner": owner,
		                     "employee": employee})

	def test_the_preparer_is_refused_under_the_intended_policy(self):
		frappe.set_user(self.users["hrmanager"])
		refusal = api._fs_approval_refusal(self._doc(self.users["hrmanager"]))
		self.assertTrue(refusal)

	def test_a_second_approver_is_not_refused_for_that_reason(self):
		frappe.set_user(self.users["hrmanager"])
		self.assertIsNone(api._fs_approval_refusal(self._doc(self.users["officer"])))

	def test_nobody_approves_their_own_settlement_whatever_the_switch_says(self):
		_write("require_separate_settlement_approval", 0)
		frappe.set_user(self.users["hrmanager"])
		frappe.db.set_value("Employee", self.employee.name, "user_id",
		                    self.users["hrmanager"])
		try:
			refusal = api._fs_approval_refusal(
				self._doc(self.users["officer"], employee=self.employee.name))
			self.assertTrue(refusal, "an employee could approve their own settlement")
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Employee", self.employee.name, "user_id", None)

	def test_a_settlement_of_another_company_is_refused(self):
		"""Company scope only exists once the user is actually restricted: with no User
		Permission on Company, Frappe grants every company, so the restriction has to be
		created for the check to have anything to refuse."""
		perm = frappe.get_doc({
			"doctype": "User Permission", "user": self.users["hrmanager"],
			"allow": "Company", "for_value": self.company,
		}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("User Permission", perm.name, force=True,
		                                          ignore_permissions=True))
		frappe.clear_cache(user=self.users["hrmanager"])
		frappe.set_user(self.users["hrmanager"])
		other = [c for c in frappe.get_all("Company", pluck="name") if c != self.company]
		target = other[0] if other else "__no_such_company__"
		self.assertTrue(api._fs_approval_refusal(
			frappe._dict({"company": target, "owner": self.users["officer"]})),
			"a settlement outside the user's company scope was approvable")

	def test_a_settlement_inside_the_company_scope_is_not_refused_for_that_reason(self):
		perm = frappe.get_doc({
			"doctype": "User Permission", "user": self.users["hrmanager"],
			"allow": "Company", "for_value": self.company,
		}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("User Permission", perm.name, force=True,
		                                          ignore_permissions=True))
		frappe.clear_cache(user=self.users["hrmanager"])
		frappe.set_user(self.users["hrmanager"])
		self.assertIsNone(api._fs_approval_refusal(self._doc(self.users["officer"])))
