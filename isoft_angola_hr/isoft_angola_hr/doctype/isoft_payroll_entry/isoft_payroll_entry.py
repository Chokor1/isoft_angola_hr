# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class IsoftPayrollEntry(Document):
	@frappe.whitelist()
	def fill_employees(self):
		"""Populate the employees table with active employees (filtered) that have a
		Salary Profile effective in the period."""
		filters = {"status": "Active", "company": self.company}
		for f in ("branch", "department", "designation"):
			if self.get(f):
				filters[f] = self.get(f)

		employees = frappe.get_all("Employee", filters=filters, fields=["name", "employee_name"])
		self.set("employees", [])
		count = 0
		for emp in employees:
			profile = frappe.get_all(
				"Isoft Salary Profile",
				filters={"employee": emp.name, "from_date": ("<=", getdate(self.end_date))},
				fields=["name"],
				order_by="from_date desc",
				limit=1,
			)
			if not profile:
				continue
			self.append("employees", {
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"salary_profile": profile[0].name,
			})
			count += 1
		self.number_of_employees = count
		return count

	@frappe.whitelist()
	def create_salary_slips(self):
		created = 0
		for row in self.employees:
			existing = frappe.db.exists(
				"Isoft Salary Slip",
				{"employee": row.employee, "start_date": self.start_date, "end_date": self.end_date,
				 "docstatus": ("<", 2)},
			)
			if existing:
				row.salary_slip = existing
				continue
			slip = frappe.get_doc({
				"doctype": "Isoft Salary Slip",
				"employee": row.employee,
				"company": self.company,
				"posting_date": self.posting_date,
				"start_date": self.start_date,
				"end_date": self.end_date,
				"payroll_entry": self.name,
				"salary_profile": row.salary_profile,
				"productivity_bonus": flt(row.productivity_bonus),
				"overtime_amount": flt(row.overtime_amount),
				"adiantamento": flt(row.adiantamento),
				"validate_attendance": self.validate_attendance,
				"based_on_timesheet": self.based_on_timesheet,
			})
			slip.insert(ignore_permissions=True)
			row.salary_slip = slip.name
			created += 1
		self.salary_slips_created = 1
		self.update_totals()
		self.save()
		frappe.msgprint(_("{0} salary slip(s) created.").format(created))
		return created

	@frappe.whitelist()
	def submit_salary_slips(self):
		submitted = 0
		for row in self.employees:
			if not row.salary_slip:
				continue
			slip = frappe.get_doc("Isoft Salary Slip", row.salary_slip)
			if slip.docstatus == 0:
				slip.submit()
				submitted += 1
		self.salary_slips_submitted = 1
		self.update_totals()
		self.save()
		frappe.msgprint(_("{0} salary slip(s) submitted.").format(submitted))
		return submitted

	def update_totals(self):
		total = 0.0
		for row in self.employees:
			if row.salary_slip:
				row.net_pay = flt(frappe.db.get_value("Isoft Salary Slip", row.salary_slip, "net_pay"))
				total += row.net_pay
		self.total_net_pay = total
