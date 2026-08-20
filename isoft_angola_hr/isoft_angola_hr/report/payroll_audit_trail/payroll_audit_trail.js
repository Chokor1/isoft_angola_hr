// Copyright (c) 2026, ISOFT LDA
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Payroll Audit Trail"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "status", label: __("Status"), fieldtype: "Select",
			options: "\nDraft\nCalculated\nPending Approval\nRejected\nApproved\nPosted\nPayment Ready\nPaid\nClosed\nCancelled",
		},
	],
};
