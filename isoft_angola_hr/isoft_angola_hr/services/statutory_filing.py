# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Statutory filing — what was verified, what was built, and what deliberately was not.

RESEARCH FINDINGS (August 2026, official sources only)
------------------------------------------------------

**IRT — Mapa Mensal de Remunerações (AGT).**
Since January 2022, settlement of IRT Grupo A is made *exclusively* by electronic
submission of the monthly remuneration map on the Portal do Contribuinte
(``portaldocontribuinte.minfin.gov.ao``), route: Serviços → Declarações → IRT → Entregar →
Mapa de Remunerações. The legal basis is article 17.º of the Código do IRT. The obligation
applies to entities with more than three employees. The portal itself computes the IRT due
from the remuneration figures entered and issues the Nota de Liquidação.

**INSS — Folha de Remunerações.**
Monthly submission is mandatory and must now be made electronically through INSS Virtual
(``virtual.inss.gov.ao``); employers can no longer submit on paper, and INSS offices keep
a supervised terminal for employers without internet access.

**NO PUBLISHED MACHINE FORMAT WAS FOUND FOR EITHER.**
Both are portal-entry processes. AGT-certified payroll products (PHC, Primavera) describe
producing the map *for delivery in the portal*, not a public upload schema, and neither AGT
nor INSS publishes a file layout, XSD or API specification. Section 59 of the brief is
explicit about what to do in that situation: *"If a documented machine format exists:
implement it. If not: do not invent one."*

So this module does not invent one. It produces a **working file** — an ordinary
spreadsheet, labelled as such — whose columns mirror the fields the portal asks for, so
that whoever keys the declaration in has the figures in front of them in the right order
and can reconcile the totals afterwards. It is explicitly NOT presented as an official
submission format, because it is not one.

What it does add, and what actually reduces risk:

* **Strict pre-flight validation** (§61) — a missing NIF or social security number fails
  loudly *before* somebody is halfway through keying a declaration.
* **A submission register** (§62) — period, type, totals, who generated it and when. The
  status stays ``Generated`` until a human records the portal's own receipt reference;
  downloading a file is not a submission and this module refuses to pretend otherwise.

Sources consulted: Portal do Contribuinte (AGT) guia rápido "Mapa Mensal de Remunerações
de IRT"; AGT Portal; INSS Virtual; ANGOP and Notícias de Angola reporting on the INSS
electronic-submission mandate; Código do IRT art. 17.º.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate, now_datetime, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

IRT = "IRT — Mapa Mensal de Remunerações"
INSS = "INSS — Folha de Remunerações"

PORTALS = {
	IRT: "portaldocontribuinte.minfin.gov.ao — Serviços → Declarações → IRT → Entregar → "
	     "Mapa de Remunerações",
	INSS: "virtual.inss.gov.ao — Folha de Remunerações",
}

#: Restated on every generated file and in every API response, so nobody downstream
#: mistakes the working file for an accepted upload format.
DISCLAIMER = (
	"FICHEIRO DE TRABALHO INTERNO. Nem a AGT nem o INSS publicam um formato de ficheiro "
	"para submissão automática: ambas as declarações são entregues no respectivo portal. "
	"Este ficheiro serve para preencher e conferir a declaração, não substitui a entrega."
)


def _slips(company, period_start, period_end):
	return frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.custom_nif as nif,
			e.custom_inss_number as inss_number, e.department, e.designation,
			e.date_of_joining, e.relieving_date,
			s.start_date, s.end_date, s.payment_days, s.total_working_days,
			s.gross_pay, s.taxable_income, s.irt_amount, s.irt_rate, s.irt_parcela_fixa,
			s.ss_base, s.ss_employee_rate, s.ss_employee_amount,
			s.ss_employer_rate, s.ss_employer_amount, s.net_pay
		from `tabIsoft Salary Slip` s
		left join `tabEmployee` e on e.name = s.employee
		where s.docstatus = 1 and s.company = %s
		  and s.start_date >= %s and s.end_date <= %s
		order by e.department, s.employee_name""",
		(company, period_start, period_end), as_dict=True)


def validate_period(submission_type, company, period_start=None, period_end=None):
	"""Everything that would make the declaration wrong, listed before it is produced (§61)."""
	perms.require(perms.REPORT_STATUTORY)
	perms.require_company(company)
	period_start = getdate(period_start or get_first_day(nowdate()))
	period_end = getdate(period_end or get_last_day(period_start))

	rows = _slips(company, period_start, period_end)
	errors, warnings = [], []

	if not rows:
		errors.append({"code": "STF-000", "employee": None,
		               "message": _("No approved payroll exists for this period. A "
		                            "declaration cannot be produced from draft payroll.")})

	for row in rows:
		who = "{0} ({1})".format(row.employee_name, row.employee)
		if submission_type == IRT:
			if not (row.nif or "").strip():
				errors.append({"code": "STF-001", "employee": row.employee,
				               "message": _("{0} has no NIF.").format(who)})
			if flt(row.gross_pay) <= 0:
				warnings.append({"code": "STF-010", "employee": row.employee,
				                 "message": _("{0} has zero gross pay.").format(who)})
			if flt(row.irt_amount) and not flt(row.taxable_income):
				errors.append({"code": "STF-002", "employee": row.employee,
				               "message": _("{0} has IRT withheld but no taxable base.")
				               .format(who)})
		else:
			if not (row.inss_number or "").strip():
				errors.append({"code": "STF-003", "employee": row.employee,
				               "message": _("{0} has no social security number.").format(who)})
			if flt(row.ss_employee_amount) and not flt(row.ss_base):
				errors.append({"code": "STF-004", "employee": row.employee,
				               "message": _("{0} has a contribution with no incidence base.")
				               .format(who)})
			if not flt(row.ss_employer_amount):
				warnings.append({"code": "STF-011", "employee": row.employee,
				                 "message": _("{0} has no employer contribution recorded.")
				                 .format(who)})

	# One person appearing twice in a monthly declaration is a rejection at the portal.
	seen = {}
	for row in rows:
		seen.setdefault(row.employee, []).append(row.name)
	for employee, slips in seen.items():
		if len(slips) > 1:
			errors.append({"code": "STF-005", "employee": employee,
			               "message": _("{0} has {1} approved slips in this period: {2}.")
			               .format(employee, len(slips), ", ".join(slips))})

	return {
		"submission_type": submission_type, "company": company,
		"period_start": str(period_start), "period_end": str(period_end),
		"employees": len(rows), "errors": errors, "warnings": warnings,
		"valid": not errors, "portal": PORTALS.get(submission_type),
	}


def _columns(submission_type):
	"""Column order mirrors what the portal asks for, so keying it in reads top to bottom."""
	if submission_type == IRT:
		return [
			("employee", _("Nº Colaborador")), ("employee_name", _("Nome")),
			("nif", _("NIF")), ("department", _("Departamento")),
			("gross_pay", _("Remuneração Bruta")),
			("ss_employee_amount", _("Segurança Social (3%)")),
			("taxable_income", _("Matéria Colectável")),
			("irt_rate", _("Taxa IRT (%)")), ("irt_parcela_fixa", _("Parcela Fixa")),
			("irt_amount", _("IRT Retido")), ("net_pay", _("Líquido")),
		]
	return [
		("employee", _("Nº Colaborador")), ("employee_name", _("Nome")),
		("inss_number", _("Nº Segurança Social")), ("department", _("Departamento")),
		("date_of_joining", _("Admissão")), ("relieving_date", _("Cessação")),
		("ss_base", _("Base de Incidência")),
		("ss_employee_rate", _("Taxa Trabalhador (%)")),
		("ss_employee_amount", _("Contribuição Trabalhador")),
		("ss_employer_rate", _("Taxa Entidade (%)")),
		("ss_employer_amount", _("Contribuição Entidade")),
	]


def build(submission_type, company, period_start=None, period_end=None, ignore_warnings=1):
	"""Validate, then produce the register entry and the working file."""
	perms.require(perms.REPORT_STATUTORY)
	if submission_type not in PORTALS:
		frappe.throw(_("Unknown submission type {0}.").format(submission_type))
	report = validate_period(submission_type, company, period_start, period_end)
	if not report["valid"]:
		# Fails visibly, and says exactly who is missing what (§61).
		frappe.throw(
			_("{0} problem(s) must be fixed before this declaration can be produced:<br>{1}")
			.format(len(report["errors"]),
			        "<br>".join(e["message"] for e in report["errors"][:15])))

	period_start = getdate(report["period_start"])
	period_end = getdate(report["period_end"])
	rows = _slips(company, period_start, period_end)
	columns = _columns(submission_type)

	total_employee = sum(flt(r.irt_amount if submission_type == IRT
	                         else r.ss_employee_amount) for r in rows)
	total_employer = sum(flt(r.ss_employer_amount) for r in rows) \
		if submission_type == INSS else 0.0
	total_base = sum(flt(r.taxable_income if submission_type == IRT else r.ss_base)
	                 for r in rows)

	doc = frappe.get_doc({
		"doctype": "Isoft Statutory Submission",
		"submission_type": submission_type,
		"company": company,
		"period_start": period_start,
		"period_end": period_end,
		"status": "Generated",
		"employees": len(rows),
		"total_gross": sum(flt(r.gross_pay) for r in rows),
		"total_base": total_base,
		"total_employee": total_employee,
		"total_employer": total_employer,
		"total_amount": total_employee + total_employer,
		"portal": PORTALS[submission_type],
		"validation_status": "Passed",
		"validation_errors": "\n".join(w["message"] for w in report["warnings"]) or None,
		"generated_by": frappe.session.user,
		"generated_at": now_datetime(),
	}).insert(ignore_permissions=True)

	return {
		"submission": doc.name, "rows": rows, "columns": columns,
		"totals": {"employees": len(rows), "base": total_base,
		           "employee": total_employee, "employer": total_employer},
		"warnings": report["warnings"], "portal": PORTALS[submission_type],
		"disclaimer": DISCLAIMER,
	}


def working_file(submission_type, company, period_start=None, period_end=None):
	"""The spreadsheet, with the disclaimer as its first row so it travels with the file."""
	from frappe.utils.xlsxutils import make_xlsx

	result = build(submission_type, company, period_start, period_end)
	columns = result["columns"]

	data = [
		[DISCLAIMER],
		["{0} · {1} · {2} → {3}".format(submission_type, company,
		                                result["rows"][0].start_date if result["rows"] else "",
		                                result["rows"][0].end_date if result["rows"] else "")],
		[PORTALS[submission_type]],
		[],
		[label for _key, label in columns],
	]
	for row in result["rows"]:
		data.append([row.get(key) for key, _label in columns])
	# A totals line, so whoever keys the declaration in can check the portal's own total
	# against ours before pressing submit.
	totals_row = [""] * len(columns)
	for index, (key, _label) in enumerate(columns):
		if key in ("gross_pay", "taxable_income", "irt_amount", "ss_base",
		           "ss_employee_amount", "ss_employer_amount", "net_pay"):
			totals_row[index] = sum(flt(r.get(key)) for r in result["rows"])
	totals_row[0] = _("TOTAL")
	data.append([])
	data.append(totals_row)

	label = "IRT" if submission_type == IRT else "INSS"
	xlsx = make_xlsx(data, "{0} {1}".format(label, result["submission"]))
	filename = "{0}_{1}_{2}.xlsx".format(
		label, company.replace(" ", "_"),
		getdate(period_start or nowdate()).strftime("%Y-%m"))

	frappe.db.set_value("Isoft Statutory Submission", result["submission"],
	                    "notes", _("Working file {0} generated.").format(filename))
	return {"filename": filename, "content": xlsx.getvalue(),
	        "submission": result["submission"], "disclaimer": DISCLAIMER}


def record_submission(name, reference, submitted_on=None, status="Submitted"):
	"""Mark a declaration as actually delivered — only with the portal's own reference (§62)."""
	perms.require(perms.STATUTORY_WRITE)
	doc = frappe.get_doc("Isoft Statutory Submission", name)
	perms.require_company(doc.company)
	if status not in ("Submitted", "Accepted", "Rejected"):
		frappe.throw(_("Unknown status {0}.").format(status))
	if not (reference or "").strip():
		# The whole point of the register. "I downloaded the file" is not evidence that
		# anything was declared, and a status that says otherwise is worse than no status.
		frappe.throw(
			_("Record the reference issued by the portal. A declaration is not marked as "
			  "submitted just because a file was produced."))
	doc.db_set({"status": status, "reference": reference,
	            "submitted_on": getdate(submitted_on or nowdate()),
	            "submitted_by": frappe.session.user})
	return {"name": doc.name, "status": doc.status, "reference": doc.reference}


def history(company=None, submission_type=None, limit=100):
	perms.require(perms.REPORT_STATUTORY)
	conditions, values = ["1=1"], []
	for field, value in (("company", company), ("submission_type", submission_type)):
		if value:
			conditions.append("s.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="s")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select s.name, s.submission_type, s.company, s.period_start, s.period_end,
			s.status, s.employees, s.total_amount, s.total_employee, s.total_employer,
			s.reference, s.submitted_on, s.generated_by, s.generated_at
		from `tabIsoft Statutory Submission` s where {0}
		order by s.period_start desc, s.creation desc limit {1}""".format(
			" and ".join(conditions), cint(limit) or 100), values, as_dict=True)


# --------------------------------------------------------------------------- #
# Bank file (§63, §64)
# --------------------------------------------------------------------------- #
#: Deliberately not implemented, and this is the reasoning.
#:
#: The brief is explicit: research the real format "only if a specific bank is
#: identified", and "do not create a fake BAI format based on assumptions". No target
#: bank is configured anywhere on this site, and Angolan banks do not publish their
#: salary-transfer layouts — they are issued to corporate clients under agreement. A
#: plausible-looking BAI or BFA adapter written from guesswork would be worse than the
#: generic spreadsheet that exists today, because it would look authoritative while
#: being rejected at the counter.
#:
#: §64 also warns against premature abstraction. One generic export is what one
#: unidentified bank needs; an adapter registry for three hypothetical banks is
#: architecture for a requirement nobody has stated.
BANK_FORMAT_STATUS = {
	"implemented": ["Generic .xlsx transfer list (Phase 2)"],
	"not_implemented": ["BAI", "BFA", "BIC", "Standard Bank Angola", "Millennium Atlântico"],
	"reason": "No target bank is configured on this site, and no Angolan bank publishes its "
	          "salary-transfer file layout publicly — the specification is issued to the "
	          "corporate client under agreement.",
	"to_enable": "Obtain the layout specification from the company's own bank, then add one "
	             "adapter for that bank. Do not add adapters for banks the company does not "
	             "use.",
}
