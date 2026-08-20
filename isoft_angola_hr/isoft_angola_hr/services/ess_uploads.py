# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The two upload paths Phase 4 left open: attendance justification, and own documents.

Both were the last routine reasons an employee still had to email HR. Both are also the
first place in this application where an employee writes a file to the server, so the
rules are stricter than anywhere else:

* **The employee is derived from the session**, as everywhere in self-service. There is
  no employee parameter to tamper with.
* **Every file is private.** Not "usually" private — the ``is_private`` flag is forced on,
  because a public file URL is a permanent, unauthenticated link to somebody's medical
  certificate.
* **Extension and size are checked server-side**, from the filename and the decoded
  bytes. The browser's ``accept`` attribute is a convenience, not a control (§43).
* **An employee-uploaded document is never authoritative.** It arrives as Pending
  Verification and stays there until HR looks at it (§42). Anything else would let an
  employee replace their own recorded qualifications.
* **An employee can explain an absence; they cannot excuse it.** Submitting a
  justification moves nothing to Justified — that decision belongs to the manager or HR
  (§38).
"""

import base64
import os

import frappe
from frappe import _
from frappe.utils import cint, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: What an employee may upload. A deny-list would admit every format somebody invents
#: next; an allow-list admits only what HR actually needs to read.
ALLOWED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".doc", ".docx")

#: 8 MB. Large enough for a scanned multi-page certificate, small enough that a phone
#: camera dump cannot fill the disk.
MAX_BYTES = 8 * 1024 * 1024

#: Magic numbers for the formats we accept. The extension says what a file claims to be;
#: these say what it is. A .pdf that begins with `MZ` is not a scan of an ID card.
_SIGNATURES = {
	b"%PDF": ".pdf",
	b"\xff\xd8\xff": ".jpg",
	b"\x89PNG\r\n\x1a\n": ".png",
	b"PK\x03\x04": ".docx",       # also .doc when saved as OOXML
	b"\xd0\xcf\x11\xe0": ".doc",  # legacy OLE compound file
}


def _validate_file(filename, content):
	"""Extension, size and content signature. Returns the cleaned filename."""
	name = os.path.basename(filename or "").strip()
	if not name:
		frappe.throw(_("The file has no name."))
	extension = os.path.splitext(name)[1].lower()
	if extension not in ALLOWED_EXTENSIONS:
		frappe.throw(_("{0} files are not accepted. Allowed: {1}.").format(
			extension or _("unknown"), ", ".join(ALLOWED_EXTENSIONS)))
	if not content:
		frappe.throw(_("The file is empty."))
	if len(content) > MAX_BYTES:
		frappe.throw(_("The file is {0} MB. The maximum is {1} MB.").format(
			round(len(content) / 1024.0 / 1024.0, 1), MAX_BYTES // 1024 // 1024))

	# HEIC has no single stable magic number at offset 0, so it is accepted on extension.
	if extension != ".heic":
		head = content[:8]
		if not any(head.startswith(sig) for sig in _SIGNATURES):
			frappe.throw(
				_("The file does not look like a {0} document. Upload the original file "
				  "rather than a renamed one.").format(extension))
	return name


def _decode(content):
	"""Accept a base64 payload, with or without a data: URL prefix."""
	if isinstance(content, bytes):
		return content
	value = content or ""
	if "," in value[:64] and value[:5] == "data:":
		value = value.split(",", 1)[1]
	try:
		return base64.b64decode(value)
	except Exception:
		frappe.throw(_("The uploaded file could not be read."))


def _attach(filename, content, doctype, name, folder_note=""):
	"""Store a PRIVATE file against a document. Never public, no exceptions."""
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": filename,
		"attached_to_doctype": doctype,
		"attached_to_name": name,
		"is_private": 1,
		"content": content,
		"decode": False,
	})
	file_doc.flags.ignore_permissions = True
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


# --------------------------------------------------------------------------- #
# Attendance justification (§37–39)
# --------------------------------------------------------------------------- #
#: States an employee may still submit an explanation for.
JUSTIFIABLE = ("Pending Justification", "Unjustified")


def my_occurrence(name):
	row = frappe.db.get_value(
		"Isoft Attendance Occurrence", name,
		["name", "employee", "occurrence_date", "occurrence_type", "hours", "status",
		 "justification_deadline", "justification_reason", "justification_date",
		 "justification_document", "remarks"], as_dict=True)
	if not row:
		frappe.throw(_("Occurrence not found."), frappe.DoesNotExistError)
	if row.employee != ess.current_employee():
		frappe.throw(_("You may only access your own records."), frappe.PermissionError)
	return row


def justification_reasons():
	"""The reasons HR has configured. An employee picks one; they do not invent one."""
	ess.current_employee()
	return frappe.get_all(
		"Isoft Absence Reason",
		filters={"disabled": 0} if frappe.db.has_column("Isoft Absence Reason", "disabled")
		else None,
		fields=["name", "reason_name" if frappe.db.has_column(
			"Isoft Absence Reason", "reason_name") else "name as reason_name"],
		order_by="name")


def submit_justification(name, reason=None, explanation=None, filename=None, content=None):
	"""Explain an absence, with a supporting document.

	The status is deliberately NOT changed. An employee submitting a justification is
	making a case, not deciding it; the manager or HR still decides. Without that
	separation an employee could clear their own unjustified absences (§38).
	"""
	row = my_occurrence(name)
	if row.status not in JUSTIFIABLE:
		frappe.throw(
			_("This occurrence is already {0} and no longer needs a justification.").format(
				_(row.status)), frappe.ValidationError)
	if row.justification_deadline and getdate(row.justification_deadline) < getdate(nowdate()):
		# Late is not refused — HR may still accept it — but it is recorded as late so
		# the decision is made with that fact visible.
		late = True
	else:
		late = False

	if not (explanation or "").strip() and not content:
		frappe.throw(_("Give an explanation, attach a document, or both."))

	values = {"justification_date": getdate(nowdate())}
	if reason:
		if not frappe.db.exists("Isoft Absence Reason", reason):
			frappe.throw(_("Unknown absence reason."))
		values["justification_reason"] = reason
	if explanation:
		stamp = _("Submitted by the employee on {0}{1}").format(
			nowdate(), _(" — after the deadline") if late else "")
		values["remarks"] = "{0}\n{1}: {2}".format(
			row.remarks or "", stamp, explanation).strip()

	file_url = None
	if content:
		clean = _validate_file(filename, _decode(content))
		file_url = _attach(clean, content, "Isoft Attendance Occurrence", name)
		values["justification_document"] = file_url

	frappe.db.set_value("Isoft Attendance Occurrence", name, values)
	_notify_manager(row.employee, name, late)
	return {"name": name, "status": row.status, "late": late, "document": file_url,
	        "note": _("Your explanation was recorded. Your manager or HR will decide "
	                  "whether the absence is justified.")}


def _notify_manager(employee, occurrence, late):
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

	try:
		user = notify._manager_user(employee)
		if not user:
			return
		notify._notify(
			_("Justificação de falta — {0}").format(occurrence),
			_("{0} submitted an explanation for attendance occurrence {1}{2}.").format(
				frappe.db.get_value("Employee", employee, "employee_name"), occurrence,
				_(" (after the deadline)") if late else ""),
			[user], "Isoft Attendance Occurrence", occurrence)
	except Exception:
		pass


# --------------------------------------------------------------------------- #
# Employee document upload (§40–43)
# --------------------------------------------------------------------------- #
def uploadable_document_types():
	"""Only the types HR has marked as employee-submittable.

	Without this an employee could file a document as "Contrato de Trabalho" or
	"Certificado Médico" and change what the record says about them (§41).
	"""
	ess.current_employee()
	if not frappe.db.has_column("Isoft Document Type", "employee_may_upload"):
		return []
	return frappe.db.sql(
		"""select name, document_type, ifnull(document_type_pt, document_type) as label,
			requires_expiry, is_confidential, is_medical
		from `tabIsoft Document Type`
		where ifnull(employee_may_upload, 0) = 1 and ifnull(disabled, 0) = 0
		order by document_type""", as_dict=True)


def upload_document(document_type, filename, content, document_number=None,
                    issue_date=None, expiry_date=None, notes=None):
	"""Submit a document for HR verification. It is never immediately authoritative."""
	me = ess.current_employee()

	allowed = {row["name"] for row in uploadable_document_types()}
	if document_type not in allowed:
		frappe.throw(
			_("You cannot upload documents of this type. Ask HR to file it for you."),
			frappe.PermissionError)

	rules = frappe.db.get_value(
		"Isoft Document Type", document_type,
		["requires_expiry", "is_confidential", "is_medical"], as_dict=True) or {}
	if cint(rules.get("requires_expiry")) and not expiry_date:
		frappe.throw(_("This document needs an expiry date."))

	clean = _validate_file(filename, _decode(content))

	doc = frappe.get_doc({
		"doctype": "Isoft Employee Document",
		"employee": me,
		"document_type": document_type,
		"document_number": document_number,
		"issue_date": getdate(issue_date) if issue_date else None,
		"expiry_date": getdate(expiry_date) if expiry_date else None,
		"notes": notes,
		"verification_status": "Pending Verification",
		"submitted_by_employee": 1,
	})
	doc.insert(ignore_permissions=True)

	file_url = _attach(clean, content, "Isoft Employee Document", doc.name)
	doc.db_set("attachment", file_url)

	_notify_hr_of_upload(doc)
	return {"name": doc.name, "document_type": document_type,
	        "verification_status": "Pending Verification",
	        "note": _("Submitted. HR will verify it before it becomes part of your "
	                  "official record.")}


def _notify_hr_of_upload(doc):
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

	try:
		notify._notify(
			_("Documento submetido — {0}").format(doc.name),
			_("{0} uploaded a {1} for verification.").format(
				doc.employee_name or doc.employee, doc.document_type),
			notify._recipients_hr(), "Isoft Employee Document", doc.name)
	except Exception:
		pass


def verify_document(name, decision, reason=None):
	"""HR accepts or rejects an employee-submitted document (§42)."""
	perms.require(perms.DOCUMENT_WRITE)
	doc = frappe.get_doc("Isoft Employee Document", name)
	perms.require_company(doc.company)
	if decision not in ("Verified", "Rejected"):
		frappe.throw(_("Decision must be Verified or Rejected."))
	if decision == "Rejected" and not (reason or "").strip():
		frappe.throw(_("Give a reason when rejecting a document — the employee sees it."))

	doc.db_set({
		"verification_status": decision,
		"verified_by": frappe.session.user,
		"verified_on": getdate(nowdate()),
		"verification_reason": reason,
		# A rejected document must not sit in the record looking valid.
		"status": "Superseded" if decision == "Rejected" else doc.status,
	})

	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

	try:
		notify._tell(doc.employee,
		             _("Documento {0} — {1}").format(doc.name, _(decision)),
		             _("Your {0} was {1}.{2}").format(
			             doc.document_type, _(decision).lower(),
			             " " + reason if reason else ""),
		             "Isoft Employee Document", doc.name)
	except Exception:
		pass
	return {"name": doc.name, "verification_status": decision}


def pending_verification(company=None):
	"""HR's queue of employee-submitted documents."""
	perms.require(perms.DOCUMENT_READ)
	conditions, values = ["ifnull(d.submitted_by_employee, 0) = 1",
	                      "d.verification_status = 'Pending Verification'"], []
	if company:
		conditions.append("d.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="d")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	rows = frappe.db.sql(
		"""select d.name, d.employee, d.employee_name, d.document_type, d.document_number,
			d.issue_date, d.expiry_date, d.attachment, d.creation, d.confidential
		from `tabIsoft Employee Document` d where {0}
		order by d.creation""".format(" and ".join(conditions)), values, as_dict=True)
	# Confidential and medical uploads stay with HR Managers even in the queue.
	if not perms.can(perms.DOCUMENT_CONFIDENTIAL):
		rows = [r for r in rows if not cint(r.get("confidential"))]
	return rows
