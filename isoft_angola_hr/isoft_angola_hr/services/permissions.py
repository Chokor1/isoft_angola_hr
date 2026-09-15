# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Role and company authorisation for payroll.

Before Phase 2 every sensitive endpoint asked the same question — "is the caller an
HR Manager?" — which meant one person could prepare, approve, post, export and pay the
whole payroll. Authorisation is now expressed as ACTIONS, each mapped to the roles that
may perform it, so segregation of duties is defined in one readable table instead of
being implied by dozens of identical guards.

Two rules are enforced everywhere:

* **Role** — the caller holds a role listed for the action.
* **Company** — the caller is allowed to act for that company, honouring standard
  Frappe User Permissions. No custom company ACL is introduced.

Workflow eligibility (state + who prepared it) belongs to :mod:`payroll_workflow`,
which calls into this module for the role half of the question.
"""

import frappe
from frappe import _

# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #
# Created on install/migrate. HR User, HR Manager and Accounts Manager already exist
# in ERPNext and are reused rather than duplicated.
PAYROLL_OFFICER = "Payroll Officer"
PAYROLL_MANAGER = "Payroll Manager"
PAYROLL_FINANCE = "Payroll Finance Approver"

HR_USER = "HR User"
HR_MANAGER = "HR Manager"
ACCOUNTS_MANAGER = "Accounts Manager"
SYSTEM_MANAGER = "System Manager"

#: Roles this app creates. Kept here so install.py and the tests share one definition.
APP_ROLES = (PAYROLL_OFFICER, PAYROLL_MANAGER, PAYROLL_FINANCE)

#: Finance side of the process. Accounts Manager is included deliberately: it is the
#: existing ERPNext role that real finance staff already hold, so a site does not have
#: to invent a new user just to post payroll. HR Manager is NOT here — that separation
#: is the entire point of the phase.
#:
#: Holding one of these roles is necessary but, since audit finding F-03, not sufficient:
#: see :func:`is_payroll_finance`. On the ITEC site every HR user also held Accounts
#: Manager, so the same person prepared, posted, released and paid the payroll while the
#: role table said Finance had done it.
_FINANCE = frozenset({PAYROLL_FINANCE, ACCOUNTS_MANAGER})

_PREPARERS = frozenset({PAYROLL_OFFICER, HR_MANAGER})
_APPROVERS = frozenset({PAYROLL_MANAGER, HR_MANAGER})

#: The HR / payroll-preparation side of the house. A user who holds any of these AND
#: Accounts Manager is an HR person with an accounting role, not Finance — Accounts
#: Manager is granted for many reasons that have nothing to do with payroll. Payroll
#: Finance Approver is different: it exists only to post and pay payroll, so granting
#: it is an explicit decision that the user is Finance for payroll purposes.
HR_SIDE_ROLES = frozenset({HR_USER, HR_MANAGER, PAYROLL_OFFICER, PAYROLL_MANAGER})
_PAYROLL_VIEWERS = frozenset({HR_USER, PAYROLL_OFFICER, PAYROLL_MANAGER, PAYROLL_FINANCE,
                              ACCOUNTS_MANAGER, HR_MANAGER})

# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
HR_ACCESS = "hr.access"                       # generic HR desk access (attendance, leave…)
EMPLOYEE_READ = "employee.read"
EMPLOYEE_WRITE = "employee.write"
ATTENDANCE_WRITE = "attendance.write"
LEAVE_APPROVE = "leave.approve"

SALARY_PROFILE_READ = "salary_profile.read"
SALARY_PROFILE_WRITE = "salary_profile.write"

PAYROLL_READ = "payroll.read"
PAYROLL_PREVIEW = "payroll.preview"
PAYROLL_PREPARE = "payroll.prepare"
PAYROLL_CALCULATE = "payroll.calculate"
PAYROLL_SUBMIT_FOR_APPROVAL = "payroll.submit_for_approval"
PAYROLL_APPROVE = "payroll.approve"
PAYROLL_REJECT = "payroll.reject"
PAYROLL_POST = "payroll.post"
PAYROLL_EXPORT_BANK = "payroll.export_bank"
PAYROLL_CONFIRM_PAYMENT = "payroll.confirm_payment"
#: Readable alias: "pay" is release for payment, the bank file and the payment entries.
PAYROLL_PAY = PAYROLL_CONFIRM_PAYMENT
PAYROLL_CANCEL = "payroll.cancel"
PAYROLL_CLOSE = "payroll.close"

#: Actions that move money or book it. Each is governed by a Finance switch in
#: Isoft HR Settings (see :func:`finance_switch`) and by :func:`is_payroll_finance`.
FINANCE_ACTIONS = frozenset({PAYROLL_POST, PAYROLL_EXPORT_BANK, PAYROLL_CONFIRM_PAYMENT})

#: Which switch governs which finance action. Posting is one decision, paying another.
FINANCE_SWITCH = {
	PAYROLL_POST: "payroll_posting_requires_finance",
	PAYROLL_EXPORT_BANK: "payroll_payment_requires_finance",
	PAYROLL_CONFIRM_PAYMENT: "payroll_payment_requires_finance",
}

STATUTORY_READ = "statutory.read"
STATUTORY_WRITE = "statutory.write"
SETTINGS_WRITE = "settings.write"

# --- Phase 3: HR lifecycle -------------------------------------------------- #
CONTRACT_READ = "contract.read"
CONTRACT_WRITE = "contract.write"
CONTRACT_APPROVE = "contract.approve"
DOCUMENT_READ = "document.read"
DOCUMENT_WRITE = "document.write"
DOCUMENT_CONFIDENTIAL = "document.confidential"
DEPENDANT_READ = "dependant.read"
DEPENDANT_WRITE = "dependant.write"
SALARY_CHANGE_REQUEST = "salary_change.request"
SALARY_CHANGE_APPROVE = "salary_change.approve"
ADVANCE_REQUEST = "advance.request"
ADVANCE_APPROVE = "advance.approve"
ADVANCE_DISBURSE = "advance.disburse"
BANK_CHANGE_APPROVE = "bank_change.approve"
HR_READINESS = "hr.readiness"

# --- HR-operated mode ------------------------------------------------------- #
# The product is operated by HR on behalf of employees and line managers, who are not
# required to hold a login at all. These actions exist so that "HR records what somebody
# asked for" is authorised as its own thing rather than by borrowing the approval
# permission — recording a request must never imply the right to grant it.
BANK_CHANGE_REQUEST = "bank_change.request"
PERFORMANCE_OPERATE = "performance.operate"
RECRUITMENT_OPERATE = "recruitment.operate"

REPORT_PAYROLL = "report.payroll"
REPORT_STATUTORY = "report.statutory"
REPORT_BANK = "report.bank"
REPORT_AUDIT = "report.audit"

#: THE permission matrix. Everything else in the app defers to this table.
ACTION_ROLES = {
	HR_ACCESS: frozenset(_PAYROLL_VIEWERS),
	EMPLOYEE_READ: frozenset(_PAYROLL_VIEWERS),
	EMPLOYEE_WRITE: frozenset({HR_USER, HR_MANAGER}),
	ATTENDANCE_WRITE: frozenset({HR_USER, PAYROLL_OFFICER, HR_MANAGER}),
	LEAVE_APPROVE: frozenset({HR_MANAGER}),

	SALARY_PROFILE_READ: frozenset({HR_USER, PAYROLL_OFFICER, PAYROLL_MANAGER, HR_MANAGER}),
	SALARY_PROFILE_WRITE: frozenset({HR_USER, PAYROLL_OFFICER, HR_MANAGER}),

	PAYROLL_READ: frozenset(_PAYROLL_VIEWERS),
	PAYROLL_PREVIEW: frozenset({HR_USER, PAYROLL_OFFICER, PAYROLL_MANAGER, HR_MANAGER}),
	PAYROLL_PREPARE: frozenset(_PREPARERS),
	PAYROLL_CALCULATE: frozenset(_PREPARERS),
	PAYROLL_SUBMIT_FOR_APPROVAL: frozenset(_PREPARERS),
	PAYROLL_APPROVE: frozenset(_APPROVERS),
	PAYROLL_REJECT: frozenset(_APPROVERS),
	PAYROLL_POST: frozenset(_FINANCE),
	PAYROLL_EXPORT_BANK: frozenset(_FINANCE),
	PAYROLL_CONFIRM_PAYMENT: frozenset(_FINANCE),
	PAYROLL_CANCEL: frozenset(_APPROVERS | _FINANCE),
	PAYROLL_CLOSE: frozenset(_FINANCE | {PAYROLL_MANAGER}),

	STATUTORY_READ: frozenset(_PAYROLL_VIEWERS),
	STATUTORY_WRITE: frozenset({HR_MANAGER}),
	SETTINGS_WRITE: frozenset({HR_MANAGER}),

	# --- Phase 3 -------------------------------------------------------------- #
	CONTRACT_READ: frozenset({HR_USER, HR_MANAGER, PAYROLL_OFFICER, PAYROLL_MANAGER}),
	CONTRACT_WRITE: frozenset({HR_USER, HR_MANAGER}),
	CONTRACT_APPROVE: frozenset({HR_MANAGER}),
	DOCUMENT_READ: frozenset({HR_USER, HR_MANAGER}),
	DOCUMENT_WRITE: frozenset({HR_USER, HR_MANAGER}),
	# Confidential and medical documents are HR Manager only, whatever else a user holds.
	DOCUMENT_CONFIDENTIAL: frozenset({HR_MANAGER}),
	DEPENDANT_READ: frozenset({HR_USER, HR_MANAGER}),
	DEPENDANT_WRITE: frozenset({HR_USER, HR_MANAGER}),
	SALARY_CHANGE_REQUEST: frozenset({HR_USER, HR_MANAGER, PAYROLL_OFFICER}),
	SALARY_CHANGE_APPROVE: frozenset({HR_MANAGER}),
	ADVANCE_REQUEST: frozenset({HR_USER, HR_MANAGER, PAYROLL_OFFICER}),
	ADVANCE_APPROVE: frozenset({HR_MANAGER, PAYROLL_MANAGER}),
	ADVANCE_DISBURSE: frozenset(_FINANCE),
	BANK_CHANGE_APPROVE: frozenset({HR_MANAGER}),
	HR_READINESS: frozenset({HR_USER, HR_MANAGER, PAYROLL_OFFICER, PAYROLL_MANAGER}),

	# Recording a bank change is HR User work; approving it stays HR Manager only, and
	# approval is the only step that writes the Employee record.
	BANK_CHANGE_REQUEST: frozenset({HR_USER, HR_MANAGER}),
	PERFORMANCE_OPERATE: frozenset({HR_USER, HR_MANAGER}),
	RECRUITMENT_OPERATE: frozenset({HR_USER, HR_MANAGER}),

	REPORT_PAYROLL: frozenset({PAYROLL_OFFICER, PAYROLL_MANAGER, PAYROLL_FINANCE,
	                           ACCOUNTS_MANAGER, HR_MANAGER}),
	REPORT_STATUTORY: frozenset({PAYROLL_OFFICER, PAYROLL_MANAGER, PAYROLL_FINANCE,
	                             ACCOUNTS_MANAGER, HR_MANAGER}),
	REPORT_BANK: frozenset(_FINANCE | {HR_MANAGER}),
	REPORT_AUDIT: frozenset({PAYROLL_MANAGER, PAYROLL_FINANCE, ACCOUNTS_MANAGER, HR_MANAGER}),
}

#: Human labels for the matrix rendered by :func:`permission_matrix`.
MATRIX_ROWS = (
	("Employee Read", EMPLOYEE_READ),
	("Employee Write", EMPLOYEE_WRITE),
	("Salary Profile Read", SALARY_PROFILE_READ),
	("Salary Profile Edit", SALARY_PROFILE_WRITE),
	("Payroll Preview", PAYROLL_PREVIEW),
	("Create Payroll", PAYROLL_PREPARE),
	("Calculate Payroll", PAYROLL_CALCULATE),
	("Submit for Approval", PAYROLL_SUBMIT_FOR_APPROVAL),
	("Approve Payroll", PAYROLL_APPROVE),
	("Reject Payroll", PAYROLL_REJECT),
	("Post Accounting", PAYROLL_POST),
	("Generate Bank File", PAYROLL_EXPORT_BANK),
	("Confirm Payment", PAYROLL_CONFIRM_PAYMENT),
	("Modify IRT Table", STATUTORY_WRITE),
	("Modify INSS Rates", STATUTORY_WRITE),
	("View Payroll Reports", REPORT_PAYROLL),
	("View Bank Payment List", REPORT_BANK),
	("Cancel Payroll", PAYROLL_CANCEL),
	("Contract Read", CONTRACT_READ),
	("Contract Edit", CONTRACT_WRITE),
	("Contract Approve", CONTRACT_APPROVE),
	("Employee Document Read", DOCUMENT_READ),
	("Confidential / Medical Document", DOCUMENT_CONFIDENTIAL),
	("Request Salary Change", SALARY_CHANGE_REQUEST),
	("Approve Salary Change", SALARY_CHANGE_APPROVE),
	("Request Salary Advance", ADVANCE_REQUEST),
	("Approve Salary Advance", ADVANCE_APPROVE),
	("Disburse Salary Advance", ADVANCE_DISBURSE),
	("Record Bank Change", BANK_CHANGE_REQUEST),
	("Approve Bank Change", BANK_CHANGE_APPROVE),
	("Record Leave Decision", LEAVE_APPROVE),
	("Operate Performance Reviews", PERFORMANCE_OPERATE),
	("Operate Recruitment", RECRUITMENT_OPERATE),
)

MATRIX_COLUMNS = (HR_USER, PAYROLL_OFFICER, PAYROLL_MANAGER, PAYROLL_FINANCE, ACCOUNTS_MANAGER,
                  HR_MANAGER, SYSTEM_MANAGER)


# --------------------------------------------------------------------------- #
# Role checks
# --------------------------------------------------------------------------- #
def user_roles(user=None):
	return set(frappe.get_roles(user or frappe.session.user))


def finance_switch(action):
	"""Whether the Finance rule applies to ``action`` right now (Isoft HR Settings).

	Read from the stored row on every call: an absent switch means nobody has decided,
	and the safe answer for a control that keeps HR away from the money is ON.
	"""
	field = FINANCE_SWITCH.get(action)
	if not field:
		return False
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
		separation_required,
	)
	return bool(separation_required(field, 1))


def is_payroll_finance(user=None, roles=None):
	"""Is this user Finance for payroll purposes?

	* Payroll Finance Approver — yes: the role exists for nothing else.
	* Accounts Manager — yes, **unless** the user also holds an HR-side role. An HR
	  Manager with Accounts Manager is the situation the audit found at ITEC: the role
	  was granted for accounting screens, and it silently made the payroll preparer
	  the payroll payer as well.
	* Anything else — no.
	"""
	roles = set(roles) if roles is not None else user_roles(user)
	if PAYROLL_FINANCE in roles:
		return True
	if ACCOUNTS_MANAGER in roles and not (roles & HR_SIDE_ROLES):
		return True
	return False


def refusal(action, user=None):
	"""Why ``user`` may not perform ``action`` — or None when they may.

	The capability check proper. The role table is the first gate; for the finance
	actions the Finance rule is the second, whenever its switch is on.
	"""
	allowed = ACTION_ROLES.get(action)
	if allowed is None:
		raise KeyError("Unknown payroll action: {0}".format(action))
	user = user or frappe.session.user
	if user == "Administrator":
		# Frappe grants Administrator every role; pretending otherwise would only produce
		# a check that is wrong about its own system. The identity rules of the workflow
		# (who prepared, who approved) still apply to Administrator.
		return None
	roles = user_roles(user)
	if not (allowed & roles):
		return _("You are not authorised to perform this operation ({0}). Required role: {1}.").format(
			action, ", ".join(sorted(allowed)))
	if action in FINANCE_ACTIONS and finance_switch(action) and not is_payroll_finance(user, roles):
		return _("{0} is a Finance action. It requires the Payroll Finance Approver role, or "
		         "Accounts Manager held by somebody who is not HR or payroll staff. "
		         "(Isoft HR Settings → Payroll Controls.)").format(action)
	return None


def can(action, user=None):
	"""True when the user may perform ``action``: holds an allowed role and, for the
	finance actions, passes the Finance rule. Buttons are rendered from this and every
	endpoint re-checks it through :func:`require`."""
	return refusal(action, user=user) is None


def require(action, user=None):
	"""Raise PermissionError unless the user may perform ``action``."""
	why = refusal(action, user=user)
	if why is None:
		return True
	frappe.throw(why, frappe.PermissionError)


def any_of(actions, user=None):
	return any(can(a, user=user) for a in actions)


def permission_matrix():
	"""The matrix as actually implemented — derived from ACTION_ROLES, never hand-written,
	so documentation cannot drift away from enforcement."""
	rows = []
	for label, action in MATRIX_ROWS:
		allowed = ACTION_ROLES[action]
		rows.append({
			"action": label,
			"key": action,
			"roles": {role: (role in allowed) for role in MATRIX_COLUMNS},
		})
	return {"columns": list(MATRIX_COLUMNS), "rows": rows}


# --------------------------------------------------------------------------- #
# Company isolation
# --------------------------------------------------------------------------- #
def allowed_companies(user=None):
	"""Companies the user may act for, or ``None`` when unrestricted.

	Uses standard Frappe User Permissions. A site that does not restrict anyone keeps
	behaving exactly as before.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	try:
		perms = frappe.permissions.get_user_permissions(user) or {}
	except Exception:
		return None
	entries = perms.get("Company") or []
	names = []
	for entry in entries:
		doc = entry.get("doc") if isinstance(entry, dict) else entry
		if doc:
			names.append(doc)
	return names or None


def can_company(company, user=None):
	if not company:
		return True
	allowed = allowed_companies(user=user)
	return allowed is None or company in allowed


def require_company(company, user=None):
	"""Refuse to act on a company the user is not permitted for.

	Without this a Payroll Manager restricted to Company A could still approve, post or
	export Company B's payroll by passing its name to the API — the dashboard filters
	companies, but the endpoint never did.
	"""
	if can_company(company, user=user):
		return True
	frappe.throw(
		_("You do not have permission for company {0}.").format(frappe.bold(company or "")),
		frappe.PermissionError,
	)


def require_action_and_company(action, company, user=None):
	require(action, user=user)
	require_company(company, user=user)
	return True


def company_filter_sql(alias="", field="company"):
	"""``(condition, values)`` restricting a query to the user's permitted companies."""
	allowed = allowed_companies()
	if not allowed:
		return "", []
	col = "{0}.{1}".format(alias, field) if alias else "`{0}`".format(field)
	return "{0} in ({1})".format(col, ", ".join(["%s"] * len(allowed))), list(allowed)
