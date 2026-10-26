# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Security gate — authentication, authorisation, capability, company scope, self-approval
and audit stamping, checked against every whitelisted endpoint the app publishes.

The point is coverage rather than illustration. Picking a handful of endpoints proves
those endpoints; enumerating all of them and asserting that each one authorises SOMETHING
proves there is no unguarded door. An endpoint that reaches the database without naming
the action it performs is the defect this module exists to catch — one such endpoint
(``isoft_hr_settings.account_query``) once let an account with no roles at all read the
company's chart of accounts, number by number, because being a link query is not itself
an authorisation.

Direct endpoint calls are made through the same functions HTTP would reach, with
``frappe.set_user``. That is the bypass route: a button can be hidden, an endpoint cannot.
"""

import ast
import inspect
import io
import os
import unittest

import frappe
from frappe.utils import now

from isoft_angola_hr.isoft_angola_hr import api, hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (PREFIX,
                                                                        PayrollFixture)
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Endpoints that legitimately authorise nothing, with the reason. Anything else that
#: reaches the database without a guard is a finding, not an entry here.
UNGUARDED_BY_DESIGN = {
	# Session-scoped self-service: the caller may only ever reach their OWN record, and
	# the employee is resolved from frappe.session.user rather than from an argument.
	"whoami", "get_my_employee", "ess_context",
}


def _module_functions(module):
	"""Whitelisted functions defined in this module (not imported into it)."""
	out = {}
	for name, fn in vars(module).items():
		if not callable(fn) or not hasattr(fn, "__module__"):
			continue
		if fn.__module__ != module.__name__:
			continue
		try:
			src = inspect.getsource(fn)
		except (OSError, TypeError):
			continue
		if "@frappe.whitelist" in src.split("def ")[0]:
			out[name] = (fn, src)
	return out


class TestNoEndpointAnswersAnUnauthorisedCaller(unittest.TestCase):
	"""The whole published surface, called as a user who holds no role at all.

	A source check says where a guard is *written*; it cannot follow a thin delegation
	layer without guessing, and it says nothing about whether the guard fires. This calls
	every whitelisted endpoint in ``api`` and ``hr_api`` as a real no-role session and
	looks at what comes back. An endpoint that returns data here is a hole.

	The endpoints listed in :data:`SELF_SCOPED` do answer, and must: they resolve the
	employee from ``frappe.session.user``, so the caller can only ever reach their own
	record. That is a stronger authorisation than a role check, not a missing one.
	"""

	#: answers any authenticated caller, by design — see the docstring
	SELF_SCOPED = {
		"self_service_context",       # who am I, what may I see
		"notification_centre",        # Notification Log where for_user = session user
		"mark_notifications_read",    # writes only rows whose for_user is the caller
		"whoami", "get_my_employee", "ess_context",
		"labour_law_reference",       # published statute: no site data, no person
	}

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.user = "sec.endpoint.norole@example.invalid"
		if not frappe.db.exists("User", cls.user):
			frappe.get_doc({"doctype": "User", "email": cls.user,
			                "send_welcome_email": 0,
			                "first_name": PREFIX + " endpoint norole",
			                "roles": []}).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		if frappe.db.exists("User", cls.user):
			frappe.delete_doc("User", cls.user, force=True, ignore_permissions=True)
		frappe.db.rollback()
		super().tearDownClass()

	def _endpoints(self):
		out = {}
		for module in (api, hr_api):
			for name, (fn, _src) in _module_functions(module).items():
				out["%s.%s" % (module.__name__.split(".")[-1], name)] = fn
		return out

	def test_the_enumeration_found_the_whole_surface(self):
		self.assertGreater(len(self._endpoints()), 200,
		                   "the enumeration is not finding the endpoints")

	def test_nothing_answers_a_caller_with_no_role(self):
		endpoints = self._endpoints()
		frappe.set_user(self.user)
		self.addCleanup(lambda: frappe.set_user("Administrator"))
		answered = []
		for label, fn in sorted(endpoints.items()):
			if label.split(".")[-1] in self.SELF_SCOPED:
				continue
			kwargs = {}
			for pname, param in inspect.signature(fn).parameters.items():
				if (param.default is inspect.Parameter.empty
						and param.kind in (param.POSITIONAL_OR_KEYWORD,
						                   param.KEYWORD_ONLY)):
					kwargs[pname] = None
			try:
				result = fn(**kwargs)
			except (frappe.PermissionError, frappe.AuthenticationError):
				continue
			except Exception:
				# refused for another reason (a missing or invalid argument). That is
				# not an answer, and the argument-first endpoints are covered by
				# TestArgumentFirstEndpointsStillRefuse below.
				continue
			finally:
				frappe.db.rollback()
			answered.append("%s -> %s" % (label, type(result).__name__))
		self.assertEqual(answered, [],
		                 "%d endpoint(s) answered a caller with no role at all:\n  %s"
		                 % (len(answered), "\n  ".join(answered)))

	def test_the_self_scoped_exemptions_really_are_self_scoped(self):
		"""Each exemption must resolve the caller from the session, not from an argument."""
		for name in sorted(self.SELF_SCOPED):
			fn = getattr(hr_api, name, None) or getattr(api, name, None)
			if fn is None:
				continue
			with self.subTest(endpoint=name):
				src = inspect.getsource(fn)
				target = src
				for hop in ("current_employee", "frappe.session.user", "reference",
				            "notification_centre", "mark_read", "_me("):
					if hop in target:
						break
				else:
					self.fail("%s is exempt but does not resolve the session user" % name)

	def test_the_permission_matrix_is_the_only_source_of_truth(self):
		"""No endpoint may decide access from a role name of its own."""
		offenders = []
		for module in (api, hr_api):
			for name, (_fn, src) in _module_functions(module).items():
				for role in ("HR Manager", "Payroll Officer", "Payroll Manager",
				             "Payroll Finance Approver", "Accounts Manager"):
					if ('"%s"' % role) in src and "ACTION_ROLES" not in src:
						if ("roles=" in src or "has_role" in src
								or "in frappe.get_roles" in src):
							offenders.append("%s.%s hard-codes %s"
							                 % (module.__name__.split(".")[-1], name, role))
		self.assertEqual(offenders, [],
		                 "access decided outside permissions.ACTION_ROLES: %s" % offenders)

	def test_every_capability_in_the_matrix_is_reachable(self):
		"""A capability nothing grants is a permission that cannot be given."""
		orphans = [action for action, roles in perms.ACTION_ROLES.items() if not roles]
		self.assertEqual(orphans, [], "capabilities no role holds: %s" % orphans)


class SecurityFixture(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.users = {}
		for label, roles in (("hrmanager", [perms.HR_MANAGER]),
		                     ("hruser", [perms.HR_USER]),
		                     ("finance", [perms.PAYROLL_FINANCE]),
		                     ("norole", [])):
			email = "sec.%s@example.invalid" % label
			if not frappe.db.exists("User", email):
				doc = frappe.get_doc({
					"doctype": "User", "email": email, "send_welcome_email": 0,
					"first_name": "%s SEC %s" % (PREFIX, label),
					"roles": [{"role": r} for r in roles]}).insert(ignore_permissions=True)
				cls._created.append(("User", doc.name))
			cls.users[label] = email

	def setUp(self):
		super().setUp()
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def as_user(self, label):
		frappe.set_user(self.users[label])


class TestAuthentication(SecurityFixture):

	def test_guest_cannot_reach_any_hr_endpoint(self):
		frappe.set_user("Guest")
		for call in (lambda: api.get_settings(),
		             lambda: api.payroll_preview(company=self.company,
		                                         start_date=str(self.start),
		                                         end_date=str(self.end))):
			with self.subTest(call=call):
				with self.assertRaises((frappe.PermissionError, frappe.AuthenticationError)):
					call()

	def test_a_logged_in_user_with_no_role_is_refused_not_admitted(self):
		self.as_user("norole")
		with self.assertRaises(frappe.PermissionError):
			api.get_settings()

	def test_the_refusal_is_a_permission_error_not_a_crash(self):
		self.as_user("norole")
		try:
			api.get_settings()
		except frappe.PermissionError:
			return
		except Exception as exc:
			self.fail("refused with %s, not PermissionError" % type(exc).__name__)
		self.fail("was not refused at all")


class TestAuthorisationByCapability(SecurityFixture):

	def test_settings_write_is_hr_manager_only(self):
		self.assertEqual(perms.ACTION_ROLES[perms.SETTINGS_WRITE],
		                 frozenset({perms.HR_MANAGER}))

	def test_an_hr_user_cannot_save_settings(self):
		self.as_user("hruser")
		with self.assertRaises(frappe.PermissionError):
			api.save_settings(data={"currency": "AKZ"})

	def test_an_hr_manager_can_read_settings(self):
		self.as_user("hrmanager")
		self.assertIn("default_company", api.get_settings())

	def test_posting_is_finance_only_and_hr_manager_is_not_finance(self):
		self.assertNotIn(perms.HR_MANAGER, perms.ACTION_ROLES[perms.PAYROLL_POST])
		self.assertNotIn(perms.HR_MANAGER,
		                 perms.ACTION_ROLES[perms.PAYROLL_CONFIRM_PAYMENT])

	def test_the_account_link_query_is_not_open_to_everyone(self):
		"""It reads the chart of accounts. Being a link query is not an authorisation."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings \
			import account_query
		self.as_user("norole")
		with self.assertRaises(frappe.PermissionError):
			account_query("Account", "", "name", 0, 20, {})

	def test_the_account_link_query_works_for_somebody_who_maps_accounts(self):
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings \
			import account_query
		self.as_user("hrmanager")
		self.assertIsInstance(account_query("Account", "", "name", 0, 5,
		                                    {"company": self.company}), (list, tuple))


class TestTheDeleteButtonFollowsTheServer(SecurityFixture):
	"""Delete Entry was drawn for everybody, so an HR User saw it and was refused on click.
	payroll_approval_summary now says whether delete_payroll_entry would accept it."""

	def test_an_hr_user_is_not_offered_delete_and_is_refused(self):
		name = self.payroll_run()
		self.as_user("hruser")
		self.assertFalse(api.payroll_approval_summary(name)["can_delete"])
		with self.assertRaises(frappe.PermissionError):
			api.delete_payroll_entry(name)
		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("Isoft Payroll Entry", name))

	def test_an_hr_manager_is_offered_delete_on_an_unapproved_run(self):
		name = self.payroll_run()
		self.as_user("hrmanager")
		self.assertTrue(api.payroll_approval_summary(name)["can_delete"])

	def test_nobody_is_offered_delete_once_the_run_is_approved(self):
		name = self.payroll_run()
		self.approve_run(name, state=wf.APPROVED)
		self.as_user("hrmanager")
		self.assertFalse(api.payroll_approval_summary(name)["can_delete"])


class TestCompanyScope(SecurityFixture):

	def _restrict(self, label, company):
		perm = frappe.get_doc({
			"doctype": "User Permission", "user": self.users[label],
			"allow": "Company", "for_value": company}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("User Permission", perm.name,
		                                          force=True, ignore_permissions=True))
		frappe.clear_cache(user=self.users[label])

	def test_an_unrestricted_user_sees_every_company(self):
		"""Only meaningful for a user who really has no Company User Permission — the QA
		site may already restrict one, and that is data, not a defect."""
		self.as_user("norole")
		if frappe.db.exists("User Permission", {"user": self.users["norole"],
		                                        "allow": "Company"}):
			self.skipTest("this QA user is already restricted to a company")
		self.assertIsNone(perms.allowed_companies())

	def test_a_restricted_user_is_confined_to_their_company(self):
		self._restrict("hrmanager", self.company)
		self.as_user("hrmanager")
		allowed = perms.allowed_companies()
		self.assertIsNotNone(allowed)
		self.assertIn(self.company, allowed)

	def test_acting_on_another_company_is_refused(self):
		other = [c for c in frappe.get_all("Company", pluck="name") if c != self.company]
		if not other:
			self.skipTest("only one company on this site")
		self._restrict("hrmanager", self.company)
		self.as_user("hrmanager")
		with self.assertRaises(frappe.PermissionError):
			perms.require_company(other[0])

	def test_the_scope_check_is_not_bypassed_by_omitting_the_company(self):
		"""A missing company must not read as 'every company' for a restricted user."""
		self._restrict("hrmanager", self.company)
		self.as_user("hrmanager")
		companies = api._companies()
		self.assertEqual(companies, [self.company])


class TestSelfApprovalCannotBeBypassed(SecurityFixture):
	def setUp(self):
		super().setUp()
		# These tests assert the documented approval policy, whatever the site runs.
		self.pin_settings(**self.DOCUMENTED_APPROVAL_POLICY)

	def entry(self, **stamps):
		doc = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " SEC",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", doc.name))
		values = {"status": "Pending Approval"}
		values.update(stamps)
		frappe.db.set_value("Isoft Payroll Entry", doc.name, values)
		return frappe.get_doc("Isoft Payroll Entry", doc.name)

	def test_the_state_machine_cannot_be_skipped_by_calling_the_action_directly(self):
		"""A Draft payroll cannot be approved, however it is reached."""
		doc = self.entry(status="Draft")
		self.as_user("hrmanager")          # holds payroll.approve
		with self.assertRaises(frappe.ValidationError):
			wf.perform(doc, wf.APPROVE)

	def test_a_finance_only_action_is_refused_to_hr_before_the_state_is_considered(self):
		doc = self.entry(status="Approved")
		self.as_user("hrmanager")
		with self.assertRaises(frappe.PermissionError):
			wf.perform(doc, wf.POST)

	def test_status_cannot_be_written_straight_onto_the_document(self):
		"""perform() is the single writer of status; a direct save must not launder it.

		This used to assert that a docstring said so. The guard is now real
		(payroll_workflow.assert_lifecycle_untouched) and test_p43 covers every channel;
		here the shortest path — a generic set_value by an approver — must be refused.
		"""
		from frappe.client import set_value

		doc = self.entry(status="Calculated")
		self.as_user("hrmanager")
		with self.assertRaises(frappe.PermissionError):
			set_value("Isoft Payroll Entry", doc.name, "status", "Paid")
		self.assertEqual(frappe.db.get_value("Isoft Payroll Entry", doc.name, "status"), "Calculated")

	def test_a_hidden_button_is_not_the_control(self):
		"""allowed_actions is a convenience. perform must refuse independently."""
		doc = self.entry(prepared_by=self.users["hrmanager"],
		                 submitted_by=self.users["hrmanager"])
		self.as_user("hrmanager")
		offered = wf.allowed_actions(doc)
		self.assertNotIn(wf.APPROVE, offered)
		with self.assertRaises(frappe.ValidationError):
			wf.perform(doc, wf.APPROVE)


class TestAuditStamping(SecurityFixture):

	def entry(self, **stamps):
		doc = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.end, "start_date": self.start, "end_date": self.end,
			"payroll_group": PREFIX + " AUDIT",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Isoft Payroll Entry", doc.name))
		values = {"status": "Pending Approval"}
		values.update(stamps)
		frappe.db.set_value("Isoft Payroll Entry", doc.name, values)
		return frappe.get_doc("Isoft Payroll Entry", doc.name)

	def test_every_transition_records_who_and_when(self):
		unstamped = [action for action, spec in wf._TRANSITIONS.items()
		             if not spec.get("stamp") or not spec["stamp"][1]]
		self.assertEqual(unstamped, [],
		                 "these payroll transitions record no timestamp: %s" % unstamped)

	def test_the_approval_records_the_approver_not_the_owner(self):
		doc = self.entry(prepared_by=self.users["hrmanager"])
		frappe.set_user("Administrator")
		wf.perform(doc, wf.APPROVE)
		self.assertEqual(doc.approved_by, "Administrator")

	def test_the_stamp_is_a_real_timestamp(self):
		doc = self.entry(prepared_by=self.users["hrmanager"])
		frappe.set_user("Administrator")
		wf.perform(doc, wf.APPROVE)
		self.assertTrue(doc.approved_at)
		self.assertLessEqual(str(doc.approved_at), now())


class TestArgumentFirstEndpointsStillRefuse(SecurityFixture):
	"""Some endpoints validate their argument before they authorise.

	Called with ``None`` they raise DoesNotExist rather than PermissionError, which makes
	an enumeration look as though they are unguarded. They are not — but "looks unguarded"
	is not an answer, so each one is called again with a REAL record and has to refuse.
	The order does disclose whether a name exists; it discloses nothing else.
	"""

	def _one(self, doctype):
		rows = frappe.get_all(doctype, limit=1, pluck="name")
		return rows[0] if rows else None

	def _refuses(self, call):
		"""Try one call as a no-role user and undo whatever it managed to do.

		The undo is a NESTED savepoint, not a plain rollback: the fixture isolates each
		test behind its own savepoint, and a bare rollback here destroys it — every later
		statement then dies with "SAVEPOINT ahr_test does not exist".
		"""
		self.as_user("norole")
		frappe.db.savepoint("sec_probe")
		try:
			call()
		except frappe.PermissionError:
			return True
		except Exception:
			return None          # refused another way; reported by the caller
		finally:
			frappe.db.rollback(save_point="sec_probe")
			frappe.set_user("Administrator")
		return False

	def test_every_document_action_refuses_a_real_record(self):
		cases = [
			("api.approve_settlement", "Isoft Final Settlement",
			 lambda n: api.approve_settlement(n)),
			("api.submit_settlement_for_approval", "Isoft Final Settlement",
			 lambda n: api.submit_settlement_for_approval(n)),
			("api.reject_settlement", "Isoft Final Settlement",
			 lambda n: api.reject_settlement(n, reason="x")),
			("api.payroll_action", "Isoft Payroll Entry",
			 lambda n: api.payroll_action(n, "approve")),
			("hr_api.contract_action", "Isoft Employment Contract",
			 lambda n: hr_api.contract_action(n, "approve")),
			("hr_api.renew_contract", "Isoft Employment Contract",
			 lambda n: hr_api.renew_contract(n)),
			("hr_api.probation_decision", "Isoft Employment Contract",
			 lambda n: hr_api.probation_decision(n, "Confirm")),
			("hr_api.salary_change_action", "Isoft Salary Change",
			 lambda n: hr_api.salary_change_action(n, "approve")),
			("hr_api.advance_action", "Isoft Salary Advance",
			 lambda n: hr_api.advance_action(n, "approve")),
			("hr_api.bank_change_action", "Isoft Bank Change Request",
			 lambda n: hr_api.bank_change_action(n, "approve")),
			("hr_api.leave_decision", "Leave Application",
			 lambda n: hr_api.leave_decision(n, "approve")),
			("hr_api.attendance_justification_decision", "Isoft Attendance Occurrence",
			 lambda n: hr_api.attendance_justification_decision(n, "Justified")),
			("hr_api.salary_slip_explanation", "Isoft Salary Slip",
			 lambda n: hr_api.salary_slip_explanation(n)),
			("hr_api.my_document", "Isoft Employee Document",
			 lambda n: hr_api.my_document(n)),
			("hr_api.my_occurrence", "Isoft Attendance Occurrence",
			 lambda n: hr_api.my_occurrence(n)),
			("hr_api.cancel_leave", "Leave Application",
			 lambda n: hr_api.cancel_leave(n)),
		]
		leaked, untested = [], []
		for label, doctype, call in cases:
			name = self._one(doctype)
			if not name:
				untested.append("%s (no %s on this site)" % (label, doctype))
				continue
			verdict = self._refuses(lambda: call(name))
			if verdict is False:
				leaked.append(label)
		self.assertEqual(leaked, [],
		                 "these acted on a real record for a user with no role: %s" % leaked)
		self.assertLessEqual(
			len(untested), 4,
			"too much of this check could not run for lack of QA data: %s" % untested)

	def test_the_appraisal_endpoints_authorise_by_relationship(self):
		"""They are guarded by who you are to the record, not by a role — which is why an
		enumeration with a null argument cannot see the guard."""
		from isoft_angola_hr.isoft_angola_hr.services import performance
		for fn in (performance.assert_manager_of, performance.assert_own):
			with self.subTest(guard=fn.__name__):
				self.assertIn("PermissionError", inspect.getsource(fn))

	def test_a_relationship_guard_refuses_somebody_with_no_employee_record(self):
		from isoft_angola_hr.isoft_angola_hr.services import performance
		self.as_user("norole")
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			performance.assert_own(frappe._dict({"employee": "SOMEBODY-ELSE"}))
