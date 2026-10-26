# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Presentation-layer helper for the payslip print formats.

Serves both "Recibo de Vencimento" (the bilingual A4 receipt every entry point links to —
ESS, the HR dashboard, the Desk print view) and the older "Recibo de Vencimentos - Angola"
format. Every key the older format reads is still produced unchanged; the newer receipt
reads the additional ``employee_id`` / ``days`` / ``payment`` / ``summary`` / ``copies``
keys and the translated line ``label``.

This module reads ONLY. It never computes payroll: every monetary figure it returns is
either copied verbatim from the submitted Isoft Salary Slip or is a sum of the slip's own
child rows. The IRT, Segurança Social, gross and net values are taken from the fields the
payroll engine already wrote (``irt_amount``, ``ss_employee_amount``, ``gross_pay``,
``total_deduction``, ``net_pay``); nothing here re-derives them.

The one place where a number is produced rather than copied is :func:`_faltas`, which
counts Attendance / Isoft Attendance Occurrence records for display in the "Faltas" box.
Those are counts of days and hours, not money, and they do not feed any total on the
receipt.

Kept out of the Jinja template on purpose: the print format sandbox can only call
whitelisted helpers, and multi-query logic in a template is unreadable and slow.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, get_datetime, get_last_day, getdate

# Component codes defined by the payroll engine (payroll/engine.py::COMPONENTS).
# Mapped to the numeric codes Angolan payroll software conventionally prints. A code is
# only printed when it is known here; otherwise the row falls back to its stored `abbr`
# so a site that adds components still prints something meaningful rather than blank.
COMPONENT_CODES = {
	# Remunerações
	"SB": "R101",  # Salário Base
	"SDA": "R201",  # Subsídio de Alimentação
	"SDT": "R204",  # Subsídio de Transporte
	"AF": "R210",  # Abono de Família
	"SFE": "R301",  # Subsídio de Férias
	"SNA": "R302",  # Subsídio de Natal
	"PPD": "R401",  # Prémio de Produtividade
	"HEX": "R501",  # Horas Extras
	# Informative absence rows (engine FAL / ATR): what came off the basic salary.
	"FAL": "F03",  # Faltas
	"ATR": "F04",  # Atrasos
	# Descontos. D01 and D40 are taken from the PRIMAVERA receipt this format replaces;
	# the earlier D30/D40/D60 guesses printed the wrong code against every deduction.
	"CTSS3": "D01",  # Segurança Social 3% (trabalhador)
	"ADT": "D40",  # Regularização - Adiantamento
	# IRT deliberately has no entry: the reference receipt carries no IRT line, so its
	# printed code is unconfirmed. Until it is, the row falls back to the abbreviation
	# "IRT", which is legible, rather than to an invented number.
}

#: Translation context for every label on the receipt (``_("Payslip", context=...)``).
#:
#: Plain msgids are resolved from every installed app's catalogue in ``sites/apps.txt``
#: order, and on this bench ERPNext is listed AFTER this app, so its Brazilian
#: "Folha de Pagamento" / "Funcionário" / "Rem. Líquida" silently replaced the Angolan
#: wording this app ships for the same English words. A context makes the key
#: ``"Payslip:Recibo de Vencimento"``, which no other app defines, so the document's own
#: wording is the only candidate. The rows live in translations/pt.csv (third column).
TRANSLATION_CONTEXT = "Recibo de Vencimento"

#: English msgid printed as the line description of each engine component. The
#: Portuguese translation of every entry is exactly the component name the engine stores
#: on the slip (payroll/engine.py::COMPONENTS), so a Portuguese receipt prints the slip's
#: own wording and an English receipt prints a real English label rather than a stored
#: Portuguese string passed through ``_()``, which has no English catalogue to hit.
COMPONENT_LABELS = {
	"SB": "Basic Salary",
	"SDA": "Food Allowance",
	"SDT": "Transport Allowance",
	"AF": "Family Allowance",
	"SFE": "Holiday Allowance",
	"SNA": "Christmas Allowance",
	"PPD": "Productivity Bonus",
	"HEX": "Overtime",
	"TI": "Taxable Income",
	"FAL": "Absences",
	"ATR": "Late Arrivals",
	"CTSS3": "Employee INSS",
	"IRT": "IRT",
	"ADT": "Salary Advance",
}

#: The two copies printed back to back, in order. ``label`` is a msgid, translated when
#: the template renders it — never here, and never at import.
COPIES = (
	{"key": "original", "label": "ORIGINAL"},
	{"key": "duplicate", "label": "DUPLICATE - COMPANY FILE"},
)

#: Largest logo file embedded in the receipt. A logo is inlined as a data URI so that the
#: PDF engine never has to fetch it over HTTP (which fails silently, or throws, when the
#: site host is not reachable from the worker). Anything bigger is simply not printed.
MAX_LOGO_BYTES = 400 * 1024

#: Deduction code carrying IRT, used to locate the IRT row for the "Retenção IRT" box.
IRT_ABBR = "IRT"
#: Deduction code carrying the employee social-security contribution.
INSS_ABBR = "CTSS3"
#: Statistical earning row that must never be printed as a remuneration line.
TAXABLE_INCOME_ABBR = "TI"


def _code_for(abbr):
	"""Printed "Código" for a component. Falls back to the stored abbreviation."""
	abbr = (abbr or "").strip()
	return COMPONENT_CODES.get(abbr) or abbr


def _company_info(company):
	"""Company header block, entirely from the Company master + linked Address.

	Nothing is hardcoded: a site with no NIF or no Address simply gets fewer lines.
	"""
	if not company:
		return {}

	fields = ["name", "company_name", "tax_id", "phone_no", "email", "website", "country"]
	meta = frappe.get_meta("Company")
	# hq_address is a custom field on some Angolan installs; only read it if present.
	if meta.has_field("hq_address"):
		fields.append("hq_address")

	row = frappe.db.get_value("Company", company, fields, as_dict=True) or frappe._dict()

	address_lines = []
	# A linked Address record is the canonical source and wins over the flat field.
	link = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Company", "link_name": company, "parenttype": "Address"},
		fields=["parent"],
		order_by="creation asc",
		limit=1,
	)
	if link:
		addr = frappe.db.get_value(
			"Address",
			link[0].parent,
			["address_line1", "address_line2", "city", "state", "pincode", "country", "phone"],
			as_dict=True,
		) or frappe._dict()
		for key in ("address_line1", "address_line2"):
			if addr.get(key):
				address_lines.append(addr.get(key))
		locality = " ".join(x for x in (addr.get("pincode"), addr.get("city")) if x)
		if locality:
			address_lines.append(locality)
		for key in ("state", "country"):
			if addr.get(key):
				address_lines.append(addr.get(key))
		if addr.get("phone") and not row.get("phone_no"):
			row.phone_no = addr.get("phone")
	elif row.get("hq_address"):
		address_lines.append(row.get("hq_address"))
	elif row.get("country"):
		address_lines.append(row.get("country"))

	return {
		"name": row.get("company_name") or row.get("name") or company,
		"nif": row.get("tax_id"),
		"address_lines": address_lines,
		"phone": row.get("phone_no"),
		"email": row.get("email"),
		"website": row.get("website"),
	}


def _employee_info(employee):
	"""Employee identification block. Custom fields are read defensively: a site that has
	not yet run the install hook will not have them, and the receipt must still print."""
	if not employee:
		return {}

	meta = frappe.get_meta("Employee")
	wanted = [
		"employee_name", "employee_number", "designation", "department",
		"branch", "date_of_joining", "holiday_list", "employment_type", "grade",
		"salary_mode", "bank_name", "bank_ac_no",
	]
	optional = [
		"custom_nif", "custom_inss_number", "custom_insurance", "custom_iban",
		"custom_dependents", "custom_irt_exempt",
	]
	# `name` is a table column, not a DocField, so meta.has_field("name") is False. It has
	# to bypass the filter or the "N.º Mec." fallback below silently never fires and the
	# box prints blank for every employee without an employee_number.
	fields = ["name"] + [f for f in wanted + optional if meta.has_field(f)]
	row = frappe.db.get_value("Employee", employee, fields, as_dict=True) or frappe._dict()

	department = row.get("department")
	if department:
		# Departments are named "Logística - ITEC"; the company abbr suffix is noise here.
		department = department.rsplit(" - ", 1)[0]

	return {
		"nome": (row.get("employee_name") or "").strip(),
		# "N.º Mec." is the mechanographic/payroll number. employee_number is the
		# user-entered one; the Employee ID is the fallback so the box is never blank.
		"numero_mecanografico": row.get("employee_number") or row.get("name"),
		"employee_id": row.get("name"),
		"categoria": row.get("designation"),
		# Category (grade) and designation are separate lines on the receipt. Most
		# employees carry no Employee Grade, so the category falls back to the
		# designation rather than printing an empty box on a legal document.
		"category": row.get("grade") or row.get("designation"),
		"designation": row.get("designation"),
		"employment_type": row.get("employment_type"),
		"departamento": department,
		"salary_mode": (row.get("salary_mode") or "").strip(),
		"bank_name": row.get("bank_name"),
		"bank_account": row.get("custom_iban") or row.get("bank_ac_no"),
		"n_beneficiario": row.get("custom_inss_number"),
		"n_contribuinte": row.get("custom_nif"),
		"seguro": row.get("custom_insurance"),
		"iban": row.get("custom_iban"),
		"data_admissao": row.get("date_of_joining"),
		"dependentes": row.get("custom_dependents"),
		"isento_irt": cint(row.get("custom_irt_exempt")),
	}


def _periodo(doc):
	"""Human period label, e.g. "Junho/Julho 2026" collapsing to "Julho 2026" when the
	period sits inside a single month."""
	start, end = getdate(doc.start_date), getdate(doc.end_date)
	months = [
		"Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
		"Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
	]
	if start.month == end.month and start.year == end.year:
		# The receipt this replaces prints "Junho" — the Data Fecho line directly below
		# carries the year, so repeating it here is noise.
		return months[end.month - 1]
	if start.year == end.year:
		return "{0}/{1} {2}".format(months[start.month - 1], months[end.month - 1], end.year)
	return "{0} {1}/{2} {3}".format(months[start.month - 1], start.year, months[end.month - 1], end.year)


def _faltas(doc):
	"""Absence counters for the "Faltas" box.

	Columns follow the Angolan receipt convention:
	  * Alim.  — meal-subsidy days lost, i.e. unpaid days (total working - paid).
	  * Turno  — shift/partial occurrences (lateness, early exit, partial absence).
	  * CDH/CDD — *com* direito a remuneração, in Horas / Dias (justified absences).
	  * SDH/SDD — *sem* direito a remuneração, in Horas / Dias (unjustified absences).

	Every figure is a count of attendance records, never a monetary value.
	"""
	zero = {"alim": 0.0, "turno": 0.0, "cdh": 0.0, "cdd": 0.0, "sdh": 0.0, "sdd": 0.0, "has_data": False}
	if not doc.get("employee"):
		return zero

	start, end = getdate(doc.start_date), getdate(doc.end_date)

	# Unpaid days drive the meal-subsidy loss. Taken from the slip's own day counts so it
	# always agrees with the proration the engine applied.
	unpaid = flt(doc.get("total_working_days")) - flt(doc.get("payment_days"))
	out = dict(zero)
	out["alim"] = max(0.0, flt(unpaid, 2))

	occurrences = frappe.get_all(
		"Isoft Attendance Occurrence",
		filters={"employee": doc.employee, "occurrence_date": ["between", [start, end]]},
		fields=["occurrence_type", "hours", "status"],
	)
	for occ in occurrences:
		hours = flt(occ.get("hours"))
		partial = occ.get("occurrence_type") in ("Lateness", "Early Exit", "Partial Absence")
		if partial:
			out["turno"] += 1
		if occ.get("status") == "Unjustified":
			if partial:
				out["sdh"] += hours
			elif occ.get("occurrence_type") == "Half Day":
				out["sdd"] += 0.5
			else:
				out["sdd"] += 1
		else:
			# Pending Justification and Justified alike are not deducted from pay, so they
			# belong in the "com direito" columns.
			if partial:
				out["cdh"] += hours
			elif occ.get("occurrence_type") == "Half Day":
				out["cdd"] += 0.5
			else:
				out["cdd"] += 1

	# Absences recorded straight on Attendance without an Occurrence record.
	absent = frappe.db.sql(
		"""select status, count(*) as n from `tabAttendance`
		   where employee=%s and docstatus=1 and attendance_date between %s and %s
		     and status in ('Absent', 'Half Day')
		   group by status""",
		(doc.employee, start, end),
		as_dict=True,
	)
	attendance_days = sum((0.5 if r.status == "Half Day" else 1) * cint(r.n) for r in absent)
	# Occurrences are authoritative for the days they cover (see engine
	# occurrence_missing_by_date); only report the excess so days are not double-counted.
	extra = attendance_days - (out["sdd"] + out["cdd"])
	if extra > 0:
		out["sdd"] += extra

	for key in ("alim", "turno", "cdh", "cdd", "sdh", "sdd"):
		out[key] = flt(out[key], 2)
	out["has_data"] = any(out[k] for k in ("alim", "turno", "cdh", "cdd", "sdh", "sdd"))
	return out


def _overtime(doc):
	"""Overtime hours backing the Horas Extras line.

	The hours are the slip's own ``overtime_hours``, the figure the amount was calculated
	from (services/overtime.py). The dated rows are the submitted Attendance records
	carrying overtime in the period, listed for the record. The amount is the slip's
	stored ``overtime_amount``: NOT recomputed here.
	"""
	amount = flt(doc.get("overtime_amount"))
	enabled = cint(frappe.db.get_single_value("Isoft HR Settings", "enable_overtime"))
	if not enabled or not doc.get("employee"):
		return {"show": False, "rows": [], "hours": 0.0, "amount": 0.0}

	rows = frappe.db.sql(
		"""select attendance_date, custom_overtime_hours as hours
		   from `tabAttendance`
		   where employee=%s and docstatus=1
		     and ifnull(custom_overtime_hours, 0) > 0
		     and attendance_date between %s and %s
		   order by attendance_date""",
		(doc.employee, getdate(doc.start_date), getdate(doc.end_date)),
		as_dict=True,
	)
	# A slip from before overtime was entered as hours has none: fall back to the dated
	# Attendance rows, which is what those slips were printed with.
	hours = flt(doc.get("overtime_hours"), 2) or flt(sum(flt(r.hours) for r in rows), 2)

	# Nothing approved and nothing paid: suppress the whole block rather than print an
	# empty table.
	if not rows and not amount and not hours:
		return {"show": False, "rows": [], "hours": 0.0, "amount": 0.0}

	return {
		"show": True,
		"rows": [{"date": r.attendance_date, "hours": flt(r.hours, 2)} for r in rows],
		"hours": hours,
		"amount": amount,
		"code": _code_for("HEX"),
		"description": "Horas Extras",
	}


def _formas_pagamento(doc):
	"""Payment breakdown for "Formas de Pagamento".

	The slip stores a single net figure and the payment is settled by one Journal Entry,
	so there is exactly one payment line. The method is inferred from data that exists:
	the employee's IBAN / salary mode. When neither is recorded the method is left blank
	rather than guessed.
	"""
	net = flt(doc.get("net_pay"))
	if not net:
		return []

	mode = None
	iban = None
	if doc.get("employee"):
		meta = frappe.get_meta("Employee")
		fields = [f for f in ("salary_mode", "custom_iban", "bank_name") if meta.has_field(f)]
		if fields:
			row = frappe.db.get_value("Employee", doc.employee, fields, as_dict=True) or frappe._dict()
			iban = row.get("custom_iban")
			salary_mode = (row.get("salary_mode") or "").strip()
			# NOT translated, deliberately. The Recibo de Vencimentos is a legal Angolan
			# document and its template is Portuguese throughout; running these three
			# labels through _() meant an HR user whose interface language is English
			# printed "Bank Transfer" in the middle of an otherwise Portuguese payslip.
			# The payment mode is part of the document, not part of the interface.
			if salary_mode == "Bank":
				mode = "Transferência Bancária"
			elif salary_mode == "Cash":
				mode = "Numerário"
			elif salary_mode == "Cheque":
				mode = "Cheque"
			elif iban:
				mode = "Transferência Bancária"

	return [{
		"percent": 100.0,
		"amount": net,
		"mode": mode,
		"reference": iban,
		"currency": doc.get("currency") or frappe.db.get_value("Company", doc.get("company"), "default_currency"),
	}]


def _lines(doc):
	"""Main table rows: earnings, deductions and memo lines, kept strictly apart.

	A component flagged ``do_not_include_in_total`` is statistical: it is printed for the
	record but must not move either total. That is how the receipt this replaces shows
	its absence lines (F03 "Falta Injustificada", F40 "Subsídio de Alimentação"), whose
	values are already reflected in the prorated Vencimento above them.

	"Rendimento Tributável" (TI) is the one statistical row that is dropped entirely: it
	is an IRT base rather than a payroll line, and it is already printed as
	"Total Remun." in the Retenção IRT box.
	"""
	earnings, deductions, memos = [], [], []

	for side, rows in (("earning", doc.get("earnings")), ("deduction", doc.get("deductions"))):
		for row in rows or []:
			amount = flt(row.get("amount"))
			if not amount:
				continue
			abbr = (row.get("abbr") or "").strip()
			line = {
				"code": _code_for(abbr),
				"description": row.get("salary_component"),
				"label": _line_label(abbr, row.get("salary_component")),
				"amount": amount,
			}
			if cint(row.get("do_not_include_in_total")):
				if abbr == TAXABLE_INCOME_ABBR:
					continue
				line["side"] = side
				memos.append(line)
			elif side == "earning":
				earnings.append(line)
			else:
				deductions.append(line)

	return earnings, deductions, memos


def _component_amount(rows, abbr):
	"""Sum of a component's rows on the slip, located by its engine abbreviation."""
	target = (abbr or "").strip()
	return flt(sum(flt(r.get("amount")) for r in (rows or []) if (r.get("abbr") or "").strip() == target))


def _line_label(abbr, stored_name):
	"""Printed description of a component, in the reader's language.

	Resolved when the receipt is rendered — ``_()`` reads ``frappe.local.lang`` at call
	time, which is the language of the user who asked for the print. A component the
	engine does not know falls back to the name stored on the slip, passed through the
	same context so a site that adds its own component can still translate it.
	"""
	msgid = COMPONENT_LABELS.get((abbr or "").strip()) or (stored_name or "")
	return _(msgid, context=TRANSLATION_CONTEXT) if msgid else ""


def _leave_days(doc):
	"""Days of the period recorded as ``On Leave`` in submitted Attendance.

	Shared with hr_api.salary_slip_explanation so the receipt and the HR explanation
	panel can never disagree about the same count. Leave is not deducted from paid days;
	it is printed so the employee can see it was counted as paid.
	"""
	if not doc.get("employee"):
		return 0.0
	return flt(frappe.db.sql(
		"""select count(*) from `tabAttendance`
		where employee = %s and docstatus = 1 and status = 'On Leave'
		  and attendance_date between %s and %s""",
		(doc.employee, getdate(doc.start_date), getdate(doc.end_date)))[0][0])


def _days(doc, overtime):
	"""Processing counters, every one copied from the slip or counted from Attendance.

	``absence`` is total working days minus paid days — the same subtraction the HR
	explanation panel shows (hr_api.salary_slip_explanation) and the same figure the
	engine's proration factor was built from. It is a presentation of two stored numbers,
	not a recalculation of either.
	"""
	total = flt(doc.get("total_working_days"))
	paid = flt(doc.get("payment_days"))
	absence = flt(total - paid, 2)
	return {
		"total": total,
		"paid": paid,
		"absence": absence if absence > 0 else 0.0,
		"leave": _leave_days(doc),
		"overtime_hours": flt(overtime.get("hours")) if overtime.get("show") else 0.0,
	}


def _period_label(doc):
	"""Localised period: the month the payroll period ends in, e.g. "Julho 2026" /
	"July 2026". A 23 June - 22 July cycle is July's payroll, so only July is printed
	(HR, October 2026; it used to read "Junho/Julho 2026").

	Month names come from Babel through ``formatdate``, which reads the language of the
	current request; ``_periodo`` above stays Portuguese for the older format.
	"""
	end = getdate(doc.end_date)
	name = formatdate(end, "MMMM")
	return "{0} {1}".format(name[:1].upper() + name[1:], end.year)


def _issue_date(doc):
	"""The receipt's issue date: the last day of the month the period ends in (HR,
	October 2026), e.g. 31-07-2026 for a 23 June - 22 July period."""
	return get_last_day(getdate(doc.end_date))


def _company_logo(company):
	"""The company logo as an inline data URI, or None.

	Only a public file (``/files/...``) is used: a private one would need the caller's
	session to be fetched and cannot be embedded for a scheduled or system render.
	"""
	import base64
	import mimetypes
	import os

	if not company or not frappe.get_meta("Company").has_field("company_logo"):
		return None
	url = frappe.db.get_value("Company", company, "company_logo")
	if not url or not url.startswith("/files/"):
		return None
	path = frappe.get_site_path("public", url.lstrip("/"))
	try:
		if not os.path.isfile(path) or os.path.getsize(path) > MAX_LOGO_BYTES:
			return None
		with open(path, "rb") as f:
			data = f.read()
	except OSError:
		return None
	mime = mimetypes.guess_type(path)[0] or "image/png"
	return "data:{0};base64,{1}".format(mime, base64.b64encode(data).decode("ascii"))


def _payment(doc, employee, currency):
	"""Payment block: method, date and bank details — all recorded data, never a guess.

	The date is the posting date of the slip's own payment Journal Entry, when one is in
	the ledger. A slip that has not been paid prints no date rather than the payroll
	posting date dressed up as one.
	"""
	mode = employee.get("salary_mode") or ""
	iban = employee.get("bank_account")
	if mode == "Bank" or (not mode and iban):
		method = "bank"
	elif mode == "Cash":
		method = "cash"
	elif mode == "Cheque":
		method = "cheque"
	else:
		method = None

	paid_on = None
	reference = None
	if doc.get("payment_entry"):
		je = frappe.db.get_value(
			"Journal Entry", doc.get("payment_entry"), ["posting_date", "docstatus", "cheque_no"],
			as_dict=True)
		if je and cint(je.get("docstatus")) == 1:
			paid_on = je.get("posting_date")
			reference = je.get("cheque_no")

	return {
		"method": method,
		"date": paid_on,
		"reference": reference,
		"currency": currency,
		"bank_name": employee.get("bank_name") if method == "bank" else None,
		"iban": iban if method == "bank" else None,
	}


@frappe.whitelist()
def get_context(doc):
	"""Build everything the receipt template needs.

	``doc`` is the Isoft Salary Slip document passed in by the print format. Permissions
	are enforced by the print pipeline before this runs; the extra check below stops the
	helper being used as a data-leak endpoint if it is ever called directly over HTTP.
	"""
	if isinstance(doc, str):
		# Direct call (whitelisted endpoint): the caller supplied only a name, so nothing
		# has authorised them yet. get_doc does not check permissions, so assert read
		# access explicitly — this is what stops the helper being used to read another
		# employee's slip. The record-level rules in doc_permissions.salary_slip_permission
		# are applied by check_permission via the has_permission hook.
		doc = frappe.get_doc("Isoft Salary Slip", doc)
		doc.check_permission("read")
	# When a document object is passed the print pipeline has already enforced access;
	# re-checking here would break legitimate system-context rendering (scheduled emails).

	earnings, deductions, memos = _lines(doc)

	total_earnings = flt(sum(r["amount"] for r in earnings))
	total_deductions = flt(sum(r["amount"] for r in deductions))

	gross = flt(doc.get("gross_pay"))
	total_deduction = flt(doc.get("total_deduction"))
	net = flt(doc.get("net_pay"))

	# The printed columns must reconcile with the stored totals. If they do not, the slip
	# holds a component the receipt is filtering out (or a stale total): surface it rather
	# than print a document whose arithmetic is visibly wrong.
	tolerance = 0.05
	reconciles = (
		abs(total_earnings - gross) <= tolerance
		and abs(total_deductions - total_deduction) <= tolerance
		and abs((gross - total_deduction) - net) <= tolerance
	)

	settings = frappe.get_cached_doc("Isoft HR Settings")
	company_currency = frappe.db.get_value("Company", doc.get("company"), "default_currency")
	currency = doc.get("currency") or settings.get("currency") or company_currency

	# Câmbio: 1 when the slip is denominated in the company's own currency, which is the
	# only case this payroll engine produces. A slip in a foreign currency would need a
	# stored rate and the slip has no such field, so it prints blank instead of a guess.
	cambio = 1.0 if (currency and company_currency and currency == company_currency) else None

	total_working_days = flt(doc.get("total_working_days"))
	base_earning = _component_amount(doc.get("earnings"), "SB")
	# "Vencimento" in the header is the CONTRACTUAL monthly salary held on the Salary
	# Profile, not the prorated R101 line further down: the reference receipt shows
	# 120 000,00 in the header against a prorated 114 461,58 on the line. Falls back to
	# the slip's own base line when the profile is gone.
	profile_base = 0.0
	if doc.get("salary_profile"):
		profile_base = flt(frappe.db.get_value(
			"Isoft Salary Profile", doc.get("salary_profile"), "base"))
	vencimento = profile_base or base_earning

	# Venc./Hora is a display rate over the contractual salary. Two divisors are accepted,
	# both read from Isoft HR Settings; neither is ever invented.
	daily_hours = flt(settings.get("standard_daily_hours"))
	weekly_hours = flt(settings.get("settlement_weekly_hours"))
	hourly_rate = None
	if vencimento and total_working_days and daily_hours:
		hourly_rate = flt(vencimento / (total_working_days * daily_hours), 2)
	elif vencimento and weekly_hours:
		# Monthly hours = weekly hours x 52 / 12. This is the divisor the reference
		# receipt uses: 120 000,00 / 195 = 615,38 at a 45-hour week.
		hourly_rate = flt(vencimento / (weekly_hours * 52.0 / 12.0), 2)

	employee = _employee_info(doc.get("employee"))
	overtime = _overtime(doc)
	# The hours used to be printed in the Processing Information box, which HR removed
	# (October 2026). The overtime line carries them instead, so the receipt still says
	# how many hours were paid.
	if overtime.get("show") and flt(overtime.get("hours")):
		for line in earnings:
			if line["code"] == _code_for("HEX"):
				line["hours"] = flt(overtime.get("hours"))
	irt_retido = _component_amount(doc.get("deductions"), IRT_ABBR) or flt(doc.get("irt_amount"))
	inss_retido = (_component_amount(doc.get("deductions"), INSS_ABBR)
	               or flt(doc.get("ss_employee_amount")))

	return {
		"company": _company_info(doc.get("company")),
		"company_logo": _company_logo(doc.get("company")),
		"employee": employee,
		"document": doc.get("name"),
		"payroll_reference": doc.get("payroll_entry"),
		"period_label": _period_label(doc),
		"issue_date": _issue_date(doc),
		"days": _days(doc, overtime),
		"payment": _payment(doc, employee, currency),
		# Headline figures, each copied from the field the engine wrote.
		"summary": {
			"gross": gross,
			"taxable": flt(doc.get("taxable_income")),
			"inss": inss_retido,
			"irt": irt_retido,
			"total_deductions": total_deduction,
			"net": net,
		},
		"copies": [dict(c) for c in COPIES],
		"periodo": _periodo(doc),
		"data_fecho": doc.get("end_date"),
		"posting_date": doc.get("posting_date"),
		"start_date": doc.get("start_date"),
		"end_date": doc.get("end_date"),
		"vencimento": vencimento,
		"vencimento_hora": hourly_rate,
		"dias_mes": total_working_days,
		"dias_pagos": flt(doc.get("payment_days")),
		"currency": currency,
		"cambio": cambio,
		"faltas": _faltas(doc),
		"irt": {
			"retido": irt_retido,
			"total_remuneracoes": flt(doc.get("taxable_income")),
			"rate": flt(doc.get("irt_rate")),
			"isento": cint(employee.get("isento_irt")),
		},
		"inss": {
			"retido": inss_retido,
			"base": flt(doc.get("ss_base")),
			"rate": flt(doc.get("ss_employee_rate")),
		},
		"earnings": earnings,
		"deductions": deductions,
		"memos": memos,
		"total_earnings": total_earnings,
		"total_deductions": total_deductions,
		"gross_pay": gross,
		"total_deduction": total_deduction,
		"net_pay": net,
		"reconciles": reconciles,
		"overtime": overtime,
		"pagamentos": _formas_pagamento(doc),
		# "Original" / "Duplicado": Frappe keeps no print counter and the slip has no
		# field for one, so every copy prints as the original. Giving this a real value
		# needs a stored reprint count — see the note in the print format's docstring.
		"copy_label": "Original",
		"is_draft": cint(doc.get("docstatus")) == 0,
		"is_cancelled": cint(doc.get("docstatus")) == 2,
	}
