# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The payslip PDF with both copies on one sheet.

HR prints the receipt (ORIGINAL + DUPLICADO, one A4 page each) with the browser's
"Pages per sheet: 2", which puts the two pages side by side on one landscape sheet. A web
page cannot preset that option of the print dialog, and Chrome does not remember it between
prints, so this endpoint produces the same sheet itself.

The print format is untouched: the PDF is Frappe's own rendering of it (frappe.get_print,
which applies the same print permission as the Print view), and each pair of pages is only
scaled and placed side by side.
"""

import io

import frappe

DOCTYPE = "Isoft Salary Slip"
DEFAULT_FORMAT = "Recibo de Vencimento"


@frappe.whitelist()
def download_two_per_sheet(name, format=None):
	"""Open the payslip as a PDF, two pages per landscape sheet."""
	pdf = frappe.get_print(DOCTYPE, name, format or DEFAULT_FORMAT, as_pdf=True)
	frappe.local.response.filename = "{0}.pdf".format(name.replace(" ", "-").replace("/", "-"))
	frappe.local.response.filecontent = two_per_sheet(pdf)
	frappe.local.response.type = "pdf"


def two_per_sheet(pdf):
	"""Each pair of pages side by side on one sheet of the same paper turned landscape,
	scaled to fit and centred, as a print dialog's "2 pages per sheet" does."""
	from PyPDF2 import PdfFileReader, PdfFileWriter
	from PyPDF2.pdf import PageObject

	reader = PdfFileReader(io.BytesIO(pdf), strict=False)
	pages = [reader.getPage(i) for i in range(reader.getNumPages())]
	writer = PdfFileWriter()
	for i in range(0, len(pages), 2):
		pair = pages[i:i + 2]
		width = float(pair[0].mediaBox.getWidth())
		height = float(pair[0].mediaBox.getHeight())
		sheet = PageObject.createBlankPage(None, height, width)
		half = height / 2.0
		for slot, page in enumerate(pair):
			w, h = float(page.mediaBox.getWidth()), float(page.mediaBox.getHeight())
			scale = min(half / w, width / h)
			sheet.mergeScaledTranslatedPage(page, scale, slot * half + (half - w * scale) / 2.0,
			                                (width - h * scale) / 2.0)
		writer.addPage(sheet)
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()
