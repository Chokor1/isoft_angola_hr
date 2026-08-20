# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The payroll control document.

Phase 2 turned this from an operational batch helper into the document that governs the
whole payroll lifecycle. It owns the state (``status``), the audit trail, the approved
snapshot and the duplicate-run controls; the transitions themselves live in
``services/payroll_workflow.py`` so that every rule is defined once.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
	get_active_profile,
)
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


class IsoftPayrollEntry(Document):
	def validate(self):
		self.set_defaults()
		self.validate_period()
		self.validate_no_duplicate_run()

	def set_defaults(self):
		if not self.status:
			self.status = wf.DRAFT
		if not self.prepared_by:
			self.prepared_by = self.owner or frappe.session.user

	def validate_period(self):
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("The payroll End Date cannot be before the Start Date."))

	def validate_no_duplicate_run(self):
		"""One live payroll run per company + period + group.

		Two entries covering the same period could each generate and submit slips, and the
		second run would pay everybody a second time. The group field is the escape hatch
		for a company that genuinely runs separate payrolls (e.g. expatriates) in one period.
		"""
		clash = frappe.db.sql(
			"""select name, status from `tabIsoft Payroll Entry`
			where company=%s and start_date=%s and end_date=%s
			  and ifnull(payroll_group,'')=%s and ifnull(status,'Draft')!='Cancelled'
			  and name!=%s limit 1""",
			(self.company, getdate(self.start_date), getdate(self.end_date),
			 self.payroll_group or "", self.name or ""),
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("Payroll Entry {0} ({1}) already covers {2} to {3} for {4}. Cancel it, or set a "
				  "different Payroll Group, before creating a second run for the same period.").format(
					frappe.bold(clash[0].name), _(clash[0].status or wf.DRAFT),
					getdate(self.start_date), getdate(self.end_date), self.company),
				title=_("Duplicate Payroll Run"),
			)

	def on_trash(self):
		if wf.state_of(self) not in (wf.DRAFT, wf.CALCULATED, wf.REJECTED, wf.CANCELLED):
			frappe.throw(
				_("A payroll that has been {0} cannot be deleted — cancel it instead so the "
				  "audit trail survives.").format(_(wf.state_of(self))))

	# ------------------------------------------------------------------ #
	# Preparation
	# ------------------------------------------------------------------ #
	@frappe.whitelist()
	def fill_employees(self):
		"""Populate the employees table with active employees (filtered) that have a
		Salary Profile effective in the period."""
		perms.require_action_and_company(perms.PAYROLL_PREPARE, self.company)
		wf.assert_transition(self, wf.CALCULATE)

		filters = {"status": "Active", "company": self.company}
		for f in ("branch", "department", "designation"):
			if self.get(f):
				filters[f] = self.get(f)

		employees = frappe.get_all("Employee", filters=filters, fields=["name", "employee_name"])
		self.set("employees", [])
		count = 0
		skipped = []
		for emp in employees:
			# Resolved through get_active_profile so an ambiguous salary is reported here
			# rather than silently resolved by arbitrary row order.
			try:
				profile = get_active_profile(emp.name, getdate(self.end_date),
				                             company=self.company, employee_name=emp.employee_name)
			except frappe.ValidationError as exc:
				skipped.append("{0}: {1}".format(emp.employee_name or emp.name,
				                                 frappe.utils.strip_html(str(exc))))
				continue
			if not profile:
				continue
			self.append("employees", {
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"salary_profile": profile.name,
			})
			count += 1
		self.number_of_employees = count
		if skipped:
			frappe.msgprint(
				_("{0} employee(s) were excluded because their salary could not be "
				  "resolved:<br>{1}").format(len(skipped), "<br>".join(skipped)),
				title=_("Employees Excluded"), indicator="orange",
			)
		return count

	@frappe.whitelist()
	def create_salary_slips(self):
		"""Generate the salary slips and move the payroll to Calculated.

		Recalculating is allowed while the payroll is Draft, Calculated or Rejected — and
		refused once anybody has approved it, because the approved numbers must not change
		underneath the approval.
		"""
		perms.require_action_and_company(perms.PAYROLL_CALCULATE, self.company)
		wf.assert_transition(self, wf.CALCULATE)

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
				"subsidio_ferias": flt(row.subsidio_ferias),
				"subsidio_natal": flt(row.subsidio_natal),
				"validate_attendance": self.validate_attendance,
				"based_on_timesheet": self.based_on_timesheet,
			})
			slip.insert(ignore_permissions=True)
			row.salary_slip = slip.name
			created += 1
		self.salary_slips_created = 1
		self.update_totals()
		wf.perform(self, wf.CALCULATE)
		frappe.msgprint(_("{0} salary slip(s) created.").format(created))
		return created

	@frappe.whitelist()
	def submit_salary_slips(self):
		"""Submit the calculated slips. Allowed once the payroll has been approved —
		submission is the act of turning the approved calculation into payroll documents."""
		perms.require_company(self.company)
		state = wf.state_of(self)
		if state not in (wf.APPROVED, wf.POSTED, wf.PAYMENT_READY):
			frappe.throw(
				_("O processamento salarial ainda não foi aprovado. Salary slips may only be "
				  "submitted after approval — current state: {0}.").format(_(state)),
				title=_("Not Approved"))
		perms.require(perms.PAYROLL_POST)

		submitted = 0
		with wf.unlocked():
			for row in self.employees:
				if not row.salary_slip:
					continue
				slip = frappe.get_doc("Isoft Salary Slip", row.salary_slip)
				if slip.docstatus == 0:
					slip.submit()
					submitted += 1
			self.salary_slips_submitted = 1
			self.update_totals()
			self.save(ignore_permissions=True)
		frappe.msgprint(_("{0} salary slip(s) submitted.").format(submitted))
		return submitted

	def update_totals(self):
		total = 0.0
		for row in self.employees:
			if row.salary_slip:
				row.net_pay = flt(frappe.db.get_value("Isoft Salary Slip", row.salary_slip, "net_pay"))
				total += row.net_pay
		self.total_net_pay = total
		self.number_of_employees = len(self.employees)

	# ------------------------------------------------------------------ #
	# Review
	# ------------------------------------------------------------------ #
	def approval_summary(self):
		"""Everything an approver must see on one screen (section 22 of the brief)."""
		rows = wf.slip_rows(self)
		totals = wf.compute_totals(self, rows=rows)
		previous = self.previous_entry_totals()
		return {
			"name": self.name,
			"company": self.company,
			"status": wf.state_of(self),
			"start_date": str(getdate(self.start_date)),
			"end_date": str(getdate(self.end_date)),
			"totals": totals,
			"previous": previous,
			"difference": {
				k: flt(totals.get(k, 0) - (previous or {}).get(k, 0), 2)
				for k in ("gross", "employee_inss", "employer_inss", "irt", "net", "employer_cost")
			} if previous else None,
			"draft_slips": len([r for r in rows if cint(r["docstatus"]) == 0]),
			"submitted_slips": len([r for r in rows if cint(r["docstatus"]) == 1]),
			"negative_net": len([r for r in rows if flt(r["net_pay"]) < 0]),
			"approved": {
				"employees": cint(self.approved_employees),
				"gross": flt(self.approved_gross), "net": flt(self.approved_net),
				"irt": flt(self.approved_irt),
				"employee_inss": flt(self.approved_employee_inss),
				"employer_inss": flt(self.approved_employer_inss),
				"employer_cost": flt(self.approved_employer_cost),
				"fingerprint": self.approval_fingerprint,
			} if self.approval_fingerprint else None,
			"audit": {f: self.get(f) for f in (
				"prepared_by", "prepared_at", "submitted_by", "submitted_at", "approved_by",
				"approved_at", "rejected_by", "rejected_at", "rejection_reason", "posted_by",
				"posted_at", "payment_authorized_by", "payment_authorized_at", "paid_at",
				"closed_by", "closed_at", "cancelled_by", "cancelled_at",
				"exported_by", "exported_at", "export_count")},
			"allowed_actions": wf.allowed_actions(self),
			"next_step": wf.next_step(self),
		}

	def previous_entry_totals(self):
		"""Totals of the previous payroll run of the same company, for the difference line."""
		prev = frappe.db.sql(
			"""select name from `tabIsoft Payroll Entry`
			where company=%s and end_date < %s and ifnull(status,'Draft') not in ('Cancelled','Draft')
			order by end_date desc limit 1""",
			(self.company, getdate(self.start_date)), as_dict=True)
		if not prev:
			return None
		return wf.compute_totals(frappe.get_doc("Isoft Payroll Entry", prev[0].name))
