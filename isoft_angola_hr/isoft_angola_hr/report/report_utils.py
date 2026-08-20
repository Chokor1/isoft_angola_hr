# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Shared query plumbing for the payroll reports.

THE RULE THAT MATTERS: a submitted salary slip is reported exactly as it was
calculated. Every figure in the IRT and INSS reports comes from the statutory snapshot
stored on the slip — never from re-running today's IRT table or today's contribution
rates over yesterday's payroll. Re-deriving history would make a report of last year's
IRT change the moment a new tax law is loaded.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def guard(action, filters):
	"""Reports carry the same authorisation as the API: role plus company scope.

	Report visibility in the workspace is not a permission — a user who can guess the
	report name can run it — so the check happens inside ``execute``.
	"""
	perms.require(action)
	company = (filters or {}).get("company")
	if company:
		perms.require_company(company)


def slip_conditions(filters, alias="s"):
	"""WHERE fragment + values shared by the payroll reports.

	Defaults to submitted payroll only: a report that silently mixes drafts into a
	statutory total is a report that cannot be reconciled with the ledger.
	"""
	filters = filters or {}
	conds, values = [], []

	docstatus = filters.get("docstatus")
	if docstatus in ("Draft", 0, "0"):
		conds.append("{0}.docstatus = 0".format(alias))
	elif docstatus in ("All", "all"):
		conds.append("{0}.docstatus < 2".format(alias))
	else:
		conds.append("{0}.docstatus = 1".format(alias))

	if filters.get("company"):
		conds.append("{0}.company = %s".format(alias))
		values.append(filters["company"])
	if filters.get("from_date"):
		conds.append("{0}.end_date >= %s".format(alias))
		values.append(getdate(filters["from_date"]))
	if filters.get("to_date"):
		conds.append("{0}.end_date <= %s".format(alias))
		values.append(getdate(filters["to_date"]))
	if filters.get("employee"):
		conds.append("{0}.employee = %s".format(alias))
		values.append(filters["employee"])
	if filters.get("payroll_entry"):
		conds.append("{0}.payroll_entry = %s".format(alias))
		values.append(filters["payroll_entry"])
	if filters.get("department"):
		conds.append("e.department = %s")
		values.append(filters["department"])

	scope, scope_values = perms.company_filter_sql(alias=alias)
	if scope:
		conds.append(scope)
		values.extend(scope_values)

	return " and ".join(conds), values


PERIOD_FILTERS = [
	{"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company",
	 "reqd": 1},
	{"fieldname": "from_date", "label": _("From Date"), "fieldtype": "Date", "reqd": 1},
	{"fieldname": "to_date", "label": _("To Date"), "fieldtype": "Date", "reqd": 1},
	{"fieldname": "employee", "label": _("Employee"), "fieldtype": "Link", "options": "Employee"},
	{"fieldname": "department", "label": _("Department"), "fieldtype": "Link",
	 "options": "Department"},
	{"fieldname": "payroll_entry", "label": _("Payroll Entry"), "fieldtype": "Link",
	 "options": "Isoft Payroll Entry"},
	{"fieldname": "docstatus", "label": _("Payroll Status"), "fieldtype": "Select",
	 "options": "Submitted\nDraft\nAll", "default": "Submitted"},
]


def column(label, fieldname, fieldtype="Data", width=120, options=None):
	col = {"label": label, "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
	if options:
		col["options"] = options
	return col


def money(label, fieldname, width=120):
	return column(label, fieldname, "Currency", width)


def totals_row(data, fields, label_field, label=None):
	"""A TOTAL row appended to the dataset, so an exported file carries its own totals."""
	if not data:
		return None
	row = {label_field: label or _("TOTAL")}
	for f in fields:
		row[f] = sum(frappe.utils.flt(d.get(f)) for d in data)
	return row
