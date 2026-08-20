# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class IsoftPerformanceCycle(Document):
	def validate(self):
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("The cycle end date cannot be before its start date."))
		if self.due_date and getdate(self.due_date) < getdate(self.end_date):
			# Reviews assess a period; asking for them before it ends produces guesses.
			frappe.throw(_("Reviews cannot be due before the period being reviewed ends."))
		if not self.created_by_user:
			self.created_by_user = frappe.session.user

	def on_trash(self):
		linked = frappe.db.count("Appraisal", {"custom_performance_cycle": self.name})
		if linked:
			frappe.throw(
				_("{0} appraisal(s) belong to this cycle. Delete or reassign them first — "
				  "removing the cycle would orphan completed reviews.").format(linked))
