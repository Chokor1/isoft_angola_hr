# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""One HR Manager, three workflows, two languages.

Three complaints arrived together and shared one shape: the HR Manager could not approve
their own contract, could not see Approve on their own salary advance, and was shown
Portuguese error text while their Frappe language was English.

Two of the three turned out to be configuration - ``require_separate_*_approval`` was
still 1 on that site, and the code was doing exactly what the switch said. These tests
pin BOTH positions of each switch, so "it is configuration" stays a verifiable claim
rather than an excuse, and so no future change can quietly make a switch stop mattering.

The third was a real defect, in two parts:

  * the source strings were written in Portuguese. ``_()`` looks a string up in the
    user's catalogue and returns the SOURCE when there is no entry, so a Portuguese
    msgid renders as Portuguese for an English user no matter what their preference
    says. English msgid + a pt.csv row is what makes a preference mean anything.
  * the mid-period salary-change message told the user to use the start of the period
    the date already falls inside - a date in the PAST. Told to fix a mid-period change,
    HR would have back-dated it further, into a period that may already have been paid.

What these tests must never be allowed to do is prove the workflows work by removing the
security around them. Every relaxation here is a single switch; the capability, the
workflow state, the company scope and the Finance boundary are asserted to survive it.
"""

import csv
import io
import os
from unittest import mock

import frappe
from frappe.utils import add_days, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import (
	advances,
	contracts,
	permissions as perms,
	salary_change,
)
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

PT_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
	os.path.abspath(__file__)))), "translations", "pt.csv")

SELF_APPROVAL_TITLE = "Self-Approval Blocked"
MID_PERIOD_MSG = (
	"The salary change for {0} must take effect at the start of a payroll processing "
	"period. {1} falls in the middle of the period that starts on {2}. The payroll "
	"engine cannot split this period between two Salary Profiles. Use {3}.")


def catalogue():
	return {r[0]: r[1] for r in csv.reader(io.open(PT_CSV, encoding="utf-8")) if len(r) == 2}


class SingleManagerFixture(PayrollFixture):
	"""An HR Manager, an HR User and an Employee, plus switch control."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.manager = cls._user("p31.manager@example.com", ["HR Manager"])
		cls.hr_user = cls._user("p31.hruser@example.com", ["HR User"])
		cls.outsider = cls._user("p31.outsider@example.com", [])
		ct = frappe.get_all("Isoft Contract Type", limit_page_length=1)
		cls.contract_type = ct[0].name if ct else None

	@classmethod
	def _user(cls, email, roles):
		if not frappe.db.exists("User", email):
			doc = frappe.get_doc({
				"doctype": "User", "email": email, "first_name": email.split("@")[0],
				"send_welcome_email": 0, "enabled": 1,
				"roles": [{"role": r} for r in roles],
			}).insert(ignore_permissions=True)
			cls._created.append(("User", doc.name))
			frappe.db.commit()
		return email

	def switch(self, field, value):
		"""Set one separation switch for the duration of a test, then put it back."""
		self.addCleanup(frappe.clear_cache)
		self.pin_settings(**{field: value})   # restores the stored row, or its absence
		frappe.clear_cache()

	def as_user(self, user):
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user(user)

	def new_contract(self, user=None):
		self.as_user(user or self.manager)
		doc = frappe.get_doc({
			"doctype": "Isoft Employment Contract", "employee": self.employee.name,
			"company": self.company, "contract_type": self.contract_type,
			"start_date": "2026-01-01", "notice_days": 30, "is_open_ended": 1,
		}).insert()
		contracts.perform(doc, contracts.SUBMIT)
		doc.reload()
		return doc

	def new_advance(self, user=None):
		self.as_user(user or self.manager)
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": self.employee.name,
			"company": self.company, "requested_amount": 50000, "installments": 2,
			"request_date": "2026-08-01", "recovery_start_date": "2026-09-01",
			"reason": PREFIX + " advance",
		}).insert()
		advances.perform(doc, advances.SUBMIT)
		doc.reload()
		return doc


# ---------------------------------------------------------------------------
# (1)(2) Contract
# ---------------------------------------------------------------------------

class TestContractSelfApproval(SingleManagerFixture):
	def test_1_the_manager_approves_their_own_contract_when_separation_is_off(self):
		self.switch("require_separate_contract_approval", 0)
		doc = self.new_contract()
		self.assertEqual(doc.get("submitted_by"), self.manager)
		self.assertIn(contracts.APPROVE, contracts.allowed_actions(doc))
		contracts.perform(doc, contracts.APPROVE)
		doc.reload()
		self.assertEqual(contracts.state_of(doc), "Active")

	def test_2_the_manager_is_refused_when_separation_is_on(self):
		self.switch("require_separate_contract_approval", 1)
		doc = self.new_contract()
		self.assertNotIn(contracts.APPROVE, contracts.allowed_actions(doc),
		                 "the button must not be offered either")
		with self.assertRaises(frappe.ValidationError) as cm:
			contracts.perform(doc, contracts.APPROVE)
		self.assertIn("must be approved by a different user", str(cm.exception))
		doc.reload()
		self.assertEqual(contracts.state_of(doc), "Pending Approval",
		                 "a refused approval must not move the document")

	def test_16_approval_records_who_approved_it_and_when(self):
		self.switch("require_separate_contract_approval", 0)
		doc = self.new_contract()
		contracts.perform(doc, contracts.APPROVE)
		doc.reload()
		self.assertEqual(doc.get("approved_by"), self.manager)
		self.assertTrue(doc.get("approved_on") or doc.get("approved_at"),
		                "an approval with no timestamp is not an audit trail")


# ---------------------------------------------------------------------------
# (3)(4)(5) Salary Advance
# ---------------------------------------------------------------------------

class TestAdvanceSelfApproval(SingleManagerFixture):
	def test_3_the_manager_approves_their_own_advance_when_separation_is_off(self):
		self.switch("require_separate_advance_approval", 0)
		doc = self.new_advance()
		self.assertEqual(doc.get("requested_by"), self.manager)
		self.assertIn(advances.APPROVE, advances.allowed_actions(doc),
		              "Approve Advance must be offered on the screen")
		advances.perform(doc, advances.APPROVE)
		doc.reload()
		self.assertEqual(advances.state_of(doc), "Approved")

	def test_4_the_manager_is_refused_when_separation_is_on(self):
		self.switch("require_separate_advance_approval", 1)
		doc = self.new_advance()
		self.assertNotIn(advances.APPROVE, advances.allowed_actions(doc))
		with self.assertRaises(frappe.ValidationError) as cm:
			advances.perform(doc, advances.APPROVE)
		self.assertIn("must be approved by a different user", str(cm.exception))
		doc.reload()
		self.assertEqual(advances.state_of(doc), "Pending Approval")

	def test_5_the_manager_can_never_disburse_the_advance_they_approved(self):
		"""Relaxing the HR approval must not move the Finance boundary."""
		self.switch("require_separate_advance_approval", 0)
		doc = self.new_advance()
		advances.perform(doc, advances.APPROVE)
		doc.reload()
		self.assertNotIn(advances.DISBURSE, advances.allowed_actions(doc),
		                 "Disburse must not be offered to HR")
		self.assertFalse(perms.can(perms.ADVANCE_DISBURSE, user=self.manager),
		                 "HR Manager must not hold the disbursement capability")
		with self.assertRaises(frappe.PermissionError):
			advances.assert_transition(doc, advances.DISBURSE, user=self.manager)

	def test_17_advance_approval_records_who_approved_it_and_when(self):
		self.switch("require_separate_advance_approval", 0)
		doc = self.new_advance()
		advances.perform(doc, advances.APPROVE)
		doc.reload()
		self.assertEqual(doc.get("approved_by"), self.manager)
		self.assertTrue(doc.get("approved_at"))


# ---------------------------------------------------------------------------
# (6)(18) security survives the relaxation
# ---------------------------------------------------------------------------

class TestSecuritySurvives(SingleManagerFixture):
	def setUp(self):
		super().setUp()
		# These tests assert the documented approval policy, whatever the site runs.
		self.pin_settings(**self.DOCUMENTED_APPROVAL_POLICY)

	def test_6_a_role_without_the_capability_is_refused_even_with_separation_off(self):
		"""The switch removes an identity comparison, not the permission model."""
		self.switch("require_separate_contract_approval", 0)
		self.switch("require_separate_advance_approval", 0)
		contract = self.new_contract()
		advance = self.new_advance()
		for user in (self.hr_user, self.outsider):
			self.assertFalse(perms.can(perms.CONTRACT_APPROVE, user=user), user)
			self.assertFalse(perms.can(perms.ADVANCE_APPROVE, user=user), user)
			self.assertNotIn(contracts.APPROVE, contracts.allowed_actions(contract, user=user))
			self.assertNotIn(advances.APPROVE, advances.allowed_actions(advance, user=user))
			with self.assertRaises(frappe.PermissionError):
				contracts.assert_transition(contract, contracts.APPROVE, user=user)
			with self.assertRaises(frappe.PermissionError):
				advances.assert_transition(advance, advances.APPROVE, user=user)

	def test_18_workflow_state_cannot_be_bypassed_by_calling_the_action_directly(self):
		"""A draft is not approvable however the call arrives."""
		self.switch("require_separate_contract_approval", 0)
		self.as_user(self.manager)
		draft = frappe.get_doc({
			"doctype": "Isoft Employment Contract", "employee": self.employee.name,
			"company": self.company, "contract_type": self.contract_type,
			"start_date": "2026-01-01", "notice_days": 30, "is_open_ended": 1,
		}).insert()
		with self.assertRaises(frappe.ValidationError) as cm:
			contracts.perform(draft, contracts.APPROVE)
		self.assertIn("Draft", str(cm.exception))
		draft.reload()
		self.assertEqual(contracts.state_of(draft), "Draft", "the document must not move")

	def test_the_payroll_switches_are_untouched_by_the_hr_ones(self):
		"""Relaxing HR must never relax payroll approval or payment."""
		self.switch("require_separate_contract_approval", 0)
		self.switch("require_separate_advance_approval", 0)
		self.switch("require_separate_salary_change_approval", 0)
		from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

		self.assertTrue(wf.requires_separate_approval(),
		                "payroll approval separation must not follow the HR switches")
		self.assertTrue(wf.requires_separate_payment_approval())


# ---------------------------------------------------------------------------
# (7)(8)(9) Salary Change effective date
# ---------------------------------------------------------------------------

class TestSalaryChangeEffectiveDate(SingleManagerFixture):
	def cycle(self, start_day):
		before = frappe.db.get_single_value("Isoft HR Settings", "payroll_cycle_start_day")
		frappe.db.set_single_value("Isoft HR Settings", "payroll_cycle_start_day", start_day)
		frappe.clear_cache()
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.set_single_value, "Isoft HR Settings",
		                "payroll_cycle_start_day", before)

	def test_7_a_period_start_is_accepted(self):
		self.cycle(1)
		self.assertTrue(salary_change.assert_effective_date_is_a_period_boundary(
			self.employee.name, "2026-09-01"))
		self.cycle(23)
		self.assertTrue(salary_change.assert_effective_date_is_a_period_boundary(
			self.employee.name, "2026-08-23"))

	def test_8_a_mid_period_date_is_rejected(self):
		self.cycle(1)
		with self.assertRaises(frappe.ValidationError) as cm:
			salary_change.assert_effective_date_is_a_period_boundary(
				self.employee.name, "2026-08-23", employee_name="Ali Bassem Yassine")
		message = frappe.utils.strip_html(str(cm.exception))
		self.assertIn("Ali Bassem Yassine", message)
		self.assertIn("23-08-2026", message, "must name the date the user chose")
		self.assertIn("01-08-2026", message, "must name the period it falls inside")

	def test_9_the_next_valid_date_is_computed_from_the_configured_cycle(self):
		"""The old message offered the start of the period the date already sits in -
		a date in the past. It must offer the NEXT boundary, from the real cycle."""
		self.cycle(1)
		self.assertEqual(salary_change.next_period_start("2026-08-23"), getdate("2026-09-01"))
		self.assertEqual(salary_change.next_period_start("2026-08-01"), getdate("2026-09-01"))
		with self.assertRaises(frappe.ValidationError) as cm:
			salary_change.assert_effective_date_is_a_period_boundary(
				self.employee.name, "2026-08-23")
		self.assertIn("01-09-2026", frappe.utils.strip_html(str(cm.exception)))

		self.cycle(23)
		self.assertEqual(salary_change.next_period_start("2026-08-25"), getdate("2026-09-23"))
		with self.assertRaises(frappe.ValidationError) as cm:
			salary_change.assert_effective_date_is_a_period_boundary(
				self.employee.name, "2026-08-25")
		message = frappe.utils.strip_html(str(cm.exception))
		self.assertIn("23-09-2026", message)
		self.assertNotIn("01-09-2026", message, "the calendar month is not this site's cycle")

	def test_the_offered_date_is_never_in_the_past(self):
		for start_day in (1, 23):
			self.cycle(start_day)
			for day in range(2, 28):
				effective = getdate("2026-08-%02d" % day)
				nxt = salary_change.next_period_start(effective)
				self.assertGreater(nxt, effective,
				                   "cycle %s: offered %s for %s" % (start_day, nxt, effective))
			self.doCleanups()


# ---------------------------------------------------------------------------
# (10)-(15) localisation
# ---------------------------------------------------------------------------

class TestLocalisation(SingleManagerFixture):
	def in_language(self, lang):
		"""Render as a user whose Frappe language is `lang`."""
		from frappe import translate

		before = getattr(frappe.local, "lang", None)
		translate.clear_cache()
		frappe.local.lang = lang
		frappe.local.lang_full_dict = None

		def restore():
			frappe.local.lang = before
			frappe.local.lang_full_dict = None
			translate.clear_cache()

		self.addCleanup(restore)

	def mid_period_message(self, lang):
		self.in_language(lang)
		before = frappe.db.get_single_value("Isoft HR Settings", "payroll_cycle_start_day")
		frappe.db.set_single_value("Isoft HR Settings", "payroll_cycle_start_day", 1)
		frappe.clear_cache()
		self.addCleanup(frappe.clear_cache)
		self.addCleanup(frappe.db.set_single_value, "Isoft HR Settings",
		                "payroll_cycle_start_day", before)
		with self.assertRaises(frappe.ValidationError) as cm:
			salary_change.assert_effective_date_is_a_period_boundary(
				self.employee.name, "2026-08-23", employee_name="Ali Bassem Yassine")
		return frappe.utils.strip_html(str(cm.exception))

	def test_10_the_validation_message_is_portuguese_for_a_portuguese_user(self):
		message = self.mid_period_message("pt")
		self.assertIn("alteração salarial", message)
		self.assertIn("período de processamento", message)
		self.assertNotIn("must take effect", message)

	def test_11_the_validation_message_is_english_for_an_english_user(self):
		message = self.mid_period_message("en")
		self.assertIn("must take effect at the start of a payroll processing period", message)
		self.assertNotIn("alteração", message)

	def test_12_the_same_message_differs_by_language_and_keeps_the_data_intact(self):
		"""Only the wording is translated; names, dates and numbers are not."""
		pt = self.mid_period_message("pt")
		self.doCleanups()
		en = self.mid_period_message("en")
		self.assertNotEqual(pt, en)
		for literal in ("Ali Bassem Yassine", "23-08-2026", "01-08-2026", "01-09-2026"):
			self.assertIn(literal, pt, literal)
			self.assertIn(literal, en, literal)

	def test_13_the_frappe_language_decides_not_the_source_files_language(self):
		"""The defect this pins: a Portuguese SOURCE string ignores the preference,
		because `_()` returns the source when the catalogue has no entry."""
		self.in_language("en")
		for source in (
			"You cannot approve a contract you prepared. A contract submitted by {0} must "
			"be approved by a different user.",
			"You cannot approve an advance you requested. An advance requested by {0} must "
			"be approved by a different user.",
			"You cannot approve a salary change you requested. A salary change requested "
			"by {0} must be approved by a different user.",
		):
			self.assertEqual(frappe._(source), source,
			                 "an English user must get the English source unchanged")

	def test_14_no_workflow_refusal_shows_english_to_a_portuguese_user(self):
		self.switch("require_separate_contract_approval", 1)
		self.switch("require_separate_advance_approval", 1)
		contract = self.new_contract()
		advance = self.new_advance()
		self.in_language("pt")
		for perform, doc, action in ((contracts.perform, contract, contracts.APPROVE),
		                             (advances.perform, advance, advances.APPROVE)):
			with self.assertRaises(frappe.ValidationError) as cm:
				perform(doc, action)
			message = frappe.utils.strip_html(str(cm.exception))
			self.assertIn("Não pode aprovar", message)
			self.assertNotIn("must be approved by a different user", message)

	def test_15_the_api_returns_the_same_localised_text_the_service_raises(self):
		"""The screen and the endpoint must not disagree about the wording."""
		from isoft_angola_hr.isoft_angola_hr import hr_api

		self.switch("require_separate_contract_approval", 1)
		contract = self.new_contract()
		self.in_language("pt")
		with self.assertRaises(frappe.ValidationError) as service:
			contracts.perform(contract, contracts.APPROVE)
		with self.assertRaises(frappe.ValidationError) as endpoint:
			hr_api.contract_action(name=contract.name, action="approve")
		self.assertEqual(frappe.utils.strip_html(str(service.exception)),
		                 frappe.utils.strip_html(str(endpoint.exception)))

	def test_every_message_this_fix_touched_has_a_portuguese_entry(self):
		"""A guard against re-introducing a Portuguese source string: the catalogue must
		carry the English msgid, which is only possible if the source IS English."""
		cat = catalogue()
		for msgid in (
			MID_PERIOD_MSG,
			"Effective Date Mid-Period",
			SELF_APPROVAL_TITLE,
			"You cannot approve a contract you prepared. A contract submitted by {0} must "
			"be approved by a different user.",
			"You cannot approve an advance you requested. An advance requested by {0} must "
			"be approved by a different user.",
			"You cannot approve a salary change you requested. A salary change requested "
			"by {0} must be approved by a different user.",
			"You cannot approve a change to your own bank details.",
			"Approve Contract", "Reject Advance", "Apply Salary Change",
		):
			self.assertIn(msgid, cat, "no pt.csv entry for %r" % msgid[:60])
			self.assertTrue(cat[msgid].strip(), msgid[:40])

	def test_no_source_string_in_the_touched_services_is_portuguese(self):
		"""Portuguese belongs in the catalogue, never in a msgid."""
		import re

		marker = re.compile(r"\b(n[aã]o|per[ií]odo|altera[cç][aã]o|processamento|"
		                    r"aprovar|recibo|colaborador)\b", re.I)
		call = re.compile(r"""(?:frappe\._|[^\w.]_)\(\s*"((?:[^"\\]|\\.)*)\"""")
		cont = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"')
		base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		offenders = []
		for rel in ("services/contracts.py", "services/advances.py",
		            "services/salary_change.py", "services/payroll_workflow.py",
		            "services/payroll_reconciliation.py", "services/hr_notifications.py"):
			lines = io.open(os.path.join(base, rel), encoding="utf-8").read().splitlines()
			for i, line in enumerate(lines):
				for m in call.finditer(line):
					text = m.group(1)
					j = i + 1
					while j < len(lines) and cont.match(lines[j]) and len(text) < 500:
						text += cont.match(lines[j]).group(1)
						j += 1
					if marker.search(text):
						offenders.append("%s:%s %s" % (rel, i + 1, text[:70]))
		self.assertEqual(offenders, [], "Portuguese source strings are back:\n" +
		                 "\n".join(offenders))
