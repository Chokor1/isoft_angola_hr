# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Configurable contract categories.

LEGAL VERIFICATION REQUIRED — the defaults held here (duration, probation, notice) are
configuration entered by the customer. This application infers no statutory right, term
limit, renewal cap or severance entitlement from a contract type, and deliberately does
not encode Angolan labour law into a label.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class IsoftContractType(Document):
	def validate(self):
		if cint(self.default_duration_months) < 0 or cint(self.default_probation_days) < 0 \
				or cint(self.default_notice_days) < 0:
			frappe.throw(_("Durations cannot be negative."))
		if not cint(self.is_fixed_term):
			self.default_duration_months = 0
