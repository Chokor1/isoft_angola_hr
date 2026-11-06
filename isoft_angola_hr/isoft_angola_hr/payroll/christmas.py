# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The Christmas bonus (Subsídio de Natal) as its own payroll run.

Source: HR's document "Natal" (November 2026). The bonus is paid in the first fortnight of
December, before the December salary, so it is calculated, approved and paid by a run of
its own (Isoft Payroll Entry.run_type = "Christmas Bonus"):

    SN  = Basic Salary × 50%                       12 months or more of service
    SN  = ((Basic Salary × 50%) / 12) × M          fewer than 12 months
    IRT = FP + (SN − EXC) × %                      on the bonus ALONE, from the IRT table
    T   = SN − IRT                                 no INSS, employee or employer

400 000,00 and 12 months → SN 200 000,00, IRT 20 500,00, T 179 500,00.
400 000,00 and 8 months  → SN 133 333,33, IRT 0,00, T 133 333,33.

M is the complete months of service in the bonus year counted to 31 December — the same
definition the December Natal and the final settlement have always used
(engine.natal_months) — so a 1 December admission is worth 1/12 and a 2 December one
nothing. The 50% is Isoft HR Settings.natal_rate.

compute_bonus returns the dict engine.compute_slip returns, so the slip, the posting, the
bank file and the receipt read a bonus slip exactly as they read a salary slip.
"""

from datetime import date

import frappe
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services.overtime import _decimal, _money

RUN_SALARY = "Salary"
RUN_CHRISTMAS = "Christmas Bonus"


def bonus_months(joining_date, year):
	"""Complete months of service in ``year``, counted to 31 December."""
	return engine.natal_months(joining_date, date(int(year), 12, 31))


def full_year_amount(base, rate):
	"""The bonus for a full year: base × rate%."""
	return float(_money(_decimal(base, "base") * _decimal(rate, "rate") / 100))


def bonus_period(year):
	"""The payroll period the bonus run belongs to: the cycle period that ends in December
	(23 November – 22 December on a cycle that starts on the 23rd), so the bonus lands in
	December in the reports, the IRT declaration and the bank reference."""
	from isoft_angola_hr.isoft_angola_hr.api import _period_for_month  # api imports engine
	return _period_for_month("{0}-12".format(int(year)))


def compute_bonus(base, rate, months, on_date, company=None, irt_table=None, override=None):
	"""One employee's bonus, IRT and net — shaped like engine.compute_slip's result.

	:param override: an amount HR entered instead of the formula (the caller checks the
	        reason and the ceiling); IRT is always recalculated on what is paid
	"""
	months = max(0, min(12, int(months or 0)))
	if override is not None:
		sn = float(_money(_decimal(override, "override")))
	else:
		sn = float(_money(_decimal(base, "base") * _decimal(rate, "rate") / 100 * months / 12))
	irt = engine.resolve_irt(sn, company=company, on_date=on_date, table=irt_table)

	earnings = []
	if sn:
		earnings.append({"abbr": "SNA", "salary_component": engine.COMPONENTS["SNA"]["name"],
		                 "amount": sn, "do_not_include_in_total": 0})
	deductions = []
	if irt.amount:
		deductions.append({"abbr": "IRT", "salary_component": engine.COMPONENTS["IRT"]["name"],
		                   "amount": flt(irt.amount, 2)})
	net = flt(sn - flt(irt.amount, 2), 2)
	return {
		"earnings": earnings,
		"deductions": deductions,
		"gross_pay": sn,
		"total_deduction": flt(irt.amount, 2),
		"net_pay": net,
		"taxable_income": sn,
		"payment_factor": 1.0,
		"has_negative_net": net < 0,
		"natal_amount": sn,
		"natal_months": months,
		"natal_base": flt(base, 2),
		# no INSS on the bonus (HR document, confirmed 2026-11-06)
		"ss_base": 0.0, "ss_employee_rate": 0.0, "ss_employee_amount": 0.0,
		"ss_employer_rate": 0.0, "ss_employer_amount": 0.0, "statutory_rate": None,
		"employer_cost": sn,
		"irt_amount": flt(irt.amount, 2),
		"irt_table": irt.table,
		"irt_effective_from": irt.effective_from,
		"irt_bracket_from": irt.bracket_from,
		"irt_bracket_to": irt.bracket_to,
		"irt_excess_over": irt.excess_over,
		"irt_rate": irt.rate,
		"irt_parcela_fixa": irt.parcela_fixa,
		"food_exemption_applied": 0.0,
		"transport_exemption_applied": 0.0,
	}


def bonus_slips_in_year(employee, year, exclude=None):
	"""Names of the employee's submitted Christmas bonus slips for ``year``."""
	return frappe.db.sql_list(
		"""select name from `tabIsoft Salary Slip`
		where employee=%s and docstatus=1 and run_type=%s and year(end_date)=%s and name!=%s
		order by name""", (employee, RUN_CHRISTMAS, int(year), exclude or ""))


def paid_in_year(employee, year):
	"""The Christmas bonus already paid to the employee for ``year`` by bonus runs."""
	return flt(frappe.db.sql(
		"""select ifnull(sum(subsidio_natal), 0) from `tabIsoft Salary Slip`
		where employee=%s and docstatus=1 and run_type=%s and year(end_date)=%s""",
		(employee, RUN_CHRISTMAS, int(year)))[0][0], 2)


def exclusion_reason(employee, year):
	"""Why an employee gets no bonus run slip for ``year``, or None.

	:param employee: dict with name and date_of_joining
	"""
	from frappe import _

	if bonus_months(employee.get("date_of_joining"), year) < 1:
		return _("no complete month of service in {0}").format(year)
	settled = frappe.db.sql_list(
		"""select name from `tabIsoft Final Settlement`
		where employee=%s and docstatus=1 and year(termination_date)=%s
		  and ifnull(christmas_bonus, 0) > 0 limit 1""", (employee.get("name"), int(year)))
	if settled:
		return _("the Christmas bonus was paid by final settlement {0}").format(settled[0])
	return None


def _profile(employee, end, company):
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
		get_active_profile,
	)
	return get_active_profile(employee.name, end, company=company, employee_name=employee.employee_name)


def preview_rows(company, year, department=None, branch=None, designation=None, inputs=None):
	"""The bonus run's preview: one row per eligible employee — base, M, the bonus, IRT,
	net — with HR's per-employee overrides ({employee: {"christmas", "reason"}}) applied."""
	from frappe import _

	year = int(year)
	start, end = bonus_period(year)
	settings = engine.get_settings()
	rate = flt(settings.natal_rate)
	posting = getdate("{0}-12-15".format(year))
	filters = {"company": company, "status": ("in", ("Active", "Suspended"))}
	for field, value in (("department", department), ("branch", branch), ("designation", designation)):
		if value:
			filters[field] = value
	inputs = inputs or {}
	out = []
	for e in frappe.get_all("Employee", filters=filters, order_by="employee_name",
	                        fields=["name", "employee_name", "department", "designation",
	                                "date_of_joining", "status"], limit_page_length=2000):
		row = {"employee": e.name, "employee_name": e.employee_name, "department": e.department,
		       "designation": e.designation, "suspended": 1 if e.status == "Suspended" else 0,
		       "blocked": 0, "exception": None}
		reason = exclusion_reason(e, year)
		if reason:
			continue
		try:
			prof = _profile(e, end, company)
		except frappe.ValidationError as exc:
			row.update(blocked=1, exception=frappe.utils.strip_html(str(exc)))
			out.append(row)
			continue
		if not prof:
			row.update(blocked=1, exception=_("No Salary Profile is effective on {0}.").format(end))
			out.append(row)
			continue
		months = bonus_months(e.date_of_joining, year)
		default = compute_bonus(prof.base, rate, months, posting)["natal_amount"]
		inp = inputs.get(e.name) or {}
		override_reason = (inp.get("reason") or "").strip() or None
		amount = flt(inp["christmas"], 2) if inp.get("christmas") not in (None, "") else default
		res = compute_bonus(prof.base, rate, months, posting,
		                    override=amount if amount != default else None)
		existing = frappe.db.get_value("Isoft Salary Slip", {
			"employee": e.name, "run_type": RUN_CHRISTMAS, "docstatus": ("<", 2),
			"start_date": start, "end_date": end}, "name")
		row.update({
			"base": flt(prof.base, 2), "months": months, "christmas_default": default,
			"christmas_max": full_year_amount(prof.base, rate),
			"christmas": res["natal_amount"], "irt": res["irt_amount"], "net": res["net_pay"],
			"override_reason": override_reason, "already": 1 if existing else 0,
			"existing_slip": existing,
		})
		out.append(row)
	return out


def check_override(row, default, ceiling=None):
	"""HR may change one employee's bonus, never silently: a different amount needs a
	reason, and it may not exceed a full year's bonus (the salary slip enforces the same
	ceiling; checking it here refuses the whole run before anything is created)."""
	from frappe import _

	amount = flt(row.get("christmas"), 2)
	if ceiling is not None and (amount < 0 or amount > flt(ceiling, 2)):
		frappe.throw(_("The Christmas bonus for {0} cannot exceed a full year's bonus ({1}).").format(
			frappe.bold(row.get("employee_name") or row.get("employee")),
			frappe.utils.fmt_money(ceiling, 2)), title=_("Christmas Bonus Too High"))
	if amount != flt(default, 2) and not (row.get("override_reason") or "").strip():
		frappe.throw(_("The Christmas bonus of {0} was changed from {1} to {2}. Enter the reason "
		               "for the change.").format(
			frappe.bold(row.get("employee_name") or row.get("employee")),
			frappe.utils.fmt_money(default, 2), frappe.utils.fmt_money(amount, 2)),
			title=_("Reason Required"))
	return amount != flt(default, 2)
