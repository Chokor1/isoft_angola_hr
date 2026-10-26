# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Subsídio de Natal (13th month) — Lei n.º 12/23, artigo 238.º.

Artigo 238.º n.º 1 al. b) sets the Christmas supplement at a **minimum of 50% of the
salário-base**. Artigo 238.º n.º 3 makes it proportional to the **complete months** of
service when a full year of service was not given.

Two things this module pins, and one it deliberately only records.

**Pinned — the month count.** ``engine.default_natal`` used to count
``12 - joining_month + 1``, which credits the month of admission whatever day it began
on. The final-settlement path has always used ``law.complete_months``, which does not.
So the same statutory supplement came out differently depending on whether an employee
was paid in December or terminated in it: on a 150,000 base, an employee admitted on
15 March was paid 62,500 through payroll and 56,250 through a settlement. The two now
share one definition, and the cases below hold them to it.

**Pinned — where the supplement lands.** Gross, taxable income, INSS base, IRT and net
are all asserted, so a change to the component catalogue cannot quietly move the 13th
month in or out of a statutory base.

**Recorded, NOT resolved — suspension.** Artigo 238.º n.º 3 prorates for a year
interrupted by suspension as well as one shortened by admission or termination. This
application stores suspension only as an ``Employee.status`` value with no dates, so
there is nothing to prorate against and an employee suspended for three months is paid
as though they were not. That is a data-model gap, and
:class:`TestSuspensionIsNotProrated` states it explicitly rather than leaving it to be
discovered in December. **LEGAL VERIFICATION REQUIRED.**
"""

import unittest

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

BASE = 150_000.0
RATE = 50.0          # artigo 238.º n.º 1 al. b) minimum
YEAR = 2026
DEC_END = "%d-12-31" % YEAR


def natal(joining, period_end=DEC_END, base=BASE, rate=RATE, payment_month="December"):
	return engine.default_natal(base, rate, joining, period_end, payment_month)


def twelfths(n, base=BASE, rate=RATE):
	return flt(base * rate / 100.0 * n / 12.0, 2)


# --------------------------------------------------------------------------- #
# The matrix: admission dates -> complete months -> money
# --------------------------------------------------------------------------- #
class TestTheMonthCountMatrix(unittest.TestCase):
	"""Cases A–D and G of the requested matrix, at the calculation level."""

	#: (label, date_of_joining, expected complete months in 2026)
	CASES = [
		("A  full year, joined a previous year", "2020-06-15", 12),
		("B  admitted 1 January",                "2026-01-01", 12),
		("B' admitted 15 January",               "2026-01-15", 11),
		("C  admitted 1 March",                  "2026-03-01", 10),
		("C' admitted 15 March",                 "2026-03-15", 9),
		("C\" admitted 31 March",                "2026-03-31", 9),
		("D  admitted 1 November",               "2026-11-01", 2),
		("D' admitted 20 November",              "2026-11-20", 1),
		("D\" admitted 1 December",              "2026-12-01", 1),
		("D\" admitted 31 December",             "2026-12-31", 0),
		("   admitted next year",                "2027-02-01", 0),
	]

	def test_complete_months_for_every_admission_date(self):
		for label, joining, months in self.CASES:
			with self.subTest(case=label):
				self.assertEqual(
					engine.natal_months(joining, DEC_END), months,
					"%s: artigo 238.º n.º 3 gives %d complete months" % (label, months))

	def test_the_money_follows_the_month_count(self):
		for label, joining, months in self.CASES:
			with self.subTest(case=label):
				self.assertAlmostEqual(natal(joining), twelfths(months), places=2)

	def test_a_full_year_is_exactly_the_statutory_minimum(self):
		"""50% of the salário-base — artigo 238.º n.º 1 al. b)."""
		self.assertAlmostEqual(natal("2020-01-01"), BASE * 0.5, places=2)
		self.assertEqual(law.CHRISTMAS_BONUS_MIN_PCT, 50.0)

	def test_the_admission_month_is_not_credited_unless_it_was_completed(self):
		"""The defect this module was written for, stated as one assertion."""
		first = natal("2026-03-01")
		fifteenth = natal("2026-03-15")
		self.assertGreater(first, fifteenth,
		                   "an employee admitted mid-March cannot be owed the same "
		                   "13th month as one admitted on the 1st")
		self.assertAlmostEqual(first - fifteenth, twelfths(1), places=2)

	def test_it_never_exceeds_a_full_year(self):
		for joining in ("2019-01-01", "2026-01-01", None):
			with self.subTest(joining=joining):
				self.assertLessEqual(natal(joining), BASE * RATE / 100.0 + 0.005)

	def test_it_is_never_negative(self):
		for joining in ("2026-12-31", "2027-06-01", "2030-01-01"):
			with self.subTest(joining=joining):
				self.assertGreaterEqual(natal(joining), 0.0)


class TestPayrollAndSettlementNowAgree(unittest.TestCase):
	"""The divergence that motivated the fix, held closed."""

	DATES = ["2026-01-01", "2026-01-15", "2026-03-01", "2026-03-15", "2026-03-31",
	         "2026-06-30", "2026-11-01", "2026-11-20", "2026-12-01"]

	def test_both_paths_count_the_same_complete_months(self):
		year_start = getdate("%d-01-01" % YEAR)
		end = getdate(DEC_END)
		for d in self.DATES:
			with self.subTest(joining=d):
				jd = getdate(d)
				window = jd if jd > year_start else year_start
				self.assertEqual(engine.natal_months(d, DEC_END),
				                 law.complete_months(window, end),
				                 "payroll and settlement disagree for %s" % d)

	def test_the_supplement_amount_matches_the_settlement_formula(self):
		"""Same base, same rate, same months -> same money, by both routes."""
		for d in self.DATES:
			with self.subTest(joining=d):
				months = law.complete_months(
					max(getdate(d), getdate("%d-01-01" % YEAR)), getdate(DEC_END))
				self.assertAlmostEqual(natal(d), twelfths(months), places=2)


class TestPaymentMonthGating(unittest.TestCase):
	"""Case I — the supplement is paid once, in the period ENDING in the payment month."""

	def test_only_the_period_ending_in_december_pays_it(self):
		for month in range(1, 12):
			end = getdate("%d-%02d-28" % (YEAR, month))
			with self.subTest(period_end=str(end)):
				self.assertEqual(natal("2020-01-01", end), 0.0)
		self.assertGreater(natal("2020-01-01", DEC_END), 0.0)

	def test_a_23rd_to_22nd_cycle_pays_it_in_the_period_ending_22_december(self):
		"""payroll_cycle_start_day = 23 -> the December period ends on 22 December."""
		self.assertGreater(natal("2020-01-01", "%d-12-22" % YEAR), 0.0)
		self.assertEqual(natal("2020-01-01", "%d-11-22" % YEAR), 0.0)

	def test_a_15th_cycle_pays_it_in_the_period_ending_14_december(self):
		self.assertGreater(natal("2020-01-01", "%d-12-14" % YEAR), 0.0)
		self.assertEqual(natal("2020-01-01", "%d-01-14" % YEAR), 0.0)

	def test_the_payment_month_is_configurable(self):
		self.assertGreater(natal("2020-01-01", "%d-11-30" % YEAR,
		                         payment_month="November"), 0.0)
		self.assertEqual(natal("2020-01-01", DEC_END, payment_month="November"), 0.0)

	def test_an_unknown_payment_month_falls_back_to_december(self):
		self.assertGreater(natal("2020-01-01", DEC_END, payment_month="Dezembro"), 0.0)


class TestDegenerateSalaries(unittest.TestCase):
	"""Case H — zero, missing and negative bases must not produce nonsense."""

	def test_zero_base_pays_nothing(self):
		self.assertEqual(natal("2020-01-01", base=0), 0.0)

	def test_zero_rate_pays_nothing(self):
		self.assertEqual(natal("2020-01-01", rate=0), 0.0)

	def test_a_negative_base_never_produces_a_positive_supplement(self):
		self.assertLessEqual(natal("2020-01-01", base=-150_000), 0.0)

	def test_a_missing_joining_date_is_treated_as_a_full_year(self):
		"""An employee with no admission date on file must not silently lose the money."""
		self.assertAlmostEqual(natal(None), BASE * 0.5, places=2)


# --------------------------------------------------------------------------- #
# Where the supplement lands: gross, taxable income, INSS, IRT, net
# --------------------------------------------------------------------------- #
class TestNatalInTheStatutoryBases(PayrollFixture):
	"""Cases A–H at slip level: what the 13th month does to every total."""

	def _slip(self, natal_amount, base=BASE, days=(22, 22)):
		total, paid = days
		return engine.compute_slip(
			{"base": base, "food_allowance": 0, "transport_allowance": 0,
			 "family_allowance": 0, "company": self.company},
			{"natal_amount": natal_amount, "ferias_amount": 0.0,
			 "productivity_bonus": 0, "overtime_amount": 0, "adiantamento": 0,
			 "payment_days": paid, "total_working_days": total,
			 "start_date": "%d-12-01" % YEAR, "end_date": DEC_END},
			on_date=DEC_END)

	def test_it_appears_as_its_own_earning_line(self):
		res = self._slip(75_000.0)
		rows = [e for e in res["earnings"] if e["abbr"] == "SNA"]
		self.assertEqual(len(rows), 1, "the 13th month must be one visible line")
		self.assertAlmostEqual(rows[0]["amount"], 75_000.0, places=2)
		self.assertEqual(rows[0]["salary_component"], "Subsídio de Natal")

	def test_it_increases_gross_by_exactly_its_amount(self):
		without = self._slip(0.0)["gross_pay"]
		with_natal = self._slip(75_000.0)["gross_pay"]
		self.assertAlmostEqual(with_natal - without, 75_000.0, places=2)

	def test_it_enters_the_social_security_base(self):
		"""The contribution base at engine.py includes SNA and excludes SFE, which is
		what the settings field descriptions and the settlement both say."""
		without = self._slip(0.0)
		with_natal = self._slip(75_000.0)
		self.assertAlmostEqual(with_natal["ss_base"] - without["ss_base"],
		                       75_000.0, places=2)

	def test_the_employee_contribution_rises_with_it(self):
		without = self._slip(0.0)
		with_natal = self._slip(75_000.0)
		rate = flt(with_natal["ss_employee_rate"])
		self.assertAlmostEqual(
			with_natal["ss_employee_amount"] - without["ss_employee_amount"],
			flt(75_000.0 * rate / 100.0, 2), places=2)

	def test_the_employer_contribution_rises_with_it(self):
		without = self._slip(0.0)
		with_natal = self._slip(75_000.0)
		rate = flt(with_natal["ss_employer_rate"])
		self.assertAlmostEqual(
			with_natal["ss_employer_amount"] - without["ss_employer_amount"],
			flt(75_000.0 * rate / 100.0, 2), places=2)

	def test_it_enters_the_taxable_income(self):
		without = self._slip(0.0)
		with_natal = self._slip(75_000.0)
		self.assertGreater(with_natal["taxable_income"], without["taxable_income"])

	def test_taxable_income_is_gross_less_the_employee_contribution(self):
		"""The order the monthly slip uses: social security comes off before IRT."""
		res = self._slip(75_000.0)
		self.assertAlmostEqual(
			res["taxable_income"],
			flt(res["gross_pay"] - res["ss_employee_amount"], 2), places=2)

	def test_it_can_push_an_exempt_employee_into_tax(self):
		"""A 150,000 base is exempt; the same base plus the 13th month is not."""
		exempt = self._slip(0.0)
		self.assertEqual(exempt["irt_amount"], 0.0)
		taxed = self._slip(75_000.0)
		self.assertGreater(taxed["irt_amount"], 0.0)

	def test_net_pay_reconciles(self):
		res = self._slip(75_000.0)
		self.assertAlmostEqual(
			res["net_pay"], flt(res["gross_pay"] - res["total_deduction"], 2), places=2)

	def test_net_pay_is_never_negative_because_of_it(self):
		for amount in (0.0, 6_250.0, 75_000.0, 500_000.0):
			with self.subTest(natal=amount):
				self.assertGreaterEqual(self._slip(amount)["net_pay"], 0.0)

	def test_it_is_not_prorated_by_attendance(self):
		"""COMPONENTS['SNA']['prorate'] is False: the supplement is an annual
		entitlement, not pay for days worked, so a short month must not cut it."""
		full = self._slip(75_000.0, days=(22, 22))
		half = self._slip(75_000.0, days=(22, 11))
		sna_full = [e for e in full["earnings"] if e["abbr"] == "SNA"][0]["amount"]
		sna_half = [e for e in half["earnings"] if e["abbr"] == "SNA"][0]["amount"]
		self.assertAlmostEqual(sna_full, sna_half, places=2)
		self.assertLess(half["gross_pay"], full["gross_pay"])   # the base still prorates


class TestDecemberTotals(PayrollFixture):
	"""The December run as a whole, across a mixed workforce."""

	WORKFORCE = [("2019-01-01", 12), ("2026-01-01", 12), ("2026-03-15", 9),
	             ("2026-11-20", 1), ("2026-12-31", 0)]

	def test_the_run_total_is_the_sum_of_the_individual_entitlements(self):
		expected = sum(twelfths(m) for _d, m in self.WORKFORCE)
		actual = sum(natal(d) for d, _m in self.WORKFORCE)
		self.assertAlmostEqual(actual, expected, places=2)

	def test_a_mixed_workforce_does_not_all_receive_the_same_amount(self):
		amounts = {natal(d) for d, _m in self.WORKFORCE}
		self.assertGreater(len(amounts), 1,
		                   "proportionality under artigo 238.º n.º 3 means the December "
		                   "run must not pay everybody the same 13th month")

	def test_the_december_gross_rises_by_the_total_supplement(self):
		def gross(natal_amount):
			return engine.compute_slip(
				{"base": BASE, "food_allowance": 0, "transport_allowance": 0,
				 "family_allowance": 0, "company": self.company},
				{"natal_amount": natal_amount, "ferias_amount": 0.0,
				 "payment_days": 22, "total_working_days": 22,
				 "start_date": "%d-12-01" % YEAR, "end_date": DEC_END},
				on_date=DEC_END)["gross_pay"]
		base_total = sum(gross(0.0) for _d, _m in self.WORKFORCE)
		with_total = sum(gross(natal(d)) for d, _m in self.WORKFORCE)
		self.assertAlmostEqual(with_total - base_total,
		                       sum(natal(d) for d, _m in self.WORKFORCE), places=2)


class TestTheEnableSwitch(PayrollFixture):
	"""What `enable_natal = 0` actually does, so the December decision is informed."""

	def test_the_calculation_is_unaffected_by_the_switch(self):
		"""The preview still proposes an amount — the switch gates SAVING, not maths."""
		self.assertGreater(natal("2020-01-01"), 0.0)

	def test_the_component_gate_reads_the_switch(self):
		self.assertFalse(engine.component_enabled({"enable_natal": 0}, "SNA"))
		self.assertTrue(engine.component_enabled({"enable_natal": 1}, "SNA"))

	def test_an_absent_switch_leaves_the_component_enabled(self):
		"""A caller supplying its own settings must not lose components silently."""
		self.assertTrue(engine.component_enabled({}, "SNA"))


class TestSuspensionIsNotProrated(unittest.TestCase):
	"""LEGAL VERIFICATION REQUIRED — stated, not silently assumed.

	Artigo 238.º n.º 3 prorates the supplement for a year of service that was not
	complete. Admission and termination are handled. Suspension is not, because this
	application records it only as an Employee status with no start or end date, so
	there is no period to deduct.
	"""

	def test_the_employee_doctype_offers_suspended_but_records_no_dates(self):
		options = (frappe.get_meta("Employee").get_field("status").options or "").split("\n")
		self.assertIn("Suspended", options)
		meta = frappe.get_meta("Employee")
		for guess in ("suspension_start_date", "suspension_end_date",
		              "suspended_from", "suspended_to"):
			self.assertIsNone(
				meta.get_field(guess),
				"a dated suspension field now exists (%s) — artigo 238.º n.º 3 "
				"proration for suspension can and should now be implemented" % guess)

	def test_the_month_count_ignores_suspension_entirely(self):
		"""Documented so the behaviour is a known gap, not a surprise."""
		self.assertEqual(engine.natal_months("2026-01-01", DEC_END), 12)


class TestTheComponentFlagsMatchTheArithmetic(PayrollFixture):
	"""The catalogue's ss_base flags and the computed base must agree.

	Nothing currently reads the flags — the contribution base is written out explicitly
	in compute_slip — so a flag that disagreed with the arithmetic changed no figure. It
	did mislead a reader into reporting a contradiction that did not exist, and it would
	become a real defect the day anything started consuming the catalogue. This test
	makes the two definitions answer the same way.
	"""

	def _delta_ss_base(self, **inputs):
		def run(**extra):
			args = {"ferias_amount": 0.0, "natal_amount": 0.0, "productivity_bonus": 0,
			        "overtime_amount": 0, "payment_days": 22, "total_working_days": 22,
			        "start_date": "%d-12-01" % YEAR, "end_date": DEC_END}
			args.update(extra)
			return engine.compute_slip(
				{"base": BASE, "food_allowance": 0, "transport_allowance": 0,
				 "family_allowance": 0, "company": self.company},
				args, on_date=DEC_END)["ss_base"]
		return run(**inputs) - run()

	def test_ferias_flag_is_false_and_it_stays_out_of_the_base(self):
		self.assertFalse(engine.COMPONENTS["SFE"]["ss_base"])
		self.assertAlmostEqual(self._delta_ss_base(ferias_amount=75_000.0), 0.0, places=2)

	def test_natal_flag_is_true_and_it_enters_the_base(self):
		self.assertTrue(engine.COMPONENTS["SNA"]["ss_base"])
		self.assertAlmostEqual(self._delta_ss_base(natal_amount=75_000.0),
		                       75_000.0, places=2)

	def test_both_supplements_are_fully_taxable_either_way(self):
		"""Artigo 238.º supplements are remuneration; only the INSS treatment differs."""
		def taxable(**extra):
			args = {"ferias_amount": 0.0, "natal_amount": 0.0, "payment_days": 22,
			        "total_working_days": 22, "start_date": "%d-12-01" % YEAR,
			        "end_date": DEC_END}
			args.update(extra)
			return engine.compute_slip(
				{"base": BASE, "food_allowance": 0, "transport_allowance": 0,
				 "family_allowance": 0, "company": self.company},
				args, on_date=DEC_END)["taxable_income"]
		self.assertGreater(taxable(ferias_amount=75_000.0), taxable())
		self.assertGreater(taxable(natal_amount=75_000.0), taxable())
