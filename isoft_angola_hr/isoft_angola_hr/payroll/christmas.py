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
