# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Manager Self-Service — a team view, scoped server-side to the actual team.

"Manager" is not a role here. Creating a Manager role would mean every holder sees every
employee, which is exactly the accident this module has to avoid. A manager is simply an
employee that other employees report to (``Employee.reports_to``), so the team is derived
from data rather than granted by a role, and it is impossible to hold "manager access"
over somebody who does not report to you.

COMPENSATION IS NOT VISIBLE BY DEFAULT. A line manager sees who is on leave, whose
probation is due and whose contract expires — not what anybody earns. Salary visibility
requires a payroll role, and the check is made per request rather than assumed.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def team(employee=None, include_indirect=False):
	"""The employees reporting to a manager.

	Indirect reports are OFF by default and must be asked for explicitly. A manager three
	levels up quietly seeing two hundred people is the kind of scope creep that turns a
	team view into a company-wide HR export.
	"""
	manager = employee or ess.current_employee()
	direct = frappe.get_all("Employee", filters={"reports_to": manager, "status": "Active"},
	                        pluck="name")
	if not include_indirect:
		return direct

	seen, frontier = set(direct), list(direct)
	while frontier:
		nxt = frappe.get_all("Employee",
		                     filters={"reports_to": ["in", frontier], "status": "Active"},
		                     pluck="name")
		frontier = [n for n in nxt if n not in seen]
		seen.update(frontier)
	return sorted(seen)


def assert_in_team(employee, manager=None, include_indirect=False):
	"""Refuse access to somebody who does not report to the caller.

	HR keeps its own access through the HR actions; this is only about the manager path.
	"""
	if perms.can(perms.EMPLOYEE_READ) and perms.can(perms.CONTRACT_READ):
		# An HR user reaching these endpoints is already authorised by role.
		if perms.can(perms.DOCUMENT_READ):
			return True
	manager = manager or ess.current_employee()
	if employee == manager:
		return True
	if employee in team(manager, include_indirect=include_indirect):
		return True

	# Phase 5: a manager covering for somebody on leave may act on that manager's team,
	# for the delegation window only. Checked here, at the single authorisation point,
	# so delegation can never widen scope anywhere it was not explicitly considered.
	from isoft_angola_hr.isoft_angola_hr.services import delegation

	for delegator in delegation.delegators_for(manager):
		if employee in team(delegator, include_indirect=include_indirect):
			return True

	frappe.throw(
		_("{0} does not report to you.").format(employee), frappe.PermissionError)


def can_see_compensation(user=None):
	"""Salary visibility is an explicit payroll permission, never a side effect of
	managing people."""
	return perms.can(perms.SALARY_PROFILE_READ, user=user)


# --------------------------------------------------------------------------- #
def my_team(include_indirect=False):
	"""Who reports to me, and what state each person is in."""
	members = team(include_indirect=include_indirect)
	if not members:
		return []
	rows = frappe.get_all(
		"Employee", filters={"name": ["in", members]},
		fields=["name", "employee_name", "designation", "department", "date_of_joining",
		        "status", "company", "image"], order_by="employee_name")

	today = getdate(nowdate())
	on_leave = set(frappe.db.sql_list(
		"""select employee from `tabLeave Application`
		where employee in ({0}) and docstatus = 1 and status = 'Approved'
		  and from_date <= %s and to_date >= %s""".format(", ".join(["%s"] * len(members))),
		members + [today, today]))
	contract_rows = {r["employee"]: r for r in frappe.db.sql(
		"""select employee, name, contract_type, end_date, is_open_ended, status,
			probation_end, probation_status
		from `tabIsoft Employment Contract`
		where employee in ({0}) and status in ('Active', 'Expiring')""".format(
			", ".join(["%s"] * len(members))), members, as_dict=True)}

	show_pay = can_see_compensation()
	for row in rows:
		row["on_leave_today"] = row["name"] in on_leave
		row["contract"] = contract_rows.get(row["name"])
		# Explicitly absent rather than silently missing, so the UI can say "you do not
		# have permission" instead of showing a blank where a salary should be.
		row["compensation_visible"] = show_pay
	return rows


def team_leave_requests(status="Open"):
	members = team()
	if not members:
		return []
	conditions = ["employee in ({0})".format(", ".join(["%s"] * len(members)))]
	values = list(members)
	if status:
		conditions.append("status = %s")
		values.append(status)
	return frappe.db.sql(
		"""select name, employee, employee_name, leave_type, from_date, to_date,
			total_leave_days, status, description, docstatus
		from `tabLeave Application` where {0} and docstatus < 2
		order by from_date""".format(" and ".join(conditions)), values, as_dict=True)


def team_attendance_exceptions(from_date=None, to_date=None):
	"""Unresolved attendance cases for the team — the manager's action list."""
	members = team()
	if not members:
		return []
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else frappe.utils.add_months(to_date, -1)
	return frappe.db.sql(
		"""select name, employee, employee_name, occurrence_date, status, hours,
			occurrence_type, justification_deadline, justification_reason
		from `tabIsoft Attendance Occurrence`
		where employee in ({0}) and occurrence_date between %s and %s
		  and status in ('Pending Justification', 'Unjustified')
		order by occurrence_date desc""".format(", ".join(["%s"] * len(members))),
		members + [from_date, to_date], as_dict=True)


def team_availability(on_date=None):
	"""Who is available today, who is away, and why."""
	members = team()
	if not members:
		return {"total": 0, "available": 0, "on_leave": 0, "rows": []}
	day = getdate(on_date or nowdate())
	leave = {r.employee: r for r in frappe.db.sql(
		"""select employee, leave_type, from_date, to_date from `tabLeave Application`
		where employee in ({0}) and docstatus = 1 and status = 'Approved'
		  and from_date <= %s and to_date >= %s""".format(", ".join(["%s"] * len(members))),
		members + [day, day], as_dict=True)}
	rows = []
	for row in frappe.get_all("Employee", filters={"name": ["in", members]},
	                          fields=["name", "employee_name", "designation"],
	                          order_by="employee_name"):
		entry = leave.get(row.name)
		row["available"] = not entry
		row["reason"] = entry.leave_type if entry else None
		rows.append(row)
	return {"total": len(rows), "available": sum(1 for r in rows if r["available"]),
	        "on_leave": sum(1 for r in rows if not r["available"]), "rows": rows,
	        "date": str(day)}


def team_probations():
	members = team()
	if not members:
		return []
	return [r for r in contracts.probation_reviews_due() if r["employee"] in set(members)]


def team_contract_expiry(within_days=90):
	members = set(team())
	if not members:
		return []
	return [r for r in contracts.expiring_contracts(within_days=within_days)
	        if r["employee"] in members]


def team_member(employee):
	"""One team member's HR summary — never their pay unless the caller may see pay."""
	assert_in_team(employee)
	row = frappe.db.get_value(
		"Employee", employee,
		["name", "employee_name", "designation", "department", "branch", "date_of_joining",
		 "status", "company", "company_email", "cell_number"], as_dict=True) or {}
	contract = contracts.active_contract(employee)
	if contract:
		row["contract"] = frappe.db.get_value(
			"Isoft Employment Contract", contract,
			["name", "contract_type", "start_date", "end_date", "is_open_ended",
			 "probation_end", "probation_status", "status"], as_dict=True)
	row["leave_balance_visible"] = True
	row["compensation_visible"] = can_see_compensation()
	# Medical and confidential documents are never part of a manager view.
	row["documents"] = frappe.db.sql(
		"""select name, document_type, expiry_date, status from `tabIsoft Employee Document`
		where employee = %s and ifnull(confidential, 0) = 0
		order by expiry_date is null, expiry_date""", employee, as_dict=True)
	return row


def team_calendar(from_date=None, to_date=None):
	"""Who is away, and when — for planning cover (§44).

	Shows the leave TYPE and nothing else. A calendar that carried the reason would
	broadcast, to a whole team, the medical facts an employee disclosed to one manager
	(§45). The type is what cover planning needs; the reason is not.
	"""
	members = team()
	if not members:
		return {"from_date": None, "to_date": None, "days": [], "rows": [], "team_size": 0}

	to_date = getdate(to_date) if to_date else frappe.utils.add_days(getdate(nowdate()), 45)
	from_date = getdate(from_date) if from_date else getdate(nowdate())
	placeholders = ", ".join(["%s"] * len(members))

	leave = frappe.db.sql(
		"""select la.name, la.employee, la.employee_name, la.leave_type, la.from_date,
			la.to_date, la.total_leave_days, la.status, la.half_day
		from `tabLeave Application` la
		where la.employee in ({0}) and la.docstatus < 2 and la.status != 'Rejected'
		  and la.from_date <= %s and la.to_date >= %s
		order by la.from_date""".format(placeholders),
		members + [to_date, from_date], as_dict=True)

	absent = frappe.db.sql(
		"""select a.employee, a.employee_name, a.attendance_date, a.status
		from `tabAttendance` a
		where a.employee in ({0}) and a.docstatus = 1
		  and a.status in ('Absent', 'Half Day')
		  and a.attendance_date between %s and %s""".format(placeholders),
		members + [from_date, to_date], as_dict=True)

	holidays = []
	holiday_list = frappe.db.get_value("Employee", members[0], "holiday_list")
	if holiday_list:
		holidays = frappe.db.sql(
			"""select holiday_date, description from `tabHoliday`
			where parent = %s and holiday_date between %s and %s order by holiday_date""",
			(holiday_list, from_date, to_date), as_dict=True)

	return {
		"from_date": str(from_date), "to_date": str(to_date),
		"team_size": len(members),
		"leave": [dict(r, approved=(r["status"] == "Approved")) for r in leave],
		"absence": absent,
		"holidays": holidays,
		"privacy_note": _("Only the type of absence is shown. Reasons and medical details "
		                  "are never displayed here."),
	}


def team_member_leave(employee):
	"""One team member's leave balance. A manager planning cover needs the balance; it
	is not compensation and does not require a payroll role."""
	assert_in_team(employee)
	return ess.leave_balance_for(employee)


# --------------------------------------------------------------------------- #
# Decisions a line manager may actually take
# --------------------------------------------------------------------------- #
def leave_decision(name, action, reason=None):
	"""Approve or reject a leave request from somebody who reports to me.

	Scope is checked here; every leave rule stays with ERPNext's own controller, which
	runs in full on submission. A manager who is not the configured ``leave_approver``
	can still decide, because on this site the reporting line is the approval line —
	but only ever for their own reports.
	"""
	doc = frappe.get_doc("Leave Application", name)
	assert_in_team(doc.employee)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("This leave request has already been decided."), frappe.ValidationError)
	if action == "approve":
		doc.status = "Approved"
	elif action == "reject":
		if not (reason or "").strip():
			frappe.throw(_("Give a reason when rejecting a leave request."))
		doc.status = "Rejected"
		doc.description = "{0}\n\n{1}: {2}".format(
			doc.description or "", _("Rejected"), reason).strip()
	else:
		frappe.throw(_("Unknown leave action {0}.").format(action))
	# The manager holds no write permission on Leave Application; authorisation is the
	# reporting line, established above. ERPNext's validation and ledger entries are
	# untouched.
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	doc.submit()
	return {"name": doc.name, "status": doc.status}


def attendance_justification_decision(name, action, reason=None):
	"""Accept or refuse a team member's explanation for an attendance occurrence."""
	doc = frappe.get_doc("Isoft Attendance Occurrence", name)
	assert_in_team(doc.employee)
	if action == "justify":
		doc.status = "Justified"
		doc.justification_date = getdate(nowdate())
	elif action == "reject":
		doc.status = "Unjustified"
	else:
		frappe.throw(_("Unknown attendance action {0}.").format(action))
	doc.approved_by = frappe.session.user
	if reason:
		doc.remarks = "{0}\n{1}".format(doc.remarks or "", reason).strip()
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


#: What a manager may recommend, and what HR does with it. The manager's word is recorded
#: on the contract; the decision itself stays in the Phase 3 probation workflow, which
#: requires CONTRACT_APPROVE. This is the whole point — input without authority.
PROBATION_RECOMMENDATIONS = ("Confirm", "Extend", "Terminate")
RENEWAL_RECOMMENDATIONS = ("Renew", "Do Not Renew")


def probation_recommendation(name, recommendation, notes=None):
	"""Record the line manager's view on a probation. It does not decide anything."""
	if recommendation not in PROBATION_RECOMMENDATIONS:
		frappe.throw(_("Recommendation must be one of: {0}.").format(
			", ".join(PROBATION_RECOMMENDATIONS)))
	doc = frappe.get_doc("Isoft Employment Contract", name)
	assert_in_team(doc.employee)
	if doc.probation_decision:
		frappe.throw(
			_("HR has already decided this probation ({0}); a recommendation no longer "
			  "applies.").format(doc.probation_decision), frappe.ValidationError)
	doc.db_set({
		"manager_recommendation": recommendation,
		"manager_recommendation_notes": notes,
		"manager_recommendation_by": frappe.session.user,
		"manager_recommendation_date": getdate(nowdate()),
	}, update_modified=True)
	return {"name": doc.name, "manager_recommendation": recommendation}


def renewal_recommendation(name, recommendation, notes=None):
	"""Record whether the line manager wants this contract renewed. HR renews."""
	if recommendation not in RENEWAL_RECOMMENDATIONS:
		frappe.throw(_("Recommendation must be one of: {0}.").format(
			", ".join(RENEWAL_RECOMMENDATIONS)))
	doc = frappe.get_doc("Isoft Employment Contract", name)
	assert_in_team(doc.employee)
	if doc.renewed_to:
		frappe.throw(_("This contract has already been renewed as {0}.").format(doc.renewed_to),
		             frappe.ValidationError)
	doc.db_set({
		"renewal_recommendation": recommendation,
		"manager_recommendation_notes": notes,
		"manager_recommendation_by": frappe.session.user,
		"manager_recommendation_date": getdate(nowdate()),
	}, update_modified=True)
	return {"name": doc.name, "renewal_recommendation": recommendation}


def approval_inbox():
	"""Everything waiting on this manager, in one list (§26)."""
	out = []
	for row in team_leave_requests(status="Open"):
		out.append({
			"type": _("Leave"), "doctype": "Leave Application", "name": row["name"],
			"employee": row["employee"], "employee_name": row["employee_name"],
			"detail": "{0} — {1} → {2} ({3}d)".format(
				row["leave_type"], row["from_date"], row["to_date"], row["total_leave_days"]),
			"date": str(row["from_date"]), "action": "leave",
		})
	for row in team_attendance_exceptions():
		out.append({
			"type": _("Attendance"), "doctype": "Isoft Attendance Occurrence",
			"name": row["name"], "employee": row["employee"],
			"employee_name": row["employee_name"],
			"detail": "{0} — {1}".format(row["occurrence_type"], row["status"]),
			"date": str(row["occurrence_date"]), "action": "attendance",
		})
	for row in team_probations():
		out.append({
			"type": _("Probation"), "doctype": "Isoft Employment Contract",
			"name": row["name"], "employee": row["employee"],
			"employee_name": row["employee_name"],
			"detail": _("Probation ends {0}").format(row["probation_end"]),
			"date": str(row["probation_end"]), "action": "probation",
		})
	out.sort(key=lambda r: r.get("date") or "")
	return out


def dashboard():
	"""One call for the manager home screen."""
	members = team()
	return {
		"manager": ess.current_employee(),
		"team_size": len(members),
		"availability": team_availability(),
		"pending_leave": team_leave_requests(status="Open"),
		"attendance_exceptions": team_attendance_exceptions(),
		"probations_due": team_probations(),
		"contracts_expiring": team_contract_expiry(),
		"compensation_visible": can_see_compensation(),
		"inbox_count": len(approval_inbox()),
	}
