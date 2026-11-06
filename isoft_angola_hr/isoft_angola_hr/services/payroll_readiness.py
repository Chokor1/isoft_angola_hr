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
	"EXC-001": (BLOCKING, "No Salary Profile"),
	"EXC-002": (BLOCKING, "Ambiguous Salary Profile"),
	"EXC-003": (BLOCKING, "Invalid working / payment days"),
	"EXC-004": (BLOCKING, "Negative net salary"),
	"EXC-005": (BLOCKING, "Missing statutory configuration"),
	"EXC-006": (BLOCKING, "Missing payroll account"),
	"EXC-007": (PAYMENT, "Missing IBAN"),
	"EXC-008": (WARNING, "Missing NIF"),
	"EXC-009": (WARNING, "Missing Social Security number"),
	"EXC-010": (WARNING, "Pending attendance occurrence"),
	"EXC-011": (BLOCKING, "Salary changes inside the payroll period"),
	"EXC-012": (BLOCKING, "Already processed in this period"),
	"EXC-013": (BLOCKING, "Salary Profile has no base salary"),
	"EXC-014": (WARNING, "Working calendar configuration"),
	# Variance / anomaly detection — warnings by design, never blockers.
	"EXC-020": (WARNING, "Gross salary variance"),
	"EXC-021": (WARNING, "Net salary variance"),
	"EXC-022": (WARNING, "IRT dropped to zero"),
	"EXC-023": (WARNING, "Social Security dropped to zero"),
	"EXC-024": (WARNING, "First payroll for this employee"),
	"EXC-025": (WARNING, "Employee missing from this payroll"),
	"EXC-026": (WARNING, "Suspended employee — suspension is not prorated"),
}

DEFAULT_VARIANCE_THRESHOLD = 25.0


def _exception(code, employee=None, employee_name=None, message=None):
	severity, label = EXCEPTIONS[code]
	# Translated HERE. The EXCEPTIONS table is a module-level constant, and a _() in it
	# would run once at import and freeze every readiness message in whatever language
	# the worker booted in.
	label = _(label)
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

	def add(key, label, value, ok, hint=None, severity=BLOCKING, required=None, where=None):
		items.append({
			"key": key, "label": label, "value": value,
			"status": "Configured" if ok else ("Missing" if not value else "Invalid"),
			"ok": bool(ok), "severity": severity, "hint": hint,
			"required": required, "where": where,
		})

	from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa

	add("payroll_payable_account", _("Payroll Payable Account"), s.get("payroll_payable_account"),
	    _account_exists(s.get("payroll_payable_account")),
	    _("Required to post the payroll accrual."), required=_("Liability"), where=pa.WHERE_PAYABLE)
	add("salary_payment_account", _("Salary Payment Account (Bank/Cash)"),
	    s.get("salary_payment_account"), _account_exists(s.get("salary_payment_account")),
	    _("Required to post the salary payment."), required=_("Asset or Liability"), where=pa.WHERE_BANK)

	# One row per component that needs a GL account, so a half-mapped chart is visible
	# before posting fails on the first employee. Each row says what the account must be
	# and where it is set, so the blocker reads as an instruction (F-04).
	mapped = {r.abbr: r.account for r in s.get("component_accounts") or [] if r.account}
	for comp in engine.journal_components():
		account = mapped.get(comp["abbr"])
		req = pa.requirement(comp["abbr"])
		add("account:" + comp["abbr"], _("Account — {0}").format(req["label"]),
		    account, _account_exists(account), _("Settings → Account per Component."),
		    required=req["required"], where=req["where"])

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

	# --- the two statutory subsidies ----------------------------------------- #
	# Lei n.º 12/23 artigo 238.º makes both of these entitlements, so a run in the month
	# one of them falls due must say out loud that it is switched off. Otherwise the only
	# evidence is a December payroll that is silently 50% of a base lighter per employee.
	pay_month = s.get("natal_payment_month") or "December"
	if pay_month in engine.MONTHS and on_date.month == engine.MONTHS.index(pay_month) + 1:
		natal_on = engine.component_enabled(s, "SNA")
		add("enable_natal", _("Subsídio de Natal (13.º) for this period"),
		    _("Enabled — {0}% of base").format(flt(s.get("natal_rate")))
		    if natal_on else _("SWITCHED OFF"),
		    natal_on,
		    _("This payroll period ends in the configured Natal payment month. The Christmas "
		      "bonus is paid by its own run (Payroll → Run Type: Christmas Bonus), which "
		      "cannot be created while the component is switched off in Settings → Enabled "
		      "Components. Artigo 238.º n.º 1 al. b) makes it an entitlement of at least 50% "
		      "of the salário-base."),
		    severity=WARNING)

	# --- suspension: a gap in the data model, stated rather than hidden -------- #
	# Artigo 238.º n.º 3 prorates the supplement for a year interrupted by suspension as
	# well as one shortened by admission or termination. This application stores
	# suspension as an Employee status with no dates and keeps no history of it (Employee
	# carries no Version rows at all), so there is nothing to prorate against — not for
	# the current year and not retrospectively. That is a data-model gap and it is NOT
	# solved by guessing. The row is raised only where the company actually has a
	# suspended employee, so it stays a finding rather than furniture.
	# LEGAL VERIFICATION REQUIRED: whether a given suspension interrupts "months of
	# service" at all depends on the kind of suspension, which is a legal determination.
	suspended = frappe.db.count("Employee", {"company": company, "status": "Suspended"}) \
		if company else 0
	if suspended:
		add("suspension_proration", _("Suspension proration"),
		    _("{0} suspended employee(s)").format(suspended), False,
		    _("Suspension is recorded as a status with no start or end date, so no "
		      "subsidy is reduced in proportion to it. Artigo 238.º n.º 3 makes the "
		      "Christmas supplement proportional for an interrupted year. Adjust the "
		      "affected employees by hand: the amount is editable per employee in the "
		      "payroll preview, and a Final Settlement has a Supplement Months override."),
		    severity=WARNING)

	# --- settings that change pay silently when unset ------------------------- #
	# A missing value here does not stop payroll; it makes payroll use a default nobody
	# chose. That is worse than an error, because nothing on screen says it happened.
	basis = s.get("working_days_basis") or "Auto (Holiday List)"
	add("working_days_basis", _("Working Days Basis"), basis, True,
	    _("Decides what Total Working Days counts and how absences are priced."),
	    severity=WARNING)

	daily_hours = flt(s.get("standard_daily_hours"))
	add("standard_daily_hours", _("Standard Daily Hours"),
	    (str(daily_hours) if daily_hours else _("not set — using 8")),
	    bool(daily_hours),
	    _("Unset. Payroll is falling back to 8 hours a day. This divisor converts "
	      "Timesheet hours into Paid Days and sets the hourly value shown on the payslip, "
	      "so leaving it unset quietly assumes an 8-hour day. (Overtime does not use it: "
	      "its hourly wage is fixed by art. 237.º n.º 7 at 52 weeks of 44 hours.)"),
	    severity=WARNING)

	if basis == "Standard (Fixed)":
		fixed = flt(s.get("standard_working_days"))
		add("standard_working_days", _("Standard Working Days"),
		    (str(fixed) if fixed else _("not set — using 30")), bool(fixed),
		    _("Unset while the basis is Standard (Fixed). Payroll is falling back to 30 "
		      "days, which changes what a day of absence costs."), severity=WARNING)

	# The holiday list is what removes public holidays from the working-day count. With
	# none, every holiday is counted as a working day.
	if basis != "Standard (Fixed)":
		holiday_list = (frappe.db.get_value("Company", company, "default_holiday_list")
		                if company else None)
		add("default_holiday_list", _("Default Holiday List"), holiday_list,
		    bool(holiday_list),
		    _("No holiday list on the Company, so public holidays are being counted as "
		      "ordinary working days in Total Working Days."), severity=WARNING)
		if holiday_list:
			covered = frappe.db.sql(
				"""select count(*) from `tabHoliday` where parent = %s
				and holiday_date between %s and %s""",
				(holiday_list, getdate(on_date).replace(month=1, day=1),
				 getdate(on_date).replace(month=12, day=31)))[0][0]
			add("holiday_list_year", _("Holidays Loaded for {0}").format(getdate(on_date).year),
			    str(covered), bool(covered),
			    _("The holiday list has no dates in this year, so no holiday will be "
			      "excluded from the working-day count."), severity=WARNING)

	return items


# --------------------------------------------------------------------------- #
# Employee readiness
# --------------------------------------------------------------------------- #
def _eligible_employees(company, start, end, department=None, branch=None, designation=None,
                        employees=None):
	filters = {"status": "Active"}
	if company:
		filters["company"] = company
	for field, value in (("department", department), ("branch", branch),
	                     ("designation", designation)):
		if value:
			filters[field] = value
	if employees is not None:
		# A payroll run's own employees: the approval gate asks about them alone, so an
		# 80-employee run is not assessed against the whole company on every approval.
		filters["name"] = ("in", list(employees) or [""])
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
	# Salary slips only, here and below: the Christmas bonus run shares December's period
	# and is not a month's pay to compare against.
	prev_end = frappe.db.sql(
		"""select max(end_date) from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date < %s
		  and ifnull(run_type, 'Salary')='Salary'""",
		(company, getdate(start)))
	prev_end = prev_end[0][0] if prev_end and prev_end[0][0] else None
	if not prev_end:
		return {}, (None, None)
	prev_start = frappe.db.sql(
		"""select min(start_date) from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date = %s
		  and ifnull(run_type, 'Salary')='Salary'""", (company, prev_end))[0][0]
	rows = frappe.db.sql(
		"""select employee, gross_pay, net_pay, irt_amount, ss_employee_amount
		from `tabIsoft Salary Slip`
		where docstatus < 2 and company=%s and end_date = %s
		  and ifnull(run_type, 'Salary')='Salary'""",
		(company, prev_end), as_dict=True)
	return {r.employee: r for r in rows}, (getdate(prev_start), getdate(prev_end))


def _existing_slips(company, start, end):
	"""Every live slip overlapping this period, keyed by employee — one query for the
	whole company instead of one per employee."""
	rows = frappe.db.sql(
		"""select employee, name, docstatus from `tabIsoft Salary Slip`
		where company=%s and docstatus<2 and start_date<=%s and end_date>=%s
		  and ifnull(run_type, 'Salary')='Salary'""",
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
             validate_attendance=0, based_on_timesheet=0, include_variance=True, employees=None):
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

	employees = _eligible_employees(company, start, end, department, branch, designation,
	                                employees=employees)
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
			elif flt(profile.base) <= 0:
				# A profile that exists but carries no base pays nothing, and a slip worth
				# nothing cannot be posted: the ledger refuses a voucher whose debit and
				# credit are both zero, and because that refusal aborts the whole batch it
				# takes the other employees' posting and payment down with it. Caught here,
				# while the run is still a draft — once the payroll is Approved the slip
				# can no longer be corrected without cancelling the approval.
				blockers.append(_exception(
					"EXC-013", e.name, e.employee_name,
					_("Salary Profile {0} has a base salary of zero, so this employee's "
					  "slip would be worth nothing and could not be posted.")
					.format(profile.name)))

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

		# Which calendar this employee will actually be paid against. A warning, never a
		# blocker: the payroll is calculable either way, and the question HR needs
		# answered is not "is something wrong" but "which Holiday List did it use" —
		# because the wrong one silently pays public holidays as ordinary working days.
		for msg in _calendar_warnings(e, start, end):
			exceptions.append(_exception("EXC-014", e.name, e.employee_name, msg))

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
			row = frappe.db.get_value("Employee", emp, ["employee_name", "status"],
			                          as_dict=True) or frappe._dict()
			name = row.get("employee_name") or emp
			# Naming the status matters: "not eligible" is the same sentence for somebody
			# who left, somebody who was made inactive and somebody who was suspended, and
			# only one of those three is reversible.
			exceptions.append(_exception(
				"EXC-025", emp, name,
				_("Paid in the previous period ({0} to {1}) but not eligible in this one "
				  "— the employee is {2}.").format(
					prev_period[0], prev_period[1], _(row.get("status") or "not Active"))))
			if row.get("status") == "Suspended":
				exceptions.append(_exception(
					"EXC-026", emp, name,
					_("Suspended, so no salary is calculated for this period. The "
					  "application records suspension as a status only, with no start or "
					  "end date, so no subsidy can be reduced in proportion to it — any "
					  "adjustment owed under artigo 238.º n.º 3 has to be entered by hand.")))

	for exc in exceptions:
		if exc["severity"] == WARNING:
			warned.add(exc["employee"])
		elif exc["severity"] == PAYMENT:
			payment_blocked.add(exc["employee"])

	if config_blocking:
		for item in config_blocking:
			is_account = item["key"].startswith("account") or "account" in item["key"]
			if is_account:
				message = _("BLOCKING — Missing payroll account mapping: {0} — {1} — required "
				            "account type: {2} — configure in {3}").format(
					item["label"].replace(_("Account — "), ""), item["status"],
					item.get("required") or "?", item.get("where") or pa.WHERE_COMPONENTS)
			else:
				message = _("{0}: {1}").format(item["label"], item["status"])
			exceptions.append(_exception("EXC-006" if is_account else "EXC-005", None, None, message))

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



def _calendar_warnings(employee, start, end):
	"""Anything HR should know about the calendar this employee is being paid against.

	Always names the list that will ACTUALLY be used. A warning saying only "the holiday
	list is out of date" leaves the reader unable to tell whether the payroll in front of
	them is right, which is the question they are actually asking.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import work_calendar

	info = work_calendar.diagnose(employee.name, start, end)
	out = []
	resolved = info["resolved_holiday_list"]

	if not resolved:
		out.append(_("No Holiday List resolves for this employee: no Shift Type list, no "
		             "Employee Holiday List and no Company default. Public holidays will "
		             "be paid as ordinary working days."))
		return out

	if not info["any_covers"]:
		out.append(_("No configured Holiday List covers this payroll period — {0} (from "
		             "the {1}) runs {2} to {3}. No public holiday will be deducted for "
		             "this employee.")
		           .format(resolved, info["source"], info["from_date"], info["to_date"]))
		return out

	for skipped in info["bypassed"]:
		if skipped["expired"]:
			out.append(_("The {0} record still points at {1}, which ran {2} to {3} and "
			             "does not cover this period. Paid against {4} instead, from the "
			             "{5}.").format(skipped["source"], skipped["list"],
			                            skipped["from_date"], skipped["to_date"],
			                            resolved, info["source"]))
		else:
			out.append(_("The {0} record says {1}, but {2} (from the {3}) takes "
			             "precedence and is what this payroll uses.")
			           .format(skipped["source"], skipped["list"], resolved,
			                   info["source"]))
	# Deliberately NOT warned about: a list that covers the period and contains no
	# holiday in it. August has no Angolan public holiday and neither do several other
	# months; flagging every one of them would fire this code at everybody, every quiet
	# month, and teach HR to scroll past it — including on the months that matter.
	return out


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
