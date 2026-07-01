// Copyright (c) 2026, Abbass Chokor and contributors
// For license information, please see license.txt

frappe.ui.form.on("Isoft Payroll Entry", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Get Employees"), () => {
			frm.call("fill_employees").then(() => frm.refresh_field("employees") || frm.save());
		});

		if ((frm.doc.employees || []).length && !frm.doc.salary_slips_created) {
			frm.add_custom_button(__("Create Salary Slips"), () => {
				frm.call("create_salary_slips").then(() => frm.reload_doc());
			}).addClass("btn-primary");
		}

		if (frm.doc.salary_slips_created && !frm.doc.salary_slips_submitted) {
			frm.add_custom_button(__("Submit Salary Slips"), () => {
				frappe.confirm(__("Submit all created salary slips?"), () => {
					frm.call("submit_salary_slips").then(() => frm.reload_doc());
				});
			}).addClass("btn-primary");
		}
	},
});
