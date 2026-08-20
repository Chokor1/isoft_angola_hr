# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Kinds of employee document, and how sensitive each one is."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class IsoftDocumentType(Document):
	def validate(self):
		# A medical document is confidential by definition; letting the two flags diverge
		# would create a "medical but not confidential" category nobody intends.
		if cint(self.is_medical):
			self.is_confidential = 1
