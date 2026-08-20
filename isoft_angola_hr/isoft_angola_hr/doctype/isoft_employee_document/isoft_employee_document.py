# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Employee documents, with validity tracking and real confidentiality.

Two things make this more than an attachment list: an expiry date that HR is warned
about before it passes, and a confidentiality flag that is enforced server-side — a
medical certificate must not be readable by every HR user just because it happens to be
attached to an employee.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: Warning windows, in days, before a document expires.
DEFAULT_EXPIRY_THRESHOLDS = (90, 60, 30, 15, 7)


class IsoftEmployeeDocument(Document):
	def validate(self):
		self.apply_type_rules()
		self.validate_dates()
		self.refresh_status()
		self.enforce_private_attachment()

	def apply_type_rules(self):
		if not self.document_type:
			return
		rules = frappe.db.get_value(
			"Isoft Document Type", self.document_type,
			["requires_expiry", "is_confidential", "is_medical"], as_dict=True) or {}
		self.confidential = 1 if (cint(rules.get("is_confidential"))
		                          or cint(rules.get("is_medical"))) else 0
		if cint(rules.get("requires_expiry")) and not self.expiry_date:
			frappe.throw(_("{0} requires an expiry date.").format(self.document_type))

	def validate_dates(self):
		if self.issue_date and self.expiry_date \
				and getdate(self.expiry_date) < getdate(self.issue_date):
			frappe.throw(_("The expiry date cannot be before the issue date."))

	def refresh_status(self):
		if self.superseded_by:
			self.status = "Superseded"
			self.days_to_expiry = None
			return
		if not self.expiry_date:
			self.status = "Valid"
			self.days_to_expiry = None
			return
		days = date_diff(getdate(self.expiry_date), getdate(nowdate()))
		self.days_to_expiry = days
		self.status = "Expired" if days < 0 else (
			"Expiring" if days <= max(expiry_thresholds()) else "Valid")

	def enforce_private_attachment(self):
		"""An employee document must never be publicly downloadable.

		Frappe stores an Attach field's file as public by default. A BI, a passport or a
		medical certificate sitting on a guessable /files/ URL is a data breach waiting to
		be indexed, so the File is flipped to private here.
		"""
		if not self.attachment or not self.attachment.startswith("/files/"):
			return
		file_name = frappe.db.get_value("File", {"file_url": self.attachment}, "name")
		if not file_name:
			return
		file_doc = frappe.get_doc("File", file_name)
		if cint(file_doc.is_private):
			return
		file_doc.is_private = 1
		file_doc.save(ignore_permissions=True)
		self.attachment = file_doc.file_url

	def has_permission(self, ptype="read", user=None):
		"""Confidential and medical documents are HR Manager only."""
		if not cint(self.confidential):
			return True
		return perms.can(perms.DOCUMENT_CONFIDENTIAL, user=user)


def expiry_thresholds():
	raw = frappe.db.get_single_value("Isoft HR Settings", "document_expiry_thresholds")
	if not raw:
		return list(DEFAULT_EXPIRY_THRESHOLDS)
	values = sorted({cint(p) for p in str(raw).replace(";", ",").split(",") if cint(p) > 0},
	                reverse=True)
	return values or list(DEFAULT_EXPIRY_THRESHOLDS)


def refresh_document_statuses():
	"""Daily sweep so "Expiring" and "Expired" mean something without anyone re-saving."""
	changed = 0
	for row in frappe.get_all("Isoft Employee Document",
	                          filters={"status": ["in", ["Valid", "Expiring"]],
	                                   "expiry_date": ["is", "set"]},
	                          fields=["name", "expiry_date", "status"]):
		days = date_diff(getdate(row.expiry_date), getdate(nowdate()))
		status = "Expired" if days < 0 else (
			"Expiring" if days <= max(expiry_thresholds()) else "Valid")
		if status != row.status:
			frappe.db.set_value("Isoft Employee Document", row.name,
			                    {"status": status, "days_to_expiry": days},
			                    update_modified=False)
			changed += 1
	return changed
