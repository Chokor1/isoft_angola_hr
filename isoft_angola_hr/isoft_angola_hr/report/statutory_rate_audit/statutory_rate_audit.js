// Copyright (c) 2026, ISOFT LDA
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Statutory Rate Audit"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
		},
	],
};
