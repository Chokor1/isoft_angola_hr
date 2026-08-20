# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class IsoftContractDocument(Document):
	def validate(self):
		if not self.employee and self.contract:
			row = frappe.db.get_value(
				"Isoft Employment Contract", self.contract,
				["employee", "employee_name", "company"], as_dict=True)
			if row:
				self.employee = row.employee
				self.employee_name = row.employee_name
				self.company = row.company

	def on_update_after_submit(self):
		pass

	def before_save(self):
		"""The issued text is immutable.

		An issued contract document is evidence of what was put in front of somebody to
		sign. Editing it after the fact would make that evidence worthless, so the body
		can only be set once, when the record is created.
		"""
		if self.is_new():
			return
		previous = frappe.db.get_value("Isoft Contract Document", self.name, "body")
		if (previous or "") != (self.body or ""):
			frappe.throw(
				_("The text of an issued contract document cannot be edited. Generate a new "
				  "document instead — the previous one is kept and marked Superseded."))
