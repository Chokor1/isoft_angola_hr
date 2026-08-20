# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Mapa de INSS — Social Security contributions per employee.

NOT AN OFFICIAL DECLARATION. The INSS electronic submission format was not available for
verification, so this is an internal contribution map: the incidence base, both rates and
both contributions, exactly as calculated on each salary slip.

The rates shown are the ones the slip was calculated with, not today's rates — that is
the whole reason the slip stores them.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

_TOTAL_FIELDS = ("ss_base", "ss_employee_amount", "ss_employer_amount", "total_contribution")


def execute(filters=None):
	ru.guard(perms.REPORT_STATUTORY, filters)
	conditions, values = ru.slip_conditions(filters)

	rows = frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.department,
			e.custom_inss_number as inss_number, s.end_date, s.ss_base,
			s.ss_employee_rate, s.ss_employee_amount, s.ss_employer_rate, s.ss_employer_amount,
			s.statutory_rate
		from `tabIsoft Salary Slip` s
		left join `tabEmployee` e on e.name = s.employee
		where {0}
		order by e.department, s.employee_name""".format(conditions),
		values, as_dict=True,
	)

	data = []
	for r in rows:
		employee_amount = flt(r.ss_employee_amount)
		employer_amount = flt(r.ss_employer_amount)
		data.append({
			"employee": r.employee,
			"employee_name": r.employee_name,
			"inss_number": r.inss_number,
			"department": r.department,
			"end_date": r.end_date,
			"ss_base": flt(r.ss_base),
			"ss_employee_rate": flt(r.ss_employee_rate),
			"ss_employee_amount": employee_amount,
			"ss_employer_rate": flt(r.ss_employer_rate),
			"ss_employer_amount": employer_amount,
			"total_contribution": flt(employee_amount + employer_amount, 2),
			"statutory_rate": r.statutory_rate,
			"salary_slip": r.name,
		})

	total = ru.totals_row(data, _TOTAL_FIELDS, "employee_name")
	if total:
		data.append(total)
	return _columns(), data


def _columns():
	return [
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Social Security No."), "inss_number", "Data", 140),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Period End"), "end_date", "Date", 95),
		ru.money(_("Contribution Base"), "ss_base", 150),
		ru.column(_("Employee Rate (%)"), "ss_employee_rate", "Percent", 130),
		ru.money(_("Employee Contribution"), "ss_employee_amount", 160),
		ru.column(_("Employer Rate (%)"), "ss_employer_rate", "Percent", 130),
		ru.money(_("Employer Contribution"), "ss_employer_amount", 160),
		ru.money(_("Total Contribution"), "total_contribution", 150),
		ru.column(_("Statutory Rate"), "statutory_rate", "Link", 140, "Isoft Statutory Rate"),
		ru.column(_("Salary Slip"), "salary_slip", "Link", 190, "Isoft Salary Slip"),
	]
