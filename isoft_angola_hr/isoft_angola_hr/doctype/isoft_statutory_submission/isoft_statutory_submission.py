# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class IsoftStatutorySubmission(Document):
	def validate(self):
		self.guard_status()

	def guard_status(self):
		"""A declaration is only "submitted" when the portal says so.

		Producing a spreadsheet proves nothing was declared. Without this guard the
		register would fill up with periods marked Submitted that nobody ever delivered —
		which is worse than having no register, because it would be believed.
		"""
		if self.status in ("Submitted", "Accepted") and not (self.reference or "").strip():
			frappe.throw(
				_("Record the reference issued by the portal before marking this "
				  "declaration as {0}.").format(_(self.status)))
