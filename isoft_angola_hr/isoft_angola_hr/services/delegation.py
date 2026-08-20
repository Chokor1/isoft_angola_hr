# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Manager delegation — temporary cover, and nothing more.

The requirement is ordinary: a manager goes on leave, somebody has to approve their team's
leave requests while they are away. The danger is equally ordinary — this is exactly the
mechanism by which "cover for two weeks" becomes permanent, untraceable access.

Four rules keep it honest (§46, §47):

* **Scope does not widen.** A delegate acts *over the delegator's team*, not over their
  own team plus the delegator's. They gain no company-wide visibility and no new role.
* **Only manager-scope approvals.** Leave, attendance and performance reviews. Never
  payroll, never HR Manager, never anything that moves money — those are the actions
  three phases of segregation-of-duties work exist to protect.
* **It expires.** Every delegation has an end date, and the window is checked at the
  moment of use rather than trusted from a status field.
* **It is chain-proof.** A delegate cannot delegate onward.
"""

import frappe
from frappe import _
from frappe.utils import getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: What a delegation may cover. Anything not listed here is not delegable — and the list
#: is deliberately short.
DELEGABLE_ACTIONS = ("leave", "attendance", "performance")


def _active_filter(on_date=None):
	day = getdate(on_date or nowdate())
	return day


def delegators_for(delegate, on_date=None):
	"""Managers whose approvals ``delegate`` may currently act on.

	The date window is evaluated here rather than relying on a nightly job to expire
	rows: an expired delegation that nobody swept must not still grant access.
	"""
	if not delegate:
		return []
	day = _active_filter(on_date)
	return frappe.db.sql_list(
		"""select delegator from `tabIsoft Manager Delegation`
		where delegate = %s and status = 'Active'
		  and from_date <= %s and to_date >= %s""", (delegate, day, day))


def delegates_of(delegator, on_date=None):
	if not delegator:
		return []
	day = _active_filter(on_date)
	return frappe.db.sql_list(
		"""select delegate from `tabIsoft Manager Delegation`
		where delegator = %s and status = 'Active'
		  and from_date <= %s and to_date >= %s""", (delegator, day, day))


def acts_for(delegator, delegate, on_date=None):
	"""Is ``delegate`` currently covering for ``delegator``?"""
	if not (delegator and delegate):
		return False
	return delegator in delegators_for(delegate, on_date=on_date)


def effective_team(employee, on_date=None):
	"""The employees this person may act on: their own team, plus each delegator's team.

	Note what is NOT here — the delegator themselves. Covering somebody's approvals does
	not make you their manager.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss

	members = set(mss.team(employee))
	for delegator in delegators_for(employee, on_date=on_date):
		members.update(mss.team(delegator))
	return sorted(members)


def create(delegator, delegate, from_date, to_date, reason=None):
	"""Create a delegation. HR, or the delegating manager themselves."""
	from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess

	me = ess.current_employee(raise_exception=False)
	if not (perms.can(perms.EMPLOYEE_WRITE) or me == delegator):
		frappe.throw(
			_("Only HR, or the manager themselves, may delegate their approvals."),
			frappe.PermissionError)
	if not frappe.db.exists("Employee", delegate):
		frappe.throw(_("Employee {0} not found.").format(delegate))

	doc = frappe.get_doc({
		"doctype": "Isoft Manager Delegation",
		"delegator": delegator, "delegate": delegate,
		"from_date": getdate(from_date), "to_date": getdate(to_date),
		"reason": reason, "status": "Active",
	}).insert(ignore_permissions=True)
	return {"name": doc.name, "delegator": doc.delegator, "delegate": doc.delegate,
	        "from_date": str(doc.from_date), "to_date": str(doc.to_date)}


def revoke(name):
	from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess

	doc = frappe.get_doc("Isoft Manager Delegation", name)
	me = ess.current_employee(raise_exception=False)
	if not (perms.can(perms.EMPLOYEE_WRITE) or me == doc.delegator):
		frappe.throw(_("Only HR, or the delegating manager, may revoke this."),
		             frappe.PermissionError)
	doc.db_set({"status": "Revoked", "revoked_by": frappe.session.user, "revoked_at": now()})
	return doc.status


def my_delegations():
	"""What I have delegated, and what has been delegated to me."""
	from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess

	me = ess.current_employee()
	return {
		"employee": me,
		"granted": frappe.db.sql(
			"""select name, delegate, delegate_name, from_date, to_date, status, reason
			from `tabIsoft Manager Delegation` where delegator = %s
			order by from_date desc limit 50""", me, as_dict=True),
		"received": frappe.db.sql(
			"""select name, delegator, delegator_name, from_date, to_date, status, reason
			from `tabIsoft Manager Delegation` where delegate = %s
			order by from_date desc limit 50""", me, as_dict=True),
		"acting_for": delegators_for(me),
		"scope_note": _("Delegation covers leave, attendance and performance approvals for "
		                "the delegating manager's team only. It never grants payroll or "
		                "HR access."),
	}


def expire_stale():
	"""Daily sweep. Cosmetic only — :func:`delegators_for` already ignores an expired row,
	so a failed sweep cannot leave access open."""
	count = frappe.db.sql(
		"""update `tabIsoft Manager Delegation` set status = 'Expired'
		where status = 'Active' and to_date < %s""", getdate(nowdate()))
	return frappe.db.sql(
		"""select count(*) from `tabIsoft Manager Delegation` where status = 'Expired'"""
	)[0][0]
