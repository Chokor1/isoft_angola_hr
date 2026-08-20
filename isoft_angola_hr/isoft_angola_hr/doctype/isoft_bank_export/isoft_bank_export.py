# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class IsoftBankExport(Document):
	def validate(self):
		self.guard_status()

	def guard_status(self):
		"""A generated file is not a payment.

		The bank tells you it received the file; the application cannot know it. So the
		status only leaves Generated when somebody records the bank's own reference.
		"""
		if self.status in ("Submitted to Bank", "Executed") and not (self.bank_reference or "").strip():
			frappe.throw(
				_("Record the bank's reference before marking this export as {0}. Producing "
				  "a file is not evidence that the bank received it.").format(_(self.status)))
