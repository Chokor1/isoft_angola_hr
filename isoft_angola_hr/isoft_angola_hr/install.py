# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt
"""Install / migrate setup for the Isoft Angola HR app.

Adds Angola-specific custom fields onto the reused ERPNext core doctypes
(Employee, Attendance, Timesheet) and ensures baseline configuration exists.
Idempotent: safe to run on every install and migrate.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Employee": [
		{
			"fieldname": "isoft_angola_hr_section",
			"label": "Angola HR",
			"fieldtype": "Section Break",
			"insert_after": "salary_mode",
			"collapsible": 1,
		},
		{
			"fieldname": "custom_nif",
			"label": "NIF (Tax ID)",
			"fieldtype": "Data",
			"insert_after": "isoft_angola_hr_section",
		},
		{
			"fieldname": "custom_inss_number",
			"label": "Nº Segurança Social (INSS)",
			"fieldtype": "Data",
			"insert_after": "custom_nif",
		},
		{
			"fieldname": "isoft_angola_hr_cb",
			"fieldtype": "Column Break",
			"insert_after": "custom_inss_number",
		},
		{
			"fieldname": "custom_dependents",
			"label": "Dependentes",
			"fieldtype": "Int",
			"insert_after": "isoft_angola_hr_cb",
			"default": "0",
		},
		{
			"fieldname": "custom_irt_exempt",
			"label": "Isento de IRT",
			"fieldtype": "Check",
			"insert_after": "custom_dependents",
			"default": "0",
		},
		{
			"fieldname": "custom_payroll_payable_account",
			"label": "Conta a Pagar (Salário)",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": "custom_irt_exempt",
			"description": "Optional. Overrides the default Payroll Payable account on the Journal Entry.",
		},
	],
	"Attendance": [
		{
			"fieldname": "custom_overtime_hours",
			"label": "Horas Extras",
			"fieldtype": "Float",
			"insert_after": "working_hours",
			"description": "Overtime hours worked on this day (feeds Angola payroll).",
		},
	],
}


# Official Angola IRT table (Grupo A) — code-defined so it ships with the app.
# (from_amount, to_amount, rate%, parcela_fixa). Marginal rate applies to the
# excess over (from_amount - 1), i.e. the round lower bound of each bracket.
ANGOLA_IRT_2024 = [
	(0, 100000, 0.0, 0),
	(100001, 150000, 13.0, 0),
	(150001, 200000, 16.0, 12500),
	(200001, 300000, 18.0, 31250),
	(300001, 500000, 19.0, 49250),
	(500001, 1000000, 20.0, 87250),
	(1000001, 1500000, 21.0, 187250),
	(1500001, 2000000, 22.0, 292250),
	(2000001, 2500000, 23.0, 402250),
	(2500001, 5000000, 24.0, 517250),
	(5000001, 10000000, 24.5, 1117250),
	(10000001, 0, 25.0, 2342250),
]
DEFAULT_IRT_TABLE = "Tabela IRT (Angola)"


PAYSLIP_FORMAT = "Recibo de Vencimento"

PAYSLIP_HTML = """
<div class="payslip" style="font-family:Arial,sans-serif;">
  <h2 style="text-align:center;margin-bottom:0;">{{ doc.company or "" }}</h2>
  <h4 style="text-align:center;margin-top:2px;font-weight:600;">{{ _("Payslip") }}</h4>
  <table style="width:100%;font-size:12px;margin:10px 0;">
    <tr><td><b>{{ _("Employee") }}:</b> {{ doc.employee_name }}</td>
        <td style="text-align:right;"><b>{{ _("Period") }}:</b> {{ doc.start_date }} - {{ doc.end_date }}</td></tr>
    <tr><td><b>{{ _("ID") }}:</b> {{ doc.employee }}</td>
        <td style="text-align:right;"><b>{{ _("Paid Days") }}:</b> {{ doc.payment_days }} / {{ doc.total_working_days }}</td></tr>
  </table>
  <table style="width:100%;font-size:12px;border-collapse:collapse;">
    <thead><tr>
      <th style="border-bottom:2px solid #333;text-align:left;padding:4px;">{{ _("Earnings") }}</th>
      <th style="border-bottom:2px solid #333;text-align:right;padding:4px;">{{ _("Amount") }}</th>
      <th style="border-bottom:2px solid #333;text-align:left;padding:4px;">{{ _("Deductions") }}</th>
      <th style="border-bottom:2px solid #333;text-align:right;padding:4px;">{{ _("Amount") }}</th>
    </tr></thead>
    <tbody>
    {% set earns = doc.earnings | selectattr("do_not_include_in_total", "equalto", 0) | list %}
    {% set rows = [earns | length, doc.deductions | length] | max %}
    {% for i in range(rows) %}
      <tr>
        <td style="padding:4px;">{{ _(earns[i].salary_component) if i < (earns|length) else "" }}</td>
        <td style="text-align:right;padding:4px;">{{ frappe.utils.fmt_money(earns[i].amount, currency=doc.currency) if i < (earns|length) else "" }}</td>
        <td style="padding:4px;">{{ _(doc.deductions[i].salary_component) if i < (doc.deductions|length) else "" }}</td>
        <td style="text-align:right;padding:4px;">{{ frappe.utils.fmt_money(doc.deductions[i].amount, currency=doc.currency) if i < (doc.deductions|length) else "" }}</td>
      </tr>
    {% endfor %}
      <tr style="border-top:2px solid #333;font-weight:bold;">
        <td style="padding:4px;">{{ _("Gross Total") }}</td>
        <td style="text-align:right;padding:4px;">{{ frappe.utils.fmt_money(doc.gross_pay, currency=doc.currency) }}</td>
        <td style="padding:4px;">{{ _("Total Deductions") }}</td>
        <td style="text-align:right;padding:4px;">{{ frappe.utils.fmt_money(doc.total_deduction, currency=doc.currency) }}</td>
      </tr>
    </tbody>
  </table>
  <table style="width:100%;font-size:13px;margin-top:12px;">
    <tr>
      <td><b>{{ _("Taxable Income") }}:</b> {{ frappe.utils.fmt_money(doc.taxable_income, currency=doc.currency) }}</td>
      <td style="text-align:right;font-size:15px;"><b>{{ _("Net Pay") }}: {{ frappe.utils.fmt_money(doc.net_pay, currency=doc.currency) }}</b></td>
    </tr>
  </table>
</div>
"""


DEFAULT_ABSENCE_REASONS = [
	"Doença",
	"Assistência médica",
	"Acompanhamento de familiar",
	"Falecimento de familiar",
	"Casamento",
	"Comparência judicial",
	"Maternidade / Paternidade",
	"Motivo pessoal",
]


def after_install():
	setup_custom_fields()
	seed_defaults()
	seed_absence_reasons()
	create_payslip_print_format()


def seed_absence_reasons():
	for reason in DEFAULT_ABSENCE_REASONS:
		if not frappe.db.exists("Isoft Absence Reason", reason):
			frappe.get_doc({"doctype": "Isoft Absence Reason", "reason": reason, "is_active": 1}).insert(
				ignore_permissions=True
			)
	frappe.db.commit()


def setup_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	frappe.db.commit()


def seed_defaults():
	"""Seed the standard Angola IRT table and baseline Settings (idempotent)."""
	if not frappe.db.exists("IRT Table", DEFAULT_IRT_TABLE):
		doc = frappe.get_doc({
			"doctype": "IRT Table",
			"title": DEFAULT_IRT_TABLE,
			"effective_from": "2024-01-01",
			"currency": "AOA",
			"brackets": [
				{
					"from_amount": fr,
					"to_amount": to,
					"excess_over": (fr - 1) if fr else 0,
					"rate": rate,
					"parcela_fixa": pf,
				}
				for (fr, to, rate, pf) in ANGOLA_IRT_2024
			],
		})
		doc.insert(ignore_permissions=True)

	settings = frappe.get_single("Isoft HR Settings")
	changed = False
	if not settings.default_irt_table:
		settings.default_irt_table = DEFAULT_IRT_TABLE
		changed = True
	if not settings.ss_employee_rate:
		settings.ss_employee_rate = 3
		changed = True
	if not settings.food_allowance_exemption:
		settings.food_allowance_exemption = 30000
		changed = True
	if not settings.transport_allowance_exemption:
		settings.transport_allowance_exemption = 30000
		changed = True
	if changed:
		settings.save(ignore_permissions=True)
	frappe.db.commit()


def create_payslip_print_format():
	if frappe.db.exists("Print Format", PAYSLIP_FORMAT):
		doc = frappe.get_doc("Print Format", PAYSLIP_FORMAT)
		doc.html = PAYSLIP_HTML
		doc.print_format_type = "Jinja"
		doc.custom_format = 1
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "Print Format",
			"name": PAYSLIP_FORMAT,
			"doc_type": "Isoft Salary Slip",
			"module": "Isoft Angola HR",
			"print_format_type": "Jinja",
			"custom_format": 1,
			"standard": "No",
			"html": PAYSLIP_HTML,
		}).insert(ignore_permissions=True)
	frappe.db.commit()
