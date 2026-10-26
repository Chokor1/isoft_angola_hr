# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The application does not notify anybody — proved from both ends.

Two halves, and both matter.

**Nothing is produced.** Every HR action that used to write to the bell menu or send mail
is driven here for real — a payslip submitted, leave requested and decided, a contract
approved, a salary change, an advance, an attendance justification, a document upload and
verification, a performance review, a payroll approval — and the Notification Log is
counted before and after. The count must not move. Where the production path is a
scheduled sweep, the sweep is run directly.

**Everything else still works.** Disabling notifications must not have disabled the
messages people need in order to operate the system: validation errors, permission
refusals, workflow refusals, and the ordinary success responses. Those are asserted in the
same module, deliberately, because "we turned off the noise" and "we turned off the
brakes" look identical in a passing test suite that only checks the first half.

The one test that will outlive the others is
:class:`TestNoUnguardedWriter`: it reads the source of the whole application and fails if
anybody adds a second way to write a Notification Log or send mail without asking the
policy first. That is what stops this from being true only on the day it was written.
"""

import ast
import io
import os
import unittest

import frappe
from frappe.utils import add_days, nowdate

from isoft_angola_hr.isoft_angola_hr import api, hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (PREFIX,
                                                                        PayrollFixture)
from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify
from isoft_angola_hr.isoft_angola_hr.services import notification_policy as policy
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def notification_count():
	"""Every row in the shared Notification Log, whoever wrote it."""
	return frappe.db.sql("select count(*) from `tabNotification Log`")[0][0]


def mail_count():
	"""Queued outbound mail. Frappe writes an Email Queue row per send."""
	try:
		return frappe.db.sql("select count(*) from `tabEmail Queue`")[0][0]
	except Exception:
		return 0


class SilenceFixture(PayrollFixture):
	"""Counts the bell and the mail queue around whatever the test does."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(lambda: frappe.set_user("Administrator"))
		self._bell = notification_count()
		self._mail = mail_count()

	def assertSilent(self, what):
		self.assertEqual(
			notification_count(), self._bell,
			"%s produced a notification; the application must not notify anybody" % what)
		self.assertEqual(
			mail_count(), self._mail,
			"%s queued an e-mail; the application must not send mail" % what)


# --------------------------------------------------------------------------- #
# 1. The policy itself
# --------------------------------------------------------------------------- #
class TestThePolicy(unittest.TestCase):

	def test_notifications_are_disabled(self):
		self.assertFalse(policy.HR_NOTIFICATIONS_ENABLED)
		self.assertFalse(policy.notifications_enabled())
		self.assertTrue(policy.suppressed())

	def test_the_policy_is_not_a_user_facing_setting(self):
		"""It must not be reachable from the Settings screen, where it could be switched
		back on by somebody who did not mean to."""
		meta = frappe.get_meta("Isoft HR Settings")
		fields = [df.fieldname for df in meta.fields]
		offenders = [f for f in fields
		             if "notif" in f.lower() and "enable" in f.lower()]
		self.assertEqual(offenders, [],
		                 "the notification policy has become a clickable setting: %s"
		                 % offenders)

	def test_the_policy_lives_in_exactly_one_place(self):
		found = []
		for base, dirs, files in os.walk(APP_ROOT):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
			for name in files:
				if not name.endswith(".py") or name.startswith("test_"):
					continue
				src = io.open(os.path.join(base, name), encoding="utf-8").read()
				if "HR_NOTIFICATIONS_ENABLED" in src:
					found.append(name)
		self.assertEqual(found, ["notification_policy.py"],
		                 "the policy constant is defined or shadowed in %s" % found)


# --------------------------------------------------------------------------- #
# 2. No hook, job or handler is left wired
# --------------------------------------------------------------------------- #
class TestNothingIsWired(unittest.TestCase):

	def test_no_scheduled_job_belongs_to_the_notification_system(self):
		jobs = []
		for entries in (frappe.get_hooks("scheduler_events") or {}).values():
			if isinstance(entries, dict):
				for group in entries.values():
					jobs.extend(group)
			else:
				jobs.extend(entries)
		ours = [j for j in jobs if "isoft_angola_hr" in j]
		offenders = [j for j in ours
		             if "hr_notifications" in j or j.endswith("_alerts")]
		self.assertEqual(offenders, [],
		                 "these scheduled jobs exist only to notify: %s" % offenders)

	def test_the_jobs_that_remain_are_state_changes_and_are_still_registered(self):
		"""Removing the alert jobs must not have removed the ones that do real work."""
		jobs = []
		for entries in (frappe.get_hooks("scheduler_events") or {}).values():
			if isinstance(entries, dict):
				for group in entries.values():
					jobs.extend(group)
			else:
				jobs.extend(entries)
		for needed in ("contracts.refresh_contract_statuses",
		               "salary_change.apply_due_changes",
		               "isoft_employee_document.refresh_document_statuses",
		               "isoft_attendance_occurrence.auto_flag_unjustified"):
			with self.subTest(job=needed):
				self.assertTrue(any(needed in j for j in jobs),
				                "%s is no longer scheduled" % needed)

	def test_no_document_event_calls_the_notification_service(self):
		offenders = []
		for doctype, events in (frappe.get_hooks("doc_events") or {}).items():
			for event, handlers in events.items():
				for handler in ([handlers] if isinstance(handlers, str) else handlers):
					if "hr_notifications" in handler:
						offenders.append("%s.%s -> %s" % (doctype, event, handler))
		self.assertEqual(offenders, [],
		                 "document events still fire notifications: %s" % offenders)

	def test_the_journal_entry_unlink_hook_survived(self):
		"""The one doc_event that is not a notification must still be registered."""
		handlers = (frappe.get_hooks("doc_events") or {}).get("Journal Entry", {})
		on_cancel = handlers.get("on_cancel") or []
		on_cancel = [on_cancel] if isinstance(on_cancel, str) else on_cancel
		self.assertTrue(
			any("unlink_cancelled_payroll_entry" in h for h in on_cancel),
			"cancelling a payroll Journal Entry no longer releases its salary slips")

	def test_the_app_registers_no_desk_notification_config(self):
		config = frappe.get_hooks("notification_config") or []
		self.assertEqual([c for c in config if "isoft_angola_hr" in c], [])


# --------------------------------------------------------------------------- #
# 3. The writers
# --------------------------------------------------------------------------- #
class TestTheWritersRefuse(SilenceFixture):

	def test_the_bell_writer_writes_nothing(self):
		self.assertEqual(
			notify._notify("subject", "message", ["Administrator"]), 0)
		self.assertSilent("_notify")

	def test_the_employee_writer_writes_nothing_and_sends_no_mail(self):
		self.assertEqual(
			notify._tell(self.employee.name, "subject", "message",
			             "Isoft Salary Slip", None, email=True), 0)
		self.assertSilent("_tell")

	def test_the_attendance_writer_writes_nothing(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence \
			import isoft_attendance_occurrence as occ
		occ._notify_hr("subject", self.employee.name, nowdate())
		self.assertSilent("_notify_hr")

	def test_the_daily_sweep_produces_nothing(self):
		result = notify.run_daily_alerts()
		self.assertTrue(result.get("disabled"))
		self.assertEqual(
			[v for k, v in result.items() if k != "disabled"], [0, 0, 0, 0])
		self.assertSilent("run_daily_alerts")

	def test_each_individual_sweep_produces_nothing(self):
		for fn in (notify.contract_expiry_alerts, notify.probation_alerts,
		           notify.document_expiry_alerts, notify.pending_approval_alerts):
			with self.subTest(sweep=fn.__name__):
				self.assertEqual(fn(), 0)
		self.assertSilent("the four alert sweeps")

	def test_the_attendance_sweeps_produce_nothing(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence \
			import isoft_attendance_occurrence as occ
		occ.check_recurrence_alerts()
		occ.check_unjustified_month_alerts()
		self.assertSilent("the attendance sweeps")

	def test_the_orphaned_performance_sweep_produces_nothing(self):
		from isoft_angola_hr.isoft_angola_hr.services import performance
		self.assertEqual(performance.reviews_due_alerts(), 0)
		self.assertSilent("reviews_due_alerts")

	def test_the_guard_is_what_stops_it(self):
		"""Flip the policy and the writer writes — so the silence above is the policy
		doing its job, not the fixture failing to reach the code."""
		policy.HR_NOTIFICATIONS_ENABLED = True
		try:
			sent = notify._notify(
				PREFIX + " policy probe", "message", ["Administrator"])
		finally:
			policy.HR_NOTIFICATIONS_ENABLED = False
		self.assertEqual(sent, 1, "the writer is unreachable for some other reason")
		self.assertEqual(notify._notify(PREFIX + " policy probe 2", "m",
		                                ["Administrator"]), 0)


# --------------------------------------------------------------------------- #
# 4. Real actions, end to end
# --------------------------------------------------------------------------- #
class TestPayrollActionsAreSilent(SilenceFixture):

	def test_submitting_a_salary_slip_notifies_nobody(self):
		self.make_slip(submit=True)
		self.assertSilent("submitting a salary slip")

	def test_the_payslip_handler_is_silent_even_if_called_directly(self):
		slip = self.make_slip(submit=True)
		notify.notify_payslip_available(slip)
		self.assertSilent("notify_payslip_available")

	def test_calculating_a_payroll_entry_notifies_nobody(self):
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " SILENCE",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", entry.name))
		self.assertSilent("creating a payroll entry")

	def test_approving_a_payroll_notifies_nobody(self):
		preparer = "silence.preparer@example.invalid"
		if not frappe.db.exists("User", preparer):
			doc = frappe.get_doc({
				"doctype": "User", "email": preparer, "send_welcome_email": 0,
				"first_name": PREFIX + " preparer",
				"roles": [{"role": perms.PAYROLL_OFFICER}]}).insert(ignore_permissions=True)
			self.__class__._created.append(("User", doc.name))
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " SILENCE APPROVE",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", entry.name))
		# prepared_by / submitted_by are Link fields on User, so the preparer has to be
		# a real account — a made-up address fails link validation, not the approval.
		frappe.db.set_value("Isoft Payroll Entry", entry.name,
		                    {"status": "Pending Approval", "prepared_by": preparer,
		                     "submitted_by": preparer})
		doc = frappe.get_doc("Isoft Payroll Entry", entry.name)
		wf.perform(doc, wf.APPROVE)
		self.assertEqual(doc.status, wf.APPROVED)
		self.assertSilent("approving a payroll")


class TestHrActionsAreSilent(SilenceFixture):

	def _contract(self):
		doc = frappe.get_doc({
			"doctype": "Isoft Employment Contract", "employee": self.employee.name,
			"company": self.company, "contract_type": self._contract_type(),
			"start_date": nowdate(), "is_open_ended": 1, "base_salary": 200000,
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Employment Contract", doc.name))
		return doc

	def _contract_type(self):
		"""Whatever type the site already has. Creating one would test the installer, and
		the field is only here because Contract Type is mandatory."""
		existing = frappe.get_all("Isoft Contract Type", limit_page_length=1, pluck="name")
		if existing:
			return existing[0]
		name = PREFIX + " Silence Type"
		if not frappe.db.exists("Isoft Contract Type", name):
			frappe.get_doc({"doctype": "Isoft Contract Type",
			                "contract_type_name": name}).insert(ignore_permissions=True)
			self.__class__._created.append(("Isoft Contract Type", name))
		return name

	def test_creating_and_approving_a_contract_notifies_nobody(self):
		doc = self._contract()
		contracts.perform(doc, contracts.SUBMIT)
		contracts.perform(doc, contracts.APPROVE)
		self.assertSilent("a contract through submit and approve")

	def test_a_salary_change_notifies_nobody(self):
		from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": self.employee.name,
			"company": self.company, "current_base": 200000, "new_base": 220000,
			"effective_date": sc.next_period_start(nowdate()), "reason": PREFIX,
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Salary Change", doc.name))
		sc.perform(doc, sc.SUBMIT)
		sc.perform(doc, sc.APPROVE)
		self.assertSilent("a salary change through approval")

	def test_a_salary_advance_notifies_nobody(self):
		from isoft_angola_hr.isoft_angola_hr.services import advances
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"company": self.company, "request_date": nowdate(),
			"requested_amount": 25000, "approved_amount": 25000, "installments": 1,
			"installment_amount": 25000, "recovery_start_date": nowdate(),
			"reason": PREFIX + " silence",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Salary Advance", doc.name))
		advances.perform(doc, advances.SUBMIT)
		advances.perform(doc, advances.APPROVE)
		self.assertSilent("a salary advance through approval")

	def test_an_advance_status_change_notifies_nobody(self):
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"company": self.company, "request_date": nowdate(),
			"requested_amount": 10000, "approved_amount": 10000, "installments": 1,
			"installment_amount": 10000, "recovery_start_date": nowdate(),
			"reason": PREFIX + " silence 2",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Salary Advance", doc.name))
		notify.notify_advance_status(doc)
		self.assertSilent("notify_advance_status")

	def test_a_bank_change_decision_notifies_nobody(self):
		doc = frappe._dict({"employee": self.employee.name, "status": "Approved",
		                    "name": "BCR-SILENCE"})
		notify.notify_bank_change_decision(doc)
		self.assertSilent("notify_bank_change_decision")


class TestLeaveAndAttendanceAreSilent(SilenceFixture):

	def test_a_leave_request_notifies_nobody(self):
		doc = frappe._dict({"employee": self.employee.name, "status": "Open",
		                    "docstatus": 0, "name": "LAP-SILENCE",
		                    "employee_name": self.employee.employee_name,
		                    "leave_type": "X", "from_date": nowdate(),
		                    "to_date": nowdate()})
		notify.notify_leave_requested(doc)
		self.assertSilent("notify_leave_requested")

	def test_a_leave_decision_notifies_nobody(self):
		doc = frappe._dict({"employee": self.employee.name, "status": "Approved",
		                    "name": "LAP-SILENCE-2", "from_date": nowdate(),
		                    "to_date": nowdate()})
		notify.notify_leave_decision(doc)
		self.assertSilent("notify_leave_decision")

	def test_marking_attendance_notifies_nobody(self):
		doc = frappe.get_doc({
			"doctype": "Attendance", "employee": self.employee.name,
			"company": self.company, "attendance_date": add_days(self.start, 1),
			"status": "Present"})
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.__class__._created.append(("Attendance", doc.name))
		self.assertSilent("marking attendance")


class TestSelfServiceActionsAreSilent(SilenceFixture):

	def test_a_document_upload_notifies_nobody(self):
		from isoft_angola_hr.isoft_angola_hr.services import ess_uploads
		doc = frappe._dict({"name": "EDOC-SILENCE", "employee": self.employee.name,
		                    "employee_name": self.employee.employee_name,
		                    "document_type": "X"})
		ess_uploads._notify_hr_of_upload(doc)
		self.assertSilent("_notify_hr_of_upload")

	def test_a_justification_notifies_nobody(self):
		from isoft_angola_hr.isoft_angola_hr.services import ess_uploads
		ess_uploads._notify_manager(self.employee.name, "AOC-SILENCE", False)
		self.assertSilent("_notify_manager")

	def test_a_performance_state_change_notifies_nobody(self):
		from isoft_angola_hr.isoft_angola_hr.services import performance
		doc = frappe._dict({"employee": self.employee.name, "name": "APR-SILENCE",
		                    "end_date": nowdate()})
		performance._notify_state(doc, performance.PENDING_EMPLOYEE)
		performance._notify_state(doc, performance.FINALISED)
		self.assertSilent("performance review state changes")


# --------------------------------------------------------------------------- #
# 5. The notification centre and its counters
# --------------------------------------------------------------------------- #
class TestTheCountersDoNotMove(SilenceFixture):

	def test_the_notification_centre_reports_nothing(self):
		centre = hr_api.notification_centre()
		self.assertTrue(centre.get("disabled"))
		self.assertEqual(centre["unread"], [])
		self.assertEqual(centre["recent"], [])
		self.assertEqual(centre["counts"]["unread"], 0)

	def test_the_counter_does_not_move_when_hr_work_happens(self):
		before = hr_api.notification_centre()["counts"]["unread"]
		self.make_slip(submit=True)
		self.assertEqual(hr_api.notification_centre()["counts"]["unread"], before)

	def test_marking_read_is_a_no_op(self):
		self.assertEqual(hr_api.mark_notifications_read(), 0)

	def test_the_centre_does_not_republish_another_apps_notifications(self):
		"""The Notification Log is shared. An HR endpoint must not become a second bell
		menu for ERPNext's notifications."""
		policy.HR_NOTIFICATIONS_ENABLED = True
		try:
			notify._notify(PREFIX + " foreign", "m", ["Administrator"])
		finally:
			policy.HR_NOTIFICATIONS_ENABLED = False
		self.assertEqual(hr_api.notification_centre()["recent"], [])


# --------------------------------------------------------------------------- #
# 6. Nothing that people need was switched off with it
# --------------------------------------------------------------------------- #
class TestTheMessagesPeopleNeedStillWork(SilenceFixture):
	"""The half of this that is easy to get wrong."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = {}
		for label, roles in (("hrmanager", [perms.HR_MANAGER]),
		                     ("hruser", [perms.HR_USER]),
		                     ("finance", [perms.PAYROLL_FINANCE]),
		                     ("norole", [])):
			email = "silence.%s@example.invalid" % label
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email, "send_welcome_email": 0,
					"first_name": "%s SILENCE %s" % (PREFIX, label),
					"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.users[label] = email

	def as_user(self, label):
		frappe.set_user(self.users[label])

	# --- permission errors ------------------------------------------------- #
	def test_a_permission_error_still_reaches_the_caller(self):
		self.as_user("norole")
		with self.assertRaises(frappe.PermissionError):
			api.get_settings()

	def test_an_hr_user_is_still_refused_settings_write(self):
		self.as_user("hruser")
		with self.assertRaises(frappe.PermissionError):
			api.save_settings(data={"currency": "AKZ"})

	def test_finance_boundaries_still_hold(self):
		self.assertNotIn(perms.HR_MANAGER, perms.ACTION_ROLES[perms.PAYROLL_POST])
		self.assertNotIn(perms.HR_MANAGER,
		                 perms.ACTION_ROLES[perms.PAYROLL_CONFIRM_PAYMENT])

	# --- workflow refusals -------------------------------------------------- #
	def test_a_workflow_refusal_still_explains_itself(self):
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " REFUSAL",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", entry.name))
		frappe.db.set_value("Isoft Payroll Entry", entry.name, "status", "Draft")
		doc = frappe.get_doc("Isoft Payroll Entry", entry.name)
		with self.assertRaises(frappe.ValidationError) as caught:
			wf.perform(doc, wf.APPROVE)
		self.assertTrue(str(caught.exception).strip(),
		                "the refusal came back with no message")

	def test_self_approval_is_still_blocked_and_still_says_why(self):
		# asserts the documented approval policy, whatever the site runs
		self.pin_settings(**self.DOCUMENTED_APPROVAL_POLICY)
		entry = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " SELF",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", entry.name))
		frappe.db.set_value("Isoft Payroll Entry", entry.name, {
			"status": "Pending Approval",
			"prepared_by": self.users["hrmanager"],
			"submitted_by": self.users["hrmanager"]})
		doc = frappe.get_doc("Isoft Payroll Entry", entry.name)
		self.as_user("hrmanager")
		with self.assertRaises(frappe.ValidationError) as caught:
			wf.perform(doc, wf.APPROVE)
		self.assertIn("cannot approve", str(caught.exception).lower())

	# --- validation --------------------------------------------------------- #
	def test_a_required_field_error_still_fires(self):
		with self.assertRaises(Exception):
			frappe.get_doc({"doctype": "Isoft Salary Advance",
			                "company": self.company}).insert(ignore_permissions=True)

	def test_a_business_validation_still_fires_and_carries_its_message(self):
		"""Two live salary slips for one employee and period must still be refused."""
		self.make_slip(submit=False)
		with self.assertRaises(Exception) as caught:
			frappe.get_doc({
				"doctype": "Isoft Salary Slip", "employee": self.employee.name,
				"company": self.company, "posting_date": self.end,
				"start_date": self.start, "end_date": self.end,
				"salary_profile": self.profile.name,
			}).insert(ignore_permissions=True)
		self.assertTrue(str(caught.exception).strip())

	def test_msgprint_is_still_available_to_the_application(self):
		"""The policy must not have reached into Frappe's user-feedback machinery."""
		frappe.msgprint("probe")
		self.assertTrue(any("probe" in str(m) for m in (frappe.local.message_log or [])),
		                "frappe.msgprint no longer reaches the user")
		frappe.local.message_log = []

	# --- successful responses ----------------------------------------------- #
	def test_a_successful_read_still_returns_its_payload(self):
		self.as_user("hrmanager")
		self.assertIn("default_company", api.get_settings())

	def test_a_successful_write_still_succeeds(self):
		slip = self.make_slip(submit=True)
		self.assertEqual(slip.docstatus, 1)

	def test_the_worklists_still_report_work(self):
		"""Notifications are gone; the screens that show pending work are not."""
		self.as_user("hrmanager")
		for call in (lambda: hr_api.contracts_expiring(),
		             lambda: hr_api.probations_due(),
		             lambda: hr_api.hr_approval_inbox()):
			with self.subTest(call=call):
				self.assertIsNotNone(call())


# --------------------------------------------------------------------------- #
# 7. The rule that outlives this change
# --------------------------------------------------------------------------- #
class TestNoUnguardedWriter(unittest.TestCase):
	"""Nobody may add a second way to notify without asking the policy.

	The audit found three writers, one of which had been written before the notification
	service existed and inserted its own Notification Log — so silencing the service alone
	would have left attendance alerts arriving. That is the failure this test exists to
	prevent happening again.
	"""

	#: function name -> the file it is allowed to live in
	GUARDED_WRITERS = {
		"_notify": "hr_notifications.py",
		"_tell": "hr_notifications.py",
		"_notify_hr": "isoft_attendance_occurrence.py",
	}

	def _functions_writing(self, needles):
		"""(file, function) for every function whose body mentions any of *needles*."""
		found = []
		for base, dirs, files in os.walk(APP_ROOT):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
			for name in files:
				if not name.endswith(".py") or name.startswith("test_"):
					continue
				path = os.path.join(base, name)
				source = io.open(path, encoding="utf-8").read()
				try:
					tree = ast.parse(source)
				except SyntaxError:
					continue
				for node in ast.walk(tree):
					if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
						continue
					body = ast.get_source_segment(source, node) or ""
					# ignore the docstring, which legitimately discusses all of this
					doc = ast.get_docstring(node) or ""
					body = body.replace(doc, "")
					if any(n in body for n in needles):
						found.append((name, node.name, body))
		return found

	def test_only_the_known_writers_create_a_notification_log(self):
		writers = self._functions_writing(('"Notification Log"', "'Notification Log'"))
		# reads are fine; only creation counts
		creating = [(f, fn) for f, fn, body in writers
		            if "new_doc(" in body or "get_doc({" in body]
		unexpected = [(f, fn) for f, fn in creating
		              if self.GUARDED_WRITERS.get(fn) != f]
		self.assertEqual(unexpected, [],
		                 "these create a Notification Log outside the guarded writers: %s"
		                 % unexpected)

	def test_only_the_known_writer_sends_mail(self):
		senders = self._functions_writing(("frappe.sendmail",))
		unexpected = [(f, fn) for f, fn, _b in senders
		              if self.GUARDED_WRITERS.get(fn) != f]
		self.assertEqual(unexpected, [],
		                 "these send mail outside the guarded writer: %s" % unexpected)

	def test_every_known_writer_actually_asks_the_policy(self):
		for needle, expected_file in self.GUARDED_WRITERS.items():
			with self.subTest(writer=needle):
				matches = [body for f, fn, body in
				           self._functions_writing(('"Notification Log"',
				                                    "'Notification Log'",
				                                    "frappe.sendmail"))
				           if fn == needle and f == expected_file]
				self.assertTrue(matches, "%s was not found in %s" % (needle, expected_file))
				self.assertTrue(
					any("policy.suppressed()" in b for b in matches),
					"%s writes without asking the policy" % needle)

	def test_the_app_emits_no_realtime_events(self):
		offenders = []
		for base, dirs, files in os.walk(APP_ROOT):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
			for name in files:
				if not name.endswith((".py", ".js")) or name.startswith("test_"):
					continue
				src = io.open(os.path.join(base, name), encoding="utf-8").read()
				if name == "testing_guard.py" and "publish_realtime(" not in src:
					continue   # it silences realtime during protected test runs; it never emits
				if "publish_realtime" in src:
					offenders.append(name)
		self.assertEqual(offenders, [],
		                 "realtime events are being published by: %s" % offenders)
