# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Code-defined Angola payroll engine.

The standard salary components and the way the slip is computed live HERE, in
code — not as hand-entered Salary Structure / Salary Component records. Per
customer you parametrise behaviour through "Isoft HR Settings" and per employee
through "Isoft Salary Profile"; the monthly variable inputs (productivity bonus,
overtime, advance) are entered on the "Isoft Salary Slip".

Component model (abbr -> definition):
  - kind: "earning" | "deduction"
  - in_gross: whether it counts toward Gross Pay (the Rendimento Tributável line
    is statistical, so in_gross=False)
  - taxable: whether it feeds the IRT taxable base
  - ss_base: whether it feeds the Segurança Social contribution base
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import resolve_irt
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_statutory_rate.isoft_statutory_rate import (
	get_statutory_rates,
	require_rate,
)


# Standard component catalogue (fixed in the app, configurable via Settings).
COMPONENTS = {
	"SB": {"name": "Salário Base", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"SDA": {"name": "Subsídio de Alimentação", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"SDT": {"name": "Subsídio de Transporte", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"AF": {"name": "Abono de Família", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	# Duodécimos — Angola holiday (férias) and Christmas (Natal) subsidies, accrued
	# monthly as a % of base. Optional, fully taxable and fully in the SS base.
	"SFE": {"name": "Subsídio de Férias", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"SNA": {"name": "Subsídio de Natal", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"PPD": {"name": "Prémio de Produtividade", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"HEX": {"name": "Horas Extras", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"TI": {"name": "Rendimento Tributável", "kind": "earning", "in_gross": False, "ss_base": False, "prorate": False},
	"CTSS3": {"name": "Segurança Social 3%", "kind": "deduction", "prorate": False},
	"IRT": {"name": "IRT", "kind": "deduction", "prorate": False},
	"ADT": {"name": "Adiantamento", "kind": "deduction", "prorate": False},
	# Employer social-security contribution. NOT a deduction: it never touches gross,
	# the taxable base or net pay. It is an employer cost booked as expense/liability,
	# so it needs its own pair of accounts.
	"CTSSE": {"name": "Segurança Social - Entidade Patronal (Custo)",
	          "kind": "employer_expense", "prorate": False},
	"CTSSP": {"name": "Segurança Social - Entidade Patronal (A Pagar)",
	          "kind": "employer_liability", "prorate": False},
}

# Kinds debited / credited when posting the accrual Journal Entry.
DEBIT_KINDS = ("earning", "employer_expense")
CREDIT_KINDS = ("deduction", "employer_liability")


def journal_components():
	"""Components that need a GL account in a Journal Entry: cash earnings and the
	employer-contribution expense (debit), deductions and the employer-contribution
	liability (credit). Excludes the statistical Rendimento Tributável (TI)."""
	out = []
	for abbr, c in COMPONENTS.items():
		if c["kind"] == "earning" and not c.get("in_gross"):
			continue
		if c["kind"] in DEBIT_KINDS or c["kind"] in CREDIT_KINDS:
			out.append({"abbr": abbr, "component": c["name"], "kind": c["kind"]})
	return out


def get_settings():
	return frappe.get_cached_doc("Isoft HR Settings")


#: Settings checkbox governing each optional component. Components not listed are always
#: part of the salary.
COMPONENT_TOGGLES = {
	"AF": "enable_family_allowance",
	"PPD": "enable_productivity_bonus",
	"HEX": "enable_overtime",
	"ADT": "enable_adiantamento",
	"SFE": "enable_ferias",
	"SNA": "enable_natal",
}


def component_enabled(settings, abbr):
	"""Whether an optional component is switched on for this site.

	The "Enabled Components" checkboxes existed but nothing read them, so unticking
	"Abono de Família" changed nothing at all. A checkbox that implies behaviour it does
	not have is worse than no checkbox.

	An ABSENT key means enabled: a caller that supplies its own settings (the payroll
	tests, an integration) must not have components silently removed from its calculation
	just because it did not know about a toggle. Only an explicit 0 disables.
	"""
	field = COMPONENT_TOGGLES.get(abbr)
	if not field:
		return True
	value = settings.get(field) if settings is not None else None
	return True if value is None else bool(cint(value))


def validate_working_days(total_days, pay_days, employee=None, start_date=None, end_date=None):
	"""Guard the proration inputs before they can produce an impossible salary.

	Previously the factor was ``pay_days / total_days if total_days else 1.0``, so a
	period with zero working days paid a FULL month, and nothing capped ``pay_days``
	at ``total_days`` (45 paid days out of 30 paid 150% of salary). Both are
	configuration/data errors and must stop payroll rather than silently mis-pay.
	"""
	who = _(" for {0}").format(employee) if employee else ""
	period = ""
	if start_date and end_date:
		period = _(" in the period {0} to {1}").format(start_date, end_date)

	if flt(total_days) < 0 or flt(pay_days) < 0:
		frappe.throw(
			_("Working days and payment days cannot be negative{0} "
			  "(working days {1}, payment days {2}).").format(who, flt(total_days), flt(pay_days))
		)
	if flt(total_days) <= 0:
		frappe.throw(
			_("No working days were found{0}{1}. Check the holiday list, the shift and the "
			  "Working Days Basis in Isoft HR Settings. Payroll cannot be calculated.").format(
				who, period)
		)
	if flt(pay_days) > flt(total_days):
		frappe.throw(
			_("Payment days ({0}) cannot exceed the {1} working days{2}{3}.").format(
				flt(pay_days), flt(total_days), who, period)
		)


def ferias_full(base, ferias_rate):
	"""Full Vacation Allowance = base * ferias_rate% (what an employee gets the month
	they take their annual leave)."""
	return flt(flt(base) * (flt(ferias_rate) or 0.0) / 100.0, 2)


MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def default_natal(base, natal_rate, joining_date, period_end, payment_month=None):
	"""Default 13th-month (Natal) allowance: base * natal_rate%, prorated by the months
	worked in the period's year (full year -> full; joined mid-year -> months since joining
	/ 12). Paid only in the payroll period that ENDS in the configured payment month
	(default December). Zero otherwise. HR can override the returned value per employee."""
	end = getdate(period_end) if period_end else None
	pm = payment_month if payment_month in MONTHS else "December"
	month_idx = MONTHS.index(pm) + 1
	if not end or end.month != month_idx:
		return 0.0
	full = flt(base) * (flt(natal_rate) or 0.0) / 100.0
	months = 12
	if joining_date:
		jd = getdate(joining_date)
		if jd.year == end.year:
			months = max(0, min(12, 12 - jd.month + 1))
		elif jd.year > end.year:
			months = 0
	return flt(full * months / 12.0, 2)


def settlement_months(joining_date, termination_date):
	"""Number of months of the termination YEAR the employee worked, for the proportional
	Vacation / Christmas allowances. Full year when hired in a previous year; from the hiring
	month when hired during the termination year. Inclusive of the termination month."""
	end = getdate(termination_date)
	start_month = 1
	if joining_date:
		jd = getdate(joining_date)
		if jd.year > end.year:
			return 0
		if jd.year == end.year:
			start_month = jd.month
	return max(0, min(12, end.month - start_month + 1))


def compute_settlement(inputs):
	"""Final-settlement (termination) gross calculation — see the two ITEC worked examples.

	inputs (all already resolved by the caller):
	    base, food_allowance, transport_allowance   — the monthly remuneration components
	    salary_days_worked                           — working days in the final (partial) period
	    salary_days                                  — the monthly divisor for the daily rate (26)
	    months_worked                                — months of the year for the allowances
	    ferias_rate, natal_rate                      — % of base (50 each)
	    untaken_leave_days                           — accrued-but-untaken annual-leave days
	    leave_days                                   — the monthly divisor for the leave daily rate (22)

	Rounding follows the ITEC drafts exactly: the salary uses a rounded daily-rate × days
	(headline figure HR can reproduce), the allowances and leave compensation round the final
	amount.
	"""
	def r2(v):
		return flt(v, 2)

	base = flt(inputs.get("base"))
	food = flt(inputs.get("food_allowance"))
	transport = flt(inputs.get("transport_allowance"))
	monthly = r2(base + food + transport)

	# 1. Proportional salary for the final period.
	salary_days = flt(inputs.get("salary_days")) or 26.0
	days_worked = flt(inputs.get("salary_days_worked"))
	salary_daily = r2(monthly / salary_days) if salary_days else 0.0
	# A full (or over-full) period pays the whole monthly remuneration; a shortened final
	# period pays the daily rate × days worked (matches the ITEC drafts).
	period_salary = monthly if (salary_days and days_worked >= salary_days) else r2(salary_daily * days_worked)

	# 2 & 3. Proportional Vacation Allowance and Christmas Bonus (% of base, months/12).
	months = flt(inputs.get("months_worked"))
	frate = flt(inputs.get("ferias_rate"))
	nrate = flt(inputs.get("natal_rate"))
	vacation_annual = base * frate / 100.0
	christmas_annual = base * nrate / 100.0
	vacation_monthly = r2(vacation_annual / 12.0)
	christmas_monthly = r2(christmas_annual / 12.0)
	vacation_allowance = r2(vacation_annual * months / 12.0)
	christmas_bonus = r2(christmas_annual * months / 12.0)

	# 4. Compensation for accrued-but-untaken annual leave.
	leave_days = flt(inputs.get("leave_days")) or 22.0
	untaken = flt(inputs.get("untaken_leave_days"))
	leave_daily = r2(base / leave_days) if leave_days else 0.0
	untaken_amount = r2(base * untaken / leave_days) if leave_days else 0.0

	total_gross = r2(period_salary + vacation_allowance + christmas_bonus + untaken_amount)
	return {
		"monthly_remuneration": monthly,
		"salary_daily_rate": salary_daily,
		"period_salary": period_salary,
		"vacation_monthly": vacation_monthly,
		"vacation_allowance": vacation_allowance,
		"christmas_monthly": christmas_monthly,
		"christmas_bonus": christmas_bonus,
		"leave_daily_rate": leave_daily,
		"untaken_leave_amount": untaken_amount,
		"total_gross": total_gross,
	}


def compute_slip(profile, inputs, settings=None, on_date=None, irt_table=None, rates=None,
                 employee=None):
	"""Compute an Angola salary slip.

	:param profile: dict-like with base, food_allowance, transport_allowance,
	        family_allowance, company, irt_table (optional)
	:param inputs: dict with productivity_bonus, overtime_amount, adiantamento,
	        advance_recovery, payment_days, total_working_days
	:param irt_table: an already-loaded IRT Table document; resolved by effective date
	        when omitted
	:param rates: an already-resolved statutory rate set; resolved by effective date
	        when omitted
	:returns: dict with earnings, deductions, the totals, the employer contribution and
	        the full statutory calculation trace
	"""
	s = settings or get_settings()

	def get(field):
		return flt(profile.get(field) if hasattr(profile, "get") else getattr(profile, field, 0))

	company = (profile.get("company") if hasattr(profile, "get")
	           else getattr(profile, "company", None)) or None

	r = rates or get_statutory_rates(company=company, on_date=on_date, settings=s)
	ss_rate = require_rate(r, "ss_employee_rate", _("Social Security - Employee Rate"))
	ss_employer_rate = require_rate(r, "ss_employer_rate", _("Social Security - Employer Rate"))
	food_exempt = flt(r.get("food_allowance_exemption"))
	transport_exempt = flt(r.get("transport_allowance_exemption"))

	total_days = flt(inputs.get("total_working_days"))
	pay_days = flt(inputs.get("payment_days"))
	validate_working_days(total_days, pay_days, employee=employee,
	                      start_date=inputs.get("start_date"), end_date=inputs.get("end_date"))
	factor = pay_days / total_days

	# --- Earnings ---
	sb = flt(get("base") * factor, 2)
	sda = flt(get("food_allowance") * factor, 2)
	sdt = flt(get("transport_allowance") * factor, 2)
	# Abono de Família is the one optional component that comes from the salary profile
	# rather than from a monthly input, so the Settings toggle is applied here. The
	# input-driven components (bonus, overtime, advance, férias, natal) are gated on the
	# Salary Slip instead, where an existing amount can be reported rather than silently
	# dropped — see IsoftSalarySlip.validate_enabled_components.
	af = flt(get("family_allowance") * factor, 2) if component_enabled(s, "AF") else 0.0
	ppd = flt(inputs.get("productivity_bonus"))
	hex_ = flt(inputs.get("overtime_amount"))

	# Vacation (VA / Subsídio de Férias) and Christmas (CA / Subsídio de Natal) are
	# per-employee amounts decided in the payroll run (see api.payroll_preview for the
	# defaults: Férias is paid the month the employee takes leave; Natal in December,
	# prorated by months worked). The engine just consumes the amounts.
	sfe = flt(inputs.get("ferias_amount"))
	sna = flt(inputs.get("natal_amount"))

	# Taxable portions of allowances (exempt up to the configured threshold).
	# LEGAL VERIFICATION REQUIRED: the threshold is a fixed monthly amount and is NOT
	# prorated for partial months. This is the behaviour that existed before Phase 1 and
	# is deliberately left unchanged — see the module docstring of isoft_statutory_rate.
	food_exemption_applied = min(sda, food_exempt)
	transport_exemption_applied = min(sdt, transport_exempt)
	taxable_food = max(0.0, sda - food_exempt)
	taxable_transport = max(0.0, sdt - transport_exempt)

	# Segurança Social incidence base: the Vacation Allowance (SFE) is excluded, the
	# Christmas Allowance (SNA) is included.
	# LEGAL VERIFICATION REQUIRED — this base is unchanged from the pre-Phase-1
	# implementation and is pinned by test_incidence_base_matches_documented_behaviour.
	# The EMPLOYER contribution deliberately uses the SAME base as the employee one,
	# because no authoritative source establishing a different base was available.
	ss_base = sb + af + sda + sdt + ppd + hex_ + sna
	ctss3 = flt(ss_base * ss_rate / 100.0, 2)
	# Employer contribution: a company cost. It must never enter gross, the taxable
	# base, the deductions or net pay.
	ss_employer = flt(ss_base * ss_employer_rate / 100.0, 2)

	# Rendimento Tributável (MC) — includes both VA and CA fully; SS is deducted before IRT.
	taxable_income = flt(sb + taxable_food + taxable_transport + ppd + hex_ + sfe + sna - ctss3, 2)

	# IRT from the Angola IRT Table (monthly-direct), resolved by effective date.
	table = irt_table
	if table is None:
		table_name = (profile.get("irt_table") if hasattr(profile, "get")
		              else getattr(profile, "irt_table", None))
		table = frappe.get_cached_doc("IRT Table", table_name) if table_name else None
	irt_trace = resolve_irt(taxable_income, company=company, on_date=on_date, table=table)
	irt = irt_trace.amount

	adiantamento = flt(inputs.get("adiantamento"))
	# Salary Advance recovery. Defaults to 0, so every caller that predates advances
	# calculates exactly as before. It is capped below so that recovering an advance can
	# never drive net pay negative — Phase 1 blocks a negative net outright, which would
	# mean an advance stopping somebody's whole salary.
	advance_recovery = flt(inputs.get("advance_recovery"))

	earnings = []
	def add_e(abbr, amount):
		if amount or abbr in ("TI",):
			c = COMPONENTS[abbr]
			earnings.append({"abbr": abbr, "salary_component": c["name"], "amount": flt(amount, 2),
			                 "do_not_include_in_total": 0 if c["in_gross"] else 1})

	add_e("SB", sb)
	add_e("SDA", sda)
	add_e("SDT", sdt)
	add_e("AF", af)
	add_e("SFE", sfe)
	add_e("SNA", sna)
	add_e("PPD", ppd)
	add_e("HEX", hex_)
	add_e("TI", taxable_income)  # statistical, excluded from gross

	deductions = []
	def add_d(abbr, amount):
		if amount:
			deductions.append({"abbr": abbr, "salary_component": COMPONENTS[abbr]["name"], "amount": flt(amount, 2)})

	# Cap the advance recovery at whatever is left after the statutory deductions and any
	# manually entered advance. Recovering more than the employee earns is not a payroll
	# outcome, it is a data error, and the remainder simply stays outstanding.
	gross_pay = flt(sum(e["amount"] for e in earnings if not e["do_not_include_in_total"]), 2)
	available = flt(gross_pay - ctss3 - irt - adiantamento, 2)
	advance_recovered = flt(max(0.0, min(advance_recovery, max(0.0, available))), 2)
	advance_deferred = flt(advance_recovery - advance_recovered, 2)

	add_d("CTSS3", ctss3)
	add_d("IRT", irt)
	add_d("ADT", flt(adiantamento + advance_recovered, 2))

	total_deduction = flt(sum(d["amount"] for d in deductions), 2)
	net_pay = flt(gross_pay - total_deduction, 2)

	return {
		"earnings": earnings,
		"deductions": deductions,
		"gross_pay": gross_pay,
		"total_deduction": total_deduction,
		"net_pay": net_pay,
		"taxable_income": taxable_income,
		"payment_factor": factor,
		"advance_requested": flt(advance_recovery, 2),
		"advance_recovered": advance_recovered,
		"advance_deferred": advance_deferred,
		# --- exception flag: computed so the preview can SHOW the problem; the salary
		# slip refuses to submit and the bank export refuses to include it.
		"has_negative_net": net_pay < 0,
		# --- Segurança Social trace ---
		"ss_base": flt(ss_base, 2),
		"ss_employee_rate": ss_rate,
		"ss_employee_amount": ctss3,
		"ss_employer_rate": ss_employer_rate,
		"ss_employer_amount": ss_employer,
		"employer_cost": flt(gross_pay + ss_employer, 2),
		"statutory_rate": r.get("statutory_rate"),
		# --- IRT trace: enough to reproduce the figure after the table changes ---
		"irt_amount": irt,
		"irt_table": irt_trace.table,
		"irt_effective_from": irt_trace.effective_from,
		"irt_bracket_from": irt_trace.bracket_from,
		"irt_bracket_to": irt_trace.bracket_to,
		"irt_excess_over": irt_trace.excess_over,
		"irt_rate": irt_trace.rate,
		"irt_parcela_fixa": irt_trace.parcela_fixa,
		# --- exemption trace ---
		"food_exemption_applied": flt(food_exemption_applied, 2),
		"transport_exemption_applied": flt(transport_exemption_applied, 2),
	}
