// Copyright (c) 2026, ISOFT LDA
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Payroll Bank Payment List"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			reqd: 1, default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -1),
		},
		{
			fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1,
			default: frappe.datetime.month_end(),
		},
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{
			fieldname: "payroll_entry", label: __("Payroll Entry"), fieldtype: "Link",
			options: "Isoft Payroll Entry",
		},
		{
			fieldname: "docstatus", label: __("Payroll Status"), fieldtype: "Select",
			options: "Submitted\nDraft\nAll", default: "Submitted",
		},
	],
};
