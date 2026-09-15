# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Install / migrate setup for the Isoft Angola HR app.

Adds Angola-specific custom fields onto the reused ERPNext core doctypes
(Employee, Attendance, Timesheet) and ensures baseline configuration exists.
Idempotent: safe to run on every install and migrate.
"""

import io
import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint


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

#: The receipt's template and stylesheet are files, next to the Print Format's own JSON
#: fixture, so they can be reviewed and diffed like code. ``create_payslip_print_format``
#: copies them into the Print Format record on every install and migrate.
PAYSLIP_TEMPLATE_DIR = os.path.join(
	os.path.dirname(os.path.abspath(__file__)), "print_format", "recibo_de_vencimento")
PAYSLIP_HTML_FILE = os.path.join(PAYSLIP_TEMPLATE_DIR, "recibo_de_vencimento.html")
PAYSLIP_CSS_FILE = os.path.join(PAYSLIP_TEMPLATE_DIR, "recibo_de_vencimento.css")


def payslip_template():
	"""(html, css) of the receipt, read from the files above."""
	with io.open(PAYSLIP_HTML_FILE, encoding="utf-8") as f:
		html = f.read()
	with io.open(PAYSLIP_CSS_FILE, encoding="utf-8") as f:
		css = f.read()
	return html, css


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
	seed_finance_controls()
	seed_hr_controls()
	seed_hr_catalogues()
	setup_custom_fields()
	seed_defaults()
	seed_absence_reasons()
	grant_finance_read_access()
	grant_capability_docperms()
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
	"require_separate_settlement_approval": 1,
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


#: The HR / Finance boundary (audit finding F-03). Self-approval off, both Finance rules
#: on. Seeded once, like the other controls: syncing the DocType writes every new Check
#: of a Single as 0, and for these three fields 0 would mean "HR may post and pay".
FINANCE_CONTROL_DEFAULTS = {
	"allow_hr_self_approval": 0,
	"payroll_posting_requires_finance": 1,
	"payroll_payment_requires_finance": 1,
}
_FINANCE_CONTROLS_SEEDED = "isoft_angola_hr:finance_controls_seeded"


def seed_finance_controls():
	"""Give the F-03 switches their intended value the first time a site sees them."""
	if frappe.db.get_default(_FINANCE_CONTROLS_SEEDED):
		return
	settings = frappe.get_single("Isoft HR Settings")
	for field, value in FINANCE_CONTROL_DEFAULTS.items():
		settings.set(field, value)
	settings.save(ignore_permissions=True)
	frappe.db.set_default(_FINANCE_CONTROLS_SEEDED, "1")
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


#: Roles that need READ on Account, and why. Nothing here is ever granted write.
_ACCOUNT_READERS = (
	# Posts payroll and disburses advances. ERPNext's Journal Entry.validate() calls
	# get_balance_on(), which reads the Account record directly, and that read is not
	# covered by the voucher's own ignore_permissions.
	"Payroll Finance Approver",
	# Holds SETTINGS_WRITE (permissions.ACTION_ROLES), and it is the ONLY role that does.
	# The settings it owns are the payroll account mappings — payroll_payable_account,
	# salary_payment_account and the per-component accounts — every one of them an Account
	# Link field. Without read, frappe.client.validate_link and search_link both return
	# 403, so the one role allowed to map payroll to the chart of accounts could not
	# search for an account or save a mapping it had typed.
	"HR Manager",
)


def grant_finance_read_access():
	"""Give the roles that need it READ on Account — and only read.

	Each role in ``_ACCOUNT_READERS`` carries a duty the app assigned it that cannot be
	discharged without resolving an Account. Neither can open Journal Entry or Payment
	Entry, so neither can post to the ledger except through the payroll flow that
	authorised it.

	Deliberately NOT granted: Journal Entry / Payment Entry create or submit, and any
	write, create or delete on Account.
	"""
	from frappe.permissions import add_permission, update_permission_property

	for role in _ACCOUNT_READERS:
		if not frappe.db.exists("Role", role):
			continue
		if frappe.db.exists("Custom DocPerm", {"parent": "Account", "role": role}):
			continue
		add_permission("Account", role, 0)
		for prop in ("write", "create", "delete", "submit", "cancel", "amend"):
			update_permission_property("Account", role, 0, prop, 0)
		update_permission_property("Account", role, 0, "read", 1)
		update_permission_property("Account", role, 0, "report", 1)
	frappe.db.commit()


#: A capability the app grants is only half a permission. ``perms.require(...)`` decides
#: whether the APPLICATION agrees; the write that follows still goes through Frappe's
#: DocPerms, and when the two disagree the user is shown a bare 403 with no message — the
#: button is offered, the guard allows it, and the framework refuses. Every entry below was
#: found that way: a role that holds the capability, performing the action the capability
#: names, being refused by the framework.
#:
#: Format: (doctype, role, {permission: value}, why).
_CAPABILITY_DOCPERMS = (
	# EMPLOYEE_WRITE is HR User + HR Manager. The console's "Edit Details" dialog carries a
	# Designation Link, and its control resolves the link before the form will save, so
	# without read the one dialog that edits an employee cannot be saved at all.
	("Designation", "HR Manager", {"read": 1}, "Employee edit dialog resolves this Link"),

	# RECRUITMENT_OPERATE is HR User + HR Manager, but only HR User carries the DocPerms,
	# so every recruitment entry point (create_job_opening / _applicant / _offer) refused
	# the manager who is supposed to run recruitment.
	("Job Opening", "HR Manager", {"read": 1, "write": 1, "create": 1},
	 "recruitment.operate"),
	("Job Applicant", "HR Manager", {"read": 1, "write": 1, "create": 1},
	 "recruitment.operate"),
	# create_job_offer refused the manager with a bare "does not have access to this
	# document": Job Offer carried HR User alone, so the recruitment flow stopped one step
	# short of the offer it exists to produce.
	("Job Offer", "HR Manager", {"read": 1, "write": 1, "create": 1}, "recruitment.operate"),

	# PERFORMANCE_OPERATE lists HR Manager, but the two ERPNext doctypes the cycle is
	# built from carried HR User alone. A cycle needs a template, so the manager could not
	# create the template, could not create the cycle, and the whole performance process
	# was unreachable for the role that owns it.
	("Appraisal Template", "HR Manager", {"read": 1, "write": 1, "create": 1},
	 "performance.operate"),
	("Appraisal", "HR Manager", {"read": 1, "write": 1, "create": 1}, "performance.operate"),

	# ATTENDANCE_WRITE is HR User + Payroll Officer + HR Manager. Attendance is submittable
	# and mark_attendance submits it, so the officer needs submit and cancel as well as
	# create — otherwise the officer's own attendance screen cannot record a day.
	("Attendance", "Payroll Officer",
	 {"read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1}, "attendance.write"),

	# PAYROLL_PREPARE is Payroll Officer + HR Manager: preparing a final settlement is the
	# officer's job, and creating one was refused. Deliberately NOT granted: submit, cancel
	# or delete — approving and reversing a settlement stay with HR Manager.
	("Isoft Final Settlement", "Payroll Officer", {"read": 1, "write": 1, "create": 1},
	 "payroll.prepare"),
)

#: Fields at permlevel > 0 are governed separately: Frappe silently DISCARDS a change to
#: such a field when the user cannot write that level, so the save appears to succeed and
#: the value simply does not move. Leave Application.status is permlevel 1 here, writable
#: by HR User and Leave Approver but not by HR Manager — and LEAVE_APPROVE is HR Manager
#: ALONE. The only role permitted to approve leave could not: the status reverted to Open
#: and the submit then failed with "Only Leave Applications with status 'Approved' and
#: 'Rejected' can be submitted", which names neither the real cause nor the field.
_CAPABILITY_PERMLEVELS = (
	("Leave Application", "HR Manager", 1, {"read": 1, "write": 1}, "leave.approve"),
)


def grant_capability_docperms():
	"""Give each role the DocPerms its capabilities already imply — and nothing more.

	Idempotent, and additive only: an existing permission is never lowered, so an
	administrator who has deliberately widened something is not overruled by a migrate.
	"""
	from frappe.permissions import add_permission, update_permission_property

	for doctype, role, props, _why in _CAPABILITY_DOCPERMS:
		if not (frappe.db.exists("DocType", doctype) and frappe.db.exists("Role", role)):
			continue
		add_permission(doctype, role, 0)
		for prop, value in props.items():
			if not _has_perm(doctype, role, 0, prop):
				update_permission_property(doctype, role, 0, prop, value)

	for doctype, role, level, props, _why in _CAPABILITY_PERMLEVELS:
		if not (frappe.db.exists("DocType", doctype) and frappe.db.exists("Role", role)):
			continue
		add_permission(doctype, role, level)
		for prop, value in props.items():
			if not _has_perm(doctype, role, level, prop):
				update_permission_property(doctype, role, level, prop, value)
	frappe.db.commit()


def _has_perm(doctype, role, permlevel, prop):
	"""True when the effective DocPerm already allows ``prop``.

	Custom DocPerm rows REPLACE the standard ones for a doctype rather than adding to them,
	so the effective table has to be chosen the same way Frappe chooses it.
	"""
	table = "Custom DocPerm" if frappe.db.count("Custom DocPerm", {"parent": doctype}) \
		else "DocPerm"
	rows = frappe.get_all(table, filters={"parent": doctype, "role": role,
	                                      "permlevel": permlevel}, fields=[prop])
	return any(cint(r.get(prop)) for r in rows)


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
	"""Install or refresh the "Recibo de Vencimento" Print Format from its template files.

	Runs on every install and migrate, so the record on the site always carries the
	template and stylesheet shipped with the app. The format is deliberately
	standard="No": Print Format.validate() refuses to save a standard format outside
	developer_mode, which would make it read-only on a production site.
	"""
	html, css = payslip_template()
	values = {
		"doc_type": "Isoft Salary Slip",
		"module": "Isoft Angola HR",
		"print_format_type": "Jinja",
		"custom_format": 1,
		"standard": "No",
		"disabled": 0,
		"html": html,
		"css": css,
	}
	if frappe.db.exists("Print Format", PAYSLIP_FORMAT):
		doc = frappe.get_doc("Print Format", PAYSLIP_FORMAT)
		doc.update(values)
		doc.flags.ignore_validate_update_after_submit = True
		try:
			doc.save(ignore_permissions=True)
		except frappe.ValidationError:
			# a copy created while developer_mode was on is flagged standard="Yes",
			# which Print Format.validate() refuses to update outside developer_mode
			frappe.db.set_value("Print Format", PAYSLIP_FORMAT, values, update_modified=False)
			frappe.clear_cache(doctype="Isoft Salary Slip")
	else:
		doc = frappe.get_doc(dict(values, doctype="Print Format", name=PAYSLIP_FORMAT))
		doc.insert(ignore_permissions=True)
	frappe.db.commit()
