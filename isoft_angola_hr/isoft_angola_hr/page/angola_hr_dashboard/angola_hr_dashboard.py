# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, get_first_day, get_last_day, nowdate


@frappe.whitelist()
def get_dashboard_data(company=None):
	"""Return headline stats for the Angola HR dashboard."""
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
