# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Overtime: Lei 12/23 art. 237.º n.º 7 (hourly wage) and art. 188.º (100% / 75%).

    SH = basic salary × 12 / (52 × 44)
    NH ≤ 30   HE = NH × SH × 100%
    NH > 30   HE = 30 × SH × 100% + (NH − 30) × SH × 75%

Acceptance figures, basic salary 400 000,00 Kz: 15 h → 31 468,50; 30 h → 62 937,00;
38 h → 62 937,00 + 12 587,40 = 75 524,40. The rule is services/overtime.py.

SAFETY. The formula and engine classes write nothing. TestOvertimeThroughPayroll extends
PayrollFixture: a `_TEST AHR` employee in its own payroll run, every test inside a
savepoint, every record deleted in tearDownClass. Every salary slip a test causes is
registered for deletion, including slips the run creates itself. Nothing is approved
through the workflow, posted to the ledger or paid.
"""

import json
import unittest

import frappe
from frappe.utils import add_days, flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll import engine, recibo, settlement
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (
	PREFIX,
	PayrollFixture,
	_company,
)
from isoft_angola_hr.isoft_angola_hr.services import overtime

BASE = 400000


def calc(hours, base=BASE):
	return overtime.calculate_overtime(base, hours)


# --------------------------------------------------------------------------- #
# The formula — document examples A to J
# --------------------------------------------------------------------------- #
class TestOvertimeFormula(unittest.TestCase):
	def test_hourly_wage_is_basic_times_12_over_52_weeks_of_44_hours(self):
		self.assertEqual(calc(1)["hourly_wage"], 2097.90)
		self.assertEqual(overtime.WEEKS * overtime.WEEKLY_HOURS, 2288)

	def test_a_15_hours_is_31468_50(self):
		r = calc(15)
		self.assertEqual((r["tier1_hours"], r["tier1_amount"]), (15, 31468.50))
		self.assertEqual((r["tier2_hours"], r["tier2_amount"]), (0, 0))
		self.assertEqual(r["total"], 31468.50)

	def test_b_h_30_hours_is_62937_00_all_at_100(self):
		r = calc(30)
		self.assertEqual((r["tier1_hours"], r["tier2_hours"], r["total"]), (30, 0, 62937.00))

	def test_c_i_31_hours_is_30_at_100_and_1_at_75(self):
		r = calc(31)
		self.assertEqual((r["tier1_hours"], r["tier1_rate"], r["tier1_amount"]), (30, 100, 62937.00))
		self.assertEqual((r["tier2_hours"], r["tier2_rate"], r["tier2_amount"]), (1, 75, 1573.43))
		self.assertEqual(r["total"], 64510.43)

	def test_d_38_hours_is_62937_00_plus_12587_40(self):
		r = calc(38)
		self.assertEqual(r["tier1_amount"], 62937.00)
		self.assertEqual(r["tier2_amount"], 12587.40)
		self.assertEqual(r["total"], 75524.40)
		sh = r["hourly_wage"]
		self.assertNotEqual(r["total"], flt(38 * sh * 0.75, 2), "not all 38 hours at 75%")
		self.assertNotEqual(r["total"], flt(38 * sh, 2), "not all 38 hours at 100%")

	def test_e_0_hours_is_0(self):
		self.assertEqual(calc(0)["total"], 0)
		self.assertEqual(calc(None)["total"], 0)
		self.assertEqual(calc("")["total"], 0)

	def test_f_1_hour_is_one_hourly_wage(self):
		self.assertEqual(calc(1)["total"], 2097.90)

	def test_g_29_hours_all_at_100(self):
		r = calc(29)
		self.assertEqual((r["tier1_hours"], r["tier2_hours"], r["total"]), (29, 0, 60839.10))

	def test_j_50_hours_is_30_at_100_and_20_at_75(self):
		r = calc(50)
		self.assertEqual((r["tier1_amount"], r["tier2_hours"], r["tier2_amount"]),
		                 (62937.00, 20, 31468.50))
		self.assertEqual(r["total"], 94405.50)

	def test_every_mandatory_boundary(self):
		"""0 1 29 30 31 32 38 40 50: hours at 100% / hours at 75% / total."""
		expected = {
			0: (0, 0, 0.00), 1: (1, 0, 2097.90), 29: (29, 0, 60839.10), 30: (30, 0, 62937.00),
			31: (30, 1, 64510.43), 32: (30, 2, 66083.85), 38: (30, 8, 75524.40),
			40: (30, 10, 78671.25), 50: (30, 20, 94405.50),
		}
		for hours, (t1, t2, total) in expected.items():
			r = calc(hours)
			self.assertEqual((r["tier1_hours"], r["tier2_hours"], r["total"]), (t1, t2, total), hours)
			self.assertEqual(r["tier1_rate"], 100)
			self.assertEqual(r["tier2_rate"], 75)

	def test_30_hours_does_not_exceed_the_threshold(self):
		r = calc(30)
		self.assertEqual((r["tier2_hours"], r["tier2_amount"]), (0, 0))
		self.assertEqual(r["total"], flt(30 * r["hourly_wage"], 2))

	def test_the_calculation_is_decimal_not_float(self):
		"""Amounts a float product would get wrong come out exact."""
		from decimal import Decimal
		self.assertIsInstance(overtime.hourly_wage(BASE), Decimal)
		self.assertEqual(overtime.hourly_wage(BASE), Decimal("2097.90"))
		self.assertEqual(calc("33.3")["tier2_amount"], 5192.30)   # 3.3 × 2097.90 × 0.75 = 5192.3025

	def test_the_threshold_is_exactly_30(self):
		self.assertEqual(calc(30.5)["tier2_hours"], 0.5)
		self.assertEqual(calc(30)["tier2_hours"], 0)

	def test_the_hourly_wage_is_rounded_before_it_is_multiplied(self):
		"""The document's figures: full precision would give 31 468,53 for 15 hours."""
		self.assertEqual(calc(15)["total"], 31468.50)

	def test_fractional_hours_are_priced_without_float_noise(self):
		self.assertEqual(calc("0.3")["total"], 629.37)
		self.assertEqual(calc(0.1 + 0.2)["total"], 629.37)
		self.assertEqual(calc("2.5")["total"], 5244.75)

	def test_the_parts_add_up_to_the_total(self):
		for h in (0, 1, 15, 29.75, 30, 31, 38, 50, 123.25):
			r = calc(h)
			self.assertEqual(flt(r["tier1_amount"] + r["tier2_amount"], 2), r["total"], h)

	def test_negative_hours_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			calc(-1)

	def test_non_numeric_hours_are_refused_not_read_as_zero(self):
		for bad in ("abc", "1,5", "nan", "inf", True, [3]):
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				calc(bad)

	def test_overtime_against_no_basic_salary_is_refused(self):
		for base in (0, None, -100):
			with self.assertRaises(frappe.ValidationError, msg=repr(base)):
				calc(5, base=base)
		self.assertEqual(calc(0, base=0)["total"], 0, "no hours, no salary: nothing to price")

	def test_hours_beyond_the_hours_in_the_period_are_refused(self):
		self.assertEqual(overtime.max_hours_in_period("2026-10-01", "2026-10-31"), 744)
		overtime.assert_possible(744, "2026-10-01", "2026-10-31")
		with self.assertRaises(frappe.ValidationError):
			overtime.assert_possible(744.5, "2026-10-01", "2026-10-31")


# --------------------------------------------------------------------------- #
# The engine — what overtime does to gross, INSS, taxable, IRT and net
# --------------------------------------------------------------------------- #
class TestOvertimeInTheEngine(unittest.TestCase):
	"""Pure engine calls on a dict profile. Rates and the IRT table are read, not written."""

	@classmethod
	def setUpClass(cls):
		cls.company = _company()
		cls.settings = engine.get_settings()

	def compute(self, hours=0, **inputs):
		profile = frappe._dict(base=BASE, food_allowance=30000, transport_allowance=30000,
		                       family_allowance=0, company=self.company,
		                       irt_table=self.settings.default_irt_table)
		values = {"payment_days": 22, "total_working_days": 22, "overtime_hours": hours,
		          "start_date": "2026-10-01", "end_date": "2026-10-31"}
		values.update(inputs)
		return engine.compute_slip(profile, values, settings=self.settings, on_date="2026-10-31")

	@staticmethod
	def line(res, abbr, side="earnings"):
		return sum(flt(x["amount"]) for x in res[side] if x["abbr"] == abbr)

	def test_overtime_enters_gross_inss_taxable_and_irt_by_the_existing_rules(self):
		without, with_ = self.compute(0), self.compute(38)
		he = 75524.40
		self.assertEqual(self.line(with_, "HEX"), he)
		self.assertEqual(flt(with_["gross_pay"] - without["gross_pay"], 2), he)
		self.assertEqual(flt(with_["ss_base"] - without["ss_base"], 2), he)
		rate = flt(with_["ss_employee_rate"])
		self.assertEqual(self.line(with_, "CTSS3", "deductions"), flt(with_["ss_base"] * rate / 100, 2))
		self.assertEqual(flt(with_["taxable_income"] - without["taxable_income"], 2),
		                 flt(he - (self.line(with_, "CTSS3", "deductions")
		                           - self.line(without, "CTSS3", "deductions")), 2))
		from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import resolve_irt
		self.assertEqual(self.line(with_, "IRT", "deductions"),
		                 flt(resolve_irt(with_["taxable_income"], company=self.company,
		                                 on_date="2026-10-31").amount, 2), "IRT formula unchanged")
		self.assertEqual(with_["net_pay"], flt(with_["gross_pay"] - with_["total_deduction"], 2))

	def test_the_result_carries_the_split(self):
		ot = self.compute(38)["overtime"]
		self.assertEqual((ot["tier1_hours"], ot["tier2_hours"], ot["total"]), (30, 8, 75524.40))

	def test_absence_prorates_the_salary_but_not_the_overtime_hourly_wage(self):
		full, absent = self.compute(15), self.compute(15, payment_days=17)
		self.assertLess(self.line(absent, "SB"), self.line(full, "SB"))
		self.assertEqual(self.line(absent, "HEX"), self.line(full, "HEX"), "SH is on the full basic")
		self.assertEqual(self.line(absent, "HEX"), 31468.50)

	def test_allowances_bonus_and_advance_do_not_change_the_overtime(self):
		res = self.compute(38, productivity_bonus=50000, adiantamento=20000)
		self.assertEqual(self.line(res, "HEX"), 75524.40)
		self.assertEqual(self.line(res, "PPD"), 50000)

	def test_an_amount_cannot_be_handed_in_instead_of_hours(self):
		with self.assertRaises(frappe.ValidationError):
			self.compute(0, overtime_amount=50000)

	def test_negative_or_text_hours_are_refused_by_the_engine(self):
		for bad in (-3, "abc"):
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				self.compute(bad)


# --------------------------------------------------------------------------- #
# Through payroll — preview, run, slip, payslip, settlement
# --------------------------------------------------------------------------- #
class TestOvertimeThroughPayroll(PayrollFixture):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.designation = PREFIX + " Overtime"
		if not frappe.db.exists("Designation", cls.designation):
			cls._track(frappe.get_doc({"doctype": "Designation",
			                           "designation_name": cls.designation}).insert(ignore_permissions=True))
		# An employee of its own, on 400 000,00 Kz. The shared fixture employee's ID is
		# re-issued by the naming series on every run, so a record a run ever leaves behind
		# (a submitted slip) would be inherited by the next run's employee.
		cls.ot_employee = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "Overtime",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"designation": cls.designation, "gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls.ot_profile = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.ot_employee.name,
			"company": cls.company, "from_date": "2020-01-01",
			"base": BASE, "food_allowance": 30000, "transport_allowance": 30000,
		}).insert(ignore_permissions=True))
		assert not frappe.db.exists("Isoft Salary Slip", {"employee": cls.ot_employee.name}), \
			"the overtime employee must start with no salary slip"

	def make_ot_slip(self, submit=False, **kwargs):
		return self.make_slip(submit=submit, employee=self.ot_employee.name,
		                      salary_profile=self.ot_profile.name, **kwargs)

	def track_slips(self):
		"""Register every slip of the overtime employee for deletion, whoever created it."""
		for name in frappe.get_all("Isoft Salary Slip", pluck="name",
		                           filters={"employee": self.ot_employee.name}):
			if ("Isoft Salary Slip", name) not in self._created:
				self._created.append(("Isoft Salary Slip", name))

	def preview(self, inputs=None, validate_attendance=0):
		rows = api.payroll_preview(self.company, self.start, self.end, designation=self.designation,
		                           inputs=json.dumps(inputs) if inputs is not None else None,
		                           validate_attendance=validate_attendance)
		self.assertEqual(len(rows), 1, rows)
		return rows[0]

	def slip_line(self, slip, abbr):
		return sum(flt(e.amount) for e in slip.earnings if e.abbr == abbr)

	# ---------------------------------------------------------------- preview == slip
	def test_preview_overtime_equals_salary_slip_overtime_38_hours(self):
		row = self.preview({self.ot_employee.name: {"overtime_hours": 38}})
		slip = self.make_ot_slip(overtime_hours=38)
		self.track_slips()
		self.assertEqual(row["overtime_amount"], 75524.40)
		self.assertEqual(slip.overtime_amount, row["overtime_amount"])
		self.assertEqual(self.slip_line(slip, "HEX"), 75524.40)
		self.assertEqual((row["overtime"]["tier1_hours"], row["overtime"]["tier2_hours"]), (30, 8))
		for key in ("gross_pay", "taxable_income", "net_pay"):
			self.assertEqual(flt(row[key], 2), flt(slip.get(key), 2), key)
		self.assertEqual(flt(row["irt"], 2), flt(slip.irt_amount, 2))
		self.assertEqual(flt(row["ss"], 2), flt(slip.ss_employee_amount, 2))

	def test_preview_overtime_equals_salary_slip_overtime_15_hours(self):
		row = self.preview({self.ot_employee.name: {"overtime_hours": 15}})
		slip = self.make_ot_slip(overtime_hours=15)
		self.track_slips()
		self.assertEqual(row["overtime_amount"], 31468.50)
		self.assertEqual(slip.overtime_amount, 31468.50)

	def test_preview_defaults_to_the_approved_attendance_hours(self):
		for day, hours in ((self.start, 8), (add_days(self.start, 1), 7)):
			att = frappe.get_doc({"doctype": "Attendance", "employee": self.ot_employee.name,
			                      "attendance_date": day, "status": "Present",
			                      "company": self.company, "custom_overtime_hours": hours})
			att.insert(ignore_permissions=True)
			att.submit()
		row = self.preview(validate_attendance=1)
		self.assertEqual(row["overtime_hours"], 15)
		self.assertEqual(row["overtime_amount"], 31468.50)

	def test_the_run_carries_hours_to_the_slip_and_the_amount_back(self):
		entry = frappe.get_doc("Isoft Payroll Entry", self.payroll_run())
		entry.append("employees", {"employee": self.ot_employee.name, "salary_profile": self.ot_profile.name,
		                           "overtime_hours": 38})
		entry.save(ignore_permissions=True)
		entry.create_salary_slips()
		self.track_slips()
		entry.reload()
		row = [r for r in entry.employees if r.employee == self.ot_employee.name][-1]
		slip = frappe.get_doc("Isoft Salary Slip", row.salary_slip)
		self.assertEqual((slip.overtime_hours, slip.overtime_amount), (38, 75524.40))
		self.assertEqual(row.overtime_amount, 75524.40)

	# ---------------------------------------------------------------- accounting
	def test_the_journal_entry_debits_the_same_overtime_amount(self):
		"""The accrual books the HEX line to the overtime expense account at 75 524,40."""
		self.block_commits()
		slip = self.make_ot_slip(submit=True, overtime_hours=38)
		self.track_slips()
		je = api.make_journal_entry(slip.name)
		hex_account = self.acc_for("HEX")
		debit = frappe.db.sql("""select sum(debit_in_account_currency) from `tabJournal Entry Account`
			where parent = %s and account = %s""", (je, hex_account))[0][0]
		self.assertEqual(flt(debit, 2), 75524.40)
		self.assertEqual(frappe.db.get_value("Journal Entry", je, "docstatus"), 1)

	def acc_for(self, abbr):
		return {r.abbr: r.account for r in frappe.get_single("Isoft HR Settings").component_accounts}[abbr]

	# ---------------------------------------------------------------- payslip
	def test_the_payslip_shows_the_slip_hours_and_amount(self):
		slip = self.make_ot_slip(overtime_hours=38)
		self.track_slips()
		ctx = recibo.get_context(slip)
		self.assertEqual(ctx["overtime"]["hours"], 38)
		self.assertEqual(ctx["overtime"]["amount"], 75524.40)
		self.assertEqual(ctx["days"]["overtime_hours"], 38)
		self.assertIn(75524.40, [flt(e["amount"]) for e in ctx["earnings"]])

	# ---------------------------------------------------------------- refusals
	def test_the_slip_refuses_invalid_hours(self):
		for bad in (-2, "abc", 745):
			with self.assertRaises(frappe.ValidationError, msg=repr(bad)):
				self.make_ot_slip(overtime_hours=bad)
		self.track_slips()

	def test_the_preview_blocks_an_employee_with_invalid_hours(self):
		for bad in (-5, "abc", 10000):
			row = self.preview({self.ot_employee.name: {"overtime_hours": bad}})
			self.assertTrue(row["blocked"], repr(bad))
			self.assertTrue(row["exception"], repr(bad))

	def test_a_screen_that_still_sends_an_amount_is_refused(self):
		row = self.preview({self.ot_employee.name: {"overtime_amount": 50000}})
		self.assertTrue(row["blocked"])
		with self.assertRaises(frappe.ValidationError):
			api.create_payroll_from_preview(self.company, self.start, self.end, json.dumps([
				{"employee": self.ot_employee.name, "employee_name": self.ot_employee.employee_name,
				 "overtime_amount": 50000}]))

	def test_overtime_without_a_basic_salary_is_refused(self):
		frappe.db.set_value("Isoft Salary Profile", self.ot_profile.name, "base", 0)
		with self.assertRaises(frappe.ValidationError):
			self.make_ot_slip(overtime_hours=5)
		self.track_slips()

	def test_a_draft_with_an_amount_but_no_hours_is_not_zeroed_silently(self):
		slip = self.make_ot_slip()
		self.track_slips()
		frappe.db.set_value("Isoft Salary Slip", slip.name, "overtime_amount", 5000)  # pre-change
		slip.reload()
		with self.assertRaises(frappe.ValidationError):
			slip.save(ignore_permissions=True)
		slip.reload()
		slip.overtime_hours = 2
		slip.save(ignore_permissions=True)
		self.assertEqual(slip.overtime_amount, 4195.80, "hours entered: priced by the rule")

	def test_hours_are_refused_while_the_component_is_disabled(self):
		frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 0)
		frappe.clear_cache(doctype="Isoft HR Settings")
		try:
			with self.assertRaises(frappe.ValidationError):
				self.make_ot_slip(overtime_hours=5)
		finally:
			frappe.db.set_value("Isoft HR Settings", "Isoft HR Settings", "enable_overtime", 1)
			frappe.clear_cache(doctype="Isoft HR Settings")
		self.track_slips()

	# ---------------------------------------------------------------- settlement
	def test_a_final_settlement_prices_overtime_with_the_same_rule(self):
		"""Through the Final Settlement document: 38 approved hours in one payroll month,
		basic 400 000 → 30 h at 100% + 8 h at 75% = 75 524,40, by services/overtime.py."""
		from isoft_angola_hr.isoft_angola_hr.api import _cycle_period

		start, _end = _cycle_period(getdate(frappe.utils.nowdate()))
		days = [add_days(start, i) for i in (12, 13, 14, 15)]
		if days[-1] > getdate(frappe.utils.nowdate()):
			start, _end = _cycle_period(add_days(start, -1))
			days = [add_days(start, i) for i in (12, 13, 14, 15)]
		for day, hours in zip(days, (10, 10, 10, 8)):
			att = frappe.get_doc({"doctype": "Attendance", "employee": self.ot_employee.name,
			                      "attendance_date": day, "status": "Present",
			                      "company": self.company, "custom_overtime_hours": hours})
			att.insert(ignore_permissions=True)
			att.submit()
		doc = frappe.get_doc({
			"doctype": "Isoft Final Settlement", "employee": self.ot_employee.name,
			"company": self.company, "date_of_joining": "2020-01-01",
			"termination_date": days[-1], "reason_key": "resignation_with_notice",
			"base": BASE, "salary_period_start": start, "salary_period_end": days[-1],
			"ferias_rate": 50, "natal_rate": 50,
		}).insert(ignore_permissions=True)
		self._created.append(("Isoft Final Settlement", doc.name))
		res = settlement.compute(doc.inputs())
		line = [ln for ln in res["lines"] if ln["key"] == "overtime"][0]
		self.assertEqual(line["amount"], 75524.40)
		self.assertEqual(doc.inputs()["overtime"]["months"][0]["tier2_hours"], 8)
		self.assertIn("100%", line["formula"])
		self.assertIn("75%", line["formula"])

		# The New Settlement form's live figures (settlement_defaults -> settlement_preview)
		# carry the same overtime line as the saved document.
		form = api.settlement_defaults(self.ot_employee.name, str(days[-1]))
		shown = [ln for ln in form["computed"]["lines"] if ln["key"] == "overtime"]
		self.assertEqual([ln["amount"] for ln in shown], [75524.40])
		self.assertEqual(shown[0]["formula"], line["formula"])


	def test_settlement_prices_unpaid_overtime_per_payroll_month(self):
		"""36 h in one payroll month and 10 h in the next: 30 h at 100% + 6 h at 75%, then
		10 h at 100%. Priced as one 46-hour month it would be 88 111,80 instead."""
		from isoft_angola_hr.isoft_angola_hr.api import _cycle_period

		this_start, _end = _cycle_period(getdate(frappe.utils.nowdate()))
		prev_day = add_days(this_start, -8)          # clear of the 17-18 Sep holidays
		days = [(add_days(prev_day, -i), 12) for i in range(3)] + [(this_start, 10)]
		for day, hours in days:
			att = frappe.get_doc({"doctype": "Attendance", "employee": self.ot_employee.name,
			                      "attendance_date": day, "status": "Present",
			                      "company": self.company, "custom_overtime_hours": hours})
			att.insert(ignore_permissions=True)
			att.submit()
		ot = settlement.collect_unpaid_overtime(self.ot_employee.name, self.company, BASE,
		                                        add_days(prev_day, -10), this_start)
		self.assertEqual([m["hours"] for m in ot["months"]], [36, 10])
		self.assertEqual(ot["amount"], flt(62937.00 + 9440.55 + 20979.00, 2))
		lines = settlement._overtime_lines({"overtime": ot}, [])
		self.assertEqual(lines[0]["amount"], ot["amount"])
		settlement._check_reconciles(lines)
