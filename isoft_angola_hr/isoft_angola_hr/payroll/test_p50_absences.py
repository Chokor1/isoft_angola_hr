# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Absence and late-arrival deductions against HR's document of October 2026.

"Absence and Late Arrival Deductions": the basic salary loses (SB × 12 / 2288) × 7,333333
per absence, the food and transport allowances are paid on 26 days, and accumulated
lateness of 40 minutes or more costs (SB × 12 / 2288) × minutes / 60. Every figure here
is the document's own example (basic 400 000, allowances 30 000, two absences, 40
minutes) or follows from it, and every path that pays — engine, slip, preview, payslip —
must produce the same numbers through services/absences.py.
"""

import unittest
from decimal import Decimal

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.services import absences

BASE = 400000
SH = 2097.90          # 400 000 × 12 / 2288, rounded to the cent


class TestTheDocumentExample(unittest.TestCase):
	def test_hourly_wage_is_the_overtime_one(self):
		self.assertEqual(absences.hourly_wage(BASE), Decimal("2097.90"))

	def test_one_absence_costs_a_six_day_week_day_of_the_hourly_wage(self):
		self.assertEqual(absences.basic_deduction(BASE, 1), Decimal("15384.60"))

	def test_two_absences_cost_30769_20(self):
		"""The document prints 30 769,19; its formula gives 30 769,1986 → ,20."""
		self.assertEqual(absences.basic_deduction(BASE, 2), Decimal("30769.20"))

	def test_the_allowances_are_paid_on_26_days(self):
		self.assertEqual(absences.allowance_payable(30000, 2), Decimal("27692.31"))
		self.assertEqual(absences.allowance_payable(30000, 0), Decimal("30000.00"))

	def test_40_late_minutes_cost_1398_60(self):
		self.assertEqual(absences.late_deduction(BASE, 40), Decimal("1398.60"))

	def test_under_40_minutes_nothing_is_deducted(self):
		for minutes in (0, 1, 39, 39.99):
			self.assertEqual(absences.late_deduction(BASE, minutes), Decimal("0.00"), minutes)

	def test_the_whole_example_in_one_call(self):
		out = absences.calculate(BASE, absent_days=2, late_minutes=40,
		                         allowances={"SDA": 30000, "SDT": 30000})
		self.assertEqual(out["hourly_wage"], SH)
		self.assertEqual(out["absence_deduction"], 30769.20)
		self.assertEqual(out["late_deduction"], 1398.60)
		self.assertTrue(out["late_applied"])
		self.assertEqual(out["total_deduction"], 32167.80)
		self.assertEqual(out["allowances"]["SDA"], {"full": 30000.0, "payable": 27692.31, "deduction": 2307.69})
		self.assertEqual(out["allowances"]["SDT"]["payable"], 27692.31)


class TestTheRulesBehindIt(unittest.TestCase):
	def test_an_absence_is_worth_the_same_whatever_the_month(self):
		"""Nothing in the service reads a calendar: 24 or 26 working days, one absence
		is 15 384,60."""
		self.assertEqual(absences.basic_deduction(BASE, 1), Decimal("15384.60"))
		self.assertEqual(absences.DAILY_HOURS * 6, absences.overtime.WEEKLY_HOURS)

	def test_a_half_day_is_half_an_absence(self):
		self.assertEqual(absences.basic_deduction(BASE, 0.5), Decimal("7692.30"))
		self.assertEqual(absences.allowance_payable(26000, 0.5), Decimal("25500.00"))

	def test_partial_hours_are_priced_at_the_hourly_wage(self):
		self.assertEqual(absences.basic_deduction(BASE, 0, 3), Decimal("6293.70"))
		self.assertEqual(absences.basic_deduction(BASE, 1, 3), Decimal("21678.30"))

	def test_hours_do_not_touch_the_allowances(self):
		out = absences.calculate(BASE, absent_hours=3, allowances={"SDA": 30000})
		self.assertEqual(out["allowances"]["SDA"]["payable"], 30000.0)

	def test_lateness_never_touches_the_allowances(self):
		out = absences.calculate(BASE, late_minutes=90, allowances={"SDA": 30000, "SDT": 30000})
		self.assertEqual(out["allowances"]["SDA"]["payable"], 30000.0)
		self.assertEqual(out["late_deduction"], 3146.85)

	def test_from_the_threshold_every_minute_counts(self):
		"""40 minutes are deducted in full, not only the part above 40."""
		self.assertEqual(absences.late_deduction(BASE, 40), Decimal("1398.60"))
		self.assertEqual(absences.late_deduction(BASE, 41), Decimal("1433.57"))

	def test_26_or_more_absences_leave_no_allowance(self):
		self.assertEqual(absences.allowance_payable(30000, 26), Decimal("0.00"))
		self.assertEqual(absences.allowance_payable(30000, 30), Decimal("0.00"))

	def test_rounding_is_half_up_at_the_cent_in_decimal(self):
		# 2 097,90 × 7,3333… × 0,5 = 7 692,30 exactly; 3 hours: 6 293,70; 1 absence +
		# 1 minute of lateness below the threshold: nothing for the minute
		self.assertEqual(absences.calculate(BASE, absent_days=1, late_minutes=1)["total_deduction"], 15384.60)
		self.assertIsInstance(absences.basic_deduction(BASE, 1), Decimal)

	def test_nothing_absent_costs_nothing(self):
		out = absences.calculate(BASE, allowances={"SDA": 30000})
		self.assertEqual((out["absence_deduction"], out["late_deduction"], out["allowances"]["SDA"]["payable"]),
		                 (0.0, 0.0, 30000.0))
		self.assertFalse(out["late_applied"])


class TestRefusals(unittest.TestCase):
	def test_negative_and_non_numeric_counts_are_refused(self):
		for bad in (-1, "abc", float("inf")):
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				absences.basic_deduction(BASE, bad)
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				absences.late_deduction(BASE, bad)
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				absences.allowance_payable(30000, bad)

	def test_an_absence_without_a_basic_salary_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			absences.basic_deduction(0, 1)
		with self.assertRaises(frappe.ValidationError):
			absences.late_deduction(None, 40)

	def test_no_absence_against_no_salary_is_simply_zero(self):
		self.assertEqual(absences.calculate(0)["total_deduction"], 0.0)


# --------------------------------------------------------------------------- #
# Through payroll — attendance, preview, slip, payslip, accounting
# --------------------------------------------------------------------------- #
import json

from frappe.utils import add_days, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll import recibo
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
	absence_inputs, compute_working_days)


class TestAbsencesThroughPayroll(PayrollFixture):
	"""The document's employee (400 000 basic, 30 000 food, 30 000 transport) with the
	document's attendance, through every path that pays."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.designation = PREFIX + " Absences"
		if not frappe.db.exists("Designation", cls.designation):
			cls._track(frappe.get_doc({"doctype": "Designation",
			                           "designation_name": cls.designation}).insert(ignore_permissions=True))
		cls.abs_employee = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Absences",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"designation": cls.designation, "gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls.abs_profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.abs_employee.name,
			"company": cls.company, "from_date": "2020-01-01",
			"base": BASE, "food_allowance": 30000, "transport_allowance": 30000,
		}).insert(ignore_permissions=True))

	# -- helpers --------------------------------------------------------------
	def weekdays(self, n, offset=0):
		"""The first ``n`` Monday–Friday dates of the period after ``offset`` days."""
		out, d = [], add_days(self.start, offset)
		while len(out) < n:
			if getdate(d).weekday() < 5:
				out.append(getdate(d))
			d = add_days(d, 1)
		return out

	def absent(self, day, status="Absent"):
		doc = frappe.get_doc({"doctype": "Attendance", "employee": self.abs_employee.name,
		                      "company": self.company, "attendance_date": day, "status": status})
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	def occurrence(self, day, occurrence_type, hours=0, status="Unjustified"):
		values = {"doctype": "Isoft Attendance Occurrence", "employee": self.abs_employee.name,
		          "occurrence_date": day, "occurrence_type": occurrence_type, "hours": hours,
		          "status": status}
		if status == "Justified":
			name = PREFIX + " Motivo"
			if not frappe.db.exists("Isoft Absence Reason", name):
				frappe.get_doc({"doctype": "Isoft Absence Reason", "reason": name}).insert(ignore_permissions=True)
				self._created.append(("Isoft Absence Reason", name))
			values["justification_reason"] = name
		doc = frappe.get_doc(values)
		doc.flags.ignore_permissions = True
		return doc.insert(ignore_permissions=True)

	def slip(self, submit=False, **kwargs):
		return self.make_slip(submit=submit, employee=self.abs_employee.name,
		                      salary_profile=self.abs_profile.name, validate_attendance=1, **kwargs)

	def track_slips(self):
		for name in frappe.get_all("Isoft Salary Slip", pluck="name",
		                           filters={"employee": self.abs_employee.name}):
			if ("Isoft Salary Slip", name) not in self._created:
				self._created.append(("Isoft Salary Slip", name))

	def preview_row(self):
		rows = api.payroll_preview(self.company, self.start, self.end, designation=self.designation,
		                           validate_attendance=1)
		self.assertEqual(len(rows), 1, rows)
		return rows[0]

	def line(self, slip, abbr):
		return sum(flt(e.amount) for e in slip.earnings if e.abbr == abbr)

	# -- the document's example --------------------------------------------
	def test_two_absences_price_exactly_as_the_document(self):
		for day in self.weekdays(2):
			self.absent(day)
		s = self.slip()
		self.track_slips()
		self.assertEqual((s.absent_days, s.absent_hours, s.late_minutes), (2, 0, 0))
		self.assertEqual(s.absence_deduction, 30769.20)
		self.assertEqual(self.line(s, "SB"), 369230.80)          # 400 000 − 30 769,20
		self.assertEqual(self.line(s, "SDA"), 27692.31)
		self.assertEqual(self.line(s, "SDT"), 27692.31)
		self.assertEqual(self.line(s, "FAL"), 30769.20)          # informative row
		self.assertEqual(flt(s.gross_pay, 2), flt(369230.80 + 27692.31 * 2, 2))

	def test_the_value_of_an_absence_does_not_depend_on_the_month(self):
		"""Whatever the period's working days, one absence is 15 384,60."""
		self.absent(self.weekdays(1)[0])
		s = self.slip()
		self.track_slips()
		twd, _ = compute_working_days(self.abs_employee.name, self.start, self.end, validate_attendance=1)
		self.assertEqual(s.absence_deduction, 15384.60)
		self.assertNotEqual(flt(BASE / twd, 2), 15384.60, "the old base ÷ working days must not be what is paid")

	def test_40_late_minutes_are_deducted_and_39_are_not(self):
		d1, d2 = self.weekdays(2)
		self.occurrence(d1, "Lateness", hours=0.5)            # 30 min
		s = self.slip()
		self.track_slips()
		self.assertEqual((s.late_minutes, s.late_deduction), (30, 0))
		self.occurrence(d2, "Lateness", hours=10 / 60.0)      # + 10 min = 40
		s.reload()
		s.save(ignore_permissions=True)                       # recomputed from attendance
		s2 = frappe.get_doc("Isoft Salary Slip", s.name)
		self.assertEqual(s2.late_minutes, 40)
		self.assertEqual(s2.late_deduction, 1398.60)
		self.assertEqual(self.line(s2, "SB"), 398601.40)
		self.assertEqual(self.line(s2, "SDA"), 30000, "lateness never touches the allowances")
		self.assertEqual(self.line(s2, "ATR"), 1398.60)

	def test_the_preview_and_the_slip_agree(self):
		d1, d2, d3 = self.weekdays(3)
		self.absent(d1)
		self.absent(d2, "Half Day")
		self.occurrence(d3, "Lateness", hours=1)
		row = self.preview_row()
		s = self.slip()
		self.track_slips()
		self.assertEqual(row["absences"]["absent_days"], 1.5)
		self.assertEqual(row["absences"]["late_minutes"], 60)
		self.assertEqual(row["absence"], flt(s.absence_deduction + s.late_deduction, 2))
		for key, field in (("base", "SB"), ("food", "SDA"), ("transport", "SDT")):
			self.assertEqual(flt(row[key], 2), flt(self.line(s, field), 2), key)
		for key in ("gross_pay", "taxable_income", "net_pay"):
			self.assertEqual(flt(row[key], 2), flt(s.get(key), 2), key)
		self.assertEqual(row["payment_days"], flt(row["total_working_days"]) - 1.5)

	def test_what_counts_as_an_absence(self):
		d = self.weekdays(6)
		self.absent(d[0])                                        # 1
		self.absent(d[1], "Half Day")                            # 0,5
		self.occurrence(d[2], "Full Day")                        # 1
		self.occurrence(d[3], "Partial Absence", hours=3)        # 3 h
		self.occurrence(d[4], "Early Exit", hours=1.5)           # 1,5 h
		self.occurrence(d[5], "Full Day", status="Justified")    # nothing
		a = absence_inputs(self.abs_employee.name, self.start, self.end)
		self.assertEqual((a["days"], a["hours"], a["late_minutes"]), (2.5, 4.5, 0))
		s = self.slip()
		self.track_slips()
		# 2,5 × 15 384,60 + 4,5 × 2 097,90 = 38 461,50 + 9 440,55
		self.assertEqual(s.absence_deduction, 47902.05)
		self.assertEqual(self.line(s, "SDA"), 27115.38)          # 30 000 / 26 × 23,5

	def test_a_justified_or_pending_occurrence_cancels_the_attendance_absence(self):
		d1, d2 = self.weekdays(2)
		self.absent(d1); self.occurrence(d1, "Full Day", status="Justified")
		self.absent(d2); self.occurrence(d2, "Full Day", status="Pending Justification")
		self.assertEqual(absence_inputs(self.abs_employee.name, self.start, self.end)["days"], 0)

	def test_an_absence_on_a_non_working_day_costs_nothing(self):
		d = self.start
		while getdate(d).weekday() != 6:                        # the first Sunday
			d = add_days(d, 1)
		self.absent(d)
		self.assertEqual(absence_inputs(self.abs_employee.name, self.start, self.end)["days"], 0)

	def test_a_slip_without_attendance_validation_deducts_nothing(self):
		self.absent(self.weekdays(1)[0])
		s = self.make_slip(submit=False, employee=self.abs_employee.name,
		                   salary_profile=self.abs_profile.name, validate_attendance=0)
		self.track_slips()
		self.assertEqual((s.absent_days, s.absence_deduction, self.line(s, "SB")), (0, 0, BASE))

	def test_the_payslip_prints_the_absence_rows_as_informative(self):
		for day in self.weekdays(2):
			self.absent(day)
		self.occurrence(self.weekdays(1, 7)[0], "Lateness", hours=1)
		s = self.slip()
		self.track_slips()
		ctx = recibo.get_context(s)
		memos = {m["code"]: m["amount"] for m in ctx["memos"]}
		self.assertEqual(memos.get("F03"), 30769.20)
		self.assertEqual(memos.get("F04"), 2097.90)
		self.assertEqual(flt(ctx["total_earnings"], 2), flt(s.gross_pay, 2), "memo rows stay out of the total")
		self.assertNotIn("F03", [e["code"] for e in ctx["earnings"]])

	def test_the_journal_entry_books_the_net_lines_and_no_memo_row(self):
		self.block_commits()
		for day in self.weekdays(2):
			self.absent(day)
		s = self.slip(submit=True)
		self.track_slips()
		je = api.make_journal_entry(s.name)
		acc = {r.abbr: r.account for r in frappe.get_single("Isoft HR Settings").component_accounts}
		debit = lambda a: flt(frappe.db.sql("""select sum(debit_in_account_currency) from `tabJournal Entry Account`
			where parent=%s and account=%s""", (je, a))[0][0], 2)
		self.assertEqual(debit(acc["SB"]), 369230.80)
		self.assertEqual(debit(acc["SDA"]), 27692.31)
		total = frappe.db.sql("select sum(debit_in_account_currency), sum(credit_in_account_currency) from `tabJournal Entry Account` where parent=%s", je)[0]
		self.assertEqual(flt(total[0], 2), flt(total[1], 2))
