# Copyright (c) 2026, Abbass Chokor and contributors
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
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import compute_irt


# Standard component catalogue (fixed in the app, configurable via Settings).
COMPONENTS = {
	"SB": {"name": "Salário Base", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"SDA": {"name": "Subsídio de Alimentação", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"SDT": {"name": "Subsídio de Transporte", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"AF": {"name": "Abono de Família", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": True},
	"PPD": {"name": "Prémio de Produtividade", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"HEX": {"name": "Horas Extras", "kind": "earning", "in_gross": True, "ss_base": True, "prorate": False},
	"TI": {"name": "Rendimento Tributável", "kind": "earning", "in_gross": False, "ss_base": False, "prorate": False},
	"CTSS3": {"name": "Segurança Social 3%", "kind": "deduction", "prorate": False},
	"IRT": {"name": "IRT", "kind": "deduction", "prorate": False},
	"ADT": {"name": "Adiantamento", "kind": "deduction", "prorate": False},
}


def journal_components():
	"""Components that need a GL account in a Journal Entry: cash earnings (debit) and
	deductions (credit). Excludes the statistical Rendimento Tributável (TI)."""
	out = []
	for abbr, c in COMPONENTS.items():
		if (c["kind"] == "earning" and c.get("in_gross")) or c["kind"] == "deduction":
			out.append({"abbr": abbr, "component": c["name"], "kind": c["kind"]})
	return out


def get_settings():
	return frappe.get_cached_doc("Isoft HR Settings")


def compute_slip(profile, inputs, settings=None, on_date=None):
	"""Compute an Angola salary slip.

	:param profile: dict-like with base, food_allowance, transport_allowance,
	        family_allowance, company, irt_table (optional)
	:param inputs: dict with productivity_bonus, overtime_amount, adiantamento,
	        payment_days, total_working_days
	:returns: dict {earnings: [...], deductions: [...], gross_pay, total_deduction,
	        net_pay, taxable_income}
	"""
	s = settings or get_settings()
	ss_rate = flt(s.ss_employee_rate) or 3.0
	food_exempt = flt(s.food_allowance_exemption)
	transport_exempt = flt(s.transport_allowance_exemption)

	total_days = flt(inputs.get("total_working_days"))
	pay_days = flt(inputs.get("payment_days"))
	# Prorate by payment/total when we have a total. pay_days == 0 must mean zero pay
	# (e.g. timesheet mode with no logged hours), not full pay.
	factor = (pay_days / total_days) if total_days else 1.0

	def get(field):
		return flt(profile.get(field) if hasattr(profile, "get") else getattr(profile, field, 0))

	# --- Earnings ---
	sb = flt(get("base") * factor, 2)
	sda = flt(get("food_allowance") * factor, 2)
	sdt = flt(get("transport_allowance") * factor, 2)
	af = flt(get("family_allowance") * factor, 2)
	ppd = flt(inputs.get("productivity_bonus"))
	hex_ = flt(inputs.get("overtime_amount"))

	# Taxable portions of allowances (exempt up to the configured threshold)
	taxable_food = max(0.0, sda - food_exempt)
	taxable_transport = max(0.0, sdt - transport_exempt)

	# Segurança Social base and contribution
	ss_base = sb + af + sda + sdt + ppd + hex_
	ctss3 = flt(ss_base * ss_rate / 100.0, 2)

	# Rendimento Tributável (taxable income) — SS is deducted before IRT
	taxable_income = flt(sb + taxable_food + taxable_transport + ppd + hex_ - ctss3, 2)

	# IRT from the Angola IRT Table (monthly-direct)
	irt_table = profile.get("irt_table") if hasattr(profile, "get") else getattr(profile, "irt_table", None)
	table = frappe.get_cached_doc("IRT Table", irt_table) if irt_table else None
	irt = compute_irt(taxable_income, company=get("company") or None, on_date=on_date, table=table)

	adiantamento = flt(inputs.get("adiantamento"))

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
	add_e("PPD", ppd)
	add_e("HEX", hex_)
	add_e("TI", taxable_income)  # statistical, excluded from gross

	deductions = []
	def add_d(abbr, amount):
		if amount:
			deductions.append({"abbr": abbr, "salary_component": COMPONENTS[abbr]["name"], "amount": flt(amount, 2)})

	add_d("CTSS3", ctss3)
	add_d("IRT", irt)
	add_d("ADT", adiantamento)

	gross_pay = flt(sum(e["amount"] for e in earnings if not e["do_not_include_in_total"]), 2)
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
	}
