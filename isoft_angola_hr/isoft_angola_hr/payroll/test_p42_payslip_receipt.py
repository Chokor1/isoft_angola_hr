# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""QA for the "Recibo de Vencimento" / "Payslip" print format — the bilingual A4 receipt.

PRESENTATION ONLY. Nothing here asserts what payroll should compute; it asserts that
whatever the engine stored on the Isoft Salary Slip is what the receipt prints, in the
language of the person printing it, on two A4 pages (ORIGINAL + DUPLICADO), and that the
receipt cannot be used to read somebody else's pay.

SAFETY — same contract as every other phase in this folder. The fixture extends
:class:`~isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration.PayrollFixture`:
every record is prefixed ``_TEST AHR``, registered for explicit deletion, and each test
runs inside a savepoint. No existing employee, profile, user or slip is written to.

The period is the PREVIOUS calendar month so that Attendance can be marked on any day of
it — ERPNext refuses Attendance on a future date, which is how a fixture pinned to the
current month fails on the first days of every month.

Set ``ISOFT_PAYSLIP_PDF_OUT=/some/dir`` to keep the PDFs the stress test produces.
"""

import io
import json
import os
import re
import unittest
from contextlib import contextmanager

import frappe
from frappe.utils import (add_days, add_months, flt, get_first_day, get_last_day, getdate,
                          nowdate)

from isoft_angola_hr.isoft_angola_hr import install
from isoft_angola_hr.isoft_angola_hr.payroll import engine, recibo
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

FORMAT = "Recibo de Vencimento"
CTX = recibo.TRANSLATION_CONTEXT

try:  # Page text comes from PyMuPDF when installed, otherwise from pdfminer.six
	import fitz  # noqa: F401
	HAS_FITZ = True
except ImportError:
	HAS_FITZ = False
try:  # pdfminer.six ships in the bench environment, so the page check really runs
	import pdfminer.high_level  # noqa: F401
	HAS_PDFMINER = True
except ImportError:
	HAS_PDFMINER = False
HAS_PAGE_TEXT = HAS_FITZ or HAS_PDFMINER


class _Lang:
	"""Render in a given language, then put the session back."""

	def __init__(self, lang):
		self.lang = lang

	def __enter__(self):
		self._before = frappe.local.lang
		frappe.local.lang = self.lang
		frappe.local.lang_full_dict = None

	def __exit__(self, *exc):
		frappe.local.lang = self._before
		frappe.local.lang_full_dict = None


def render(slip_name, lang="pt", as_pdf=False, doc=None):
	"""Through frappe.get_print — the same call the Print view and the PDF download use,
	so permissions, the stylesheet and the page options are applied as in production."""
	with _Lang(lang):
		return frappe.get_print("Isoft Salary Slip", slip_name, print_format=FORMAT,
		                        no_letterhead=1, as_pdf=as_pdf, doc=doc)


def text_of(html):
	"""Visible text, whitespace-normalised."""
	body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
	body = re.sub(r"<[^>]+>", " ", body)
	body = frappe.utils.html_utils.unescape_html(body)
	return re.sub(r"\s+", " ", body)


def pdf_info(pdf):
	"""(page count, [(width, height)]) via PyPDF2, which Frappe ships."""
	from PyPDF2 import PdfFileReader

	reader = PdfFileReader(io.BytesIO(pdf))
	sizes = []
	for i in range(reader.numPages):
		box = reader.getPage(i).mediaBox
		sizes.append((round(float(box.getWidth())), round(float(box.getHeight()))))
	return reader.numPages, sizes


def pdf_page_texts(pdf):
	"""Text of every page. PyPDF2 1.x cannot read wkhtmltopdf's fonts; PyMuPDF and
	pdfminer.six both can, and either one is enough."""
	if HAS_FITZ:
		import fitz

		doc = fitz.open(stream=pdf, filetype="pdf")
		return [re.sub(r"\s+", " ", page.get_text()) for page in doc]
	from pdfminer.high_level import extract_text
	from pdfminer.pdfpage import PDFPage

	count = sum(1 for _page in PDFPage.get_pages(io.BytesIO(pdf)))
	return [re.sub(r"\s+", " ", extract_text(io.BytesIO(pdf), page_numbers=[i]))
	        for i in range(count)]


def money(v):
	return frappe.utils.fmt_money(flt(v), 2)


class ReceiptFixture(PayrollFixture):
	"""Two employees with users, one of them with very long names, plus HR staff users."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		# Previous calendar month — fully in the past, so Attendance can be marked.
		cls.start = get_first_day(add_months(getdate(nowdate()), -1))
		cls.end = get_last_day(cls.start)

		cls.department = cls._department(PREFIX + " Logística e Distribuição Internacional")
		cls.designation = cls._designation(PREFIX + " Técnico Superior de Operações Logísticas")

		emp = frappe.get_doc("Employee", cls.employee.name)
		for field, value in (
			("custom_nif", "5417600123"),
			("custom_inss_number", "9988776655"),
			("custom_iban", "AO06000600000100037131174"),
			("salary_mode", "Bank"),
			("bank_name", "BAI"),
			("designation", cls.designation),
			("department", cls.department),
			("employment_type", cls._employment_type()),
		):
			if emp.meta.has_field(field):
				emp.set(field, value)
		emp.flags.ignore_permissions = True
		emp.save(ignore_permissions=True)

		# Employee B: the longest name on record, a seven-figure salary, cash payment.
		cls.employee_b = cls._track(frappe.get_doc({
			"doctype": "Employee", "first_name": PREFIX + " Hermenegildo Vunge Chiquito",
			"middle_name": "Caiango dos Santos Ferreira",
			"last_name": "da Silva Guimarães Júnior",
			"company": cls.company, "date_of_joining": "2020-01-01", "status": "Active",
			"gender": frappe.get_all("Gender", pluck="name")[0], "date_of_birth": "1988-05-05",
			"department": cls.department, "designation": cls.designation, "salary_mode": "Cash",
		}).insert(ignore_permissions=True))
		cls.profile_b = cls._track(frappe.get_doc({
			"doctype": "Isoft Salary Profile", "employee": cls.employee_b.name,
			"company": cls.company, "from_date": "2020-01-01",
			"base": 12345678.90, "food_allowance": 250000, "transport_allowance": 175000,
		}).insert(ignore_permissions=True))

		cls.users = {}
		for key, role, lang, employee in (
			("a", "Employee", "pt", cls.employee),
			("b", "Employee", "en", cls.employee_b),
			("hr", "HR Manager", "pt", None),
			("payroll", "Payroll Officer", "en", None),
		):
			cls.users[key] = cls._user(key, role, lang, employee)
		frappe.flags.pop("_isoft_ess_employee", None)

	@classmethod
	def _user(cls, key, role, lang, employee=None):
		"""A user holding exactly one role.

		The Employee role is granted AFTER the Employee record points at the user:
		ERPNext's User hook (validate_employee_role) silently strips the Employee role
		from any user no Employee record is linked to.
		"""
		email = "{0}.{1}@example.com".format(PREFIX.lower().replace(" ", ""), key)
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		user = cls._track(frappe.get_doc({
			"doctype": "User", "email": email, "first_name": PREFIX + " " + key,
			"send_welcome_email": 0, "language": lang,
		}).insert(ignore_permissions=True))
		if employee:
			frappe.db.set_value("Employee", employee.name, "user_id", email)
		user.add_roles(role)
		assert role in frappe.get_roles(email), "%s did not receive the %s role" % (email, role)
		return email

	@classmethod
	def _designation(cls, name):
		if not frappe.db.exists("Designation", name):
			cls._created.append(("Designation", name))
			frappe.get_doc({"doctype": "Designation", "designation_name": name}).insert(
				ignore_permissions=True)
		return name

	@classmethod
	def _department(cls, name):
		existing = frappe.db.get_value("Department", {"department_name": name, "company": cls.company})
		if existing:
			return existing
		doc = frappe.get_doc({"doctype": "Department", "department_name": name,
		                      "company": cls.company}).insert(ignore_permissions=True)
		cls._created.append(("Department", doc.name))
		return doc.name

	@classmethod
	def _employment_type(cls):
		return (frappe.db.exists("Employment Type", "Full-time") and "Full-time") \
			or frappe.get_all("Employment Type", pluck="name", limit=1)[0]

	@contextmanager
	def as_user(self, key):
		frappe.set_user(self.users[key])
		frappe.flags.pop("_isoft_ess_employee", None)
		try:
			yield
		finally:
			frappe.set_user("Administrator")
			frappe.flags.pop("_isoft_ess_employee", None)

	def _attendance(self, employee, date, status="Present", overtime=None, leave_type=None):
		doc = frappe.get_doc({
			"doctype": "Attendance", "employee": employee, "company": self.company,
			"attendance_date": getdate(date), "status": status,
		})
		if leave_type:
			doc.leave_type = leave_type
		if overtime is not None and doc.meta.has_field("custom_overtime_hours"):
			doc.custom_overtime_hours = overtime
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	def make_slip_for(self, employee, profile, submit=True, **kwargs):
		values = {
			"doctype": "Isoft Salary Slip", "employee": employee.name,
			"company": self.company, "posting_date": self.end,
			"start_date": self.start, "end_date": self.end, "salary_profile": profile.name,
		}
		values.update(kwargs)
		return self.insert_slip(values, submit=submit)

	def enabled_inputs(self, **amounts):
		"""Only the monthly inputs the site has switched on — a disabled component makes
		the slip refuse to save, which would be a settings failure, not a receipt one."""
		settings = engine.get_settings()
		out = {}
		for field, abbr in (("productivity_bonus", "PPD"), ("overtime_hours", "HEX"),
		                    ("adiantamento", "ADT"), ("subsidio_ferias", "SFE"),
		                    ("subsidio_natal", "SNA")):
			if field in amounts and engine.component_enabled(settings, abbr):
				out[field] = amounts[field]
		return out

	def weekday(self, offset):
		"""First Monday–Friday on or after ``start + offset``: an absence on a Sunday is
		not a working day for the default calendar and reduces nothing."""
		d = add_days(self.start, offset)
		while getdate(d).weekday() > 4:
			d = add_days(d, 1)
		return d

	def full_slip(self):
		"""Employee A: attendance-validated, with overtime, an absence and a leave day."""
		self._attendance(self.employee.name, self.weekday(1), overtime=3.0)
		self._attendance(self.employee.name, self.weekday(4), overtime=2.5)
		self._attendance(self.employee.name, self.weekday(8), "Absent")
		leave_type = frappe.get_all("Leave Type", pluck="name", limit=1)
		self._attendance(self.employee.name, self.weekday(11), "On Leave",
		                 leave_type=leave_type[0] if leave_type else None)
		return self.make_slip(submit=True, validate_attendance=1,
		                      # overtime is entered as hours: the 3 + 2.5 approved above
		                      **self.enabled_inputs(productivity_bonus=25000, overtime_hours=5.5))


# --------------------------------------------------------------------------- #
# HR review of October 2026: the month, the issue date and a full page per copy
# --------------------------------------------------------------------------- #
class TestReceiptOctoberReview(ReceiptFixture):
	"""A 23 June - 22 July cycle is July's payroll: Period "July 2026" (not "June/July"),
	Issue Date 31-07-2026, no Processing Information box, and each copy fills its page."""

	@staticmethod
	def period(start, end):
		return frappe._dict(start_date=start, end_date=end)

	def test_the_period_is_the_month_the_period_ends_in(self):
		cycle = self.period("2026-06-23", "2026-07-22")
		with _Lang("en"):
			self.assertEqual(recibo._period_label(cycle), "July 2026")
		with _Lang("pt"):
			self.assertEqual(recibo._period_label(cycle), "Julho 2026")
		with _Lang("en"):
			self.assertEqual(recibo._period_label(self.period("2026-12-23", "2027-01-22")), "January 2027")
			self.assertEqual(recibo._period_label(self.period("2026-07-01", "2026-07-31")), "July 2026")

	def test_the_issue_date_is_the_last_day_of_that_month(self):
		for start, end, expected in (("2026-06-23", "2026-07-22", "2026-07-31"),
		                             ("2026-11-23", "2026-12-22", "2026-12-31"),
		                             ("2028-01-23", "2028-02-22", "2028-02-29"),
		                             ("2026-07-01", "2026-07-31", "2026-07-31")):
			self.assertEqual(recibo._issue_date(self.period(start, end)), getdate(expected), end)

	def test_the_receipt_prints_them(self):
		slip = self.full_slip()
		with _Lang("en"):
			ctx = recibo.get_context(slip)
		text = text_of(render(slip.name, lang="en"))
		self.assertIn(ctx["period_label"], text)
		self.assertNotIn("/", ctx["period_label"])
		self.assertIn(frappe.utils.formatdate(get_last_day(slip.end_date)), text)

	def test_overtime_hours_are_printed_on_the_overtime_line(self):
		"""They used to be in the Processing Information box."""
		slip = self.full_slip()
		if not flt(slip.overtime_hours):
			self.skipTest("overtime is switched off on this site")
		printed = frappe.utils.fmt_money(recibo.get_context(slip)["overtime"]["hours"], 2)
		printed = printed[:-3] if printed[-2:] == "00" else printed      # the template's qty()
		self.assertIn("(%s h)" % printed, text_of(render(slip.name, lang="pt")))

	def test_each_copy_is_one_full_page(self):
		slip = self.full_slip()
		html = render(slip.name, lang="pt")
		self.assertEqual(html.count('class="rv-page"'), 2, "one full-page table per copy")
		css = frappe.db.get_value("Print Format", FORMAT, "css")
		self.assertRegex(css, r"\.rv \.rv-page \{[^}]*height:\s*\d+mm", "the copy has a fixed page height")


# --------------------------------------------------------------------------- #
# Content — the receipt prints what the slip stores
# --------------------------------------------------------------------------- #
class TestReceiptContent(ReceiptFixture):
	def test_renders_without_a_template_error_in_both_languages(self):
		slip = self.full_slip()
		for lang in ("pt", "en"):
			html = render(slip.name, lang=lang)
			self.assertNotIn("Traceback", html)
			self.assertNotIn("jinja", html.lower())
			self.assertNotIn("Error", text_of(html)[:200])

	def test_prints_employee_department_components_and_totals(self):
		slip = self.full_slip()
		text = text_of(render(slip.name, lang="pt"))

		self.assertIn(slip.employee_name, text)
		self.assertIn(slip.employee, text)
		self.assertIn(self.department.rsplit(" - ", 1)[0], text)
		self.assertIn(self.designation, text)
		self.assertIn("5417600123", text)          # NIF
		self.assertIn("9988776655", text)          # social security number
		for row in slip.earnings:
			if not row.do_not_include_in_total:
				self.assertIn(money(row.amount), text, row.salary_component)
		# Deductions are summarised (employee INSS, IRT, total), not itemised (HR review).
		self.assertIn(money(slip.ss_employee_amount), text)
		self.assertIn(money(slip.irt_amount), text)
		self.assertIn(money(slip.gross_pay), text)           # as Total de Remunerações
		self.assertIn(money(slip.taxable_income), text)
		self.assertIn(money(slip.total_deduction), text)
		self.assertIn(money(slip.net_pay), text)
		self.assertIn("Total a Receber".upper(), text.upper())

	def test_the_elements_hr_asked_to_remove_are_not_printed(self):
		"""HR review of 10 September 2026: no company contacts, no employment type, no
		admission date, no repeated payroll period, no itemised deductions table, no gross
		line in the summary, no date field beside the signature."""
		slip = self.full_slip()
		company = frappe.db.get_value("Company", self.company, ["phone_no", "email", "website"], as_dict=True)
		for lang in ("pt", "en"):
			html = render(slip.name, lang=lang)
			text = text_of(html)
			for value in (company.phone_no, company.email, company.website):
				if value:
					self.assertNotIn(value, text, value)
			self.assertNotIn(self._employment_type(), text)
			self.assertNotIn(frappe.utils.formatdate(self.employee.date_of_joining), text)
			for label in ("Tipo de Contrato", "Employment Type", "Data de Admissão", "Admission Date",
			              "Período de Processamento", "Payroll Period", "Salário Bruto", "Gross Salary",
			              # HR review of October 2026: no Processing Information box
			              "Informação de Processamento", "Processing Information",
			              "Dias Pagos", "Paid Days", "Total Working Days", "Absence Days"):
				self.assertNotIn(label, text, label)
			# the deductions table (its section title and its D-codes) is gone; the summary stays
			self.assertNotIn('rv-sec-title">Descontos<', html)
			self.assertNotIn('rv-sec-title">Deductions<', html)
			self.assertNotIn("D01", text)
			self.assertIn("Total de Descontos" if lang == "pt" else "Total Deductions", text)
			# no logo
			self.assertNotIn("rv-logo", html)
			self.assertNotIn("<img", html)
			# one signature line, no date caption
			self.assertEqual(html.count('class="rv-sign-line"'), 2, "one signature line per copy")
			self.assertNotIn("rv-sign-cap\">Data:", html)
			self.assertNotIn("rv-sign-cap\">Date:", html)

	def test_context_copies_the_headline_figures_from_the_slip(self):
		slip = self.full_slip()
		ctx = recibo.get_context(slip)

		self.assertAlmostEqual(ctx["summary"]["gross"], flt(slip.gross_pay), places=2)
		self.assertAlmostEqual(ctx["summary"]["taxable"], flt(slip.taxable_income), places=2)
		self.assertAlmostEqual(ctx["summary"]["inss"], flt(slip.ss_employee_amount), places=2)
		self.assertAlmostEqual(ctx["summary"]["irt"], flt(slip.irt_amount), places=2)
		self.assertAlmostEqual(ctx["summary"]["total_deductions"], flt(slip.total_deduction), places=2)
		self.assertAlmostEqual(ctx["summary"]["net"], flt(slip.net_pay), places=2)
		self.assertTrue(ctx["reconciles"])
		self.assertEqual(ctx["document"], slip.name)
		self.assertEqual(ctx["employee"]["employee_id"], slip.employee)

	def test_days_are_the_slips_own_counters(self):
		slip = self.full_slip()
		days = recibo.get_context(slip)["days"]

		self.assertAlmostEqual(days["paid"], flt(slip.payment_days), places=2)
		self.assertAlmostEqual(days["total"], flt(slip.total_working_days), places=2)
		self.assertAlmostEqual(days["absence"], flt(slip.total_working_days - slip.payment_days, 2),
		                       places=2)
		self.assertGreater(days["absence"], 0, "the fixture records an unjustified absence")
		self.assertEqual(days["leave"], 1.0, "one On Leave attendance in the period")
		self.assertAlmostEqual(days["overtime_hours"], 5.5, places=2)

	def test_leave_days_agree_with_the_hr_explanation_panel(self):
		from isoft_angola_hr.isoft_angola_hr import hr_api

		slip = self.full_slip()
		explained = hr_api.salary_slip_explanation(slip.name)
		ctx = recibo.get_context(slip)
		self.assertEqual(flt(explained["leave_days"]), ctx["days"]["leave"])
		self.assertEqual(flt(explained["absent_days"]), ctx["days"]["absence"])

	def test_payment_block_reads_recorded_data_only(self):
		slip = self.full_slip()
		pay = recibo.get_context(slip)["payment"]
		self.assertEqual(pay["method"], "bank")
		self.assertEqual(pay["iban"], "AO06000600000100037131174")
		self.assertEqual(pay["bank_name"], "BAI")
		self.assertIsNone(pay["date"], "no payment Journal Entry: no payment date may be invented")

		slip_b = self.make_slip_for(self.employee_b, self.profile_b)
		pay_b = recibo.get_context(slip_b)["payment"]
		self.assertEqual(pay_b["method"], "cash")
		self.assertIsNone(pay_b["iban"])

	def test_statistical_rows_are_informative_and_excluded_from_totals(self):
		slip = self.full_slip()
		slip.append("earnings", {"salary_component": "Falta Injustificada", "abbr": "F03",
		                         "amount": 5999.94, "do_not_include_in_total": 1})
		ctx = recibo.get_context(slip)
		self.assertIn("F03", [m["code"] for m in ctx["memos"]])
		self.assertAlmostEqual(ctx["total_earnings"], flt(slip.gross_pay), places=2)
		text = text_of(render(slip.name, lang="pt", doc=slip))
		self.assertIn("Falta Injustificada", text)
		self.assertIn("informativo", text)


# --------------------------------------------------------------------------- #
# Language — follows the reader, never the boot language
# --------------------------------------------------------------------------- #
class TestReceiptLanguage(ReceiptFixture):
	PT = ("Recibo de Vencimento", "Funcionário", "Salário Base", "Descontos",
	      "Assinatura do Trabalhador", "Declaro que recebi a quantia constante neste recibo.",
	      "DUPLICADO - ARQUIVO EMPRESA", "TOTAL A RECEBER")
	EN = ("Payslip", "Employee", "Basic Salary", "Deductions", "Employee Signature",
	      "I confirm receipt of the amount stated in this payslip.",
	      "DUPLICATE - COMPANY FILE", "TOTAL TO RECEIVE")

	def test_portuguese_user_gets_recibo_de_vencimento(self):
		text = text_of(render(self.full_slip().name, lang="pt"))
		for label in self.PT:
			self.assertIn(label, text)
		for label in ("Basic Salary", "Employee Signature", "Payslip"):
			self.assertNotIn(label, text)
		# ERPNext's Brazilian catalogue must not leak in.
		self.assertNotIn("Folha de Pagamento", text)
		self.assertNotIn("Holerite", text)

	def test_english_user_gets_payslip(self):
		text = text_of(render(self.full_slip().name, lang="en"))
		for label in self.EN:
			self.assertIn(label, text)
		for label in ("Recibo de Vencimento", "Assinatura do Trabalhador", "Descontos"):
			self.assertNotIn(label, text)

	def test_regional_portuguese_gets_the_angolan_wording(self):
		text = text_of(render(self.full_slip().name, lang="pt-BR"))
		self.assertIn("Recibo de Vencimento", text)
		self.assertIn("Assinatura do Trabalhador", text)
		self.assertNotIn("Holerite", text)

	def test_language_is_resolved_on_every_render(self):
		slip = self.full_slip()
		self.assertIn("Assinatura do Trabalhador", text_of(render(slip.name, lang="pt")))
		self.assertIn("Employee Signature", text_of(render(slip.name, lang="en")))
		self.assertIn("Assinatura do Trabalhador", text_of(render(slip.name, lang="pt")))

	def test_identifiers_are_never_translated(self):
		slip = self.full_slip()
		pt, en = text_of(render(slip.name, lang="pt")), text_of(render(slip.name, lang="en"))
		company = frappe.db.get_value("Company", self.company, "company_name")
		for value in (slip.employee_name, company, "5417600123", "9988776655",
		              "AO06000600000100037131174", slip.name, "R101"):
			self.assertIn(value, pt)
			self.assertIn(value, en)

	def test_portuguese_line_labels_are_the_engines_own_component_names(self):
		with _Lang("pt"):
			for abbr, msgid in recibo.COMPONENT_LABELS.items():
				self.assertEqual(frappe._(msgid, context=CTX), engine.COMPONENTS[abbr]["name"],
				                 abbr)

	def test_msgid_tables_hold_plain_english(self):
		accents = r"[ãõáéíóúâêôàçÃÕÁÉÍÓÚÂÊÔÀÇ]"
		for msgid in list(recibo.COMPONENT_LABELS.values()) + [c["label"] for c in recibo.COPIES]:
			self.assertNotRegex(msgid, accents, "%r is already translated" % msgid)

	def test_period_label_follows_the_language(self):
		slip = self.full_slip()
		with _Lang("en"):
			en = recibo.get_context(slip)["period_label"]
		with _Lang("pt"):
			pt = recibo.get_context(slip)["period_label"]
		self.assertIn(str(getdate(self.end).year), en)
		self.assertNotEqual(en, pt, "month names must be localised")


# --------------------------------------------------------------------------- #
# Two copies on two A4 pages
# --------------------------------------------------------------------------- #
class TestReceiptPdf(ReceiptFixture):
	def test_html_carries_original_and_duplicate_with_identical_figures(self):
		slip = self.full_slip()
		html = render(slip.name, lang="pt")
		copies = re.findall(r'class="rv-copy rv-copy-(\w+)', html)
		self.assertEqual(copies, ["original", "duplicate"])
		text = text_of(html)
		self.assertEqual(text.count("ORIGINAL"), 2, "copy label + footer of page 1")
		self.assertEqual(text.count("DUPLICADO - ARQUIVO EMPRESA"), 2, "copy label + footer of page 2")
		self.assertEqual(text.count(slip.employee_name), 2)
		self.assertGreaterEqual(text.count(money(slip.net_pay)), 4)  # net + total band, twice

	def test_pdf_is_two_a4_portrait_pages(self):
		pdf = render(self.full_slip().name, lang="pt", as_pdf=True)
		self.assertTrue(pdf.startswith(b"%PDF"))
		pages, sizes = pdf_info(pdf)
		self.assertEqual(pages, 2, "ORIGINAL on page 1, DUPLICADO on page 2")
		self.assertEqual(sizes, [(595, 842), (595, 842)])

	@unittest.skipUnless(HAS_PAGE_TEXT, "neither PyMuPDF nor pdfminer.six is installed")
	def test_pdf_pages_carry_the_same_payroll_data(self):
		slip = self.full_slip()
		pages = pdf_page_texts(render(slip.name, lang="pt", as_pdf=True))
		self.assertEqual(len(pages), 2)
		self.assertIn("ORIGINAL", pages[0])
		self.assertIn("DUPLICADO - ARQUIVO EMPRESA", pages[1])
		for page in pages:
			self.assertIn(slip.employee_name, page)
			self.assertIn(money(slip.net_pay), page)
			self.assertIn(money(slip.gross_pay), page)
			self.assertIn("RECIBO DE VENCIMENTO", page.upper())

	def test_long_names_large_amounts_and_many_rows_still_fit_two_pages(self):
		"""The stress case: 45-character name, long department and designation, a
		seven-figure salary, every optional earning, several deductions and two
		informative rows. Nothing may be clipped and each copy must stay on one page."""
		slip = self.make_slip_for(self.employee_b, self.profile_b, **self.enabled_inputs(
			productivity_bonus=1234567.89, overtime_hours=200,
			subsidio_ferias=6172839.45, subsidio_natal=6172839.45, adiantamento=250000))
		for name, abbr, amount in (("Quota Sindical", "QS", 12345.67),
		                           ("Penhora Judicial", "PJ", 98765.43)):
			slip.append("deductions", {"salary_component": name, "abbr": abbr, "amount": amount})
		slip.append("earnings", {"salary_component": "Falta Injustificada", "abbr": "F03",
		                         "amount": 5999.94, "do_not_include_in_total": 1})
		slip.append("deductions", {"salary_component": "Dias de Falta", "abbr": "F40",
		                           "amount": 1.0, "do_not_include_in_total": 1})
		self.assertGreaterEqual(len([e for e in slip.earnings if not e.do_not_include_in_total]), 6)

		for lang in ("pt", "en"):
			html = render(slip.name, lang=lang, doc=slip)
			self.assertNotIn("Traceback", html)
			text = text_of(html)
			self.assertIn(self.employee_b.employee_name, text)
			# Deductions are no longer itemised (HR review); their total still prints.
			self.assertNotIn("Quota Sindical", text)
			self.assertNotIn("Penhora Judicial", text)
			self.assertIn(money(slip.total_deduction), text)
			self.assertIn(money(slip.gross_pay), text)
			self.assertIn(money(slip.net_pay), text)
			pdf = render(slip.name, lang=lang, as_pdf=True, doc=slip)
			out = os.environ.get("ISOFT_PAYSLIP_PDF_OUT")
			if out:
				with open(os.path.join(out, "stress_%s.pdf" % lang), "wb") as f:
					f.write(pdf)
			pages, sizes = pdf_info(pdf)
			self.assertEqual(pages, 2, "%s: a copy overflowed its page" % lang)
			self.assertEqual(sizes, [(595, 842), (595, 842)])
			if HAS_PAGE_TEXT:
				# Each copy prints the same thing as the HTML above: no itemised deductions
				# (HR review), their total, the net and the full name. This block only ran
				# with PyMuPDF and still expected the itemised deduction the format dropped.
				for page in pdf_page_texts(pdf):
					self.assertNotIn("Penhora Judicial", page)
					self.assertIn(money(slip.total_deduction), page)
					self.assertIn(money(slip.net_pay), page)
					self.assertIn("Guimarães Júnior", page)

	def test_pdf_page_options_are_stated_in_the_stylesheet(self):
		css = frappe.db.get_value("Print Format", FORMAT, "css") or ""
		for attr in ("margin-top", "margin-bottom", "margin-left", "margin-right"):
			self.assertRegex(css, r"\.print-format\s*{[^}]*" + attr + r":\s*\d+mm;",
			                 "%s must be declared for wkhtmltopdf" % attr)


# --------------------------------------------------------------------------- #
# Installation — one format, wired as the default, sourced from files
# --------------------------------------------------------------------------- #
class TestReceiptInstallation(ReceiptFixture):
	def test_receipt_is_the_default_print_format_of_the_slip(self):
		self.assertEqual(frappe.get_meta("Isoft Salary Slip").default_print_format, FORMAT)

	def test_format_is_editable_from_the_desk(self):
		row = frappe.db.get_value("Print Format", FORMAT, ["standard", "custom_format",
		                                                   "print_format_type", "disabled"], as_dict=True)
		self.assertEqual(row.standard, "No")
		self.assertEqual(row.custom_format, 1)
		self.assertEqual(row.print_format_type, "Jinja")
		self.assertEqual(row.disabled, 0)

	def test_site_record_matches_the_template_files(self):
		html, css = install.payslip_template()
		row = frappe.db.get_value("Print Format", FORMAT, ["html", "css"], as_dict=True)
		self.assertEqual(row.html, html, "run bench migrate: the Print Format is stale")
		self.assertEqual(row.css, css, "run bench migrate: the Print Format is stale")

	def test_json_fixture_matches_the_template_files(self):
		html, css = install.payslip_template()
		with io.open(os.path.join(install.PAYSLIP_TEMPLATE_DIR, "recibo_de_vencimento.json"),
		             encoding="utf-8") as f:
			fixture = json.load(f)
		self.assertEqual(fixture["html"], html)
		self.assertEqual(fixture["css"], css)
		self.assertEqual(fixture["standard"], "No")
		self.assertEqual(fixture["doc_type"], "Isoft Salary Slip")

	def test_template_translates_at_render_time_with_the_receipt_context(self):
		html, _css = install.payslip_template()
		self.assertIn('set ctx = "%s"' % CTX, html)
		self.assertIn("_(msgid, context=ctx)", html)
		# no bare _("...") outside the t() macro: every label must carry the context
		bare = [m for m in re.findall(r'_\("([^"]+)"\)', html)]
		self.assertEqual(bare, [], "labels without the receipt context: %s" % bare)


# --------------------------------------------------------------------------- #
# Security — the receipt is not a way around record-level permissions
# --------------------------------------------------------------------------- #
class TestReceiptSecurity(ReceiptFixture):
	def test_employee_a_cannot_open_employee_bs_receipt(self):
		from frappe.utils.print_format import download_pdf

		slip_b = self.make_slip_for(self.employee_b, self.profile_b)
		with self.as_user("a"):
			with self.assertRaises(frappe.PermissionError):
				render(slip_b.name, lang="pt")
			with self.assertRaises(frappe.PermissionError):
				render(slip_b.name, lang="pt", as_pdf=True)
			with self.assertRaises(frappe.PermissionError):
				recibo.get_context(slip_b.name)
			with self.assertRaises(frappe.PermissionError):
				download_pdf("Isoft Salary Slip", slip_b.name, format=FORMAT)

	def test_employee_b_cannot_open_employee_as_receipt(self):
		slip_a = self.full_slip()
		with self.as_user("b"):
			with self.assertRaises(frappe.PermissionError):
				render(slip_a.name, lang="en")
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("Isoft Salary Slip", slip_a.name).check_permission("print")

	def test_employee_can_open_their_own_approved_receipt(self):
		slip = self.full_slip()
		with self.as_user("a"):
			text = text_of(render(slip.name, lang="pt"))
			self.assertIn(slip.employee_name, text)
			self.assertIn(money(slip.net_pay), text)
			pdf = render(slip.name, lang="pt", as_pdf=True)
			self.assertEqual(pdf_info(pdf)[0], 2)

	def test_employee_cannot_print_a_draft(self):
		draft = self.make_slip(submit=False)
		with self.as_user("a"):
			with self.assertRaises(frappe.PermissionError):
				render(draft.name, lang="pt")

	def test_employee_list_is_scoped_to_their_own_slips(self):
		slip_a = self.full_slip()
		slip_b = self.make_slip_for(self.employee_b, self.profile_b)
		with self.as_user("a"):
			names = frappe.get_list("Isoft Salary Slip", pluck="name", limit_page_length=0)
		self.assertIn(slip_a.name, names)
		self.assertNotIn(slip_b.name, names)

	def test_hr_manager_and_payroll_officer_can_print_any_receipt(self):
		slip_a = self.full_slip()
		slip_b = self.make_slip_for(self.employee_b, self.profile_b)
		for key in ("hr", "payroll"):
			with self.as_user(key):
				for slip in (slip_a, slip_b):
					self.assertIn(slip.employee_name, text_of(render(slip.name, lang="en")))

	def test_the_context_helper_is_whitelisted_and_checks_read_permission(self):
		"""The Jinja helper is also an HTTP endpoint; called by name it must authorise."""
		self.assertIn(recibo.get_context, frappe.whitelisted)
		slip_b = self.make_slip_for(self.employee_b, self.profile_b)
		with self.as_user("a"):
			with self.assertRaises(frappe.PermissionError):
				recibo.get_context(slip_b.name)
		with self.as_user("b"):
			self.assertAlmostEqual(recibo.get_context(slip_b.name)["net_pay"], flt(slip_b.net_pay),
			                       places=2)
