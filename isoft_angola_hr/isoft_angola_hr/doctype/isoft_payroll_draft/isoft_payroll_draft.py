# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""HR's unfinished payroll preview for one company and period (HR, November 2026).

Written and read only through api.save_payroll_draft / get_payroll_draft /
discard_payroll_draft; cleared by create_payroll_from_preview once the slips exist.
"""

from frappe.model.document import Document


class IsoftPayrollDraft(Document):
	pass
