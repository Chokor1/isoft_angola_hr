# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 3 tests: contracts, probation, salary changes, advances, documents,
self-service, manager scope and HR readiness.

SAFETY — unchanged from every earlier phase. The fixture builds its own employees,
users, contracts and documents prefixed ``_TEST AHR``, deletes them explicitly, and runs
each test inside a savepoint. No existing employee, salary profile or payroll slip is
read for mutation or written to.
"""

import frappe
from frappe.utils import add_days, add_months, flt, get_first_day, get_last_day, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr import hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle
from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc


class HRFixture(ControlFixture):
	"""Payroll fixture plus an HR-shaped org: a manager, a report, and their users."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.contract_type = cls._contract_type()
		# A line manager is an ordinary employee that others report to — no HR role.
		cls.manager_user = cls._make_user("linemanager", ["Employee"])
		cls.manager_employee = cls._employee("Manager", user=cls.manager_user)
		cls.report_employee = cls._employee("Report", reports_to=cls.manager_employee)
		cls.employee_user = cls._make_user("staff", ["Employee"])
		frappe.db.set_value("Employee", cls.report_employee, "user_id", cls.employee_user)
		frappe.db.set_value("Employee", cls.employee.name, "reports_to", cls.manager_employee)

	@classmethod
	def _contract_type(cls):
		name = PREFIX + " Fixed Term"
		if not frappe.db.exists("Isoft Contract Type", name):
			frappe.get_doc({
				"doctype": "Isoft Contract Type", "contract_type": name,
				"is_fixed_term": 1, "default_duration_months": 12,
				"default_probation_days": 60, "default_notice_days": 30, "renewable": 1,
			}).insert(ignore_permissions=True)
			cls._created.append(("Isoft Contract Type", name))
		return name

	@classmethod
	def _employee(cls, label, reports_to=None, user=None):
		doc = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": label,
			"company": cls.company, "date_of_joining": "2021-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01", "reports_to": reports_to, "user_id": user,
		}).insert(ignore_permissions=True)
		cls._created.append(("Employee", doc.name))
		return doc.name

	# ------------------------------------------------------------------ #
	def make_contract(self, employee=None, start=None, end=None, **kwargs):
		values = {
			"doctype": "Isoft Employment Contract",
			"employee": employee or self.employee.name,
			"contract_type": self.contract_type,
			"start_date": start or "2026-01-01",
			"end_date": end or "2026-12-31",
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def approved_contract(self, **kwargs):
		doc = self.make_contract(**kwargs)
		frappe.set_user(self.users["hruser"])
		contracts.perform(doc, contracts.SUBMIT)
		frappe.set_user("Administrator")
		doc.reload()
		contracts.perform(doc, contracts.APPROVE, user="Administrator")
		doc.reload()
		return doc

	def next_period_start(self):
		"""The start of a payroll period that has not been processed yet."""
		from isoft_angola_hr.isoft_angola_hr import api

		start, _end = api._cycle_period(add_months(getdate(nowdate()), 2))
		return start


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #
class TestEmploymentContract(HRFixture):
	def test_contract_defaults_come_from_the_contract_type(self):
		doc = frappe.get_doc({
			"doctype": "Isoft Employment Contract", "employee": self.employee.name,
			"contract_type": self.contract_type, "start_date": "2026-01-01",
		}).insert(ignore_permissions=True)
		self.assertEqual(getdate(doc.end_date), getdate("2026-12-31"))
		self.assertEqual(doc.notice_days, 30)
		self.assertEqual(getdate(doc.probation_end), getdate("2026-03-01"))
		self.assertEqual(doc.status, contracts.DRAFT)

	def test_approval_walks_the_workflow_and_stamps_who_did_what(self):
		doc = self.make_contract()
		frappe.set_user(self.users["hruser"])
		self.assertEqual(contracts.perform(doc, contracts.SUBMIT), contracts.PENDING_APPROVAL)
		frappe.set_user("Administrator")
		doc.reload()
		contracts.perform(doc, contracts.APPROVE, user="Administrator")
		doc.reload()
		self.assertIn(doc.status, (contracts.ACTIVE, contracts.EXPIRING, contracts.EXPIRED))
		self.assertEqual(doc.submitted_by, self.users["hruser"])
		self.assertEqual(doc.approved_by, "Administrator")

	def test_preparer_cannot_approve_their_own_contract(self):
		doc = self.make_contract()
		frappe.set_user(self.users["hruser"])
		contracts.perform(doc, contracts.SUBMIT)
		doc.reload()
		hr_user = frappe.get_doc("User", self.users["hruser"])
		hr_user.append("roles", {"role": perms.HR_MANAGER})
		hr_user.save(ignore_permissions=True)
		frappe.set_user(self.users["hruser"])
		with self.assertRaises(frappe.ValidationError) as cm:
			contracts.perform(doc, contracts.APPROVE)
		self.assertIn("preparou", str(cm.exception))

	def test_overlapping_contracts_are_blocked(self):
		self.approved_contract(start="2026-01-01", end="2026-12-31")
		with self.assertRaises(frappe.ValidationError) as cm:
			self.approved_contract(start="2026-07-01", end="2026-12-31")
		self.assertIn("two employment contracts", str(cm.exception))

	def test_a_later_non_overlapping_contract_is_allowed(self):
		self.approved_contract(start="2026-01-01", end="2026-06-30")
		later = self.approved_contract(start="2026-07-01", end="2027-06-30")
		self.assertTrue(later.name)

	def test_a_fixed_term_contract_needs_an_end_date(self):
		no_default = PREFIX + " Fixed No Default"
		if not frappe.db.exists("Isoft Contract Type", no_default):
			frappe.get_doc({"doctype": "Isoft Contract Type", "contract_type": no_default,
			                "is_fixed_term": 1, "default_duration_months": 0}).insert(
				ignore_permissions=True)
			self._created.append(("Isoft Contract Type", no_default))
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.get_doc({
				"doctype": "Isoft Employment Contract", "employee": self.employee.name,
				"contract_type": no_default, "start_date": "2026-01-01", "is_open_ended": 0,
			}).insert(ignore_permissions=True)
		self.assertIn("needs an End Date", str(cm.exception))

	def test_expiry_status_is_derived_from_the_dates(self):
		doc = self.approved_contract(start=add_days(getdate(nowdate()), -300),
		                             end=add_days(getdate(nowdate()), 10))
		self.assertEqual(contracts.derive_status(doc), contracts.EXPIRING)
		past = frappe._dict(status=contracts.ACTIVE, is_open_ended=0,
		                    end_date=add_days(getdate(nowdate()), -1))
		self.assertEqual(contracts.derive_status(past), contracts.EXPIRED)

	def test_renewal_creates_a_new_contract_and_preserves_the_old_one(self):
		original = self.approved_contract(start="2026-01-01", end="2026-12-31")
		renewal = contracts.renew(original, start_date="2027-01-01", end_date="2027-12-31")
		original.reload()

		self.assertEqual(original.status, contracts.RENEWED)
		self.assertEqual(getdate(original.start_date), getdate("2026-01-01"))
		self.assertEqual(getdate(original.end_date), getdate("2026-12-31"),
		                 "the original agreement's dates must never be rewritten")
		self.assertEqual(original.renewed_to, renewal.name)
		self.assertEqual(renewal.previous_contract, original.name)
		self.assertEqual(getdate(renewal.start_date), getdate("2027-01-01"))

	def test_a_renewal_cannot_start_before_the_old_contract_ends(self):
		original = self.approved_contract(start="2026-01-01", end="2026-12-31")
		with self.assertRaises(frappe.ValidationError):
			contracts.renew(original, start_date="2026-06-01", end_date="2027-05-31")

	def test_a_contract_cannot_be_renewed_twice(self):
		original = self.approved_contract(start="2026-01-01", end="2026-12-31")
		contracts.renew(original, start_date="2027-01-01", end_date="2027-12-31")
		original.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			contracts.renew(original, start_date="2027-01-01", end_date="2027-12-31")
		# The state guard fires first: a Renewed contract is no longer renewable.
		self.assertIn("Renewed", str(cm.exception))

	def test_an_approved_contract_cannot_be_deleted(self):
		doc = self.approved_contract()
		with self.assertRaises(frappe.ValidationError):
			doc.delete()

	def test_company_isolation(self):
		if not getattr(self, "other_company", None):
			others = [c for c in frappe.get_all("Company", pluck="name") if c != self.company]
			if not others:
				self.skipTest("single company site")
			self.other_company = others[0]
		doc = self.make_contract()
		frappe.get_doc({"doctype": "User Permission", "user": self.users["hruser"],
		                "allow": "Company", "for_value": self.other_company,
		                "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
		frappe.clear_cache(user=self.users["hruser"])
		self.addCleanup(frappe.clear_cache, user=self.users["hruser"])
		frappe.set_user(self.users["hruser"])
		with self.assertRaises(frappe.PermissionError):
			contracts.perform(doc, contracts.SUBMIT)


# --------------------------------------------------------------------------- #
# Probation
# --------------------------------------------------------------------------- #
class TestProbation(HRFixture):
	def _with_probation(self, end_offset):
		return self.approved_contract(
			start=add_days(getdate(nowdate()), -120),
			end=add_days(getdate(nowdate()), 240),
			probation_start=add_days(getdate(nowdate()), -120),
			probation_end=add_days(getdate(nowdate()), end_offset))

	def test_probation_status_follows_the_dates(self):
		def probation(end_offset):
			return frappe._dict(status=contracts.ACTIVE, probation_decision=None,
			                    probation_end=add_days(getdate(nowdate()), end_offset))

		self.assertEqual(contracts.derive_probation_status(probation(60)), "In Progress")
		self.assertEqual(contracts.derive_probation_status(probation(5)), "Review Due")
		self.assertEqual(contracts.derive_probation_status(probation(-5)), "Overdue")

	def test_confirming_probation_records_the_decision_and_confirms_the_employee(self):
		doc = self._with_probation(5)
		contracts.record_probation_decision(doc, "Confirmed", notes="Doing well",
		                                    user="Administrator")
		doc.reload()
		self.assertEqual(doc.probation_status, "Confirmed")
		self.assertEqual(doc.probation_decision, "Confirmed")
		self.assertEqual(doc.probation_decision_by, "Administrator")
		self.assertTrue(frappe.db.get_value("Employee", doc.employee, "final_confirmation_date"))

	def test_extending_probation_requires_a_later_end_date(self):
		doc = self._with_probation(5)
		with self.assertRaises(frappe.ValidationError):
			contracts.record_probation_decision(doc, "Extended", user="Administrator")
		with self.assertRaises(frappe.ValidationError):
			contracts.record_probation_decision(
				doc, "Extended", new_end=add_days(getdate(nowdate()), 1),
				user="Administrator")
		new_end = add_days(getdate(doc.probation_end), 30)
		contracts.record_probation_decision(doc, "Extended", new_end=new_end,
		                                    user="Administrator")
		doc.reload()
		self.assertEqual(getdate(doc.probation_end), getdate(new_end))
		self.assertEqual(doc.probation_status, "Extended")

	def test_a_failed_probation_is_recorded(self):
		doc = self._with_probation(5)
		contracts.record_probation_decision(doc, "Terminated", notes="Not confirmed",
		                                    user="Administrator")
		doc.reload()
		self.assertEqual(doc.probation_status, "Failed")

	def test_probation_reviews_due_lists_the_undecided(self):
		doc = self._with_probation(5)
		names = [r["name"] for r in contracts.probation_reviews_due(company=self.company)]
		self.assertIn(doc.name, names)
		contracts.record_probation_decision(doc, "Confirmed", user="Administrator")
		names = [r["name"] for r in contracts.probation_reviews_due(company=self.company)]
		self.assertNotIn(doc.name, names)


# --------------------------------------------------------------------------- #
# Salary change
# --------------------------------------------------------------------------- #
class TestSalaryChange(HRFixture):
	def make_change(self, employee=None, effective=None, new_base=260000, **kwargs):
		values = {
			"doctype": "Isoft Salary Change", "employee": employee or self.employee.name,
			"change_type": "Promotion", "new_base": new_base,
			"effective_date": effective or self.next_period_start(),
			"reason": "Annual review",
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def test_current_salary_is_snapshotted_for_the_approver(self):
		doc = self.make_change()
		self.assertEqual(doc.current_profile, self.profile.name)
		self.assertAlmostEqual(flt(doc.current_base), 200000.00, places=2)
		self.assertAlmostEqual(flt(doc.percentage_change), 30.0, places=2)
		# Allowances default to the current ones rather than silently becoming zero.
		self.assertAlmostEqual(flt(doc.new_food_allowance), 30000.00, places=2)

	def test_a_mid_period_effective_date_is_refused(self):
		start = self.next_period_start()
		with self.assertRaises(frappe.ValidationError) as cm:
			self.make_change(effective=add_days(start, 5))
		self.assertIn("meio do período", str(cm.exception))

	def test_requester_cannot_approve_their_own_salary_change(self):
		frappe.set_user(self.users["officer"])
		doc = self.make_change()
		sc.perform(doc, sc.SUBMIT)
		officer = frappe.get_doc("User", self.users["officer"])
		officer.append("roles", {"role": perms.HR_MANAGER})
		officer.save(ignore_permissions=True)
		frappe.set_user(self.users["officer"])
		doc.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			sc.perform(doc, sc.APPROVE)
		self.assertIn("pediu", str(cm.exception))

	def test_applying_closes_the_old_profile_and_opens_a_new_one(self):
		effective = self.next_period_start()
		doc = self.make_change(effective=effective)
		sc.perform(doc, sc.SUBMIT, user="Administrator")
		doc.reload()
		doc.db_set("requested_by", self.users["officer"], update_modified=False)
		doc.reload()
		sc.perform(doc, sc.APPROVE, user="Administrator")
		doc.reload()
		sc.perform(doc, sc.APPLY, user="Administrator")
		doc.reload()

		self.assertEqual(doc.status, sc.APPLIED)
		self.assertTrue(doc.created_profile)
		new_profile = frappe.get_doc("Isoft Salary Profile", doc.created_profile)
		self.assertEqual(getdate(new_profile.from_date), getdate(effective))
		self.assertAlmostEqual(flt(new_profile.base), 260000.00, places=2)
		self.assertAlmostEqual(flt(new_profile.food_allowance), 30000.00, places=2)

		old = frappe.get_doc("Isoft Salary Profile", self.profile.name)
		self.assertEqual(getdate(old.to_date), getdate(add_days(effective, -1)),
		                 "the previous profile must be closed the day before")

	def test_applying_twice_does_not_create_a_second_profile(self):
		doc = self.make_change()
		doc.db_set("status", sc.APPROVED, update_modified=False)
		doc.reload()
		first = sc.apply_change(doc)
		doc.reload()
		second = sc.apply_change(doc)
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Isoft Salary Profile",
		                                 {"employee": self.employee.name}), 2)

	def test_a_change_cannot_reach_into_an_already_processed_period(self):
		slip = self.make_slip(submit=False)
		start, _end = frappe.get_attr(
			"isoft_angola_hr.isoft_angola_hr.api._cycle_period")(getdate(slip.start_date))
		doc = self.make_change(effective=start)
		doc.db_set("status", sc.APPROVED, update_modified=False)
		doc.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			sc.apply_change(doc)
		self.assertIn("already covers", str(cm.exception))

	def test_the_new_profile_is_what_payroll_then_uses(self):
		"""The integration that matters: a salary change must actually change the pay."""
		effective = self.next_period_start()
		doc = self.make_change(effective=effective, new_base=260000)
		doc.db_set("status", sc.APPROVED, update_modified=False)
		doc.reload()
		sc.apply_change(doc)

		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
			get_active_profile,
		)

		resolved = get_active_profile(self.employee.name, add_days(effective, 5),
		                              company=self.company)
		self.assertAlmostEqual(flt(resolved.base), 260000.00, places=2)
		before = get_active_profile(self.employee.name, add_days(effective, -1),
		                            company=self.company)
		self.assertAlmostEqual(flt(before.base), 200000.00, places=2)


# --------------------------------------------------------------------------- #
# Salary advances
# --------------------------------------------------------------------------- #
class TestSalaryAdvance(HRFixture):
	def make_advance(self, amount=60000, installments=3, **kwargs):
		values = {
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"requested_amount": amount, "installments": installments,
			"reason": "Family emergency", "request_date": nowdate(),
			"recovery_start_date": self.start,
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def approved_advance(self, **kwargs):
		doc = self.make_advance(**kwargs)
		frappe.set_user(self.users["officer"])
		advances.perform(doc, advances.SUBMIT)
		frappe.set_user("Administrator")
		doc.reload()
		advances.perform(doc, advances.APPROVE, user="Administrator")
		doc.reload()
		return doc

	def test_approval_builds_a_schedule_that_sums_to_the_approved_amount(self):
		doc = self.approved_advance(amount=100000, installments=3)
		self.assertEqual(len(doc.schedule), 3)
		self.assertAlmostEqual(sum(flt(i.amount) for i in doc.schedule), 100000.00, places=2)
		self.assertAlmostEqual(flt(doc.outstanding_amount), 100000.00, places=2)

	def test_requester_cannot_approve_their_own_advance(self):
		frappe.set_user(self.users["officer"])
		doc = self.make_advance()
		advances.perform(doc, advances.SUBMIT)
		officer = frappe.get_doc("User", self.users["officer"])
		officer.append("roles", {"role": perms.PAYROLL_MANAGER})
		officer.save(ignore_permissions=True)
		frappe.set_user(self.users["officer"])
		doc.reload()
		with self.assertRaises(frappe.ValidationError) as cm:
			advances.perform(doc, advances.APPROVE)
		self.assertIn("pediu", str(cm.exception))

	def test_approved_amount_cannot_exceed_the_request(self):
		doc = self.make_advance(amount=50000)
		doc.approved_amount = 80000
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_only_one_open_advance_per_employee(self):
		self.approved_advance()
		second = self.make_advance()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError) as cm:
			advances.perform(second, advances.SUBMIT)
			advances.perform(second, advances.APPROVE)
		self.assertIn("already has an open salary advance", str(cm.exception))

	def test_disbursement_posts_to_the_ledger_and_is_idempotent(self):
		doc = self.approved_advance(amount=60000)
		frappe.set_user(self.users["finance"])
		advances.perform(doc, advances.DISBURSE)
		doc.reload()
		self.assertEqual(doc.status, advances.DISBURSED)
		self.assertTrue(doc.disbursement_entry)
		je = frappe.get_doc("Journal Entry", doc.disbursement_entry)
		self.assertEqual(je.docstatus, 1)
		self.assertAlmostEqual(flt(je.total_debit), 60000.00, places=2)
		self.assertEqual(advances.disburse(doc), doc.disbursement_entry,
		                 "disbursing twice must not pay twice")

	def test_payroll_recovers_the_installment(self):
		doc = self.approved_advance(amount=30000, installments=3)
		doc.db_set("status", advances.DISBURSED, update_modified=False)
		doc.reload()

		slip = self.make_slip(submit=False)
		self.assertAlmostEqual(flt(slip.advance_recovery), 10000.00, places=2)
		# Net pay falls by exactly the recovered installment.
		self.assertAlmostEqual(flt(slip.net_pay), 232948.00 - 10000.00, places=2)

		slip.submit()
		self.assertAlmostEqual(advances.outstanding_for(self.employee.name), 20000.00, places=2)

	def test_cancelling_payroll_gives_the_installment_back(self):
		doc = self.approved_advance(amount=30000, installments=3)
		doc.db_set("status", advances.DISBURSED, update_modified=False)
		slip = self.make_slip()
		self.assertAlmostEqual(advances.outstanding_for(self.employee.name), 20000.00, places=2)
		slip.cancel()
		self.assertAlmostEqual(advances.outstanding_for(self.employee.name), 30000.00, places=2,
		                       msg="a cancelled slip never recovered anything")

	def test_recovery_never_drives_net_pay_negative(self):
		"""The Phase 1 rule stands: an advance must not stop somebody's salary."""
		doc = self.approved_advance(amount=900000, installments=1)
		doc.db_set("status", advances.DISBURSED, update_modified=False)
		slip = self.make_slip(submit=False)
		self.assertGreaterEqual(flt(slip.net_pay), 0.0)
		self.assertGreater(flt(slip.advance_deferred), 0.0,
		                   "what could not be taken must stay outstanding, not vanish")
		self.assertAlmostEqual(
			flt(slip.advance_recovery) + flt(slip.advance_deferred), 900000.00, places=2)
		slip.submit()   # a zero net is allowed; a negative one is not

	def test_full_recovery_settles_the_advance(self):
		doc = self.approved_advance(amount=15000, installments=1)
		doc.db_set("status", advances.DISBURSED, update_modified=False)
		self.make_slip()
		doc.reload()
		self.assertEqual(doc.status, advances.SETTLED)
		self.assertAlmostEqual(flt(doc.outstanding_amount), 0.0, places=2)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
class TestEmployeeDocuments(HRFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for name, medical in ((PREFIX + " Passport", 0), (PREFIX + " Medical", 1)):
			if not frappe.db.exists("Isoft Document Type", name):
				frappe.get_doc({
					"doctype": "Isoft Document Type", "document_type": name,
					"requires_expiry": 1, "is_medical": medical,
					"is_confidential": medical,
				}).insert(ignore_permissions=True)
				cls._created.append(("Isoft Document Type", name))

	def make_document(self, doc_type=None, expiry=None):
		return frappe.get_doc({
			"doctype": "Isoft Employee Document", "employee": self.employee.name,
			"document_type": doc_type or (PREFIX + " Passport"),
			"document_number": "AO123456",
			"issue_date": add_days(getdate(nowdate()), -365),
			"expiry_date": expiry or add_days(getdate(nowdate()), 400),
		}).insert(ignore_permissions=True)

	def test_expiry_status_is_derived(self):
		self.assertEqual(self.make_document().status, "Valid")
		self.assertEqual(
			self.make_document(expiry=add_days(getdate(nowdate()), 10)).status, "Expiring")
		self.assertEqual(
			self.make_document(expiry=add_days(getdate(nowdate()), -10)).status, "Expired")

	def test_a_type_that_requires_expiry_refuses_a_document_without_one(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Isoft Employee Document", "employee": self.employee.name,
				"document_type": PREFIX + " Passport", "document_number": "X",
			}).insert(ignore_permissions=True)

	def test_a_medical_document_is_confidential_automatically(self):
		doc = self.make_document(doc_type=PREFIX + " Medical")
		self.assertEqual(doc.confidential, 1)

	def test_confidential_documents_are_hidden_from_non_hr_managers(self):
		self.make_document(doc_type=PREFIX + " Medical")
		self.make_document()
		frappe.set_user(self.users["officer"])
		visible = hr_api.employee_360(self.employee.name)["documents"]
		self.assertTrue(all(not d.get("confidential") for d in visible),
		                "a payroll officer must not see medical documents")

	def test_an_employee_never_sees_confidential_documents_in_self_service(self):
		self.make_document(doc_type=PREFIX + " Medical")
		self.make_document()
		frappe.db.set_value("Employee", self.employee.name, "user_id", self.employee_user)
		frappe.set_user(self.employee_user)
		docs = ess.my_documents()
		self.assertEqual(len(docs), 1)
		self.assertNotIn(PREFIX + " Medical", [d["document_type"] for d in docs])

	def test_the_expiry_sweep_updates_statuses(self):
		doc = self.make_document(expiry=add_days(getdate(nowdate()), 400))
		frappe.db.set_value("Isoft Employee Document", doc.name, "expiry_date",
		                    add_days(getdate(nowdate()), -1), update_modified=False)
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_employee_document.isoft_employee_document import (
			refresh_document_statuses,
		)

		refresh_document_statuses()
		self.assertEqual(frappe.db.get_value("Isoft Employee Document", doc.name, "status"),
		                 "Expired")


# --------------------------------------------------------------------------- #
# Employee Self-Service
# --------------------------------------------------------------------------- #
class TestEmployeeSelfService(HRFixture):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Employee", self.employee.name, "user_id", self.employee_user)

	def test_an_employee_sees_only_their_own_payslips(self):
		mine = self.make_slip()
		other = frappe.get_doc({
			"doctype": "Isoft Salary Slip", "employee": self.report_employee,
			"company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end,
		})
		frappe.set_user(self.employee_user)
		payslips = ess.my_payslips()
		self.assertIn(mine.name, [p["name"] for p in payslips])
		detail = ess.my_payslip(mine.name)
		self.assertAlmostEqual(flt(detail["net_pay"]), 232948.00, places=2)
		self.assertIn("Segurança Social", detail["explanation"]["social_security"]["label"])

	def test_another_employees_payslip_is_denied(self):
		frappe.db.set_value("Employee", self.report_employee, "user_id", None)
		other_slip = frappe.get_doc({
			"doctype": "Isoft Salary Slip", "employee": self.employee.name,
			"company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end,
		}).insert(ignore_permissions=True)
		other_slip.submit()
		frappe.db.set_value("Employee", self.employee.name, "user_id", None)
		frappe.db.set_value("Employee", self.report_employee, "user_id", self.employee_user)
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			ess.my_payslip(other_slip.name)

	def test_a_draft_payslip_is_never_visible(self):
		draft = self.make_slip(submit=False)
		frappe.set_user(self.employee_user)
		self.assertNotIn(draft.name, [p["name"] for p in ess.my_payslips()])
		with self.assertRaises(frappe.PermissionError):
			ess.my_payslip(draft.name)

	def test_an_employee_can_update_only_their_contact_details(self):
		frappe.set_user(self.employee_user)
		ess.update_my_profile({"cell_number": "+244923000000"})
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee.name, "cell_number"),
			"+244923000000")
		for field, value in (("designation", "CEO"), ("custom_nif", "999"),
		                     ("custom_iban", "AO0600060000010003713"),
		                     ("department", "Anything")):
			with self.assertRaises(frappe.PermissionError,
			                       msg="{0} must not be self-editable".format(field)):
				ess.update_my_profile({field: value})

	def test_bank_details_go_through_a_request(self):
		frappe.set_user(self.employee_user)
		name = ess.request_bank_change("AO06000600000100037139999", bank_name="BAI")
		self.assertEqual(frappe.db.get_value("Isoft Bank Change Request", name, "status"),
		                 "Pending Approval")
		# The employee record is untouched until HR approves.
		self.assertNotEqual(
			frappe.db.get_value("Employee", self.employee.name, "custom_iban"),
			"AO06000600000100037139999")

		frappe.set_user("Administrator")
		frappe.get_doc("Isoft Bank Change Request", name).approve()
		self.assertEqual(
			frappe.db.get_value("Employee", self.employee.name, "custom_iban"),
			"AO06000600000100037139999")

	def test_the_employee_cannot_approve_their_own_bank_change(self):
		frappe.set_user(self.employee_user)
		name = ess.request_bank_change("AO06000600000100037139999")
		hr_user = frappe.get_doc("User", self.employee_user)
		hr_user.append("roles", {"role": perms.HR_MANAGER})
		hr_user.save(ignore_permissions=True)
		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.get_doc("Isoft Bank Change Request", name).approve()
		self.assertIn("próprios dados bancários", str(cm.exception))

	def test_the_masked_iban_never_exposes_the_full_number(self):
		frappe.db.set_value("Employee", self.employee.name, "custom_iban",
		                    "AO06000600000100037131174")
		frappe.set_user(self.employee_user)
		profile = ess.my_profile()
		self.assertNotIn("AO06000600000100037131174", str(profile))
		self.assertEqual(profile["iban_masked"], "AO06…1174")

	def test_payroll_endpoints_stay_denied_to_the_employee(self):
		from isoft_angola_hr.isoft_angola_hr import api

		frappe.set_user(self.employee_user)
		with self.assertRaises(frappe.PermissionError):
			api.list_salary_slips()
		with self.assertRaises(frappe.PermissionError):
			api.payroll_preview(self.company, self.start, self.end)


# --------------------------------------------------------------------------- #
# Manager Self-Service
# --------------------------------------------------------------------------- #
class TestManagerSelfService(HRFixture):
	def setUp(self):
		super().setUp()
		frappe.db.set_value("Employee", self.manager_employee, "user_id", self.manager_user)

	def test_the_team_is_derived_from_reports_to(self):
		frappe.set_user(self.manager_user)
		members = mss.team()
		self.assertIn(self.report_employee, members)
		self.assertIn(self.employee.name, members)

	def test_indirect_reports_are_excluded_by_default(self):
		grandchild = self._employee("Grandchild", reports_to=self.report_employee)
		frappe.set_user(self.manager_user)
		self.assertNotIn(grandchild, mss.team())
		self.assertIn(grandchild, mss.team(include_indirect=True))

	def test_an_unrelated_employee_is_denied(self):
		outsider = self._employee("Outsider")
		frappe.set_user(self.manager_user)
		with self.assertRaises(frappe.PermissionError):
			mss.team_member(outsider)

	def test_a_manager_does_not_see_compensation_by_default(self):
		frappe.set_user(self.manager_user)
		self.assertFalse(mss.can_see_compensation(),
		                 "managing people must not imply seeing pay")
		member = mss.team_member(self.report_employee)
		self.assertFalse(member["compensation_visible"])
		self.assertNotIn("salary", str(member).lower())

	def test_a_manager_with_a_payroll_role_may_see_compensation(self):
		frappe.set_user(self.users["officer"])
		self.assertTrue(mss.can_see_compensation())

	def test_team_dashboard_is_scoped(self):
		frappe.set_user(self.manager_user)
		data = mss.dashboard()
		self.assertEqual(data["team_size"], len(mss.team()))
		self.assertFalse(data["compensation_visible"])


# --------------------------------------------------------------------------- #
# HR readiness, onboarding and timeline
# --------------------------------------------------------------------------- #
class TestHRReadiness(HRFixture):
	def test_onboarding_checklist_flags_what_is_missing(self):
		result = lifecycle.onboarding_checklist(self.report_employee)
		keys = {i["key"]: i for i in result["items"]}
		self.assertEqual(keys["contract"]["status"], lifecycle.BLOCKED)
		self.assertEqual(keys["salary_profile"]["status"], lifecycle.BLOCKED)
		self.assertEqual(result["status"], lifecycle.BLOCKED)

	def test_a_fully_onboarded_employee_is_ready(self):
		self.approved_contract(start=add_days(getdate(nowdate()), -30),
		                       end=add_days(getdate(nowdate()), 300))
		result = lifecycle.onboarding_checklist(self.employee.name)
		keys = {i["key"]: i for i in result["items"]}
		self.assertTrue(keys["contract"]["ok"])
		self.assertTrue(keys["salary_profile"]["ok"])
		self.assertNotEqual(result["status"], lifecycle.BLOCKED)

	def test_hr_readiness_counts_the_blockers(self):
		report = lifecycle.hr_readiness(self.company)
		codes = {b["code"] for b in report["blockers"]}
		self.assertIn("HR-001", codes)
		self.assertEqual(report["status"], lifecycle.BLOCKED)

	def test_timeline_is_built_from_the_documents(self):
		contract = self.approved_contract(start=add_days(getdate(nowdate()), -30),
		                                  end=add_days(getdate(nowdate()), 300))
		events = lifecycle.timeline(self.employee.name)
		kinds = {e["kind"] for e in events}
		self.assertIn("join", kinds)
		self.assertIn("contract", kinds)
		self.assertIn(contract.name, [e.get("name") for e in events])

	def test_the_timeline_hides_salary_amounts_from_users_without_payroll_access(self):
		effective = self.next_period_start()
		change = frappe.get_doc({
			"doctype": "Isoft Salary Change", "employee": self.employee.name,
			"change_type": "Merit Increase", "new_base": 240000,
			"effective_date": effective, "reason": "Review",
		}).insert(ignore_permissions=True)
		change.db_set("status", "Applied", update_modified=False)

		frappe.set_user(self.users["finance"])      # Finance: may post pay, may not read it
		events = lifecycle.timeline(self.employee.name)
		salary_events = [e for e in events if e["kind"] == "salary"]
		self.assertTrue(salary_events)
		self.assertIn("hidden", salary_events[0]["detail"].lower())

		frappe.set_user(self.users["officer"])      # Payroll Officer: may see amounts
		events = lifecycle.timeline(self.employee.name)
		salary_events = [e for e in events if e["kind"] == "salary"]
		self.assertIn("240000", salary_events[0]["detail"].replace(",", "").replace(".0", ""))

	def test_approval_inbox_gathers_every_pending_decision(self):
		doc = self.make_contract()
		frappe.set_user(self.users["hruser"])
		contracts.perform(doc, contracts.SUBMIT)
		frappe.set_user("Administrator")
		inbox = lifecycle.pending_approvals(self.company)
		self.assertIn(doc.name, [row["name"] for row in inbox])

	def test_hr_dashboard_returns_operational_numbers(self):
		data = lifecycle.hr_dashboard(self.company)
		for key in ("active_employees", "on_leave_today", "attendance_exceptions",
		            "contracts_expiring", "probations_due", "documents_expiring",
		            "pending_approvals", "headcount"):
			self.assertIn(key, data)
		self.assertGreater(data["active_employees"], 0)

	def test_readiness_is_denied_to_the_employee_role(self):
		frappe.set_user(self.users["employee_only"])
		with self.assertRaises(frappe.PermissionError):
			lifecycle.hr_readiness(self.company)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
class TestHRNotifications(HRFixture):
	def test_contract_expiry_alerts_are_sent_once_per_threshold(self):
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

		self.approved_contract(start=add_days(getdate(nowdate()), -300),
		                       end=add_days(getdate(nowdate()), 25))
		first = notify.contract_expiry_alerts()
		self.assertGreater(first, 0)
		self.assertEqual(notify.contract_expiry_alerts(), 0,
		                 "the same threshold must never alert twice")

	def test_the_daily_sweep_runs_end_to_end(self):
		from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

		result = notify.run_daily_alerts()
		for key in ("contract_expiry", "probation", "document_expiry", "pending_approvals"):
			self.assertIn(key, result)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
class TestHRReports(HRFixture):
	def test_contract_expiry_report_lists_the_contract(self):
		from isoft_angola_hr.isoft_angola_hr.report.employee_contract_expiry import (
			employee_contract_expiry as report,
		)

		doc = self.approved_contract(start=add_days(getdate(nowdate()), -300),
		                             end=add_days(getdate(nowdate()), 30))
		columns, data = report.execute({"company": self.company})
		row = [r for r in data if r["contract"] == doc.name]
		self.assertTrue(row)
		self.assertEqual(row[0]["days_left"], 30)

	def test_master_data_completeness_reports_flags_not_values(self):
		from isoft_angola_hr.isoft_angola_hr.report.employee_master_data_completeness import (
			employee_master_data_completeness as report,
		)

		frappe.db.set_value("Employee", self.employee.name, "custom_nif", "5417000000")
		columns, data = report.execute({"company": self.company})
		row = [r for r in data if r["employee"] == self.employee.name][0]
		self.assertEqual(row["nif"], 1)
		self.assertNotIn("5417000000", str(row), "the report must not print the value")

	def test_salary_change_history_requires_payroll_permission(self):
		from isoft_angola_hr.isoft_angola_hr.report.salary_change_history import (
			salary_change_history as report,
		)

		frappe.set_user(self.users["finance"])      # no salary-read permission
		with self.assertRaises(frappe.PermissionError):
			report.execute({"company": self.company})
		frappe.set_user(self.users["officer"])
		report.execute({"company": self.company})

	def test_advance_balance_report_shows_the_outstanding_amount(self):
		from isoft_angola_hr.isoft_angola_hr.report.employee_advance_balance import (
			employee_advance_balance as report,
		)

		doc = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"requested_amount": 40000, "approved_amount": 40000, "installments": 2,
			"reason": "Test", "request_date": nowdate(), "recovery_start_date": self.start,
		}).insert(ignore_permissions=True)
		doc.build_schedule()
		doc.db_set("status", advances.DISBURSED, update_modified=False)
		doc.db_set("outstanding_amount", 40000, update_modified=False)

		columns, data = report.execute({"company": self.company})
		row = [r for r in data if r.get("name") == doc.name]
		self.assertTrue(row)
		self.assertAlmostEqual(flt(row[0]["outstanding_amount"]), 40000.00, places=2)

	def test_document_expiry_report_hides_confidential_rows(self):
		from isoft_angola_hr.isoft_angola_hr.report.employee_document_expiry import (
			employee_document_expiry as report,
		)

		if not frappe.db.exists("Isoft Document Type", PREFIX + " Medical2"):
			frappe.get_doc({"doctype": "Isoft Document Type",
			                "document_type": PREFIX + " Medical2",
			                "requires_expiry": 1, "is_medical": 1}).insert(
				ignore_permissions=True)
			self._created.append(("Isoft Document Type", PREFIX + " Medical2"))
		frappe.get_doc({
			"doctype": "Isoft Employee Document", "employee": self.employee.name,
			"document_type": PREFIX + " Medical2",
			"expiry_date": add_days(getdate(nowdate()), 5),
		}).insert(ignore_permissions=True)

		frappe.set_user(self.users["hruser"])       # HR User, not HR Manager
		columns, data = report.execute({"company": self.company})
		self.assertTrue(all(not r.get("confidential") for r in data))
