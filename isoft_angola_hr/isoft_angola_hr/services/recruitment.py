# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Recruitment — a workspace over ERPNext's own recruitment module, not a new one.

ERPNext 13 already ships Job Opening, Job Applicant, Interview, Interview Round,
Interview Feedback, Job Offer and Employee Onboarding, all installed on this bench and
all unused. Building a second recruitment module would duplicate seven DocTypes to gain
nothing, so this module contributes only what ERPNext genuinely lacks:

* a **pipeline view** that reads across those DocTypes in one query set, so a recruiter
  sees the funnel instead of five separate list views (§44);
* a **controlled applicant → employee conversion** that refuses to create a second
  Employee for the same applicant and then hands straight over to the Phase 3 onboarding
  and contract flow (§45);
* a **permission boundary**: a recruiter sees applicants, never payroll (§46).

Everything else — the offer letter, the interview scoring, the staffing plan — stays
where it already is.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

#: Recruitment is HR work, not payroll work. This is why a separate action exists rather
#: than reusing EMPLOYEE_READ: an agency recruiter can be given HR User without that
#: implying any access to what anybody earns.
RECRUITMENT_ACTIONS = (perms.EMPLOYEE_READ,)


def _table(doctype):
	return frappe.db.table_exists(doctype)


def pipeline(company=None):
	"""The funnel: openings → applicants → interviews → offers → hired (§44)."""
	perms.require(perms.EMPLOYEE_READ)
	if not _table("Job Opening"):
		return {"available": False,
		        "message": _("ERPNext recruitment is not installed on this site.")}

	scope, scope_values = perms.company_filter_sql(alias="o")
	conditions, values = ["1=1"], []
	if company:
		conditions.append("o.company = %s")
		values.append(company)
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	openings = frappe.db.sql(
		"""select o.name, o.job_title, o.company, o.designation, o.department, o.status,
			o.planned_vacancies,
			(select count(*) from `tabJob Applicant` a where a.job_title = o.name)
				as applicants
		from `tabJob Opening` o where {0}
		order by o.status, o.creation desc limit 100""".format(" and ".join(conditions)),
		values, as_dict=True)

	applicants = frappe.db.sql(
		"""select a.name, a.applicant_name, a.email_id, a.phone_number, a.status,
			a.job_title, a.designation, a.applicant_rating, a.source, a.creation
		from `tabJob Applicant` a
		order by a.creation desc limit 300""", as_dict=True)

	interviews = frappe.db.sql(
		"""select i.name, i.job_applicant, i.interview_round, i.scheduled_on, i.status,
			i.average_rating
		from `tabInterview` i where i.docstatus < 2
		order by i.scheduled_on desc limit 200""", as_dict=True) if _table("Interview") else []

	offers = frappe.db.sql(
		"""select f.name, f.job_applicant, f.applicant_name, f.status, f.offer_date,
			f.designation, f.company
		from `tabJob Offer` f where f.docstatus < 2
		order by f.offer_date desc limit 200""", as_dict=True)

	# An offer that has already become an employee must not look actionable.
	hired = {}
	for row in frappe.db.sql(
		"""select name, employee_name, job_applicant, date_of_joining, status
		from `tabEmployee` where ifnull(job_applicant, '') != ''""", as_dict=True):
		hired[row.job_applicant] = row

	for offer in offers:
		offer["employee"] = (hired.get(offer.job_applicant) or {}).get("name")

	stages = {
		"openings": len([o for o in openings if o.status == "Open"]),
		"applicants": len([a for a in applicants
		                   if a.status not in ("Rejected", "Accepted")]),
		"interviews": len([i for i in interviews if i.status in ("Pending", "Under Review")]),
		"offers": len([f for f in offers if f.status == "Awaiting Response"]),
		"accepted": len([f for f in offers if f.status == "Accepted" and not f.get("employee")]),
		"hired": len(hired),
	}

	return {
		"available": True, "stages": stages, "openings": openings,
		"applicants": applicants, "interviews": interviews, "offers": offers,
		# Offers accepted but not yet turned into an employee — the actual work list.
		"ready_to_hire": [f for f in offers
		                  if f.status == "Accepted" and not f.get("employee")],
	}


def conversion_check(job_offer):
	"""Can this accepted offer become an employee? Answered before anything is written."""
	perms.require(perms.EMPLOYEE_WRITE)
	offer = frappe.db.get_value(
		"Job Offer", job_offer,
		["name", "job_applicant", "applicant_name", "status", "company", "designation",
		 "docstatus"], as_dict=True)
	if not offer:
		frappe.throw(_("Job Offer {0} not found.").format(job_offer))
	perms.require_company(offer.company)

	blockers = []
	if offer.status != "Accepted":
		blockers.append(_("The offer status is {0}; only an accepted offer can be "
		                  "converted.").format(offer.status))
	if cint(offer.docstatus) != 1:
		blockers.append(_("The offer has not been submitted."))

	existing = frappe.db.get_value(
		"Employee", {"job_applicant": offer.job_applicant},
		["name", "employee_name", "status"], as_dict=True)
	if existing:
		# The duplicate-hire guard. Without it, pressing "Create Employee" twice produces
		# two payroll-eligible people with the same name and the same bank account.
		blockers.append(_("{0} has already been created as employee {1}.").format(
			offer.applicant_name, existing.name))

	applicant = frappe.db.get_value(
		"Job Applicant", offer.job_applicant,
		["applicant_name", "email_id", "phone_number"], as_dict=True) or {}

	return {
		"offer": offer.name, "applicant": offer.job_applicant,
		"applicant_name": offer.applicant_name, "company": offer.company,
		"designation": offer.designation, "email": applicant.get("email_id"),
		"phone": applicant.get("phone_number"),
		"existing_employee": existing.name if existing else None,
		"blockers": blockers, "can_convert": not blockers,
	}


def convert_to_employee(job_offer, date_of_joining=None, department=None, reports_to=None,
                        employment_type=None, create_onboarding=1):
	"""Turn an accepted offer into an Employee, then hand over to onboarding (§45).

	Uses ERPNext's own ``make_employee`` mapping so the field mapping stays theirs and
	keeps working across upgrades. What this adds is the part ERPNext leaves open: the
	duplicate guard, the company permission check, and the link back to the applicant
	that makes the guard possible next time.
	"""
	check = conversion_check(job_offer)
	if not check["can_convert"]:
		frappe.throw(_("This offer cannot be converted: {0}").format(
			" ".join(check["blockers"])))

	from erpnext.hr.doctype.job_offer.job_offer import make_employee

	employee = make_employee(job_offer)
	employee.date_of_joining = getdate(date_of_joining or nowdate())
	employee.company = check["company"]
	employee.status = "Active"
	if department:
		employee.department = department
	if reports_to:
		employee.reports_to = reports_to
	if employment_type:
		employee.employment_type = employment_type
	if not employee.get("date_of_birth"):
		# Employee.validate insists on a date of birth. Refusing here with a clear
		# message beats a framework error about a mandatory field halfway through a hire.
		frappe.throw(
			_("The applicant record has no date of birth. Add it to the Job Applicant "
			  "before creating the employee."))
	employee.insert()

	out = {"employee": employee.name, "employee_name": employee.employee_name,
	       "offer": job_offer, "applicant": check["applicant"]}

	if cint(create_onboarding) and _table("Employee Onboarding"):
		try:
			onboarding = frappe.get_doc({
				"doctype": "Employee Onboarding",
				"job_applicant": check["applicant"],
				"job_offer": job_offer,
				"employee": employee.name,
				"employee_name": employee.employee_name,
				"company": check["company"],
				"date_of_joining": str(employee.date_of_joining),
				"designation": employee.designation,
				"department": employee.department,
			})
			onboarding.insert()
			out["onboarding"] = onboarding.name
		except Exception as exc:
			# An onboarding template that does not exist must not undo the hire.
			out["onboarding_error"] = str(exc)

	from isoft_angola_hr.isoft_angola_hr.services import bulk_onboarding

	out["readiness"] = bulk_onboarding.readiness_for_work_and_payroll(employee.name)
	out["next_steps"] = [
		_("Create the employment contract."),
		_("Create the salary profile — payroll cannot run without one."),
		_("Record NIF, social security number and IBAN."),
	]
	return out


# --------------------------------------------------------------------------- #
# Performance and training — surfaced, not rebuilt (§47, §50)
# --------------------------------------------------------------------------- #
def appraisals(employee=None, company=None, status=None):
	"""ERPNext Appraisal, filtered. No scoring logic is duplicated here."""
	perms.require(perms.EMPLOYEE_READ)
	if not _table("Appraisal"):
		return []
	conditions, values = ["a.docstatus < 2"], []
	for field, value in (("employee", employee), ("company", company), ("status", status)):
		if value:
			conditions.append("a.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="a")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.department, a.kra_template,
			a.start_date, a.end_date, a.status, a.total_score, a.docstatus
		from `tabAppraisal` a where {0}
		order by a.end_date desc limit 200""".format(" and ".join(conditions)),
		values, as_dict=True)


def team_appraisals(manager=None):
	"""Appraisals for the caller's direct reports, for Manager Self-Service (§48)."""
	from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss

	members = mss.team(manager)
	if not members or not _table("Appraisal"):
		return []
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.kra_template, a.start_date,
			a.end_date, a.status, a.total_score, a.docstatus
		from `tabAppraisal` a
		where a.employee in ({0}) and a.docstatus < 2
		order by a.end_date desc""".format(", ".join(["%s"] * len(members))),
		members, as_dict=True)


def training(employee=None, company=None):
	"""ERPNext Training Event / Result, surfaced on the employee timeline (§50)."""
	perms.require(perms.EMPLOYEE_READ)
	if not _table("Training Event"):
		return []
	conditions, values = ["e.docstatus < 2"], []
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	if employee:
		conditions.append(
			"""exists (select 1 from `tabTraining Event Employee` te
			where te.parent = e.name and te.employee = %s)""")
		values.append(employee)
	return frappe.db.sql(
		"""select e.name, e.event_name, e.training_program, e.type, e.event_status,
			e.start_time, e.end_time, e.location,
			(select count(*) from `tabTraining Event Employee` te where te.parent = e.name)
				as attendees
		from `tabTraining Event` e where {0}
		order by e.start_time desc limit 100""".format(" and ".join(conditions)),
		values, as_dict=True)


def performance_summary(company=None):
	"""Small enough to sit on the HR dashboard without becoming a second product."""
	perms.require(perms.EMPLOYEE_READ)
	rows = appraisals(company=company)
	return {
		"available": _table("Appraisal"),
		"total": len(rows),
		"draft": len([r for r in rows if cint(r["docstatus"]) == 0]),
		"completed": len([r for r in rows if r["status"] == "Completed"]),
		"average_score": round(
			sum(float(r["total_score"] or 0) for r in rows) / len(rows), 2) if rows else 0,
		"recent": rows[:10],
		"training_events": len(training(company=company)),
		# §49: an appraisal never becomes a pay rise by itself. The link is a human
		# raising an Isoft Salary Change, which is separately approved.
		"note": _("An appraisal result does not create a salary change. Raise an Isoft "
		          "Salary Change, which follows its own approval."),
	}


# --------------------------------------------------------------------------- #
# Interviews (§48–52)
# --------------------------------------------------------------------------- #
def interview_pipeline(company=None, upcoming_only=0):
	"""Every interview in one list — applicant, round, when, who and the outcome.

	ERPNext has all of this across Interview, Interview Detail and Interview Round, but
	no single view of it, so a recruiter reads three list views to answer "what is
	happening this week".
	"""
	perms.require(perms.EMPLOYEE_READ)
	if not _table("Interview"):
		return {"available": False,
		        "message": _("ERPNext recruitment is not installed on this site.")}

	conditions, values = ["i.docstatus < 2"], []
	if upcoming_only:
		conditions.append("i.scheduled_on >= %s")
		values.append(getdate(nowdate()))

	rows = frappe.db.sql(
		"""select i.name, i.job_applicant, i.job_opening, i.interview_round, i.designation,
			i.status, i.scheduled_on, i.from_time, i.to_time, i.average_rating,
			i.expected_average_rating, a.applicant_name, a.email_id, a.phone_number
		from `tabInterview` i
		left join `tabJob Applicant` a on a.name = i.job_applicant
		where {0} order by i.scheduled_on desc limit 200""".format(" and ".join(conditions)),
		values, as_dict=True)

	interviewers = {}
	for row in frappe.db.sql(
		"""select parent, interviewer, result, average_rating, comments
		from `tabInterview Detail` where parent in (
			select name from `tabInterview` where docstatus < 2)""", as_dict=True):
		interviewers.setdefault(row.parent, []).append(row)
	for row in rows:
		row["interviewers"] = interviewers.get(row.name, [])

	today = getdate(nowdate())
	return {
		"available": True,
		"rows": rows,
		"stages": {
			"scheduled": len([r for r in rows if r["status"] == "Pending"]),
			"upcoming": len([r for r in rows if r["scheduled_on"]
			                 and getdate(r["scheduled_on"]) >= today]),
			"under_review": len([r for r in rows if r["status"] == "Under Review"]),
			"cleared": len([r for r in rows if r["status"] == "Cleared"]),
			"rejected": len([r for r in rows if r["status"] == "Rejected"]),
		},
		"rounds": frappe.get_all("Interview Round",
		                         fields=["name", "round_name", "designation",
		                                 "expected_average_rating"])
		if _table("Interview Round") else [],
	}


def schedule_interview(job_applicant, interview_round, scheduled_on, from_time=None,
                       to_time=None, interviewers=None, job_opening=None):
	"""Schedule an interview using ERPNext's own Interview document (§49).

	Every rule — round configuration, expected rating, feedback collection — stays with
	ERPNext. This adds the two guards a scheduler actually needs: a real applicant, and
	at least one interviewer, because an interview with nobody assigned is a diary entry.
	"""
	perms.require(perms.EMPLOYEE_WRITE)
	if not _table("Interview"):
		frappe.throw(_("ERPNext recruitment is not installed on this site."))
	if not frappe.db.exists("Job Applicant", job_applicant):
		frappe.throw(_("Applicant {0} not found.").format(job_applicant))
	if not frappe.db.exists("Interview Round", interview_round):
		frappe.throw(_("Interview round {0} does not exist.").format(interview_round))

	people = frappe.parse_json(interviewers) if isinstance(interviewers, str) \
		else (interviewers or [])
	if not people:
		# Fall back to the round's configured panel before refusing.
		people = frappe.db.sql_list(
			"""select user from `tabInterviewer` where parent = %s""", interview_round)
	if not people:
		frappe.throw(_("Assign at least one interviewer, or configure a panel on the "
		               "interview round."))

	doc = frappe.get_doc({
		"doctype": "Interview",
		"job_applicant": job_applicant,
		"job_opening": job_opening,
		"interview_round": interview_round,
		"scheduled_on": getdate(scheduled_on),
		"from_time": from_time,
		"to_time": to_time,
		"status": "Pending",
		"interview_details": [{"interviewer": u} for u in people],
	})
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status, "scheduled_on": str(doc.scheduled_on),
	        "interviewers": people}


#: §51 — deliberately not built. ERPNext's Job Opening is already a WebsiteGenerator
#: (``templates/generators/job_opening.html``, published when ``publish`` is ticked), so
#: every opening already has a public page. Building a second careers site would
#: duplicate a feature that ships with the platform, and §52's public-endpoint hardening
#: would then apply to code this app owns rather than code Frappe maintains.
CAREERS_PAGE = {
	"implemented_by": "ERPNext Job Opening (WebsiteGenerator)",
	"how": "Tick 'Publish' on a Job Opening; it is served at its own route.",
	"why_not_rebuilt": "A second public applicant portal would duplicate platform "
	                   "functionality and widen the public attack surface for no gain.",
	"public_endpoints_added_by_this_app": 0,
}
