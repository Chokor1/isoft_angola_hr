# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class IsoftManagerDelegation(Document):
	def validate(self):
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The delegation end date cannot be before its start date."))
		if self.delegator == self.delegate:
			frappe.throw(_("A manager cannot delegate to themselves."))
		self.delegator_name = frappe.db.get_value("Employee", self.delegator, "employee_name")
		self.delegate_name = frappe.db.get_value("Employee", self.delegate, "employee_name")
		if not self.created_by_user:
			self.created_by_user = frappe.session.user
		self.validate_no_chain()
		self.validate_no_overlap()

	def validate_no_chain(self):
		"""A delegate may not delegate onward.

		Chained delegation is how a temporary cover arrangement quietly becomes a permanent
		one that nobody can trace back to a decision.
		"""
		# The chain runs the other way: the person delegating must not themselves be
		# acting under a delegation. Checking the delegate instead (the first version of
		# this) blocked the wrong case and allowed the real one.
		acting = frappe.db.exists("Isoft Manager Delegation", {
			"delegate": self.delegator, "status": "Active", "name": ("!=", self.name or "")})
		if acting:
			frappe.throw(
				_("{0} is already acting under delegation {1}. A delegate cannot delegate "
				  "onward.").format(self.delegator_name, acting))

	def validate_no_overlap(self):
		clash = frappe.db.sql("""select name from `tabIsoft Manager Delegation`
			where delegator = %s and status = 'Active' and name != %s
			  and from_date <= %s and to_date >= %s""",
			(self.delegator, self.name or "", self.to_date, self.from_date))
		if clash:
			frappe.throw(_("This overlaps delegation {0}.").format(clash[0][0]))
