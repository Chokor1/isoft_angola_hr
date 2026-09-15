# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""One row per change of a payroll account mapping — who, when, from what, to what.

Written only by ``IsoftHRSettings.record_mapping_changes``. The rows are the history of
the mapping; the vouchers are the history of the ledger. Neither is ever rewritten: a
row can be read, never edited or deleted, and a mapping change after a payroll was
posted is flagged rather than hidden.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class IsoftPayrollAccountChange(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("A payroll account change record is history and cannot be edited."),
			             frappe.PermissionError)

	def on_trash(self):
		if not frappe.flags.in_test and not frappe.flags.in_install:
			frappe.throw(_("A payroll account change record is history and cannot be deleted."),
			             frappe.PermissionError)
