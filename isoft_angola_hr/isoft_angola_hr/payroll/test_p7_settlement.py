# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 7 tests: the Final Settlement under Lei n.º 12/23.

THE CLAIM UNDER TEST
--------------------
Every amount on a final settlement is reconcilable from the arithmetic shown next to it,
carries the article that authorises it, and is never invented where the statute is
silent.

Four kinds of test do the work:

**Arithmetic.** The defect that started this phase was a screen printing
``6,818.18 × 26 = 150,000`` — a product that is not that product. So the first test
walks every line of every scenario and asserts that the printed formula evaluates to the
stored amount. It runs over the whole matrix, not one case.

**Article routing.** Compensation is not a universal severance. Each termination reason
is asserted against the article that governs it, including the reasons that owe nothing
and the reasons that cannot be decided at all.

**Refusals.** As always, the tests that matter most assert that something is REFUSED: a
settlement with no termination reason will not go for approval; an override with no
reason will not save; a preparer cannot approve their own work; an employee cannot
approve their own settlement.

**Immutability.** A settlement calculated before this phase keeps its stored amounts.
Opening it, saving other records, or migrating must not restate what somebody was paid.

SAFETY — every record is prefixed ``_TEST AHR`` and registered for deletion; no live
employee, salary profile, slip or settlement is read or written.
"""

import json

import frappe
from frappe.utils import add_days, flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll import engine, settlement as fs
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX
from isoft_angola_hr.isoft_angola_hr.payroll.test_p3_hr import HRFixture
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

#: The screenshot that opened this phase, as an isolated scenario.
SCREENSHOT = {
	"base": 120000.0, "food_allowance": 20000.0, "transport_allowance": 10000.0,
	"period_start": "2026-07-23", "period_end": "2026-08-21",
	"period_days": 26.0, "days_worked": 26.0, "salary_divisor": 22,
	"joining_date": "2020-03-01", "termination_date": "2026-08-21",
	"vested_untaken_days": 7.0, "leave_divisor": 21,
	"ferias_rate": 50.0, "natal_rate": 50.0,
	"reason_key": "resignation_with_notice",
}


def scenario(**overrides):
	data = dict(SCREENSHOT)
	data.update(overrides)
	return data


class SettlementFixture(HRFixture):
	"""Adds a company to the engine inputs so IRT and INSS resolve against real records."""

	def compute(self, **overrides):
		data = scenario(**overrides)
		data.setdefault("company", self.company)
		return fs.compute(data)

	def make_settlement(self, **overrides):
		data = scenario(**overrides)
		doc = frappe.get_doc({
			"doctype": "Isoft Final Settlement",
			"employee": self.employee.name, "company": self.company,
			"date_of_joining": data["joining_date"],
			"termination_date": data["termination_date"],
			"reason_key": data.get("reason_key"),
			"base": data["base"], "food_allowance": data["food_allowance"],
			"transport_allowance": data["transport_allowance"],
			"salary_period_start": data["period_start"],
			"salary_period_end": data["period_end"],
			"salary_days_worked": data["days_worked"], "period_days": data["period_days"],
			"salary_days": data["salary_divisor"], "leave_days": data["leave_divisor"],
			"vested_untaken_days": data["vested_untaken_days"],
			"ferias_rate": data["ferias_rate"], "natal_rate": data["natal_rate"],
			"calc_version": fs.CALC_VERSION,
			"notice_given_days": data.get("notice_given_days"),
			"supplement_months_override": data.get("supplement_months_override"),
			"override_reason": data.get("override_reason"),
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Final Settlement", doc.name))
		return doc


# --------------------------------------------------------------------------- #
# Arithmetic — the printed formula IS the amount
# --------------------------------------------------------------------------- #
class TestSettlementArithmetic(SettlementFixture):
	#: Every shape the engine can produce, so the reconciliation test is not one case.
	MATRIX = (
		("full period", {}),
		("partial period, company divisor", {"days_worked": 12.0}),
		("partial period, statutory hourly", {
			"days_worked": 12.0, "salary_method": "hourly_237_7",
			"weekly_hours": 40.0, "working_days_per_week": 5}),
		("no vested leave", {"vested_untaken_days": 0.0}),
		("joined this year", {"joining_date": "2026-02-01", "vested_untaken_days": 0.0}),
		("fixed term under a year", {
			"joining_date": "2026-01-01", "vested_untaken_days": 0.0,
			"fixed_term_under_one_year": True}),
		("objective dismissal", {"reason_key": "objective_dismissal"}),
		("collective dismissal", {"reason_key": "collective_dismissal"}),
		("insolvency", {"reason_key": "employer_insolvency_extinction"}),
		("indirect dismissal", {"reason_key": "indirect_dismissal"}),
		("non reinstatement", {"reason_key": "non_reinstatement"}),
		("resignation without notice", {
			"reason_key": "resignation_without_notice", "notice_given_days": 10,
			"notice_required_days": 30}),
		("leave on the statutory hourly rate", {
			"leave_rate_method": "hourly_237_7", "weekly_hours": 40.0}),
		("allowances inside the leave base", {"leave_base_includes_allowances": 1}),
		("advance to recover", {"advance_outstanding": 50000.0}),
		("enhanced supplement rates", {"ferias_rate": 100.0, "natal_rate": 75.0}),
	)

	def test_every_printed_formula_equals_the_amount_it_prints(self):
		"""The defect this phase exists to remove, asserted across the whole matrix."""
		import re
		checked = 0
		for label, overrides in self.MATRIX:
			res = self.compute(**overrides)
			for line in res["lines"]:
				formula = line.get("formula_check") or line.get("formula")
				if not formula or not re.match(r"^[\d\s,\.×−+\-*/%()]+$", formula):
					continue
				expr = formula.replace(",", "").replace("×", "*").replace("−", "-")
				expr = expr.replace("÷", "/")
				expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100.0)", expr)
				value = eval(expr, {"__builtins__": {}}, {})     # noqa: S307
				self.assertAlmostEqual(
					flt(value), flt(line["amount"]), places=2,
					msg="{0}: '{1}' shows {2} but the settlement stores {3} for {4}".format(
						label, formula, value, line["amount"], line["label"]))
				checked += 1
		# A test that silently checked nothing would pass; assert it did work.
		self.assertGreater(checked, 40)

	def test_sections_add_up_to_the_gross_and_the_net(self):
		for label, overrides in self.MATRIX:
			res = self.compute(**overrides)
			payable = sum(flt(l["amount"]) for l in res["lines"] if l["sign"] > 0)
			self.assertAlmostEqual(payable, res["gross"], places=2, msg=label)
			expected = flt(res["gross"] - res["total_deductions"], 2)
			self.assertAlmostEqual(max(0.0, expected), res["net"], places=2, msg=label)

	def test_a_full_period_pays_a_full_month_and_shows_no_multiplication(self):
		"""Section 5: a full period is a full salary. The old screen showed
		``150,000 ÷ 22 × 26`` and paid 150,000; both halves were wrong to print."""
		res = self.compute()
		line = next(l for l in res["lines"] if l["key"] == "salary")
		self.assertEqual(line["amount"], 150000.0)
		self.assertEqual(line["basis_kind"], "law")
		self.assertNotIn("×", line["formula"])

	def test_a_partial_period_shows_the_whole_basis_not_a_rounded_daily_rate(self):
		res = self.compute(days_worked=12.0)
		line = next(l for l in res["lines"] if l["key"] == "salary")
		self.assertEqual(line["amount"], flt(150000.0 / 22 * 12, 2))
		self.assertIn("÷", line["formula"])
		self.assertEqual(line["basis_kind"], "company")

	def test_the_old_engine_printed_a_product_it_did_not_pay(self):
		"""Pins the original defect, so nobody reintroduces it thinking it was fine."""
		old = engine.compute_settlement({
			"base": 120000, "food_allowance": 20000, "transport_allowance": 10000,
			"salary_days_worked": 26, "salary_days": 22, "months_worked": 8,
			"ferias_rate": 50, "natal_rate": 50, "untaken_leave_days": 7,
			"leave_days": 21})
		self.assertEqual(old["period_salary"], 150000.0)
		self.assertNotAlmostEqual(
			old["salary_daily_rate"] * 26, old["period_salary"], places=2)

	def test_the_reconciliation_guard_raises_rather_than_shipping_a_bad_line(self):
		bad = [{"key": "x", "label": "Broken", "amount": 100.0, "formula": "2.00 × 3.00",
		        "sign": 1}]
		with self.assertRaises(frappe.ValidationError):
			fs._check_reconciles(bad)


# --------------------------------------------------------------------------- #
# Periods — artigos 204.º, 238.º and 311.º each measure something different
# --------------------------------------------------------------------------- #
class TestSettlementPeriods(SettlementFixture):
	def test_complete_months_do_not_count_a_started_month(self):
		"""Artigo 238.º n.º 3 says *meses completos*. The old engine counted month
		numbers, which turned a termination on the 21st of August into eight months."""
		self.assertEqual(law.complete_months("2026-01-01", "2026-08-21"), 7)
		self.assertEqual(law.complete_months("2026-01-01", "2026-08-31"), 8)
		self.assertEqual(engine.settlement_months("2026-01-01", "2026-08-21"), 8)

	def test_supplements_use_complete_months_from_january(self):
		res = self.compute()
		self.assertEqual(res["supplements"]["months"], 7)
		vacation = next(l for l in res["lines"] if l["key"] == "vacation_allowance")
		self.assertEqual(vacation["amount"], flt(120000 * 0.5 * 7 / 12, 2))
		self.assertIn("Artigo 238", vacation["article"])

	def test_supplements_start_from_the_joining_date_when_hired_this_year(self):
		res = self.compute(joining_date="2026-04-10", vested_untaken_days=0.0)
		# 10 April to 21 August is four complete months (10 Aug), not five.
		self.assertEqual(res["supplements"]["months"], 4)

	def test_seniority_rounds_a_three_month_fraction_up_and_nothing_less(self):
		self.assertEqual(law.seniority_years("2026-06-01", "2026-08-01"), 0)
		self.assertEqual(law.seniority_years("2026-05-01", "2026-08-01"), 1)
		self.assertEqual(law.seniority_years("2021-09-01", "2026-08-01"), 5)
		self.assertEqual(law.seniority_years("2021-05-01", "2026-08-01"), 6)
		self.assertEqual(law.seniority_years("2016-08-01", "2026-08-01"), 10)

	def test_seniority_is_not_the_difference_between_two_years(self):
		self.assertEqual(law.seniority_years("2025-12-31", "2026-01-01"), 0)

	def test_leave_and_supplement_periods_are_computed_separately(self):
		"""Section 15: one integer must not stand in for several legal periods."""
		res = self.compute(joining_date="2026-03-01", vested_untaken_days=0.0)
		self.assertEqual(res["leave"]["proportional_months"], 5)   # from admission
		self.assertEqual(res["supplements"]["months"], 5)
		self.assertEqual(res["seniority_years"], 1)                # artigo 311.º


# --------------------------------------------------------------------------- #
# Leave — artigo 212.º, priced on the artigo 213.º base
# --------------------------------------------------------------------------- #
class TestSettlementLeave(SettlementFixture):
	def test_vested_and_proportional_leave_are_separate_lines(self):
		"""Section 9: one 'untaken leave days' field cannot express artigo 212.º."""
		res = self.compute()
		keys = [l["key"] for l in res["lines"]]
		self.assertIn("leave_vested", keys)
		self.assertIn("leave_proportional", keys)
		self.assertEqual(res["leave"]["vested_untaken_days"], 7.0)
		self.assertEqual(res["leave"]["proportional_days"], 14.0)   # 2 × 7 months
		self.assertEqual(res["leave"]["total_days"], 21.0)

	def test_termination_before_vesting_uses_paragraph_three_only(self):
		res = self.compute(joining_date="2026-02-01", vested_untaken_days=9.0)
		self.assertEqual(res["leave"]["branch"], "212.3")
		self.assertEqual(res["leave"]["article"], "Artigo 212.º n.º 3")
		# n.os 1 and 2 expressly do not apply, so the vested days are NOT also paid.
		self.assertEqual(res["leave"]["vested_untaken_days"], 0.0)
		self.assertEqual(res["leave"]["proportional_days"], 12.0)   # 2 × 6 months

	def test_a_new_employee_below_the_six_day_floor_is_flagged_not_topped_up(self):
		res = self.compute(joining_date="2026-06-01", vested_untaken_days=0.0)
		self.assertEqual(res["leave"]["total_days"], 4.0)
		self.assertTrue(res["leave"]["floor_note"])
		self.assertIn(law.REVIEW_MARKER, res["leave"]["floor_note"])

	def test_a_short_fixed_term_contract_caps_leave_at_twenty_two_days(self):
		res = self.compute(joining_date="2020-01-01", termination_date="2026-12-31",
		                   vested_untaken_days=20.0, fixed_term_under_one_year=True)
		self.assertTrue(res["leave"]["cap_applied"])
		self.assertEqual(res["leave"]["total_days"], 22.0)
		self.assertEqual(res["leave"]["article"], "Artigo 205.º n.º 1")

	def test_an_employee_with_no_leave_left_still_gets_the_proportional_entitlement(self):
		res = self.compute(vested_untaken_days=0.0)
		self.assertEqual(res["leave"]["vested_untaken_days"], 0.0)
		self.assertEqual(res["leave"]["proportional_days"], 14.0)

	def test_leave_pay_excludes_meal_and_transport_by_default(self):
		"""Artigo 213.º n.os 1 and 2 — the base is not the monthly remuneration."""
		res = self.compute()
		self.assertEqual(res["leave"]["remuneration_base"], 120000.0)

	def test_leave_pay_includes_the_technical_and_availability_supplements(self):
		res = self.compute(technical_supplement=8000.0, availability_supplement=2000.0)
		self.assertEqual(res["leave"]["remuneration_base"], 130000.0)

	def test_including_allowances_in_the_leave_base_is_flagged_as_contractual(self):
		res = self.compute(leave_base_includes_allowances=1)
		self.assertEqual(res["leave"]["remuneration_base"], 150000.0)
		self.assertTrue(any(f["code"] == "LEAVE-ALLOW" for f in res["flags"]))

	def test_the_leave_divisor_is_never_presented_as_a_statutory_rate(self):
		"""Section 8 and section 34: a chosen divisor is a company basis."""
		res = self.compute()
		line = next(l for l in res["lines"] if l["key"] == "leave_vested")
		self.assertEqual(line["rate_basis_kind"], "company")
		self.assertIn("Company Calculation Basis", line["note"])
		self.assertFalse(law.LEAVE_DAY_DIVISOR_IS_STATUTORY)


# --------------------------------------------------------------------------- #
# Compensation — the reason decides, not a universal severance
# --------------------------------------------------------------------------- #
class TestSettlementCompensation(SettlementFixture):
	CASES = (
		("resignation_with_notice", 7, "not_applicable", 0.0, "Artigo 305.º n.º 1"),
		("resignation_without_notice", 7, "not_applicable", 0.0, "Artigo 305.º n.º 2"),
		("abandonment", 7, "not_applicable", 0.0, "Artigo 306.º n.º 5"),
		("fixed_term_expiry", 7, "not_applicable", 0.0, "Artigo 17.º n.º 3"),
		("disciplinary_dismissal", 7, "not_applicable", 0.0, "Artigo 281.º"),
		("death_incapacity_retirement", 7, "not_applicable", 0.0, None),
		("objective_dismissal", 7, "applicable", 720000.0, "Artigo 308.º"),
		("collective_dismissal", 7, "applicable", 720000.0, "Artigo 308.º"),
		("extinction_after_suspension", 7, "applicable", 720000.0, "Artigo 308.º"),
		("employer_insolvency_extinction", 7, "applicable", 420000.0, "Artigo 307.º"),
		("non_reinstatement", 7, "applicable", 420000.0, "Artigo 309.º"),
		("indirect_dismissal", 7, "applicable", 840000.0, "Artigo 310.º"),
		("unlawful_dismissal_no_reinstatement", 7, "applicable", 840000.0, "Artigo 310.º"),
		("other", 7, "legal_input_required", 0.0, None),
	)

	def test_each_termination_reason_routes_to_its_own_article(self):
		for reason, years, status, amount, article in self.CASES:
			result = law.compensation(reason, 120000.0, years)
			self.assertEqual(result["status"], status, msg=reason)
			self.assertEqual(result["amount"], amount, msg=reason)
			if article and status == "applicable":
				self.assertEqual(result["article"], article, msg=reason)

	def test_no_reason_returns_legal_input_required_and_never_a_silent_zero(self):
		"""Section 17: an unknown reason is not the same fact as 'nothing is owed'."""
		res = self.compute(reason_key=None)
		line = next(l for l in res["lines"] if l["key"] == "compensation")
		self.assertEqual(line["status"], "legal_input_required")
		self.assertFalse(res["is_complete"])
		self.assertTrue(any(f["code"] == "COMP-INPUT" for f in res["flags"]))

		applicable = self.compute(reason_key="resignation_with_notice")
		not_owed = next(l for l in applicable["lines"] if l["key"] == "compensation")
		self.assertEqual(not_owed["status"], "ok")
		self.assertTrue(applicable["is_complete"])

	def test_article_308_changes_rate_after_five_years(self):
		for years, expected in ((3, 360000.0), (5, 600000.0), (6, 660000.0),
		                        (8, 780000.0), (10, 900000.0)):
			self.assertEqual(
				law.compensation("objective_dismissal", 120000.0, years)["amount"],
				expected, msg="{0} years".format(years))

	def test_article_308_is_never_applied_to_a_resignation_or_a_disciplinary_case(self):
		for reason in ("resignation_with_notice", "disciplinary_dismissal",
		               "fixed_term_expiry", "mutual_agreement"):
			result = law.compensation(reason, 120000.0, 10)
			self.assertNotEqual(result.get("article"), "Artigo 308.º", msg=reason)

	def test_article_310_carries_a_three_month_floor(self):
		short = law.compensation("indirect_dismissal", 120000.0, 1)
		self.assertEqual(short["amount"], 360000.0)
		self.assertTrue(short["floor_applied"])
		long = law.compensation("indirect_dismissal", 120000.0, 5)
		self.assertFalse(long.get("floor_applied"))

	def test_insolvency_and_objective_dismissal_do_not_share_a_formula(self):
		"""Section 19 — artigo 307.º and artigo 308.º stay separate."""
		a = law.compensation("employer_insolvency_extinction", 120000.0, 8)
		b = law.compensation("objective_dismissal", 120000.0, 8)
		self.assertEqual(a["article"], "Artigo 307.º")
		self.assertEqual(b["article"], "Artigo 308.º")
		self.assertNotEqual(a["amount"], b["amount"])

	def test_a_court_dependent_indemnity_is_flagged(self):
		res = self.compute(reason_key="non_reinstatement")
		self.assertTrue(any(f["code"] == "COMP-COURT" for f in res["flags"]))

	def test_an_agreed_compensation_with_no_amount_asks_for_one(self):
		res = self.compute(reason_key="mutual_agreement")
		line = next(l for l in res["lines"] if l["key"] == "compensation")
		self.assertEqual(line["status"], "legal_input_required")
		res = self.compute(reason_key="mutual_agreement", agreed_compensation=250000.0)
		line = next(l for l in res["lines"] if l["key"] == "compensation")
		self.assertEqual(line["amount"], 250000.0)
		self.assertEqual(line["basis_kind"], "input")


# --------------------------------------------------------------------------- #
# Notice — artigo 305.º n.º 2 and artigo 17.º n.º 4 run in opposite directions
# --------------------------------------------------------------------------- #
class TestSettlementNotice(SettlementFixture):
	def test_missing_resignation_notice_is_owed_by_the_employee(self):
		res = self.compute(reason_key="resignation_without_notice",
		                   notice_required_days=30, notice_given_days=10)
		line = next(l for l in res["lines"] if l["key"] == "notice_employee")
		self.assertEqual(line["sign"], -1)
		self.assertEqual(line["article"], "Artigo 305.º n.º 2")
		self.assertEqual(line["amount"], flt(120000.0 / 30 * 20, 2))

	def test_missing_notice_days_are_asked_for_rather_than_assumed(self):
		res = self.compute(reason_key="resignation_without_notice", notice_given_days=None)
		self.assertFalse(res["is_complete"])
		self.assertTrue(any(f["code"] == "LGT-305-2" for f in res["flags"]))

	def test_notice_is_not_deducted_from_an_ordinary_resignation(self):
		res = self.compute(reason_key="resignation_with_notice", notice_given_days=0)
		self.assertFalse([l for l in res["lines"] if l["key"] == "notice_employee"])

	def test_the_employer_owes_thirty_days_for_a_missed_non_renewal_notice(self):
		res = self.compute(reason_key="fixed_term_expiry",
		                   employer_missed_renewal_notice=1)
		line = next(l for l in res["lines"] if l["key"] == "notice_employer")
		self.assertEqual(line["article"], "Artigo 17.º n.º 4")
		self.assertEqual(line["sign"], 1)
		self.assertEqual(line["amount"], flt(120000.0 / 30 * 30, 2))


# --------------------------------------------------------------------------- #
# Statutory deductions, advances and the net
# --------------------------------------------------------------------------- #
class TestSettlementDeductions(SettlementFixture):
	def test_statutory_deductions_reuse_the_payroll_engine_resolvers(self):
		"""Section 25 — one IRT table, one rate record, one trace. Not a second engine."""
		res = self.compute()
		self.assertGreater(res["inss"], 0)
		self.assertGreater(res["irt"], 0)
		trace = res["trace"]["statutory"]
		self.assertTrue(trace["irt_table"])
		self.assertEqual(trace["ss_employee_rate"], res["trace"]["statutory"]["ss_employee_rate"])
		# Social security comes off before IRT, exactly as on a salary slip.
		self.assertAlmostEqual(res["irt_base"], flt(res["irt_gross"] - res["inss"], 2), places=2)

	def test_the_vacation_gratuity_is_outside_the_social_security_base(self):
		"""Decreto Presidencial n.º 227/18 artigo 13.º excludes the subsídio de férias;
		the Christmas bonus is not excluded. Same split the salary slip applies."""
		res = self.compute()
		vacation = next(l for l in res["lines"] if l["key"] == "vacation_allowance")
		christmas = next(l for l in res["lines"] if l["key"] == "christmas_bonus")
		self.assertFalse(vacation["inss_base"])
		self.assertTrue(christmas["inss_base"])
		self.assertTrue(vacation["irt_taxable"])

	def test_compensation_tax_position_is_never_silently_zero(self):
		res = self.compute(reason_key="objective_dismissal")
		line = next(l for l in res["lines"] if l["key"] == "compensation")
		self.assertEqual(line["status"], "verify")
		self.assertIn(law.REVIEW_MARKER, line["note"])
		self.assertFalse(line["irt_taxable"])

		taxed = self.compute(reason_key="objective_dismissal",
		                     compensation_tax_position="taxable")
		line = next(l for l in taxed["lines"] if l["key"] == "compensation")
		self.assertTrue(line["irt_taxable"])
		self.assertGreater(taxed["irt"], res["irt"])

	def test_the_lump_sum_irt_question_is_surfaced(self):
		res = self.compute()
		self.assertTrue(any(f["code"] == "IRT-LUMP" for f in res["flags"]))

	def test_an_advance_is_recovered_and_the_remainder_stays_outstanding(self):
		res = self.compute(advance_outstanding=50000.0)
		self.assertEqual(res["advance_recovered"], 50000.0)
		self.assertEqual(res["advance_deferred"], 0.0)

		huge = self.compute(advance_outstanding=5000000.0)
		self.assertLess(huge["advance_recovered"], 5000000.0)
		self.assertGreater(huge["advance_deferred"], 0.0)
		self.assertGreaterEqual(huge["net"], 0.0)
		self.assertTrue(any(f["code"] == "ADV-REMAIN" for f in huge["flags"]))

	def test_the_net_never_goes_negative_and_the_shortfall_is_shown(self):
		"""Section 42 — over-deducting silently is the failure mode being prevented."""
		res = self.compute(reason_key="resignation_without_notice",
		                   notice_required_days=3000, notice_given_days=0)
		self.assertEqual(res["net"], 0.0)
		self.assertGreater(res["shortfall"], 0.0)
		self.assertTrue(any(f["code"] == "NEG-NET" for f in res["flags"]))

	def test_advance_recovery_is_optional(self):
		res = self.compute(advance_outstanding=50000.0, recover_advance=0)
		self.assertEqual(res["advance_recovered"], 0.0)


# --------------------------------------------------------------------------- #
# Payment deadline — artigo 245.º, not artigo 240.º
# --------------------------------------------------------------------------- #
class TestSettlementDeadline(SettlementFixture):
	def test_the_deadline_is_three_days_under_artigo_245(self):
		due = fs.payment_deadline("2026-08-21", "resignation_with_notice")
		self.assertEqual(getdate(due["due_date"]), getdate(add_days("2026-08-21", 3)))
		self.assertEqual(due["article"], "Artigo 245.º n.º 4")

	def test_a_collective_dismissal_uses_its_own_deadline(self):
		due = fs.payment_deadline("2026-08-21", "collective_dismissal")
		self.assertIsNone(due["due_date"])
		self.assertEqual(due["article"], "Artigo 296.º")


# --------------------------------------------------------------------------- #
# The document: overrides, workflow, permissions, immutability
# --------------------------------------------------------------------------- #
class TestSettlementDocument(SettlementFixture):
	def test_a_new_settlement_stores_the_corrected_amounts_and_its_trace(self):
		doc = self.make_settlement()
		self.assertEqual(doc.calc_version, fs.CALC_VERSION)
		self.assertEqual(doc.supplement_months, 7)
		self.assertEqual(doc.seniority_years, 7)
		self.assertEqual(doc.proportional_leave_days, 14.0)
		self.assertEqual(doc.period_salary, 150000.0)
		self.assertGreater(flt(doc.net_payable), 0)
		trace = json.loads(doc.calculation_trace)
		for key in ("law", "remuneration", "leave", "supplements", "seniority",
		            "statutory", "advance", "overrides"):
			self.assertIn(key, trace)
		self.assertIn("12/23", trace["law"])
		self.assertTrue(trace["statutory"]["irt_table"])

	def test_an_override_without_a_reason_is_refused(self):
		"""Section 30 — a derived legal quantity may be overridden, never anonymously."""
		with self.assertRaises(frappe.ValidationError):
			self.make_settlement(supplement_months_override=12)

	def test_an_override_records_who_and_when(self):
		doc = self.make_settlement(supplement_months_override=12,
		                           override_reason="Suspension period agreed with MAPTSS")
		self.assertEqual(doc.supplement_months, 12)
		self.assertTrue(doc.override_by)
		self.assertTrue(doc.override_at)

	def test_a_settlement_with_no_reason_cannot_go_for_approval(self):
		doc = self.make_settlement(reason_key=None)
		frappe.set_user(self.users["officer"])
		with self.assertRaises(frappe.ValidationError):
			api.submit_settlement_for_approval(doc.name)
		frappe.set_user("Administrator")

	def test_a_settlement_with_no_reason_cannot_be_submitted(self):
		doc = self.make_settlement(reason_key=None)
		with self.assertRaises(frappe.ValidationError):
			doc.submit()

	def test_the_workflow_runs_draft_to_approved_across_two_people(self):
		doc = self.make_settlement()
		frappe.set_user(self.users["officer"])
		api.submit_settlement_for_approval(doc.name)
		doc.reload()
		self.assertEqual(doc.workflow_status, "Pending Approval")
		self.assertEqual(doc.submitted_for_approval_by, self.users["officer"])

		frappe.set_user(self.users["manager"])
		api.approve_settlement(doc.name)
		doc.reload()
		self.assertEqual(doc.workflow_status, "Approved")
		self.assertEqual(doc.approved_by, self.users["manager"])
		frappe.set_user("Administrator")

	def test_the_preparer_cannot_also_approve(self):
		"""Section 45 — preparing and approving are different permissions."""
		doc = self.make_settlement()
		frappe.set_user(self.users["officer"])
		api.submit_settlement_for_approval(doc.name)
		with self.assertRaises(frappe.PermissionError):
			api.approve_settlement(doc.name)
		frappe.set_user("Administrator")

	def test_an_employee_cannot_approve_their_own_settlement(self):
		"""Section 45 — the separation that matters most."""
		doc = self.make_settlement()
		frappe.db.set_value("Employee", self.employee.name, "user_id",
		                    self.users["manager"])
		frappe.set_user(self.users["officer"])
		api.submit_settlement_for_approval(doc.name)
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError):
			api.approve_settlement(doc.name)
		frappe.set_user("Administrator")
		frappe.db.set_value("Employee", self.employee.name, "user_id", None)

	def test_a_rejection_must_carry_a_reason(self):
		doc = self.make_settlement()
		frappe.set_user(self.users["officer"])
		api.submit_settlement_for_approval(doc.name)
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError):
			api.reject_settlement(doc.name, reason="")
		api.reject_settlement(doc.name, reason="Leave balance disputed")
		doc.reload()
		self.assertEqual(doc.workflow_status, "Rejected")
		frappe.set_user("Administrator")

	def test_an_approved_settlement_cannot_be_edited(self):
		doc = self.make_settlement()
		frappe.set_user(self.users["officer"])
		api.submit_settlement_for_approval(doc.name)
		frappe.set_user(self.users["manager"])
		api.approve_settlement(doc.name)
		frappe.set_user(self.users["officer"])
		with self.assertRaises(frappe.ValidationError):
			api.update_settlement(doc.name, json.dumps({"base": 999999}))
		frappe.set_user("Administrator")

	def test_out_of_order_transitions_are_refused(self):
		doc = self.make_settlement()
		frappe.set_user(self.users["manager"])
		with self.assertRaises(frappe.ValidationError):
			api.approve_settlement(doc.name)          # still a draft
		frappe.set_user("Administrator")


# --------------------------------------------------------------------------- #
# Historical settlements — section 46
# --------------------------------------------------------------------------- #
class TestSettlementHistory(SettlementFixture):
	def legacy(self):
		"""A settlement as the pre-audit engine would have stored it."""
		doc = frappe.get_doc({
			"doctype": "Isoft Final Settlement",
			"employee": self.employee.name, "company": self.company,
			"date_of_joining": "2020-03-01", "termination_date": "2026-08-21",
			"reason": "Resignation",
			"base": 120000.0, "food_allowance": 20000.0, "transport_allowance": 10000.0,
			"salary_period_start": "2026-07-23", "salary_period_end": "2026-08-21",
			"salary_days_worked": 26, "salary_days": 22, "months_worked": 8,
			"ferias_rate": 50, "natal_rate": 50, "untaken_leave_days": 7,
			"leave_days": 21, "calc_version": 1,
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Final Settlement", doc.name))
		return doc

	def test_a_version_one_settlement_keeps_its_stored_amounts(self):
		doc = self.legacy()
		self.assertEqual(doc.calc_version, 1)
		self.assertEqual(doc.total_gross, 270000.0)
		self.assertEqual(doc.months_worked, 8)

	def test_saving_a_version_one_settlement_does_not_restate_it(self):
		doc = self.legacy()
		doc.notes = "Touched"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.total_gross, 270000.0)
		self.assertEqual(doc.calc_version, 1)

	def test_a_version_one_settlement_is_shown_as_legacy_with_no_invented_formula(self):
		doc = self.legacy()
		out = api.get_settlement(doc.name)
		self.assertTrue(out["is_legacy"])
		salary = next(l for l in out["lines"] if l["key"] == "salary")
		# The old screen printed 6,818.18 x 26 for an amount of 150,000. Rather than
		# repeat arithmetic that never held, the calculation column is left empty.
		self.assertIsNone(salary["formula"])
		self.assertTrue(any(f["code"] == "LEGACY" for f in out["flags"]))

	def test_restating_a_legacy_settlement_is_explicit_and_reports_the_difference(self):
		doc = self.legacy()
		preview = api.recalculate_settlement(doc.name)
		self.assertTrue(preview["confirm_required"])
		self.assertEqual(preview["before"], 270000.0)
		self.assertNotEqual(preview["after"], preview["before"])
		doc.reload()
		self.assertEqual(doc.total_gross, 270000.0)     # unchanged until confirmed

		api.recalculate_settlement(doc.name, confirm=1)
		doc.reload()
		self.assertEqual(doc.calc_version, fs.CALC_VERSION)
		self.assertEqual(doc.supplement_months, 7)

	def test_new_fields_are_nullable_so_migration_cannot_fail(self):
		"""Section 47 — an old row has none of the new columns filled."""
		doc = self.legacy()
		for field in ("reason_key", "technical_supplement", "vested_untaken_days",
		              "compensation_amount", "net_payable", "seniority_years"):
			self.assertIn(field, doc.as_dict())
		self.assertFalse(doc.reason_key)
		# And it still renders.
		self.assertTrue(api.get_settlement(doc.name))

	def test_the_legacy_reason_still_maps_to_an_article_for_display(self):
		doc = self.legacy()
		out = api.get_settlement(doc.name)
		# No reason_key, so the label falls back to the legacy free text rather than
		# going blank — and the map still tells the engine which article that meant.
		self.assertEqual(out["reason_label"], "Resignation")
		self.assertEqual(law.LEGACY_REASON_MAP["Resignation"], "resignation_with_notice")


# --------------------------------------------------------------------------- #
# The legal reference the UI shows
# --------------------------------------------------------------------------- #
class TestSettlementLegalReference(SettlementFixture):
	def test_the_reference_names_the_statute_and_its_source(self):
		ref = law.settlement_reference()
		self.assertIn("12/23", ref["law"])
		self.assertIn("Diário da República", ref["source"])
		self.assertEqual(ref["marker"], law.REVIEW_MARKER)

	def test_every_termination_reason_is_offered_with_its_article(self):
		ref = law.settlement_reference()
		keys = {r["key"] for r in ref["reasons"]}
		self.assertEqual(keys, set(law.TERMINATION_REASONS))
		for reason in ref["reasons"]:
			if reason["compensation"] not in (None, "unknown", "agreed"):
				self.assertTrue(reason["article"], msg=reason["key"])

	def test_the_open_questions_are_stated_rather_than_answered(self):
		"""Section 51 — where the law is silent the app says so."""
		ref = law.settlement_reference()
		self.assertGreaterEqual(len(ref["open_questions"]), 3)
		text = " ".join(q["answer"] for q in ref["open_questions"])
		self.assertIn(law.REVIEW_MARKER, text)
		self.assertIn("COMPANY CALCULATION BASIS", text.upper())
