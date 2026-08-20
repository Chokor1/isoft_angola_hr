# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Final-settlement engine — Lei n.º 12/23, de 27 de Dezembro.

WHAT THIS REPLACES
------------------
``engine.compute_settlement`` (calculation version 1) reproduced two hand-written ITEC
worked examples. It is kept, unchanged, so settlements calculated with it stay
reproducible — but it is not the law:

* it paid the full monthly remuneration whenever the days worked reached the divisor,
  while the screen went on displaying ``daily rate × days``, so the arithmetic shown to
  HR did not equal the amount paid;
* it counted "months worked" by month NUMBER, so a termination on the 21st of August
  counted August as a complete month (artigo 238.º n.º 3 says *meses completos*);
* it had one undifferentiated "untaken leave days" input, where artigo 212.º draws a
  sharp line between leave already vested and leave accruing in the current year;
* it priced leave off the salário-base over a divisor of 21 or 22 presented as a legal
  rate, when the statute fixes no monetary divisor at all;
* it never asked why the employment ended, so it could not compute — or correctly
  refuse to compute — the compensation of artigos 307.º to 310.º;
* it was gross-only, with a footnote saying IRT and INSS "apply" and no figure.

DESIGN RULES
------------
1. **Every line reconciles.** A line's ``amount`` is computed from the same operands its
   ``formula`` string prints. There is no code path that prints ``A × B`` and pays
   something else; :func:`_check_reconciles` asserts it before returning.
2. **Law and configuration are never confused.** Each line carries ``basis_kind`` of
   ``law`` (with the article), ``company`` (a divisor or rate this company chose) or
   ``input`` (a figure HR supplied). The UI renders the three differently.
3. **Silence is not zero.** Where the statute does not settle a question, the line comes
   back flagged ``legal_input_required`` or carries a ``LEGAL VERIFICATION REQUIRED``
   note, and the flag travels all the way to the screen.
4. **One statutory engine.** IRT and INSS reuse the payroll resolvers — the same
   effective-dated ``IRT Table`` and ``Isoft Statutory Rate`` records, with the same
   trace — so a settlement and a salary slip in the same month agree by construction.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.irt_table.irt_table import resolve_irt
from isoft_angola_hr.isoft_angola_hr.doctype.isoft_statutory_rate.isoft_statutory_rate import (
	get_statutory_rates,
	require_rate,
)
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

#: Bump when a change would alter the amount produced from identical inputs. Stored on
#: every settlement so an old record is always recomputed by the engine that made it.
CALC_VERSION = 2

MARKER = law.REVIEW_MARKER

# --------------------------------------------------------------------------- #
# Sections of the settlement, in the order they are presented.
# --------------------------------------------------------------------------- #
SEC_SALARY = "salary"
SEC_LEAVE = "leave"
SEC_SUPPLEMENTS = "supplements"
SEC_COMPENSATION = "compensation"
SEC_NOTICE = "notice"
SEC_OTHER = "other"

#: Salary-proration methods. Only ``hourly_237_7`` is statutory.
SALARY_METHODS = ("full_period", "hourly_237_7", "company_divisor")
#: Leave-pricing methods. NEITHER is statutory — the statute fixes no monetary divisor.
LEAVE_RATE_METHODS = ("company_divisor", "hourly_237_7")

#: Company tax position on termination compensation. See
#: ``law.settlement_reference()["open_questions"]`` — the sources genuinely conflict.
COMPENSATION_TAX_POSITIONS = ("exempt_within_lgt_limits", "taxable", "verification_required")

DEFAULT_WORKING_DAYS_PER_WEEK = 5


def _r2(v):
	return flt(v, 2)


def _money(v):
	"""Plain fixed-point rendering for formula strings. The UI formats currency itself;
	a formula must show the exact operand that was multiplied, not a rounded display."""
	return "{0:,.2f}".format(flt(v))


def _line(section, label, amount, basis_kind, formula=None, article=None, note=None,
          status="ok", irt=False, inss=False, sign=1, key=None, formula_check=None):
	return {
		"key": key or label,
		"section": section,
		"label": label,
		"amount": _r2(amount),
		"sign": sign,                 # +1 payable to the employee, -1 deducted
		"basis_kind": basis_kind,     # "law" | "company" | "input" | "none"
		"article": article,
		"formula": formula,
		# The purely numeric form of ``formula`` when the readable one carries words
		# ("× 5 years"). The reconciliation guard checks this instead.
		"formula_check": formula_check,
		"note": note,
		"status": status,             # "ok" | "legal_input_required" | "verify"
		"irt_taxable": bool(irt),
		"inss_base": bool(inss),
	}


def _check_reconciles(lines):
	"""Guard rule 1: a printed ``a × b`` must equal the amount paid.

	Only formulas made purely of numbers and ``× + - % ( ) /`` are checked; a formula
	such as "Full salary for the payroll period" carries no arithmetic to verify. A
	mismatch is a programming error, so it raises rather than warning.
	"""
	import re
	problems = []
	for ln in lines:
		f = ln.get("formula_check") or ln.get("formula")
		if not f or not re.match(r"^[\d\s,\.×−+\-*/%()]+$", f):
			continue
		expr = (f.replace(",", "").replace("×", "*").replace("−", "-"))
		expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100.0)", expr)
		try:
			value = eval(expr, {"__builtins__": {}}, {})       # noqa: S307 - numeric only
		except Exception:
			continue
		if abs(flt(value) - flt(ln["amount"])) > 0.01:
			problems.append("{0}: shows {1} = {2} but stores {3}".format(
				ln["label"], f, value, ln["amount"]))
	if problems:
		frappe.throw(_("Final settlement arithmetic does not reconcile: {0}").format(
			"; ".join(problems)))
	return True


# --------------------------------------------------------------------------- #
# Salary due for the final period
# --------------------------------------------------------------------------- #
def _salary_due(inp, flags):
	"""The salary owed for the final, possibly incomplete, payroll period.

	The statute's only proration rule is the hourly formula of artigo 237.º n.º 7, which
	artigo 240.º n.º 2 applies to *absences*. Days after a termination are not absences —
	the contract has ended — and Lei n.º 12/23 gives no formula for that case. So the
	method is explicit and its legal status is labelled honestly.
	"""
	monthly = _r2(inp["monthly_remuneration"])
	period_days = flt(inp.get("period_days"))
	days_worked = flt(inp.get("days_worked"))
	method = inp.get("salary_method") or "auto"

	if method == "auto":
		method = "full_period" if (period_days and days_worked >= period_days) \
			else (inp.get("default_partial_method") or "company_divisor")

	# A full period is a full salary. No multiplication is shown, because none happens —
	# this is the bug that made the old screen print "6,818.18 × 26 = 150,000".
	if method == "full_period":
		return _line(
			SEC_SALARY, _("Salary for the payroll period"), monthly, "law",
			formula=_("Full period worked — the whole monthly remuneration is due"),
			article="Artigo 245.º n.º 1", irt=True, inss=True, key="salary")

	unworked = max(0.0, period_days - days_worked)

	if method == "hourly_237_7":
		weekly_hours = flt(inp.get("weekly_hours"))
		dpw = flt(inp.get("working_days_per_week")) or DEFAULT_WORKING_DAYS_PER_WEEK
		if not weekly_hours:
			flags.append({
				"code": "SET-HOURS", "level": "blocking",
				"message": _("The statutory hourly formula (artigo 237.º n.º 7) needs the "
				             "normal weekly hours (Hs). Record them on the contract or "
				             "choose another calculation basis.")})
			return _line(SEC_SALARY, _("Salary for the payroll period"), 0.0, "law",
			             article="Artigo 237.º n.º 7", status="legal_input_required",
			             note=_("Normal weekly hours not recorded."),
			             irt=True, inss=True, key="salary")
		# S/H = (Sm × 12) / (52 × Hs) on the salário-base, per the article's own Sm.
		# The formula prints the whole expression instead of a pre-rounded hourly rate:
		# rounding S/H to two decimals and then multiplying by a hundred-odd hours moves
		# the answer by more than a cent, and a printed line that is off by a cent is
		# exactly the defect this rewrite exists to remove.
		sh = law.hourly_rate(inp["base"], weekly_hours)
		daily_hours = weekly_hours / dpw
		hours_unworked = unworked * daily_hours
		amount = _r2(monthly - (flt(inp["base"]) * law.MONTHS_PER_YEAR
		                        / (law.WORKING_WEEKS_PER_YEAR * weekly_hours))
		             * hours_unworked)
		formula = "{0} − {1} × 12 ÷ (52 × {2}) × {3}".format(
			_money(monthly), _money(inp["base"]), _money(weekly_hours),
			_money(hours_unworked))
		return _line(
			SEC_SALARY, _("Salary for the payroll period"), amount, "law",
			formula=formula, formula_check=formula.replace("÷", "/").replace("−", "-"),
			article="Artigo 237.º n.º 7 / Artigo 240.º n.º 2",
			note=_("S/H = ({0} × 12) ÷ (52 × {1}h) = {2} an hour. {3} unworked days × "
			       "{4}h = {5} hours deducted.").format(
				       _money(inp["base"]), _money(weekly_hours), _money(sh),
				       _money(unworked), _money(daily_hours), _money(hours_unworked)),
			irt=True, inss=True, key="salary")

	# Company divisor — a convention, and labelled as one.
	divisor = flt(inp.get("salary_divisor")) or 0.0
	if not divisor:
		flags.append({"code": "SET-DIV", "level": "blocking",
		              "message": _("No salary divisor is configured.")})
		return _line(SEC_SALARY, _("Salary for the payroll period"), 0.0, "company",
		             status="legal_input_required", irt=True, inss=True, key="salary")
	daily = _r2(monthly / divisor)
	amount = _r2(monthly / divisor * days_worked)
	return _line(
		SEC_SALARY, _("Salary for the payroll period"), amount, "company",
		formula="{0} ÷ {1} × {2}".format(_money(monthly), _money(divisor),
		                                  _money(days_worked)),
		formula_check="{0} / {1} * {2}".format(_money(monthly), _money(divisor),
		                                       _money(days_worked)),
		note=_("Company Calculation Basis: monthly remuneration ÷ {0} days × {1} days "
		       "worked. Lei n.º 12/23 fixes no such divisor — its only proration rule is "
		       "the hourly formula of artigo 237.º n.º 7.").format(
			       _money(divisor), _money(days_worked)),
		irt=True, inss=True, key="salary")


# --------------------------------------------------------------------------- #
# Annual leave — artigo 212.º priced on the artigo 213.º base
# --------------------------------------------------------------------------- #
def _leave_lines(inp, flags):
	ent = law.leave_entitlement(
		inp.get("joining_date"), inp["termination_date"],
		vested_untaken_days=flt(inp.get("vested_untaken_days")),
		leave_vested=inp.get("leave_vested"),
		fixed_term_under_one_year=bool(inp.get("fixed_term_under_one_year")),
	)

	# Artigo 213.º n.º 1: salário-base plus technical and availability supplements.
	# Artigo 213.º n.º 2: meal and transport are excluded "salvo acordo das partes".
	base = flt(inp["base"]) + flt(inp.get("technical_supplement")) \
		+ flt(inp.get("availability_supplement"))
	included = [_("Base salary"), _("Technical supplement"), _("Availability supplement")]
	if cint(inp.get("leave_base_includes_allowances")):
		base += flt(inp.get("food_allowance")) + flt(inp.get("transport_allowance"))
		included += [_("Meal allowance"), _("Transport allowance")]
		flags.append({
			"code": "LEAVE-ALLOW", "level": "info",
			"message": _("Meal and transport allowances are included in the leave "
			             "remuneration base. Artigo 213.º n.º 2 excludes them unless the "
			             "parties agreed otherwise — this is a contractual setting.")})
	leave_base = _r2(base)

	# Pricing a day of leave. The statute never does this, so the rate is labelled.
	# The formula prints the *whole* basis — base ÷ divisor × days — rather than a
	# pre-rounded daily rate × days, so the printed arithmetic and the amount paid agree
	# to the cent instead of drifting apart by the rounding of the intermediate rate.
	method = inp.get("leave_rate_method") or "company_divisor"
	if method == "hourly_237_7" and flt(inp.get("weekly_hours")):
		dpw = flt(inp.get("working_days_per_week")) or DEFAULT_WORKING_DAYS_PER_WEEK
		sh = law.hourly_rate(leave_base, inp["weekly_hours"])
		daily_hours = flt(inp["weekly_hours"]) / dpw
		rate = _r2(sh * daily_hours)
		rate_basis, rate_article = "law", "Artigo 237.º n.º 7"
		rate_note = _("Daily equivalent of the statutory hourly rate S/H = ({0} × 12) ÷ "
		              "(52 × {1}h).").format(_money(leave_base), _money(inp["weekly_hours"]))

		def _amount(days):
			return _r2(rate * days)

		def _formula(days):
			return "{0} × {1}".format(_money(rate), _money(days))
	else:
		divisor = flt(inp.get("leave_divisor")) or float(law.ANNUAL_LEAVE_WORKING_DAYS)
		rate = _r2(leave_base / divisor) if divisor else 0.0
		rate_basis, rate_article = "company", None
		rate_note = _("Company Calculation Basis: leave remuneration base ÷ {0} = {1} a "
		              "day. {2}").format(_money(divisor), _money(rate),
		                                 law.LEAVE_DAY_DIVISOR_NOTE)

		def _amount(days):
			return _r2(leave_base / divisor * days) if divisor else 0.0

		def _formula(days):
			return "{0} ÷ {1} × {2}".format(_money(leave_base), _money(divisor),
			                                 _money(days))

	def _check(f):
		return f.replace("÷", "/")

	lines = []
	if ent["vested_untaken_days"]:
		f = _formula(ent["vested_untaken_days"])
		lines.append(_line(
			SEC_LEAVE, _("Vested leave, not taken"),
			_amount(ent["vested_untaken_days"]), "law",
			formula=f, formula_check=_check(f), article="Artigo 212.º n.º 1",
			note=rate_note, irt=True, inss=True, key="leave_vested"))
		lines[-1]["rate_basis_kind"] = rate_basis
		lines[-1]["rate_article"] = rate_article
	if ent["proportional_days"]:
		f = _formula(ent["proportional_days"])
		lines.append(_line(
			SEC_LEAVE, _("Proportional leave to termination"),
			_amount(ent["proportional_days"]), "law",
			formula=f, formula_check=_check(f), article=ent["article"],
			note="{0} {1}".format(ent["explanation"], rate_note), irt=True, inss=True,
			key="leave_proportional"))
		lines[-1]["rate_basis_kind"] = rate_basis
		lines[-1]["rate_article"] = rate_article
	if not lines:
		lines.append(_line(
			SEC_LEAVE, _("Leave compensation"), 0.0, "law", article=ent["article"],
			note=ent["explanation"], irt=True, inss=True, key="leave_none"))

	if ent.get("floor_note"):
		flags.append({"code": "LGT-204-2", "level": "verify", "message": ent["floor_note"]})
	if ent.get("cap_applied"):
		flags.append({
			"code": "LGT-205", "level": "info",
			"message": _("Leave capped at {0} working days — fixed-term contract of a year "
			             "or less (artigo 205.º n.º 1).").format(law.FIXED_TERM_LEAVE_CAP_DAYS)})

	ent["remuneration_base"] = leave_base
	ent["base_components"] = included
	ent["daily_rate"] = rate
	ent["rate_basis_kind"] = rate_basis
	ent["rate_article"] = rate_article
	return lines, ent


# --------------------------------------------------------------------------- #
# Annual supplements — artigo 238.º
# --------------------------------------------------------------------------- #
def _supplement_lines(inp, flags):
	"""Vacation gratuity and Christmas bonus, each a percentage of the **salário-base**,
	proportional to *complete* months (artigo 238.º n.º 3)."""
	term = getdate(inp["termination_date"])
	joined = getdate(inp["joining_date"]) if inp.get("joining_date") else None
	year_start = getdate("{0}-01-01".format(term.year))
	window_start = joined if (joined and joined > year_start) else year_start

	months = cint(inp.get("supplement_months_override")) \
		if inp.get("supplement_months_override") not in (None, "") \
		else law.complete_months(window_start, term)
	months = max(0, min(law.MONTHS_PER_YEAR, months))

	base = flt(inp["base"])
	lines = []
	for key, label, rate_key, minimum, art in (
		("vacation_allowance", _("Vacation allowance (gratificação de férias)"),
		 "ferias_rate", law.VACATION_ALLOWANCE_MIN_PCT, "Artigo 238.º n.º 1 al. a)"),
		("christmas_bonus", _("Christmas bonus (subsídio de Natal)"),
		 "natal_rate", law.CHRISTMAS_BONUS_MIN_PCT, "Artigo 238.º n.º 1 al. b)"),
	):
		rate = flt(inp.get(rate_key))
		if rate < minimum:
			flags.append({
				"code": "LGT-238-MIN", "level": "warning",
				"message": _("{0} is set to {1}% of the base salary. Artigo 238.º n.º 1 "
				             "sets a statutory MINIMUM of {2}%.").format(
					             label, rate, minimum)})
		annual = base * rate / 100.0
		amount = _r2(annual * months / 12.0)
		lines.append(_line(
			SEC_SUPPLEMENTS, label, amount,
			"law" if rate == minimum else "company",
			formula="{0} × {1}% × {2}/12".format(_money(base), _money(rate), months),
			article=art,
			note=_("{0} complete months worked from {1} to {2}. Artigo 238.º n.º 3 makes "
			       "the supplement proportional to complete months when a full year of "
			       "service was not given.{3}").format(
				       months, window_start, term,
				       "" if rate == minimum else _(" The percentage is above the "
				                                    "statutory minimum — a contractual "
				                                    "or collective enhancement.")),
			irt=True,
			# Decreto Presidencial n.º 227/18 artigo 13.º excludes the subsídio de férias
			# from the contribution base; the Christmas bonus is not excluded. This is
			# exactly the split the payroll engine already applies to SFE and SNA.
			inss=(key == "christmas_bonus"),
			key=key))
	return lines, {"months": months, "window_start": str(window_start),
	               "window_end": str(term),
	               "overridden": inp.get("supplement_months_override") not in (None, "")}


# --------------------------------------------------------------------------- #
# Compensation / indemnity and notice
# --------------------------------------------------------------------------- #
def _compensation_line(inp, seniority, flags):
	reason = inp.get("reason_key")
	res = law.compensation(reason, inp["base"], seniority,
	                       agreed_amount=flt(inp.get("agreed_compensation")))
	spec = law.TERMINATION_REASONS.get(reason) or {}

	if res["status"] == "legal_input_required":
		flags.append({"code": "COMP-INPUT", "level": "blocking",
		              "message": res.get("message") or _("Termination reason required.")})
		return _line(SEC_COMPENSATION, _("Compensation / indemnity"), 0.0, "none",
		             article=res.get("article"), note=res.get("message"),
		             status="legal_input_required", key="compensation")

	if res["status"] == "not_applicable":
		return _line(SEC_COMPENSATION, _("Compensation / indemnity"), 0.0, "law",
		             article=res.get("article"),
		             note=_("Not applicable for the selected termination reason. {0}").format(
			             res.get("message") or ""),
		             key="compensation")

	if res.get("requires_court_ruling"):
		flags.append({
			"code": "COMP-COURT", "level": "verify",
			"message": _("{0} applies only once a court has ruled. Confirm the ruling "
			             "before paying this amount.").format(res["article"])})
	if res.get("floor_applied"):
		flags.append({
			"code": "LGT-310-3", "level": "info",
			"message": _("The three-month minimum of artigo 310.º n.º 3 raised this "
			             "indemnity above the seniority calculation.")})

	# Compensation is the one component whose tax treatment the sources dispute.
	position = inp.get("compensation_tax_position") or "verification_required"
	taxable = position == "taxable"
	note = res.get("message") or spec.get("note") or ""
	return _line(
		SEC_COMPENSATION, _("Compensation / indemnity"), res["amount"],
		"input" if res.get("is_agreed") else "law",
		formula=res.get("formula"), formula_check=res.get("formula_check"),
		article=res.get("article"),
		note=(note + " " if note else "") + _tax_position_note(position),
		status="verify" if position == "verification_required" else "ok",
		irt=taxable, inss=False, key="compensation")


def _tax_position_note(position):
	"""The company's stated position on taxing termination compensation, restated on the
	settlement itself. The sources genuinely conflict, so the position is never presented
	as settled law — see ``law.settlement_reference()["open_questions"]``."""
	if position == "exempt_within_lgt_limits":
		return _("Company tax position: not subject to IRT within the Lei Geral do "
		         "Trabalho limits (Código do IRT, artigo 2.º n.º 1 al. g)). ") + MARKER
	if position == "taxable":
		return _("Company tax position: fully subject to IRT, following the commentary "
		         "on Lei n.º 28/20. ") + MARKER
	return _("IRT incidence on termination compensation has NOT been settled: the "
	         "consolidated Código do IRT excludes it within the statutory limits, while "
	         "commentary on Lei n.º 28/20 states it became fully taxable. No IRT has "
	         "been applied to this line. ") + MARKER


def _notice_lines(inp, flags):
	"""Notice has money consequences in exactly two places in Lei n.º 12/23, and they run
	in opposite directions. Neither is a generic deduction."""
	lines = []
	reason = inp.get("reason_key")
	spec = law.TERMINATION_REASONS.get(reason) or {}
	required = cint(inp.get("notice_required_days"))
	given = cint(inp.get("notice_given_days"))
	daily_base = flt(inp["base"]) / 30.0     # notice is expressed in calendar days

	# --- artigo 305.º n.º 2 — the WORKER owes the employer ---------------------- #
	if spec.get("employee_owes_notice"):
		required = required or law.RESIGNATION_NOTICE_DAYS
		if inp.get("notice_given_days") in (None, ""):
			flags.append({
				"code": "LGT-305-2", "level": "blocking",
				"message": _("This termination is a resignation without the required "
				             "notice. Artigo 305.º n.º 2 obliges the worker to compensate "
				             "the employer for the notice not given, but the days actually "
				             "given have not been recorded, so the amount cannot be "
				             "calculated. Record them, or change the reason.")})
			lines.append(_line(SEC_NOTICE, _("Notice not given (owed by the employee)"),
			                   0.0, "law", article="Artigo 305.º n.º 2", sign=-1,
			                   status="legal_input_required", key="notice_employee",
			                   note=_("Days of notice actually given not recorded.")))
		else:
			missing = max(0, required - given)
			amount = _r2(daily_base * missing)
			if amount:
				lines.append(_line(
					SEC_NOTICE, _("Notice not given (owed by the employee)"), amount,
					"law", sign=-1, article="Artigo 305.º n.º 2",
					formula="{0} × {1}".format(_money(daily_base), missing),
					note=_("{0} of the {1} days required were given. The salary for the "
					       "{2} missing days is owed to the employer.").format(
						       given, required, missing),
					key="notice_employee"))

	# --- artigo 17.º n.º 4 — the EMPLOYER owes the worker ----------------------- #
	if reason == "fixed_term_expiry" and cint(inp.get("employer_missed_renewal_notice")):
		amount = _r2(daily_base * law.NON_RENEWAL_MISSED_NOTICE_DAYS)
		lines.append(_line(
			SEC_NOTICE, _("Notice of non-renewal not given (owed to the employee)"),
			amount, "law", article="Artigo 17.º n.º 4",
			formula="{0} × {1}".format(_money(daily_base),
			                                law.NON_RENEWAL_MISSED_NOTICE_DAYS),
			note=_("The employer did not give the 30 days' notice of non-renewal."),
			key="notice_employer"))
	return lines


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def compute(inputs):
	"""Compute a whole final settlement. Pure: every figure comes from ``inputs``.

	:returns: dict with ``lines`` (each reconciling), the section subtotals, the
	          statutory deductions, the net, a full ``trace`` and the ``flags`` that must
	          reach the screen.
	"""
	inp = dict(inputs or {})
	flags = []

	inp["base"] = flt(inp.get("base"))
	inp["monthly_remuneration"] = _r2(
		inp["base"] + flt(inp.get("technical_supplement"))
		+ flt(inp.get("availability_supplement")) + flt(inp.get("food_allowance"))
		+ flt(inp.get("transport_allowance")))

	term = getdate(inp["termination_date"])
	joined = getdate(inp["joining_date"]) if inp.get("joining_date") else None

	# --- the several legally distinct periods (never one "months worked") ------ #
	seniority = cint(inp.get("seniority_years_override")) \
		if inp.get("seniority_years_override") not in (None, "") \
		else law.seniority_years(joined, term)

	# --- earnings -------------------------------------------------------------- #
	lines = [_salary_due(inp, flags)]
	leave_lines, leave_trace = _leave_lines(inp, flags)
	lines += leave_lines
	supp_lines, supp_trace = _supplement_lines(inp, flags)
	lines += supp_lines
	lines.append(_compensation_line(inp, seniority, flags))
	lines += _notice_lines(inp, flags)

	for extra in (inp.get("other_earnings") or []):
		if flt(extra.get("amount")):
			lines.append(_line(
				SEC_OTHER, extra.get("label") or _("Other earning"), flt(extra["amount"]),
				"input", note=extra.get("note"),
				irt=bool(extra.get("irt_taxable", True)),
				inss=bool(extra.get("inss_base", True)),
				key=extra.get("key") or "other_earning"))

	_check_reconciles(lines)

	# --- statutory bases ------------------------------------------------------- #
	payable = [ln for ln in lines if ln["sign"] > 0]
	gross = _r2(sum(ln["amount"] for ln in payable))
	inss_base = _r2(sum(ln["amount"] for ln in payable if ln["inss_base"]))
	irt_gross = _r2(sum(ln["amount"] for ln in payable if ln["irt_taxable"]))

	rates = get_statutory_rates(company=inp.get("company"), on_date=term)
	ss_rate = require_rate(rates, "ss_employee_rate", _("Employee Social Security rate"))
	ss_employer_rate = flt(rates.get("ss_employer_rate"))
	inss = _r2(inss_base * ss_rate / 100.0)
	inss_employer = _r2(inss_base * ss_employer_rate / 100.0)

	# Same order as the salary slip: social security comes off before IRT.
	irt_base = _r2(max(0.0, irt_gross - inss))
	irt_trace = resolve_irt(irt_base, company=inp.get("company"), on_date=term)
	irt = flt(irt_trace.amount)

	statutory = [
		_line(SEC_OTHER, _("Social security (employee)"), inss, "law", sign=-1,
		      article="Decreto Presidencial n.º 227/18, artigo 13.º",
		      formula="{0} × {1}%".format(_money(inss_base), _money(ss_rate)),
		      note=_("Contribution base excludes the vacation gratuity (artigo 13.º) and "
		             "any compensation for termination. ") + MARKER,
		      key="inss"),
		_line(SEC_OTHER, _("IRT"), irt, "law", sign=-1,
		      article="Código do IRT (Lei n.º 18/14, alterada pela Lei n.º 28/20)",
		      formula=None,
		      note=_("Taxable base {0} less social security {1} = {2}, taxed on the IRT "
		             "table effective on {3}.").format(
			             _money(irt_gross), _money(inss), _money(irt_base), term),
		      key="irt"),
	]
	flags.append({
		"code": "IRT-LUMP", "level": "verify",
		"message": _("The IRT table is a monthly table. It has been applied once to the "
		             "whole taxable settlement. Whether a termination settlement is taxed "
		             "as a single month or spread has not been verified. ") + MARKER})

	# --- other deductions ------------------------------------------------------ #
	notice_owed = _r2(sum(ln["amount"] for ln in lines if ln["sign"] < 0))
	other_deductions = list(statutory)

	after_statutory = _r2(gross - inss - irt - notice_owed)

	advance_outstanding = _r2(inp.get("advance_outstanding"))
	advance_recovered = 0.0
	if advance_outstanding and cint(inp.get("recover_advance", 1)):
		advance_recovered = _r2(max(0.0, min(advance_outstanding, max(0.0, after_statutory))))
		if advance_recovered:
			other_deductions.append(_line(
				SEC_OTHER, _("Salary advance recovered"), advance_recovered, "input",
				sign=-1, key="advance",
				note=_("Outstanding {0}; recovery is capped at what the settlement can "
				       "carry so the net can never go negative.").format(
					       _money(advance_outstanding))))
	advance_deferred = _r2(advance_outstanding - advance_recovered)
	if advance_deferred:
		flags.append({
			"code": "ADV-REMAIN", "level": "warning",
			"message": _("{0} of the salary advance could not be recovered from this "
			             "settlement and remains outstanding. It is not written off.").format(
				             _money(advance_deferred))})

	for extra in (inp.get("other_deductions") or []):
		if flt(extra.get("amount")):
			other_deductions.append(_line(
				SEC_OTHER, extra.get("label") or _("Other deduction"),
				flt(extra["amount"]), "input", sign=-1, note=extra.get("note"),
				key=extra.get("key") or "other_deduction"))

	deducted = _r2(sum(ln["amount"] for ln in other_deductions) + notice_owed)
	net = _r2(gross - deducted)

	# --- negative-net protection ---------------------------------------------- #
	shortfall = 0.0
	if net < 0:
		shortfall = _r2(-net)
		net = 0.0
		flags.append({
			"code": "NEG-NET", "level": "warning",
			"message": _("Authorised deductions exceed the amount payable by {0}. The net "
			             "settlement is shown as zero and the {0} remains outstanding — it "
			             "has not been silently over-deducted.").format(_money(shortfall))})

	blocking = [f for f in flags if f["level"] == "blocking"]
	return {
		"calc_version": CALC_VERSION,
		"lines": lines + other_deductions,
		"earnings": payable,
		"deductions": other_deductions + [ln for ln in lines if ln["sign"] < 0],
		"monthly_remuneration": inp["monthly_remuneration"],
		"gross": gross,
		"inss_base": inss_base,
		"inss": inss,
		"inss_employer": inss_employer,
		"irt_gross": irt_gross,
		"irt_base": irt_base,
		"irt": irt,
		"statutory_deductions": _r2(inss + irt),
		"other_deductions": _r2(deducted - inss - irt),
		"total_deductions": deducted,
		"net": net,
		"shortfall": shortfall,
		"advance_outstanding": advance_outstanding,
		"advance_recovered": advance_recovered,
		"advance_deferred": advance_deferred,
		"seniority_years": seniority,
		"leave": leave_trace,
		"supplements": supp_trace,
		"flags": flags,
		"is_complete": not blocking,
		"blocking": blocking,
		"trace": _trace(inp, seniority, leave_trace, supp_trace, rates, irt_trace,
		                ss_rate, ss_employer_rate),
	}


def _trace(inp, seniority, leave_trace, supp_trace, rates, irt_trace, ss_rate,
           ss_employer_rate):
	"""Everything needed to re-explain this settlement years later, without reading any
	configuration that may since have changed (§31)."""
	return {
		"calc_version": CALC_VERSION,
		"law": law.LAW,
		"law_source": "Diário da República, I Série n.º 245, de 27 de Dezembro de 2023",
		"computed_for": {
			"employee": inp.get("employee"),
			"company": inp.get("company"),
			"joining_date": str(inp.get("joining_date") or ""),
			"termination_date": str(inp.get("termination_date")),
			"reason_key": inp.get("reason_key"),
			"reason_article": (law.TERMINATION_REASONS.get(inp.get("reason_key")) or {}).get("article"),
			"contract": inp.get("contract"),
			"fixed_term_under_one_year": bool(inp.get("fixed_term_under_one_year")),
		},
		"remuneration": {
			"base": flt(inp.get("base")),
			"technical_supplement": flt(inp.get("technical_supplement")),
			"availability_supplement": flt(inp.get("availability_supplement")),
			"food_allowance": flt(inp.get("food_allowance")),
			"transport_allowance": flt(inp.get("transport_allowance")),
			"monthly_remuneration": inp.get("monthly_remuneration"),
			"salary_profile": inp.get("salary_profile"),
		},
		"salary_period": {
			"start": str(inp.get("period_start") or ""),
			"end": str(inp.get("period_end") or ""),
			"period_days": flt(inp.get("period_days")),
			"days_worked": flt(inp.get("days_worked")),
			"method": inp.get("salary_method"),
			"divisor": flt(inp.get("salary_divisor")),
			"weekly_hours": flt(inp.get("weekly_hours")),
			"working_days_per_week": flt(inp.get("working_days_per_week")),
		},
		"leave": leave_trace,
		"supplements": dict(supp_trace, ferias_rate=flt(inp.get("ferias_rate")),
		                    natal_rate=flt(inp.get("natal_rate"))),
		"seniority": {"years": seniority, "rule": "Artigo 311.º",
		              "overridden": inp.get("seniority_years_override") not in (None, "")},
		"statutory": {
			"statutory_rate_record": rates.get("statutory_rate"),
			"ss_employee_rate": ss_rate,
			"ss_employer_rate": ss_employer_rate,
			"irt_table": irt_trace.table,
			"irt_effective_from": str(irt_trace.effective_from or ""),
			"irt_bracket_from": irt_trace.bracket_from,
			"irt_bracket_to": irt_trace.bracket_to,
			"irt_excess_over": irt_trace.excess_over,
			"irt_rate": irt_trace.rate,
			"irt_parcela_fixa": irt_trace.parcela_fixa,
			"compensation_tax_position": inp.get("compensation_tax_position"),
		},
		"advance": {
			"outstanding": flt(inp.get("advance_outstanding")),
			"recover": cint(inp.get("recover_advance", 1)),
		},
		"overrides": {
			"supplement_months": inp.get("supplement_months_override"),
			"seniority_years": inp.get("seniority_years_override"),
			"reason": inp.get("override_reason"),
			"by": inp.get("override_by"),
			"at": str(inp.get("override_at") or ""),
		},
	}


def payment_deadline(termination_date, reason_key=None):
	"""**Artigo 245.º n.º 4** — three days. Not artigo 240.º, which is about deducting
	salary for absence; that misreading came from Lei n.º 7/15."""
	from frappe.utils import add_days
	spec = law.TERMINATION_REASONS.get(reason_key) or {}
	if spec.get("deadline_article"):
		return {"due_date": None, "article": spec["deadline_article"],
		        "rule": _("Paid by the end of the collective dismissal process.")}
	return {"due_date": str(add_days(getdate(termination_date), law.SETTLEMENT_PAYMENT_DAYS)),
	        "article": "Artigo 245.º n.º 4",
	        "rule": _("Salary, indemnity and every other amount owed are paid within "
	                  "three days of cessation.")}
