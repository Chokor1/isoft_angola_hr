// Copyright (c) 2026, ISOFT LDA
/* eslint-disable */

frappe.query_reports["Employee Advance Balance"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
		  default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
	],
};
