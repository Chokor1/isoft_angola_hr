# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Phase 1 / P0 regression tests.

Every test in this module asserts the CORRECT behaviour. Before the Phase 1 fixes
each of them fails, which is what proves the defect exists; afterwards they pass and
protect against regression.

These tests are deliberately free of database writes: the payroll engine is exercised
directly with injected statutory configuration, so a run can never touch live payroll.
"""

import unittest

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import compute_irt
from isoft_angola_hr.isoft_angola_hr.payroll import engine


# --------------------------------------------------------------------------- #
# Fixtures — statutory configuration built in memory, never read from the site
# --------------------------------------------------------------------------- #

# Official Angola IRT Grupo A table in force for 2026 (Lei n.º 14/25, de 30 de Dezembro).
# Expressed the way the law prints it: lower bound = previous upper bound + 1.
IRT_2026_AS_PRINTED = [
	# (from, to, excess_over, rate, parcela_fixa)
	(0, 150000, 0, 0.0, 0),
	(150001, 200000, 150000, 16.0, 12500),
	(200001, 300000, 200000, 18.0, 31250),
	(300001, 500000, 300000, 19.0, 49250),
	(500001, 1000000, 500000, 20.0, 87250),
	(1000001, 1500000, 1000000, 21.0, 187250),
	(1500001, 2000000, 1500000, 22.0, 292250),
	(2000001, 2500000, 2000000, 23.0, 402250),
	(2500001, 5000000, 2500000, 24.0, 517250),
	(5000001, 10000000, 5000000, 24.5, 1117250),
	(10000001, 0, 10000000, 25.0, 2342250),
]


def make_table(rows, name="TEST-IRT"):
	"""An in-memory stand-in for an IRT Table document."""
	return frappe._dict(
		name=name,
		effective_from="2026-01-01",
		brackets=[
			frappe._dict(from_amount=fr, to_amount=to, excess_over=xs, rate=rate, parcela_fixa=pf)
			for (fr, to, xs, rate, pf) in rows
		],
	)


def make_settings(**overrides):
	s = frappe._dict(
		ss_employee_rate=3.0,
		ss_employer_rate=8.0,
		food_allowance_exemption=30000.0,
		transport_allowance_exemption=30000.0,
		natal_rate=50.0,
		ferias_rate=50.0,
		overtime_multiplier=2.0,
	)
	s.update(overrides)
	return s


def make_profile(base=200000, food=0, transport=0, family=0):
	return {
		"base": base,
		"food_allowance": food,
		"transport_allowance": transport,
		"family_allowance": family,
		"company": None,
		"irt_table": None,
	}


def make_rates(settings):
	"""The statutory rate set the engine would resolve, built directly from the given
	settings so unit tests never depend on records present on the site."""
	return frappe._dict(
		ss_employee_rate=settings.ss_employee_rate,
		ss_employer_rate=settings.ss_employer_rate,
		food_allowance_exemption=settings.food_allowance_exemption,
		transport_allowance_exemption=settings.transport_allowance_exemption,
		statutory_rate=None,
	)


def run_slip(profile=None, table=None, settings=None, **inputs):
	"""Call the engine with fully injected configuration (no database access)."""
	inputs.setdefault("payment_days", 30)
	inputs.setdefault("total_working_days", 30)
	settings = settings or make_settings()
	return engine.compute_slip(
		profile or make_profile(),
		inputs,
		settings=settings,
		rates=make_rates(settings),
		irt_table=table or make_table(IRT_2026_AS_PRINTED),
	)


def deduction(res, abbr):
	return flt(next((d["amount"] for d in res["deductions"] if d["abbr"] == abbr), 0.0))


def earning(res, abbr):
	return flt(next((e["amount"] for e in res["earnings"] if e["abbr"] == abbr), 0.0))


# --------------------------------------------------------------------------- #
# P0-03 / P0-04 — IRT must match decimal amounts and must fail loudly
# --------------------------------------------------------------------------- #
class TestIRTBracketMatching(unittest.TestCase):
	def setUp(self):
		self.table = make_table(IRT_2026_AS_PRINTED)

	def test_decimal_amounts_between_printed_bounds_are_taxed(self):
		"""P0-03: taxable income landing between a bracket's upper bound and the next
		bracket's printed lower bound must still be taxed, not silently zero-rated."""
		cases = [
			(150000.50, 12500.08),
			(200000.50, 31250.09),
			(300000.75, 49250.14),
			(500000.25, 87250.05),
			(1000000.25, 187250.05),
			(10000000.50, 2342250.13),
		]
		for taxable, expected in cases:
			with self.subTest(taxable=taxable):
				got = compute_irt(taxable, table=self.table)
				self.assertAlmostEqual(
					got, expected, places=2,
					msg="taxable %.2f fell into a bracket gap and was taxed %.2f" % (taxable, got),
				)

	def test_exemption_boundary(self):
		self.assertEqual(compute_irt(149999.99, table=self.table), 0.0)
		self.assertEqual(compute_irt(150000.00, table=self.table), 0.0)
		self.assertAlmostEqual(compute_irt(150000.01, table=self.table), 12500.00, places=2)

	def test_every_bracket_boundary(self):
		"""boundary - 0.01 / boundary / boundary + 0.01 across the whole table."""
		bounds = [150000, 200000, 300000, 500000, 1000000, 1500000, 2000000, 2500000, 5000000, 10000000]
		for b in bounds:
			for probe in (b - 0.01, b, b + 0.01):
				with self.subTest(probe=probe):
					self.assertIsInstance(compute_irt(probe, table=self.table), float)

	def test_tax_is_monotonic_non_decreasing(self):
		"""More taxable income must never mean less tax."""
		prev = -1.0
		v = 0.0
		while v <= 12000000:
			tax = compute_irt(v, table=self.table)
			self.assertGreaterEqual(
				tax, prev, "tax decreased between %.2f and %.2f" % (v - 250.13, v))
			prev = tax
			v += 250.13

	def test_zero_and_negative(self):
		self.assertEqual(compute_irt(0, table=self.table), 0.0)
		self.assertEqual(compute_irt(-5000, table=self.table), 0.0)

	def test_missing_top_bracket_raises(self):
		"""P0-03: an incomplete table is a configuration error, never a zero-tax employee."""
		truncated = make_table(IRT_2026_AS_PRINTED[:3])  # tops out at 300,000
		with self.assertRaises(frappe.ValidationError):
			compute_irt(4000000, table=truncated)

	def test_no_table_raises(self):
		with self.assertRaises(frappe.ValidationError):
			compute_irt(500000, table=None, on_date="1899-01-01", company="__no_such_company__")


# --------------------------------------------------------------------------- #
# P0-05 — proration guards
# --------------------------------------------------------------------------- #
class TestProrationGuards(unittest.TestCase):
	def test_full_month(self):
		res = run_slip(payment_days=30, total_working_days=30)
		self.assertEqual(res["payment_factor"], 1.0)
		self.assertEqual(earning(res, "SB"), 200000.00)

	def test_half_month(self):
		res = run_slip(payment_days=15, total_working_days=30)
		self.assertEqual(res["payment_factor"], 0.5)
		self.assertEqual(earning(res, "SB"), 100000.00)

	def test_zero_payment_days_pays_nothing(self):
		res = run_slip(payment_days=0, total_working_days=30)
		self.assertEqual(res["gross_pay"], 0.0)

	def test_zero_working_days_raises_instead_of_paying_full_month(self):
		"""P0-05: TWD=0 must be a hard error. It previously produced factor=1.0."""
		with self.assertRaises(frappe.ValidationError):
			run_slip(payment_days=0, total_working_days=0)

	def test_payment_days_cannot_exceed_working_days(self):
		"""P0-05: 45 paid days out of 30 previously produced 150% of salary."""
		with self.assertRaises(frappe.ValidationError):
			run_slip(payment_days=45, total_working_days=30)

	def test_payment_days_one_over_raises(self):
		with self.assertRaises(frappe.ValidationError):
			run_slip(payment_days=31, total_working_days=30)

	def test_negative_days_raise(self):
		with self.assertRaises(frappe.ValidationError):
			run_slip(payment_days=-1, total_working_days=30)
		with self.assertRaises(frappe.ValidationError):
			run_slip(payment_days=10, total_working_days=-30)


# --------------------------------------------------------------------------- #
# P0-02 — employer social security
# --------------------------------------------------------------------------- #
class TestEmployerSocialSecurity(unittest.TestCase):
	def test_employer_contribution_is_calculated(self):
		"""P0-02: the employer's contribution must exist as a first-class result."""
		res = run_slip(profile=make_profile(base=200000, food=30000, transport=30000))
		self.assertEqual(flt(res["ss_base"], 2), 260000.00)
		self.assertEqual(flt(res["ss_employee_rate"], 2), 3.00)
		self.assertEqual(flt(res["ss_employee_amount"], 2), 7800.00)
		self.assertEqual(flt(res["ss_employer_rate"], 2), 8.00)
		self.assertEqual(flt(res["ss_employer_amount"], 2), 20800.00)

	def test_employer_contribution_does_not_reduce_net_pay(self):
		"""The employer contribution is a company cost, never an employee deduction."""
		prof = make_profile(base=200000, food=30000, transport=30000)
		with_employer = run_slip(profile=prof, settings=make_settings(ss_employer_rate=8.0))
		without_employer = run_slip(profile=prof, settings=make_settings(ss_employer_rate=0.0))
		self.assertEqual(with_employer["net_pay"], without_employer["net_pay"])
		self.assertEqual(with_employer["gross_pay"], without_employer["gross_pay"])
		self.assertEqual(with_employer["total_deduction"], without_employer["total_deduction"])
		self.assertEqual(flt(with_employer["ss_employer_amount"], 2), 20800.00)
		self.assertEqual(flt(without_employer["ss_employer_amount"], 2), 0.00)

	def test_employer_contribution_is_not_a_deduction_row(self):
		res = run_slip(profile=make_profile(base=200000))
		self.assertNotIn("CTSSE", [d["abbr"] for d in res["deductions"]])
		self.assertNotIn("CTSSE", [e["abbr"] for e in res["earnings"]])

	def test_employer_cost_total(self):
		res = run_slip(profile=make_profile(base=200000, food=30000, transport=30000))
		self.assertEqual(
			flt(res["employer_cost"], 2),
			flt(res["gross_pay"] + res["ss_employer_amount"], 2),
		)

	def test_rates_are_configuration_driven(self):
		"""Rates must never be hard-coded in the engine."""
		res = run_slip(settings=make_settings(ss_employee_rate=5.0, ss_employer_rate=10.0))
		self.assertEqual(flt(res["ss_employee_amount"], 2), 10000.00)   # 5% of 200,000
		self.assertEqual(flt(res["ss_employer_amount"], 2), 20000.00)   # 10% of 200,000

	def test_zero_remuneration(self):
		res = run_slip(profile=make_profile(base=0))
		self.assertEqual(res["gross_pay"], 0.0)
		self.assertEqual(flt(res["ss_employee_amount"], 2), 0.0)
		self.assertEqual(flt(res["ss_employer_amount"], 2), 0.0)

    # -- incidence base: existing behaviour, pinned so it cannot drift silently --
	def test_incidence_base_matches_documented_behaviour(self):
		"""Pins the CURRENT base: Férias excluded, Natal included. Not a legal ruling —
		see the LEGAL VERIFICATION note in engine.py."""
		with_ferias = run_slip(ferias_amount=100000)
		with_natal = run_slip(natal_amount=100000)
		self.assertEqual(flt(with_ferias["ss_base"], 2), 200000.00)
		self.assertEqual(flt(with_natal["ss_base"], 2), 300000.00)

	def test_employer_uses_same_base_as_employee(self):
		res = run_slip(profile=make_profile(base=200000, food=30000, transport=30000),
		               productivity_bonus=50000)
		self.assertEqual(
			flt(res["ss_employer_amount"], 2),
			flt(res["ss_base"] * res["ss_employer_rate"] / 100.0, 2),
		)


# --------------------------------------------------------------------------- #
# P0-06 — invalid net pay
# --------------------------------------------------------------------------- #
class TestNegativeNetPay(unittest.TestCase):
	def test_engine_reports_the_exception_without_hiding_it(self):
		"""Preview must still compute so HR can SEE the problem..."""
		res = run_slip(profile=make_profile(base=100000), adiantamento=500000)
		self.assertLess(res["net_pay"], 0)
		self.assertTrue(res["has_negative_net"])

	def test_valid_payroll_is_not_flagged(self):
		res = run_slip(profile=make_profile(base=200000), adiantamento=10000)
		self.assertGreater(res["net_pay"], 0)
		self.assertFalse(res["has_negative_net"])


# --------------------------------------------------------------------------- #
# Calculation snapshot / statutory traceability
# --------------------------------------------------------------------------- #
class TestCalculationTrace(unittest.TestCase):
	def test_trace_explains_the_irt_figure(self):
		res = run_slip(profile=make_profile(base=200000, food=30000, transport=30000))
		self.assertEqual(flt(res["taxable_income"], 2), 192200.00)
		self.assertEqual(flt(res["irt_bracket_from"], 2), 150001.00)
		self.assertEqual(flt(res["irt_bracket_to"], 2), 200000.00)
		self.assertEqual(flt(res["irt_rate"], 2), 16.00)
		self.assertEqual(flt(res["irt_parcela_fixa"], 2), 12500.00)
		self.assertEqual(flt(res["irt_amount"], 2), 19252.00)
		# parcela fixa + rate x (taxable - excess) reproduces the figure exactly
		self.assertAlmostEqual(
			res["irt_parcela_fixa"] + (res["taxable_income"] - res["irt_excess_over"]) * res["irt_rate"] / 100.0,
			res["irt_amount"], places=2,
		)

	def test_exemptions_are_recorded(self):
		res = run_slip(profile=make_profile(base=200000, food=45000, transport=10000))
		self.assertEqual(flt(res["food_exemption_applied"], 2), 30000.00)
		self.assertEqual(flt(res["transport_exemption_applied"], 2), 10000.00)


# --------------------------------------------------------------------------- #
# Regression pin — the reference calculation must not change
# --------------------------------------------------------------------------- #
class TestReferenceCalculation(unittest.TestCase):
	def test_standard_employee_unchanged(self):
		"""base 200,000 + food 30,000 + transport 30,000, full month.
		These figures were verified against the engine BEFORE the Phase 1 changes and
		must remain identical afterwards."""
		res = run_slip(profile=make_profile(base=200000, food=30000, transport=30000))
		self.assertEqual(flt(res["gross_pay"], 2), 260000.00)
		self.assertEqual(deduction(res, "CTSS3"), 7800.00)
		self.assertEqual(flt(res["taxable_income"], 2), 192200.00)
		self.assertEqual(deduction(res, "IRT"), 19252.00)
		self.assertEqual(flt(res["net_pay"], 2), 232948.00)
