# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The 2026 IRT table (Grupo A), pinned bracket by bracket against Lei n.º 14/25 (OGE 2026).

An earlier audit read the step from the first bracket to the second - income of 150,000
taxed at nil, income of 150,001 carrying a *parcela fixa* of 12,500 - as an internal
contradiction, on the assumption that a parcela fixa must equal the tax accumulated at the
bracket's lower bound. That assumption does not hold for the Angolan table. Lei n.º 14/25
publishes the first three brackets as

    1st   0 - 150,000          parcela 0          exempt
    2nd   150,001 - 200,000    parcela 12,500     16% on the excess over 150,000
    3rd   200,001 - 300,000    parcela 31,250     18% on the excess over 200,000

so the steps at 150,000 and 200,000 are what the statute prescribes, not a data-entry
error. From the third bracket upward the published parcelas do coincide with the
accumulated tax, which is why only those first two boundaries step at all.

These tests therefore do two separate jobs:

* pin every published 2026 value, so a future edit to the table has to be deliberate;
* pin the *behaviour* at every boundary, including the two statutory steps, so that the
  step is recorded as intended rather than rediscovered and "fixed" by the next reader.

They assert against the table as published. They do not assert that the published table is
good tax policy, and they do not re-derive it.
"""

import unittest

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import resolve_irt

#: Lei n.º 14/25 (OGE 2026), IRT Grupo A - monthly table.
#: (from, to, excess_over, parcela_fixa, rate %).  `to = 0` means open-ended.
LEI_14_25 = [
	(0,          150_000,     0,          0.0,         0.0),
	(150_000,    200_000,     150_000,    12_500.0,    16.0),
	(200_000,    300_000,     200_000,    31_250.0,    18.0),
	(300_000,    500_000,     300_000,    49_250.0,    19.0),
	(500_000,    1_000_000,   500_000,    87_250.0,    20.0),
	(1_000_000,  1_500_000,   1_000_000,  187_250.0,   21.0),
	(1_500_000,  2_000_000,   1_500_000,  292_250.0,   22.0),
	(2_000_000,  2_500_000,   2_000_000,  402_250.0,   23.0),
	(2_500_000,  5_000_000,   2_500_000,  517_250.0,   24.0),
	(5_000_000,  10_000_000,  5_000_000,  1_117_250.0, 24.5),
	(10_000_000, 0,           10_000_000, 2_342_250.0, 25.0),
]

#: Income -> IRT, computed from the table above by hand and checked against the statute.
#: Every bracket edge is present twice: the last kwanza inside it and the first outside.
BOUNDARIES = [
	(0,          0.00),
	(1,          0.00),
	(149_999,    0.00),
	(150_000,    0.00),           # still exempt
	(150_001,    12_500.16),      # statutory step: parcela 12,500 applies at once
	(200_000,    20_500.00),
	(200_001,    31_250.18),      # statutory step
	(300_000,    49_250.00),
	(300_001,    49_250.19),      # continuous from here up
	(500_000,    87_250.00),
	(500_001,    87_250.20),
	(1_000_000,  187_250.00),
	(1_000_001,  187_250.21),
	(1_500_000,  292_250.00),
	(1_500_001,  292_250.22),
	(2_000_000,  402_250.00),
	(2_000_001,  402_250.23),
	(2_500_000,  517_250.00),
	(2_500_001,  517_250.24),
	(5_000_000,  1_117_250.00),
	(5_000_001,  1_117_250.25),
	(10_000_000, 2_342_250.00),
	(10_000_001, 2_342_250.25),
	(50_000_000, 12_342_250.00),  # open-ended top bracket
]


def _table():
	name = frappe.db.get_single_value("Isoft HR Settings", "default_irt_table")
	return frappe.get_doc("IRT Table", name)


class TestConfiguredTableMatchesLei1425(unittest.TestCase):
	"""Every published value, in order."""

	@classmethod
	def setUpClass(cls):
		cls.doc = _table()
		cls.rows = sorted(cls.doc.brackets, key=lambda r: flt(r.from_amount))

	def test_the_table_has_the_published_number_of_brackets(self):
		self.assertEqual(len(self.rows), len(LEI_14_25),
		                 "Lei n.º 14/25 publishes %d brackets; the table has %d."
		                 % (len(LEI_14_25), len(self.rows)))

	def test_every_bracket_matches_the_published_values(self):
		for i, (want, row) in enumerate(zip(LEI_14_25, self.rows), start=1):
			frm, to, excess, parcela, rate = want
			with self.subTest(bracket=i, published_from=frm):
				self.assertEqual(flt(row.from_amount), float(frm), "bracket %d: from" % i)
				self.assertEqual(flt(row.to_amount), float(to), "bracket %d: to" % i)
				self.assertEqual(flt(row.excess_over), float(excess),
				                 "bracket %d: excess over" % i)
				self.assertEqual(flt(row.parcela_fixa), parcela,
				                 "bracket %d: parcela fixa" % i)
				self.assertEqual(flt(row.rate), rate, "bracket %d: rate" % i)

	def test_the_first_bracket_starts_at_zero(self):
		self.assertEqual(flt(self.rows[0].from_amount), 0.0)

	def test_the_last_bracket_is_open_ended(self):
		"""Otherwise a high earner matches no bracket and payroll stops mid-run."""
		self.assertEqual(flt(self.rows[-1].to_amount), 0.0)

	def test_brackets_are_contiguous(self):
		"""No gap and no overlap between one bracket's ceiling and the next one's floor."""
		for i in range(len(self.rows) - 1):
			with self.subTest(boundary=i + 1):
				self.assertEqual(flt(self.rows[i].to_amount),
				                 flt(self.rows[i + 1].from_amount))


class TestBoundaryValues(unittest.TestCase):
	"""What the engine actually charges at and around every bracket edge."""

	@classmethod
	def setUpClass(cls):
		cls.doc = _table()

	def test_every_boundary_value(self):
		for income, expected in BOUNDARIES:
			with self.subTest(income=income):
				got = resolve_irt(income, table=self.doc).amount
				self.assertAlmostEqual(
					got, expected, places=2,
					msg="income %s: expected %s, got %s" % (income, expected, got))

	def test_zero_and_negative_income_pay_nothing(self):
		for income in (0, -1, -150_000):
			with self.subTest(income=income):
				self.assertEqual(resolve_irt(income, table=self.doc).amount, 0.0)

	def test_the_trace_names_the_bracket_that_was_applied(self):
		"""A submitted slip must stay explainable after the table changes."""
		trace = resolve_irt(250_000, table=self.doc)
		self.assertEqual(flt(trace.bracket_from), 200_000.0)
		self.assertEqual(flt(trace.bracket_to), 300_000.0)
		self.assertEqual(flt(trace.parcela_fixa), 31_250.0)
		self.assertEqual(flt(trace.rate), 18.0)
		self.assertEqual(flt(trace.excess_over), 200_000.0)
		self.assertAlmostEqual(trace.amount, 31_250.0 + 50_000 * 0.18, places=2)


class TestTaxNeverDecreases(unittest.TestCase):
	"""Tax must be non-decreasing in income across the whole range.

	This is the property that would actually be broken by a mistyped bracket - a parcela
	fixa lower than the one before it, or a negative rate - and it holds regardless of
	where the statute chooses to place its steps.
	"""

	@classmethod
	def setUpClass(cls):
		cls.doc = _table()

	def _sweep(self, points):
		last_income, last_tax = None, None
		for income in points:
			tax = resolve_irt(income, table=self.doc).amount
			self.assertGreaterEqual(
				tax, 0.0, "income %s produced negative tax %s" % (income, tax))
			if last_tax is not None:
				self.assertGreaterEqual(
					tax, last_tax,
					"tax fell from %s at income %s to %s at income %s"
					% (last_tax, last_income, tax, income))
			last_income, last_tax = income, tax

	def test_across_every_boundary(self):
		self._sweep([i for i, _t in BOUNDARIES])

	def test_across_a_dense_sweep_of_the_common_range(self):
		"""Every 500 AKZ from nil to 1.5m - the band nearly all employees sit in."""
		self._sweep(range(0, 1_500_001, 500))

	def test_across_the_upper_range(self):
		self._sweep(range(1_500_000, 60_000_001, 250_000))

	def test_effective_rate_never_exceeds_the_top_marginal_rate(self):
		"""A parcela fixa that was too large would show up here."""
		for income in (150_001, 200_001, 300_001, 1_000_001, 5_000_001, 50_000_000):
			with self.subTest(income=income):
				tax = resolve_irt(income, table=self.doc).amount
				self.assertLessEqual(
					tax / income, 0.25,
					"income %s pays an effective %.2f%%, above the 25%% top marginal rate"
					% (income, 100.0 * tax / income))


class TestTheTwoStatutoryStepsAreRecordedDeliberately(unittest.TestCase):
	"""The steps at 150,000 and 200,000 are the statute's, and are pinned as such.

	They mean an employee just over a threshold takes home less than one just under it:
	at 150,000 the difference is 12,500 AKZ. That is a real consequence HR should know
	about when setting a salary near a threshold, and it is NOT a defect in this
	application. The test exists so that nobody "fixes" the table into disagreeing with
	the law.
	"""

	@classmethod
	def setUpClass(cls):
		cls.doc = _table()

	def _net_of_tax(self, income):
		return income - resolve_irt(income, table=self.doc).amount

	def test_the_step_at_150000_is_exactly_the_published_parcela(self):
		below = resolve_irt(150_000, table=self.doc).amount
		above = resolve_irt(150_001, table=self.doc).amount
		self.assertEqual(below, 0.0)
		self.assertAlmostEqual(above - below, 12_500.16, places=2)

	def test_the_step_at_200000_is_the_published_difference(self):
		below = resolve_irt(200_000, table=self.doc).amount     # 20,500.00
		above = resolve_irt(200_001, table=self.doc).amount     # 31,250.18
		self.assertAlmostEqual(above - below, 10_750.18, places=2)

	def test_take_home_dips_at_the_two_steps_and_nowhere_else(self):
		"""Confirms the statute produces exactly two such points, not more."""
		dips = []
		for income in range(0, 5_000_001, 1_000):
			if income and self._net_of_tax(income) < self._net_of_tax(income - 1_000):
				dips.append(income)
		self.assertEqual(dips, [151_000, 201_000],
		                 "expected take-home to dip only just past the two published "
		                 "steps, found dips at %s" % dips)


class TestEffectiveDating(unittest.TestCase):
	"""The 2026 table must not silently reprice periods it does not govern."""

	@classmethod
	def setUpClass(cls):
		cls.doc = _table()

	def test_the_table_declares_an_effective_from_date(self):
		self.assertTrue(self.doc.effective_from,
		                "an IRT Table with no effective date cannot be matched to a period")

	def test_the_trace_carries_the_effective_date(self):
		trace = resolve_irt(250_000, table=self.doc)
		self.assertEqual(trace.effective_from, self.doc.effective_from)
		self.assertEqual(trace.table, self.doc.name)
