# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The Christmas bonus (Subsídio de Natal) as its own run — HR's document of November 2026.

    SN  = Basic Salary × 50%                       (12 months or more of service)
    SN  = ((Basic Salary × 50%) / 12) × M          (fewer than 12 months)
    IRT = FP + (SN − EXC) × %                      (on the bonus alone)
    T   = SN − IRT                                 (no INSS)

Pure: the formula and the IRT table, nothing is written.
"""

import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr.payroll import christmas, engine


class TestTheDocumentExamples(unittest.TestCase):
	def test_twelve_months(self):
		res = christmas.compute_bonus(400000, 50, 12, "2026-12-15")
		self.assertEqual(res["natal_amount"], 200000.0)
		self.assertEqual(res["irt_amount"], 20500.0)
		self.assertEqual(res["net_pay"], 179500.0)
		self.assertEqual(res["gross_pay"], 200000.0)
		self.assertEqual(res["ss_employee_amount"], 0)
		self.assertEqual(res["ss_employer_amount"], 0)
		self.assertEqual([e["abbr"] for e in res["earnings"]], ["SNA"])
		self.assertEqual([d["abbr"] for d in res["deductions"]], ["IRT"])
		self.assertEqual((res["natal_months"], res["natal_base"]), (12, 400000.0))

	def test_eight_months(self):
		res = christmas.compute_bonus(400000, 50, 8, "2026-12-15")
		self.assertEqual(res["natal_amount"], 133333.33)
		self.assertEqual(res["irt_amount"], 0.0)
		self.assertEqual(res["net_pay"], 133333.33)
		self.assertEqual(res["deductions"], [])

	def test_override_replaces_the_amount(self):
		res = christmas.compute_bonus(400000, 50, 8, "2026-12-15", override=150000)
		self.assertEqual(res["natal_amount"], 150000.0)
		self.assertEqual(res["irt_amount"], engine.resolve_irt(150000.0, on_date="2026-12-15").amount)
		self.assertEqual(res["net_pay"], 150000.0 - res["irt_amount"])

	def test_full_year_amount(self):
		self.assertEqual(christmas.full_year_amount(400000, 50), 200000.0)


class TestMonths(unittest.TestCase):
	"""Complete months of service in the bonus year, counted to 31 December."""

	def test_months(self):
		for joined, months in (("2020-03-10", 12), ("2026-01-01", 12), ("2026-05-15", 7),
		                       ("2026-12-01", 1), ("2026-12-02", 0), ("2027-01-05", 0)):
			self.assertEqual(christmas.bonus_months(joined, 2026), months, joined)

	def test_run_types(self):
		self.assertEqual((christmas.RUN_SALARY, christmas.RUN_CHRISTMAS), ("Salary", "Christmas Bonus"))
