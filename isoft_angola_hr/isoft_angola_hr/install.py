# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Install / migrate setup for the Isoft Angola HR app.

Adds Angola-specific custom fields onto the reused ERPNext core doctypes
(Employee, Attendance, Timesheet) and ensures baseline configuration exists.
Idempotent: safe to run on every install and migrate.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	# The per-weekday Start/End override on ERPNext's Shift Type, together with the
	# Shift Weekday Hours child table this app now owns. Both used to sit in ERPNext
	# core; they are this app's enhancement, and Isoft Salary Slip derives an
	# employee's daily working hours from them. ERPNext's own
	# get_weekday_shift_hours() reads the table through `shift_doc.get("weekday_hours")
	# or []`, so without this app installed it simply finds nothing and Shift Type
	# behaves exactly like stock ERPNext.
	#
	# Deliberately NOT named custom_weekday_hours like the fields below: existing
	# child rows carry parentfield="weekday_hours", and ERPNext core plus this app's
	# api.py both address it by that name.
	"Shift Type": [
		{"fieldname": "weekly_schedule_section", "label": "Weekly Schedule (Optional)",
		 "fieldtype": "Section Break", "collapsible": 1, "insert_after": "holiday_list",
		 "description": "Optionally override Start Time / End Time per weekday (e.g. shorter "
		                "Saturdays). Weekdays not listed here use the Start Time and End Time "
		                "above. Uncheck 'Working Day' to make a weekday a non-working day "
		                "(treated like a holiday for auto-attendance)."},
		{"fieldname": "weekday_hours", "label": "Weekday Hours", "fieldtype": "Table",
		 "options": "Shift Weekday Hours", "insert_after": "weekly_schedule_section"},
	],
	# Phase 5 — the review workflow ERPNext's Appraisal does not have. Deliberately custom
	# fields on ERPNext's own DocType rather than a parallel review record: the goals,
	# weightings and scores stay where ERPNext computes them, and this app contributes
	# only the states and the attribution around them.
	"Appraisal": [
		{"fieldname": "custom_performance_cycle", "label": "Performance Cycle",
		 "fieldtype": "Link", "options": "Isoft Performance Cycle", "read_only": 1,
		 "insert_after": "company"},
		{"fieldname": "custom_review_state", "label": "Review State", "fieldtype": "Select",
		 "options": "\nPending Manager\nPending Employee\nPending HR\nFinalised\nCancelled",
		 "read_only": 1, "insert_after": "custom_performance_cycle"},
		{"fieldname": "custom_due_date", "label": "Review Due", "fieldtype": "Date",
		 "read_only": 1, "insert_after": "custom_review_state"},
		{"fieldname": "custom_manager", "label": "Reviewing Manager", "fieldtype": "Link",
		 "options": "Employee", "read_only": 1, "insert_after": "custom_due_date"},
		{"fieldname": "custom_manager_comments", "label": "Manager Comments",
		 "fieldtype": "Small Text", "insert_after": "remarks"},
		{"fieldname": "custom_manager_submitted_by", "label": "Reviewed By",
		 "fieldtype": "Link", "options": "User", "read_only": 1,
		 "insert_after": "custom_manager_comments"},
		{"fieldname": "custom_manager_submitted_at", "label": "Reviewed At",
		 "fieldtype": "Datetime", "read_only": 1,
		 "insert_after": "custom_manager_submitted_by"},
		{"fieldname": "custom_employee_comments", "label": "Employee Comments",
		 "fieldtype": "Small Text", "insert_after": "custom_manager_submitted_at"},
		{"fieldname": "custom_employee_acknowledged_at", "label": "Acknowledged At",
		 "fieldtype": "Datetime", "read_only": 1,
		 "insert_after": "custom_employee_comments"},
		{"fieldname": "custom_hr_finalised_by", "label": "Finalised By", "fieldtype": "Link",
		 "options": "User", "read_only": 1,
		 "insert_after": "custom_employee_acknowledged_at"},
		{"fieldname": "custom_hr_finalised_at", "label": "Finalised At",
		 "fieldtype": "Datetime", "read_only": 1, "insert_after": "custom_hr_finalised_by"},
		# HR-operated mode: the product does not require a line manager to hold a login.
		# When HR keys the manager's evaluation, "Reviewed By" is the HR user who typed it,
		# which on its own would misattribute the judgement. These two fields keep the
		# distinction the audit needs: who DECIDED, and how the decision arrived.
		{"fieldname": "custom_evaluation_source", "label": "Evaluation Source",
		 "fieldtype": "Select",
		 "options": "\nLine manager (self-service)\nLine manager decision recorded by HR"
		            "\nHR Manager (no line manager)\nOther",
		 "read_only": 1, "insert_after": "custom_manager_submitted_at"},
		{"fieldname": "custom_decision_by", "label": "Decision By",
		 "fieldtype": "Data", "read_only": 1,
		 "description": "The person whose evaluation this is, when it was recorded by "
		                "somebody else.",
		 "insert_after": "custom_evaluation_source"},
	],
	# Leave is the request HR records on somebody's behalf most often. Without the channel
	# the trail reads as if HR invented the absence.
	"Leave Application": [
		{"fieldname": "custom_request_source", "label": "Request Source",
		 "fieldtype": "Select",
		 "options": "\nEmployee verbal request\nEmail\nWritten request"
		            "\nManagement instruction\nHR initiated\nOther",
		 "insert_after": "description"},
	],
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
		{
			"fieldname": "custom_iban",
			"label": "IBAN",
			"fieldtype": "Data",
			"insert_after": "custom_payroll_payable_account",
			"description": "Bank IBAN — used for the payroll bank-transfer export.",
		},
		{
			"fieldname": "custom_insurance",
			"label": "Seguro",
			"fieldtype": "Data",
			"insert_after": "custom_iban",
			"description": "Insurance policy / number — shown on the payslip (Seguro).",
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


# Official Angola IRT tables (Grupo A) — code-defined so they ship with the app.
# (from_amount, to_amount, rate%, parcela_fixa). The marginal rate applies to the
# excess over (from_amount - 1), i.e. the round lower bound of each bracket.
#
# Superseded table: exemption at 100.000 Kz with a 13% bracket. Retained ONLY so a site
# can reconstruct payroll from that era; it is never seeded automatically.
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

# Approved by Lei n.º 14/25 de 30 de Dezembro (OGE 2026), Anexo I (art. 21.º).
# The exemption threshold rose to 150.000 Kz and the 13% bracket was removed.
#
# EFFECTIVE DATE — three different dates exist and only one of them is the right one:
#   * 15-12-2025  approved by the National Assembly
#   * 30-12-2025  the date the law bears (Lei n.º 14/25 "de 30 de Dezembro")
#   * 01-01-2026  ENTRY INTO FORCE — "A presente Lei entra em vigor a 1 de Janeiro de
#                 2026" (art. 43.º), confirmed independently by KPMG Angola's OGE 2026
#                 note. This is the date payroll must use.
# Phase 1 seeded 2025-12-30, which is the law's own date, not the date it took effect.
# The difference is not academic: a December-2025 payroll period would have resolved the
# 2026 table and taxed that month at the new exemption, two days early.
ANGOLA_IRT_2026 = [
	(0, 150000, 0.0, 0),
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
ANGOLA_IRT_2026_EFFECTIVE_FROM = "2026-01-01"

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
	ensure_roles()
	seed_payroll_controls()
	seed_hr_controls()
	seed_hr_catalogues()
	setup_custom_fields()
	seed_defaults()
	seed_absence_reasons()
	create_payslip_print_format()


# Segregation-of-duties roles. HR User, HR Manager and Accounts Manager already exist in
# ERPNext and are deliberately reused rather than duplicated.
PAYROLL_ROLES = {
	"Payroll Officer": "Prepares payroll: creates the run, resolves exceptions and submits "
	                   "it for approval. Cannot approve, post or pay.",
	"Payroll Manager": "Reviews and approves (or rejects) calculated payroll. Cannot post "
	                   "accounting or authorise payment.",
	"Payroll Finance Approver": "Posts approved payroll to the ledger, releases it for "
	                            "payment and generates the bank file.",
}


#: Applied ONCE, the first time a site reaches Phase 2. Marker-guarded so that an
#: administrator who deliberately relaxes a control is never overruled by a later migrate.
PAYROLL_CONTROL_DEFAULTS = {
	"require_separate_payroll_approval": 1,
	"require_separate_payment_approval": 1,
	"variance_threshold_percent": 25,
}
_CONTROLS_SEEDED = "isoft_ahr_payroll_controls_seeded"

#: Phase 3 HR controls, seeded the same way and for the same reason: a Check field added
#: by a migration sits at 0, which for these fields means "approval control disabled".
HR_CONTROL_DEFAULTS = {
	"require_separate_contract_approval": 1,
	"require_separate_salary_change_approval": 1,
	"require_separate_advance_approval": 1,
	"contract_expiry_thresholds": "90,60,30,15,7",
	"document_expiry_thresholds": "90,60,30,15,7",
	"probation_review_window_days": 30,
}
_HR_CONTROLS_SEEDED = "isoft_ahr_hr_controls_seeded"


def seed_payroll_controls():
	"""Give the new segregation-of-duties settings their intended value on upgrade.

	Adding a Check field through a migration leaves it at 0 on an existing Singles
	record, which for these particular fields means "segregation switched off" — the
	opposite of the safe default. They are therefore seeded explicitly, once.
	"""
	if frappe.db.get_default(_CONTROLS_SEEDED):
		return
	settings = frappe.get_single("Isoft HR Settings")
	for field, value in PAYROLL_CONTROL_DEFAULTS.items():
		settings.set(field, value)
	settings.save(ignore_permissions=True)
	frappe.db.set_default(_CONTROLS_SEEDED, "1")
	frappe.db.commit()


def seed_hr_controls():
	if frappe.db.get_default(_HR_CONTROLS_SEEDED):
		return
	settings = frappe.get_single("Isoft HR Settings")
	for field, value in HR_CONTROL_DEFAULTS.items():
		settings.set(field, value)
	settings.save(ignore_permissions=True)
	frappe.db.set_default(_HR_CONTROLS_SEEDED, "1")
	frappe.db.commit()


# Starter catalogues. Seeded on FRESH INSTALL only — never re-created if the site has
# already customised or deleted them.
DEFAULT_CONTRACT_TYPES = [
	# (name, portuguese, fixed_term, months, probation_days, notice_days, renewable)
	("Permanent Contract", "Contrato por Tempo Indeterminado", 0, 0, 60, 30, 0),
	("Fixed Term Contract", "Contrato por Tempo Determinado", 1, 12, 30, 30, 1),
	("Internship", "Estágio", 1, 6, 0, 15, 1),
	("Service Contract", "Contrato de Prestação de Serviços", 1, 12, 0, 30, 1),
	("Temporary Contract", "Contrato Temporário", 1, 3, 15, 15, 1),
]

DEFAULT_DOCUMENT_TYPES = [
	# (name, portuguese, requires_expiry, mandatory, confidential, medical)
	("National ID (BI)", "Bilhete de Identidade", 1, 1, 0, 0),
	("Passport", "Passaporte", 1, 0, 0, 0),
	("NIF Document", "Documento de NIF", 0, 1, 0, 0),
	("Social Security Document", "Documento de Segurança Social", 0, 1, 0, 0),
	("Employment Contract", "Contrato de Trabalho", 0, 1, 1, 0),
	("Academic Certificate", "Certificado Académico", 0, 0, 0, 0),
	("Work Permit", "Autorização de Trabalho", 1, 0, 0, 0),
	("Medical Certificate", "Atestado Médico", 1, 0, 1, 1),
	("Criminal Record", "Registo Criminal", 1, 0, 1, 0),
	("Other", "Outro Documento", 0, 0, 0, 0),
]


def seed_hr_catalogues():
	"""Create the starter contract and document types.

	LEGAL VERIFICATION REQUIRED — the durations, probation and notice values are ordinary
	configuration defaults so HR has somewhere to start. They are NOT a statement of
	Angolan labour law, and the application derives no right or entitlement from them.
	"""
	for name, pt, fixed, months, probation, notice, renewable in DEFAULT_CONTRACT_TYPES:
		if frappe.db.exists("Isoft Contract Type", name):
			continue
		frappe.get_doc({
			"doctype": "Isoft Contract Type", "contract_type": name, "contract_type_pt": pt,
			"is_fixed_term": fixed, "default_duration_months": months,
			"default_probation_days": probation, "default_notice_days": notice,
			"renewable": renewable,
		}).insert(ignore_permissions=True)

	for name, pt, expiry, mandatory, confidential, medical in DEFAULT_DOCUMENT_TYPES:
		if frappe.db.exists("Isoft Document Type", name):
			continue
		frappe.get_doc({
			"doctype": "Isoft Document Type", "document_type": name, "document_type_pt": pt,
			"requires_expiry": expiry, "is_mandatory": mandatory,
			"is_confidential": confidential, "is_medical": medical,
		}).insert(ignore_permissions=True)
	frappe.db.commit()


def ensure_roles():
	"""Create the payroll roles. Idempotent, and it never assigns them to anybody.

	Granting the new roles automatically to existing HR Managers would recreate exactly
	the concentration of duties this phase exists to break, so role assignment is left to
	the administrator as a deliberate act.
	"""
	for role in PAYROLL_ROLES:
		if frappe.db.exists("Role", role):
			continue
		frappe.get_doc({
			"doctype": "Role", "role_name": role, "desk_access": 1,
			"search_bar": 1, "notifications": 1, "list_sidebar": 1, "disabled": 0,
		}).insert(ignore_permissions=True)
	frappe.db.commit()


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


def ensure_currency(code="AOA"):
	"""Make sure the app's currency exists on the site, so Currency links resolve on a
	fresh install (some sites don't ship every ISO currency). Returns the code if present."""
	if not frappe.db.exists("Currency", code):
		try:
			frappe.get_doc({
				"doctype": "Currency", "currency_name": code, "enabled": 1,
				"symbol": "Kz" if code == "AOA" else code, "fraction": "Cêntimo", "fraction_units": 100,
			}).insert(ignore_permissions=True)
		except Exception:
			return None
	return code


def seed_defaults():
	"""Seed the current Angola IRT table and baseline Settings.

	FRESH INSTALL ONLY. An existing table is never rewritten: statutory configuration is
	historical evidence, and a migration that silently replaced it would retroactively
	change how past payroll can be explained. Sites that need a newer table create it
	from the dashboard, which produces a new effective-dated version.
	"""
	currency = ensure_currency("AOA")

	if not frappe.db.exists("IRT Table", DEFAULT_IRT_TABLE):
		doc = frappe.get_doc({
			"doctype": "IRT Table",
			"title": DEFAULT_IRT_TABLE,
			"effective_from": ANGOLA_IRT_2026_EFFECTIVE_FROM,
			"currency": currency,  # None if the currency couldn't be created — avoids link errors
			"brackets": [
				{
					"from_amount": fr,
					"to_amount": to,
					"excess_over": (fr - 1) if fr else 0,
					"rate": rate,
					"parcela_fixa": pf,
				}
				for (fr, to, rate, pf) in ANGOLA_IRT_2026
			],
		})
		doc.insert(ignore_permissions=True)

	settings = frappe.get_single("Isoft HR Settings")
	changed = False
	if currency and not settings.currency:
		settings.currency = currency
		changed = True
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
		# a copy created while developer_mode was on is flagged standard="Yes",
		# which Print Format.validate() refuses to update outside developer_mode
		doc.standard = "No"
		doc.flags.ignore_validate_update_after_submit = True
		try:
			doc.save(ignore_permissions=True)
		except frappe.ValidationError:
			frappe.db.set_value(
				"Print Format",
				PAYSLIP_FORMAT,
				{
					"standard": "No",
					"custom_format": 1,
					"print_format_type": "Jinja",
					"html": PAYSLIP_HTML,
				},
				update_modified=False,
			)
			frappe.clear_cache(doctype="Isoft Salary Slip")
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
