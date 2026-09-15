# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Audit findings F-01 and F-02: the payroll workflow is the only way to change lifecycle
state, and a salary slip exists only inside the payroll run that approved it.

F-01 — the state, the audit stamps and the approval snapshot of an Isoft Payroll Entry were
read-only in the form but writable through every generic channel (``frappe.client.set_value``,
``frappe.client.save`` / ``insert``, the REST resource API, a plain ``doc.save()``). One call
marked a payroll Paid, or Approved with a forged approver, with no approval and no ledger
entry. These tests drive exactly those channels, as the roles that hold *write*, and expect a
``PermissionError`` every time — Administrator included, because the guard is about the
channel, not the caller. Then they prove the workflow itself still works.

F-02 — a salary slip with no Payroll Entry had no approval to check, so it could be created
in the Desk, submitted by an HR Manager and posted and paid by Finance. A slip that merely
pointed at an approved run was equally unapproved. Now a slip cannot be created without its
run, cannot be re-pointed, and cannot be submitted, posted or paid unless the run lists it
and is approved.

SAFETY — same contract as every other phase in this folder: every record is prefixed
``_TEST AHR`` and registered for explicit deletion; each test runs inside a savepoint.
"""

import json

import frappe
from frappe.client import insert as client_insert
from frappe.client import save as client_save
from frappe.client import set_value as client_set_value
from frappe.client import submit as client_submit
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

#: The fields the audit asked to protect. LIFECYCLE_FIELDS also covers the remaining stamps
#: and the approval snapshot; this is the minimum.
REQUESTED_FIELDS = ("status", "approved_by", "approved_at", "submitted_by", "posted_by",
                    "payment_authorized_by", "paid_at", "closed_by")


class IntegrityFixture(PayrollFixture):
	ROLES = {
		"hrm": "HR Manager",
		"po": "Payroll Officer",
		"pm": "Payroll Manager",
		"fin": "Payroll Finance Approver",
		"acc": "Accounts Manager",
	}

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = {}
		for key, role in cls.ROLES.items():
			email = "{0}.p43.{1}@example.invalid".format(PREFIX.lower().replace(" ", ""), key)
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
			user = cls._track(frappe.get_doc({
				"doctype": "User", "email": email, "first_name": PREFIX + " p43 " + key,
				"send_welcome_email": 0,
			}).insert(ignore_permissions=True))
			user.add_roles(role)
			cls.users[key] = email
		frappe.set_user("Administrator")

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.run = self.payroll_run()
		frappe.db.set_value("Isoft Payroll Entry", self.run, "status", wf.CALCULATED,
		                    update_modified=False)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def as_user(self, key):
		frappe.set_user(self.users[key])

	def status(self):
		return frappe.db.get_value("Isoft Payroll Entry", self.run, "status")

	def stored(self, *fields):
		return frappe.db.get_value("Isoft Payroll Entry", self.run, list(fields), as_dict=True)


# --------------------------------------------------------------------------- #
# F-01 — lifecycle fields
# --------------------------------------------------------------------------- #
class TestLifecycleFieldsAreProtected(IntegrityFixture):

	def test_the_audited_fields_are_all_covered(self):
		for field in REQUESTED_FIELDS:
			self.assertIn(field, wf.LIFECYCLE_FIELDS, field)
		self.assertIn("approval_fingerprint", wf.LIFECYCLE_FIELDS,
		              "a forged fingerprint would defeat the posting integrity check")

	def test_hr_manager_cannot_set_status_approved(self):
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, "status", wf.APPROVED)
		self.assertEqual(self.status(), wf.CALCULATED)

	def test_hr_manager_cannot_forge_the_approver(self):
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, "approved_by", self.users["pm"])
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, {
				"status": wf.APPROVED, "approved_by": self.users["pm"],
				"approved_at": frappe.utils.now(), "approval_fingerprint": "forged",
			})
		row = self.stored("status", "approved_by", "approval_fingerprint")
		self.assertEqual(row.status, wf.CALCULATED)
		self.assertFalse(row.approved_by)
		self.assertFalse(row.approval_fingerprint)

	def test_hr_manager_cannot_mark_paid(self):
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, "status", wf.PAID)
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, "paid_at", frappe.utils.now())
		row = self.stored("status", "paid_at")
		self.assertEqual(row.status, wf.CALCULATED)
		self.assertFalse(row.paid_at)

	def test_the_rest_resource_save_is_refused_too(self):
		"""``PUT /api/resource/...`` and the Desk form both end in frappe.client.save."""
		self.as_user("hrm")
		doc = frappe.get_doc("Isoft Payroll Entry", self.run).as_dict()
		doc.update({"status": wf.APPROVED, "approved_by": self.users["pm"]})
		with self.assertRaises(frappe.PermissionError):
			client_save(json.dumps(doc, default=str))
		self.assertEqual(self.status(), wf.CALCULATED)

	def test_finance_cannot_bypass_approval(self):
		for key in ("fin", "acc"):
			self.as_user(key)
			with self.assertRaises(frappe.PermissionError):
				client_set_value("Isoft Payroll Entry", self.run, "status", wf.APPROVED)
			with self.assertRaises(frappe.PermissionError):
				client_set_value("Isoft Payroll Entry", self.run, "status", wf.PAYMENT_READY)
			self.assertEqual(self.status(), wf.CALCULATED, key)

	def test_payroll_officer_and_payroll_manager_are_refused_as_well(self):
		for key, target in (("po", wf.APPROVED), ("pm", wf.PAID)):
			self.as_user(key)
			with self.assertRaises(frappe.PermissionError):
				client_set_value("Isoft Payroll Entry", self.run, "status", target)
			self.assertEqual(self.status(), wf.CALCULATED, key)

	def test_a_new_entry_cannot_be_born_approved(self):
		self.as_user("hrm")
		base = {
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
		}
		for stamps in ({"status": wf.APPROVED},
		               {"status": wf.PAID},
		               {"approved_by": self.users["pm"]},
		               {"prepared_by": self.users["po"]},
		               {"approval_fingerprint": "forged"}):
			values = dict(base, payroll_group=PREFIX + " born " + "-".join(stamps))
			values.update(stamps)
			with self.assertRaises(frappe.PermissionError, msg=str(stamps)):
				client_insert(json.dumps(values, default=str))
		# A plain new run is still fine, and it is born Draft with its creator as preparer.
		created = client_insert(json.dumps(dict(base, payroll_group=PREFIX + " born plain"), default=str))
		self.assertEqual(created.get("status"), wf.DRAFT)
		self.assertEqual(created.get("prepared_by"), self.users["hrm"])

	def test_administrator_is_not_exempt(self):
		"""The guard is about the channel. Administrator changes state through the
		workflow like everybody else; tests use frappe.db.set_value (SQL), which is not a
		document save and is documented as such."""
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			client_set_value("Isoft Payroll Entry", self.run, "status", wf.PAID)
		entry = frappe.get_doc("Isoft Payroll Entry", self.run)
		entry.status = wf.APPROVED
		with self.assertRaises(frappe.PermissionError):
			entry.save(ignore_permissions=True)
		self.assertEqual(self.status(), wf.CALCULATED)

	def test_fields_outside_the_lifecycle_are_still_editable(self):
		self.as_user("hrm")
		client_set_value("Isoft Payroll Entry", self.run, "payroll_group", PREFIX + " renamed")
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", self.run, "payroll_group"),
		                 PREFIX + " renamed")
		self.assertEqual(self.status(), wf.CALCULATED)

	def test_the_workflow_itself_still_moves_the_state(self):
		# A run with a calculated slip: the approval gate refuses an empty payroll.
		self.make_slip(submit=False)
		frappe.db.set_value("Isoft Payroll Entry", self.run, "status", wf.CALCULATED,
		                    update_modified=False)
		entry = frappe.get_doc("Isoft Payroll Entry", self.run)
		self.as_user("hrm")
		wf.perform(entry, wf.SUBMIT_FOR_APPROVAL)
		self.assertEqual(self.status(), wf.PENDING_APPROVAL)
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", self.run, "submitted_by"),
		                 self.users["hrm"])
		self.as_user("pm")
		out = api.payroll_action(self.run, wf.APPROVE)
		self.assertEqual(out["status"], wf.APPROVED)
		row = self.stored("status", "approved_by", "approved_at", "approval_fingerprint")
		self.assertEqual(row.status, wf.APPROVED)
		self.assertEqual(row.approved_by, self.users["pm"])
		self.assertTrue(row.approved_at)
		self.assertTrue(row.approval_fingerprint)

	def test_the_transition_context_is_the_only_door(self):
		frappe.set_user("Administrator")
		entry = frappe.get_doc("Isoft Payroll Entry", self.run)
		entry.status = wf.PENDING_APPROVAL
		with self.assertRaises(frappe.PermissionError):
			entry.save(ignore_permissions=True)
		entry.reload()
		entry.status = wf.PENDING_APPROVAL
		with wf.transition():
			entry.save(ignore_permissions=True)
		self.assertEqual(self.status(), wf.PENDING_APPROVAL)
		self.assertFalse(wf.in_transition(), "the flag must not leak out of the context")

	def test_paid_is_still_reached_through_the_ledger(self):
		"""refresh_payment_state saves through the transition context: a payroll whose
		every slip carries a submitted payment entry still becomes Paid."""
		slip = self.make_slip()                       # run → Payment Ready, slip submitted
		frappe.set_user("Administrator")
		api.make_journal_entry(slip.name)
		api.make_payment_entry(slip.name)
		entry = frappe.get_doc("Isoft Payroll Entry", self.run)
		self.assertEqual(wf.refresh_payment_state(entry), wf.PAID)
		row = self.stored("status", "paid_at")
		self.assertEqual(row.status, wf.PAID)
		self.assertTrue(row.paid_at)


# --------------------------------------------------------------------------- #
# F-02 — salary slips belong to a run
# --------------------------------------------------------------------------- #
class TestSalarySlipsBelongToARun(IntegrityFixture):

	def _slip_values(self, **overrides):
		values = {
			"doctype": "Isoft Salary Slip", "employee": self.employee.name,
			"company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end,
			"salary_profile": self.profile.name,
		}
		values.update(overrides)
		return values

	def _other_run(self):
		start = get_first_day(add_months(getdate(self.start), -1))
		return self.payroll_run(start, get_last_day(start))

	def test_hr_manager_cannot_create_a_slip_outside_a_run(self):
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(self._slip_values()).insert()
		with self.assertRaises(frappe.PermissionError):
			client_insert(json.dumps(self._slip_values(), default=str))
		self.assertFalse(frappe.db.exists("Isoft Salary Slip", {
			"employee": self.employee.name, "start_date": self.start, "payroll_entry": ["in", ["", None]]}))

	def test_a_slip_cannot_point_at_a_run_of_another_period(self):
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError) as cm:
			frappe.get_doc(self._slip_values(payroll_entry=self._other_run())).insert()
		self.assertIn("does not match", str(cm.exception))

	def test_a_slip_cannot_point_at_a_run_that_does_not_exist(self):
		"""Frappe's own link validation refuses this first; the guard is the backstop."""
		self.as_user("hrm")
		with self.assertRaises((frappe.PermissionError, frappe.LinkValidationError)):
			frappe.get_doc(self._slip_values(payroll_entry="IPE-DOES-NOT-EXIST")).insert()
		self.assertFalse(frappe.db.exists("Isoft Salary Slip", {"payroll_entry": "IPE-DOES-NOT-EXIST"}))

	def test_the_run_link_cannot_be_changed_after_creation(self):
		slip = self.make_slip(submit=False)
		other = self._other_run()
		frappe.db.set_value("Isoft Payroll Entry", other, {
			"start_date": self.start, "end_date": self.end}, update_modified=False)
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError) as cm:
			client_set_value("Isoft Salary Slip", slip.name, "payroll_entry", other)
		self.assertIn("cannot be moved", str(cm.exception))
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip.name, "payroll_entry"), self.run)

	def test_a_slip_of_an_unapproved_run_cannot_be_submitted(self):
		slip = self.make_slip(submit=False)
		for key in ("hrm", "fin"):
			self.as_user(key)
			with self.assertRaises(frappe.ValidationError) as cm:
				with wf.unlocked():
					frappe.get_doc("Isoft Salary Slip", slip.name).submit()
			self.assertIn("has not been approved", str(cm.exception))
			self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip.name, "docstatus"), 0)

	def test_finance_cannot_submit_the_runs_slips_before_approval(self):
		self.make_slip(submit=False)
		self.as_user("fin")
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.get_doc("Isoft Payroll Entry", self.run).submit_salary_slips()
		self.assertIn("has not been approved", str(cm.exception))

	def test_a_slip_the_run_does_not_list_cannot_be_submitted(self):
		"""Pointing at an approved run is not being approved."""
		slip = self.make_slip(submit=False)
		frappe.db.sql("delete from `tabIsoft Payroll Employee` where parent=%s and salary_slip=%s",
		              (self.run, slip.name))
		self.approve_run(self.run)
		self.as_user("fin")
		with self.assertRaises(frappe.PermissionError) as cm:
			with wf.unlocked():
				frappe.get_doc("Isoft Salary Slip", slip.name).submit()
		self.assertIn("not listed", str(cm.exception))
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip.name, "docstatus"), 0)

	def test_an_unlisted_slip_cannot_be_posted_or_paid(self):
		slip = self.make_slip()                       # listed, approved, submitted
		frappe.db.sql("delete from `tabIsoft Payroll Employee` where parent=%s and salary_slip=%s",
		              (self.run, slip.name))
		self.as_user("fin")
		with self.assertRaises(frappe.PermissionError):
			api.make_journal_entry(slip.name)
		with self.assertRaises(frappe.PermissionError):
			api.make_payment_entry(slip.name)
		self.assertFalse(frappe.db.get_value("Isoft Salary Slip", slip.name, "journal_entry"))

	def test_a_legacy_standalone_slip_cannot_be_submitted_posted_or_paid(self):
		"""A slip that predates F-02 and carries no run (only SQL can produce one now)."""
		draft = self.make_slip(submit=False)
		frappe.db.set_value("Isoft Salary Slip", draft.name, "payroll_entry", None,
		                    update_modified=False)
		self.as_user("hrm")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Isoft Salary Slip", draft.name).submit()
		frappe.set_user("Administrator")
		submitted = self.make_slip(start_date=get_first_day(add_months(getdate(self.start), -1)),
		                           end_date=get_last_day(add_months(getdate(self.start), -1)),
		                           posting_date=get_last_day(add_months(getdate(self.start), -1)))
		frappe.db.set_value("Isoft Salary Slip", submitted.name, "payroll_entry", None,
		                    update_modified=False)
		self.as_user("fin")
		with self.assertRaises(frappe.PermissionError):
			api.make_journal_entry(submitted.name)
		with self.assertRaises(frappe.PermissionError):
			api.make_payment_entry(submitted.name)

	def test_client_submit_of_a_run_slip_needs_the_approved_run(self):
		slip = self.make_slip(submit=False)
		self.as_user("hrm")
		with self.assertRaises(frappe.ValidationError):
			client_submit(json.dumps(frappe.get_doc("Isoft Salary Slip", slip.name).as_dict(),
			                         default=str))
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip.name, "docstatus"), 0)

	def test_the_sanctioned_path_still_works_end_to_end(self):
		slip = self.make_slip()
		self.assertEqual(slip.docstatus, 1)
		self.assertEqual(slip.payroll_entry, self.run)
		self.assertTrue(frappe.db.exists("Isoft Payroll Employee",
		                                 {"parent": self.run, "salary_slip": slip.name}))
		self.as_user("fin")
		je = api.make_journal_entry(slip.name)
		pe = api.make_payment_entry(slip.name)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Journal Entry", pe, "docstatus"), 1)
		self.assertEqual(api._slip_status(1, je, pe), "Paid")
		self.assertAlmostEqual(flt(frappe.db.get_value("Journal Entry", pe, "total_credit")),
		                       flt(slip.net_pay), places=2)
