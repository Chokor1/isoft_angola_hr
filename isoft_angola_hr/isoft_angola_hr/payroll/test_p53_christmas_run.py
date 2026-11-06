# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The Christmas bonus run (run_type "Christmas Bonus") — HR's document of November 2026.

See payroll/christmas.py for the rule. These tests write payroll documents, so they run
only through the protected runner (payroll/testing_guard.py).
"""

import unittest

import frappe
from frappe.utils import getdate

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


# --------------------------------------------------------------------------- #
# The bonus run
# --------------------------------------------------------------------------- #
from isoft_angola_hr.isoft_angola_hr import api  # noqa: E402
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import _account  # noqa: E402
from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture  # noqa: E402
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf  # noqa: E402


class BonusRunFixture(BonusFixture, ControlFixture):
	"""Bonus fixture + one user per role, the SNA account mapped, and a department that
	holds only the fixture's bonus employees (so fill_employees sees nobody else)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa
		cls.acc["SNA"] = _account(cls.company, PREFIX + " Subsidio de Natal", "Expense")
		cls._created.append(("Account", cls.acc["SNA"]))
		s = frappe.get_single("Isoft HR Settings")
		rows = {r.abbr: r for r in s.component_accounts}
		if "SNA" in rows:
			rows["SNA"].account = cls.acc["SNA"]
		else:
			s.append("component_accounts", {"abbr": "SNA", "account": cls.acc["SNA"]})
		with pa.unlogged_mapping():
			s.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Isoft HR Settings")
		last = pa.last_posted_run(cls.company)
		cls._track(frappe.get_doc({
			"doctype": "Isoft Payroll Account Change", "company": cls.company,
			"account_key": pa.MAPPING_CONFIRMED, "label": PREFIX + " bonus mapping confirmation",
			"changed_by": "Administrator", "changed_on": frappe.utils.now(),
			"after_posting": 1 if last else 0, "posted_payroll": last.name if last else None,
			"note": pa.mapping_snapshot(),
		}).insert(ignore_permissions=True))
		cls.dept = cls._department(PREFIX + " Natal")
		frappe.db.set_value("Employee", cls.emp.name, {
			"department": cls.dept, "custom_iban": "AO06000600000100037131174",
			"custom_nif": "5417000001", "custom_inss_number": "0123456780"})

	@classmethod
	def _department(cls, name):
		existing = frappe.db.get_value("Department", {"department_name": name, "company": cls.company})
		if existing:
			return existing
		return cls._track(frappe.get_doc({"doctype": "Department", "department_name": name,
		                                  "company": cls.company}).insert(ignore_permissions=True)).name

	def posted_bonus_run(self):
		"""A bonus run prepared by the officer, approved by the manager, posted by Finance."""
		entry = self.entry()
		frappe.set_user(self.users["officer"])
		entry.fill_employees()
		entry.create_salary_slips()
		api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)
		frappe.set_user(self.users["manager"])
		api.payroll_action(entry.name, wf.APPROVE)
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		api.make_bulk_journal_entry(entry.name)
		frappe.set_user("Administrator")
		entry.reload()
		return entry

	def entry(self, group="A", employees=None):
		doc = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"run_type": christmas.RUN_CHRISTMAS, "posting_date": "{0}-12-15".format(YEAR),
			"start_date": self.bstart, "end_date": self.bend,
			"payroll_group": "{0} bonus {1} {2}".format(PREFIX, type(self).__name__, group)[:140],
			"department": self.dept,
		})
		for e in employees or ():
			doc.append("employees", {"employee": e.name, "employee_name": e.employee_name})
		return doc.insert(ignore_permissions=True)


class TestBonusRun(BonusRunFixture):
	def test_full_lifecycle(self):
		entry = self.entry()
		frappe.set_user(self.users["officer"])
		self.assertEqual(entry.fill_employees(), 1)
		entry.create_salary_slips()
		self.assertEqual(api.payroll_action(entry.name, wf.SUBMIT_FOR_APPROVAL)["status"],
		                 wf.PENDING_APPROVAL)
		frappe.set_user(self.users["manager"])
		self.assertEqual(api.payroll_action(entry.name, wf.APPROVE)["status"], wf.APPROVED)
		frappe.set_user(self.users["finance"])
		entry.reload()
		entry.submit_salary_slips()
		self.assertEqual(api.make_bulk_journal_entry(entry.name)["status"], wf.POSTED)
		frappe.set_user("Administrator")

		entry.reload()
		slip = frappe.get_doc("Isoft Salary Slip", entry.employees[0].salary_slip)
		self.assertEqual((slip.run_type, slip.net_pay), (christmas.RUN_CHRISTMAS, 179500.0))
		self.assertEqual(entry.total_net_pay, 179500.0)
		gl = frappe.get_all("Journal Entry Account", filters={"parent": slip.journal_entry},
		                    fields=["account", "debit_in_account_currency as debit",
		                            "credit_in_account_currency as credit"])
		accounts = {g.account: (g.debit, g.credit) for g in gl}
		self.assertEqual(accounts[self.acc["SNA"]], (200000.0, 0.0))
		self.assertEqual(accounts[self.acc["IRT"]], (0.0, 20500.0))
		for abbr in ("CTSS3", "CTSSE", "CTSSP"):
			self.assertNotIn(self.acc[abbr], accounts, abbr)

	def test_second_bonus_run_same_year_refused(self):
		self.entry()
		with self.assertRaises(frappe.ValidationError):
			self.entry()

	def test_salary_run_for_december_still_allowed(self):
		bonus = self.entry()
		salary = frappe.get_doc({
			"doctype": "Isoft Payroll Entry", "company": self.company,
			"posting_date": self.bend, "start_date": self.bstart, "end_date": self.bend,
			"payroll_group": bonus.payroll_group,
		}).insert(ignore_permissions=True)
		self.assertEqual(salary.run_type, christmas.RUN_SALARY)
		slip = self.salary_slip(payroll_entry=salary.name)
		self.assertNotIn("SNA", [e.abbr for e in slip.earnings])
		self.assertTrue(slip.ss_employee_amount)
		frappe.delete_doc("Isoft Payroll Entry", salary.name, force=True, ignore_permissions=True)

	def test_december_second_hire_is_excluded_first_is_included(self):
		first = self.new_employee("Dez Um", "{0}-12-01".format(YEAR), 400000)
		second = self.new_employee("Dez Dois", "{0}-12-02".format(YEAR), 400000)
		suspended = self.new_employee("Suspenso", "2021-01-01", 400000, status="Suspended")
		for e in (first, second, suspended):
			frappe.db.set_value("Employee", e.name, "department", self.dept)
			self.addCleanup(frappe.db.set_value, "Employee", e.name, "department", None)
		entry = self.entry()
		entry.fill_employees()
		listed = {r.employee for r in entry.employees}
		self.assertIn(first.name, listed)
		self.assertIn(suspended.name, listed)
		self.assertNotIn(second.name, listed)
		entry.create_salary_slips()
		entry.reload()
		months = {r.employee: frappe.db.get_value("Isoft Salary Slip", r.salary_slip, "natal_months")
		          for r in entry.employees}
		self.assertEqual(months[first.name], 1)

	def test_cancel_and_amend_one_bonus_slip(self):
		slip = self.bonus_slip(submit=True)
		self.cancel_slip(slip)
		amended = self.bonus_slip(run=slip.payroll_entry, submit=True, amended_from=slip.name)
		self.assertEqual(amended.docstatus, 1)
		self.assertEqual(amended.net_pay, 179500.0)


class TestConsole(BonusRunFixture):
	def preview(self, inputs=None):
		rows = api.payroll_preview(self.company, None, None, department=self.dept,
		                           run_type=christmas.RUN_CHRISTMAS, year=YEAR,
		                           inputs=frappe.as_json(inputs or {}))
		return {r["employee"]: r for r in rows}

	def test_preview_shows_the_bonus(self):
		row = self.preview()[self.emp.name]
		self.assertEqual((row["base"], row["months"], row["christmas"], row["irt"], row["net"]),
		                 (400000.0, 12, 200000.0, 20500.0, 179500.0))
		self.assertFalse(row["suspended"])

	def test_preview_applies_an_override(self):
		row = self.preview({self.emp.name: {"christmas": 150000, "reason": "Suspended 2 months"}})[self.emp.name]
		self.assertEqual(row["christmas"], 150000.0)
		self.assertEqual(row["net"], 150000.0 - row["irt"])

	def create(self, christmas_amount=None, reason=None):
		row = dict(self.preview()[self.emp.name])
		if christmas_amount is not None:
			row["christmas"] = christmas_amount
			row["override_reason"] = reason
		res = api.create_payroll_from_preview(
			self.company, None, None, frappe.as_json([row]), posting_date="{0}-12-15".format(YEAR),
			run_type=christmas.RUN_CHRISTMAS, year=YEAR)
		self.addCleanup(frappe.db.sql, "delete from `tabIsoft Payroll Employee` where parent=%s", res["name"])
		self.addCleanup(frappe.db.sql, "delete from `tabIsoft Payroll Entry` where name=%s", res["name"])
		return res

	def test_create_makes_a_bonus_run(self):
		res = self.create()
		entry = frappe.get_doc("Isoft Payroll Entry", res["name"])
		self.assertEqual(entry.run_type, christmas.RUN_CHRISTMAS)
		self.assertEqual((getdate(entry.start_date), getdate(entry.end_date)),
		                 tuple(getdate(d) for d in christmas.bonus_period(YEAR)))
		self.assertEqual(entry.total_net_pay, 179500.0)

	def test_override_needs_a_reason(self):
		with self.assertRaises(frappe.ValidationError):
			self.create(150000, reason="  ")

	def test_override_cannot_exceed_a_full_year(self):
		with self.assertRaises(frappe.ValidationError):
			self.create(200000.01, reason="typo")

	def test_override_is_kept_and_shown_to_the_approver(self):
		res = self.create(150000, reason="Suspended two months")
		entry = frappe.get_doc("Isoft Payroll Entry", res["name"])
		slip = frappe.get_doc("Isoft Salary Slip", entry.employees[0].salary_slip)
		self.assertEqual((slip.subsidio_natal, slip.natal_override_reason), (150000.0, "Suspended two months"))
		overrides = entry.approval_summary()["christmas_overrides"]
		self.assertEqual([(o["employee"], o["amount"]) for o in overrides], [(self.emp.name, 150000.0)])

	def test_salary_preview_no_longer_defaults_the_bonus(self):
		start, end = self.bstart, self.bend
		rows = api.payroll_preview(self.company, start, end, department=self.dept)
		row = [r for r in rows if r["employee"] == self.emp.name][0]
		self.assertEqual((row["christmas"], row["natal_default"]), (0.0, 0.0))


class TestPostingAndBank(BonusRunFixture):
	def test_the_bonus_reference_is_natal_and_the_year(self):
		self.assertEqual(api._salary_reference(self.bend, christmas.RUN_CHRISTMAS), "Natal_{0}".format(YEAR))
		self.assertEqual(api._salary_reference(self.bend), "Sal_Dezembro_{0}".format(YEAR))

	def test_the_bank_file_of_a_bonus_run_uses_it(self):
		from isoft_angola_hr.isoft_angola_hr.services import bank_export
		entry = self.posted_bonus_run()
		frappe.set_user(self.users["finance"])
		api.payroll_action(entry.name, wf.RELEASE_FOR_PAYMENT)
		res = bank_export.generate(entry.name)
		frappe.set_user("Administrator")
		self.assertIn("Natal_{0}".format(YEAR), frappe.as_json(res))
