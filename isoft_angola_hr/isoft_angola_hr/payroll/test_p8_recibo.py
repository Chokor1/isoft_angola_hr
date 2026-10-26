# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""QA for the "Recibo de Vencimentos - Angola" print format.

This file tests the PRESENTATION layer only. It never asserts what payroll *should*
compute — it asserts that whatever the engine computed is what the receipt prints, that
earnings and deductions never cross sides, and that the printed arithmetic reconciles
with the slip's own stored totals.

SAFETY — same contract as every other phase in this folder. The fixture is
:class:`~isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration.PayrollFixture`:
every record is prefixed ``_TEST AHR``, is registered for explicit deletion, and each
test runs inside a savepoint. No existing employee, profile or slip is written to.

The fixture deliberately exercises a *full* Angolan payslip — base, meal, transport,
productivity bonus, overtime, employee social security, IRT and an advance instalment —
because the bugs this format has had were all in the rows that only appear when a slip
is not trivial.
"""

import re

import frappe
from frappe.utils import add_days, add_months, flt, get_first_day, get_last_day, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import recibo
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

FORMAT_NAME = "Recibo de Vencimentos - Angola"


def _render(slip_name, lang="pt", as_pdf=False):
	"""Render through frappe.get_print — the same call the Print view and the PDF
	download both go through, so the stylesheet is applied exactly as in production.

	Building the HTML by hand and handing it to get_pdf() separately is what a first
	version of this file did, and it silently dropped the CSS: the receipt then ran to
	two pages and the page-count assertion was measuring the wrong document.
	"""
	previous = frappe.local.lang
	frappe.local.lang = lang
	try:
		return frappe.get_print(
			"Isoft Salary Slip", slip_name, print_format=FORMAT_NAME,
			no_letterhead=1, as_pdf=as_pdf,
		)
	finally:
		frappe.local.lang = previous


def _text(html):
	"""Visible text of the rendered receipt, whitespace-normalised."""
	body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
	body = re.sub(r"<[^>]+>", "\n", body)
	body = frappe.utils.html_utils.unescape_html(body)
	return re.sub(r"[ \t]+", " ", body)


class ReciboFixture(PayrollFixture):
	"""A payslip with every Angolan component on it, plus the identification data the
	receipt header prints."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		# The previous calendar month, not the current one: this fixture marks Attendance
		# on the 3rd, 4th and 9th of the period, and ERPNext refuses Attendance on a future
		# date — so on the 1st to the 9th of any month every test that calls full_slip()
		# failed with "Attendance can not be marked for future dates". A month that has
		# already ended has no future in it. Nothing here posts to the ledger, so the
		# accounting freeze date that keeps PayrollFixture in the current month does not
		# apply.
		cls.start = get_first_day(add_months(getdate(frappe.utils.nowdate()), -1))
		cls.end = get_last_day(cls.start)

		# Identification fields the header prints. Written straight onto the fixture
		# employee so the receipt has something to show for each labelled box.
		emp = frappe.get_doc("Employee", cls.employee.name)
		for field, value in (
			("custom_nif", "5417600123"),
			("custom_inss_number", "9988776655"),
			("custom_insurance", "AP-TEST-001"),
			("custom_iban", "AO06000600000100037131174"),
			("salary_mode", "Bank"),
			("designation", cls._designation()),
		):
			if emp.meta.has_field(field):
				emp.set(field, value)
		emp.flags.ignore_permissions = True
		emp.save(ignore_permissions=True)

		# Approved, disbursed advance with one instalment due inside the payroll period,
		# so the slip picks up an "Adiantamento" deduction through the normal path.
		cls.advance = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Advance", "employee": cls.employee.name,
			"company": cls.company, "request_date": add_days(cls.start, -40),
			"status": "Disbursed", "requested_amount": 60000, "approved_amount": 60000,
			"recovery_start_date": cls.start, "installments": 3,
			"installment_amount": 20000, "reason": PREFIX + " QA advance",
			"schedule": [{
				"period_start": cls.start, "period_end": cls.end,
				"amount": 20000, "recovered": 0, "status": "Pending",
			}],
		}).insert(ignore_permissions=True))

		# Approved overtime: submitted Attendance carrying custom_overtime_hours. These
		# are the ONLY rows the receipt may list as overtime.
		cls.ot_dates = [add_days(cls.start, 2), add_days(cls.start, 3)]
		cls.ot_hours = {cls.ot_dates[0]: 3.0, cls.ot_dates[1]: 2.5}
		# One unjustified absence, so the "Faltas" box has a non-zero column to print.
		cls.absent_date = add_days(cls.start, 8)

	@classmethod
	def _designation(cls):
		name = PREFIX + " Tecnico"
		if not frappe.db.exists("Designation", name):
			cls._created.append(("Designation", name))
			frappe.get_doc({"doctype": "Designation", "designation_name": name}).insert(
				ignore_permissions=True)
		return name

	def _attendance(self, date, status="Present", overtime=None):
		doc = frappe.get_doc({
			"doctype": "Attendance", "employee": self.employee.name,
			"company": self.company, "attendance_date": getdate(date), "status": status,
		})
		if overtime is not None and doc.meta.has_field("custom_overtime_hours"):
			doc.custom_overtime_hours = overtime
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	def full_slip(self):
		"""A submitted slip carrying every component the receipt has to render."""
		for date, hours in self.ot_hours.items():
			self._attendance(date, "Present", overtime=hours)
		self._attendance(self.absent_date, "Absent")

		return self.make_slip(
			submit=True,
			productivity_bonus=25000,
			# overtime is entered as hours: the approved Attendance hours above
			overtime_hours=sum(self.ot_hours.values()),
		)


# --------------------------------------------------------------------------- #
# The receipt reproduces the slip — it never invents or re-derives a figure
# --------------------------------------------------------------------------- #
class TestReciboFigures(ReciboFixture):
	def test_receipt_renders_without_a_template_error(self):
		slip = self.full_slip()
		html = _render(slip.name)
		self.assertNotIn("Traceback", html)
		self.assertNotIn("jinja", html.lower())
		self.assertIn("Recibo de Vencimentos", _text(html))

	def test_printed_totals_reconcile_with_the_slip(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)

		self.assertTrue(ctx["reconciles"], "printed columns must agree with the slip")
		self.assertAlmostEqual(ctx["total_earnings"], flt(slip.gross_pay), places=2)
		self.assertAlmostEqual(ctx["total_deductions"], flt(slip.total_deduction), places=2)
		# The identity the document itself claims at the bottom.
		self.assertAlmostEqual(
			ctx["gross_pay"] - ctx["total_deduction"], ctx["net_pay"], places=2,
			msg="Total Remunerações - Total Descontos must equal Total Pago")
		self.assertAlmostEqual(ctx["net_pay"], flt(slip.net_pay), places=2)

	def test_earnings_and_deductions_never_cross_sides(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)

		printed_earnings = {r["description"] for r in ctx["earnings"]}
		printed_deductions = {r["description"] for r in ctx["deductions"]}
		self.assertFalse(printed_earnings & printed_deductions,
		                 "a component may not appear on both sides")

		slip_earnings = {r.salary_component for r in slip.earnings}
		slip_deductions = {r.salary_component for r in slip.deductions}
		self.assertTrue(printed_earnings <= slip_earnings)
		self.assertTrue(printed_deductions <= slip_deductions)
		# Every earning row carries an amount in the Remunerações column and nothing else.
		self.assertTrue(all(flt(r["amount"]) > 0 for r in ctx["earnings"]))
		self.assertTrue(all(flt(r["amount"]) > 0 for r in ctx["deductions"]))

	def test_statistical_taxable_income_row_is_not_printed(self):
		slip = self.full_slip()
		self.assertIn("TI", [r.abbr for r in slip.earnings],
		              "fixture must actually carry the statistical row")
		ctx = recibo.get_context(slip)
		self.assertNotIn("TI", [r["code"] for r in ctx["earnings"]])
		self.assertNotIn("Rendimento Tributável", [r["description"] for r in ctx["earnings"]])

	def test_irt_box_is_copied_from_the_slip(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		irt_row = [r for r in slip.deductions if r.abbr == "IRT"]

		self.assertTrue(irt_row, "fixture must produce an IRT deduction")
		self.assertAlmostEqual(ctx["irt"]["retido"], flt(irt_row[0].amount), places=2)
		self.assertAlmostEqual(ctx["irt"]["retido"], flt(slip.irt_amount), places=2)
		self.assertAlmostEqual(ctx["irt"]["total_remuneracoes"], flt(slip.taxable_income),
		                       places=2)

	def test_inss_is_printed_as_a_deduction_matching_the_slip(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)

		self.assertAlmostEqual(ctx["inss"]["retido"], flt(slip.ss_employee_amount), places=2)
		printed = [r for r in ctx["deductions"] if r["description"] == "Segurança Social 3%"]
		self.assertEqual(len(printed), 1)
		self.assertAlmostEqual(printed[0]["amount"], flt(slip.ss_employee_amount), places=2)
		# The employer contribution is a company cost and must never reach the receipt.
		self.assertNotIn("Entidade Patronal",
		                 " ".join(r["description"] for r in ctx["deductions"]))

	def test_advance_instalment_is_printed_as_a_deduction(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		printed = [r for r in ctx["deductions"] if r["code"] == "D40"]

		self.assertEqual(len(printed), 1, "the advance instalment must be a printed line")
		self.assertAlmostEqual(printed[0]["amount"], flt(slip.advance_recovery), places=2)

	def test_angola_component_codes_are_printed(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		codes = {r["description"]: r["code"] for r in ctx["earnings"] + ctx["deductions"]}

		self.assertEqual(codes.get("Salário Base"), "R101")
		self.assertEqual(codes.get("Subsídio de Alimentação"), "R201")
		self.assertEqual(codes.get("Subsídio de Transporte"), "R204")
		self.assertEqual(codes.get("Segurança Social 3%"), "D01")
		self.assertEqual(codes.get("Adiantamento"), "D40")
		# IRT has no confirmed printed code on the reference receipt, so it falls back to
		# its abbreviation rather than to an invented number.
		self.assertEqual(codes.get("IRT"), "IRT")

	def test_memo_lines_print_but_move_neither_total(self):
		# A component flagged statistical is shown for the record only — this is how the
		# reference receipt prints its F03/F40 absence lines.
		slip = self.full_slip()
		slip.append("earnings", {
			"salary_component": "Falta Injustificada", "abbr": "F03",
			"amount": 5999.94, "do_not_include_in_total": 1,
		})
		ctx = recibo.get_context(slip)

		memo = [m for m in ctx["memos"] if m["code"] == "F03"]
		self.assertEqual(len(memo), 1, "the memo line must be printed")
		self.assertEqual(memo[0]["side"], "earning")
		self.assertNotIn("F03", [r["code"] for r in ctx["earnings"]])
		# and it changed neither total
		self.assertAlmostEqual(ctx["total_earnings"], flt(slip.gross_pay), places=2)
		self.assertTrue(ctx["reconciles"])

	def test_taxable_income_is_dropped_rather_than_shown_as_a_memo(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertNotIn("TI", [m["code"] for m in ctx["memos"]])
		self.assertNotIn("Rendimento Tributável", [m["description"] for m in ctx["memos"]])

	def test_header_vencimento_is_the_contractual_salary_not_the_prorated_line(self):
		# The reference receipt shows 120 000,00 in the header against a prorated
		# 114 461,58 on the R101 line; the header reads the Salary Profile.
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertAlmostEqual(ctx["vencimento"], flt(self.profile.base), places=2)

	def test_a_single_calendar_month_prints_as_the_month_name(self):
		months = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
		          "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		# The fixture period is exactly one calendar month, so the label is the month
		# name alone — no range, no year (Data Fecho below it carries the year).
		self.assertEqual(ctx["periodo"], months[getdate(self.start).month - 1])

	def test_an_unmapped_component_falls_back_to_its_abbreviation(self):
		# A site that adds its own component must still print something in "Código".
		self.assertEqual(recibo._code_for("ZZZ"), "ZZZ")
		self.assertEqual(recibo._code_for(None), "")


# --------------------------------------------------------------------------- #
# Overtime — approved records only, never re-derived
# --------------------------------------------------------------------------- #
class TestReciboOvertime(ReciboFixture):
	def test_overtime_lists_the_approved_attendance_rows(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		ot = ctx["overtime"]

		self.assertTrue(ot["show"])
		self.assertEqual(len(ot["rows"]), len(self.ot_hours))
		self.assertAlmostEqual(ot["hours"], sum(self.ot_hours.values()), places=2)
		self.assertEqual({getdate(r["date"]) for r in ot["rows"]},
		                 {getdate(d) for d in self.ot_hours})

	def test_overtime_value_is_the_stored_amount_not_a_recalculation(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertAlmostEqual(ctx["overtime"]["amount"], flt(slip.overtime_amount), places=2)

	def test_hours_entered_without_attendance_rows_print_the_slip_hours(self):
		# Overtime is entered as hours on the run, so an amount always has hours behind it.
		# With no dated Attendance rows the total line prints those hours, never a dash.
		slip = self.make_slip(submit=True, overtime_hours=6)
		ctx = recibo.get_context(slip)

		self.assertTrue(ctx["overtime"]["show"], "a paid amount must still be shown")
		self.assertEqual(ctx["overtime"]["rows"], [], "no dated Attendance rows")
		self.assertEqual(ctx["overtime"]["hours"], 6)
		self.assertAlmostEqual(ctx["overtime"]["amount"], flt(slip.overtime_amount), places=2)

		block = re.search(r"(?s)Horas Extraordin\u00e1rias.*?</table>", _render(slip.name))
		self.assertIsNotNone(block, "the overtime table must be rendered")
		self.assertNotIn("\u2014", frappe.utils.html_utils.unescape_html(block.group(0)))

	def test_overtime_block_is_suppressed_when_there_is_none(self):
		slip = self.make_slip(submit=True)          # no attendance, no overtime amount
		ctx = recibo.get_context(slip)
		self.assertFalse(ctx["overtime"]["show"],
		                 "an empty overtime table must not be printed at all")
		self.assertNotIn("Horas Extraordinárias", _text(_render(slip.name)))


# --------------------------------------------------------------------------- #
# Faltas
# --------------------------------------------------------------------------- #
class TestReciboFaltas(ReciboFixture):
	def test_absence_is_counted_in_the_faltas_box(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertTrue(ctx["faltas"]["has_data"])
		self.assertGreaterEqual(ctx["faltas"]["sdd"], 1)

	def test_faltas_box_prints_zeros_rather_than_disappearing(self):
		slip = self.make_slip(submit=True)
		ctx = recibo.get_context(slip)
		self.assertFalse(ctx["faltas"]["has_data"])
		for key in ("alim", "turno", "cdh", "cdd", "sdh", "sdd"):
			self.assertEqual(ctx["faltas"][key], 0)
		self.assertIn("Faltas", _text(_render(slip.name)))


# --------------------------------------------------------------------------- #
# Header, identification and language
# --------------------------------------------------------------------------- #
class TestReciboHeader(ReciboFixture):
	def test_company_block_comes_from_the_company_master(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		expected = frappe.db.get_value(
			"Company", self.company, ["company_name", "tax_id"], as_dict=True)

		self.assertEqual(ctx["company"]["name"], expected.company_name)
		self.assertEqual(ctx["company"]["nif"], expected.tax_id)
		# Nothing about the company may be baked into the template.
		html = frappe.db.get_value("Print Format", FORMAT_NAME, "html")
		self.assertNotIn(expected.company_name, html)
		if expected.tax_id:
			self.assertNotIn(expected.tax_id, html)

	def test_numero_mecanografico_falls_back_to_the_employee_id(self):
		# Regression: `name` is a column, not a DocField, so a has_field() filter dropped
		# it and this box printed blank for every employee without an employee_number.
		frappe.db.set_value("Employee", self.employee.name, "employee_number", None)
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertEqual(ctx["employee"]["numero_mecanografico"], self.employee.name)

	def test_employee_identification_is_read_from_the_employee_master(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertEqual(ctx["employee"]["n_contribuinte"], "5417600123")
		self.assertEqual(ctx["employee"]["n_beneficiario"], "9988776655")
		self.assertEqual(ctx["employee"]["seguro"], "AP-TEST-001")

	def test_receipt_is_portuguese_whatever_language_the_reader_uses(self):
		# Regression: the literals were wrapped in _(), and Frappe's "pt" catalogue maps
		# the English word "Data" to "Dados" — which corrupted the date column header.
		slip = self.full_slip()
		for lang in ("pt", "en", "pt-BR"):
			text = _text(_render(slip.name, lang=lang))
			self.assertIn("Recibo de Vencimentos", text)
			self.assertIn("Remunerações", text)
			self.assertIn("Descontos", text)
			self.assertNotIn("Dados", text)
			self.assertNotIn("Earnings", text)

	def test_draft_slip_is_stamped_as_having_no_legal_value(self):
		slip = self.make_slip(submit=False)
		self.assertIn("RASCUNHO", _text(_render(slip.name)))

	def test_payment_block_uses_the_employees_recorded_method(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)
		self.assertEqual(len(ctx["pagamentos"]), 1)
		row = ctx["pagamentos"][0]
		self.assertAlmostEqual(row["amount"], flt(slip.net_pay), places=2)
		self.assertEqual(row["percent"], 100.0)
		self.assertEqual(row["reference"], "AO06000600000100037131174")


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
class TestReciboPdf(ReciboFixture):
	def test_pdf_is_produced_on_a_single_a4_page(self):
		slip = self.full_slip()
		pdf = _render(slip.name, as_pdf=True)

		self.assertTrue(pdf.startswith(b"%PDF"), "a PDF must actually be produced")
		self.assertGreater(len(pdf), 1000)
		# One /Type /Page object per rendered page; /Type /Pages is the tree node, so the
		# trailing character has to be excluded or the tree is counted as a page.
		pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
		self.assertEqual(pages, 1, "the receipt must fit on one A4 page")
		# A4 portrait is 595 x 842 pt.
		self.assertIn(b"595", re.search(rb"/MediaBox\s*\[[^\]]*\]", pdf).group(0))
		self.assertIn(b"842", re.search(rb"/MediaBox\s*\[[^\]]*\]", pdf).group(0))


# --------------------------------------------------------------------------- #
# Permissions — the receipt must not become a way around them
# --------------------------------------------------------------------------- #
class TestReciboIsEditableInTheDesk(ReciboFixture):
	"""The receipt has to be editable and savable from the Print Format list.

	A format flagged standard="Yes" is refused by Print Format.validate() outside
	developer_mode, so it would be read-only on any production site.
	"""

	def test_format_is_not_flagged_standard(self):
		self.assertEqual(frappe.db.get_value("Print Format", FORMAT_NAME, "standard"), "No")

	def test_html_can_be_edited_and_saved_without_developer_mode(self):
		previous = frappe.conf.get("developer_mode")
		frappe.conf["developer_mode"] = 0
		doc = frappe.get_doc("Print Format", FORMAT_NAME)
		original = doc.html
		try:
			doc.html = original + "\n<!-- edited from the Desk -->"
			doc.save()
			self.assertIn("edited from the Desk",
			              frappe.db.get_value("Print Format", FORMAT_NAME, "html"))
		finally:
			doc.reload()
			doc.html = original
			doc.save()
			frappe.conf["developer_mode"] = previous


class TestReciboPermissions(ReciboFixture):
	def _employee_user(self):
		email = PREFIX.lower().replace(" ", "") + ".stranger@example.com"
		if not frappe.db.exists("User", email):
			user = frappe.get_doc({
				"doctype": "User", "email": email, "first_name": PREFIX + " Stranger",
				"send_welcome_email": 0, "roles": [{"role": "Employee"}],
			}).insert(ignore_permissions=True)
			self._created.append(("User", user.name))
		return email

	def test_context_helper_refuses_another_employees_slip(self):
		slip = self.full_slip()
		user = self._employee_user()
		frappe.set_user(user)
		try:
			# Called by name, the helper is an HTTP-reachable endpoint and must check.
			with self.assertRaises(frappe.PermissionError):
				recibo.get_context(slip.name)
		finally:
			frappe.set_user("Administrator")

	def test_print_view_refuses_another_employees_slip(self):
		slip = self.full_slip()
		user = self._employee_user()
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("Isoft Salary Slip", slip.name).check_permission("read")
		finally:
			frappe.set_user("Administrator")

	def test_payroll_staff_may_read_the_receipt(self):
		slip = self.full_slip()
		for role in ("HR Manager", "Payroll Officer"):
			email = "{0}.{1}@example.com".format(
				PREFIX.lower().replace(" ", ""), role.lower().replace(" ", ""))
			if not frappe.db.exists("User", email):
				u = frappe.get_doc({
					"doctype": "User", "email": email, "first_name": PREFIX + " " + role,
					"send_welcome_email": 0, "roles": [{"role": role}],
				}).insert(ignore_permissions=True)
				self._created.append(("User", u.name))
			frappe.set_user(email)
			try:
				ctx = recibo.get_context(slip.name)
				self.assertAlmostEqual(ctx["net_pay"], flt(slip.net_pay), places=2)
			finally:
				frappe.set_user("Administrator")
