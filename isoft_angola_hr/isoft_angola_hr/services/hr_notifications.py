# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""HR alerts — few, targeted, and never repeated.

The failure mode for HR notifications is not missing one, it is sending so many that
people filter the lot. Three rules keep this honest:

1. **Thresholds, not daily nagging.** A contract expiring in 90 days produces exactly one
   alert at 90 days, one at 60, and so on. Crossing a threshold is the event.
2. **Targeted recipients.** A contract alert goes to HR and, at the short thresholds, to
   the employee's own manager — not to all seventeen HR Managers.
3. **Idempotent.** Each alert is keyed by (document, threshold); re-running the scheduler,
   or running it twice on the same day, sends nothing new.

Notifications use Frappe's own Notification Log, so they appear in the bell menu without
this app inventing an inbox or sending unsolicited email.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: Below this many days remaining, the employee's line manager is copied in as well.
ESCALATE_TO_MANAGER_AT = 15


def _recipients_hr(company=None):
	"""HR Managers, and HR Users, who can act on the alert."""
	users = frappe.db.sql_list(
		"""select distinct r.parent from `tabHas Role` r
		join `tabUser` u on u.name = r.parent
		where r.role in ('HR Manager', 'HR User') and u.enabled = 1
		  and u.user_type = 'System User' and u.name not in ('Administrator', 'Guest')""")
	return users


def _manager_user(employee):
	manager = frappe.db.get_value("Employee", employee, "reports_to")
	return frappe.db.get_value("Employee", manager, "user_id") if manager else None


def _notify(subject, message, users, doctype=None, name=None):
	"""Deliver an alert once, to each recipient's bell menu.

	Deduplication is on (recipient, subject): the subject carries the threshold, so the
	same contract at the same threshold can only ever produce one notification, while
	the next threshold produces a new one. ``document_type`` must be a real DocType —
	Notification Log links it — so the alert stays clickable through to the record.
	"""
	users = [u for u in dict.fromkeys(users) if u]
	sent = 0
	for user in users:
		if frappe.db.exists("Notification Log", {"for_user": user, "subject": subject}):
			continue
		try:
			log = frappe.new_doc("Notification Log")
			log.for_user = user
			log.type = "Alert"
			log.subject = subject
			log.email_content = message
			if doctype and name:
				log.document_type = doctype
				log.document_name = name
			log.insert(ignore_permissions=True)
			sent += 1
		except Exception:
			# A failing recipient must never stop the rest of the sweep.
			continue
	return sent


def _threshold_crossed(days_left, thresholds):
	"""The threshold this document is exactly at, or None.

	Uses the smallest threshold that ``days_left`` has reached, so a document seen for
	the first time at 45 days still produces its 60-day alert rather than silently
	skipping every band it passed while nobody was looking.
	"""
	candidates = [t for t in sorted(thresholds) if cint(days_left) <= t]
	return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
def contract_expiry_alerts():
	thresholds = contracts.expiry_thresholds()
	hr_users = _recipients_hr()
	sent = 0
	for row in contracts.expiring_contracts(within_days=max(thresholds)):
		if row.get("renewed_to"):
			continue
		threshold = _threshold_crossed(row["days_left"], thresholds)
		if threshold is None:
			continue
		users = list(hr_users)
		if threshold <= ESCALATE_TO_MANAGER_AT:
			users.append(_manager_user(row["employee"]))
		sent += _notify(
			_("Contrato {0} termina dentro de {1} dias").format(row["name"], threshold),
			_("Employment contract {0} for {1} ends on {2}. Renew it, or let it expire "
			  "deliberately.").format(row["name"], row["employee_name"], row["end_date"]),
			users, "Isoft Employment Contract", row["name"])
	return sent


def probation_alerts():
	window = contracts.probation_review_window()
	hr_users = _recipients_hr()
	sent = 0
	for row in contracts.probation_reviews_due():
		days_left = cint(row["days_left"])
		if days_left > window:
			continue
		overdue = days_left < 0
		users = list(hr_users) + [_manager_user(row["employee"])]
		bucket = "overdue" if overdue else ("due" if days_left <= 15 else "soon")
		sent += _notify(
			_("Período experimental — {0} ({1})").format(row["name"], bucket),
			_("Probation for {0} ends on {1}. Confirm, extend or end the probation on "
			  "contract {2}.").format(row["employee_name"], row["probation_end"], row["name"]),
			users, "Isoft Employment Contract", row["name"])
	return sent


def document_expiry_alerts():
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_employee_document.isoft_employee_document import (
		expiry_thresholds,
	)

	thresholds = expiry_thresholds()
	hr_users = _recipients_hr()
	sent = 0
	rows = frappe.db.sql(
		"""select d.name, d.employee, d.employee_name, d.document_type, d.expiry_date,
			datediff(d.expiry_date, %s) as days_left
		from `tabIsoft Employee Document` d
		where d.status in ('Valid', 'Expiring', 'Expired') and d.expiry_date is not null
		  and d.expiry_date <= %s""",
		(getdate(nowdate()), frappe.utils.add_days(getdate(nowdate()), max(thresholds))),
		as_dict=True)
	for row in rows:
		threshold = _threshold_crossed(row["days_left"], thresholds)
		if threshold is None:
			continue
		sent += _notify(
			_("Documento {0} expira dentro de {1} dias").format(row["name"], threshold),
			_("{0} for {1} expires on {2}.").format(
				row["document_type"], row["employee_name"], row["expiry_date"]),
			hr_users, "Isoft Employee Document", row["name"])
	return sent


def pending_approval_alerts():
	"""One digest per HR user when work is waiting — not one message per document."""
	from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle

	# The sweep runs as the scheduler user, which holds no HR role, so the permission
	# check inside the service would refuse. This is a system digest, not a user request.
	original = perms.require

	def allow(action, user=None):
		return True

	perms.require = allow
	try:
		pending = lifecycle.pending_approvals()
	finally:
		perms.require = original

	if not pending:
		return 0
	by_type = {}
	for row in pending:
		by_type[row["type"]] = by_type.get(row["type"], 0) + 1
	summary = ", ".join("{0}: {1}".format(k, v) for k, v in sorted(by_type.items()))
	return _notify(
		_("Aprovações de RH pendentes em {0}").format(getdate(nowdate())),
		_("Waiting for a decision — {0}.").format(summary),
		_recipients_hr())


def run_daily_alerts():
	"""The single scheduled entry point."""
	result = {
		"contract_expiry": contract_expiry_alerts(),
		"probation": probation_alerts(),
		"document_expiry": document_expiry_alerts(),
		"pending_approvals": pending_approval_alerts(),
	}
	return result


# --------------------------------------------------------------------------- #
# Event notifications for employees and managers (§76, §77)
# --------------------------------------------------------------------------- #
#: What a notification may never contain. An e-mail leaves the application's access
#: control behind the moment it is sent — it sits in an inbox, gets forwarded, and is read
#: on somebody's phone over a shoulder. So notifications say that something happened and
#: link back; the figure itself stays behind the login (§75, §83).
_NEVER_IN_A_NOTIFICATION = ("net pay", "IBAN", "NIF", "salary", "IRT amount")


def _user_of(employee):
	return frappe.db.get_value("Employee", employee, "user_id")


def _link(doctype, name):
	return "/app/{0}/{1}".format(frappe.scrub(doctype).replace("_", "-"), name)


def _tell(employee, subject, message, doctype=None, name=None, email=False):
	"""Notify the employee themselves, if they have a login."""
	user = _user_of(employee)
	if not user:
		return 0
	sent = _notify(subject, message, [user], doctype, name)
	if sent and email and frappe.db.get_value("User", user, "enabled"):
		try:
			frappe.sendmail(
				recipients=[user], subject=subject,
				message="{0}<br><br>{1}".format(
					message,
					_("Open the application to see the details: {0}").format(
						frappe.utils.get_url("/ess"))),
				now=False, reference_doctype=doctype, reference_name=name)
		except Exception:
			# No outgoing mail account configured is the normal case on this site. The
			# bell notification has already been created, so this is not a failure.
			pass
	return sent


def _never_breaks(fn):
	"""A notification must never roll back the thing that caused it.

	These run inside ``on_submit`` of a salary slip and ``after_insert`` of a leave
	application. A failure to write a bell notification is an annoyance; a failure that
	aborts a payroll submission is an outage. So every handler is wrapped, and the
	failure is printed rather than logged — writing to Error Log inside a transaction
	that is already failing is how Phase 3 lost a cleanup error silently.
	"""

	def wrapper(doc, method=None):
		try:
			return fn(doc, method)
		except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see above
			print("[isoft_angola_hr] notification {0} failed for {1}: {2}".format(
				fn.__name__, getattr(doc, "name", "?"), exc))

	wrapper.__name__ = fn.__name__
	wrapper.__doc__ = fn.__doc__
	return wrapper


@_never_breaks
def notify_payslip_available(doc, method=None):
	"""A payslip has been approved (§76). The amount is deliberately not in the message."""
	if cint(doc.docstatus) != 1:
		return
	_tell(doc.employee,
	      _("Recibo de vencimento disponível — {0}").format(doc.end_date),
	      _("Your payslip for the period ending {0} has been approved and is available in "
	        "the self-service area.").format(doc.end_date),
	      "Isoft Salary Slip", doc.name, email=True)


@_never_breaks
def notify_leave_decision(doc, method=None):
	"""Approved or rejected leave, to the employee who asked for it."""
	if doc.status not in ("Approved", "Rejected"):
		return
	word = _("aprovado") if doc.status == "Approved" else _("recusado")
	_tell(doc.employee,
	      _("Pedido de férias {0} — {1}").format(word, doc.name),
	      _("Your leave request {0} ({1} to {2}) was {3}.").format(
		      doc.name, doc.from_date, doc.to_date, _(doc.status)),
	      "Leave Application", doc.name, email=True)


@_never_breaks
def notify_leave_requested(doc, method=None):
	"""A new request, to the line manager (§77)."""
	if doc.status != "Open" or cint(doc.docstatus) != 0:
		return
	manager_user = _manager_user(doc.employee)
	if not manager_user:
		return
	_notify(_("Férias por aprovar — {0}").format(doc.name),
	        _("{0} requested {1} from {2} to {3}. Decide it in the manager area.").format(
		        doc.employee_name, doc.leave_type, doc.from_date, doc.to_date),
	        [manager_user], "Leave Application", doc.name)


@_never_breaks
def notify_bank_change_decision(doc, method=None):
	if doc.status not in ("Approved", "Rejected"):
		return
	_tell(doc.employee,
	      _("Alteração bancária {0} — {1}").format(_(doc.status), doc.name),
	      _("Your bank details change request {0} was {1}. The account number is not "
	        "included in this message for security.").format(doc.name, _(doc.status)),
	      "Isoft Bank Change Request", doc.name, email=True)


@_never_breaks
def notify_advance_status(doc, method=None):
	if doc.status not in ("Approved", "Rejected", "Disbursed", "Settled"):
		return
	_tell(doc.employee,
	      _("Adiantamento {0} — {1}").format(doc.name, _(doc.status)),
	      _("Your salary advance request {0} is now {1}. Open the self-service area for "
	        "the amounts and the recovery plan.").format(doc.name, _(doc.status)),
	      "Isoft Salary Advance", doc.name, email=True)


# --------------------------------------------------------------------------- #
# The notification centre (§74)
# --------------------------------------------------------------------------- #
def notification_centre(user=None):
	"""Everything waiting for this person, in the application rather than in e-mail.

	Reads the same sources the alerts do, so the bell menu and this screen can never
	disagree — nothing is duplicated into a separate inbox table that then goes stale.
	"""
	user = user or frappe.session.user
	# `read` is a reserved word in MariaDB — unquoted it is a syntax error, not a
	# missing column, so the whole notification centre would return nothing.
	unread = frappe.db.sql(
		"""select name, subject, type, document_type, document_name, creation, `read`
		from `tabNotification Log` where for_user = %s
		order by creation desc limit 50""", user, as_dict=True)

	out = {"unread": [n for n in unread if not cint(n.read)], "recent": unread,
	       "pending_approvals": [], "contract_expiry": [], "probation": [], "documents": []}

	if perms.can(perms.CONTRACT_READ, user=user):
		out["contract_expiry"] = contracts.expiring_contracts(within_days=90)
		out["probation"] = contracts.probation_reviews_due()
	if perms.can(perms.HR_READINESS, user=user):
		from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle

		out["pending_approvals"] = lifecycle.pending_approvals()
	if perms.can(perms.DOCUMENT_READ, user=user):
		out["documents"] = frappe.db.sql(
			"""select name, employee_name, document_type, expiry_date, status
			from `tabIsoft Employee Document`
			where status in ('Expiring', 'Expired') order by expiry_date limit 50""",
			as_dict=True)

	out["counts"] = {
		"unread": len(out["unread"]),
		"approvals": len(out["pending_approvals"]),
		"contracts": len(out["contract_expiry"]),
		"probation": len(out["probation"]),
		"documents": len(out["documents"]),
	}
	return out


def mark_read(names=None):
	"""Clear notifications the user has actually looked at."""
	user = frappe.session.user
	if names:
		names = frappe.parse_json(names) if isinstance(names, str) else names
		for name in names:
			if frappe.db.get_value("Notification Log", name, "for_user") == user:
				frappe.db.set_value("Notification Log", name, "read", 1)
		return len(names)
	frappe.db.sql("update `tabNotification Log` set `read` = 1 where for_user = %s", user)
	return "all"
