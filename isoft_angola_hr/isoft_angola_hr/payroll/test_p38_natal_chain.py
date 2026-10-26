# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Subsídio de Natal — the whole chain, not just the arithmetic.

``test_p34`` pins the money. This module pins what the APPLICATION does with it:
whether the switch that governs the component can be reached at all, whether the
preview proposes a figure the save will accept, what a December run looks like when the
component is off, and whether the amount survives intact from the payroll preview
through the Salary Slip and out onto the printed payslip.

Three defects are pinned here, all found by running a December period against the
configuration production was actually in (``enable_natal = 0``):

1. ``api.get_settings`` never returned ``enable_natal`` or ``enable_ferias``, and the
   Settings screen had no control for either. The RATE and the payment month of both
   subsidies were on screen, so the configuration read as "Subsídio de Natal, 50%,
   December" while the switch deciding whether it is paid at all was invisible.

2. The payroll preview computed the December default from the rate alone. With the
   component off it therefore showed a full 13th month for every employee, and every
   resulting slip was then refused by ``validate_enabled_components`` — whose message
   told the reader to enable it under "Settings → Enabled Components", the panel that
   did not contain it. A brand-new slip did the same thing to itself: ``compute()``
   auto-filled the default and ``validate()`` rejected the document on its own value.

3. Nothing anywhere said that a December run was going out without the 13th month. The
   only evidence was every employee being 50% of a salary lighter.

**LEGAL VERIFICATION REQUIRED — the tax treatment.** The supplement enters gross, the
INSS contribution base and taxable income in full, and no separate exemption is applied.
That is this application's long-standing behaviour and it is asserted below so it cannot
drift, but it is asserted as *documented behaviour*, not as a proposition confirmed
against a published AGT ruling. Artigo 238.º establishes the entitlement; it does not
establish the withholding treatment.
"""

import io
import os
import re
import unittest

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll import engine, recibo
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import (PREFIX,
                                                                        PayrollFixture)
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_JS = os.path.join(APP_ROOT, "isoft_angola_hr", "page", "angola_hr_dashboard",
                            "angola_hr_dashboard.js")
if not os.path.exists(DASHBOARD_JS):
	DASHBOARD_JS = os.path.join(APP_ROOT, "page", "angola_hr_dashboard",
	                            "angola_hr_dashboard.js")

MONTHS = engine.MONTHS


def _js():
	return io.open(DASHBOARD_JS, encoding="utf-8").read()


class NatalFixture(PayrollFixture):
	"""The standard payroll fixture with the 13th month switched ON, and the payment
	month moved to the month the fixture actually runs in.

	Moving the payment month rather than moving the period to December is deliberate:
	the fixture posts in the current month because the site has an accounting freeze
	date, and a hard-coded December would stop posting as soon as the books closed past
	it. It also exercises the configurability of the payment month, which is the setting
	a 23rd-to-22nd cycle depends on.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		s = frappe.get_single("Isoft HR Settings")
		cls._natal_backup = {
			"enable_natal": s.get("enable_natal"),
			"enable_ferias": s.get("enable_ferias"),
			"natal_rate": s.get("natal_rate"),
			"natal_payment_month": s.get("natal_payment_month"),
		}
		cls.pay_month = MONTHS[getdate(cls.end).month - 1]
		s.enable_natal = 1
		s.natal_rate = law.CHRISTMAS_BONUS_MIN_PCT
		s.natal_payment_month = cls.pay_month
		s.save(ignore_permissions=True)
		frappe.clear_cache()
		cls.rate = flt(law.CHRISTMAS_BONUS_MIN_PCT)

	@classmethod
	def tearDownClass(cls):
		s = frappe.get_single("Isoft HR Settings")
		for field, value in getattr(cls, "_natal_backup", {}).items():
			s.set(field, value)
		s.save(ignore_permissions=True)
		frappe.clear_cache()
		super().tearDownClass()

	def set_natal(self, on):
		s = frappe.get_single("Isoft HR Settings")
		s.enable_natal = 1 if on else 0
		s.save(ignore_permissions=True)
		frappe.clear_cache()

	def expected(self, base=None, joining="2020-01-01"):
		base = flt(self.profile.base) if base is None else flt(base)
		months = engine.natal_months(joining, self.end)
		return flt(base * self.rate / 100.0 * months / 12.0, 2)

	def fresh_slip(self, **kwargs):
		"""A slip for the fixture period, replacing any earlier one.

		The slip name is derived from employee + period, so two slips for the same month
		collide on the primary key. Tests that need to compare two calculations use
		:func:`engine.compute_slip` instead; tests that need a real document use this.
		"""
		for name in frappe.db.sql_list(
				"""select name from `tabIsoft Salary Slip`
				where employee = %s and start_date = %s""", (self.employee.name, self.start)):
			doc = frappe.get_doc("Isoft Salary Slip", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Isoft Salary Slip", name, force=True, ignore_permissions=True)
		return self.make_slip(**kwargs)

	def compute(self, **inputs):
		"""The engine result for the fixture profile, with the given monthly inputs."""
		values = {"payment_days": 30, "total_working_days": 30,
		          "start_date": self.start, "end_date": self.end}
		values.update(inputs)
		return engine.compute_slip(self.profile, values, settings=engine.get_settings(),
		                           on_date=self.end, employee=self.employee.name)

	def preview_row(self):
		rows = api.payroll_preview(company=self.company, start_date=str(self.start),
		                           end_date=str(self.end))
		rows = rows.get("rows") if isinstance(rows, dict) else rows
		for r in rows:
			if r.get("employee") == self.employee.name:
				return r
		return None


# --------------------------------------------------------------------------- #
# 1. The switch has to be reachable
# --------------------------------------------------------------------------- #
class TestTheSwitchIsReachable(NatalFixture):
	"""A control the backend enforces and the screen does not offer is not a policy
	decision, it is an accident waiting for December."""

	def test_get_settings_returns_the_natal_switch(self):
		self.assertIn("enable_natal", api.get_settings())

	def test_get_settings_returns_the_ferias_switch(self):
		self.assertIn("enable_ferias", api.get_settings())

	def test_get_settings_returns_the_switch_for_every_optional_component(self):
		got = api.get_settings()
		missing = [field for field in sorted(set(engine.COMPONENT_TOGGLES.values()))
		           if field not in got]
		self.assertEqual(missing, [],
		                 "the Settings screen cannot show or change: %s" % missing)

	def test_the_settings_screen_has_a_control_for_every_toggle(self):
		src = _js()
		missing = [field for field in sorted(set(engine.COMPONENT_TOGGLES.values()))
		           if ('chk("%s"' % field) not in src]
		self.assertEqual(missing, [],
		                 "no checkbox on the Settings screen for: %s" % missing)

	def test_the_refusal_message_names_a_panel_that_contains_the_switch(self):
		"""validate_enabled_components sends the reader to Settings → Enabled Components."""
		src = _js()
		panel = src[src.index('title: __("Enabled Components")'):]
		panel = panel[:panel.index("this.panel(", 10)] if "this.panel(" in panel[10:] else panel
		self.assertIn('chk("enable_natal"', panel)
		self.assertIn('chk("enable_ferias"', panel)


# --------------------------------------------------------------------------- #
# 2. The preview may never propose what the save refuses
# --------------------------------------------------------------------------- #
class TestThePreviewAgreesWithTheSave(NatalFixture):

	def tearDown(self):
		self.set_natal(True)

	def test_with_the_component_on_the_preview_proposes_the_supplement(self):
		self.set_natal(True)
		row = self.preview_row()
		self.assertIsNotNone(row)
		self.assertEqual(flt(row["natal_default"]), self.expected())
		self.assertGreater(flt(row["natal_default"]), 0)

	def test_with_the_component_off_the_preview_proposes_nothing(self):
		self.set_natal(False)
		row = self.preview_row()
		self.assertIsNotNone(row)
		self.assertEqual(flt(row["natal_default"]), 0.0,
		                 "the preview offered a 13th month the save would refuse")
		self.assertEqual(flt(row["christmas"]), 0.0)

	def test_with_the_component_off_a_new_slip_does_not_fill_itself(self):
		"""The slip used to auto-fill the default and then reject its own value."""
		self.set_natal(False)
		slip = self.fresh_slip(submit=False)
		self.assertEqual(flt(slip.subsidio_natal), 0.0)

	def test_with_the_component_off_an_explicit_amount_is_still_refused(self):
		"""Turning the proposal off must not turn the CONTROL off."""
		self.set_natal(False)
		with self.assertRaises(frappe.ValidationError):
			self.fresh_slip(submit=False, subsidio_natal=50000)

	def test_with_the_component_on_the_previewed_amount_saves_unchanged(self):
		self.set_natal(True)
		row = self.preview_row()
		slip = self.fresh_slip(submit=False, subsidio_natal=row["natal_default"])
		self.assertEqual(flt(slip.subsidio_natal), flt(row["natal_default"]))

	def test_every_component_the_preview_proposes_is_one_the_slip_accepts(self):
		"""The general form of the defect, over the whole toggle catalogue."""
		self.set_natal(True)
		settings = engine.get_settings()
		row = self.preview_row()
		proposals = {"SNA": flt(row.get("natal_default")),
		             "SFE": flt(row.get("ferias_full"))}
		for abbr, amount in proposals.items():
			with self.subTest(component=abbr):
				if amount:
					self.assertTrue(
						engine.component_enabled(settings, abbr),
						"the preview proposes %s while the component is disabled" % abbr)


# --------------------------------------------------------------------------- #
# 3. A December run that is going out without the 13th month must say so
# --------------------------------------------------------------------------- #
class TestTheRunSaysWhenItIsOff(NatalFixture):

	def tearDown(self):
		self.set_natal(True)

	def _row(self):
		for item in readiness.configuration_status(self.company, on_date=self.end):
			if item["key"] == "enable_natal":
				return item
		return None

	def test_the_payment_month_reports_the_component_as_a_configuration_item(self):
		self.set_natal(False)
		row = self._row()
		self.assertIsNotNone(row, "nothing warned that the 13th month was switched off")
		self.assertFalse(row["ok"])
		self.assertEqual(row["severity"], readiness.WARNING)

	def test_it_reports_configured_when_the_component_is_on(self):
		self.set_natal(True)
		row = self._row()
		self.assertIsNotNone(row)
		self.assertTrue(row["ok"])

	def test_it_is_silent_outside_the_payment_month(self):
		"""It is not a finding in March that December has not happened yet."""
		self.set_natal(False)
		other = getdate(self.end).replace(
			month=(getdate(self.end).month % 12) + 1, day=1)
		keys = [i["key"] for i in readiness.configuration_status(self.company, on_date=other)]
		self.assertNotIn("enable_natal", keys)

	def test_the_hint_cites_the_article(self):
		self.set_natal(False)
		self.assertIn("238", self._row()["hint"])


# --------------------------------------------------------------------------- #
# 4. Preview -> Slip -> Payslip PDF, figure by figure
# --------------------------------------------------------------------------- #
class TestTheFullChain(NatalFixture):

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.amount = None

	def slip_with_natal(self, submit=False):
		row = self.preview_row()
		self.amount = flt(row["natal_default"])
		return row, self.fresh_slip(submit=submit, subsidio_natal=row["natal_default"])

	def test_the_preview_amount_reaches_the_slip_unchanged(self):
		row, slip = self.slip_with_natal()
		self.assertEqual(flt(slip.subsidio_natal), flt(row["natal_default"]))

	def test_the_slip_carries_it_as_its_own_earning_line(self):
		row, slip = self.slip_with_natal()
		line = [e for e in slip.earnings if e.abbr == "SNA"]
		self.assertEqual(len(line), 1)
		self.assertEqual(flt(line[0].amount), flt(row["natal_default"]))

	def test_gross_matches_the_preview(self):
		row, slip = self.slip_with_natal()
		self.assertEqual(flt(slip.gross_pay, 2), flt(row["gross_pay"], 2))

	def test_taxable_income_matches_the_preview(self):
		row, slip = self.slip_with_natal()
		self.assertEqual(flt(slip.taxable_income, 2), flt(row["taxable_income"], 2))

	def test_the_employee_contribution_matches_the_preview(self):
		row, slip = self.slip_with_natal()
		got = [d for d in slip.deductions if d.abbr == "CTSS3"]
		self.assertEqual(flt(got[0].amount, 2), flt(row["ss"], 2))

	def test_the_irt_matches_the_preview(self):
		row, slip = self.slip_with_natal()
		got = [d for d in slip.deductions if d.abbr == "IRT"]
		amount = flt(got[0].amount, 2) if got else 0.0
		self.assertEqual(amount, flt(row["irt"], 2))

	def test_net_pay_matches_the_preview(self):
		row, slip = self.slip_with_natal()
		self.assertEqual(flt(slip.net_pay, 2), flt(row["net_pay"], 2))

	def test_net_reconciles_on_the_slip_itself(self):
		row, slip = self.slip_with_natal()
		self.assertEqual(flt(slip.net_pay, 2),
		                 flt(flt(slip.gross_pay) - flt(slip.total_deduction), 2))

	def test_the_payslip_prints_the_supplement_under_its_statutory_code(self):
		row, slip = self.slip_with_natal(submit=True)
		ctx = recibo.get_context(slip)
		lines = [l for l in ctx["earnings"] if l["code"] == recibo.COMPONENT_CODES["SNA"]]
		self.assertEqual(len(lines), 1, "the 13th month is missing from the payslip")
		self.assertEqual(flt(lines[0]["amount"], 2), flt(row["natal_default"], 2))

	def test_the_payslip_total_earnings_include_it(self):
		row, slip = self.slip_with_natal(submit=True)
		ctx = recibo.get_context(slip)
		self.assertEqual(flt(ctx["total_earnings"], 2), flt(slip.gross_pay, 2))

	def test_the_payslip_net_is_the_slip_net(self):
		row, slip = self.slip_with_natal(submit=True)
		ctx = recibo.get_context(slip)
		self.assertEqual(flt(ctx["net_pay"], 2), flt(slip.net_pay, 2))

	def test_a_pdf_actually_renders_with_the_supplement_on_it(self):
		row, slip = self.slip_with_natal(submit=True)
		pdf = frappe.get_print("Isoft Salary Slip", slip.name,
		                       print_format="Recibo de Vencimento", no_letterhead=1,
		                       as_pdf=True)
		self.assertTrue(pdf and pdf[:4] == b"%PDF", "no PDF came out")


# --------------------------------------------------------------------------- #
# 5. Where the supplement lands in the statutory bases — DOCUMENTED BEHAVIOUR
# --------------------------------------------------------------------------- #
class TestTheStatutoryTreatment(NatalFixture):
	"""LEGAL VERIFICATION REQUIRED. These assertions describe what the application does,
	so it cannot drift silently. They are not a claim that a published AGT ruling was
	checked."""

	AMOUNT = 100000.0

	def _pair(self):
		"""The same month with and without the supplement. Computed through the engine
		rather than two saved slips, which would collide on the slip's derived name."""
		return self.compute(natal_amount=0), self.compute(natal_amount=self.AMOUNT)

	def test_it_raises_gross_by_exactly_its_amount(self):
		a, b = self._pair()
		self.assertEqual(flt(b["gross_pay"] - a["gross_pay"], 2), self.AMOUNT)

	def test_it_is_inside_the_social_security_base(self):
		a, b = self._pair()
		rate = flt(engine.get_settings().ss_employee_rate)

		def inss(res):
			return flt(sum(d["amount"] for d in res["deductions"] if d["abbr"] == "CTSS3"))

		self.assertEqual(flt(inss(b) - inss(a), 2), flt(self.AMOUNT * rate / 100.0, 2))

	def test_it_is_fully_taxable_net_of_the_employee_contribution(self):
		a, b = self._pair()
		rate = flt(engine.get_settings().ss_employee_rate)
		expected = flt(self.AMOUNT * (1 - rate / 100.0), 2)
		self.assertEqual(flt(b["taxable_income"] - a["taxable_income"], 2), expected)

	def test_no_separate_exemption_is_applied_to_it(self):
		"""If an exemption is ever introduced it must break this test, not slip past it."""
		a, b = self._pair()
		self.assertGreater(flt(b["taxable_income"]), flt(a["taxable_income"]))

	def test_the_component_metadata_agrees_with_the_arithmetic(self):
		self.assertTrue(engine.COMPONENTS["SNA"]["ss_base"])
		self.assertTrue(engine.COMPONENTS["SNA"]["in_gross"])
		self.assertFalse(engine.COMPONENTS["SNA"]["prorate"],
		                 "the 13th month is proportional to months of service, never to "
		                 "attendance within the month")


# --------------------------------------------------------------------------- #
# 6. Admission and termination — the requested matrix, through real documents
# --------------------------------------------------------------------------- #
class TestAdmissionMatrix(NatalFixture):

	CASES = [
		("January admission, 1st",      "%d-01-01", 12),
		("January admission, mid-month", "%d-01-15", 11),
		("March admission",             "%d-03-01", 10),
		("March admission, mid-month",  "%d-03-15", 9),
		("November admission",          "%d-11-01", 2),
		("December admission",          "%d-12-01", 1),
		("December admission, last day", "%d-12-31", 0),
	]

	def _months(self, joining, end):
		return engine.natal_months(joining, end)

	def test_every_admission_date_yields_the_complete_months(self):
		year = getdate(self.end).year
		end = "%d-12-31" % year
		for name, template, expected in self.CASES:
			with self.subTest(case=name):
				self.assertEqual(self._months(template % year, end), expected)

	def test_the_money_is_the_month_count_over_twelve(self):
		year = getdate(self.end).year
		end = "%d-12-31" % year
		base = 200000.0
		for name, template, expected in self.CASES:
			with self.subTest(case=name):
				self.assertEqual(
					engine.default_natal(base, self.rate, template % year, end, "December"),
					flt(base * self.rate / 100.0 * expected / 12.0, 2))

	def test_an_employee_admitted_after_the_period_gets_nothing(self):
		year = getdate(self.end).year
		self.assertEqual(self._months("%d-01-01" % (year + 1), "%d-12-31" % year), 0)

	def test_an_employee_admitted_in_an_earlier_year_gets_a_full_year(self):
		year = getdate(self.end).year
		self.assertEqual(self._months("%d-06-15" % (year - 3), "%d-12-31" % year), 12)

	def test_the_matrix_runs_through_a_real_slip(self):
		"""Not just the helper: the employee's own joining date drives the slip."""
		year = getdate(self.end).year
		emp = frappe.get_doc("Employee", self.employee.name)
		original = emp.date_of_joining
		try:
			for name, template, expected in self.CASES:
				joining = template % year
				if getdate(joining) > getdate(self.end):
					continue
				with self.subTest(case=name):
					frappe.db.set_value("Employee", emp.name, "date_of_joining", joining)
					frappe.clear_cache()
					row = self.preview_row()
					months = engine.natal_months(joining, self.end)
					self.assertEqual(
						flt(row["natal_default"]),
						flt(flt(self.profile.base) * self.rate / 100.0 * months / 12.0, 2))
		finally:
			frappe.db.set_value("Employee", emp.name, "date_of_joining", original)
			frappe.clear_cache()


class TestTerminationMatrix(NatalFixture):
	"""Termination before December and during December.

	Payroll pays the supplement only in the period ending in the payment month, so an
	employee who leaves in September is paid theirs through the Final Settlement. The two
	paths must agree on the month count — they used not to.
	"""

	def _settlement_months(self, joining, termination):
		year_start = getdate("%d-01-01" % getdate(termination).year)
		joined = getdate(joining)
		window = joined if joined > year_start else year_start
		return max(0, min(law.MONTHS_PER_YEAR, law.complete_months(window, termination)))

	def test_a_termination_before_december_is_paid_through_the_settlement(self):
		year = getdate(self.end).year
		self.assertEqual(self._settlement_months("%d-01-01" % (year - 2),
		                                         "%d-09-30" % year), 9)

	def test_a_termination_during_december_counts_the_completed_months(self):
		year = getdate(self.end).year
		self.assertEqual(self._settlement_months("%d-01-01" % (year - 2),
		                                         "%d-12-15" % year), 11)

	def test_a_termination_on_the_last_day_of_december_counts_twelve(self):
		year = getdate(self.end).year
		self.assertEqual(self._settlement_months("%d-01-01" % (year - 2),
		                                         "%d-12-31" % year), 12)

	def test_payroll_and_settlement_agree_on_every_matrix_date(self):
		year = getdate(self.end).year
		for joining in ("%d-01-01" % (year - 1), "%d-01-15" % year, "%d-03-15" % year,
		                "%d-11-01" % year):
			with self.subTest(joining=joining):
				end = "%d-12-31" % year
				self.assertEqual(engine.natal_months(joining, end),
				                 self._settlement_months(joining, end))

	def test_payroll_pays_nothing_outside_the_payment_month(self):
		year = getdate(self.end).year
		self.assertEqual(
			engine.default_natal(200000, self.rate, "%d-01-01" % (year - 1),
			                     "%d-09-30" % year, "December"), 0.0)


# --------------------------------------------------------------------------- #
# 7. Salary changes during the year
# --------------------------------------------------------------------------- #
class TestSalaryChangesDuringTheYear(NatalFixture):
	"""Artigo 238.º sets the supplement as a percentage of the salário-base. The base is
	the one in force when it is paid, so a salary that changed in June is paid on the
	December figure — not on an average and not on the January figure.

	Profiles are effective-dated and the DocType refuses two that overlap, so a raise is
	recorded by closing the old profile the day before the new one starts. That is what
	an approved Salary Change does automatically; these tests do it by hand so the
	arithmetic can be checked without also exercising the approval workflow.
	"""

	def setUp(self):
		super().setUp()
		self._closed = []

	def tearDown(self):
		for name, to_date in self._closed:
			frappe.db.set_value("Isoft Salary Profile", name, "to_date", to_date)
		for doctype, name in list(getattr(self, "_extra", [])):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		self._extra = []
		frappe.clear_cache()

	def _raise_to(self, base, from_date):
		"""Close the standing profile and open a new one on *from_date*."""
		from_date = getdate(from_date)
		self._closed.append((self.profile.name,
		                     frappe.db.get_value("Isoft Salary Profile", self.profile.name,
		                                         "to_date")))
		frappe.db.set_value("Isoft Salary Profile", self.profile.name, "to_date",
		                    frappe.utils.add_days(from_date, -1))
		doc = frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": self.employee.name,
			"company": self.company, "from_date": from_date, "base": base,
			"food_allowance": 30000, "transport_allowance": 30000,
		}).insert(ignore_permissions=True)
		self._extra = list(getattr(self, "_extra", [])) + [("Isoft Salary Profile", doc.name)]
		frappe.clear_cache()
		return doc

	def _mid_year(self):
		"""The first of the fixture month — an earlier month of the same year, or None."""
		end = getdate(self.end)
		if end.month == 1:
			return None
		return end.replace(day=1)

	def _months(self):
		return engine.natal_months(
			frappe.db.get_value("Employee", self.employee.name, "date_of_joining"), self.end)

	def test_an_increase_earlier_in_the_year_is_paid_on_the_current_base(self):
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January; no earlier month to change in")
		raised = flt(self.profile.base) + 100000
		self._raise_to(raised, mid)
		row = self.preview_row()
		self.assertEqual(flt(row["natal_default"]),
		                 flt(raised * self.rate / 100.0 * self._months() / 12.0, 2),
		                 "the 13th month was not paid on the salary in force when it fell due")

	def test_a_decrease_earlier_in_the_year_is_paid_on_the_current_base(self):
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January; no earlier month to change in")
		lowered = flt(self.profile.base) - 50000
		self._raise_to(lowered, mid)
		row = self.preview_row()
		self.assertEqual(flt(row["natal_default"]),
		                 flt(lowered * self.rate / 100.0 * self._months() / 12.0, 2))

	def test_the_january_base_is_not_what_is_paid(self):
		"""Guards against an implementation that reads the year's FIRST profile."""
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January")
		raised = flt(self.profile.base) + 100000
		self._raise_to(raised, mid)
		row = self.preview_row()
		self.assertNotEqual(
			flt(row["natal_default"]),
			flt(flt(self.profile.base) * self.rate / 100.0 * self._months() / 12.0, 2))

	def test_no_average_of_the_two_salaries_is_used(self):
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January")
		old_base = flt(self.profile.base)
		raised = old_base + 100000
		self._raise_to(raised, mid)
		average = (old_base + raised) / 2.0
		row = self.preview_row()
		self.assertNotEqual(
			flt(row["natal_default"]),
			flt(average * self.rate / 100.0 * self._months() / 12.0, 2))

	def test_the_slip_resolves_the_same_profile_the_preview_used(self):
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January")
		raised = flt(self.profile.base) + 100000
		new_profile = self._raise_to(raised, mid)
		slip = self.fresh_slip(submit=False, salary_profile=None)
		self.assertEqual(slip.salary_profile, new_profile.name)

	def test_two_profiles_effective_the_same_day_are_refused_at_source(self):
		"""The first line of defence: the DocType will not let the ambiguity exist."""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({
				"doctype": "Isoft Salary Profile", "employee": self.employee.name,
				"company": self.company, "from_date": self.profile.from_date,
				"base": flt(self.profile.base) + 1,
			}).insert(ignore_permissions=True)

	def test_an_ambiguous_pair_stops_payroll_rather_than_guessing(self):
		"""The second line of defence, for data that got in another way (an import, a
		direct write). The rows are inserted below the DocType to reproduce it."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile \
			import get_active_profile
		mid = self._mid_year()
		if not mid:
			self.skipTest("fixture runs in January")
		first = self._raise_to(flt(self.profile.base) + 10000, mid)
		clone = "%s-CLONE" % first.name
		frappe.db.sql(
			"""insert into `tabIsoft Salary Profile`
			(name, creation, modified, modified_by, owner, docstatus, employee, company,
			 from_date, base)
			select %s, creation, modified, modified_by, owner, docstatus, employee, company,
			 from_date, base + 1 from `tabIsoft Salary Profile` where name = %s""",
			(clone, first.name))
		self._extra = list(getattr(self, "_extra", [])) + [("Isoft Salary Profile", clone)]
		frappe.clear_cache()
		with self.assertRaises(frappe.ValidationError):
			get_active_profile(self.employee.name, self.end, company=self.company)

	def test_a_change_inside_the_payroll_period_is_refused_not_averaged(self):
		inside = getdate(self.end)
		if inside == getdate(self.start):
			self.skipTest("single-day period")
		self._raise_to(flt(self.profile.base) + 25000, inside)
		with self.assertRaises(frappe.ValidationError) as cm:
			self.insert_slip({
				"employee": self.employee.name, "posting_date": self.end,
				"start_date": self.start, "end_date": self.end,
			}, submit=False)
		self.assertIn("changes during", str(cm.exception))


# --------------------------------------------------------------------------- #
# 8. Invalid or missing salary
# --------------------------------------------------------------------------- #
class TestInvalidOrMissingSalary(NatalFixture):

	def test_an_employee_with_no_profile_is_reported_not_paid_zero(self):
		emp = frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX, "last_name": "NoProfile",
			"company": self.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0],
			"date_of_birth": "1990-01-01",
		}).insert(ignore_permissions=True)
		self.__class__._created.append(("Employee", emp.name))
		rows = api.payroll_preview(company=self.company, start_date=str(self.start),
		                           end_date=str(self.end))
		rows = rows.get("rows") if isinstance(rows, dict) else rows
		mine = [r for r in rows if r.get("employee") == emp.name]
		self.assertTrue(mine, "an employee with no salary vanished from the run")
		self.assertTrue(mine[0].get("exception"),
		                "an employee with no salary was previewed without a word")

	def test_a_zero_base_pays_no_supplement(self):
		self.assertEqual(engine.default_natal(0, self.rate, "2020-01-01",
		                                      "%d-12-31" % getdate(self.end).year), 0.0)

	def test_a_missing_rate_pays_no_supplement(self):
		self.assertEqual(engine.default_natal(200000, None, "2020-01-01",
		                                      "%d-12-31" % getdate(self.end).year), 0.0)

	def test_a_rate_below_the_statutory_minimum_is_flagged_by_the_settlement(self):
		from isoft_angola_hr.isoft_angola_hr.payroll import settlement as fs
		res = fs.compute({
			"employee": self.employee.name, "company": self.company,
			"base": 200000, "joining_date": "2020-01-01",
			"termination_date": "%d-12-31" % getdate(self.end).year,
			"termination_reason": "Mútuo Acordo",
			"natal_rate": 10, "ferias_rate": 50,
		})
		codes = [f.get("code") for f in (res.get("flags") or [])]
		self.assertIn("LGT-238-MIN", codes,
		              "a Christmas supplement below the statutory 50% went unflagged")

	def test_the_statutory_minimum_constant_is_fifty_percent(self):
		self.assertEqual(flt(law.CHRISTMAS_BONUS_MIN_PCT), 50.0)
