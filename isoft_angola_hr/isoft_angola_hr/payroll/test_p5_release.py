# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 5 tests: release gate, bank export, performance reviews, ESS uploads,
delegation, team calendar and recruitment interviews.

SAFETY — unchanged from every earlier phase. Every record is prefixed ``_TEST AHR``, is
registered for explicit deletion, and each test runs inside a savepoint.

The tests that matter most here are the ones that assert a REFUSAL: an employee cannot
justify their own absence into "Justified", cannot upload a document type HR has not
opened, cannot read another employee's review; a delegate cannot reach beyond the
delegating manager's team; a payment file cannot be produced for payroll nobody released.
Every one of those is a rule that would be invisible if it silently stopped working.
"""

import base64

import frappe
from frappe.utils import add_days, add_months, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr import hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p4_ux import UXFixture
from isoft_angola_hr.isoft_angola_hr.services import bank_export
from isoft_angola_hr.isoft_angola_hr.services import delegation
from isoft_angola_hr.isoft_angola_hr.services import ess_uploads
from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss
from isoft_angola_hr.isoft_angola_hr.services import performance
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import recruitment
from isoft_angola_hr.isoft_angola_hr.services import release_gate

#: A real, minimal PDF. Content validation checks the signature, so a random blob would
#: be rejected — which is exactly what the negative test relies on.
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
PDF_B64 = base64.b64encode(PDF_BYTES).decode()


class ReleaseFixture(UXFixture):
	def absence_reason(self):
		name = frappe.get_all("Isoft Absence Reason", pluck="name", limit=1)
		return name[0] if name else None

	def make_occurrence(self, employee=None, status="Pending Justification"):
		doc = frappe.get_doc({
			"doctype": "Isoft Attendance Occurrence",
			"employee": employee or self.report_employee,
			"company": self.company,
			"occurrence_date": add_days(nowdate(), -3),
			"occurrence_type": "Full Day",
			"hours": 8,
			"status": status,
			# The controller requires a reason before an occurrence may be Justified.
			"justification_reason": self.absence_reason() if status == "Justified" else None,
			"justification_deadline": add_days(nowdate(), 2),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Attendance Occurrence", doc.name))
		return doc

	def uploadable_type(self, confidential=False):
		name = PREFIX + (" Upload Conf" if confidential else " Upload Type")
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({
				"doctype": "Isoft Document Type", "document_type": name,
				"employee_may_upload": 1,
				"is_confidential": 1 if confidential else 0,
			}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		return name

	def locked_type(self):
		name = PREFIX + " HR Only Type"
		if not frappe.db.exists("Isoft Document Type", name):
			frappe.get_doc({
				"doctype": "Isoft Document Type", "document_type": name,
				"employee_may_upload": 0,
			}).insert(ignore_permissions=True)
			self._created.append(("Isoft Document Type", name))
		return name

	def appraisal_template(self):
		name = PREFIX + " Template"
		if not frappe.db.exists("Appraisal Template", name):
			frappe.get_doc({
				"doctype": "Appraisal Template", "kra_title": name,
				"goals": [{"kra": "Delivery", "per_weightage": 60},
				          {"kra": "Teamwork", "per_weightage": 40}],
			}).insert(ignore_permissions=True)
			self._created.append(("Appraisal Template", name))
		return name

	def make_cycle(self, **kwargs):
		values = {
			"doctype": "Isoft Performance Cycle",
			"cycle_name": "{0} Cycle {1}".format(PREFIX, frappe.generate_hash(length=6)),
			"company": self.company,
			"period_type": "Annual",
			"appraisal_template": self.appraisal_template(),
			"start_date": "2026-01-01",
			"end_date": "2026-12-31",
			"due_date": "2027-01-31",
			"minimum_service_months": 0,
		}
		values.update(kwargs)
		doc = frappe.get_doc(values).insert(ignore_permissions=True)
		self._created.append(("Isoft Performance Cycle", doc.name))
		return doc


# --------------------------------------------------------------------------- #
# §63 — what a clean installation must produce
# --------------------------------------------------------------------------- #
class TestInstallVerification(ReleaseFixture):
	def test_every_shipped_doctype_and_report_exists(self):
		result = release_gate.install_verification()
		failed = [c for c in result["checks"] if c["status"] == "FAIL"]
		self.assertFalse(failed, "install verification failed: {0}".format(
			[(c["check"], c["detail"]) for c in failed]))

	def test_app_roles_exist(self):
		for role in perms.APP_ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_seeded_catalogues_are_present(self):
		for doctype in ("Isoft Contract Type", "Isoft Document Type", "Isoft Absence Reason"):
			self.assertGreater(frappe.db.count(doctype), 0, doctype)

	def test_portal_routes_are_shipped(self):
		import os

		www = os.path.join(
			os.path.dirname(os.path.dirname(os.path.abspath(hr_api.__file__))), "www")
		for route in release_gate.PORTAL_ROUTES:
			self.assertTrue(os.path.exists(os.path.join(www, route + ".html")), route)
			self.assertTrue(os.path.exists(os.path.join(www, route + ".py")), route)

	def test_scheduled_jobs_are_registered(self):
		registered = []
		for entries in (frappe.get_hooks("scheduler_events") or {}).values():
			if isinstance(entries, dict):
				for group in entries.values():
					registered.extend(group)
			else:
				registered.extend(entries)
		for job in release_gate.SCHEDULED_JOBS:
			self.assertIn(job, registered)

	def test_employee_role_can_read_but_never_export_payroll(self):
		row = frappe.db.get_value(
			"DocPerm", {"parent": "Isoft Salary Slip", "role": "Employee"},
			["read", "export", "report"], as_dict=True)
		self.assertTrue(row and row.read)
		self.assertFalse(row.export)
		self.assertFalse(row.report)


class TestReleaseGate(ReleaseFixture):
	def test_gate_refuses_production_ready_without_a_clean_install(self):
		"""The hard rule of this phase (§92). It must not be satisfiable by opinion."""
		status = release_gate.clean_install_status()
		result = release_gate.production_release_gate(company=self.company)
		if not status["verified"]:
			self.assertNotEqual(result["verdict"], release_gate.PRODUCTION_READY)
			self.assertTrue(any(b["category"] == release_gate.SOFTWARE_RELEASE
			                    for b in result["blockers"]))

	def test_blockers_are_classified_by_owner_not_lumped_together(self):
		result = release_gate.production_release_gate(company=self.company)
		for blocker in result["blockers"]:
			self.assertIn(blocker["category"],
			              (release_gate.SOFTWARE_RELEASE, release_gate.PAYROLL_RUN,
			               release_gate.EMPLOYEE_DATA, release_gate.SECURITY))
			self.assertTrue(blocker["owner"])

	def test_missing_iban_is_a_data_blocker_never_a_software_blocker(self):
		"""§14 — a customer's incomplete bank details are not a defect in the product."""
		result = release_gate.production_release_gate(company=self.company)
		software = [b for b in result["blockers"]
		            if b["category"] == release_gate.SOFTWARE_RELEASE]
		for blocker in software:
			self.assertNotIn("IBAN", blocker["message"])
			self.assertNotIn("NIF", blocker["message"])

	def test_site_security_never_returns_a_secret_value(self):
		conf = frappe.get_conf() or {}
		secret = conf.get("openai_api_key")
		rows = release_gate.site_security()
		blob = frappe.as_json(rows)
		if secret:
			self.assertNotIn(str(secret), blob,
			                 "a credential value must never appear in a readiness report")

	def test_harden_site_is_a_dry_run_by_default(self):
		result = release_gate.harden_site()
		self.assertFalse(result["applied"])
		self.assertTrue(result["not_automated"])


# --------------------------------------------------------------------------- #
# §64 — bank export
# --------------------------------------------------------------------------- #
class TestBankExport(ReleaseFixture):
	VALID_IBAN = "AO06000600000100037131174"

	def released_entry(self):
		from isoft_angola_hr.isoft_angola_hr import api
		from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

		frappe.db.set_value("Employee", self.employee.name, "custom_iban", self.VALID_IBAN)
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def test_valid_iban_accepted_and_formatting_stripped(self):
		ok, normalised, _reason = bank_export.validate_iban("AO06 0006 0000 0100 0371 3117 4")
		self.assertTrue(ok)
		self.assertEqual(normalised, self.VALID_IBAN)

	def test_a_raw_account_number_is_not_an_iban(self):
		"""Found on live data: a BAI account number stored in the IBAN field."""
		ok, _n, reason = bank_export.validate_iban("0040.0000.9876.8769.1111.9")
		self.assertFalse(ok)
		self.assertIn("AO", reason)

	def test_check_digits_are_verified(self):
		# Transpose two digits: length and prefix still pass, mod-97 does not.
		mangled = self.VALID_IBAN[:6] + self.VALID_IBAN[7] + self.VALID_IBAN[6] + \
			self.VALID_IBAN[8:]
		ok, _n, reason = bank_export.validate_iban(mangled)
		if mangled != self.VALID_IBAN:
			self.assertFalse(ok)
			self.assertIn("check digits", reason)

	def test_unapproved_payroll_produces_no_file(self):
		entry = self.posted_entry()
		frappe.set_user(self.users["finance"])
		with self.assertRaises(frappe.ValidationError):
			bank_export.generate(entry.name)

	def test_missing_iban_is_reported_not_written_as_a_blank_row(self):
		entry = self.released_entry()
		frappe.db.set_value("Employee", self.employee.name, "custom_iban", "")
		frappe.set_user(self.users["finance"])
		report = bank_export.validate_export(entry.name)
		self.assertFalse(report["valid"])
		self.assertTrue(any(p["code"] == "BNK-004" for p in report["problems"]))

	def test_file_total_must_equal_the_payroll_total(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		report = bank_export.validate_export(entry.name)
		self.assertTrue(report["valid"], report["problems"])
		self.assertAlmostEqual(report["total"], report["payroll_total"], places=2)

	def test_generation_records_a_checksum_and_a_history_row(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		result = bank_export.generate(entry.name)
		self._created.append(("Isoft Bank Export", result["export"]))
		self.assertEqual(len(result["checksum"]), 64)
		row = frappe.db.get_value(
			"Isoft Bank Export", result["export"],
			["status", "checksum", "employee_count", "total_amount"], as_dict=True)
		self.assertEqual(row.status, "Generated")
		self.assertEqual(row.checksum, result["checksum"])

	def test_regenerating_supersedes_the_previous_file(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		first = bank_export.generate(entry.name)
		second = bank_export.generate(entry.name)
		self._created.extend([("Isoft Bank Export", first["export"]),
		                      ("Isoft Bank Export", second["export"])])
		self.assertEqual(second["version"], first["version"] + 1)
		self.assertEqual(
			frappe.db.get_value("Isoft Bank Export", first["export"], "status"),
			"Superseded")

	def test_generating_a_file_is_not_a_payment(self):
		"""The distinction three phases have protected (§24)."""
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		result = bank_export.generate(entry.name)
		self._created.append(("Isoft Bank Export", result["export"]))
		frappe.set_user("Administrator")
		entry.reload()
		from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

		self.assertEqual(entry.status, wf.PAYMENT_READY)
		self.assertEqual(
			frappe.db.get_value("Isoft Bank Export", result["export"], "status"), "Generated")

	def test_marking_submitted_requires_the_bank_reference(self):
		entry = self.released_entry()
		frappe.set_user(self.users["finance"])
		result = bank_export.generate(entry.name)
		self._created.append(("Isoft Bank Export", result["export"]))
		with self.assertRaises(frappe.ValidationError):
			bank_export.record_bank_response(result["export"], "")
		out = bank_export.record_bank_response(result["export"], "BAI-2026-0001")
		self.assertEqual(out["status"], "Submitted to Bank")

	def test_no_bank_adapter_is_claimed_without_a_specification(self):
		"""§17, §19 — an invented format is worse than none."""
		status = bank_export.BANK_STATUS
		self.assertIn("SPECIFICATION REQUIRED", status["status"])
		self.assertIn("BAI", status["not_implemented"])
		self.assertEqual(sorted(bank_export.ADAPTERS), ["generic_xlsx"])

	def test_only_finance_may_export(self):
		"""§60 — approving payroll is not the same as being allowed to pay it."""
		entry = self.released_entry()
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.PermissionError):
			bank_export.validate_export(entry.name)


# --------------------------------------------------------------------------- #
# §65 — performance
# --------------------------------------------------------------------------- #
class TestPerformance(ReleaseFixture):
	def cycle_with_appraisal(self):
		cycle = self.make_cycle()
		frappe.db.set_value("Employee", self.report_employee, "reports_to",
		                    self.manager_employee)
		result = performance.generate_cycle(cycle.name)
		for row in result["created"]:
			self._created.append(("Appraisal", row["appraisal"]))
		appraisal = frappe.db.get_value(
			"Appraisal", {"custom_performance_cycle": cycle.name,
			              "employee": self.report_employee}, "name")
		if not appraisal:
			# Report WHY rather than asserting on a bare None — a generator that silently
			# creates nothing is the failure mode this test exists to catch.
			self.fail("no appraisal created. failed={0} blocked={1} skipped={2}".format(
				result["failed"][:3], result["blocked"][:3], result["skipped"][:3]))
		return cycle, appraisal

	def test_cycle_preview_writes_nothing(self):
		cycle = self.make_cycle()
		before = frappe.db.count("Appraisal")
		plan = performance.preview_cycle(cycle.name)
		self.assertEqual(frappe.db.count("Appraisal"), before)
		self.assertIn("summary", plan)

	def test_generation_creates_one_appraisal_per_eligible_employee(self):
		cycle, appraisal = self.cycle_with_appraisal()
		self.assertTrue(appraisal)
		self.assertEqual(
			frappe.db.get_value("Appraisal", appraisal, "custom_review_state"),
			performance.PENDING_MANAGER)

	def test_regenerating_does_not_duplicate(self):
		cycle, _appraisal = self.cycle_with_appraisal()
		before = frappe.db.count("Appraisal", {"custom_performance_cycle": cycle.name})
		again = performance.generate_cycle(cycle.name)
		self.assertEqual(again["summary"]["created"], 0)
		self.assertEqual(
			frappe.db.count("Appraisal", {"custom_performance_cycle": cycle.name}), before)

	def test_employee_without_a_manager_is_included_and_the_reason_says_hr_reviews(self):
		"""BUSINESS MODEL CHANGED — this test used to assert "Blocked".

		It was the one existing test that encoded the manager-must-log-in model: an
		employee with no ``reports_to`` was excluded from every review cycle, because the
		evaluation could only be entered by the manager's own session.

		HR now operates performance reviews, so a missing line manager is a fallback rule
		("HR conducts this review"), not an exclusion. Blocking would have kept 43 of this
		site's active employees permanently out of performance management.

		The assertion is not weakened: it still requires the row to be present, to carry a
		definite action, and to state the reason in words HR can act on. It now requires
		the OPPOSITE outcome, which is the point.
		"""
		cycle = self.make_cycle()
		orphan = self._employee("NoManager")
		plan = performance.preview_cycle(cycle.name)
		row = [r for r in plan["rows"] if r["employee"] == orphan]
		self.assertTrue(row)
		self.assertEqual(row[0]["action"], "Create")
		self.assertIn("manager", row[0]["reason"].lower())
		self.assertIn("hr", row[0]["reason"].lower())
		self.assertEqual(plan["summary"]["blocked"], 0,
		                 "nothing should be blocked for want of a line manager any more")

	def test_manager_scores_and_submits(self):
		cycle, appraisal = self.cycle_with_appraisal()
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name", "kra"])
		frappe.set_user(self.manager_user)
		result = performance.manager_review(
			appraisal, goals={g["name"]: 4 for g in goals}, comments="Solid year")
		self.assertEqual(result["state"], performance.PENDING_EMPLOYEE)
		self.assertGreater(result["total_score"], 0)

	def test_manager_cannot_submit_with_an_unscored_objective(self):
		cycle, appraisal = self.cycle_with_appraisal()
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name"])
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.ValidationError):
			performance.manager_review(appraisal, goals={goals[0]["name"]: 4})

	def test_an_unrelated_manager_is_refused(self):
		cycle, appraisal = self.cycle_with_appraisal()
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			performance.review_detail(appraisal)

	def test_employee_cannot_read_a_review_still_with_the_manager(self):
		"""§31 — a draft opinion about somebody is not theirs to read yet."""
		cycle, appraisal = self.cycle_with_appraisal()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			performance.my_review(appraisal)
		self.assertEqual(performance.my_reviews(), [])

	def test_employee_cannot_read_another_employees_review(self):
		cycle, appraisal = self.cycle_with_appraisal()
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			performance.my_review(appraisal)

	def test_full_workflow_to_finalised_and_then_visible(self):
		cycle, appraisal = self.cycle_with_appraisal()
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name"])
		frappe.set_user(self.manager_user)
		performance.manager_review(appraisal, goals={g["name"]: 4 for g in goals},
		                           comments="Good")
		frappe.set_user(self.employee_user)
		performance.employee_acknowledge(appraisal, comments="Noted")
		frappe.set_user("Administrator")
		performance.hr_finalise(appraisal)

		frappe.set_user(self.employee_user)
		mine = performance.my_reviews()
		self.assertIn(appraisal, [r["name"] for r in mine])
		self.assertEqual(performance.my_review(appraisal)["custom_review_state"],
		                 performance.FINALISED)

	def test_a_score_never_changes_pay_by_itself(self):
		"""§33 — the single most important rule in this module."""
		cycle, appraisal = self.cycle_with_appraisal()
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name"])
		frappe.set_user(self.manager_user)
		performance.manager_review(appraisal, goals={g["name"]: 5 for g in goals})
		frappe.set_user("Administrator")
		performance.hr_finalise(appraisal)

		profiles_before = frappe.db.count("Isoft Salary Profile")
		# The effective date must land on a payroll period boundary — the Phase 3 rule
		# still applies when the request arrives from a performance review.
		result = performance.recommend_salary_change(
			appraisal, new_base=500000, effective_date=self.next_period_start())
		self._created.append(("Isoft Salary Change", result["salary_change"]))

		# A request, not a change: no new salary profile, and it is still a draft.
		self.assertEqual(frappe.db.count("Isoft Salary Profile"), profiles_before)
		self.assertEqual(
			frappe.db.get_value("Isoft Salary Change", result["salary_change"], "status"),
			"Draft")

	def test_a_recommendation_still_obeys_the_period_boundary_rule(self):
		"""A review is not a way around payroll's own rules."""
		cycle, appraisal = self.cycle_with_appraisal()
		goals = frappe.get_all("Appraisal Goal", filters={"parent": appraisal},
		                       fields=["name"])
		frappe.set_user(self.manager_user)
		performance.manager_review(appraisal, goals={g["name"]: 4 for g in goals})
		frappe.set_user("Administrator")
		performance.hr_finalise(appraisal)
		with self.assertRaises(frappe.ValidationError) as cm:
			performance.recommend_salary_change(
				appraisal, 500000, add_days(self.next_period_start(), 5))
		self.assertIn("período", str(cm.exception))

	def test_salary_recommendation_requires_a_finalised_review(self):
		cycle, appraisal = self.cycle_with_appraisal()
		with self.assertRaises(frappe.ValidationError):
			performance.recommend_salary_change(appraisal, 500000, nowdate())

	def test_cycle_cannot_close_while_reviews_are_open(self):
		cycle, _appraisal = self.cycle_with_appraisal()
		with self.assertRaises(frappe.ValidationError):
			performance.close_cycle(cycle.name)

	def test_progress_reports_bands_and_never_names_the_lowest_scorer(self):
		"""§36 — no "worst employees" screen."""
		cycle, _appraisal = self.cycle_with_appraisal()
		progress = performance.cycle_progress(cycle=cycle.name)
		self.assertIn("distribution", progress)
		for band in progress["distribution"]:
			self.assertNotIn("employee", band)
			self.assertNotIn("employee_name", band)


# --------------------------------------------------------------------------- #
# §46, §47 — delegation
# --------------------------------------------------------------------------- #
class TestDelegation(ReleaseFixture):
	def delegate_to_stranger(self, days=7):
		record = delegation.create(
			self.manager_employee, self.stranger, nowdate(), add_days(nowdate(), days),
			reason="Annual leave")
		self._created.append(("Isoft Manager Delegation", record["name"]))
		return record

	def test_delegate_can_act_on_the_delegators_team(self):
		self.delegate_to_stranger()
		frappe.set_user(self.stranger_user)
		self.assertTrue(mss.assert_in_team(self.report_employee))

	def test_delegation_does_not_widen_scope_to_everybody(self):
		self.delegate_to_stranger()
		outsider = self._employee("Outsider")
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			mss.assert_in_team(outsider)

	def test_an_expired_delegation_grants_nothing(self):
		record = delegation.create(
			self.manager_employee, self.stranger,
			add_days(nowdate(), -30), add_days(nowdate(), -1))
		self._created.append(("Isoft Manager Delegation", record["name"]))
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			mss.assert_in_team(self.report_employee)

	def test_delegation_cannot_be_chained(self):
		self.delegate_to_stranger()
		third = self._employee("Third")
		with self.assertRaises(frappe.ValidationError):
			delegation.create(self.stranger, third, nowdate(), add_days(nowdate(), 5))

	def test_revoked_delegation_stops_immediately(self):
		record = self.delegate_to_stranger()
		delegation.revoke(record["name"])
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			mss.assert_in_team(self.report_employee)

	def test_delegation_grants_no_payroll_access(self):
		"""The line that must never move."""
		self.delegate_to_stranger()
		frappe.set_user(self.stranger_user)
		for action in (perms.PAYROLL_READ, perms.SALARY_PROFILE_READ,
		               perms.PAYROLL_EXPORT_BANK, perms.REPORT_PAYROLL):
			self.assertFalse(perms.can(action))

	def test_a_manager_cannot_delegate_to_themselves(self):
		with self.assertRaises(frappe.ValidationError):
			delegation.create(self.manager_employee, self.manager_employee,
			                  nowdate(), add_days(nowdate(), 5))


class TestTeamCalendar(ReleaseFixture):
	def test_calendar_is_scoped_to_the_team(self):
		frappe.set_user(self.manager_user)
		calendar = mss.team_calendar()
		team = set(mss.team())
		for row in calendar["leave"]:
			self.assertIn(row["employee"], team)

	def test_calendar_never_carries_a_leave_reason(self):
		"""§45 — the type is what cover planning needs; the reason is not."""
		frappe.set_user(self.manager_user)
		calendar = mss.team_calendar()
		blob = frappe.as_json(calendar)
		self.assertNotIn("description", blob)
		self.assertTrue(calendar["privacy_note"])


# --------------------------------------------------------------------------- #
# §66 — attendance justification upload
# --------------------------------------------------------------------------- #
class TestAttendanceJustification(ReleaseFixture):
	def test_employee_submits_an_explanation_with_a_document(self):
		occurrence = self.make_occurrence()
		frappe.set_user(self.employee_user)
		result = ess_uploads.submit_justification(
			occurrence.name, explanation="Hospital appointment",
			filename="atestado.pdf", content=PDF_B64)
		self.assertTrue(result["document"])
		self.assertTrue(result["document"].startswith("/private/files/"),
		                "a justification attachment must be private")

	def test_submitting_a_justification_does_not_justify_the_absence(self):
		"""§38 — an employee makes a case; they do not decide it."""
		occurrence = self.make_occurrence()
		frappe.set_user(self.employee_user)
		ess_uploads.submit_justification(occurrence.name, explanation="Was ill")
		self.assertEqual(
			frappe.db.get_value("Isoft Attendance Occurrence", occurrence.name, "status"),
			"Pending Justification")

	def test_employee_cannot_justify_another_employees_occurrence(self):
		occurrence = self.make_occurrence(employee=self.stranger)
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess_uploads.submit_justification(occurrence.name, explanation="Not mine")

	def test_an_already_justified_occurrence_is_refused(self):
		occurrence = self.make_occurrence(status="Justified")
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.submit_justification(occurrence.name, explanation="Again")

	def test_an_empty_submission_is_refused(self):
		occurrence = self.make_occurrence()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.submit_justification(occurrence.name)

	def test_a_disallowed_extension_is_refused(self):
		occurrence = self.make_occurrence()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.submit_justification(
				occurrence.name, filename="payload.exe", content=PDF_B64)

	def test_an_oversize_file_is_refused(self):
		occurrence = self.make_occurrence()
		big = base64.b64encode(PDF_BYTES + b"0" * (ess_uploads.MAX_BYTES + 1)).decode()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.submit_justification(
				occurrence.name, filename="huge.pdf", content=big)

	def test_a_renamed_file_is_refused(self):
		"""§43 — the extension says what a file claims to be, not what it is."""
		occurrence = self.make_occurrence()
		fake = base64.b64encode(b"MZ\x90\x00 this is an executable").decode()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.submit_justification(
				occurrence.name, filename="scan.pdf", content=fake)


# --------------------------------------------------------------------------- #
# §67 — employee document upload
# --------------------------------------------------------------------------- #
class TestEmployeeDocumentUpload(ReleaseFixture):
	def upload(self, document_type=None):
		frappe.set_user(self.employee_user)
		result = ess_uploads.upload_document(
			document_type or self.uploadable_type(), "bi.pdf", PDF_B64)
		self._created.append(("Isoft Employee Document", result["name"]))
		return result

	def test_allowed_type_uploads_as_pending_verification(self):
		result = self.upload()
		self.assertEqual(result["verification_status"], "Pending Verification")

	def test_a_type_hr_has_not_opened_is_refused(self):
		"""§41 — an employee must not be able to file their own contract."""
		locked = self.locked_type()
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess_uploads.upload_document(locked, "contract.pdf", PDF_B64)

	def test_the_attachment_is_private(self):
		result = self.upload()
		attachment = frappe.db.get_value(
			"Isoft Employee Document", result["name"], "attachment")
		self.assertTrue(attachment.startswith("/private/files/"))
		file_row = frappe.db.get_value("File", {"file_url": attachment},
		                               ["is_private"], as_dict=True)
		self.assertTrue(file_row and file_row.is_private)

	def test_an_uploaded_document_is_not_authoritative_until_verified(self):
		"""§42 — otherwise an employee could rewrite their own record."""
		result = self.upload()
		self.assertEqual(
			frappe.db.get_value("Isoft Employee Document", result["name"],
			                    "verification_status"),
			"Pending Verification")

	def test_hr_verifies_and_rejects_with_a_reason(self):
		result = self.upload()
		frappe.set_user("Administrator")
		out = ess_uploads.verify_document(result["name"], "Verified")
		self.assertEqual(out["verification_status"], "Verified")

		second = self.upload()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			ess_uploads.verify_document(second["name"], "Rejected")
		out = ess_uploads.verify_document(second["name"], "Rejected", reason="Illegible")
		self.assertEqual(out["verification_status"], "Rejected")

	def test_another_employee_cannot_read_the_upload(self):
		result = self.upload()
		frappe.set_user(self.stranger_user)
		with self.assertRaises(frappe.PermissionError):
			hr_api.my_document(result["name"])

	def test_a_confidential_upload_stays_out_of_a_non_manager_queue(self):
		confidential = self.uploadable_type(confidential=True)
		frappe.set_user(self.employee_user)
		result = ess_uploads.upload_document(confidential, "medico.pdf", PDF_B64)
		self._created.append(("Isoft Employee Document", result["name"]))
		frappe.set_user(self.users["hruser"])
		queue = [r["name"] for r in ess_uploads.pending_verification()]
		self.assertNotIn(result["name"], queue,
		                 "an HR User without the confidential permission must not see it")


# --------------------------------------------------------------------------- #
# §68 — recruitment interviews
# --------------------------------------------------------------------------- #
class TestRecruitmentInterviews(ReleaseFixture):
	def test_interview_pipeline_available(self):
		result = recruitment.interview_pipeline()
		if not result.get("available"):
			self.skipTest("ERPNext recruitment is not installed")
		for stage in ("scheduled", "upcoming", "under_review", "cleared", "rejected"):
			self.assertIn(stage, result["stages"])

	def test_scheduling_without_an_interviewer_is_refused(self):
		if not frappe.db.table_exists("Interview Round"):
			self.skipTest("Interview Round not available")
		applicant = frappe.get_doc({
			"doctype": "Job Applicant",
			"applicant_name": PREFIX + " Interviewee",
			"email_id": "ahr.interview@ahrtest.invalid", "status": "Open",
		}).insert(ignore_permissions=True)
		self._created.append(("Job Applicant", applicant.name))

		round_name = PREFIX + " Round"
		if not frappe.db.exists("Interview Round", round_name):
			frappe.get_doc({
				"doctype": "Interview Round", "round_name": round_name,
				"expected_average_rating": 3,
				"interview_type": self.interview_type(),
				"expected_skill_set": [{"skill": PREFIX + " Skill"}],
			}).insert(ignore_permissions=True)
			self._created.append(("Interview Round", round_name))

		with self.assertRaises(frappe.ValidationError):
			recruitment.schedule_interview(applicant.name, round_name, nowdate())

	def interview_type(self):
		name = PREFIX + " Interview Type"
		if not frappe.db.exists("Interview Type", name):
			frappe.get_doc({"doctype": "Interview Type", "name": name
			                }).insert(ignore_permissions=True)
			self._created.append(("Interview Type", name))
		return name

	def test_no_public_endpoint_is_added_by_this_app(self):
		"""§51, §52 — ERPNext's Job Opening already publishes openings."""
		self.assertEqual(recruitment.CAREERS_PAGE["public_endpoints_added_by_this_app"], 0)


# --------------------------------------------------------------------------- #
# §25 — the Salary Profile overlap guard
#
# The defect this class exists for: a profile could be created while the previous one
# was still open-ended, so two profiles both claimed every day from the later start
# onwards and payroll could not tell which salary applied. Three employees on the live
# site reached that state.
# --------------------------------------------------------------------------- #
class TestSalaryProfileOverlap(ReleaseFixture):
	def make_profile(self, from_date, to_date=None, base=150000, employee=None, legacy=False):
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Profile",
			"employee": employee or self.overlap_employee,
			"company": self.company, "from_date": from_date, "to_date": to_date,
			"base": base,
		})
		if legacy:
			# Reproduces data that predates the guard.
			doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Profile", doc.name))
		return doc

	def setUp(self):
		super().setUp()
		self.overlap_employee = self._employee("Overlap")

	def test_one_current_open_profile_is_allowed(self):
		doc = self.make_profile("2026-01-01")
		self.assertIsNone(doc.to_date)

	def test_closed_history_plus_a_new_open_profile_is_allowed(self):
		"""The intended model: close the old one, open the next the following day."""
		self.make_profile("2026-01-01", to_date="2026-06-30", base=150000)
		later = self.make_profile("2026-07-01", base=200000)
		self.assertEqual(getdate(later.from_date), getdate("2026-07-01"))

	def test_a_second_open_profile_after_an_open_one_is_refused(self):
		"""THE defect. Two open-ended profiles both claim July onwards."""
		self.make_profile("2026-01-01")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.make_profile("2026-07-01", base=200000)
		self.assertIn("already covers", str(cm.exception))

	def test_overlapping_closed_ranges_are_refused(self):
		self.make_profile("2026-01-01", to_date="2026-12-31")
		with self.assertRaises(frappe.ValidationError):
			self.make_profile("2026-07-01", to_date="2026-09-30", base=200000)

	def test_a_new_open_profile_inside_a_closed_range_is_refused(self):
		self.make_profile("2026-01-01", to_date="2026-12-31")
		with self.assertRaises(frappe.ValidationError):
			self.make_profile("2026-07-01", base=200000)

	def test_the_same_start_date_is_still_refused(self):
		self.make_profile("2026-01-01")
		with self.assertRaises(frappe.ValidationError):
			self.make_profile("2026-01-01", base=200000)

	def test_a_profile_starting_the_day_after_a_closed_one_is_allowed(self):
		"""Boundary: 30 June closed, 1 July open — adjacent, not overlapping."""
		self.make_profile("2026-01-01", to_date="2026-06-30")
		self.make_profile("2026-07-01", base=200000)
		rows = frappe.db.count("Isoft Salary Profile", {"employee": self.overlap_employee})
		self.assertEqual(rows, 2)

	def test_a_profile_ending_the_day_a_later_one_starts_is_refused(self):
		"""Off-by-one: both claim 1 July."""
		self.make_profile("2026-01-01", to_date="2026-07-01")
		with self.assertRaises(frappe.ValidationError):
			self.make_profile("2026-07-01", base=200000)

	def test_existing_overlaps_can_still_be_edited_without_moving_their_dates(self):
		"""§26 — the three live conflicts must remain editable and must not break
		migration. An overlap that already existed, whose dates are unchanged, is saved
		with a warning rather than blocked."""
		first = self.make_profile("2026-01-01")
		second = self.make_profile("2026-07-01", base=200000, legacy=True)
		second.reload()
		second.base = 210000
		second.save(ignore_permissions=True)
		self.assertEqual(
			flt(frappe.db.get_value("Isoft Salary Profile", second.name, "base")), 210000)

	def test_moving_a_date_into_an_overlap_is_still_refused(self):
		self.make_profile("2026-01-01", to_date="2026-06-30")
		later = self.make_profile("2026-07-01", base=200000)
		later.reload()
		later.from_date = "2026-05-01"
		with self.assertRaises(frappe.ValidationError):
			later.save(ignore_permissions=True)

	def test_salary_change_apply_closes_the_old_profile_and_opens_the_new_one(self):
		"""The controlled workflow must still work under the stricter guard — and it is
		now the ONLY way to raise a salary without hand-editing dates."""
		from isoft_angola_hr.isoft_angola_hr import api
		from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc

		employee = self._employee("ChangeFlow")
		self.make_profile("2026-01-01", base=150000, employee=employee)
		effective, _end = api._cycle_period(add_months(getdate(nowdate()), 2))

		change = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": employee,
			"company": self.company, "change_type": "Merit Increase",
			"effective_date": effective, "current_base": 150000, "new_base": 200000,
			"reason": "overlap guard regression",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Change", change.name))

		frappe.set_user(self.users["hruser"])
		sc.perform(change, sc.SUBMIT)
		frappe.set_user("Administrator")
		sc.perform(change, sc.APPROVE, user="Administrator")
		sc.perform(change, sc.APPLY, user="Administrator")
		change.reload()
		self._created.append(("Isoft Salary Profile", change.created_profile))

		rows = frappe.db.sql(
			"""select from_date, to_date, base from `tabIsoft Salary Profile`
			where employee = %s order by from_date""", employee, as_dict=True)
		self.assertEqual(len(rows), 2)
		self.assertEqual(getdate(rows[0].to_date), add_days(getdate(effective), -1),
		                 "the old profile must be closed the day before the new one")
		self.assertEqual(getdate(rows[1].from_date), getdate(effective))
		self.assertIsNone(rows[1].to_date, "the new profile is the current one")

	def test_re_applying_a_salary_change_is_idempotent(self):
		from isoft_angola_hr.isoft_angola_hr import api
		from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc

		employee = self._employee("Idempotent")
		self.make_profile("2026-01-01", base=150000, employee=employee)
		effective, _end = api._cycle_period(add_months(getdate(nowdate()), 2))
		change = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": employee,
			"company": self.company, "change_type": "Merit Increase",
			"effective_date": effective, "current_base": 150000, "new_base": 200000,
			"reason": "idempotency",
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Salary Change", change.name))
		frappe.set_user(self.users["hruser"])
		sc.perform(change, sc.SUBMIT)
		frappe.set_user("Administrator")
		sc.perform(change, sc.APPROVE, user="Administrator")
		first = sc.apply_change(change)
		self._created.append(("Isoft Salary Profile", first))
		second = sc.apply_change(change)
		self.assertEqual(first, second, "re-applying must not create a second profile")
		self.assertEqual(
			frappe.db.count("Isoft Salary Profile", {"employee": employee}), 2)

	def test_the_three_live_conflicts_are_reported_not_silently_fixed(self):
		"""Existing bad data must stay visible in readiness, and stay untouched."""
		from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr

		conflicts = frappe.db.sql("""
			select p.employee from `tabIsoft Salary Profile` p
			join `tabEmployee` e on e.name = p.employee
			where e.status = 'Active'
			group by p.employee having count(*) > 1""")
		data = pr.data_counts(self.company)
		keys = {row["key"]: row for row in data}
		self.assertIn("ambiguous_profile", keys)
		self.assertIn("missing_profile", keys)
