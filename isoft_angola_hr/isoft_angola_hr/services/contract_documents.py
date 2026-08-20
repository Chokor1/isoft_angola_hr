# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Contract documents: templates, safe substitution, versioning and issue.

THE TEMPLATE ENGINE IS NOT JINJA, AND THAT IS THE POINT.

Frappe ships Jinja and it would be one line to call it. It is also the single most
dangerous thing that could be done here. HR — not a developer — writes these templates,
and Frappe's Jinja environment exposes ``frappe``, which means a template body of
``{{ frappe.db.sql("...") }}`` or ``{{ frappe.get_doc("User", "Administrator").api_secret }}``
would execute with the privileges of whoever pressed "Generate". A contract template
would become a remote code execution primitive operated through a rich-text box.

So this module implements substitution *itself*: a regular expression finds
``{{ name }}``, looks ``name`` up in a fixed dictionary, and writes the value in. There is
no expression parser, no attribute access, no filters, no function calls. A placeholder
that is not in :data:`VARIABLES` is left visibly unresolved rather than silently emptied,
so a typo shows up on the page instead of producing a contract with a blank where a
salary should be.

Versioning (§32): a generated document stores the rendered text, the template it came
from and that template's version number. Editing the template afterwards bumps its
version and does not touch anything already issued. An old contract therefore stays
readable exactly as it was signed, which is the only property that matters here.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, format_date, getdate, now_datetime, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: ``{{ name }}`` with any surrounding whitespace. Nothing else is recognised — no
#: ``{% %}`` tags, no filters, no dotted paths.
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

#: Placeholders that reveal pay. They resolve only when the template is marked
#: ``include_salary`` AND the caller holds SALARY_PROFILE_READ (§31).
SALARY_VARIABLES = frozenset({
	"base_salary", "food_allowance", "transport_allowance", "gross_salary",
})

#: Every placeholder HR may use, with a one-line description for the help panel.
VARIABLE_HELP = {
	"employee_name": "Full name",
	"employee_id": "Employee number",
	"nif": "Tax number (NIF)",
	"inss_number": "Social security number",
	"date_of_birth": "Date of birth",
	"id_document": "Identity document number, if recorded",
	"address": "Current address",
	"company": "Employer name",
	"company_nif": "Employer tax number",
	"company_address": "Employer address",
	"contract_type": "Contract type",
	"contract_number": "Contract reference",
	"designation": "Job title",
	"department": "Department",
	"work_location": "Place of work",
	"employment_type": "Employment type",
	"start_date": "Contract start date",
	"end_date": "Contract end date, or “sem termo”",
	"duration_months": "Contract duration in whole months",
	"probation_start": "Probation start date",
	"probation_end": "Probation end date",
	"probation_months": "Probation length in whole months",
	"notice_days": "Notice period in days, as configured",
	"holiday_list": "Holiday calendar",
	"shift_type": "Shift",
	"today": "Date the document is generated",
	"base_salary": "Base salary (only when the template allows it)",
	"food_allowance": "Food allowance (only when the template allows it)",
	"transport_allowance": "Transport allowance (only when the template allows it)",
	"gross_salary": "Base + allowances (only when the template allows it)",
}


def available_variables(include_salary=False):
	"""The placeholders a template may use, for the help panel and the tests."""
	return sorted(
		[{"name": k, "help": v, "salary": k in SALARY_VARIABLES}
		 for k, v in VARIABLE_HELP.items()
		 if include_salary or k not in SALARY_VARIABLES],
		key=lambda r: r["name"])


def _company_address(company):
	"""The company's primary address, via the Dynamic Link table Frappe actually uses."""
	rows = frappe.db.sql(
		"""select a.address_line1, a.address_line2, a.city
		from `tabAddress` a join `tabDynamic Link` dl on dl.parent = a.name
		where dl.link_doctype = 'Company' and dl.link_name = %s
		order by a.is_primary_address desc, a.modified desc limit 1""",
		company, as_dict=True)
	if not rows:
		return ""
	row = rows[0]
	return ", ".join(p for p in (row.address_line1, row.address_line2, row.city) if p)


def _months_between(start, end):
	if not (start and end):
		return None
	start, end = getdate(start), getdate(end)
	months = (end.year - start.year) * 12 + (end.month - start.month)
	if end.day < start.day:
		months -= 1
	return max(0, months)


def build_context(contract, include_salary=False):
	"""Resolve every whitelisted placeholder for one contract.

	Returns plain strings. Nothing callable, nothing with attributes — whatever ends up
	in this dict is the entire universe a template can reach.
	"""
	doc = contract if hasattr(contract, "employee") else frappe.get_doc(
		"Isoft Employment Contract", contract)
	emp = frappe.get_doc("Employee", doc.employee)
	company = frappe.get_doc("Company", doc.company or emp.company)

	def d(value):
		return format_date(value) if value else ""

	ctx = {
		"employee_name": emp.employee_name or "",
		"employee_id": emp.name,
		"nif": emp.get("custom_nif") or "",
		"inss_number": emp.get("custom_inss_number") or "",
		"date_of_birth": d(emp.date_of_birth),
		"id_document": emp.get("passport_number") or emp.get("custom_id_number") or "",
		"address": emp.current_address or "",
		"company": company.name,
		"company_nif": company.tax_id or "",
		# Addresses link through the Dynamic Link child table, not a column on Address.
		"company_address": _company_address(company.name),
		"contract_type": doc.contract_type or "",
		"contract_number": doc.contract_number or doc.name,
		"designation": doc.designation or emp.designation or "",
		"department": doc.department or emp.department or "",
		"work_location": doc.work_location or emp.get("branch") or "",
		"employment_type": doc.employment_type or emp.employment_type or "",
		"start_date": d(doc.start_date),
		"end_date": d(doc.end_date) if not cint(doc.is_open_ended) else _("sem termo"),
		"duration_months": str(_months_between(doc.start_date, doc.end_date) or ""),
		"probation_start": d(doc.probation_start),
		"probation_end": d(doc.probation_end),
		"probation_months": str(_months_between(doc.probation_start, doc.probation_end) or ""),
		"notice_days": str(cint(doc.notice_days) or ""),
		"holiday_list": doc.holiday_list or emp.holiday_list or "",
		"shift_type": doc.shift_type or "",
		"today": format_date(nowdate()),
	}

	if include_salary:
		profile = doc.salary_profile or frappe.db.get_value(
			"Isoft Salary Profile", {"employee": doc.employee}, "name",
			order_by="from_date desc")
		row = frappe.db.get_value(
			"Isoft Salary Profile", profile,
			["base", "food_allowance", "transport_allowance"], as_dict=True) if profile else None
		base = flt(row.base) if row else 0
		food = flt(row.food_allowance) if row else 0
		transport = flt(row.transport_allowance) if row else 0
		currency = frappe.db.get_value("Company", company.name, "default_currency") or "AKZ"
		ctx.update({
			"base_salary": frappe.utils.fmt_money(base, currency=currency),
			"food_allowance": frappe.utils.fmt_money(food, currency=currency),
			"transport_allowance": frappe.utils.fmt_money(transport, currency=currency),
			"gross_salary": frappe.utils.fmt_money(base + food + transport, currency=currency),
		})
	return ctx


def render(body, context):
	"""Substitute whitelisted placeholders. Executes nothing.

	An unknown placeholder is preserved verbatim — ``{{ salary }}`` stays ``{{ salary }}``
	— so a mistake is visible on the printed page instead of silently becoming a blank
	in a legal document.
	"""
	body = body or ""
	unresolved = []

	def sub(match):
		key = match.group(1)
		if key in context:
			value = context[key]
			return frappe.utils.escape_html("" if value is None else str(value))
		unresolved.append(key)
		return match.group(0)

	out = PLACEHOLDER.sub(sub, body)
	return out, sorted(set(unresolved))


def validate_template(body):
	"""Report placeholders a template uses that this app cannot resolve.

	Called on save so HR is told about a typo when they write it, not when a contract is
	generated three weeks later.
	"""
	used = set(PLACEHOLDER.findall(body or ""))
	unknown = sorted(used - set(VARIABLE_HELP))
	# Anything that looks like a Jinja statement or an expression is refused outright.
	# Not because the renderer would run it — it would not — but because a template
	# containing `{% if %}` will silently print the tag on a signed contract.
	suspicious = re.findall(r"\{%.*?%\}|\{\{[^}]*[.\(\[|][^}]*\}\}", body or "")
	return {"unknown": unknown, "suspicious": sorted(set(suspicious))[:10]}


# --------------------------------------------------------------------------- #
# Issuing a document
# --------------------------------------------------------------------------- #
def pick_template(contract):
	"""The most specific active template for a contract, or None."""
	doc = contract if hasattr(contract, "contract_type") else frappe.get_doc(
		"Isoft Employment Contract", contract)
	rows = frappe.db.sql(
		"""select name, contract_type, company, version, include_salary
		from `tabIsoft Contract Template`
		where ifnull(is_active, 0) = 1
		  and (ifnull(effective_from, '1900-01-01') <= %s)
		  and (ifnull(contract_type, '') in ('', %s))
		  and (ifnull(company, '') in ('', %s))
		order by (contract_type = %s) desc, (company = %s) desc, modified desc""",
		(getdate(nowdate()), doc.contract_type or "", doc.company or "",
		 doc.contract_type or "", doc.company or ""), as_dict=True)
	return rows[0] if rows else None


def generate(contract, template=None):
	"""Produce a contract document from a template. Always creates a NEW record.

	Regenerating never overwrites an issued document: the previous one is marked
	Superseded and kept. A contract that somebody has already signed must remain exactly
	as they signed it, whatever the template says today.
	"""
	perms.require(perms.CONTRACT_WRITE)
	doc = frappe.get_doc("Isoft Employment Contract", contract)
	perms.require_company(doc.company)

	tpl_name = template or (pick_template(doc) or {}).get("name")
	if not tpl_name:
		frappe.throw(
			_("No active contract template matches this contract type. Create one in "
			  "Isoft Contract Template first."))
	tpl = frappe.get_doc("Isoft Contract Template", tpl_name)
	if not cint(tpl.is_active):
		frappe.throw(_("Template {0} is not active.").format(tpl.name))

	# Salary appears only if BOTH the template allows it and this user may see pay.
	include_salary = bool(cint(tpl.include_salary) and perms.can(perms.SALARY_PROFILE_READ))
	if cint(tpl.include_salary) and not include_salary:
		frappe.msgprint(
			_("This template includes salary, but you do not have permission to see "
			  "compensation. The salary placeholders were left unresolved."))

	body, unresolved = render(tpl.body, build_context(doc, include_salary=include_salary))

	for old in frappe.get_all("Isoft Contract Document",
	                          filters={"contract": doc.name, "status": ("in", ("Draft", "Final"))},
	                          pluck="name"):
		frappe.db.set_value("Isoft Contract Document", old, "status", "Superseded")

	out = frappe.get_doc({
		"doctype": "Isoft Contract Document",
		"contract": doc.name,
		"employee": doc.employee,
		"employee_name": doc.employee_name,
		"company": doc.company,
		"template": tpl.name,
		"template_version": cint(tpl.version),
		"language": tpl.language,
		"body": body,
		"status": "Draft",
		"salary_included": 1 if include_salary else 0,
		"generated_by": frappe.session.user,
		"generated_at": now_datetime(),
	}).insert(ignore_permissions=True)

	if unresolved:
		frappe.msgprint(
			_("These placeholders are not recognised and were left in the text: {0}").format(
				", ".join(unresolved)))
	return {"name": out.name, "template": tpl.name, "version": cint(tpl.version),
	        "unresolved": unresolved, "salary_included": include_salary}


def finalise(name):
	"""Mark a reviewed document as the issued version (§33)."""
	perms.require(perms.CONTRACT_WRITE)
	doc = frappe.get_doc("Isoft Contract Document", name)
	perms.require_company(doc.company)
	if doc.status != "Draft":
		frappe.throw(_("Only a draft document can be finalised (this one is {0}).").format(
			doc.status))
	doc.db_set({"status": "Final", "finalised_by": frappe.session.user,
	            "finalised_at": now_datetime()})
	return doc.status


def attach_signed(name, file_url, signed_on=None):
	"""Record the signed copy. The generated text is never replaced by it."""
	perms.require(perms.CONTRACT_WRITE)
	doc = frappe.get_doc("Isoft Contract Document", name)
	perms.require_company(doc.company)
	if not file_url:
		frappe.throw(_("Attach the signed document first."))
	doc.db_set({"signed_copy": file_url, "signed_on": getdate(signed_on or nowdate()),
	            "status": "Signed"})
	# Mirror it onto the contract so the signed copy is one click from the contract too.
	frappe.db.set_value("Isoft Employment Contract", doc.contract, "contract_document", file_url)
	return doc.status


def documents_for(contract):
	return frappe.db.sql(
		"""select name, template, template_version, status, language, generated_by,
			generated_at, signed_copy, signed_on, salary_included
		from `tabIsoft Contract Document` where contract = %s
		order by creation desc""", contract, as_dict=True)
