# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Defects found by the isolated browser/API UAT, pinned so they cannot come back.

Every test here reproduces a failure that was observed against a restored copy of the
production database, by the role the application itself says owns the action. They fall
into three groups:

  * A capability the app grants that Frappe's DocPerms then refused. ``perms.require``
    passing only means the APPLICATION agreed; the write still goes through DocPerms, and
    where the two disagreed the user got a bare 403 with no message — or, worse, at
    permlevel > 0, a silent discard of the field they had just changed.
  * A whitelisted helper with no authorisation at all.
  * A payroll input that is accepted but cannot be posted, and whose blast radius is the
    whole batch rather than the one employee.

The negative half of each test matters as much as the positive half: the fix is meant to
be narrow, so a later change that grants a role blanket rights fails these too.
"""

import json

import frappe
from frappe.utils import add_days, nowdate

from isoft_angola_hr.isoft_angola_hr import api, hr_api
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
	account_query,
)
from isoft_angola_hr.isoft_angola_hr.install import _has_perm, grant_capability_docperms
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness

ROLES = ("HR User", "HR Manager", "Payroll Officer", "Payroll Manager",
         "Payroll Finance Approver", "Accounts Manager")


class GateFixture(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# the provisioning under test is what after_install/migrate runs
		grant_capability_docperms()
		cls.role_users = {}
		for role in ROLES:
			email = "gate.%s@example.invalid" % role.lower().replace(" ", "")
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email,
					"first_name": PREFIX + " " + role, "send_welcome_email": 0,
					"roles": [{"role": role}],
				}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.role_users[role] = email
		# an account with a login and no roles at all
		if not frappe.db.exists("User", "gate.zero@example.invalid"):
			doc = frappe.get_doc({
				"doctype": "User", "email": "gate.zero@example.invalid",
				"first_name": PREFIX + " zero", "send_welcome_email": 0, "roles": [],
			}).insert(ignore_permissions=True)
			cls._created.append(("User", doc.name))

	def as_role(self, role):
		frappe.set_user(self.role_users[role])
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def as_user(self, email):
		frappe.set_user(email)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def tearDown(self):
		"""Tolerate the commits the code under test performs.

		The fixture isolates each test with a savepoint, but a commit discards savepoints
		and two of the flows here commit because they must in production: approving leave
		and writing DocPerms. When the savepoint is gone there is nothing left to roll back
		to, so a plain rollback is the correct close-out; records that survived the commit
		are removed by the class-level cleanup, which is what it is for.
		"""
		try:
			super().tearDown()
		except Exception:
			frappe.db.rollback()


class TestCapabilitiesAreBackedByDocPerms(GateFixture):
	"""Each of these was a 403 in the browser for the role that owns the action."""

	def test_hr_manager_can_read_designation_for_the_employee_edit_dialog(self):
		# The Edit Employee dialog carries a Designation Link. Its control resolves the
		# link before the form will save, so without read the dialog could not be saved
		# and the message named the field, not the cause.
		self.as_role("HR Manager")
		self.assertTrue(frappe.has_permission("Designation", "read"))

	def test_hr_manager_can_run_recruitment(self):
		# RECRUITMENT_OPERATE lists HR Manager, but only HR User carried the DocPerms.
		self.assertIn("HR Manager", perms.ACTION_ROLES[perms.RECRUITMENT_OPERATE])
		for doctype in ("Job Opening", "Job Applicant"):
			self.assertTrue(_has_perm(doctype, "HR Manager", 0, "create"),
			                "HR Manager cannot create %s" % doctype)

	def test_payroll_officer_can_record_attendance(self):
		# ATTENDANCE_WRITE lists Payroll Officer; Attendance is submittable and
		# mark_attendance submits it, so create alone would not have been enough.
		self.assertIn("Payroll Officer", perms.ACTION_ROLES[perms.ATTENDANCE_WRITE])
		for prop in ("read", "write", "create", "submit", "cancel"):
			self.assertTrue(_has_perm("Attendance", "Payroll Officer", 0, prop),
			                "Payroll Officer cannot %s Attendance" % prop)

	def test_payroll_officer_can_prepare_a_settlement_but_not_approve_it(self):
		self.assertIn("Payroll Officer", perms.ACTION_ROLES[perms.PAYROLL_PREPARE])
		for prop in ("read", "write", "create"):
			self.assertTrue(_has_perm("Isoft Final Settlement", "Payroll Officer", 0, prop))
		# narrow on purpose: preparing is not approving or reversing
		for prop in ("submit", "cancel", "delete"):
			self.assertFalse(_has_perm("Isoft Final Settlement", "Payroll Officer", 0, prop),
			                 "preparing a settlement must not imply %s" % prop)

	def test_hr_manager_can_write_the_leave_status_field(self):
		"""LEAVE_APPROVE is HR Manager alone, and status is a permlevel-1 field.

		Frappe DISCARDS a change to a field the user cannot write at that permlevel
		instead of refusing it, so the approval silently reverted to Open and the submit
		then failed with a message about the status — never about the permission.
		"""
		self.assertEqual(set(perms.ACTION_ROLES[perms.LEAVE_APPROVE]), {"HR Manager"})
		self.assertEqual(frappe.get_meta("Leave Application").get_field("status").permlevel, 1)
		self.assertTrue(_has_perm("Leave Application", "HR Manager", 1, "write"))

	def test_provisioning_is_idempotent_and_never_lowers_a_permission(self):
		before = {(d, r, p): _has_perm(d, r, 0, p)
		          for d in ("Attendance", "Designation", "Job Opening")
		          for r in ROLES for p in ("read", "write", "create", "submit")}
		grant_capability_docperms()
		after = {k: _has_perm(k[0], k[1], 0, k[2]) for k in before}
		for k, was in before.items():
			if was:
				self.assertTrue(after[k], "%s was lowered by re-running provisioning" % (k,))


class TestLeaveApprovalCompletes(GateFixture):
	"""The end of the story the permlevel test starts: approval must actually stick."""

	def _leave(self, day_offset=0):
		frappe.set_user("Administrator")
		lt = frappe.db.get_value("Leave Type", {"name": "Compensatory Off"}) \
			or frappe.db.get_value("Leave Type", {})
		# ERPNext refuses an application outside an allocation period, so the allocation
		# is part of the fixture rather than part of what is being tested
		year_start, year_end = "%s-01-01" % nowdate()[:4], "%s-12-31" % nowdate()[:4]
		if not frappe.db.exists("Leave Allocation",
		                        {"employee": self.employee.name, "leave_type": lt,
		                         "docstatus": 1}):
			alloc = frappe.get_doc({
				"doctype": "Leave Allocation", "employee": self.employee.name,
				"leave_type": lt, "from_date": year_start, "to_date": year_end,
				"new_leaves_allocated": 20, "company": self.company,
			}).insert(ignore_permissions=True)
			alloc.submit()
			self._created.append(("Leave Allocation", alloc.name))
			type(self)._alloc = alloc.name
		# each test needs its own day: ERPNext refuses two applications that overlap
		day = add_days(nowdate(), day_offset)
		doc = frappe.get_doc({
			"doctype": "Leave Application", "employee": self.employee.name,
			"leave_type": lt, "from_date": day, "to_date": day,
			"company": self.company, "status": "Open",
			"leave_approver": self.role_users["HR Manager"],
			"description": PREFIX + " gate",
		}).insert(ignore_permissions=True)
		self._created.append(("Leave Application", doc.name))
		# a submitted document cannot be deleted, so make sure teardown can remove it
		self.addCleanup(self._cancel_if_submitted, "Leave Application", doc.name)
		return doc.name

	@staticmethod
	def _cancel_if_submitted(doctype, name):
		frappe.set_user("Administrator")
		if frappe.db.get_value(doctype, name, "docstatus") == 1:
			frappe.get_doc(doctype, name).cancel()
			frappe.db.commit()

	def test_hr_manager_approval_is_recorded_and_submitted(self):
		name = self._leave()
		self.as_role("HR Manager")
		api.approve_leave(name)

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Leave Application", name, "status"), "Approved")
		self.assertEqual(frappe.db.get_value("Leave Application", name, "docstatus"), 1)

	def test_hr_user_still_cannot_approve_leave(self):
		name = self._leave(day_offset=3)
		self.as_role("HR User")
		with self.assertRaises(frappe.PermissionError):
			api.approve_leave(name)


class TestAccountQueryIsAuthorised(GateFixture):
	"""A whitelisted link query is reachable over HTTP by anyone with a login.

	This one answered with the company's chart of accounts — number, name and root type —
	for an account holding no roles at all.
	"""

	ARGS = ("Account", "", "name", 0, 5)

	def test_a_user_with_no_roles_cannot_enumerate_the_chart_of_accounts(self):
		self.as_user("gate.zero@example.invalid")
		with self.assertRaises(frappe.PermissionError):
			account_query(*self.ARGS, filters={})

	def test_hr_roles_without_account_duties_cannot_either(self):
		for role in ("HR User", "Payroll Officer", "Payroll Manager"):
			frappe.set_user(self.role_users[role])
			with self.assertRaises(frappe.PermissionError):
				account_query(*self.ARGS, filters={})
		frappe.set_user("Administrator")

	def test_the_roles_that_map_or_post_accounts_still_get_their_link_query(self):
		# HR Manager owns the mappings (settings.write); finance posts to them.
		for role in ("HR Manager", "Payroll Finance Approver", "Accounts Manager"):
			frappe.set_user(self.role_users[role])
			rows = account_query(*self.ARGS, filters={})
			self.assertIsInstance(rows, (list, tuple),
			                      "%s lost the account link query" % role)
		frappe.set_user("Administrator")

	def test_filters_may_arrive_as_json_text(self):
		"""Called directly rather than through the desk, filters is a string."""
		self.as_role("HR Manager")
		rows = account_query(*self.ARGS, filters=json.dumps({"root_types": ["Expense"]}))
		self.assertIsInstance(rows, (list, tuple))


class TestInterviewPipelineContract(GateFixture):
	"""The console read this response as a list; it has always been an object.

	The result was "(rows || []).filter is not a function" and a button that did nothing
	at all — silently, because an unhandled rejection has nowhere to surface. The shape is
	pinned here so the console and the endpoint cannot drift apart again.
	"""

	def test_interview_pipeline_returns_an_object_with_rows(self):
		self.as_role("HR Manager")
		res = hr_api.interview_pipeline()
		self.assertIsInstance(res, dict)
		self.assertIn("available", res)
		if res.get("available"):
			self.assertIsInstance(res.get("rows"), list)
			self.assertIn("stages", res)
		else:
			self.assertTrue(res.get("message"), "an unavailable pipeline must say why")


class TestZeroBaseProfileIsCaughtBeforePayroll(GateFixture):
	"""A profile that exists but pays nothing produces a slip that cannot be posted.

	The ledger refuses a voucher whose debit and credit are both zero, and that refusal
	aborts the whole batch — so one employee's empty profile stops everybody else being
	posted and paid. Worse, by then the payroll is Approved and the slip can no longer be
	corrected without cancelling the approval, so the run has to be unwound.
	"""

	def test_exception_catalogue_carries_the_code(self):
		self.assertIn("EXC-013", readiness.EXCEPTIONS)
		severity, _label = readiness.EXCEPTIONS["EXC-013"]
		self.assertEqual(severity, readiness.BLOCKING)

	def test_a_zero_base_profile_is_reported_as_blocking(self):
		frappe.set_user("Administrator")
		profile = frappe.db.get_value("Isoft Salary Profile",
		                              {"employee": self.employee.name}, "name")
		self.assertTrue(profile, "fixture employee should carry a salary profile")
		original = frappe.db.get_value("Isoft Salary Profile", profile, "base")
		frappe.db.set_value("Isoft Salary Profile", profile, "base", 0)
		self.addCleanup(frappe.db.set_value, "Isoft Salary Profile", profile, "base", original)

		res = readiness.evaluate(self.company, self.start, self.end)
		codes = {e["code"] for e in res.get("exceptions", [])
		         if e.get("employee") == self.employee.name}
		self.assertIn("EXC-013", codes,
		              "a profile with no base salary must block the run, not the ledger")
