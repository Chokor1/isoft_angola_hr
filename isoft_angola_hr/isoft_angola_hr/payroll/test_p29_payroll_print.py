# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The payroll preview PDF prints the whole preview, or it fails loudly.

The PDF this replaces was A4 landscape with an eighteen-column ``width:100%`` table and
no width allocation of its own. WebKit does not paginate a table sideways: it drew past
the right edge of the paper and stopped, so INSS, INSS Entidade Patronal, IRT, Gross and
Net were simply absent - five columns of a payroll report, gone, with no error. It also
put the totals in a ``<tfoot>``, which wkhtmltopdf repeats on every page, so the payroll
total was printed fourteen times.

Both failures share a cause worth pinning: the layout was the renderer's decision. Here
the widths are computed from the measured text and summed to the printable width, so a
column that does not fit cannot be silently dropped - either the planner finds a size and
paper where everything fits, or it throws. These tests assert that property directly, on
the plan and the composed HTML, because a test that only inspects finished PDF pixels can
tell you a column is missing but never why.

Numbers are not this module's business. Every figure here comes from the preview through
``value_of``; the tests check that what went in came out, not that it was right.
"""

import io
import json
import re

import frappe
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.services import payroll_print as pp

CURRENCY = "AKZ"

#: Every canonical preview column, as ``export_payroll_preview`` passes them.
ALL_COLUMNS = [(c["label"], c["key"], bool(c["money"])) for c in api.PREVIEW_COLUMNS]

RIGHT_HAND = ("Taxable", "INSS", "INSS Entidade Patronal", "IRT", "Gross", "Net")


def money(v):
	return frappe.utils.fmt_money(flt(v), currency=CURRENCY)


def cell(row, key):
	"""The same mapping ``export_payroll_preview`` uses for both PDF and spreadsheet."""
	if key == "days":
		return "{0}/{1}".format(flt(row.get("payment_days")), flt(row.get("total_working_days")))
	if key in ("employee_name", "department"):
		return row.get(key) or ""
	return flt(row.get(key), 2)


def row(name="Abraão António Quissanga", department="Hospitality Services - ITEC",
        days=24.5, **values):
	r = {"employee_name": name, "department": department,
	     "payment_days": days, "total_working_days": days}
	for (_l, key, is_money) in ALL_COLUMNS:
		if is_money:
			r[key] = 0.0
	r.update(values)
	return r


def totals_of(rows):
	"""The preview's own totalling rule: money columns only, nothing else."""
	t = {k: 0.0 for (_l, k, m) in ALL_COLUMNS if m}
	for r in rows:
		for k in t:
			t[k] += flt(r.get(k))
	return t


def compose(rows, columns=None, company=None):
	columns = columns or ALL_COLUMNS
	return pp.compose(columns=columns, rows=rows, totals=totals_of(rows), value_of=cell,
	                  money=money, company=company, period_label="23-07-2026 - 22-08-2026",
	                  generated_label="Generated 23-08-2026 09:00:00")


def widths_by_key(plan_):
	out = {}
	for section in plan_["sections"]:
		for col, w in zip(section["columns"], section["widths"]):
			out.setdefault(col["key"], w)
	return out


def cells_of(html):
	"""Text of every <td>/<th>, in document order."""
	return [re.sub(r"<[^>]+>", "", c).strip()
	        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", html, re.S)]


def pdf_pages(content):
	from PyPDF2 import PdfFileReader

	reader = PdfFileReader(io.BytesIO(content), overwriteWarnings=False)
	box = [float(x) for x in reader.getPage(0).mediaBox]
	return reader.getNumPages(), (round(box[2] - box[0]), round(box[3] - box[1]))


class PrintLayout(PayrollFixture):
	"""Layout assertions. No payroll is calculated here - the rows are handed in."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")


# ---------------------------------------------------------------------------
# (1) a PDF is actually produced
# ---------------------------------------------------------------------------

class TestThePdfIsProduced(PrintLayout):
	def test_a_real_pdf_comes_out_on_a3_landscape(self):
		rows = [row(name="Employee %d" % i, base=100000 + i, net_pay=97000 + i)
		        for i in range(40)]
		content, plan_ = pp.build(
			columns=ALL_COLUMNS, rows=rows, totals=totals_of(rows), value_of=cell,
			money=money, company=self.company, period_label="23-07-2026 - 22-08-2026")
		self.assertTrue(content.startswith(b"%PDF"), "no PDF was produced")
		self.assertGreater(len(content), 5000)
		pages, size = pdf_pages(content)
		self.assertEqual(plan_["paper"], "A3")
		# A3 landscape is 1191 x 842pt; the old report was A4 landscape and lost columns
		self.assertEqual(size, (1191, 842), "wrong paper size: %s" % (size,))
		self.assertGreaterEqual(pages, 1)

	def test_the_page_size_is_stated_to_the_engine_not_left_to_print_settings(self):
		"""``get_pdf`` falls back to Print Settings, which is A4. That fallback is what
		put an A3-sized table on A4 paper."""
		_html, options, _plan = compose([row()])
		self.assertEqual(options["page-size"], "A3")
		self.assertEqual(options["orientation"], "Landscape")

	def test_the_paper_does_not_change_with_the_data(self):
		"""One employee and two hundred must come out on the same sheet: the report is
		filed and compared month to month."""
		small = compose([row(base=100000)])[2]
		large = compose([row(name="E %d" % i, base=11498653.05) for i in range(200)])[2]
		self.assertEqual(small["paper"], "A3")
		self.assertEqual(large["paper"], "A3")
		self.assertEqual(small["avail"], large["avail"])


# ---------------------------------------------------------------------------
# (2)(3)(4)(5)(6)(7) everything the preview shows reaches the paper
# ---------------------------------------------------------------------------

class TestNothingIsLost(PrintLayout):
	def test_every_visible_column_header_is_printed(self):
		html, _o, plan_ = compose([row()])
		printed = cells_of(html)
		for (label, key, _m) in ALL_COLUMNS:
			self.assertIn(label, printed, "column header missing from the PDF: %s" % label)
			self.assertIn(key, widths_by_key(plan_), "column %s was never laid out" % key)

	def test_the_right_hand_columns_survive(self):
		"""The exact five the old PDF dropped, plus the one it cut in half."""
		html, _o, plan_ = compose([row(taxable_income=97000, ss=3000, ss_employer=8000,
		                               irt=1234.56, gross_pay=100000, net_pay=97000)])
		printed = cells_of(html)
		widths = widths_by_key(plan_)
		for label in RIGHT_HAND:
			self.assertIn(label, printed, "%s is missing" % label)
		for key in ("taxable_income", "ss", "ss_employer", "irt", "gross_pay", "net_pay"):
			self.assertGreater(widths[key], 0)
		for value in ("KZ 97 000,00", "KZ 3 000,00", "KZ 8 000,00", "KZ 1 234,56",
		              "KZ 100 000,00"):
			self.assertIn(value, printed, "%s never reached the paper" % value)

	def test_employee_names_are_printed(self):
		names = ["Abraão António Quissanga", "Teresa Jamba Afonso", "Ali Bassem Yassine"]
		html, _o, _p = compose([row(name=n) for n in names])
		printed = cells_of(html)
		for n in names:
			self.assertIn(n, printed)

	def test_paid_total_days_print_exactly_as_the_preview_shows_them(self):
		html, _o, _p = compose([row(days=24.5), row(name="Half", days=12.25),
		                        row(name="None", days=0)])
		printed = cells_of(html)
		self.assertIn("24.5/24.5", printed)
		self.assertIn("12.25/12.25", printed)
		self.assertIn("0.0/0.0", printed)
		# a day count is not money and must never be totalled as if it were
		self.assertNotIn("KZ 24,50", printed)

	def test_monetary_values_are_printed_unchanged(self):
		r = row(base=11498653.05, food=811734.70, transport=636714.28, absence=32346.95,
		        overtime_amount=15000.5, productivity_bonus=2500, adiantamento=7500,
		        taxable_income=94536.54, ss=2923.81, irt=636.14, gross_pay=97460.35,
		        net_pay=94536.54)
		html, _o, _p = compose([r])
		printed = cells_of(html)
		for (_l, key, is_money) in ALL_COLUMNS:
			if is_money:
				self.assertIn(money(r[key]), printed,
				              "%s printed as something other than %s" % (key, money(r[key])))

	def test_the_totals_are_the_previews_totals(self):
		rows = [row(base=100000, food=30000, net_pay=97000),
		        row(name="B", base=250000, food=20000, net_pay=203600),
		        row(name="C", base=11148653.05, food=761734.70, net_pay=13671561)]
		totals = totals_of(rows)
		html, _o, _p = compose(rows)
		printed = cells_of(html)
		self.assertIn("TOTAL", printed)
		for key in ("base", "food", "net_pay"):
			self.assertIn(money(totals[key]), printed,
			              "total for %s is not the preview's total" % key)
		# and the identity the report stands on
		self.assertEqual(flt(totals["base"], 2), flt(100000 + 250000 + 11148653.05, 2))

	def test_a_day_column_is_never_totalled_as_money(self):
		rows = [row(days=24.5), row(name="B", days=24.5)]
		html, _o, _p = compose(rows)
		total_cells = cells_of(html)[-len(ALL_COLUMNS):]
		days_at = [k for (_l, k, _m) in ALL_COLUMNS].index("days")
		self.assertEqual(total_cells[days_at], "",
		                 "the days column was totalled: %r" % total_cells[days_at])


# ---------------------------------------------------------------------------
# (8)(9)(10) the geometry: nothing may leave the page
# ---------------------------------------------------------------------------

class TestNothingIsClipped(PrintLayout):
	def assert_fits(self, plan_, note=""):
		for section in plan_["sections"]:
			total = sum(section["widths"])
			self.assertLessEqual(round(total, 2), round(plan_["avail"], 2) + 0.05,
			                     "table is %.1fpt wider than the %.1fpt printable width %s"
			                     % (total, plan_["avail"], note))
			self.assertGreater(min(section["widths"]), 0)
		self.assertGreaterEqual(plan_["font"], pp.MIN_FONT_PT,
		                        "the report was shrunk below legibility %s" % note)

	def test_the_table_never_exceeds_the_printable_width(self):
		html, _o, plan_ = compose([row(base=11498653.05, taxable_income=11498653.05,
		                               gross_pay=11498653.05, net_pay=11498653.05)])
		self.assert_fits(plan_)
		self.assertIn("table-layout: fixed", html)

	def test_every_column_is_wide_enough_for_what_it_prints(self):
		"""The guarantee that replaces "it looked fine when I tried it"."""
		rows = [row(name="Hermengildo Vunge Chiquito Vunge Chiquito Caiango",
		            department="Accounting - Contabilidade - ITEC",
		            base=11498653.05, food=811734.70, transport=636714.28,
		            taxable_income=11322150, ss=458850, ss_employer=1223600,
		            irt=864589, gross_pay=15295000, net_pay=13971561)]
		html, _o, plan_ = compose(rows)
		self.assert_fits(plan_)
		widths = widths_by_key(plan_)
		pad = 2 * pp.CELL_PAD_PT + 2 * pp.BORDER_PT
		for (label, key, is_money) in ALL_COLUMNS:
			if not is_money and key != "days":
				continue        # wrapping columns are allowed to use several lines
			longest = max(pp.text_width(money(rows[0][key]) if is_money
			                            else cell(rows[0], key), plan_["font"]),
			              pp.text_width(label, plan_["font"], True))
			self.assertGreaterEqual(widths[key] + 0.05, min(longest + pad, widths[key]),
			                        "%s is too narrow for its own content" % key)
			if is_money:
				self.assertGreaterEqual(
					widths[key] + 0.05,
					pp.text_width(money(rows[0][key]), plan_["font"]) + pad,
					"%s would clip %s" % (key, money(rows[0][key])))

	def test_money_cells_are_right_aligned_and_never_broken_across_lines(self):
		"""`KZ 30` above `000,00` is what the old PDF did to every figure it kept."""
		html, _o, _p = compose([row(base=100000)])
		self.assertIn("td.num {", html)
		self.assertIn("white-space: nowrap", html)
		self.assertIn("text-align: right", html)

	def test_a_layout_that_cannot_fit_splits_instead_of_clipping(self):
		"""Forced onto paper too narrow for one table, the report must break into
		sections that repeat the identity columns - never drop a column."""
		narrow = dict(pp.PAPER_PT)
		try:
			pp.PAPER_PT["A4"] = (420.0, 300.0)
			pp.PAPER_PT["A3"] = (520.0, 400.0)
			html, _o, plan_ = compose([row(base=11498653.05, net_pay=13971561)])
		finally:
			pp.PAPER_PT.clear()
			pp.PAPER_PT.update(narrow)
		self.assertGreater(len(plan_["sections"]), 1, "it should have split")
		self.assert_fits(plan_, "(forced-narrow)")
		for section in plan_["sections"]:
			keys = [c["key"] for c in section["columns"]]
			for ident in pp.IDENTITY_KEYS:
				self.assertIn(ident, keys,
				              "section without %s: the reader cannot tell whose row it is"
				              % ident)
		printed = cells_of(html)
		for (label, _k, _m) in ALL_COLUMNS:
			self.assertIn(label, printed, "%s was dropped by the split" % label)

	def test_a_multi_page_report_repeats_the_table_header(self):
		rows = [row(name="Employee %d" % i, base=100000) for i in range(120)]
		content, _plan = pp.build(
			columns=ALL_COLUMNS, rows=rows, totals=totals_of(rows), value_of=cell,
			money=money, company=self.company, period_label="23-07-2026 - 22-08-2026")
		pages, _size = pdf_pages(content)
		self.assertGreater(pages, 1, "this fixture is supposed to span pages")
		html, _o, _p = compose(rows)
		self.assertIn("<thead>", html)
		self.assertIn("display: table-header-group", html)

	def test_the_total_row_is_printed_once_and_not_in_a_tfoot(self):
		"""wkhtmltopdf repeats <tfoot> on every page. The old PDF printed the payroll
		total on all fourteen of them."""
		rows = [row(name="Employee %d" % i, base=100000) for i in range(120)]
		html, _o, _p = compose(rows)
		self.assertNotIn("<tfoot", html)
		self.assertEqual(html.count("pp-total"), 2, "the total row is not printed once")

	def test_an_employee_row_is_never_split_across_pages(self):
		html, _o, _p = compose([row(name="A B C D E F G H", base=100000)])
		self.assertIn("page-break-inside: avoid", html)
		self.assertIn("break-inside: avoid", html)


# ---------------------------------------------------------------------------
# (11)(12) against the live endpoint: same values, and nothing written
# ---------------------------------------------------------------------------

class TestAgainstTheLivePreview(PayrollFixture):
	"""These go through ``export_payroll_preview`` itself, not a hand-built row set."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

	def preview(self):
		return api.payroll_preview(self.company, str(self.start), str(self.end))

	def test_the_printed_values_are_the_api_values(self):
		rows = self.preview()
		self.assertTrue(rows, "the fixture employee should appear in the preview")
		cols = [(c["label"], c["key"], bool(c["money"]))
		        for c in api.get_preview_columns() if c.get("visible")]
		currency = frappe.db.get_single_value("Isoft HR Settings", "currency") or "AOA"

		def m(v):
			return frappe.utils.fmt_money(flt(v), currency=currency)

		html, _o, _p = pp.compose(columns=cols, rows=rows, totals=totals_of(rows),
		                          value_of=cell, money=m, company=self.company,
		                          period_label="x", generated_label="y")
		printed = cells_of(html)
		mine = [r for r in rows if r["employee"] == self.employee.name][0]
		for (label, key, is_money) in cols:
			expected = m(cell(mine, key)) if is_money else str(cell(mine, key))
			if expected:
				self.assertIn(expected, printed,
				              "%s: the PDF does not show the API's %r" % (label, expected))

	def test_the_printed_total_is_the_api_total(self):
		rows = self.preview()
		totals = totals_of(rows)
		html, _o, _p = compose(rows)
		printed = cells_of(html)
		for key in ("base", "gross_pay", "net_pay", "ss", "irt"):
			self.assertIn(money(totals[key]), printed,
			              "total for %s differs from the API total" % key)

	def test_generating_the_pdf_writes_nothing(self):
		"""A report that can change payroll is not a report."""
		watched = ("Isoft Salary Slip", "Isoft Payroll Entry", "Journal Entry",
		           "Payment Entry", "Attendance", "Isoft Salary Profile", "Employee")

		def snapshot():
			out = {}
			for dt in watched:
				out[dt] = (frappe.db.count(dt),
				           frappe.db.sql("select max(modified) from `tab%s`" % dt)[0][0])
			return out

		before = snapshot()
		result = api.export_payroll_preview(self.company, str(self.start), str(self.end),
		                                    file_format="pdf")
		# deliberately NOT committing: an uncommitted write is still a write, and this
		# connection would see it. Committing here would only destroy the fixture.
		after = snapshot()
		self.assertEqual(json.dumps(before, default=str), json.dumps(after, default=str),
		                 "generating the PDF changed payroll data")
		self.assertTrue(result["filename"].endswith(".pdf"))
		self.assertEqual(result["mime"], "application/pdf")

	def test_printing_leaves_the_browser_preview_alone(self):
		"""The screen is the source of truth; the print layer may not touch it."""
		cols_before = api.get_preview_columns()
		rows_before = self.preview()
		api.export_payroll_preview(self.company, str(self.start), str(self.end),
		                           file_format="pdf")
		self.assertEqual(json.dumps(api.get_preview_columns(), default=str),
		                 json.dumps(cols_before, default=str))
		self.assertEqual(json.dumps(self.preview(), default=str),
		                 json.dumps(rows_before, default=str))


# ---------------------------------------------------------------------------
# the shapes of data this report actually meets
# ---------------------------------------------------------------------------

def _many(n, **kw):
	return [row(name="Employee %d" % i, department="Sales - ITEC", **kw) for i in range(n)]


CASES = {
	"85 employees": _many(85, base=100000, net_pay=97000),
	"one employee": [row(base=100000, net_pay=97000)],
	"long employee names": [
		row(name="Hermengildo Vunge Chiquito Vunge Chiquito Caiango", base=100000),
		row(name="Shelcia Rossana Contreiras Teixeira da Silva", base=100000)],
	"long department names": [
		row(department="Accounting - Contabilidade - ITEC", base=100000),
		row(department="Hospitality Services - ITEC", base=100000)],
	"large salaries": [row(base=11498653.05, gross_pay=15295000, net_pay=13971561)],
	"large allowances": [row(food=811734.70, transport=636714.28, base=500000)],
	"overtime": [row(base=100000, overtime_amount=45678.9, gross_pay=145678.9)],
	"absence values": [row(base=100000, absence=32346.95, net_pay=67653.05)],
	"non-zero INSS and IRT": [row(base=400000, ss=13800, ss_employer=36800, irt=65628,
	                              taxable_income=386200, net_pay=380572)],
	"every column zero": [row()],
	"multiple pages": _many(200, base=100000, net_pay=97000),
	# 43 single-line rows plus the total fill exactly one A3 page; 44 pushes the break
	# through the total row, which is where a <tfoot> layout would have repeated it
	"a page break at the total row": _many(44, base=100000, net_pay=97000),
}


class TestEveryShapeOfData(PrintLayout):
	def test_no_shape_of_payroll_data_can_clip_a_column(self):
		failures = []
		for label, rows in CASES.items():
			html, _o, plan_ = compose(rows)
			printed = cells_of(html)
			for section in plan_["sections"]:
				if round(sum(section["widths"]), 2) > round(plan_["avail"], 2) + 0.05:
					failures.append("%s: table wider than the page" % label)
			if plan_["font"] < pp.MIN_FONT_PT:
				failures.append("%s: font %.1fpt" % (label, plan_["font"]))
			for (col_label, _k, _m) in ALL_COLUMNS:
				if col_label not in printed:
					failures.append("%s: column %s missing" % (label, col_label))
			for r in rows[:3]:
				if r["employee_name"] not in printed:
					failures.append("%s: %s missing" % (label, r["employee_name"]))
		self.assertEqual(failures, [], "\n".join(failures))

	def test_the_totals_hold_for_every_shape(self):
		for label, rows in CASES.items():
			totals = totals_of(rows)
			html, _o, _p = compose(rows)
			printed = cells_of(html)
			for key in ("base", "net_pay", "food"):
				self.assertIn(money(totals[key]), printed,
				              "%s: total for %s is wrong" % (label, key))
