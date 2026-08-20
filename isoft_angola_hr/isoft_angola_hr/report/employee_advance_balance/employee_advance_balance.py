# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""What each employee still owes on a salary advance, and when it will be recovered."""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.ADVANCE_REQUEST, filters)
	filters = filters or {}
	conditions, values = ["1=1"], []
	if filters.get("company"):
		conditions.append("a.company = %s")
		values.append(filters["company"])
	if filters.get("employee"):
		conditions.append("a.employee = %s")
		values.append(filters["employee"])
	if filters.get("status"):
		conditions.append("a.status = %s")
		values.append(filters["status"])
	else:
		conditions.append("a.status in ('Approved', 'Disbursed', 'Recovering')")
	scope, scope_values = perms.company_filter_sql(alias="a")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	rows = frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, e.department, a.request_date,
			a.approved_amount, a.recovered_amount, a.outstanding_amount, a.installments,
			a.installment_amount, a.status, a.disbursement_entry,
			(select min(i.period_start) from `tabIsoft Advance Installment` i
			  where i.parent = a.name and i.status in ('Pending', 'Partial')) as next_due
		from `tabIsoft Salary Advance` a
		left join `tabEmployee` e on e.name = a.employee
		where {0} order by a.request_date""".format(" and ".join(conditions)),
		values, as_dict=True)

	total = ru.totals_row(rows, ("approved_amount", "recovered_amount", "outstanding_amount"),
	                      "employee_name")
	if total:
		rows.append(total)
	return _columns(), rows


def _columns():
	return [
		ru.column(_("Advance"), "name", "Link", 120, "Isoft Salary Advance"),
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Requested"), "request_date", "Date", 100),
		ru.money(_("Approved"), "approved_amount", 130),
		ru.money(_("Recovered"), "recovered_amount", 130),
		ru.money(_("Outstanding"), "outstanding_amount", 130),
		ru.column(_("Installments"), "installments", "Int", 100),
		ru.money(_("Per Installment"), "installment_amount", 130),
		ru.column(_("Next Due Period"), "next_due", "Date", 120),
		ru.column(_("Status"), "status", "Data", 110),
		ru.column(_("Disbursement"), "disbursement_entry", "Link", 150, "Journal Entry"),
	]
