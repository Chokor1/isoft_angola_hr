# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe.utils import flt, get_first_day, get_last_day, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


@frappe.whitelist()
def get_dashboard_data(company=None):
	"""Return headline stats for the Angola HR dashboard.

	The Page is restricted to the HR/payroll roles, but a Page role list only governs
	who can OPEN the page — the endpoint behind it is callable directly by any
	authenticated user. Without this guard the headcount, the slip counts and
	``net_paid_this_month`` (the company's total net payroll for the month) were
	readable by any logged-in account, including one holding no roles at all.
	Guarded with the same action as its peer :func:`api.get_overview`.
	"""
	perms.require(perms.HR_ACCESS)
	if company:
		perms.require_company(company)

	start = get_first_day(nowdate())
	end = get_last_day(nowdate())

	emp_filters = {"status": "Active"}
	if company:
		emp_filters["company"] = company

	slip_filters = {"start_date": (">=", start), "end_date": ("<=", end)}
	if company:
		slip_filters["company"] = company

	net_paid = frappe.db.sql(
		"""select coalesce(sum(net_pay), 0) from `tabIsoft Salary Slip`
		where docstatus = 1 and start_date >= %s and end_date <= %s {comp}""".format(
			comp="and company = %s" if company else ""
		),
		([start, end, company] if company else [start, end]),
	)[0][0]

	return {
		"period": {"start": str(start), "end": str(end)},
		"active_employees": frappe.db.count("Employee", emp_filters),
		"salary_profiles": frappe.db.count("Isoft Salary Profile"),
		"draft_slips": frappe.db.count("Isoft Salary Slip", {**slip_filters, "docstatus": 0}),
		"submitted_slips": frappe.db.count("Isoft Salary Slip", {**slip_filters, "docstatus": 1}),
		"net_paid_this_month": flt(net_paid),
		"irt_tables": frappe.db.count("IRT Table", {"disabled": 0}),
		"currency": frappe.db.get_default("currency") or "AOA",
	}
