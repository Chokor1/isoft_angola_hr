# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Audit finding F-03: HR prepares, Finance posts and pays — and a role name alone does
not decide which is which.

Every ITEC HR user also held Accounts Manager, so the role table said Finance had posted,
released and paid the July payroll while one HR user had done all of it. Two rules now
stand behind every finance endpoint, whatever roles the caller holds:

* **Capability** — ``permissions.can`` accepts a finance action only from Payroll Finance
  Approver, or from Accounts Manager held by somebody with no HR/payroll role
  (``is_payroll_finance``), while the corresponding Finance switch is on.
* **Identity** — whoever calculated or submitted a run can never post it, submit its
  slips, release it, export its bank file, record the bank's answer or pay it.

This module is the complete security matrix the audit asked for: HR Manager, Payroll
Officer, Payroll Manager, Payroll Finance Approver, Accounts Manager, an HR Manager who is
handed Accounts Manager, an unauthorised user and Administrator — through the API
functions the HTTP handlers call, through ``frappe.client``, and through direct document
actions. Nothing here touches user roles on the site: every user is a fixture.

SAFETY — the ControlFixture contract: every record is prefixed ``_TEST AHR``, registered
for deletion, and each test runs inside a savepoint.
"""

import json
from unittest import mock

import frappe
from frappe.client import set_value as client_set_value
from frappe.client import submit as client_submit
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import bank_export
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr

FINANCE_ENDPOINTS = ("make_bulk_journal_entry", "make_bulk_payment_entry", "export_bank_transfer",
                     "bank_export_preflight")


def _patch(test, obj, attr, value):
	p = mock.patch.object(obj, attr, return_value=value)
	p.start()
	test.addCleanup(p.stop)


class SegregationFixture(ControlFixture):
	USERS = dict(ControlFixture.USERS, **{
		"hrm": [perms.HR_MANAGER],
		"hrm_acc": [perms.HR_MANAGER, perms.ACCOUNTS_MANAGER],      # what ITEC HR users hold
		"po_acc": [perms.PAYROLL_OFFICER, perms.ACCOUNTS_MANAGER],
		"acc": [perms.ACCOUNTS_MANAGER],                            # Finance, ERPNext-style
		"fin_hrm": [perms.PAYROLL_FINANCE, perms.HR_MANAGER],       # explicit finance grant
	})

	# ------------------------------------------------------------------ #
	def as_user(self, key):
		frappe.set_user("Administrator" if key == "admin" else self.users[key])

	def prepared_by(self, key, tag):
		"""A run created and calculated by ``key`` through the real permission checks."""
		self.as_user(key)
		entry = self.make_entry(group="{0} {1}".format(PREFIX, tag), insert=False)
		entry.insert()                       # not ignore_permissions: proves `create`
		entry.create_salary_slips()
		entry.reload()
		frappe.set_user("Administrator")
		return entry

	def submitted_by(self, key, tag):
		entry = self.prepared_by(key, tag)
		self.as_user(key)
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def approved_run(self, prep_key, tag, approver="manager"):
		entry = self.submitted_by(prep_key, tag)
		self.as_user(approver)
		api.payroll_action(entry.name, wf.APPROVE)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def refused(self, exc_type, fn, *args, **kwargs):
		with self.assertRaises(exc_type) as cm:
			fn(*args, **kwargs)
		return str(cm.exception)

	def status(self, entry):
		return frappe.db.get_value("Isoft Payroll Entry", entry.name, "status")


# --------------------------------------------------------------------------- #
# The capability itself
# --------------------------------------------------------------------------- #
class TestFinanceCapability(SegregationFixture):

	def test_who_is_finance_for_payroll(self):
		self.assertTrue(perms.is_payroll_finance(self.users["finance"]))
		self.assertTrue(perms.is_payroll_finance(self.users["acc"]))
		self.assertTrue(perms.is_payroll_finance(self.users["fin_hrm"]), "explicit grant stands")
		self.assertFalse(perms.is_payroll_finance(self.users["hrm_acc"]), "HR with an accounting role")
		self.assertFalse(perms.is_payroll_finance(self.users["po_acc"]))
		self.assertFalse(perms.is_payroll_finance(self.users["hrm"]))
		self.assertFalse(perms.is_payroll_finance(self.users["manager"]))

	def test_the_matrix_as_enforced(self):
		expected = {
			# user:      prepare, approve, post, export, pay
			"hrm":       (True, True, False, False, False),
			"officer":   (True, False, False, False, False),
			"manager":   (False, True, False, False, False),
			"finance":   (False, False, True, True, True),
			"acc":       (False, False, True, True, True),
			"hrm_acc":   (True, True, False, False, False),
			"po_acc":    (True, False, False, False, False),
			"fin_hrm":   (True, True, True, True, True),
			"employee_only": (False, False, False, False, False),
		}
		actions = (perms.PAYROLL_PREPARE, perms.PAYROLL_APPROVE, perms.PAYROLL_POST,
		           perms.PAYROLL_EXPORT_BANK, perms.PAYROLL_CONFIRM_PAYMENT)
		for key, row in expected.items():
			got = tuple(perms.can(a, user=self.users[key]) for a in actions)
			self.assertEqual(got, row, key)
		self.assertTrue(all(perms.can(a, user="Administrator") for a in actions))

	def test_the_refusal_names_the_finance_rule(self):
		why = perms.refusal(perms.PAYROLL_POST, user=self.users["hrm_acc"])
		self.assertIn("Finance action", why)
		self.assertIn("Payroll Finance Approver", why)
		self.assertIsNone(perms.refusal(perms.PAYROLL_POST, user=self.users["acc"]))

	def test_switching_the_finance_rule_off_restores_the_role_table(self):
		_patch(self, perms, "finance_switch", False)
		self.assertTrue(perms.can(perms.PAYROLL_POST, user=self.users["hrm_acc"]))
		self.assertFalse(perms.can(perms.PAYROLL_POST, user=self.users["hrm"]),
		                 "the role table still requires a finance role")

	def test_the_switches_default_to_the_safe_side_when_absent(self):
		for field in ("allow_hr_self_approval", "payroll_posting_requires_finance",
		              "payroll_payment_requires_finance"):
			frappe.db.sql("delete from `tabSingles` where doctype='Isoft HR Settings' and field=%s", field)
		self.assertFalse(wf.hr_self_approval_allowed())
		self.assertTrue(wf.posting_requires_finance())
		self.assertTrue(wf.payment_requires_finance())
		self.assertTrue(wf.requires_separate_approval())
		self.assertTrue(perms.finance_switch(perms.PAYROLL_POST))
		self.assertTrue(perms.finance_switch(perms.PAYROLL_CONFIRM_PAYMENT))
		self.assertFalse(perms.finance_switch(perms.PAYROLL_APPROVE), "not a finance action")

	def test_the_switches_are_visible_in_settings(self):
		frappe.set_user("Administrator")
		s = api.get_settings()
		self.assertEqual(s["allow_hr_self_approval"], 0)
		self.assertEqual(s["payroll_posting_requires_finance"], 1)
		self.assertEqual(s["payroll_payment_requires_finance"], 1)
		meta = frappe.get_meta("Isoft HR Settings")
		for field in ("allow_hr_self_approval", "payroll_posting_requires_finance",
		              "payroll_payment_requires_finance"):
			self.assertTrue(meta.has_field(field), field)
			self.assertFalse(meta.get_field(field).hidden, field)

	def test_readiness_reports_whether_anybody_can_actually_post(self):
		checks, _staffing = pr.role_checks()
		check = next(c for c in checks if c["key"] == "finance_effective")
		self.assertEqual(check["status"], pr.READY, check)
		self.assertIn(self.users["acc"], check["current"])
		self.assertNotIn(self.users["hrm_acc"], check["current"])


# --------------------------------------------------------------------------- #
# HR Manager
# --------------------------------------------------------------------------- #
class TestHRManager(SegregationFixture):

	def test_prepares_calculates_and_submits(self):
		entry = self.prepared_by("hrm", "hrm-prep")
		self.assertEqual(self.status(entry), wf.CALCULATED)
		self.assertEqual(entry.prepared_by, self.users["hrm"])
		self.assertTrue(entry.employees[0].salary_slip)
		self.as_user("hrm")
		out = api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		self.assertEqual(out["status"], wf.PENDING_APPROVAL)
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", entry.name, "submitted_by"),
		                 self.users["hrm"])

	def test_cannot_approve_own_payroll_by_default(self):
		entry = self.submitted_by("hrm", "hrm-self")
		self.as_user("hrm")
		why = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.APPROVE)
		self.assertIn("cannot approve a payroll you prepared", why)
		self.assertEqual(self.status(entry), wf.PENDING_APPROVAL)
		self.assertNotIn(wf.APPROVE, wf.allowed_actions(entry, user=self.users["hrm"]))

	def test_may_approve_own_payroll_when_hr_self_approval_is_on(self):
		_patch(self, wf, "hr_self_approval_allowed", True)
		entry = self.submitted_by("hrm", "hrm-selfok")
		self.assertIn(wf.APPROVE, wf.allowed_actions(entry, user=self.users["hrm"]))
		self.as_user("hrm")
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", entry.name, "approved_by"),
		                 self.users["hrm"])

	def test_cannot_post_pay_or_export(self):
		entry = self.approved_run("hrm", "hrm-fin")
		self.as_user("hrm")
		self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
		self.refused(frappe.PermissionError, frappe.get_doc("Isoft Payroll Entry", entry.name).submit_salary_slips)
		self.refused(frappe.PermissionError, api.submit_payroll_entry, entry.name)
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		self.refused(frappe.PermissionError, api.make_bulk_payment_entry, entry.name)
		self.refused(frappe.PermissionError, api.export_bank_transfer, entry.name)
		self.refused(frappe.PermissionError, api.bank_export_preflight, entry.name)
		self.refused(frappe.PermissionError, api.make_journal_entry, entry.employees[0].salary_slip)
		self.assertEqual(self.status(entry), wf.APPROVED)
		self.assertEqual(frappe.db.count("Isoft Salary Slip", {"payroll_entry": entry.name, "docstatus": 1}), 0)

	def test_sees_only_hr_buttons(self):
		entry = self.approved_run("hrm", "hrm-ui")
		self.as_user("hrm")
		offered = set(wf.allowed_actions(entry))
		self.assertFalse(offered & {wf.POST, wf.RELEASE_FOR_PAYMENT, wf.PAY, wf.CLOSE}, offered)
		flags = frappe.get_doc("Isoft Payroll Entry", entry.name).approval_summary()["finance"]
		self.assertEqual(flags, {"can_post": False, "can_submit_slips": False, "can_pay": False,
		                         "can_export_bank": False})
		caps = api.payroll_capabilities()
		self.assertTrue(caps["prepare"] and caps["calculate"] and caps["submit_for_approval"])
		self.assertFalse(caps["post"] or caps["export_bank"] or caps["confirm_payment"])


# --------------------------------------------------------------------------- #
# HR Manager who is handed Accounts Manager — the ITEC situation
# --------------------------------------------------------------------------- #
class TestHRManagerWithAccountsManager(SegregationFixture):

	def test_cannot_post_pay_or_export_own_run(self):
		entry = self.approved_run("hrm_acc", "hacc-own")
		self.as_user("hrm_acc")
		for fn in (api.make_bulk_journal_entry, api.make_bulk_payment_entry,
		           api.export_bank_transfer, api.bank_export_preflight, api.submit_payroll_entry):
			self.refused(frappe.PermissionError, fn, entry.name)
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		self.refused(frappe.PermissionError, api.make_journal_entry, entry.employees[0].salary_slip)
		self.assertEqual(self.status(entry), wf.APPROVED)

	def test_cannot_post_somebody_elses_run_either(self):
		"""The role conflict, independent of who prepared the run."""
		entry = self.approved_run("officer", "hacc-other")
		self.as_user("hrm_acc")
		why = self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
		self.assertIn("Finance action", why)
		self.refused(frappe.PermissionError, api.export_bank_transfer, entry.name)
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		self.assertFalse(perms.can(perms.PAYROLL_POST))
		self.assertEqual(api.payroll_capabilities()["post"], False)
		self.assertEqual(api.payroll_capabilities()["export_bank"], False)

	def test_generic_apis_and_direct_document_actions_are_refused_too(self):
		entry = self.approved_run("hrm_acc", "hacc-generic")
		slip = entry.employees[0].salary_slip
		self.as_user("hrm_acc")
		# F-01 channel: the status field
		self.refused(frappe.PermissionError, client_set_value, "Isoft Payroll Entry", entry.name,
		             "status", wf.POSTED)
		# frappe.client.submit on a slip of a locked, approved run
		self.refused((frappe.ValidationError, frappe.PermissionError), client_submit,
		             json.dumps(frappe.get_doc("Isoft Salary Slip", slip).as_dict(), default=str))
		# direct document actions
		self.refused(frappe.ValidationError, frappe.get_doc("Isoft Salary Slip", slip).submit)
		self.refused(frappe.PermissionError,
		             frappe.get_doc("Isoft Payroll Entry", entry.name).submit_salary_slips)
		# the workflow service itself
		self.refused(frappe.PermissionError, wf.perform,
		             frappe.get_doc("Isoft Payroll Entry", entry.name), wf.RELEASE_FOR_PAYMENT)
		self.assertEqual(frappe.db.get_value("Isoft Salary Slip", slip, "docstatus"), 0)
		self.assertEqual(self.status(entry), wf.APPROVED)

	def test_with_the_posting_rule_off_the_accounting_role_counts_again(self):
		_patch(self, perms, "finance_switch", False)
		_patch(self, wf, "posting_requires_finance", False)
		entry = self.approved_run("officer", "hacc-off")
		self.as_user("hrm_acc")
		api.submit_payroll_entry(entry.name)
		out = api.make_bulk_journal_entry(entry.name)
		self.assertEqual(out["status"], wf.POSTED)


# --------------------------------------------------------------------------- #
# Payroll Officer and Payroll Manager
# --------------------------------------------------------------------------- #
class TestPayrollOfficerAndManager(SegregationFixture):

	def test_officer_prepares_but_neither_approves_nor_posts(self):
		entry = self.submitted_by("officer", "po")
		self.as_user("officer")
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.APPROVE)
		self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
		self.refused(frappe.PermissionError, api.export_bank_transfer, entry.name)

	def test_officer_with_accounts_manager_still_cannot_post(self):
		entry = self.approved_run("officer", "po-acc")
		self.as_user("po_acc")
		self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
		self.refused(frappe.PermissionError, api.make_bulk_payment_entry, entry.name)

	def test_manager_approves_but_cannot_post_or_pay(self):
		entry = self.submitted_by("officer", "pm")
		self.as_user("manager")
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)
		self.refused(frappe.PermissionError, api.make_bulk_journal_entry, entry.name)
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		self.refused(frappe.PermissionError, api.make_bulk_payment_entry, entry.name)
		self.refused(frappe.PermissionError, api.export_bank_transfer, entry.name)


# --------------------------------------------------------------------------- #
# Finance
# --------------------------------------------------------------------------- #
class TestFinance(SegregationFixture):

	def _finance_path(self, key, tag):
		entry = self.approved_run("officer", tag)
		self.as_user(key)
		flags = frappe.get_doc("Isoft Payroll Entry", entry.name).approval_summary()["finance"]
		self.assertTrue(flags["can_post"] and flags["can_submit_slips"], flags)
		self.assertFalse(flags["can_pay"] or flags["can_export_bank"], "not released yet")
		api.submit_payroll_entry(entry.name)
		out = api.make_bulk_journal_entry(entry.name)
		self.assertEqual(out["status"], wf.POSTED)
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", entry.name, "posted_by"), self.users[key])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		flags = frappe.get_doc("Isoft Payroll Entry", entry.name).approval_summary()["finance"]
		self.assertTrue(flags["can_pay"] and flags["can_export_bank"], flags)
		api.export_bank_transfer(entry.name)
		frappe.response.pop("filecontent", None)
		export = frappe.db.get_value("Isoft Bank Export", {"payroll_entry": entry.name}, "name")
		self.assertTrue(export)
		bank_export.record_bank_response(export, "BANK-REF-1")
		out = api.make_bulk_payment_entry(entry.name)
		self.assertEqual(out["status"], wf.PAID)
		entry.reload()
		self.assertEqual(entry.payment_authorized_by, self.users[key])
		self.assertTrue(entry.paid_at)
		return entry

	def test_finance_approver_posts_releases_exports_and_pays(self):
		entry = self._finance_path("finance", "fin-path")
		self.as_user("finance")
		self.assertEqual(api.payroll_action(entry.name, wf.CLOSE)["status"], wf.CLOSED)

	def test_accounts_manager_without_hr_roles_is_finance(self):
		self._finance_path("acc", "acc-path")

	def test_an_explicit_finance_approver_who_is_also_hr_manager_may_post_others_runs(self):
		self._finance_path("fin_hrm", "finhrm-path")

	def test_but_never_a_run_they_prepared(self):
		entry = self.approved_run("fin_hrm", "finhrm-own")
		self.as_user("fin_hrm")
		why = self.refused(frappe.ValidationError, api.make_bulk_journal_entry, entry.name)
		self.assertIn("cannot post a payroll you prepared", why)
		self.refused(frappe.ValidationError, api.submit_payroll_entry, entry.name)
		self.assertNotIn(wf.POST, wf.allowed_actions(entry))
		flags = frappe.get_doc("Isoft Payroll Entry", entry.name).approval_summary()["finance"]
		self.assertFalse(flags["can_post"] or flags["can_submit_slips"])

	def test_the_approver_still_cannot_pay(self):
		# Payroll Manager + Accounts Manager: approves, then is refused the payment by
		# identity (and by the Finance rule).
		self.as_user("admin")
		frappe.get_doc("User", self.users["manager"]).add_roles(perms.ACCOUNTS_MANAGER)
		entry = self.approved_run("officer", "appr-pay")
		self.as_user("finance")
		api.submit_payroll_entry(entry.name)
		api.make_bulk_journal_entry(entry.name)
		self.as_user("manager")
		self.refused(frappe.PermissionError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		_patch(self, perms, "finance_switch", False)
		why = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.RELEASE_FOR_PAYMENT)
		self.assertIn("payroll you approved", why)


# --------------------------------------------------------------------------- #
# Unauthorised user and Administrator
# --------------------------------------------------------------------------- #
class TestUnauthorisedAndAdministrator(SegregationFixture):

	def test_an_employee_can_do_nothing(self):
		entry = self.approved_run("officer", "emp")
		self.as_user("employee_only")
		self.refused(frappe.PermissionError, self.make_entry(group=PREFIX + " emp-new", insert=False).insert)
		for action in (wf.CALCULATE, wf.SUBMIT_FOR_APPROVAL, wf.APPROVE, wf.REJECT,
		               wf.RELEASE_FOR_PAYMENT, wf.CLOSE, wf.CANCEL):
			self.refused(frappe.PermissionError, api.payroll_action, entry.name, action)
		for fn in (api.make_bulk_journal_entry, api.make_bulk_payment_entry, api.export_bank_transfer,
		           api.bank_export_preflight, api.submit_payroll_entry, api.get_payroll_entry,
		           api.payroll_approval_summary):
			self.refused(frappe.PermissionError, fn, entry.name)
		self.refused(frappe.PermissionError, api.payroll_capabilities)
		self.assertEqual(wf.allowed_actions(entry), [])

	def test_administrator_is_bound_by_identity(self):
		entry = self.submitted_by("admin", "admin-own")
		frappe.set_user("Administrator")
		why = self.refused(frappe.ValidationError, api.payroll_action, entry.name, wf.APPROVE)
		self.assertIn("cannot approve a payroll you prepared", why)
		self.as_user("manager")
		api.payroll_action(entry.name, wf.APPROVE)
		frappe.set_user("Administrator")
		why = self.refused(frappe.ValidationError, api.make_bulk_journal_entry, entry.name)
		self.assertIn("cannot post a payroll you prepared", why)
		self.refused(frappe.ValidationError, api.submit_payroll_entry, entry.name)
		self.assertNotIn(wf.POST, wf.allowed_actions(entry, user="Administrator"))

	def test_administrator_may_post_a_run_somebody_else_prepared(self):
		"""By role Administrator is everything; only the identity rules bind it.
		(A separate test: create_salary_slips adopts an existing slip for the same
		employee and period, so two runs in one test would share one slip.)"""
		other = self.approved_run("officer", "admin-other")
		frappe.set_user("Administrator")
		self.assertIn(wf.POST, wf.allowed_actions(other, user="Administrator"))
		api.submit_payroll_entry(other.name)
		self.assertEqual(api.make_bulk_journal_entry(other.name)["status"], wf.POSTED)
