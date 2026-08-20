# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll Register — the monthly payroll review for Finance and Payroll.

One line per salary slip with the full cost picture: what the employee earned, what was
withheld, what they receive, and what the employer pays on top. Values come straight
from the slip, so a register for a closed period never changes.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

_TOTAL_FIELDS = ("basic", "allowances", "gross_pay", "ss_employee_amount", "irt_amount",
                 "other_deductions", "net_pay", "ss_employer_amount", "employer_cost")


def execute(filters=None):
	ru.guard(perms.REPORT_PAYROLL, filters)
	conditions, values = ru.slip_conditions(filters)

	rows = frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.department, s.start_date, s.end_date,
			s.gross_pay, s.total_deduction, s.net_pay, s.irt_amount, s.ss_employee_amount,
			s.ss_employer_amount, s.employer_cost, s.docstatus, s.journal_entry, s.payment_entry,
			s.payroll_entry,
			(select sum(d.amount) from `tabIsoft Salary Detail` d
			  where d.parent = s.name and d.parentfield = 'earnings' and d.abbr = 'SB') as basic
		from `tabIsoft Salary Slip` s
		left join `tabEmployee` e on e.name = s.employee
		where {0}
		order by e.department, s.employee_name""".format(conditions),
		values, as_dict=True,
	)

	data = []
	for r in rows:
		basic = flt(r.basic)
		gross = flt(r.gross_pay)
		data.append({
			"salary_slip": r.name,
			"employee": r.employee,
			"employee_name": r.employee_name,
			"department": r.department,
			"start_date": r.start_date,
			"end_date": r.end_date,
			"basic": basic,
			"allowances": flt(gross - basic, 2),
			"gross_pay": gross,
			"ss_employee_amount": flt(r.ss_employee_amount),
			"irt_amount": flt(r.irt_amount),
			"other_deductions": flt(flt(r.total_deduction) - flt(r.ss_employee_amount)
			                        - flt(r.irt_amount), 2),
			"net_pay": flt(r.net_pay),
			"ss_employer_amount": flt(r.ss_employer_amount),
			"employer_cost": flt(r.employer_cost),
			"payroll_entry": r.payroll_entry,
			"status": _status(r),
		})

	total = ru.totals_row(data, _TOTAL_FIELDS, "employee_name")
	if total:
		data.append(total)
	return _columns(), data


def _status(row):
	from isoft_angola_hr.isoft_angola_hr import api

	return api._slip_status(row.docstatus, row.journal_entry, row.payment_entry)


def _columns():
	return [
		ru.column(_("Salary Slip"), "salary_slip", "Link", 190, "Isoft Salary Slip"),
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("From"), "start_date", "Date", 95),
		ru.column(_("To"), "end_date", "Date", 95),
		ru.money(_("Basic Salary"), "basic"),
		ru.money(_("Allowances"), "allowances"),
		ru.money(_("Gross"), "gross_pay"),
		ru.money(_("Employee INSS"), "ss_employee_amount"),
		ru.money(_("IRT"), "irt_amount"),
		ru.money(_("Other Deductions"), "other_deductions"),
		ru.money(_("Net Pay"), "net_pay", 130),
		ru.money(_("Employer INSS"), "ss_employer_amount"),
		ru.money(_("Employer Total Cost"), "employer_cost", 150),
		ru.column(_("Payroll Entry"), "payroll_entry", "Link", 120, "Isoft Payroll Entry"),
		ru.column(_("Status"), "status", "Data", 100),
	]
