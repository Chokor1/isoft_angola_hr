# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Every pay change, who asked for it, who approved it and what it was worth."""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	# Compensation history is payroll data, so it needs the salary-read permission and
	# not merely the HR role that can request a change.
	ru.guard(perms.SALARY_PROFILE_READ, filters)
	filters = filters or {}
	conditions, values = ["1=1"], []
	if filters.get("company"):
		conditions.append("s.company = %s")
		values.append(filters["company"])
	if filters.get("employee"):
		conditions.append("s.employee = %s")
		values.append(filters["employee"])
	if filters.get("from_date"):
		conditions.append("s.effective_date >= %s")
		values.append(filters["from_date"])
	if filters.get("to_date"):
		conditions.append("s.effective_date <= %s")
		values.append(filters["to_date"])
	if filters.get("status"):
		conditions.append("s.status = %s")
		values.append(filters["status"])
	scope, scope_values = perms.company_filter_sql(alias="s")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	rows = frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.department, s.change_type,
			s.effective_date, s.current_base, s.new_base, s.percentage_change, s.status,
			s.reason, s.requested_by, s.approved_by, s.created_profile
		from `tabIsoft Salary Change` s
		left join `tabEmployee` e on e.name = s.employee
		where {0} order by s.effective_date desc""".format(" and ".join(conditions)),
		values, as_dict=True)

	for r in rows:
		r["increase"] = flt(flt(r.new_base) - flt(r.current_base), 2)
		r["segregated"] = 1 if (r.requested_by and r.approved_by
		                        and r.requested_by != r.approved_by) else 0
	total = ru.totals_row(rows, ("current_base", "new_base", "increase"), "employee_name")
	if total:
		rows.append(total)
	return _columns(), rows


def _columns():
	return [
		ru.column(_("Change"), "name", "Link", 120, "Isoft Salary Change"),
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Type"), "change_type", "Data", 120),
		ru.column(_("Effective"), "effective_date", "Date", 100),
		ru.money(_("Previous Base"), "current_base", 130),
		ru.money(_("New Base"), "new_base", 130),
		ru.money(_("Increase"), "increase", 120),
		ru.column(_("Change (%)"), "percentage_change", "Percent", 100),
		ru.column(_("Status"), "status", "Data", 100),
		ru.column(_("Requested By"), "requested_by", "Link", 160, "User"),
		ru.column(_("Approved By"), "approved_by", "Link", 160, "User"),
		ru.column(_("Duties Separated"), "segregated", "Check", 120),
		ru.column(_("Reason"), "reason", "Data", 240),
	]
