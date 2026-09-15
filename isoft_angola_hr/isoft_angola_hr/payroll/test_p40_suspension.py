# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Suspension proration — NOT SUPPORTED, and never silently.

The decision and its reasoning are in ``docs/suspension-proration.md``. Option B was
taken: suspension proration is not implemented, because the data to implement it does
not exist (``Employee.status`` has no dates and Employee carries no Version history) and
because whether a given suspension interrupts "months of service" under Lei n.º 12/23
artigo 238.º n.º 3 is a legal determination nobody has made. **LEGAL VERIFICATION
REQUIRED.**

What these tests hold is the half that IS a promise: the gap must stay visible. A
suspended employee is excluded from payroll rather than paid a wrong figure, the
readiness panel says so in words that name the status, and the run-level warning appears
where — and only where — a suspended employee actually exists.

If somebody later implements proration, these tests are the ones that should fail.
"""

import io
import os
import unittest

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (PREFIX,
                                                                        PayrollFixture)
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness

#: docs/ lives at the repository root, four levels above this module.
DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
	os.path.abspath(__file__))))), "docs", "SUSPENSION_PRORATION.md")


class TestTheDataModelReallyHasNoDates(unittest.TestCase):
	"""The premise of the decision. If it stops being true, revisit Option A."""

	def test_the_status_field_offers_suspended(self):
		options = (frappe.get_meta("Employee").get_field("status").options or "").split("\n")
		self.assertIn("Suspended", options)

	def test_no_suspension_dates_exist_on_the_employee(self):
		meta = frappe.get_meta("Employee")
		dated = [df.fieldname for df in meta.fields
		         if "suspen" in df.fieldname and df.fieldtype in ("Date", "Datetime")]
		self.assertEqual(dated, [],
		                 "suspension now has dates — the decision record is out of date")

	def test_no_suspension_doctype_exists(self):
		self.assertFalse(frappe.db.exists("DocType", "Suspension Period"))
		self.assertFalse(frappe.db.exists("DocType", "Isoft Suspension Period"))

	def test_the_decision_is_written_down(self):
		self.assertTrue(os.path.exists(DOC),
		                "the suspension decision has no record; it is folklore")
		text = io.open(DOC, encoding="utf-8").read()
		for needle in ("NOT SUPPORTED", "LEGAL VERIFICATION REQUIRED", "238"):
			self.assertIn(needle, text)


class TestTheCalculationDoesNotPretend(unittest.TestCase):

	def test_the_month_count_ignores_suspension_entirely(self):
		"""Two identical employees, one of whom was suspended, get the same count —
		which is the gap, stated as an assertion rather than left to December."""
		self.assertEqual(engine.natal_months("2026-01-01", "2026-12-31"), 12)

	def test_nothing_in_the_engine_reads_a_suspension(self):
		import inspect
		src = inspect.getsource(engine)
		self.assertNotIn("Suspended", src,
		                 "the engine now reads suspension; the decision record must be "
		                 "updated and the legal rule confirmed first")

	def test_the_docstring_says_suspension_is_not_deducted(self):
		self.assertIn("Suspension", engine.default_natal.__doc__)


class TestASuspendedEmployeeIsExcludedNotMispaid(PayrollFixture):

	def _suspend(self):
		frappe.db.set_value("Employee", self.employee.name, "status", "Suspended")
		frappe.clear_cache()
		self.addCleanup(self._reactivate)

	def _reactivate(self):
		frappe.db.set_value("Employee", self.employee.name, "status", "Active")
		frappe.clear_cache()

	def test_eligibility_is_restricted_to_active_employees(self):
		self._suspend()
		eligible = readiness._eligible_employees(self.company, self.start, self.end)
		self.assertNotIn(self.employee.name, [e.name for e in eligible],
		                 "a suspended employee entered payroll and would be paid a "
		                 "figure nothing prorated")

	def test_an_active_employee_is_eligible(self):
		eligible = readiness._eligible_employees(self.company, self.start, self.end)
		self.assertIn(self.employee.name, [e.name for e in eligible])


class TestTheGapIsVisible(PayrollFixture):

	def _suspend(self):
		frappe.db.set_value("Employee", self.employee.name, "status", "Suspended")
		frappe.clear_cache()
		self.addCleanup(lambda: (
			frappe.db.set_value("Employee", self.employee.name, "status", "Active"),
			frappe.clear_cache()))

	def _config(self):
		return {i["key"]: i for i in
		        readiness.configuration_status(self.company, on_date=self.end)}

	def test_no_warning_where_nobody_is_suspended(self):
		self.assertNotIn("suspension_proration", self._config(),
		                 "a permanent warning nobody can act on trains people to ignore "
		                 "the panel")

	def test_a_warning_appears_as_soon_as_somebody_is(self):
		self._suspend()
		row = self._config().get("suspension_proration")
		self.assertIsNotNone(row, "a suspended employee produced no warning at all")
		self.assertFalse(row["ok"])
		self.assertEqual(row["severity"], readiness.WARNING)

	def test_the_warning_cites_the_article(self):
		self._suspend()
		self.assertIn("238", self._config()["suspension_proration"]["hint"])

	def test_the_warning_names_the_manual_remedy(self):
		self._suspend()
		hint = self._config()["suspension_proration"]["hint"]
		self.assertIn("hand", hint.lower())

	def test_the_exception_code_exists_and_is_a_warning_not_a_blocker(self):
		severity, label = readiness.EXCEPTIONS["EXC-026"]
		self.assertEqual(severity, readiness.WARNING)
		self.assertIn("uspen", label)

	def test_the_missing_employee_message_names_the_status(self):
		"""EXC-025 used to say only "not eligible", which reads the same for somebody who
		left, somebody made inactive and somebody suspended."""
		import inspect
		src = inspect.getsource(readiness.evaluate)
		self.assertIn("EXC-026", src)
		self.assertIn('row.get("status")', src)
