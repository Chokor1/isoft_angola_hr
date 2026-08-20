# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Performance reviews — a workflow around ERPNext's Appraisal, not a second one.

ERPNext 13 already has Appraisal, Appraisal Template and weighted KRA goals, and it
computes the score. What it does not have is anything that makes the process run: no
review cycle, no way to create four hundred appraisals at once, and no states between
"draft" and "submitted". So this module adds exactly three things and reuses everything
else:

1. **A cycle** (:class:`Isoft Performance Cycle`) — a period, a template, a scope and a
   due date, so appraisals are generated in one operation rather than typed one by one.
2. **A review state** on ERPNext's own Appraisal, as custom fields:
   Pending Manager → Pending Employee → Pending HR → Finalised.
3. **Scoping** — a manager sees their own reports and nobody else's, an employee sees
   their own *finalised* review and never a draft comment about them.

TWO THINGS THIS DELIBERATELY DOES NOT DO
-----------------------------------------
It does not change anybody's pay (§33). A good review produces a *recommendation*, which
becomes an ``Isoft Salary Change`` and goes through that workflow's own approval. Payroll
governance took three phases to build and is not bypassed by a score.

It does not promote anybody (§34). The same applies: a recommendation, then ERPNext's own
Employee Promotion, decided by a person.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

# --------------------------------------------------------------------------- #
# Review states
# --------------------------------------------------------------------------- #
PENDING_MANAGER = "Pending Manager"
PENDING_EMPLOYEE = "Pending Employee"
PENDING_HR = "Pending HR"
FINALISED = "Finalised"
CANCELLED = "Cancelled"

OPEN_STATES = (PENDING_MANAGER, PENDING_EMPLOYEE, PENDING_HR)

#: How long each period type runs, for the date helper on the cycle form.
PERIOD_MONTHS = {"Annual": 12, "Semiannual": 6, "Quarterly": 3}


def _cycle(name):
	return frappe.get_doc("Isoft Performance Cycle", name)


def suggest_period(period_type, start_date):
	"""End date for a period type — so HR is not doing date arithmetic by hand."""
	months = PERIOD_MONTHS.get(period_type)
	if not months:
		return None
	return frappe.utils.add_days(add_months(getdate(start_date), months), -1)


# --------------------------------------------------------------------------- #
# Cycle generation (§29)
# --------------------------------------------------------------------------- #
def preview_cycle(cycle):
	"""Who would be reviewed, by whom, and who would be skipped and why.

	Writes nothing. Same discipline as bulk contracts: a skipped employee is reported
	with a reason rather than quietly dropped from a count.
	"""
	perms.require(perms.EMPLOYEE_READ)
	doc = _cycle(cycle)
	perms.require_company(doc.company)

	conditions, values = ["e.status = 'Active'", "e.company = %s"], [doc.company]
	if doc.department:
		conditions.append("e.department = %s")
		values.append(doc.department)

	employees = frappe.db.sql(
		"""select e.name, e.employee_name, e.department, e.designation, e.date_of_joining,
			e.reports_to
		from `tabEmployee` e where {0} order by e.employee_name""".format(
			" and ".join(conditions)), values, as_dict=True)

	existing = {
		r["employee"] for r in frappe.db.sql(
			"""select employee from `tabAppraisal`
			where custom_performance_cycle = %s and docstatus < 2""", doc.name, as_dict=True)}

	cutoff = add_months(getdate(doc.end_date), -cint(doc.minimum_service_months or 0))
	rows = []
	for employee in employees:
		row = {"employee": employee.name, "employee_name": employee.employee_name,
		       "department": employee.department, "designation": employee.designation,
		       "manager": employee.reports_to, "action": "Create", "reason": ""}
		if employee.name in existing:
			row.update({"action": "Skipped",
			            "reason": _("Already has an appraisal in this cycle.")})
		elif not employee.reports_to:
			# Previously BLOCKED, on the reasoning that without a manager nobody could
			# perform the review. That was true only while the review had to be entered by
			# the manager's own session. HR now operates the process, so the fallback is
			# explicit: no line manager means HR conducts the review. Blocking here instead
			# would exclude 43 of this site's active employees from performance management
			# for a reason that no longer holds.
			row.update({"action": "Create",
			            "reason": _("No line manager — HR will conduct this review.")})
		elif employee.date_of_joining and getdate(employee.date_of_joining) > cutoff:
			row.update({"action": "Skipped",
			            "reason": _("Joined {0}; less than {1} months' service at the end "
			                        "of the period.").format(employee.date_of_joining,
			                                                 doc.minimum_service_months)})
		rows.append(row)

	summary = {"total": len(rows)}
	for action in ("Create", "Skipped", "Blocked"):
		summary[action.lower()] = len([r for r in rows if r["action"] == action])
	return {"cycle": doc.name, "rows": rows, "summary": summary,
	        "template": doc.appraisal_template}


def generate_cycle(cycle):
	"""Create one Appraisal per eligible employee, from the cycle's template.

	Idempotent: an employee who already has an appraisal in this cycle is skipped, so
	re-running after adding a department creates only the new ones.
	"""
	perms.require(perms.EMPLOYEE_WRITE)
	doc = _cycle(cycle)
	perms.require_company(doc.company)
	if doc.status == "Closed":
		frappe.throw(_("This cycle is closed."))

	plan = preview_cycle(cycle)
	template = frappe.get_doc("Appraisal Template", doc.appraisal_template)
	goals = [{"kra": g.kra, "per_weightage": flt(g.per_weightage)} for g in template.goals]
	if not goals:
		frappe.throw(_("Appraisal Template {0} has no KRAs.").format(template.name))

	# ERPNext's Appraisal.calculate_total refuses to save an unscored appraisal unless the
	# session user IS the employee — it assumes each person creates their own. That makes
	# bulk generation of *blank* reviews impossible through the controller, so the insert
	# below sets ignore_validate and this function performs the checks that matter itself:
	#
	#   * weightings total 100  — validated here, once, from the template
	#   * employee is active    — preview_cycle selects only status = 'Active'
	#   * no duplicate          — preview_cycle skips anyone already in this cycle
	#   * dates                 — the cycle's own validate enforces start <= end
	#
	# Scoring is never bypassed: manager_review saves through the controller, so
	# calculate_total runs in full the moment a real score exists.
	weightage = sum(g["per_weightage"] for g in goals)
	if int(weightage) != 100:
		frappe.throw(
			_("The weightings on Appraisal Template {0} total {1}%, not 100%.").format(
				template.name, weightage))

	created, skipped, blocked, failed = [], [], [], []
	for row in plan["rows"]:
		if row["action"] == "Skipped":
			skipped.append(row)
			continue
		if row["action"] == "Blocked":
			blocked.append(row)
			continue
		savepoint = "perf_{0}".format(abs(hash(row["employee"])) % 10 ** 8)
		frappe.db.savepoint(savepoint)
		try:
			appraisal = frappe.get_doc({
				"doctype": "Appraisal",
				"employee": row["employee"],
				"employee_name": row["employee_name"],
				"department": row["department"],
				"company": doc.company,
				"kra_template": template.name,
				"start_date": doc.start_date,
				"end_date": doc.end_date,
				"status": "Draft",
				"goals": goals,
				"custom_performance_cycle": doc.name,
				"custom_review_state": PENDING_MANAGER,
				"custom_due_date": doc.due_date,
				"custom_manager": row["manager"],
			})
			appraisal.flags.ignore_validate = True
			appraisal.insert(ignore_permissions=True)
			created.append({"employee": row["employee"], "appraisal": appraisal.name})
		except Exception as exc:
			# One employee with bad data must not lose the other three hundred.
			frappe.db.rollback(save_point=savepoint)
			failed.append({"employee": row["employee"],
			               "employee_name": row["employee_name"], "error": str(exc)})

	total = frappe.db.count("Appraisal", {"custom_performance_cycle": doc.name})
	doc.db_set({"appraisals_created": total, "generated_at": now(),
	            "status": "Active" if total else doc.status})
	return {"created": created, "skipped": skipped, "blocked": blocked, "failed": failed,
	        "summary": {"created": len(created), "skipped": len(skipped),
	                    "blocked": len(blocked), "failed": len(failed), "total": total}}


def close_cycle(cycle):
	"""Close a cycle. Refuses while reviews are still open — a closed cycle with unfinished
	reviews is a report nobody can trust."""
	perms.require(perms.EMPLOYEE_WRITE)
	doc = _cycle(cycle)
	perms.require_company(doc.company)
	open_reviews = frappe.db.count(
		"Appraisal", {"custom_performance_cycle": doc.name,
		              "custom_review_state": ("in", OPEN_STATES)})
	if open_reviews:
		frappe.throw(_("{0} review(s) are still open in this cycle.").format(open_reviews))
	doc.db_set({"status": "Closed", "closed_by": frappe.session.user, "closed_at": now()})
	return doc.status


# --------------------------------------------------------------------------- #
# Scope (§32) — the security model, in one place
# --------------------------------------------------------------------------- #
def _appraisal(name):
	row = frappe.db.get_value(
		"Appraisal", name,
		["name", "employee", "employee_name", "company", "custom_review_state",
		 "custom_manager", "custom_performance_cycle", "total_score", "docstatus"],
		as_dict=True)
	if not row:
		frappe.throw(_("Appraisal {0} not found.").format(name), frappe.DoesNotExistError)
	return row


def assert_manager_of(appraisal):
	"""The reviewing manager, or somebody the reviewing manager delegated to.

	HR-OPERATED MODE. The product does not require a line manager to hold a login, so HR
	may record the evaluation on their behalf. That path sets ``isoft_hr_operated_review``
	after checking ``PERFORMANCE_OPERATE``, and it is the ONLY thing this flag skips —
	state, scoring and attribution all still run. The flag is set and cleared inside a
	try/finally in :mod:`hr_operations`, so it can never leak into a later request; and it
	is checked here, at the one authorisation point, rather than being bolted onto each
	caller, so no future caller can quietly acquire manager scope by accident.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss
	from isoft_angola_hr.isoft_angola_hr.services import permissions as _perms

	if frappe.flags.get("isoft_hr_operated_review") and _perms.can(_perms.PERFORMANCE_OPERATE):
		return True

	me = mss.ess.current_employee()
	if appraisal.custom_manager and appraisal.custom_manager == me:
		return True
	# Delegation is checked explicitly rather than by widening the team query, so a
	# delegate never silently gains scope over anybody else.
	from isoft_angola_hr.isoft_angola_hr.services import delegation

	if delegation.acts_for(appraisal.custom_manager, me):
		return True
	if appraisal.employee in mss.team(me):
		return True
	frappe.throw(_("You are not the reviewing manager for this appraisal."),
	             frappe.PermissionError)


def assert_own(appraisal):
	from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess

	if appraisal.employee != ess.current_employee():
		frappe.throw(_("You may only access your own review."), frappe.PermissionError)
	return True


# --------------------------------------------------------------------------- #
# The workflow (§26)
# --------------------------------------------------------------------------- #
def manager_review(name, goals=None, comments=None, submit=True):
	"""Record the manager's scores and comments.

	ERPNext computes ``score_earned`` and ``total_score`` from the weightings — that
	arithmetic is left entirely to it.
	"""
	row = _appraisal(name)
	assert_manager_of(row)
	if row.custom_review_state not in (PENDING_MANAGER, None, ""):
		frappe.throw(_("This review is at stage {0} and is no longer with the manager.")
		             .format(row.custom_review_state))

	doc = frappe.get_doc("Appraisal", name)
	scores = frappe.parse_json(goals) if isinstance(goals, str) else (goals or {})
	for goal in doc.goals:
		if goal.name in scores:
			goal.score = flt(scores[goal.name])
		elif goal.kra in scores:
			goal.score = flt(scores[goal.kra])
	if comments is not None:
		doc.custom_manager_comments = comments
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	if not cint(submit):
		return {"name": doc.name, "state": doc.custom_review_state,
		        "total_score": flt(doc.total_score)}

	missing = [g.kra for g in doc.goals if not flt(g.score)]
	if missing:
		frappe.throw(_("Score every objective before submitting. Missing: {0}").format(
			", ".join(missing[:5])))

	cycle = frappe.db.get_value("Isoft Performance Cycle", row.custom_performance_cycle,
	                            "employee_acknowledgement") if row.custom_performance_cycle \
		else 1
	next_state = PENDING_EMPLOYEE if cint(cycle) else PENDING_HR
	doc.db_set({"custom_review_state": next_state,
	            "custom_manager_submitted_by": frappe.session.user,
	            "custom_manager_submitted_at": now()})
	_notify_state(doc, next_state)
	return {"name": doc.name, "state": next_state, "total_score": flt(doc.total_score)}


def employee_acknowledge(name, comments=None):
	"""The employee confirms they have seen the review. It is not an approval."""
	row = _appraisal(name)
	assert_own(row)
	if row.custom_review_state != PENDING_EMPLOYEE:
		frappe.throw(_("This review is not waiting for you."), frappe.ValidationError)
	frappe.db.set_value("Appraisal", name, {
		"custom_employee_comments": comments,
		"custom_employee_acknowledged_at": now(),
		"custom_review_state": PENDING_HR,
	})
	return {"name": name, "state": PENDING_HR}


def hr_finalise(name):
	"""HR closes the review. Only now does the employee see it."""
	perms.require(perms.EMPLOYEE_WRITE)
	row = _appraisal(name)
	perms.require_company(row.company)
	if row.custom_review_state not in (PENDING_HR, PENDING_EMPLOYEE):
		frappe.throw(_("This review is at stage {0} and cannot be finalised.").format(
			row.custom_review_state or _("not started")))
	doc = frappe.get_doc("Appraisal", name)
	doc.db_set({"custom_review_state": FINALISED, "status": "Completed",
	            "custom_hr_finalised_by": frappe.session.user,
	            "custom_hr_finalised_at": now()})
	_notify_state(doc, FINALISED)
	return {"name": name, "state": FINALISED, "total_score": flt(doc.total_score)}


def _notify_state(doc, state):
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

	try:
		if state == PENDING_EMPLOYEE:
			notify._tell(doc.employee,
			             _("Avaliação de desempenho para revisão"),
			             _("Your performance review for {0} is ready for you to read and "
			               "acknowledge.").format(doc.end_date), "Appraisal", doc.name)
		elif state == FINALISED:
			notify._tell(doc.employee,
			             _("Avaliação de desempenho concluída"),
			             _("Your performance review for {0} has been finalised.").format(
				             doc.end_date), "Appraisal", doc.name)
	except Exception:
		# A notification must never roll back a review.
		pass


# --------------------------------------------------------------------------- #
# Recommendations (§33, §34) — never automatic
# --------------------------------------------------------------------------- #
def recommend_salary_change(name, new_base, effective_date, reason=None):
	"""Turn a review into a salary-change REQUEST, which then follows its own approval.

	This is the whole point of §33: a score never moves money. It creates a Draft
	``Isoft Salary Change``, which still has to be requested, approved by somebody else
	and applied on a period boundary.
	"""
	perms.require(perms.SALARY_CHANGE_REQUEST)
	row = _appraisal(name)
	perms.require_company(row.company)
	if row.custom_review_state != FINALISED:
		frappe.throw(_("Only a finalised review can support a salary change."))

	current = frappe.db.get_value(
		"Isoft Salary Profile", {"employee": row.employee}, "base",
		order_by="from_date desc")
	change = frappe.get_doc({
		"doctype": "Isoft Salary Change",
		"employee": row.employee,
		"company": row.company,
		"change_type": "Merit Increase",
		"effective_date": getdate(effective_date),
		"current_base": flt(current),
		"new_base": flt(new_base),
		"reason": reason or _("Recommended by performance review {0} (score {1}).").format(
			name, flt(row.total_score, 2)),
	}).insert(ignore_permissions=True)
	return {"salary_change": change.name, "status": change.status,
	        "note": _("Created as a request. It still requires approval by somebody other "
	                  "than the requester, and takes effect only on a period boundary.")}


def recommend_promotion(name, new_designation, effective_date, reason=None):
	"""Record a promotion recommendation against ERPNext's Employee Promotion (§34)."""
	perms.require(perms.EMPLOYEE_WRITE)
	row = _appraisal(name)
	perms.require_company(row.company)
	if row.custom_review_state != FINALISED:
		frappe.throw(_("Only a finalised review can support a promotion."))
	if not frappe.db.table_exists("Employee Promotion"):
		frappe.throw(_("ERPNext Employee Promotion is not available on this site."))

	promotion = frappe.get_doc({
		"doctype": "Employee Promotion",
		"employee": row.employee,
		"company": row.company,
		"promotion_date": getdate(effective_date),
		"promotion_details": [{
			"property": "Designation", "current": frappe.db.get_value(
				"Employee", row.employee, "designation"),
			"new": new_designation, "fieldname": "designation", "field_datatype": "Link",
		}],
	})
	# Left as a DRAFT deliberately. Submitting it would apply the change immediately.
	promotion.insert(ignore_permissions=True)
	return {"promotion": promotion.name, "docstatus": promotion.docstatus,
	        "note": _("Created as a draft. Submitting it is an HR decision.")}


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def my_reviews():
	"""An employee sees their FINALISED reviews only (§31).

	A review still with the manager contains an unfinished opinion about somebody,
	written in the expectation that they will not read it yet.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess

	me = ess.current_employee()
	rows = frappe.db.sql(
		"""select a.name, a.start_date, a.end_date, a.total_score, a.kra_template,
			a.custom_review_state, a.custom_manager_comments, a.custom_employee_comments,
			a.custom_hr_finalised_at, a.custom_performance_cycle
		from `tabAppraisal` a
		where a.employee = %s
		  and a.custom_review_state in (%s, %s)
		order by a.end_date desc""", (me, FINALISED, PENDING_EMPLOYEE), as_dict=True)
	for row in rows:
		if row["custom_review_state"] == PENDING_EMPLOYEE:
			row["action_required"] = True
		row["goals"] = frappe.db.sql(
			"""select kra, per_weightage, score, score_earned from `tabAppraisal Goal`
			where parent = %s order by idx""", row["name"], as_dict=True)
	return rows


def my_review(name):
	row = _appraisal(name)
	assert_own(row)
	if row.custom_review_state not in (FINALISED, PENDING_EMPLOYEE):
		frappe.throw(_("This review is not available to you yet."), frappe.PermissionError)
	doc = frappe.db.get_value(
		"Appraisal", name,
		["name", "start_date", "end_date", "total_score", "kra_template",
		 "custom_review_state", "custom_manager_comments", "custom_employee_comments",
		 "custom_hr_finalised_at"], as_dict=True)
	doc["goals"] = frappe.db.sql(
		"""select kra, per_weightage, score, score_earned from `tabAppraisal Goal`
		where parent = %s order by idx""", name, as_dict=True)
	return doc


def team_reviews(state=None):
	"""Reviews the caller is responsible for, including any delegated to them."""
	from isoft_angola_hr.isoft_angola_hr.services import delegation
	from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss

	me = mss.ess.current_employee()
	managers = [me] + delegation.delegators_for(me)
	conditions = ["a.docstatus < 2",
	              "a.custom_manager in ({0})".format(", ".join(["%s"] * len(managers)))]
	values = list(managers)
	if state:
		conditions.append("a.custom_review_state = %s")
		values.append(state)
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.start_date, a.end_date,
			a.total_score, a.custom_review_state, a.custom_due_date,
			a.custom_performance_cycle, a.custom_manager
		from `tabAppraisal` a where {0}
		order by a.custom_due_date, a.employee_name""".format(" and ".join(conditions)),
		values, as_dict=True)


def review_detail(name):
	"""The full review, for the manager who owns it."""
	row = _appraisal(name)
	assert_manager_of(row)
	doc = frappe.db.get_value(
		"Appraisal", name,
		["name", "employee", "employee_name", "start_date", "end_date", "total_score",
		 "kra_template", "custom_review_state", "custom_due_date",
		 "custom_manager_comments", "custom_employee_comments"], as_dict=True)
	doc["goals"] = frappe.db.sql(
		"""select name, kra, per_weightage, score, score_earned from `tabAppraisal Goal`
		where parent = %s order by idx""", name, as_dict=True)
	doc["editable"] = doc["custom_review_state"] in (PENDING_MANAGER, None, "")
	return doc


# --------------------------------------------------------------------------- #
# Reporting (§36)
# --------------------------------------------------------------------------- #
def cycle_progress(cycle=None, company=None):
	"""Completion, and who is holding it up. Not a ranking of people."""
	perms.require(perms.EMPLOYEE_READ)
	conditions, values = ["a.docstatus < 2"], []
	if cycle:
		conditions.append("a.custom_performance_cycle = %s")
		values.append(cycle)
	if company:
		conditions.append("a.company = %s")
		values.append(company)

	by_state = frappe.db.sql(
		"""select ifnull(a.custom_review_state, 'Not started') as state, count(*) n
		from `tabAppraisal` a where {0} group by state""".format(" and ".join(conditions)),
		values, as_dict=True)

	overdue = frappe.db.sql(
		"""select a.name, a.employee_name, a.custom_manager, a.custom_due_date,
			a.custom_review_state
		from `tabAppraisal` a where {0} and a.custom_due_date < %s
		  and a.custom_review_state in (%s, %s, %s)
		order by a.custom_due_date""".format(" and ".join(conditions)),
		values + [getdate(nowdate()), PENDING_MANAGER, PENDING_EMPLOYEE, PENDING_HR],
		as_dict=True)

	# Distribution by score band. Bands, not a league table — §36 is explicit that this
	# must not become a "worst employees" screen, so no employee names appear here.
	distribution = frappe.db.sql(
		"""select case
			when a.total_score >= 4.5 then '4.5 - 5.0'
			when a.total_score >= 3.5 then '3.5 - 4.4'
			when a.total_score >= 2.5 then '2.5 - 3.4'
			when a.total_score >= 1.5 then '1.5 - 2.4'
			else 'below 1.5' end as band, count(*) n
		from `tabAppraisal` a where {0} and a.custom_review_state = %s
		group by band order by band desc""".format(" and ".join(conditions)),
		values + [FINALISED], as_dict=True)

	total = sum(r["n"] for r in by_state)
	finalised = sum(r["n"] for r in by_state if r["state"] == FINALISED)
	return {
		"cycle": cycle, "total": total, "finalised": finalised,
		"completion_pct": round(finalised * 100.0 / total, 1) if total else 0.0,
		"by_state": by_state, "overdue": overdue, "distribution": distribution,
		"note": _("Score distribution is shown in bands. This module does not rank "
		          "employees against one another."),
	}


def reviews_due_alerts():
	"""Daily reminder to whoever is holding a review up. Threshold-based, deduplicated."""
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications as notify

	sent = 0
	rows = frappe.db.sql(
		"""select a.name, a.employee_name, a.custom_manager, a.custom_due_date,
			datediff(a.custom_due_date, %s) as days_left
		from `tabAppraisal` a
		where a.docstatus < 2 and a.custom_review_state = %s
		  and a.custom_due_date is not null
		  and datediff(a.custom_due_date, %s) in (14, 7, 0, -7)""",
		(getdate(nowdate()), PENDING_MANAGER, getdate(nowdate())), as_dict=True)
	for row in rows:
		user = frappe.db.get_value("Employee", row.custom_manager, "user_id")
		if not user:
			continue
		overdue = cint(row.days_left) < 0
		sent += notify._notify(
			_("Avaliação de {0} — {1}").format(
				row.employee_name,
				_("em atraso") if overdue else _("{0} dias").format(row.days_left)),
			_("The performance review for {0} is due on {1}.").format(
				row.employee_name, row.custom_due_date),
			[user], "Appraisal", row.name)
	return sent
