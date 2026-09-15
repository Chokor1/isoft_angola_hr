# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The approval gate: what has to be true before a payroll may be approved.

Approval is the moment the numbers are frozen and handed to Finance. Everything that
would stop the run *after* that point — an account nobody mapped, a slip that belongs to
no run, an employee whose salary profile vanished, a period that a readiness check
already flags as blocked — must be found here, while the run is still correctable, and
stated in a sentence the approver can act on.

Nothing here writes, and nothing here decides an account: a blocker names what is
missing and who owns it (Finance for accounts, HR for employee data, Payroll for slips).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

FINANCE, HR, PAYROLL = "Finance", "HR", "Payroll"


def _blocker(code, owner, message, detail=None):
	return {"code": code, "owner": owner, "message": message, "detail": detail}


def _slips(entry):
	names = [r.salary_slip for r in entry.get("employees") or [] if r.get("salary_slip")]
	if not names:
		return []
	return [frappe.get_doc("Isoft Salary Slip", n) for n in names
	        if frappe.db.exists("Isoft Salary Slip", n)]


def approval_blockers(entry, readiness_report=None):
	"""Every reason this run may not be approved, in business language.

	Codes:
	    APR-001  missing or unusable payroll account (for the components this run uses)
	    APR-002  a salary component of the catalogue has no account at all
	    APR-003  blocking readiness exception for an employee of the run
	    APR-004  employee without a salary profile
	    APR-005  invalid salary structure (ambiguous, zero base, profile of another employee)
	    APR-006  orphan / unlisted / duplicated salary slips
	    APR-007  journal requirements (payable, cost centre, currency, fiscal year, drafts)
	    APR-008  the mapping moved after the previous payroll was posted (Finance must confirm)
	"""
	out = []
	company = entry.get("company")
	start, end = getdate(entry.get("start_date")), getdate(entry.get("end_date"))
	settings = frappe.get_cached_doc("Isoft HR Settings")
	slips = _slips(entry)
	slip_docs = [s.as_dict() for s in slips]

	# 1. accounts the run needs -----------------------------------------------
	seen = set()
	for problem in pa.accrual_problems(slip_docs, settings) if slip_docs else []:
		msg = pa.business_message(problem)
		if msg not in seen:
			seen.add(msg)
			out.append(_blocker("APR-001", FINANCE, msg,
			                    _("{0}: {1} — required: {2} — {3}").format(
				                    problem["label"], problem["problem"], problem["required"],
				                    problem["where"])))
	for problem in pa.payment_problems(slip_docs, settings) if slip_docs else []:
		msg = pa.business_message(problem)
		if msg not in seen:
			seen.add(msg)
			out.append(_blocker("APR-001", FINANCE, msg,
			                    _("{0}: {1} — required: {2} — {3}").format(
				                    problem["label"], problem["problem"], problem["required"],
				                    problem["where"])))

	# 2. the whole catalogue ---------------------------------------------------
	checklist = {i["key"]: i for i in pa.finance_checklist(company, settings, include_readiness=False)}
	for reason in checklist["components"]["reasons"]:
		if reason not in seen:
			seen.add(reason)
			out.append(_blocker("APR-002", FINANCE, reason, checklist["components"]["detail"]))
	for key in ("payable", "bank"):
		for reason in checklist[key]["reasons"]:
			if reason not in seen:
				seen.add(reason)
				out.append(_blocker("APR-002", FINANCE, reason, checklist[key]["detail"]))

	# 8. drift ---------------------------------------------------------------
	if not checklist["mapping_drift"]["ok"]:
		out.append(_blocker("APR-008", FINANCE, checklist["mapping_drift"]["reasons"][0],
		                    checklist["mapping_drift"]["detail"]))

	# 3. readiness ------------------------------------------------------------
	members = {r.employee: (r.employee_name or r.employee) for r in entry.get("employees") or []}
	blockers = pa._readiness_blockers(company, entry=entry, report=readiness_report)
	by_code = {}
	for b in blockers:
		by_code.setdefault(b["code"], []).append(b)
	for code, rows in sorted(by_code.items()):
		if code == "EXC-006":
			continue        # accounts are reported above, in business language
		who = sorted({r["employee_name"] or r["employee"] for r in rows if r.get("employee")})
		label = _(rows[0]["label"])
		if who:
			out.append(_blocker("APR-003", HR if code in ("EXC-001", "EXC-002", "EXC-011", "EXC-013") else PAYROLL,
			                    _("{0}: {1} employee(s) — {2}{3}. Resolve this in Payroll Readiness "
			                      "before approving.").format(
				                    label, len(who), ", ".join(who[:6]), "…" if len(who) > 6 else ""),
			                    "; ".join(r["message"] for r in rows[:6])))
		else:
			out.append(_blocker("APR-003", PAYROLL, "{0}: {1}".format(label, rows[0]["message"])))

	# 4 / 5. salary profiles and structures ----------------------------------
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
		get_active_profile,
	)
	no_profile, invalid = [], []
	for row in entry.get("employees") or []:
		who = row.employee_name or row.employee
		try:
			profile = get_active_profile(row.employee, end, company=company, employee_name=who)
		except frappe.ValidationError as exc:
			invalid.append("{0} ({1})".format(who, frappe.utils.strip_html(str(exc))[:120]))
			continue
		if not profile:
			no_profile.append(who)
			continue
		problems = []
		if flt(profile.base) <= 0:
			problems.append(_("base salary is zero"))
		if row.salary_profile and row.salary_profile != profile.name:
			problems.append(_("the run names profile {0} but {1} is the one in force").format(
				row.salary_profile, profile.name))
		if profile.employee != row.employee:
			problems.append(_("profile belongs to another employee"))
		if problems:
			invalid.append("{0} ({1})".format(who, "; ".join(problems)))
	if no_profile:
		out.append(_blocker("APR-004", HR,
		                    _("{0} employee(s) have no salary profile: {1}. HR must create one or "
		                      "remove them from the run.").format(
			                    len(no_profile), ", ".join(no_profile[:6]))))
	if invalid:
		out.append(_blocker("APR-005", HR,
		                    _("{0} employee(s) have an invalid salary structure: {1}.").format(
			                    len(invalid), "; ".join(invalid[:4]))))

	# 6. slips: every row has a slip, every slip belongs here, nothing else covers the
	#    period, nobody appears twice -----------------------------------------
	missing_slip = [(r.employee_name or r.employee) for r in entry.get("employees") or []
	                if not r.get("salary_slip") or not frappe.db.exists("Isoft Salary Slip", r.salary_slip)]
	if missing_slip:
		out.append(_blocker("APR-006", PAYROLL,
		                    _("{0} employee(s) in the run have no salary slip: {1}. Recalculate the "
		                      "payroll.").format(len(missing_slip), ", ".join(missing_slip[:6]))))
	listed = {r.salary_slip for r in entry.get("employees") or [] if r.get("salary_slip")}
	foreign = [s for s in slips if (s.payroll_entry or "") != entry.name]
	if foreign:
		out.append(_blocker("APR-006", PAYROLL,
		                    _("{0} salary slip(s) listed in this run belong to another run: "
		                      "{1}.").format(len(foreign), ", ".join(s.name for s in foreign[:6]))))
	cancelled = [s for s in slips if cint(s.docstatus) == 2]
	if cancelled:
		out.append(_blocker("APR-006", PAYROLL,
		                    _("{0} salary slip(s) in this run are cancelled: {1}. Recalculate the "
		                      "payroll.").format(len(cancelled), ", ".join(s.name for s in cancelled[:6]))))
	counts = {}
	for r in entry.get("employees") or []:
		counts[r.employee] = counts.get(r.employee, 0) + 1
	dup = [members[e] for e, n in counts.items() if n > 1]
	if dup:
		out.append(_blocker("APR-006", PAYROLL,
		                    _("{0} employee(s) appear more than once in the run: {1}.").format(
			                    len(dup), ", ".join(dup[:6]))))
	if members:
		placeholders = ", ".join(["%s"] * len(members))
		strays = frappe.db.sql(
			"""select name, employee_name, payroll_entry, docstatus from `tabIsoft Salary Slip`
			where company = %s and start_date = %s and end_date = %s and docstatus < 2
			  and employee in ({0}) and name not in ({1})""".format(
				placeholders, ", ".join(["%s"] * len(listed)) if listed else "''"),
			[company, start, end] + list(members) + list(listed), as_dict=True)
		if strays:
			out.append(_blocker(
				"APR-006", PAYROLL,
				_("{0} other salary slip(s) cover the same employees and period outside this "
				  "run: {1}. Cancel them, or they will be paid twice.").format(
					len(strays), ", ".join("{0} ({1}{2})".format(
						s.name, s.employee_name, ", run " + s.payroll_entry if s.payroll_entry
						else ", no run") for s in strays[:6]))))

	# 7. journal requirements -------------------------------------------------
	if not slips:
		out.append(_blocker("APR-007", PAYROLL,
		                    _("The payroll has no salary slips. Calculate it before approval.")))
	negative = [s.employee_name or s.employee for s in slips if flt(s.net_pay) < 0]
	if negative:
		out.append(_blocker("APR-007", PAYROLL,
		                    _("{0} employee(s) have a negative net pay and cannot be posted: "
		                      "{1}.").format(len(negative), ", ".join(negative[:6]))))
	cost_center = frappe.db.get_value("Company", company, "cost_center") if company else None
	if not cost_center:
		out.append(_blocker("APR-007", FINANCE,
		                    _("The company has no default Cost Center, so the payroll Journal "
		                      "Entry cannot be posted. {0}").format(_(pa.CONTACT_FINANCE)),
		                    _("Set Company → Cost Center for {0}.").format(company)))
	company_currency = frappe.db.get_value("Company", company, "default_currency") if company else None
	payroll_currency = settings.get("currency")
	if payroll_currency and company_currency and payroll_currency != company_currency:
		out.append(_blocker("APR-007", FINANCE,
		                    _("Payroll is set to {0} but the company keeps its books in {1}. "
		                      "{2}").format(payroll_currency, company_currency, _(pa.CONTACT_FINANCE))))
	posting_date = getdate(entry.get("posting_date") or end)
	if not frappe.db.exists("Fiscal Year", {"year_start_date": ("<=", posting_date),
	                                        "year_end_date": (">=", posting_date)}):
		out.append(_blocker("APR-007", FINANCE,
		                    _("No Fiscal Year covers the posting date {0}, so nothing can be "
		                      "posted to the ledger. {1}").format(posting_date, _(pa.CONTACT_FINANCE))))
	return out


def assert_approvable(entry, readiness_report=None):
	"""Refuse approval (and submission for approval) with every reason at once."""
	blockers = approval_blockers(entry, readiness_report=readiness_report)
	if not blockers:
		return True
	lines = ["• {0}".format(b["message"]) for b in blockers]
	detail = [b["detail"] for b in blockers if b.get("detail")]
	frappe.throw(
		"{0}<br><br>{1}{2}".format(
			_("Payroll {0} cannot be approved. Payroll cannot proceed until the following "
			  "is resolved:").format(frappe.bold(entry.name)),
			"<br>".join(lines),
			"<br><br><span class='text-muted small'>{0}<br>{1}</span>".format(
				_("Details:"), "<br>".join(detail)) if detail else ""),
		title=_("Payroll Approval Blocked"))


def approval_gate(entry, readiness_report=None):
	"""READY / BLOCKED with the reasons, for the screen. Never trusted by the server."""
	blockers = approval_blockers(entry, readiness_report=readiness_report)
	return {
		"status": pa.BLOCKED if blockers else pa.READY,
		"ok": not blockers,
		"blockers": blockers,
		"headline": _("Payroll cannot proceed until Finance configuration is complete.")
		if any(b["owner"] == FINANCE for b in blockers) else (
			_("Payroll cannot be approved until the items below are resolved.") if blockers
			else _("This payroll can be approved.")),
	}
