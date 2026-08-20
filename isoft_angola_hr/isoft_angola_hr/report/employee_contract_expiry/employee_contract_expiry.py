# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Which employment contracts end when, and which have no successor yet."""

import frappe
from frappe import _
from frappe.utils import cint

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.CONTRACT_READ, filters)
	filters = filters or {}
	conditions, values = ["1=1"], []
	if filters.get("company"):
		conditions.append("c.company = %s")
		values.append(filters["company"])
	if filters.get("department"):
		conditions.append("c.department = %s")
		values.append(filters["department"])
	if filters.get("status"):
		conditions.append("c.status = %s")
		values.append(filters["status"])
	else:
		conditions.append("c.status in ('Active', 'Expiring', 'Expired')")
	scope, scope_values = perms.company_filter_sql(alias="c")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	rows = frappe.db.sql(
		"""select c.name, c.employee, c.employee_name, c.department, c.designation,
			c.contract_type, c.start_date, c.end_date, c.is_open_ended, c.status,
			c.renewal_allowed, c.renewed_to, c.probation_end, c.probation_status,
			datediff(c.end_date, curdate()) as days_left
		from `tabIsoft Employment Contract` c
		where {0} order by c.is_open_ended, c.end_date""".format(" and ".join(conditions)),
		values, as_dict=True)

	data = []
	for r in rows:
		data.append({
			"contract": r.name, "employee": r.employee, "employee_name": r.employee_name,
			"department": r.department, "designation": r.designation,
			"contract_type": r.contract_type, "start_date": r.start_date,
			"end_date": None if cint(r.is_open_ended) else r.end_date,
			"open_ended": cint(r.is_open_ended),
			"days_left": None if cint(r.is_open_ended) else cint(r.days_left),
			"status": r.status, "renewable": cint(r.renewal_allowed),
			"renewed_to": r.renewed_to,
			"action": _("Renewed") if r.renewed_to else (
				_("Open ended") if cint(r.is_open_ended) else (
					_("EXPIRED") if cint(r.days_left) < 0 else _("Renew or let expire"))),
		})
	return _columns(), data


def _columns():
	return [
		ru.column(_("Contract"), "contract", "Link", 130, "Isoft Employment Contract"),
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Designation"), "designation", "Link", 140, "Designation"),
		ru.column(_("Contract Type"), "contract_type", "Link", 160, "Isoft Contract Type"),
		ru.column(_("Start"), "start_date", "Date", 95),
		ru.column(_("End"), "end_date", "Date", 95),
		ru.column(_("Open Ended"), "open_ended", "Check", 90),
		ru.column(_("Days Left"), "days_left", "Int", 90),
		ru.column(_("Status"), "status", "Data", 110),
		ru.column(_("Renewable"), "renewable", "Check", 90),
		ru.column(_("Renewed To"), "renewed_to", "Link", 130, "Isoft Employment Contract"),
		ru.column(_("Action"), "action", "Data", 170),
	]
