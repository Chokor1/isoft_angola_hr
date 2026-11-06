# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The Christmas bonus run (run_type "Christmas Bonus") — HR's document of November 2026.

See payroll/christmas.py for the rule. These tests write payroll documents, so they run
only through the protected runner (payroll/testing_guard.py).
"""

import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr import install
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestDataModel(unittest.TestCase):
	def test_existing_runs_read_as_salary(self):
		install.backfill_run_type()
		for dt in ("Isoft Payroll Entry", "Isoft Salary Slip"):
			self.assertEqual(frappe.get_meta(dt).get_field("run_type").options.split("\n"),
			                 ["Salary", "Christmas Bonus"], dt)
			self.assertEqual(frappe.db.count(dt, {"run_type": ("in", ("", None))}), 0, dt)
			self.assertEqual(frappe.db.sql("select count(*) from `tab{0}` where ifnull(run_type,'')=''"
			                               .format(dt))[0][0], 0, dt)


# --------------------------------------------------------------------------- #
# Bonus slips
# --------------------------------------------------------------------------- #
from isoft_angola_hr.isoft_angola_hr.payroll import christmas  # noqa: E402
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (  # noqa: E402
	PREFIX, PayrollFixture)

YEAR = 2026


class BonusFixture(PayrollFixture):
	"""An employee on 400 000,00 joined in a previous year, the Christmas bonus switched on,
	and the bonus period of 2026 (the cycle period that ends in December)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.set_value("Isoft HR Settings", None, "enable_natal", 1)
		frappe.db.set_value("Isoft HR Settings", None, "natal_rate", 50)
		frappe.clear_cache(doctype="Isoft HR Settings")
		cls.bstart, cls.bend = christmas.bonus_period(YEAR)
		cls.emp = cls.new_employee("Natal", "2020-03-10", 400000)

	def tearDown(self):
		"""Each test starts with no slips for the fixture's employees: the class shares one
		transaction, and a bonus slip left by one test is a duplicate for the next."""
		emps = [n for dt, n in self._created if dt == "Employee"]
		slips = frappe.db.sql_list("select name from `tabIsoft Salary Slip` where employee in ({0})"
		                           .format(", ".join(["%s"] * len(emps))), emps)
		if slips:
			marks = ", ".join(["%s"] * len(slips))
			frappe.db.sql("delete from `tabIsoft Salary Detail` where parent in ({0})".format(marks), slips)
			frappe.db.sql("delete from `tabIsoft Payroll Employee` where salary_slip in ({0})".format(marks), slips)
			frappe.db.sql("delete from `tabIsoft Salary Slip` where name in ({0})".format(marks), slips)
		frappe.db.sql("""delete from `tabIsoft Payroll Entry` where company=%s and payroll_group like %s""",
		              (self.company, "{0} bonus {1}%".format(PREFIX, type(self).__name__)))

	@classmethod
	def new_employee(cls, name, joined, base, status="Active"):
		emp = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": name,
			"company": cls.company, "date_of_joining": joined, "status": status,
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True))
		cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": emp.name, "company": cls.company,
			"from_date": joined, "base": base, "food_allowance": 30000, "transport_allowance": 30000,
		}).insert(ignore_permissions=True))
		return emp

	@classmethod
	def bonus_run(cls, group="A"):
		return (frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": cls.company,
			"run_type": christmas.RUN_CHRISTMAS, "posting_date": "{0}-12-15".format(YEAR),
			"start_date": cls.bstart, "end_date": cls.bend,
			"payroll_group": "{0} bonus {1} {2}".format(PREFIX, cls.__name__, group)[:140],
		}).insert(ignore_permissions=True)).name  # removed by tearDown

	def bonus_slip(self, emp=None, run=None, submit=False, **kw):
		values = {"employee": (emp or self.emp).name, "run_type": christmas.RUN_CHRISTMAS,
		          "posting_date": "{0}-12-15".format(YEAR),
		          "start_date": self.bstart, "end_date": self.bend,
		          "payroll_entry": run or self.bonus_run()}
		values.update(kw)
		return self.insert_slip(values, submit=submit)

	def salary_slip(self, emp=None, submit=False, **kw):
		values = {"employee": (emp or self.emp).name, "posting_date": self.bend,
		          "start_date": self.bstart, "end_date": self.bend}
		values.update(kw)
		return self.insert_slip(values, submit=submit)


class TestBonusSlip(BonusFixture):
	def test_bonus_slip_matches_the_document(self):
		slip = self.bonus_slip()
		self.assertEqual(slip.name, "ISN-{0}-{1}".format(self.emp.name, YEAR))
		self.assertEqual(slip.run_type, christmas.RUN_CHRISTMAS)
		self.assertEqual((slip.subsidio_natal, slip.irt_amount, slip.net_pay),
		                 (200000.0, 20500.0, 179500.0))
		self.assertEqual((slip.ss_employee_amount, slip.ss_employer_amount), (0, 0))
		self.assertEqual((slip.natal_months, slip.natal_base), (12, 400000.0))
		self.assertEqual([e.abbr for e in slip.earnings], ["SNA"])
		self.assertEqual([d.abbr for d in slip.deductions], ["IRT"])

	def test_bonus_and_december_salary_coexist(self):
		salary = self.salary_slip(submit=True)
		bonus = self.bonus_slip(submit=True)
		self.assertEqual((salary.docstatus, bonus.docstatus), (1, 1))
		self.assertEqual(salary.run_type, christmas.RUN_SALARY)

	def test_two_bonus_slips_in_a_year_are_refused(self):
		self.bonus_slip(submit=True)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.bonus_slip(run=self.bonus_run("B"))
		self.assertIn("Christmas", str(ctx.exception))

	def test_salary_slip_no_longer_adds_the_bonus(self):
		slip = self.salary_slip()
		self.assertFalse(frappe.utils.flt(slip.subsidio_natal))
		self.assertNotIn("SNA", [e.abbr for e in slip.earnings])

	def test_salary_slip_refuses_sna_after_a_bonus(self):
		bonus = self.bonus_slip(submit=True)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.salary_slip(subsidio_natal=1000)
		self.assertIn(bonus.name, str(ctx.exception))
