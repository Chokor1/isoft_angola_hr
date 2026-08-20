# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Changing where somebody's salary is paid — the highest-value fraud target in payroll.

An employee may REQUEST a new IBAN; only HR can approve it, and only approval writes it
to the Employee record. The stored "current" value is masked, because an audit screen
that prints full bank account numbers is itself the leak.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

PENDING = "Pending Approval"
APPROVED = "Approved"
REJECTED = "Rejected"
CANCELLED = "Cancelled"


def mask_iban(value):
	"""Show only enough to recognise the account: ``AO06…1174``."""
	value = (value or "").strip().replace(" ", "")
	if not value:
		return ""
	if len(value) <= 8:
		return "…" + value[-2:]
	return "{0}…{1}".format(value[:4], value[-4:])


class IsoftBankChangeRequest(Document):
	def validate(self):
		if not self.status:
			self.status = PENDING
		if not self.requested_by:
			self.requested_by = self.owner or frappe.session.user
		if not self.requested_at:
			self.requested_at = now()
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")

		new_iban = (self.new_iban or "").strip().replace(" ", "")
		if not new_iban:
			frappe.throw(_("Enter the new IBAN."))
		if len(new_iban) < 15 or not new_iban[:2].isalpha() or not new_iban[2:].isalnum():
			# Structural sanity only. Full IBAN check-digit validation is deliberately not
			# claimed, because a half-correct validator that rejects a valid account is
			# worse than none.
			frappe.throw(_("{0} does not look like an IBAN. Expected a country prefix followed "
			               "by the account number, e.g. AO06...").format(self.new_iban))
		self.new_iban = new_iban

		if self.is_new():
			current = frappe.db.get_value("Employee", self.employee, "custom_iban")
			self.current_iban_masked = mask_iban(current)
			if current and current.strip().replace(" ", "") == new_iban:
				frappe.throw(_("The new IBAN is the same as the one already on file."))

		if frappe.db.exists("Isoft Bank Change Request",
		                    {"employee": self.employee, "status": PENDING,
		                     "name": ["!=", self.name or ""]}):
			frappe.throw(_("{0} already has a bank change request awaiting approval.").format(
				self.employee_name or self.employee))

	@frappe.whitelist()
	def approve(self):
		"""Only approval writes the Employee record."""
		perms.require(perms.BANK_CHANGE_APPROVE)
		perms.require_company(self.company)
		if self.status != PENDING:
			frappe.throw(_("This request is {0} and can no longer be approved.").format(
				_(self.status)))
		if self.requested_by == frappe.session.user and perms.can(perms.BANK_CHANGE_APPROVE):
			# HR changing their own bank details still needs a second pair of eyes.
			if frappe.db.get_value("Employee", self.employee, "user_id") == frappe.session.user:
				frappe.throw(
					_("Não pode aprovar a alteração dos seus próprios dados bancários."),
					title=_("Self-Approval Blocked"))
		frappe.db.set_value("Employee", self.employee, "custom_iban", self.new_iban)
		self.db_set("status", APPROVED, update_modified=False)
		self.db_set("approved_by", frappe.session.user, update_modified=False)
		self.db_set("approved_at", now(), update_modified=False)
		return APPROVED

	@frappe.whitelist()
	def reject(self, reason=None):
		perms.require(perms.BANK_CHANGE_APPROVE)
		perms.require_company(self.company)
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is mandatory."))
		self.db_set("status", REJECTED, update_modified=False)
		self.db_set("rejection_reason", reason.strip(), update_modified=False)
		self.db_set("approved_by", frappe.session.user, update_modified=False)
		self.db_set("approved_at", now(), update_modified=False)
		return REJECTED
