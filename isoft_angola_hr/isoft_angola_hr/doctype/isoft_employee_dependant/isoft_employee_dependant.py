# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Employee dependants — an HR record, deliberately not a tax rule.

LEGAL VERIFICATION REQUIRED. ``is_tax_dependant`` is recorded because HR asks for it,
but nothing in the payroll engine reads it. No authoritative Angolan rule granting an
IRT deduction per dependant has been verified, and inventing one would change what every
employee is paid.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class IsoftEmployeeDependant(Document):
	def validate(self):
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")
		if self.effective_from and self.effective_to \
				and getdate(self.effective_to) < getdate(self.effective_from):
			frappe.throw(_("Effective To cannot be before Effective From."))
		if self.date_of_birth and getdate(self.date_of_birth) > getdate(frappe.utils.nowdate()):
			frappe.throw(_("A date of birth cannot be in the future."))
