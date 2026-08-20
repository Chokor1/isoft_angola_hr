# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""HR-operated paths for the work that previously only ESS or MSS could reach.

THE OPERATING MODEL
-------------------
Employees and line managers are NOT required to hold a login. The employee speaks to
HR; HR records the request; an authorised HR person decides it; the system applies the
result. ``/ess`` and ``/mss`` remain available and unchanged, but nothing depends on
them — every process has an HR front door.

Three earlier assumptions had to go, and each was a real hole rather than a preference:

* a bank change could only be *requested* by the employee whose account it was, so an
  employee without a login could never have their IBAN corrected at all;
* an employee document could only be *uploaded* by the employee, so a birth certificate
  handed to HR across a desk had nowhere to go;
* a performance review could only be scored by the line manager's own session, so a
  cycle stalled permanently the moment a manager had no account.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not weaken a single control.

* Approval still requires the approving permission, checked by the existing services —
  nothing here approves anything.
* Recording a request is a separate permission from granting it, so an HR User who may
  key a bank change still cannot authorise one.
* Where HR records somebody else's decision, the decision-maker and the person who typed
  it are stored in different fields. The audit trail never claims HR made a judgement
  that a manager actually made.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: The channels through which a request reaches HR. Free text would make the field
#: useless for reporting; a closed list keeps "how did we hear about this" answerable.
REQUEST_SOURCES = (
	"Employee verbal request",
	"Email",
	"Written request",
	"Management instruction",
	"HR initiated",
	"Other",
)

#: How a performance evaluation arrived. Stored on the appraisal so a reader can tell a
#: manager's own entry from HR keying what the manager decided.
EVALUATION_SOURCES = (
	"Line manager (self-service)",
	"Line manager decision recorded by HR",
	"HR Manager (no line manager)",
	"Other",
)


def validate_source(value, allowed=REQUEST_SOURCES, label=None):
	"""Accept an empty source; reject an invented one."""
	value = (value or "").strip()
	if not value:
		return None
	if value not in allowed:
		frappe.throw(
			_("{0} must be one of: {1}.").format(label or _("Request Source"),
			                                     ", ".join(allowed)))
	return value


def _employee(employee):
	row = frappe.db.get_value(
		"Employee", employee,
		["name", "employee_name", "company", "status", "user_id", "reports_to"],
		as_dict=True)
	if not row:
		frappe.throw(_("Employee {0} does not exist.").format(frappe.bold(employee or "")),
		             frappe.DoesNotExistError)
	return row


# --------------------------------------------------------------------------- #
# Bank change (§13)
# --------------------------------------------------------------------------- #
def create_bank_change(employee, new_iban, bank_name=None, proof_document=None,
                       request_source=None):
	"""HR records an employee's request to be paid into a different account.

	Deliberately a REQUEST and not a write. The Employee record is still only touched by
	``approve()``, which requires ``BANK_CHANGE_APPROVE`` — so an HR User keying this has
	changed nothing yet, and the previous IBAN stays on file, masked, for comparison.

	This is the single highest-value fraud target in payroll, which is exactly why the
	HR path is a request rather than a shortcut.
	"""
	perms.require(perms.BANK_CHANGE_REQUEST)
	row = _employee(employee)
	perms.require_company(row.company)

	doc = frappe.get_doc({
		"doctype": "Isoft Bank Change Request",
		"employee": row.name,
		"employee_name": row.employee_name,
		"company": row.company,
		"new_iban": new_iban,
		"bank_name": bank_name,
		"proof_document": proof_document,
		"request_source": validate_source(request_source) or "Written request",
	})
	# All structural validation (IBAN shape, duplicate pending request, "same as the one
	# on file") lives on the DocType and runs here in full.
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status,
	        "employee": doc.employee, "employee_name": doc.employee_name,
	        "current_iban_masked": doc.current_iban_masked}


# --------------------------------------------------------------------------- #
# Employee documents (§14)
# --------------------------------------------------------------------------- #
def add_employee_document(employee, document_type, filename=None, content=None,
                          document_number=None, issue_date=None, expiry_date=None,
                          issuing_authority=None, notes=None, confidential=None):
	"""HR files a document it was physically handed.

	File validation (type, size, extension) is reused from the self-service uploader
	rather than reimplemented, so HR and the employee cannot end up with two different
	ideas of what a permitted attachment is.

	Confidentiality is NOT taken from the caller's wish: it comes from the Document Type,
	and a caller may only raise it. A medical certificate stays HR-Manager-only whether
	the employee uploaded it or HR did.
	"""
	perms.require(perms.DOCUMENT_WRITE)
	row = _employee(employee)
	perms.require_company(row.company)

	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	dtype = frappe.db.get_value("Isoft Document Type", document_type,
	                            ["name", "is_confidential", "is_medical", "disabled"],
	                            as_dict=True)
	if not dtype:
		frappe.throw(_("Document type {0} does not exist.").format(
			frappe.bold(document_type or "")))
	if cint(dtype.disabled):
		frappe.throw(_("Document type {0} is disabled.").format(dtype.name))

	# Medical implies confidential — a sick note is not less private because somebody
	# forgot to tick the second box on the document type.
	is_confidential = (cint(dtype.is_confidential) or cint(dtype.is_medical)
	                   or cint(confidential or 0))
	if is_confidential:
		# Filing one is as sensitive as reading one: an HR User could otherwise attach a
		# medical report and then be unable to see what they had just filed.
		perms.require(perms.DOCUMENT_CONFIDENTIAL)

	doc = frappe.get_doc({
		"doctype": "Isoft Employee Document",
		"employee": row.name,
		"employee_name": row.employee_name,
		"company": row.company,
		"document_type": dtype.name,
		"document_number": document_number,
		"issue_date": issue_date or None,
		"expiry_date": expiry_date or None,
		"issuing_authority": issuing_authority,
		"notes": notes,
		"confidential": 1 if is_confidential else 0,
		# HR filed it, so it is verified by definition — the person who checked the
		# original is the person entering it. An employee upload arrives unverified.
		"submitted_by_employee": 0,
		"verification_status": "Verified",
		"verified_by": frappe.session.user,
		"verified_on": getdate(nowdate()),
	})
	doc.insert(ignore_permissions=True)

	if filename and content:
		url = ess_uploads._attach(filename, content, "Isoft Employee Document", doc.name,
		                          folder_note=dtype.name)
		doc.db_set("attachment", url, update_modified=False)
		doc.reload()
	return {"name": doc.name, "document_type": doc.document_type,
	        "status": doc.status, "attachment": doc.attachment,
	        "confidential": cint(doc.confidential)}


# --------------------------------------------------------------------------- #
# Attendance justification (§9)
# --------------------------------------------------------------------------- #
def record_justification(occurrence, reason, explanation=None, filename=None,
                         content=None, justification_source=None, decision=None):
	"""HR records the explanation an employee gave, and optionally decides it.

	The employee hands over a medical certificate at the HR desk; HR attaches it here.
	The five-day window, the extraordinary override and the HR Manager restriction on
	re-justifying a locked occurrence are all enforced by the existing occurrence API,
	which this calls into rather than duplicating.
	"""
	perms.require(perms.ATTENDANCE_WRITE)
	doc = frappe.get_doc("Isoft Attendance Occurrence", occurrence)
	perms.require_company(doc.company)

	source = validate_source(justification_source, label=_("Justification Source"))
	updates = {"justification_reason": reason}
	if source:
		updates["justification_source"] = source
	if explanation:
		updates["remarks"] = "{0}\n{1}".format(doc.remarks or "", explanation).strip()

	if filename and content:
		from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

		updates["justification_document"] = ess_uploads._attach(
			filename, content, "Isoft Attendance Occurrence", doc.name,
			folder_note=_("Justification"))

	doc.db_set(updates, update_modified=True)

	if decision:
		from isoft_angola_hr.isoft_angola_hr import api

		if decision not in ("justify", "reject"):
			frappe.throw(_("Decision must be 'justify' or 'reject'."))
		if decision == "justify":
			# The explanation was already appended to remarks above; passing it again
			# would make justify_occurrence overwrite the field with a bare copy and lose
			# whatever was recorded before.
			api.justify_occurrence(doc.name, reason)
		else:
			api.set_occurrence_status(doc.name, "Unjustified")
		doc.reload()
	return {"name": doc.name, "status": doc.status,
	        "justification_document": doc.justification_document}


# --------------------------------------------------------------------------- #
# Performance (§16, §17, §27)
# --------------------------------------------------------------------------- #
def record_evaluation(appraisal, goals=None, comments=None, decision_by=None,
                      evaluation_source=None, submit=True):
	"""HR records the evaluation for a review, on the line manager's behalf.

	Deliberately NOT a copy of ``performance.manager_review``: the state machine, the
	"score every objective" rule and ERPNext's own weighted arithmetic are all reached by
	calling that function with the manager check satisfied differently. Only the
	authorisation question is answered here — HR is authorised by role, not by being the
	manager — and the attribution is recorded honestly.

	``decision_by`` is free text on purpose. The person whose judgement this is may have
	no Employee record and certainly may have no User: a department head who sent an
	email, a client-site supervisor, a director who phoned. Forcing a link would push HR
	into either inventing a record or leaving the field blank, and a blank field is how
	an evaluation ends up looking like HR's own opinion.
	"""
	perms.require(perms.PERFORMANCE_OPERATE)

	from isoft_angola_hr.isoft_angola_hr.services import performance

	row = performance._appraisal(appraisal)
	perms.require_company(row.company)

	source = validate_source(evaluation_source, EVALUATION_SOURCES,
	                         label=_("Evaluation Source"))
	if not source:
		source = ("Line manager decision recorded by HR" if row.custom_manager
		          else "HR Manager (no line manager)")
	if source == "Line manager decision recorded by HR" and not (decision_by or "").strip():
		frappe.throw(
			_("Name the person whose evaluation this is. Recording a manager's decision "
			  "without saying whose decision it was leaves the review attributed to you."))

	# The manager check is the only thing bypassed, and only because HR holds
	# PERFORMANCE_OPERATE. Everything else — state, scores, totals — runs unchanged.
	frappe.flags.isoft_hr_operated_review = True
	try:
		result = performance.manager_review(appraisal, goals=goals, comments=comments,
		                                    submit=submit)
	finally:
		frappe.flags.isoft_hr_operated_review = False

	frappe.db.set_value("Appraisal", appraisal, {
		"custom_evaluation_source": source,
		"custom_decision_by": (decision_by or "").strip() or None,
	}, update_modified=False)
	result["evaluation_source"] = source
	result["decision_by"] = (decision_by or "").strip() or None
	return result


def record_acknowledgement(appraisal, comments=None, acknowledged_by=None):
	"""HR records that the employee has seen their review.

	An acknowledgement is a statement of fact — "this was shown to them" — not an
	approval, so HR recording it changes nothing about what the review says. The employee
	who has no login physically signs the printed review; this is where that is entered.
	"""
	perms.require(perms.PERFORMANCE_OPERATE)

	from isoft_angola_hr.isoft_angola_hr.services import performance

	row = performance._appraisal(appraisal)
	perms.require_company(row.company)
	if row.custom_review_state != performance.PENDING_EMPLOYEE:
		frappe.throw(
			_("This review is at stage {0}. An acknowledgement can only be recorded while "
			  "it is waiting for the employee.").format(
				row.custom_review_state or _("not started")),
			frappe.ValidationError)

	note = (comments or "").strip()
	who = (acknowledged_by or "").strip()
	if who:
		note = "{0}\n\n{1}: {2}".format(note, _("Acknowledged in person, recorded by HR"),
		                                who).strip()
	frappe.db.set_value("Appraisal", appraisal, {
		"custom_employee_comments": note or None,
		"custom_employee_acknowledged_at": now(),
		"custom_review_state": performance.PENDING_HR,
	})
	return {"name": appraisal, "state": performance.PENDING_HR}


# --------------------------------------------------------------------------- #
# Recruitment (§15)
# --------------------------------------------------------------------------- #
#: ERPNext's own Interview status vocabulary. Reused rather than invented so the
#: recruitment records stay ordinary ERPNext records that its reports still understand.
INTERVIEW_RESULTS = ("Cleared", "Rejected")


def record_interview_result(interview, result, feedback=None, decision_by=None):
	"""HR records the outcome of an interview the panel conducted offline.

	Interview panels are typically people with no HRMS login — a technical lead, a client
	representative — so requiring the interviewer's own session would mean interviews are
	never recorded at all.
	"""
	perms.require(perms.RECRUITMENT_OPERATE)
	if result not in INTERVIEW_RESULTS:
		frappe.throw(_("Interview result must be one of: {0}.").format(
			", ".join(INTERVIEW_RESULTS)))
	if not frappe.db.exists("Interview", interview):
		frappe.throw(_("Interview {0} does not exist.").format(frappe.bold(interview or "")),
		             frappe.DoesNotExistError)

	note = (feedback or "").strip()
	if decision_by:
		note = "{0}\n\n{1}: {2}".format(note, _("Panel"), decision_by).strip()
	updates = {"status": result}
	meta = frappe.get_meta("Interview")
	if meta.has_field("interview_summary") and note:
		updates["interview_summary"] = note
	frappe.db.set_value("Interview", interview, updates)
	return {"name": interview, "status": result}


# --------------------------------------------------------------------------- #
# What needs HR action today (§28)
# --------------------------------------------------------------------------- #
def action_queue(company=None):
	"""The one question the HR home screen has to answer: what needs doing today?

	Every row is a count plus the screen that clears it. A dashboard that shows a number
	without saying where to go to act on it is a report, not an operating console — and
	the audit found HR staring at exactly that.

	Counts are deliberately computed with aggregate SQL rather than by loading the rows:
	this runs on every dashboard open, and at 679 employees loading them was measurable.
	"""
	perms.require(perms.HR_READINESS)
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	perms.require_company(company)
	today = getdate(nowdate())

	def count(query, values=()):
		return cint(frappe.db.sql(query, values)[0][0])

	company_clause = " and company = %s" if company else ""
	cvals = (company,) if company else ()

	pending_contracts = count(
		"""select count(*) from `tabIsoft Employment Contract`
		where status = 'Pending Approval'{0}""".format(company_clause), cvals)
	pending_changes = count(
		"""select count(*) from `tabIsoft Salary Change`
		where status = 'Pending Approval'{0}""".format(company_clause), cvals)
	pending_advances = count(
		"""select count(*) from `tabIsoft Salary Advance`
		where status = 'Pending Approval'{0}""".format(company_clause), cvals)
	pending_bank = count(
		"""select count(*) from `tabIsoft Bank Change Request`
		where status = 'Pending Approval'{0}""".format(company_clause), cvals)
	pending_leave = count(
		"""select count(*) from `tabLeave Application`
		where docstatus = 0 and status = 'Open'{0}""".format(company_clause), cvals)
	approved_advances = count(
		"""select count(*) from `tabIsoft Salary Advance`
		where status = 'Approved'{0}""".format(company_clause), cvals)

	emp_join = "join `tabEmployee` e on e.name = o.employee where e.company = %s and " \
		if company else "where "
	occurrences = count(
		"""select count(*) from `tabIsoft Attendance Occurrence` o {0}
		o.status = 'Pending Justification'""".format(emp_join), cvals)

	doc_join = "join `tabEmployee` e on e.name = d.employee where e.company = %s and " \
		if company else "where "
	docs_pending = count(
		"""select count(*) from `tabIsoft Employee Document` d {0}
		d.verification_status = 'Pending Verification'""".format(doc_join), cvals)
	docs_expiring = count(
		"""select count(*) from `tabIsoft Employee Document` d {0}
		d.status in ('Expiring', 'Expired')""".format(doc_join), cvals)

	reviews_due = count(
		"""select count(*) from `tabAppraisal`
		where docstatus < 2 and ifnull(custom_review_state, '') in
			('Pending Manager', 'Pending Employee', 'Pending HR'){0}""".format(company_clause),
		cvals)

	from isoft_angola_hr.isoft_angola_hr.services import contracts

	expiring = len(contracts.expiring_contracts(company=company))
	probations = [p for p in contracts.probation_reviews_due(company=company)
	              if cint(p.days_left) <= contracts.probation_review_window()]

	changes_due = count(
		"""select count(*) from `tabIsoft Salary Change`
		where status = 'Approved' and effective_date <= %s{0}""".format(company_clause),
		(today,) + tuple(cvals))

	no_contract = count(
		"""select count(*) from `tabEmployee` e where e.status = 'Active'{0}
		  and not exists (select 1 from `tabIsoft Employment Contract` c
			where c.employee = e.name and c.status in ('Active', 'Expiring'))""".format(
			" and e.company = %s" if company else ""), cvals)
	no_profile = count(
		"""select count(*) from `tabEmployee` e where e.status = 'Active'{0}
		  and not exists (select 1 from `tabIsoft Salary Profile` p
			where p.employee = e.name)""".format(" and e.company = %s" if company else ""),
		cvals)

	rows = [
		# key, label, count, the screen that clears it, why it matters
		("approvals_contract", _("Contracts waiting for approval"), pending_contracts,
		 "contracts", _("An HR Manager approves; the contract is not active until then.")),
		("approvals_salary", _("Salary changes waiting for approval"), pending_changes,
		 "salarychanges", _("An HR Manager who did not request it approves.")),
		("approvals_advance", _("Salary advances waiting for approval"), pending_advances,
		 "advances", _("An HR or Payroll Manager approves; Finance then disburses.")),
		("approvals_bank", _("Bank changes waiting for approval"), pending_bank,
		 "bankchanges", _("Only approval writes the employee's IBAN.")),
		("approvals_leave", _("Leave requests waiting for a decision"), pending_leave,
		 "leaves", _("An HR Manager approves or rejects.")),
		("advances_to_disburse", _("Approved advances waiting to be paid"), approved_advances,
		 "advances", _("Finance disburses; recovery starts at the next payroll.")),
		("changes_to_apply", _("Approved salary changes due to be applied"), changes_due,
		 "salarychanges", _("Applying closes the old salary profile and opens the new one.")),
		("occurrences", _("Attendance occurrences awaiting justification"), occurrences,
		 "occurrences", _("Record the explanation the employee gave, and decide it.")),
		("documents_pending", _("Employee documents awaiting verification"), docs_pending,
		 "documents", _("Check the original, then verify or reject.")),
		("documents_expiring", _("Employee documents expiring or expired"), docs_expiring,
		 "documents", _("Collect the renewed document and file it.")),
		("contracts_expiring", _("Contracts expiring"), expiring,
		 "contracts", _("Renew, or let it expire deliberately.")),
		("probations", _("Probation decisions due"), len(probations),
		 "contracts", _("Confirm, extend or end the probation before it lapses.")),
		("reviews", _("Performance reviews still open"), reviews_due,
		 "performance", _("Record the evaluation, the acknowledgement, then finalise.")),
		("no_contract", _("Active employees with no contract"), no_contract,
		 "bulkcontracts", _("Create contracts — in bulk for staff who predate the module.")),
		("no_profile", _("Active employees with no salary profile"), no_profile,
		 "profiles", _("Payroll cannot calculate them until a salary profile exists.")),
	]
	items = [{"key": k, "label": l, "count": n, "view": v, "hint": h}
	         for k, l, n, v, h in rows if n]
	return {
		"company": company,
		"total": sum(i["count"] for i in items),
		"items": items,
		# Everything, including the zeroes, so the console can show "nothing waiting"
		# for a queue instead of silently omitting it.
		"all": [{"key": k, "label": l, "count": n, "view": v, "hint": h}
		        for k, l, n, v, h in rows],
	}


# --------------------------------------------------------------------------- #
# Self-approval policy (§7, §38)
# --------------------------------------------------------------------------- #
#: Whether one HR person may both record and decide a given process, and why.
#: This is the enforced answer, not a description of one: each row names the guard that
#: implements it, and :func:`self_approval_policy` reads the live setting where the rule
#: is configurable, so the table cannot drift away from behaviour.
SELF_APPROVAL_POLICY = (
	{
		"process": "Leave", "same_user": True, "configurable": False,
		"guard": "perms.LEAVE_APPROVE (HR Manager)",
		"reason": "No money moves and the entitlement itself is enforced by ERPNext's leave "
		          "ledger — an over-allocation is refused whoever approves it. Requiring a "
		          "second HR person would stall ordinary absence administration.",
	},
	{
		"process": "Attendance justification", "same_user": True, "configurable": False,
		"guard": "perms.ATTENDANCE_WRITE; extraordinary re-justify = HR Manager",
		"reason": "Low value, and the five-day window plus the HR-Manager-only override "
		          "already stops silent back-dating.",
	},
	{
		"process": "Employee document", "same_user": True, "configurable": False,
		"guard": "perms.DOCUMENT_WRITE; confidential = HR Manager",
		"reason": "Filing evidence somebody handed over is clerical. Confidentiality, not "
		          "duality, is the control that matters here.",
	},
	{
		"process": "Contract", "same_user": False, "configurable": False,
		"guard": "contracts.assert_transition — preparer may not approve",
		"reason": "A contract sets the legal terms of employment. The person who typed the "
		          "terms should not be the person who accepts them.",
	},
	{
		"process": "Salary change", "same_user": False, "configurable": True,
		"guard": "salary_change._is_self_approval; Isoft HR Settings → "
		         "require_separate_salary_change_approval",
		"reason": "Directly changes pay. This is the single highest-value unaudited action "
		          "in an HR system.",
	},
	{
		"process": "Salary advance", "same_user": False, "configurable": True,
		"guard": "advances._is_self_approval; Isoft HR Settings → "
		         "require_separate_advance_approval",
		"reason": "Money leaves the company before it is earned, and Finance disburses on "
		          "the strength of the approval.",
	},
	{
		"process": "Bank change", "same_user": False, "configurable": False,
		"guard": "IsoftBankChangeRequest.approve — recording needs BANK_CHANGE_REQUEST, "
		         "approving needs BANK_CHANGE_APPROVE",
		"reason": "Redirecting salary payments is the highest-value fraud target in the "
		          "system. Recording and approving are separate permissions.",
	},
	{
		"process": "Performance", "same_user": False, "configurable": False,
		"guard": "performance.hr_finalise — evaluation and finalisation are separate states",
		"reason": "The evaluation and the sign-off are different judgements, and a finalised "
		          "review can justify a pay recommendation.",
	},
	{
		"process": "Termination / offboarding", "same_user": False, "configurable": False,
		"guard": "perms.CONTRACT_APPROVE (HR Manager)",
		"reason": "Ending employment triggers a final settlement and is not reversible in "
		          "any meaningful sense.",
	},
	{
		"process": "Payroll", "same_user": False, "configurable": False,
		"guard": "payroll_workflow — Payroll Officer → Payroll Manager → Finance",
		"reason": "Unchanged by HR-operated mode. Preparation, approval and payment stay "
		          "three roles; this is a financial control, not an HR convenience.",
	},
)


def self_approval_policy():
	"""The policy above, with the two configurable rows resolved to their live setting."""
	from isoft_angola_hr.isoft_angola_hr.services import advances
	from isoft_angola_hr.isoft_angola_hr.services import salary_change

	live = {
		"Salary change": not salary_change.requires_separate_approval(),
		"Salary advance": not advances.requires_separate_approval(),
	}
	rows = []
	for row in SELF_APPROVAL_POLICY:
		out = dict(row)
		if row["process"] in live:
			out["same_user"] = bool(live[row["process"]])
		rows.append(out)
	return rows


# --------------------------------------------------------------------------- #
# Where the login model stands (§42)
# --------------------------------------------------------------------------- #
def login_dependencies(company=None):
	"""Prove, from live data, that no HR process depends on an employee or manager login.

	Returned as evidence rather than as a claim: it reports how many employees have no
	User and how many have no manager, and asserts for each process whether that matters.
	An assertion nobody can check is not a control.
	"""
	perms.require(perms.HR_READINESS)
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	perms.require_company(company)

	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	total = frappe.db.count("Employee", filters)
	no_user = cint(frappe.db.sql(
		"""select count(*) from `tabEmployee` where status = 'Active'
		  and ifnull(user_id, '') = ''{0}""".format(" and company = %s" if company else ""),
		(company,) if company else ())[0][0])
	no_manager = cint(frappe.db.sql(
		"""select count(*) from `tabEmployee` where status = 'Active'
		  and ifnull(reports_to, '') = ''{0}""".format(" and company = %s" if company else ""),
		(company,) if company else ())[0][0])

	processes = [
		("Employment contract", False, False, "hr_api.create_contract / contract_action"),
		("Leave", False, False, "api.create_leave / approve_leave / reject_leave"),
		("Attendance justification", False, False,
		 "hr_api.record_justification / api.justify_occurrence"),
		("Salary change", False, False, "hr_api.create_salary_change / salary_change_action"),
		("Salary advance", False, False, "hr_api.create_advance / advance_action"),
		("Bank change", False, False, "hr_api.create_bank_change / bank_change_action"),
		("Employee document", False, False, "hr_api.add_employee_document"),
		("Recruitment", False, False,
		 "hr_api.create_job_opening / create_job_applicant / record_interview_result"),
		("Performance", False, False,
		 "hr_api.create_performance_cycle / record_evaluation / finalise_review"),
		("Transfer", False, False, "ERPNext Employee Transfer (HR Desk)"),
		("Promotion", False, False, "hr_api.create_salary_change (Promotion)"),
		("Offboarding", False, False, "hr_api.exit_checklist / contract_action terminate"),
		("Final settlement", False, False, "api.create_settlement"),
	]
	return {
		"company": company,
		"active_employees": total,
		"without_user_id": no_user,
		"without_manager": no_manager,
		"self_service_available": total - no_user,
		"processes": [{"process": p, "employee_login_required": e,
		               "manager_login_required": m, "hr_entry_point": s}
		              for p, e, m, s in processes],
		"employee_login_required_anywhere": any(p[1] for p in processes),
		"manager_login_required_anywhere": any(p[2] for p in processes),
	}
