# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Employee documents approaching or past their expiry date.

Confidential and medical documents are listed only for HR Managers; everyone else sees
the rest. The document NUMBER is never printed — an expiry report does not need to
reproduce passport and ID numbers to do its job.
"""

import frappe
from frappe import _
from frappe.utils import cint

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.DOCUMENT_READ, filters)
	filters = filters or {}
	conditions = ["d.expiry_date is not null"]
	values = []
	if filters.get("company"):
		conditions.append("e.company = %s")
		values.append(filters["company"])
	if filters.get("department"):
		conditions.append("e.department = %s")
		values.append(filters["department"])
	if filters.get("status"):
		conditions.append("d.status = %s")
		values.append(filters["status"])
	else:
		conditions.append("d.status in ('Expiring', 'Expired')")
	if not perms.can(perms.DOCUMENT_CONFIDENTIAL):
		conditions.append("ifnull(d.confidential, 0) = 0")
	scope, scope_values = perms.company_filter_sql(alias="e")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	rows = frappe.db.sql(
		"""select d.name, d.employee, d.employee_name, e.department, d.document_type,
			d.issue_date, d.expiry_date, d.status, d.confidential,
			datediff(d.expiry_date, curdate()) as days_left
		from `tabIsoft Employee Document` d
		join `tabEmployee` e on e.name = d.employee
		where {0} order by d.expiry_date""".format(" and ".join(conditions)),
		values, as_dict=True)

	for r in rows:
		r["days_left"] = cint(r["days_left"])
		r["action"] = _("EXPIRED — collect a replacement") if r["days_left"] < 0 \
			else _("Renew before {0}").format(r["expiry_date"])
	return _columns(), rows


def _columns():
	return [
		ru.column(_("Document"), "name", "Link", 130, "Isoft Employee Document"),
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 180),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Document Type"), "document_type", "Link", 170, "Isoft Document Type"),
		ru.column(_("Issued"), "issue_date", "Date", 95),
		ru.column(_("Expires"), "expiry_date", "Date", 95),
		ru.column(_("Days Left"), "days_left", "Int", 90),
		ru.column(_("Status"), "status", "Data", 100),
		ru.column(_("Confidential"), "confidential", "Check", 100),
		ru.column(_("Action"), "action", "Data", 220),
	]
