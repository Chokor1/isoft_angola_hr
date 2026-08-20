# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Whitelisted endpoints for the Phase 3 HR lifecycle.

Kept out of ``api.py`` deliberately — that file already carries the payroll console and
was 2 600 lines before this phase started. Everything here is a thin wrapper: the
authorisation, scoping and workflow decisions all live in ``services/``.

The self-service endpoints take **no employee parameter**. The employee is derived from
the session inside the service, so there is nothing for a caller to tamper with.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from isoft_angola_hr.isoft_angola_hr.services import advances as advance_service
from isoft_angola_hr.isoft_angola_hr.services import bulk_onboarding
from isoft_angola_hr.isoft_angola_hr.services import contract_documents
from isoft_angola_hr.isoft_angola_hr.services import contracts as contract_service
from isoft_angola_hr.isoft_angola_hr.services import employee_lifecycle as lifecycle
from isoft_angola_hr.isoft_angola_hr.services import employee_self_service as ess
from isoft_angola_hr.isoft_angola_hr.services import hr_operations as hr_ops
from isoft_angola_hr.isoft_angola_hr.services import manager_self_service as mss
from isoft_angola_hr.isoft_angola_hr.services import org_analytics
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import recruitment
from isoft_angola_hr.isoft_angola_hr.services import salary_change as salary_change_service


def _parse(value):
	return frappe.parse_json(value) if isinstance(value, str) else value


# --------------------------------------------------------------------------- #
# Employment contracts
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_contract(data):
	perms.require(perms.CONTRACT_WRITE)
	values = _parse(data) or {}
	doc = frappe.get_doc(dict(values, doctype="Isoft Employment Contract"))
	perms.require_company(doc.company or frappe.db.get_value("Employee", doc.employee, "company"))
	doc.insert()
	return doc.name


@frappe.whitelist()
def contract_action(name, action, reason=None):
	doc = frappe.get_doc("Isoft Employment Contract", name)
	status = contract_service.perform(doc, action, reason=reason)
	return {"name": doc.name, "status": status,
	        "allowed_actions": contract_service.allowed_actions(doc)}


@frappe.whitelist()
def renew_contract(name, start_date=None, end_date=None, contract_type=None, notes=None):
	doc = frappe.get_doc("Isoft Employment Contract", name)
	new = contract_service.renew(doc, start_date=start_date, end_date=end_date,
	                             contract_type=contract_type, notes=notes)
	return {"previous": doc.name, "renewal": new.name, "status": new.status}


@frappe.whitelist()
def probation_decision(name, decision, notes=None, new_end=None):
	doc = frappe.get_doc("Isoft Employment Contract", name)
	return contract_service.record_probation_decision(doc, decision, notes=notes,
	                                                  new_end=new_end)


@frappe.whitelist()
def list_contracts(company=None, employee=None, status=None):
	perms.require(perms.CONTRACT_READ)
	conditions, values = ["1=1"], []
	for field, value in (("company", company), ("employee", employee), ("status", status)):
		if value:
			conditions.append("c.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="c")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select c.name, c.employee, c.employee_name, c.company, c.contract_type,
			c.start_date, c.end_date, c.is_open_ended, c.status, c.probation_end,
			c.probation_status, c.renewed_to
		from `tabIsoft Employment Contract` c where {0}
		order by c.start_date desc limit 300""".format(" and ".join(conditions)),
		values, as_dict=True)


@frappe.whitelist()
def contracts_expiring(company=None, within_days=None):
	perms.require(perms.CONTRACT_READ)
	return contract_service.expiring_contracts(company=company, within_days=within_days)


@frappe.whitelist()
def probations_due(company=None):
	perms.require(perms.CONTRACT_READ)
	return contract_service.probation_reviews_due(company=company)


# --------------------------------------------------------------------------- #
# Salary change
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_salary_change(data):
	perms.require(perms.SALARY_CHANGE_REQUEST)
	values = _parse(data) or {}
	# Checked here rather than trusting the Select: Frappe silently coerces an unknown
	# option in some paths, which would record a channel nobody chose.
	values["request_source"] = hr_ops.validate_source(values.get("request_source")) \
		or "HR initiated"
	doc = frappe.get_doc(dict(values, doctype="Isoft Salary Change"))
	perms.require_company(doc.company or frappe.db.get_value("Employee", doc.employee, "company"))
	doc.insert()
	return doc.name


@frappe.whitelist()
def salary_change_action(name, action, reason=None):
	doc = frappe.get_doc("Isoft Salary Change", name)
	status = salary_change_service.perform(doc, action, reason=reason)
	doc.reload()
	return {"name": doc.name, "status": status, "created_profile": doc.created_profile,
	        "allowed_actions": salary_change_service.allowed_actions(doc)}


@frappe.whitelist()
def list_salary_changes(company=None, employee=None, status=None):
	perms.require(perms.SALARY_CHANGE_REQUEST)
	conditions, values = ["1=1"], []
	for field, value in (("company", company), ("employee", employee), ("status", status)):
		if value:
			conditions.append("s.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="s")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, s.company, s.change_type,
			s.effective_date, s.current_base, s.new_base, s.percentage_change, s.status,
			s.requested_by, s.approved_by, s.created_profile, s.request_source, s.owner
		from `tabIsoft Salary Change` s where {0}
		order by s.effective_date desc limit 300""".format(" and ".join(conditions)),
		values, as_dict=True)


# --------------------------------------------------------------------------- #
# Salary advances
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_advance(data):
	perms.require(perms.ADVANCE_REQUEST)
	values = _parse(data) or {}
	values["request_source"] = hr_ops.validate_source(values.get("request_source")) \
		or "Employee verbal request"
	doc = frappe.get_doc(dict(values, doctype="Isoft Salary Advance"))
	perms.require_company(doc.company or frappe.db.get_value("Employee", doc.employee, "company"))
	doc.insert()
	return doc.name


@frappe.whitelist()
def advance_action(name, action, reason=None):
	doc = frappe.get_doc("Isoft Salary Advance", name)
	status = advance_service.perform(doc, action, reason=reason)
	doc.reload()
	return {"name": doc.name, "status": status,
	        "outstanding": flt(doc.outstanding_amount),
	        "disbursement_entry": doc.disbursement_entry,
	        "allowed_actions": advance_service.allowed_actions(doc)}


@frappe.whitelist()
def list_advances(company=None, employee=None, open_only=0):
	perms.require(perms.ADVANCE_REQUEST)
	if cint(open_only):
		return advance_service.open_advances(company=company)
	conditions, values = ["1=1"], []
	for field, value in (("company", company), ("employee", employee)):
		if value:
			conditions.append("a.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="a")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.company, a.request_date,
			a.requested_amount, a.approved_amount, a.recovered_amount, a.outstanding_amount,
			a.installments, a.status, a.request_source, a.requested_by, a.owner
		from `tabIsoft Salary Advance` a where {0}
		order by a.request_date desc limit 300""".format(" and ".join(conditions)),
		values, as_dict=True)


# --------------------------------------------------------------------------- #
# Bank change requests
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def bank_change_action(name, action, reason=None):
	doc = frappe.get_doc("Isoft Bank Change Request", name)
	if action == "approve":
		return doc.approve()
	if action == "reject":
		return doc.reject(reason=reason)
	frappe.throw(_("Unknown action {0}.").format(action))


@frappe.whitelist()
def list_bank_change_requests(company=None, status=None):
	# Seeing the queue is not approving it. This required BANK_CHANGE_APPROVE, which is
	# HR Manager only — so the HR User who is meant to RECORD bank changes could not open
	# the screen at all, and it rendered blank rather than saying why. The new IBAN is
	# still withheld from the list for every caller; it is visible only on the request
	# itself, to the person approving it.
	perms.require(perms.BANK_CHANGE_REQUEST)
	conditions, values = ["1=1"], []
	for field, value in (("company", company), ("status", status)):
		if value:
			conditions.append("b.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="b")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	# The new IBAN is deliberately NOT returned in the list: it is visible on the request
	# itself, to the person approving it, and nowhere else.
	return frappe.db.sql(
		"""select b.name, b.employee, b.employee_name, b.company, b.status,
			b.current_iban_masked, b.bank_name, b.requested_by, b.requested_at
		from `tabIsoft Bank Change Request` b where {0}
		order by b.requested_at desc limit 200""".format(" and ".join(conditions)),
		values, as_dict=True)


# --------------------------------------------------------------------------- #
# HR readiness, dashboard and employee 360
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def hr_readiness(company=None):
	return lifecycle.hr_readiness(company=company)


@frappe.whitelist()
def hr_dashboard(company=None):
	return lifecycle.hr_dashboard(company=company)


@frappe.whitelist()
def hr_approval_inbox(company=None):
	return lifecycle.pending_approvals(company=company)


@frappe.whitelist()
def onboarding_checklist(employee):
	return lifecycle.onboarding_checklist(employee)


@frappe.whitelist()
def employee_timeline(employee, limit=200):
	return lifecycle.timeline(employee, limit=limit)


@frappe.whitelist()
def employee_360(employee):
	"""Everything HR needs about one person on a single screen."""
	perms.require(perms.EMPLOYEE_READ)
	company = frappe.db.get_value("Employee", employee, "company")
	perms.require_company(company)
	show_pay = perms.can(perms.SALARY_PROFILE_READ)
	contract = contract_service.active_contract(employee)

	out = {
		"employee": frappe.db.get_value(
			"Employee", employee,
			["name", "employee_name", "company", "department", "designation", "branch",
			 "date_of_joining", "status", "reports_to", "employment_type", "holiday_list",
			 "default_shift", "cell_number", "company_email"], as_dict=True),
		"contract": frappe.db.get_value(
			"Isoft Employment Contract", contract,
			["name", "contract_type", "start_date", "end_date", "is_open_ended", "status",
			 "probation_end", "probation_status", "notice_days"], as_dict=True)
		if contract else None,
		"onboarding": lifecycle.onboarding_checklist(employee),
		"timeline": lifecycle.timeline(employee, limit=30),
		"documents": frappe.db.sql(
			"""select name, document_type, document_number, issue_date, expiry_date, status,
				confidential
			from `tabIsoft Employee Document` where employee = %s
			order by expiry_date is null, expiry_date""", employee, as_dict=True),
		"dependants": frappe.db.sql(
			"""select name, dependant_name, relationship, date_of_birth
			from `tabIsoft Employee Dependant` where employee = %s""", employee, as_dict=True),
		"compensation_visible": show_pay,
	}
	# Confidential documents are filtered for anyone who is not an HR Manager.
	if not perms.can(perms.DOCUMENT_CONFIDENTIAL):
		out["documents"] = [d for d in out["documents"] if not cint(d.get("confidential"))]
	if show_pay:
		out["salary_profile"] = frappe.db.get_value(
			"Isoft Salary Profile", {"employee": employee},
			["name", "from_date", "to_date", "base", "food_allowance",
			 "transport_allowance"], as_dict=True, order_by="from_date desc")
		out["outstanding_advance"] = advance_service.outstanding_for(employee)
	return out


# --------------------------------------------------------------------------- #
# Employee Self-Service
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def my_dashboard():
	return ess.dashboard()


@frappe.whitelist()
def my_profile():
	return ess.my_profile()


@frappe.whitelist()
def update_my_profile(values):
	return ess.update_my_profile(values)


@frappe.whitelist()
def my_payslips(limit=24):
	return ess.my_payslips(limit=limit)


@frappe.whitelist()
def my_payslip(name):
	return ess.my_payslip(name)


@frappe.whitelist()
def my_leave():
	return ess.my_leave()


@frappe.whitelist()
def my_leave_balance():
	return ess.my_leave_balance()


@frappe.whitelist()
def my_attendance(from_date=None, to_date=None):
	return ess.my_attendance(from_date=from_date, to_date=to_date)


@frappe.whitelist()
def my_requests():
	return ess.my_requests()


@frappe.whitelist()
def my_documents():
	return ess.my_documents()


@frappe.whitelist()
def request_bank_change(new_iban, bank_name=None, proof_document=None):
	return ess.request_bank_change(new_iban, bank_name=bank_name,
	                               proof_document=proof_document)


@frappe.whitelist()
def my_advances():
	return ess.my_advances()


@frappe.whitelist()
def my_document(name):
	return ess.my_document(name)


@frappe.whitelist()
def my_attendance_summary(from_date=None, to_date=None):
	return ess.attendance_summary(from_date=from_date, to_date=to_date)


@frappe.whitelist()
def leave_preview(leave_type, from_date, to_date, half_day=0, half_day_date=None):
	return ess.leave_preview(leave_type, from_date, to_date, half_day=cint(half_day),
	                         half_day_date=half_day_date)


@frappe.whitelist()
def apply_leave(leave_type, from_date, to_date, description=None, half_day=0,
                half_day_date=None, attachment=None):
	return ess.apply_leave(leave_type, from_date, to_date, description=description,
	                       half_day=cint(half_day), half_day_date=half_day_date,
	                       attachment=attachment)


@frappe.whitelist()
def cancel_leave(name):
	return ess.cancel_leave(name)


@frappe.whitelist()
def request_advance(requested_amount, reason, installments=None, recovery_start_date=None):
	return ess.request_advance(requested_amount, reason, installments=installments,
	                           recovery_start_date=recovery_start_date)


# --------------------------------------------------------------------------- #
# Manager Self-Service
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def my_team(include_indirect=0):
	return mss.my_team(include_indirect=cint(include_indirect))


@frappe.whitelist()
def team_dashboard():
	return mss.dashboard()


@frappe.whitelist()
def team_member(employee):
	return mss.team_member(employee)


@frappe.whitelist()
def team_leave_requests(status="Open"):
	return mss.team_leave_requests(status=status)


@frappe.whitelist()
def team_attendance_exceptions(from_date=None, to_date=None):
	return mss.team_attendance_exceptions(from_date=from_date, to_date=to_date)


@frappe.whitelist()
def team_availability(on_date=None):
	return mss.team_availability(on_date=on_date)


@frappe.whitelist()
def team_approval_inbox():
	return mss.approval_inbox()


@frappe.whitelist()
def team_member_leave(employee):
	return mss.team_member_leave(employee)


@frappe.whitelist()
def team_probations():
	return mss.team_probations()


@frappe.whitelist()
def team_contract_expiry(within_days=90):
	return mss.team_contract_expiry(within_days=cint(within_days) or 90)


@frappe.whitelist()
def leave_decision(name, action, reason=None):
	return mss.leave_decision(name, action, reason=reason)


@frappe.whitelist()
def attendance_justification_decision(name, action, reason=None):
	return mss.attendance_justification_decision(name, action, reason=reason)


@frappe.whitelist()
def probation_recommendation(name, recommendation, notes=None):
	return mss.probation_recommendation(name, recommendation, notes=notes)


@frappe.whitelist()
def renewal_recommendation(name, recommendation, notes=None):
	return mss.renewal_recommendation(name, recommendation, notes=notes)


# --------------------------------------------------------------------------- #
# Which self-service areas the caller can actually use.
# The portal asks this once on load so it can render the right navigation instead of
# showing links that will fail — and so a plain employee never sees a manager tab.
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def self_service_context():
	employee = ess.current_employee(raise_exception=False)
	out = {
		"user": frappe.session.user,
		"employee": employee,
		"employee_name": frappe.db.get_value("Employee", employee, "employee_name")
		if employee else None,
		"is_manager": False,
		"team_size": 0,
		"is_hr": perms.can(perms.HR_ACCESS),
		"can_see_compensation": mss.can_see_compensation(),
		"language": frappe.db.get_value("User", frappe.session.user, "language") or "pt",
	}
	if employee:
		members = mss.team(employee)
		out["is_manager"] = bool(members)
		out["team_size"] = len(members)
	return out


# --------------------------------------------------------------------------- #
# Contract documents (§30–34)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def contract_template_variables(include_salary=0):
	return contract_documents.available_variables(include_salary=cint(include_salary))


@frappe.whitelist()
def generate_contract_document(contract, template=None):
	return contract_documents.generate(contract, template=template)


@frappe.whitelist()
def finalise_contract_document(name):
	return contract_documents.finalise(name)


@frappe.whitelist()
def attach_signed_contract(name, file_url, signed_on=None):
	return contract_documents.attach_signed(name, file_url, signed_on=signed_on)


@frappe.whitelist()
def contract_documents_for(contract):
	perms.require(perms.CONTRACT_READ)
	return contract_documents.documents_for(contract)


@frappe.whitelist()
def preview_contract_template(template, contract):
	"""Render a template against a real contract WITHOUT creating a document.

	Lets HR see the wording before committing it, which is the difference between
	catching a wrong placeholder now and finding it on a signed contract.
	"""
	perms.require(perms.CONTRACT_WRITE)
	tpl = frappe.get_doc("Isoft Contract Template", template)
	include_salary = bool(cint(tpl.include_salary) and perms.can(perms.SALARY_PROFILE_READ))
	body, unresolved = contract_documents.render(
		tpl.body, contract_documents.build_context(contract, include_salary=include_salary))
	return {"body": body, "unresolved": unresolved, "version": cint(tpl.version),
	        "salary_included": include_salary}


# --------------------------------------------------------------------------- #
# Bulk onboarding and offboarding (§35–42)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def bulk_candidates(company=None, department=None, without_contract=1):
	return bulk_onboarding.candidates(company=company, department=department,
	                                  without_contract=cint(without_contract))


@frappe.whitelist()
def bulk_contract_preview(employees, contract_type, start_date=None, end_date=None,
                          is_open_ended=0, probation_months=None, template=None,
                          use_joining_date=0):
	return bulk_onboarding.preview(
		employees, contract_type, start_date=start_date, end_date=end_date,
		is_open_ended=cint(is_open_ended), probation_months=probation_months,
		template=template, use_joining_date=cint(use_joining_date))


@frappe.whitelist()
def bulk_contract_execute(employees, contract_type, start_date=None, end_date=None,
                          is_open_ended=0, probation_months=None, template=None,
                          use_joining_date=0, generate_documents=0):
	return bulk_onboarding.execute(
		employees, contract_type, start_date=start_date, end_date=end_date,
		is_open_ended=cint(is_open_ended), probation_months=probation_months,
		template=template, use_joining_date=cint(use_joining_date),
		generate_documents=cint(generate_documents))


@frappe.whitelist()
def new_hire_readiness(employee):
	return bulk_onboarding.readiness_for_work_and_payroll(employee)


@frappe.whitelist()
def exit_checklist(employee):
	return bulk_onboarding.exit_checklist(employee)


# --------------------------------------------------------------------------- #
# Recruitment, performance and training (§43–50)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def recruitment_pipeline(company=None):
	return recruitment.pipeline(company=company)


@frappe.whitelist()
def recruitment_conversion_check(job_offer):
	return recruitment.conversion_check(job_offer)


@frappe.whitelist()
def recruitment_convert(job_offer, date_of_joining=None, department=None, reports_to=None,
                        employment_type=None, create_onboarding=1):
	return recruitment.convert_to_employee(
		job_offer, date_of_joining=date_of_joining, department=department,
		reports_to=reports_to, employment_type=employment_type,
		create_onboarding=cint(create_onboarding))


@frappe.whitelist()
def performance_summary(company=None):
	return recruitment.performance_summary(company=company)


@frappe.whitelist()
def my_team_appraisals():
	return recruitment.team_appraisals()


@frappe.whitelist()
def employee_training(employee=None, company=None):
	return recruitment.training(employee=employee, company=company)


# --------------------------------------------------------------------------- #
# Org chart and analytics (§52–57)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def org_chart(company=None, department=None):
	return org_analytics.org_chart(company=company, department=department)


@frappe.whitelist()
def org_chart_quality():
	perms.require(perms.EMPLOYEE_READ)
	return org_analytics.chart_quality()


@frappe.whitelist()
def headcount_trend(company=None, months=12, department=None):
	return org_analytics.headcount_trend(company=company, months=cint(months) or 12,
	                                     department=department)


@frappe.whitelist()
def turnover(company=None, from_date=None, to_date=None, department=None):
	return org_analytics.turnover(company=company, from_date=from_date, to_date=to_date,
	                              department=department)


@frappe.whitelist()
def absenteeism(company=None, from_date=None, to_date=None, department=None):
	return org_analytics.absenteeism(company=company, from_date=from_date, to_date=to_date,
	                                 department=department)


@frappe.whitelist()
def analytics_dashboard(company=None, months=12):
	return org_analytics.analytics_dashboard(company=company, months=cint(months) or 12)


# --------------------------------------------------------------------------- #
# Statutory filing (§58–62)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def statutory_validate(submission_type, company, period_start=None, period_end=None):
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	return statutory_filing.validate_period(submission_type, company, period_start, period_end)


@frappe.whitelist()
def statutory_generate(submission_type, company, period_start=None, period_end=None):
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	result = statutory_filing.build(submission_type, company, period_start, period_end)
	# The rows are large and the caller only needs the register entry and the totals.
	result.pop("rows", None)
	return result


@frappe.whitelist()
def statutory_working_file(submission_type, company, period_start=None, period_end=None):
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	out = statutory_filing.working_file(submission_type, company, period_start, period_end)
	frappe.response["filename"] = out["filename"]
	frappe.response["filecontent"] = out["content"]
	frappe.response["type"] = "binary"


@frappe.whitelist()
def statutory_record_submission(name, reference, submitted_on=None, status="Submitted"):
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	return statutory_filing.record_submission(name, reference, submitted_on=submitted_on,
	                                          status=status)


@frappe.whitelist()
def statutory_history(company=None, submission_type=None, limit=100):
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	return statutory_filing.history(company=company, submission_type=submission_type,
	                                limit=cint(limit) or 100)


@frappe.whitelist()
def bank_format_status():
	"""What bank formats exist, and why the others deliberately do not (§63, §64)."""
	from isoft_angola_hr.isoft_angola_hr.services import statutory_filing

	perms.require(perms.REPORT_BANK)
	return statutory_filing.BANK_FORMAT_STATUS


# --------------------------------------------------------------------------- #
# Notification centre (§74) and legal reference (§96)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def notification_centre():
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

	return hr_notifications.notification_centre()


@frappe.whitelist()
def mark_notifications_read(names=None):
	from isoft_angola_hr.isoft_angola_hr.services import hr_notifications

	return hr_notifications.mark_read(names)


@frappe.whitelist()
def labour_law_reference():
	"""The statutory limits this app checks against, with their articles and sources."""
	from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law

	return angola_labour_law.reference()


# --------------------------------------------------------------------------- #
# Phase 5 — performance management (§25–36)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def performance_cycle_preview(cycle):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.preview_cycle(cycle)


@frappe.whitelist()
def performance_cycle_generate(cycle):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.generate_cycle(cycle)


@frappe.whitelist()
def performance_cycle_close(cycle):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.close_cycle(cycle)


@frappe.whitelist()
def performance_cycle_progress(cycle=None, company=None):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.cycle_progress(cycle=cycle, company=company)


@frappe.whitelist()
def my_team_reviews(state=None):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.team_reviews(state=state)


@frappe.whitelist()
def review_detail(name):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.review_detail(name)


@frappe.whitelist()
def submit_review(name, goals=None, comments=None, submit=1):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.manager_review(name, goals=goals, comments=comments,
	                                  submit=cint(submit))


@frappe.whitelist()
def acknowledge_review(name, comments=None):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.employee_acknowledge(name, comments=comments)


@frappe.whitelist()
def finalise_review(name):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.hr_finalise(name)


@frappe.whitelist()
def my_reviews():
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.my_reviews()


@frappe.whitelist()
def my_review(name):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.my_review(name)


@frappe.whitelist()
def review_recommend_salary_change(name, new_base, effective_date, reason=None):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.recommend_salary_change(name, new_base, effective_date, reason=reason)


@frappe.whitelist()
def review_recommend_promotion(name, new_designation, effective_date, reason=None):
	from isoft_angola_hr.isoft_angola_hr.services import performance

	return performance.recommend_promotion(name, new_designation, effective_date,
	                                       reason=reason)


# --------------------------------------------------------------------------- #
# Phase 5 — delegation and team calendar (§44–47)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def team_calendar(from_date=None, to_date=None):
	return mss.team_calendar(from_date=from_date, to_date=to_date)


@frappe.whitelist()
def my_delegations():
	from isoft_angola_hr.isoft_angola_hr.services import delegation

	return delegation.my_delegations()


@frappe.whitelist()
def create_delegation(delegator, delegate, from_date, to_date, reason=None):
	from isoft_angola_hr.isoft_angola_hr.services import delegation

	return delegation.create(delegator, delegate, from_date, to_date, reason=reason)


@frappe.whitelist()
def revoke_delegation(name):
	from isoft_angola_hr.isoft_angola_hr.services import delegation

	return delegation.revoke(name)


# --------------------------------------------------------------------------- #
# Phase 5 — ESS uploads (§37–43)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def my_occurrence(name):
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.my_occurrence(name)


@frappe.whitelist()
def justification_reasons():
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.justification_reasons()


@frappe.whitelist()
def submit_justification(name, reason=None, explanation=None, filename=None, content=None):
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.submit_justification(name, reason=reason, explanation=explanation,
	                                        filename=filename, content=content)


@frappe.whitelist()
def uploadable_document_types():
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.uploadable_document_types()


@frappe.whitelist()
def upload_my_document(document_type, filename, content, document_number=None,
                       issue_date=None, expiry_date=None, notes=None):
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.upload_document(document_type, filename, content,
	                                   document_number=document_number,
	                                   issue_date=issue_date, expiry_date=expiry_date,
	                                   notes=notes)


@frappe.whitelist()
def verify_employee_document(name, decision, reason=None):
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.verify_document(name, decision, reason=reason)


@frappe.whitelist()
def documents_pending_verification(company=None):
	from isoft_angola_hr.isoft_angola_hr.services import ess_uploads

	return ess_uploads.pending_verification(company=company)


@frappe.whitelist()
def interview_pipeline(company=None, upcoming_only=0):
	return recruitment.interview_pipeline(company=company,
	                                      upcoming_only=cint(upcoming_only))


@frappe.whitelist()
def schedule_interview(job_applicant, interview_round, scheduled_on, from_time=None,
                       to_time=None, interviewers=None, job_opening=None):
	return recruitment.schedule_interview(
		job_applicant, interview_round, scheduled_on, from_time=from_time,
		to_time=to_time, interviewers=interviewers, job_opening=job_opening)


@frappe.whitelist()
def careers_page_status():
	perms.require(perms.EMPLOYEE_READ)
	return recruitment.CAREERS_PAGE


# --------------------------------------------------------------------------- #
# UX completion — the create endpoints the HR screens' new buttons call.
#
# Every one of these is a thin wrapper over a DocType the user could already have
# created from the Frappe Desk. Their reason for existing is discoverability: an HR
# administrator should never need to know a /app/... URL to start a standard workflow.
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def next_payroll_boundary(after=None):
	"""The next date a salary change may take effect.

	Payroll runs on a configured cycle (23rd → 22nd on this site), and a salary change
	must land on a period start because the engine cannot split one period between two
	salary profiles. The date is CALCULATED from the configured cycle, never hard-coded.
	"""
	from frappe.utils import add_months, getdate, nowdate

	from isoft_angola_hr.isoft_angola_hr import api

	base = getdate(after) if after else getdate(nowdate())
	start, end = api._cycle_period(base)
	if start <= base:
		start, end = api._cycle_period(add_months(base, 1))
	return {"next_start": str(start), "period_end": str(end),
	        "hint": _("A salary change takes effect at the start of a payroll period. "
	                  "The next one begins on {0}.").format(start)}


@frappe.whitelist()
def create_performance_cycle(data):
	perms.require(perms.EMPLOYEE_WRITE)
	values = _parse(data) or {}
	doc = frappe.get_doc(dict(values, doctype="Isoft Performance Cycle"))
	perms.require_company(doc.company)
	doc.insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def list_performance_cycles(company=None):
	perms.require(perms.EMPLOYEE_READ)
	conditions, values = ["1=1"], []
	if company:
		conditions.append("c.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="c")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select c.name, c.cycle_name, c.company, c.period_type, c.start_date, c.end_date,
			c.due_date, c.status, c.appraisal_template, c.appraisals_created, c.department
		from `tabIsoft Performance Cycle` c where {0}
		order by c.start_date desc limit 100""".format(" and ".join(conditions)),
		values, as_dict=True)


@frappe.whitelist()
def create_appraisal_template(kra_title, goals):
	"""An Appraisal Template is ERPNext's; this only saves HR a trip to the Desk.

	The weightings must total 100% — ERPNext enforces it, and so does the cycle
	generator, so it is checked here too and reported before the record is created.
	"""
	perms.require(perms.EMPLOYEE_WRITE)
	rows = _parse(goals) or []
	if not rows:
		frappe.throw(_("Add at least one objective (KRA)."))
	total = sum(flt(r.get("per_weightage")) for r in rows)
	if int(total) != 100:
		frappe.throw(_("The weightings total {0}%, not 100%.").format(total))
	doc = frappe.get_doc({
		"doctype": "Appraisal Template", "kra_title": kra_title,
		"goals": [{"kra": r.get("kra"), "per_weightage": flt(r.get("per_weightage"))}
		          for r in rows],
	}).insert()
	return {"name": doc.name}


@frappe.whitelist()
def list_appraisal_templates():
	perms.require(perms.EMPLOYEE_READ)
	if not frappe.db.table_exists("Appraisal Template"):
		return []
	return frappe.db.sql(
		"""select t.name, t.kra_title,
			(select count(*) from `tabAppraisal Template Goal` g where g.parent = t.name)
				as goals
		from `tabAppraisal Template` t order by t.kra_title""", as_dict=True)


@frappe.whitelist()
def create_job_opening(data):
	perms.require(perms.EMPLOYEE_WRITE)
	values = _parse(data) or {}
	doc = frappe.get_doc(dict(values, doctype="Job Opening"))
	perms.require_company(doc.company)
	doc.insert()
	return {"name": doc.name, "route": doc.route, "status": doc.status}


@frappe.whitelist()
def create_job_applicant(data):
	perms.require(perms.EMPLOYEE_WRITE)
	values = _parse(data) or {}
	doc = frappe.get_doc(dict(values, doctype="Job Applicant")).insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def create_job_offer(data):
	perms.require(perms.EMPLOYEE_WRITE)
	values = _parse(data) or {}
	doc = frappe.get_doc(dict(values, doctype="Job Offer"))
	perms.require_company(doc.company)
	doc.insert()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def recruitment_reference_data():
	"""Everything the recruitment forms need to populate their pickers in one call."""
	perms.require(perms.EMPLOYEE_READ)
	return {
		"openings": frappe.get_all("Job Opening", filters={"status": "Open"},
		                           fields=["name", "job_title"], limit=100)
		if frappe.db.table_exists("Job Opening") else [],
		"applicants": frappe.get_all(
			"Job Applicant", filters={"status": ("not in", ("Rejected",))},
			fields=["name", "applicant_name", "status"], limit=200)
		if frappe.db.table_exists("Job Applicant") else [],
		"rounds": frappe.get_all("Interview Round", fields=["name", "round_name"], limit=50)
		if frappe.db.table_exists("Interview Round") else [],
		"designations": frappe.get_all("Designation", pluck="name", limit=200),
		"departments": frappe.get_all("Department", pluck="name", limit=200),
	}


# --------------------------------------------------------------------------- #
# HR-OPERATED MODE
#
# The endpoints below are the HR front doors for work that previously existed only
# behind /ess or /mss. Employees and line managers are not required to hold a login;
# HR records the request and an authorised HR person decides it.
#
# Each is a thin wrapper, like everything else in this file. None of them approves
# anything: recording a request and granting it stay separate permissions.
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_bank_change(employee, new_iban, bank_name=None, proof_document=None,
                       request_source=None):
	"""HR records an employee's request to be paid into a different account.

	Approval, which is the only step that writes the Employee record, still requires
	BANK_CHANGE_APPROVE and is a different endpoint.
	"""
	return hr_ops.create_bank_change(employee, new_iban, bank_name=bank_name,
	                                 proof_document=proof_document,
	                                 request_source=request_source)


@frappe.whitelist()
def add_employee_document(employee, document_type, filename=None, content=None,
                          document_number=None, issue_date=None, expiry_date=None,
                          issuing_authority=None, notes=None, confidential=None):
	"""HR files a document it was handed — a BI, a certificate, a sick note."""
	return hr_ops.add_employee_document(
		employee, document_type, filename=filename, content=content,
		document_number=document_number, issue_date=issue_date, expiry_date=expiry_date,
		issuing_authority=issuing_authority, notes=notes, confidential=confidential)


@frappe.whitelist()
def record_justification(occurrence, reason, explanation=None, filename=None, content=None,
                        justification_source=None, decision=None):
	"""HR records the explanation an employee gave for an attendance occurrence."""
	return hr_ops.record_justification(
		occurrence, reason, explanation=explanation, filename=filename, content=content,
		justification_source=justification_source, decision=decision)


@frappe.whitelist()
def record_evaluation(appraisal, goals=None, comments=None, decision_by=None,
                      evaluation_source=None, submit=1):
	"""HR records a performance evaluation on the line manager's behalf."""
	return hr_ops.record_evaluation(appraisal, goals=goals, comments=comments,
	                                decision_by=decision_by,
	                                evaluation_source=evaluation_source,
	                                submit=cint(submit))


@frappe.whitelist()
def record_acknowledgement(appraisal, comments=None, acknowledged_by=None):
	"""HR records that the employee has seen and signed their review."""
	return hr_ops.record_acknowledgement(appraisal, comments=comments,
	                                     acknowledged_by=acknowledged_by)


@frappe.whitelist()
def record_interview_result(interview, result, feedback=None, decision_by=None):
	"""HR records the outcome of an interview the panel conducted offline."""
	return hr_ops.record_interview_result(interview, result, feedback=feedback,
	                                      decision_by=decision_by)


@frappe.whitelist()
def hr_action_queue(company=None):
	"""What needs HR action today, and which screen clears each queue."""
	return hr_ops.action_queue(company=company)


@frappe.whitelist()
def self_approval_policy():
	"""Whether one HR person may both record and decide each process, and why."""
	perms.require(perms.HR_READINESS)
	return hr_ops.self_approval_policy()


@frappe.whitelist()
def login_dependencies(company=None):
	"""Evidence that no HR process requires an employee or manager login."""
	return hr_ops.login_dependencies(company=company)


@frappe.whitelist()
def request_sources():
	"""The channels through which a request can reach HR."""
	perms.require(perms.HR_ACCESS)
	return {"request": list(hr_ops.REQUEST_SOURCES),
	        "evaluation": list(hr_ops.EVALUATION_SOURCES)}


@frappe.whitelist()
def list_employee_documents(company=None, employee=None, status=None,
                            verification_status=None):
	"""Employee documents HR holds, filtered for the console.

	Confidential documents are excluded for a caller without DOCUMENT_CONFIDENTIAL —
	filtered in SQL rather than after the fetch, so the rows never reach the browser.
	"""
	perms.require(perms.DOCUMENT_READ)
	conditions, values = ["1=1"], []
	for field, value in (("d.employee", employee), ("d.status", status),
	                     ("d.verification_status", verification_status)):
		if value:
			conditions.append("{0} = %s".format(field))
			values.append(value)
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	scope, scope_values = perms.company_filter_sql(alias="e")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	if not perms.can(perms.DOCUMENT_CONFIDENTIAL):
		conditions.append("ifnull(d.confidential, 0) = 0")
	return frappe.db.sql(
		"""select d.name, d.employee, d.employee_name, d.document_type, d.document_number,
			d.issue_date, d.expiry_date, d.days_to_expiry, d.status, d.verification_status,
			d.confidential, d.submitted_by_employee, d.attachment, d.verified_by
		from `tabIsoft Employee Document` d
		join `tabEmployee` e on e.name = d.employee
		where {0} order by d.modified desc limit 300""".format(" and ".join(conditions)),
		values, as_dict=True)


@frappe.whitelist()
def document_type_options():
	"""Document types HR may file, with their confidentiality already resolved."""
	perms.require(perms.DOCUMENT_READ)
	rows = frappe.get_all(
		"Isoft Document Type", filters={"disabled": 0},
		fields=["name", "document_type_pt", "requires_expiry", "is_mandatory",
		        "is_confidential", "is_medical"], order_by="name")
	may_file_confidential = perms.can(perms.DOCUMENT_CONFIDENTIAL)
	for row in rows:
		row["confidential"] = cint(row.is_confidential) or cint(row.is_medical)
		row["allowed"] = bool(may_file_confidential or not row["confidential"])
	return rows


@frappe.whitelist()
def open_appraisals(company=None, cycle=None, state=None):
	"""Reviews HR is running, whatever stage they are at.

	The MSS equivalent lists only the caller's own team. This one is scoped by company
	and role, because in HR-operated mode the reviews HR has to progress are precisely
	the ones nobody's manager is going to touch.
	"""
	perms.require(perms.PERFORMANCE_OPERATE)
	conditions, values = ["a.docstatus < 2"], []
	for field, value in (("a.company", company), ("a.custom_performance_cycle", cycle),
	                     ("a.custom_review_state", state)):
		if value:
			conditions.append("{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="a")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select a.name, a.employee, a.employee_name, a.department, a.company,
			a.start_date, a.end_date, a.total_score, a.status,
			a.custom_review_state, a.custom_manager, a.custom_due_date,
			a.custom_performance_cycle, a.custom_evaluation_source, a.custom_decision_by,
			e.reports_to
		from `tabAppraisal` a
		left join `tabEmployee` e on e.name = a.employee
		where {0} order by a.custom_due_date, a.employee_name limit 500""".format(
			" and ".join(conditions)), values, as_dict=True)


@frappe.whitelist()
def appraisal_goals(appraisal):
	"""The objectives to score, and what has been scored so far."""
	perms.require(perms.PERFORMANCE_OPERATE)
	row = frappe.db.get_value("Appraisal", appraisal,
	                          ["name", "employee", "employee_name", "company",
	                           "custom_review_state", "custom_manager",
	                           "custom_manager_comments", "total_score"], as_dict=True)
	if not row:
		frappe.throw(_("Appraisal {0} not found.").format(appraisal),
		             frappe.DoesNotExistError)
	perms.require_company(row.company)
	row["goals"] = frappe.get_all(
		"Appraisal Goal", filters={"parent": appraisal},
		fields=["name", "kra", "per_weightage", "score", "score_earned"], order_by="idx")
	row["manager_name"] = frappe.db.get_value("Employee", row.custom_manager,
	                                          "employee_name") if row.custom_manager else None
	return row
