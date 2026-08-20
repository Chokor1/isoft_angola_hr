# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""A requested, justified and approved change to somebody's pay."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, now

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import salary_change as sc


class IsoftSalaryChange(Document):
	def validate(self):
		self.set_defaults()
		self.load_current_values()
		self.compute_change()
		self.validate_effective_date()

	def set_defaults(self):
		if not self.status:
			self.status = sc.DRAFT
		if not self.requested_by:
			self.requested_by = self.owner or frappe.session.user
		if not self.requested_at:
			self.requested_at = now()
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")

	def load_current_values(self):
		"""Snapshot what the employee earns today, so the approver sees before and after.

		Taken from the profile governing the day BEFORE the change, which is the one the
		new profile will actually replace.
		"""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
			get_active_profile,
		)

		if not (self.employee and self.effective_date):
			return
		try:
			current = get_active_profile(self.employee, add_days(getdate(self.effective_date), -1),
			                             company=self.company,
			                             employee_name=self.employee_name)
		except frappe.ValidationError:
			# An ambiguous salary is a pre-existing data problem; it must not stop HR from
			# recording the change they want, and it is caught again when applying.
			current = None
		if not current:
			return
		self.current_profile = current.name
		self.current_base = flt(current.base)
		self.current_food_allowance = flt(current.food_allowance)
		self.current_transport_allowance = flt(current.transport_allowance)
		self.current_family_allowance = flt(current.family_allowance)
		# Unspecified new allowances default to the current ones, so a base-only change
		# does not silently zero somebody's food and transport allowance.
		if self.is_new():
			for field in ("food_allowance", "transport_allowance", "family_allowance"):
				if self.get("new_" + field) is None:
					self.set("new_" + field, flt(current.get(field)))

	def compute_change(self):
		if flt(self.current_base):
			self.percentage_change = flt(
				(flt(self.new_base) - flt(self.current_base)) / flt(self.current_base) * 100.0, 2)
		else:
			self.percentage_change = 0
		if flt(self.new_base) < 0:
			frappe.throw(_("The new base salary cannot be negative."))

	def validate_effective_date(self):
		"""Catch a mid-period effective date at REQUEST time, not at payroll time."""
		if self.status in (sc.CANCELLED, sc.REJECTED) or not self.effective_date:
			return
		sc.assert_effective_date_is_a_period_boundary(
			self.employee, self.effective_date, self.employee_name)

	def on_trash(self):
		if self.status == sc.APPLIED:
			frappe.throw(_("An applied salary change cannot be deleted — it is the record of "
			               "why the Salary Profile exists."))

	@frappe.whitelist()
	def summary(self):
		perms.require(perms.SALARY_CHANGE_REQUEST)
		perms.require_company(self.company)
		return {
			"name": self.name, "status": self.status,
			"allowed_actions": sc.allowed_actions(self),
			"percentage_change": flt(self.percentage_change),
		}
