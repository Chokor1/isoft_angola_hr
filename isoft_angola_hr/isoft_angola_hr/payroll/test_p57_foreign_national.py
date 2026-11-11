# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Foreign nationals are exempt from INSS (HR, November 2026).

Employee.custom_foreign_national: no INSS is calculated for the employee — neither the
employee's 3% nor the company's 8% — on salary slips, the payroll preview and final
settlements; IRT is then taken on the whole taxable income. They are left out of the INSS
declaration and report, and a missing INSS number is not a finding for them. Angolan
employees are calculated exactly as before.
"""

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestForeignNationals(PayrollFixture):
	def setUp(self):
		frappe.db.set_value("Employee", self.employee.name, {
			"custom_foreign_national": 0, "custom_nif": "5417000009", "custom_inss_number": "0123456789"})

	def tearDown(self):
		for name in frappe.db.sql_list("select name from `tabIsoft Salary Slip` where employee=%s",
		                               self.employee.name):
			frappe.db.sql("delete from `tabIsoft Salary Detail` where parent=%s", name)
			frappe.db.sql("delete from `tabIsoft Payroll Employee` where salary_slip=%s", name)
			frappe.db.sql("delete from `tabIsoft Salary Slip` where name=%s", name)

	def foreign(self):
		frappe.db.set_value("Employee", self.employee.name,
		                    {"custom_foreign_national": 1, "custom_inss_number": None})

	def test_the_employee_form_has_the_field(self):
		df = frappe.get_meta("Employee").get_field("custom_foreign_national")
		self.assertIsNotNone(df)
		self.assertEqual(df.fieldtype, "Check")

	def test_an_angolan_employee_pays_inss_as_before(self):
		slip = self.make_slip(submit=False)
		self.assertGreater(slip.ss_employee_amount, 0)
		self.assertGreater(slip.ss_employer_amount, 0)
		self.assertIn("CTSS3", [d.abbr for d in slip.deductions])

	def test_a_foreign_national_pays_no_inss(self):
		angolan = self.make_slip(submit=False)
		self.tearDown()
		self.foreign()
		slip = self.make_slip(submit=False)
		self.assertEqual((slip.ss_base, slip.ss_employee_amount, slip.ss_employer_amount), (0, 0, 0))
		self.assertNotIn("CTSS3", [d.abbr for d in slip.deductions])
		self.assertEqual(slip.gross_pay, angolan.gross_pay)
		# IRT on the whole taxable income: the 3% is no longer taken off first
		self.assertAlmostEqual(slip.taxable_income, angolan.taxable_income + angolan.ss_employee_amount, places=2)
		self.assertAlmostEqual(slip.net_pay, flt(slip.gross_pay - slip.total_deduction, 2), places=2)
		self.assertAlmostEqual(slip.employer_cost, slip.gross_pay, places=2)

	def test_the_preview_agrees(self):
		self.foreign()
		rows = api.payroll_preview(self.company, str(self.start), str(self.end))
		row = [r for r in rows if r["employee"] == self.employee.name][0]
		self.assertEqual((flt(row["ss"]), flt(row["ss_employer"])), (0.0, 0.0))

	def test_a_final_settlement_takes_no_inss(self):
		from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
		from isoft_angola_hr.isoft_angola_hr.payroll.test_p7_settlement import scenario
		data = scenario(company=self.company)
		with_inss = fs.compute(dict(data))
		without = fs.compute(dict(data, inss_exempt=1))
		inss = lambda res: [l for l in res["lines"] if l["key"] == "inss"][0]["amount"]
		self.assertGreater(inss(with_inss), 0)
		self.assertEqual(inss(without), 0)

	def test_no_missing_inss_number_finding(self):
		from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
		self.foreign()
		report = readiness.evaluate(self.company, self.start, self.end, employees=[self.employee.name])
		codes = [e["code"] for e in report["exceptions"] if e.get("employee") == self.employee.name]
		self.assertNotIn("EXC-009", codes)

	def test_the_inss_declaration_leaves_them_out(self):
		from isoft_angola_hr.isoft_angola_hr.services import statutory_filing as sf
		self.foreign()
		self.make_slip(submit=True)
		rows = sf._slips(self.company, self.start, self.end, sf.INSS)
		self.assertNotIn(self.employee.name, [r.employee for r in rows])
		irt = sf._slips(self.company, self.start, self.end, sf.IRT)
		self.assertIn(self.employee.name, [r.employee for r in irt])
