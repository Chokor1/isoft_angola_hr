# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll pre-flight: can this payroll actually be run, and what will bite?

Every problem the payroll engine can refuse to calculate is detected HERE, before a
single Salary Slip exists, and returned with a severity so the dashboard can show
"3 blocked, 68 without IBAN" instead of failing employee by employee halfway through a
run. The classification is deliberately three-way:

    BLOCKING   payroll cannot be calculated or submitted at all
    PAYMENT    payroll is fine; the bank file is not (missing IBAN)
    WARNING    payroll works but somebody should look (missing NIF, big variance)

Treating a missing NIF as fatal would stop payday for a compliance form; treating a
missing IBAN as harmless would produce a bank file that silently drops an employee.
Both mistakes existed before this module.

This is server-authoritative. The dashboard renders what ``evaluate`` returns; it does
not decide readiness itself, and neither does the JavaScript.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

BLOCKING = "BLOCKING"
PAYMENT = "PAYMENT"
WARNING = "WARNING"

# --------------------------------------------------------------------------- #
# Exception catalogue
# --------------------------------------------------------------------------- #
EXCEPTIONS = {
	"EXC-001": (BLOCKING, _("No Salary Profile")),
	"EXC-002": (BLOCKING, _("Ambiguous Salary Profile")),
	"EXC-003": (BLOCKING, _("Invalid working / payment days")),
	"EXC-004": (BLOCKING, _("Negative net salary")),
	"EXC-005": (BLOCKING, _("Missing statutory configuration")),
	"EXC-006": (BLOCKING, _("Missing payroll account")),
	"EXC-007": (PAYMENT, _("Missing IBAN")),
	"EXC-008": (WARNING, _("Missing NIF")),
	"EXC-009": (WARNING, _("Missing Social Security number")),
	"EXC-010": (WARNING, _("Pending attendance occurrence")),
	"EXC-011": (BLOCKING, _("Salary changes inside the payroll period")),
	"EXC-012": (BLOCKING, _("Already processed in this period")),
	# Variance / anomaly detection — warnings by design, never blockers.
	"EXC-020": (WARNING, _("Gross salary variance")),
	"EXC-021": (WARNING, _("Net salary variance")),
	"EXC-022": (WARNING, _("IRT dropped to zero")),
	"EXC-023": (WARNING, _("Social Security dropped to zero")),
	"EXC-024": (WARNING, _("First payroll for this employee")),
	"EXC-025": (WARNING, _("Employee missing from this payroll")),
}

DEFAULT_VARIANCE_THRESHOLD = 25.0


def _exception(code, employee=None, employee_name=None, message=None):
	severity, label = EXCEPTIONS[code]
	return {
		"code": code,
		"severity": severity,
		"label": label,
		"employee": employee,
		"employee_name": employee_name,
		"message": message or label,
	}


# --------------------------------------------------------------------------- #
# Configuration readiness
# --------------------------------------------------------------------------- #
def _account_exists(name):
	return bool(name) and bool(frappe.db.exists("Account", name))


def configuration_status(company, on_date=None):
	"""Every configuration item payroll actually reads, and whether it is usable.

	"Configured" means the value is set AND the record it points at exists — a Link to a
	deleted account used to read as configured right up to the moment posting failed.
	"""
	on_date = getdate(on_date) if on_date else getdate()
	s = frappe.get_cached_doc("Isoft HR Settings")
	items = []

	def add(key, label, value, ok, hint=None, severity=BLOCKING):
		items.append({
			"key": key, "label": label, "value": value,
			"status": "Configured" if ok else ("Missing" if not value else "Invalid"),
			"ok": bool(ok), "severity": severity, "hint": hint,
		})

	add("payroll_payable_account", _("Payroll Payable Account"), s.get("payroll_payable_account"),
	    _account_exists(s.get("payroll_payable_account")),
	    _("Required to post the payroll accrual."))
	add("salary_payment_account", _("Salary Payment Account (Bank/Cash)"),
	    s.get("salary_payment_account"), _account_exists(s.get("salary_payment_account")),
	    _("Required to post the salary payment."))

	# One row per component that needs a GL account, so a half-mapped chart is visible
	# before posting fails on the first employee.
	mapped = {r.abbr: r.account for r in s.get("component_accounts") or [] if r.account}
	for comp in engine.journal_components():
		account = mapped.get(comp["abbr"])
		add("account:" + comp["abbr"], _("Account — {0}").format(comp["component"]),
		    account, _account_exists(account), _("Settings → Account per Component."))

	cost_center = frappe.db.get_value("Company", company, "cost_center") if company else None
	add("cost_center", _("Default Cost Center"), cost_center, bool(cost_center),
	    _("Set on the Company."), severity=WARNING)

	irt_table = s.get("default_irt_table")
	irt_ok = bool(irt_table) and bool(frappe.db.exists("IRT Table", irt_table))
	add("default_irt_table", _("Current IRT Table"), irt_table, irt_ok,
	    _("Settings → Default IRT Table."))
	if irt_ok:
		effective = frappe.db.get_value("IRT Table", irt_table, "effective_from")
		covers = not effective or getdate(effective) <= on_date
		add("irt_effective_from", _("IRT Table Effective From"), str(effective or ""), covers,
		    _("The IRT table takes effect after this payroll period, so it does not "
		      "legally cover it."))

	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_statutory_rate.isoft_statutory_rate import (
		get_statutory_rates,
	)
	rates = get_statutory_rates(company, on_date, settings=s)
	source = rates.get("statutory_rate") or _("Isoft HR Settings (fallback)")
	rates_ok = rates.get("ss_employee_rate") is not None and rates.get("ss_employer_rate") is not None
	add("statutory_rates", _("Current Statutory Rates"),
	    "{0} — {1}% / {2}%".format(source, flt(rates.get("ss_employee_rate")),
	                               flt(rates.get("ss_employer_rate"))),
	    rates_ok, _("Create an Isoft Statutory Rate effective for this period."))

	return items


# --------------------------------------------------------------------------- #
# Employee readiness
# --------------------------------------------------------------------------- #
def _eligible_employees(company, start, end, department=None, branch=None, designation=None):
	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	for field, value in (("department", department), ("branch", branch),
	                     ("designation", designation)):
		if value:
			filters[field] = value
	return frappe.get_all(
		"Employee", filters=filters,
		fields=["name", "employee_name", "department", "designation", "date_of_joining",
		        "relieving_date", "custom_nif", "custom_inss_number", "custom_iban",
		        "salary_mode"],
		order_by="employee_name", limit_page_length=5000,
	)


def _pending_occurrences(company, start, end):
	"""Attendance occurrences still awaiting a decision, counted per employee."""
	rows = frappe.db.sql(
		"""select employee, count(*) as n from `tabIsoft Attendance Occurrence`
		where status in ('Pending', 'Unjustified') and occurrence_date between %s and %s
		group by employee""", (getdate(start), getdate(end)), as_dict=True)
	return {r.employee: cint(r.n) for r in rows}


def _previous_period_slips(company, start, end):
	"""The most recent payroll BEFORE this period, keyed by employee, for variance checks.

	It is found by looking for the latest payroll that actually exists rather than by
	assuming the immediately preceding calendar window holds one. A company that skips a
	cycle, or runs a 23rd-to-22nd period, otherwise compares against an empty window and
	every single employee is reported as "first payroll" — 80 warnings that mean nothing
	and train people to ignore the panel.
	"""
	prev_end = frappe.db.sql(
		"""select max(end_date) from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date < %s""",
		(company, getdate(start)))
	prev_end = prev_end[0][0] if prev_end and prev_end[0][0] else None
	if not prev_end:
		return {}, (None, None)
	prev_start = frappe.db.sql(
		"""select min(start_date) from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date = %s""", (company, prev_end))[0][0]
	rows = frappe.db.sql(
		"""select employee, gross_pay, net_pay, irt_amount, ss_employee_amount
		from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date = %s""",
		(company, prev_end), as_dict=True)
	return {r.employee: r for r in rows}, (getdate(prev_start), getdate(prev_end))


def _existing_slips(company, start, end):
	"""Every live slip overlapping this period, keyed by employee — one query for the
	whole company instead of one per employee."""
	rows = frappe.db.sql(
		"""select employee, name, docstatus from `tabIsoft Salary Slip`
		where company=%s and docstatus<2 and start_date<=%s and end_date>=%s""",
		(company, getdate(end), getdate(start)), as_dict=True)
	out = {}
	for r in rows:
		out.setdefault(r.employee, r)
	return out


def _ever_paid(company):
	"""Employees who have ever had a salary slip. Used to tell a genuine first payroll
	from somebody merely absent from the previous run."""
	return set(frappe.db.sql_list(
		"""select distinct employee from `tabIsoft Salary Slip`
		where company=%s and docstatus < 2""", company))


def evaluate(company, start_date, end_date, department=None, branch=None, designation=None,
             validate_attendance=0, based_on_timesheet=0, include_variance=True):
	"""Full readiness assessment for a payroll period.

	Returns counts, the configuration checklist and one entry per detected exception.
	No salary amounts are exposed: the summary answers "who is blocked and why", which
	is what HR needs before running payroll, without turning a readiness panel into a
	salary list.
	"""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
		assert_single_profile_for_period,
	)
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
		compute_working_days,
	)

	perms.require(perms.PAYROLL_PREVIEW)
	perms.require_company(company)

	start, end = getdate(start_date), getdate(end_date)
	settings = engine.get_settings()
	threshold = flt(settings.get("variance_threshold_percent")) or DEFAULT_VARIANCE_THRESHOLD

	config = configuration_status(company, on_date=end)
	config_blocking = [c for c in config if not c["ok"] and c["severity"] == BLOCKING]

	employees = _eligible_employees(company, start, end, department, branch, designation)
	occurrences = _pending_occurrences(company, start, end)
	previous, prev_period = ({}, (None, None))
	ever_paid = set()
	if include_variance:
		previous, prev_period = _previous_period_slips(company, start, end)
		ever_paid = _ever_paid(company)
	existing_slips = _existing_slips(company, start, end)

	exceptions = []
	ready, blocked_employees, payment_blocked, warned = 0, set(), set(), set()
	seen = set()

	for e in employees:
		seen.add(e.name)
		blockers = []

		try:
			profile = assert_single_profile_for_period(
				e.name, start, end, company=company, employee_name=e.employee_name)
		except frappe.ValidationError as exc:
			message = frappe.utils.strip_html(str(exc))
			code = "EXC-011" if "mid-period" in message.lower() or "changes during" in message.lower() \
				else "EXC-002"
			blockers.append(_exception(code, e.name, e.employee_name, message))
			profile = None
		else:
			if not profile:
				blockers.append(_exception(
					"EXC-001", e.name, e.employee_name,
					_("No Salary Profile is effective on {0}.").format(end)))

		if profile and not blockers:
			try:
				twd, pay_days = compute_working_days(
					e.name, start, end, validate_attendance=validate_attendance,
					based_on_timesheet=based_on_timesheet)
				engine.validate_working_days(twd, pay_days, employee=e.employee_name,
				                             start_date=start, end_date=end)
			except frappe.ValidationError as exc:
				blockers.append(_exception("EXC-003", e.name, e.employee_name,
				                           frappe.utils.strip_html(str(exc))))
				twd = pay_days = 0.0

			if not blockers:
				if not profile.irt_table:
					profile.irt_table = settings.default_irt_table
				try:
					res = engine.compute_slip(profile, {
						"payment_days": pay_days, "total_working_days": twd,
						"start_date": start, "end_date": end,
					}, settings=settings, on_date=end, employee=e.employee_name)
				except frappe.ValidationError as exc:
					blockers.append(_exception("EXC-005", e.name, e.employee_name,
					                           frappe.utils.strip_html(str(exc))))
					res = None
				else:
					if res.get("has_negative_net"):
						blockers.append(_exception(
							"EXC-004", e.name, e.employee_name,
							_("Net pay would be {0}.").format(flt(res["net_pay"], 2))))
					if include_variance:
						exceptions.extend(_variance_exceptions(
							e, res, previous.get(e.name), threshold,
							ever_paid=e.name in ever_paid))

		existing = existing_slips.get(e.name)
		if existing:
			exceptions.append(_exception(
				"EXC-012", e.name, e.employee_name,
				_("Salary Slip {0} already covers this period.").format(existing.name))
				if cint(existing.docstatus) == 1 else _exception(
				"EXC-012", e.name, e.employee_name,
				_("Draft Salary Slip {0} already covers this period.").format(existing.name)))
			# A draft is not a duplicate payment; only a submitted slip blocks a re-run.
			if cint(existing.docstatus) != 1:
				exceptions[-1]["severity"] = WARNING

		# Payment and compliance data — never blockers for calculation.
		if not (e.get("custom_iban") or "").strip():
			exceptions.append(_exception(
				"EXC-007", e.name, e.employee_name,
				_("No IBAN — this employee cannot be included in the bank transfer file.")))
			payment_blocked.add(e.name)
		if not (e.get("custom_nif") or "").strip():
			exceptions.append(_exception("EXC-008", e.name, e.employee_name,
			                             _("No NIF recorded (needed for the IRT report).")))
		if not (e.get("custom_inss_number") or "").strip():
			exceptions.append(_exception(
				"EXC-009", e.name, e.employee_name,
				_("No Social Security number recorded (needed for the INSS report).")))
		if occurrences.get(e.name):
			exceptions.append(_exception(
				"EXC-010", e.name, e.employee_name,
				_("{0} attendance occurrence(s) are still pending or unjustified.").format(
					occurrences[e.name])))

		exceptions.extend(blockers)
		if blockers:
			blocked_employees.add(e.name)
		else:
			ready += 1

	# Someone who was paid last period and is not in this one.
	if include_variance:
		for emp, row in previous.items():
			if emp in seen:
				continue
			name = frappe.db.get_value("Employee", emp, "employee_name") or emp
			exceptions.append(_exception(
				"EXC-025", emp, name,
				_("Paid in the previous period ({0} to {1}) but not eligible in this one.").format(
					prev_period[0], prev_period[1])))

	for exc in exceptions:
		if exc["severity"] == WARNING:
			warned.add(exc["employee"])
		elif exc["severity"] == PAYMENT:
			payment_blocked.add(exc["employee"])

	if config_blocking:
		for item in config_blocking:
			exceptions.append(_exception(
				"EXC-006" if item["key"].startswith("account") or "account" in item["key"]
				else "EXC-005", None, None,
				_("{0}: {1}").format(item["label"], item["status"])))

	by_code = {}
	for exc in exceptions:
		by_code.setdefault(exc["code"], {"code": exc["code"], "severity": exc["severity"],
		                                 "label": exc["label"], "count": 0})["count"] += 1

	return {
		"company": company,
		"start_date": str(start),
		"end_date": str(end),
		"total_employees": len(employees),
		"ready": ready,
		"blocked": len(blocked_employees),
		"payment_blocked": len(payment_blocked),
		"warnings": len([x for x in exceptions if x["severity"] == WARNING]),
		"warned_employees": len(warned - {None}),
		"can_calculate": not blocked_employees and not config_blocking,
		"configuration": config,
		"configuration_ok": not config_blocking,
		"exceptions": exceptions,
		"summary": sorted(by_code.values(), key=lambda r: r["code"]),
	}


def _variance_exceptions(employee, result, previous, threshold, ever_paid=False):
	"""Anomaly detection against the previous payroll of the same length.

	Kept to a handful of high-value rules on purpose. A readiness panel that cries wolf
	on twenty different metrics gets ignored, and an ignored panel is worse than none.
	"""
	out = []
	if not previous:
		# "First payroll" only means something when the employee has NEVER been paid.
		# Somebody simply absent from the last run is not an anomaly worth a warning.
		if ever_paid:
			return out
		out.append(_exception("EXC-024", employee.name, employee.employee_name,
		                      _("This employee has no previous payroll — first run.")))
		return out

	def pct(now_value, before):
		before = flt(before)
		if not before:
			return None
		return flt((flt(now_value) - before) / before * 100.0, 1)

	gross_delta = pct(result.get("gross_pay"), previous.gross_pay)
	if gross_delta is not None and abs(gross_delta) >= threshold:
		out.append(_exception(
			"EXC-020", employee.name, employee.employee_name,
			_("Gross remuneration changes by {0}% versus the previous period.").format(gross_delta)))

	net_delta = pct(result.get("net_pay"), previous.net_pay)
	if net_delta is not None and abs(net_delta) >= threshold:
		out.append(_exception(
			"EXC-021", employee.name, employee.employee_name,
			_("Net pay changes by {0}% versus the previous period.").format(net_delta)))

	if flt(previous.irt_amount) > 0 and not flt(result.get("irt_amount")):
		out.append(_exception("EXC-022", employee.name, employee.employee_name,
		                      _("IRT was {0} last period and is zero now.").format(
			                      flt(previous.irt_amount, 2))))

	if flt(previous.ss_employee_amount) > 0 and not flt(result.get("ss_employee_amount")):
		out.append(_exception("EXC-023", employee.name, employee.employee_name,
		                      _("Social Security was {0} last period and is zero now.").format(
			                      flt(previous.ss_employee_amount, 2))))
	return out


def blocking_exceptions(company, start_date, end_date, **kwargs):
	"""Only the reasons payroll must not proceed — used as a gate by the workflow."""
	report = evaluate(company, start_date, end_date, include_variance=False, **kwargs)
	return [e for e in report["exceptions"] if e["severity"] == BLOCKING]


def assert_ready_to_submit(entry):
	"""Refuse to send payroll for approval while blocking exceptions remain.

	The exceptions are recomputed against the entry's own salary slips rather than the
	whole company: employees deliberately excluded from this run must not block it.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	rows = wf.slip_rows(entry)
	if not rows:
		frappe.throw(_("Calculate the payroll before submitting it for approval — this entry "
		               "has no salary slips."))
	negative = [r for r in rows if flt(r["net_pay"]) < 0]
	if negative:
		frappe.throw(
			_("{0} salary slip(s) have a negative net pay and cannot be approved: {1}.").format(
				len(negative), ", ".join((r["employee_name"] or r["employee"]) for r in negative[:8])),
			title=_("Payroll Blocked"))

	config_blocking = [c for c in configuration_status(entry.company, on_date=entry.end_date)
	                   if not c["ok"] and c["severity"] == BLOCKING]
	if config_blocking:
		frappe.throw(
			_("Payroll configuration is incomplete: {0}. Fix it in Isoft HR Settings before "
			  "submitting payroll for approval.").format(
				", ".join("{0} ({1})".format(c["label"], c["status"]) for c in config_blocking)),
			title=_("Configuration Incomplete"))
	return True


def payment_blockers(entry):
	"""Employees of an entry that cannot be included in a bank transfer file."""
	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	out = []
	for row in wf.slip_rows(entry):
		if flt(row["net_pay"]) <= 0:
			continue
		iban = frappe.db.get_value("Employee", row["employee"], "custom_iban")
		if not (iban or "").strip():
			out.append(_exception("EXC-007", row["employee"], row["employee_name"],
			                      _("No IBAN recorded.")))
	return out
