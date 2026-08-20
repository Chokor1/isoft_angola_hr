# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Relatório de IRT — Imposto sobre o Rendimento do Trabalho withheld per employee.

NOT AN OFFICIAL DECLARATION. This is an internal payroll report that shows the IRT
actually withheld, the taxable base it was computed on and the bracket that produced it.
No authoritative specification of the AGT electronic submission format was available, so
nothing here claims to be one — see section 33/70 of the Phase 2 brief.

Every value is the snapshot stored on the salary slip at calculation time: the taxable
base, the applicable bracket, its rate and parcela fixa. Loading a new IRT table never
changes a historical IRT report.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

_TOTAL_FIELDS = ("gross_pay", "ss_employee_amount", "taxable_income", "irt_amount", "net_pay")


def execute(filters=None):
	ru.guard(perms.REPORT_STATUTORY, filters)
	conditions, values = ru.slip_conditions(filters)

	rows = frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.department, e.custom_nif as nif,
			s.start_date, s.end_date, s.gross_pay, s.ss_employee_amount, s.taxable_income,
			s.irt_amount, s.net_pay, s.irt_table, s.irt_bracket_from, s.irt_bracket_to,
			s.irt_rate, s.irt_parcela_fixa
		from `tabIsoft Salary Slip` s
		left join `tabEmployee` e on e.name = s.employee
		where {0}
		order by e.department, s.employee_name""".format(conditions),
		values, as_dict=True,
	)

	data = []
	for r in rows:
		data.append({
			"employee": r.employee,
			"employee_name": r.employee_name,
			"nif": r.nif,
			"department": r.department,
			"end_date": r.end_date,
			"gross_pay": flt(r.gross_pay),
			"ss_employee_amount": flt(r.ss_employee_amount),
			"taxable_income": flt(r.taxable_income),
			"bracket": _bracket_label(r),
			"irt_rate": flt(r.irt_rate),
			"irt_parcela_fixa": flt(r.irt_parcela_fixa),
			"irt_amount": flt(r.irt_amount),
			"net_pay": flt(r.net_pay),
			"irt_table": r.irt_table,
			"salary_slip": r.name,
		})

	total = ru.totals_row(data, _TOTAL_FIELDS, "employee_name")
	if total:
		data.append(total)
	return _columns(), data


def _bracket_label(row):
	"""The bracket exactly as it was applied — blank on slips calculated before the
	statutory trace existed, rather than a bracket invented from today's table."""
	if row.irt_rate is None and not row.irt_bracket_from and not row.irt_bracket_to:
		return ""
	upper = flt(row.irt_bracket_to)
	return "{0} – {1}".format(
		frappe.format_value(flt(row.irt_bracket_from), {"fieldtype": "Currency"}),
		frappe.format_value(upper, {"fieldtype": "Currency"}) if upper else _("and above"))


def _columns():
	return [
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("NIF"), "nif", "Data", 110),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Period End"), "end_date", "Date", 95),
		ru.money(_("Gross Remuneration"), "gross_pay", 150),
		ru.money(_("INSS (Employee)"), "ss_employee_amount", 130),
		ru.money(_("IRT Taxable Base"), "taxable_income", 140),
		ru.column(_("Applicable Bracket"), "bracket", "Data", 180),
		ru.column(_("Rate (%)"), "irt_rate", "Percent", 90),
		ru.money(_("Parcela Fixa"), "irt_parcela_fixa"),
		ru.money(_("IRT Withheld"), "irt_amount", 130),
		ru.money(_("Net Salary"), "net_pay", 130),
		ru.column(_("IRT Table"), "irt_table", "Link", 160, "IRT Table"),
		ru.column(_("Salary Slip"), "salary_slip", "Link", 190, "Isoft Salary Slip"),
	]
