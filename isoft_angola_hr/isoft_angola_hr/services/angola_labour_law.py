# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Angolan labour-law reference limits — cited, and used only to WARN.

WHY THIS MODULE ONLY WARNS
--------------------------
Phase 3 left four questions open (§96): maximum fixed-term duration, maximum probation
length, notice periods and severance calculation. They are now researched and cited
below. They are still not *enforced*, and that is deliberate:

* The lawful maximum for a fixed-term contract depends on the **legal ground** for using
  one (artigo 16.º lists four grounds with four different ceilings), and this app does not
  record the ground. Blocking a 36-month contract because the app assumed the 12-month
  ground would stop lawful work.
* The probation ceiling depends on whether the role is a *função de direcção*, which is a
  legal characterisation, not the ERPNext Designation field.
* Severance depends on facts this app cannot know — the reason for termination, whether a
  court has ruled, whether reinstatement was offered.

So each limit produces a **warning that cites its article**, HR decides, and the decision
is recorded on the contract. §97 is explicit that severance and notice must not be
generated automatically until legal verification is complete, and "verified from two
readings of the statute" is not the same as "verified by the customer's lawyer".

SOURCES
-------
**Lei n.º 12/23, de 27 de Dezembro — Lei Geral do Trabalho**, which revoked Lei n.º 7/15
de 15 de Junho.

The contract limits at the top of this module (probation, fixed term, notice) were
originally read from secondary reproductions of the statute. The **final-settlement**
section further down was written against the *Diário da República, I Série n.º 245, de 27
de Dezembro de 2023* itself — the machine-readable copy published by the ILO's NATLEX
database — and every article quoted there is quoted from that text.

.. warning:: LEGAL VERIFICATION REQUIRED — a statute read correctly is still not legal
   advice. Where the law is silent (notably the monetary daily divisor for leave, and the
   IRT/INSS incidence of termination compensation) this module says so explicitly rather
   than inventing a rule, and the choice is exposed as company configuration. Nothing here
   should be relied on for a real dismissal or severance payment before the customer's own
   legal adviser has confirmed it.
"""

from frappe import _
from frappe.utils import add_days, add_months, cint, flt, getdate

#: The governing statute, restated wherever a limit is shown.
LAW = "Lei n.º 12/23, de 27 de Dezembro (Lei Geral do Trabalho)"
REVIEW_MARKER = "LEGAL VERIFICATION REQUIRED"

# --------------------------------------------------------------------------- #
# Probation — artigo 18.º
# --------------------------------------------------------------------------- #
#: Artigo 18.º n.º 1: the probation period is the first 60 days of an open-ended contract.
PROBATION_DEFAULT_DAYS = 60
#: Artigo 18.º n.º 2: extendable by written agreement to 120 days, and to 180 days for
#: workers performing management functions (funções de direcção).
PROBATION_MAX_DAYS = 120
PROBATION_MAX_DAYS_MANAGEMENT = 180
#: Fixed-term contracts: a shorter ceiling.
PROBATION_MAX_DAYS_FIXED_TERM = 30

# --------------------------------------------------------------------------- #
# Fixed-term contracts — artigos 16.º and 17.º
# --------------------------------------------------------------------------- #
#: Artigo 16.º n.º 1 — the ceiling depends on the legal ground for the fixed term.
FIXED_TERM_LIMITS_MONTHS = {
	"seasonal_or_urgent": 6,
	"temporary_increase": 12,
	"substitution_or_construction": 36,
	"new_activity": 60,
}
#: The highest ceiling in the article. Beyond this no lawful ground exists, so a contract
#: longer than 60 months is flagged whatever its ground.
FIXED_TERM_ABSOLUTE_MAX_MONTHS = 60
#: Artigo 17.º n.º 5 — exceeding the applicable maximum converts the contract to an
#: open-ended one by operation of law.
CONVERTS_TO_OPEN_ENDED_IF_EXCEEDED = True
#: Artigo 17.º n.º 3 — 30 days' notice to decline renewal.
NON_RENEWAL_NOTICE_DAYS = 30

# --------------------------------------------------------------------------- #
# Notice — minimum 30 days
# --------------------------------------------------------------------------- #
MINIMUM_NOTICE_DAYS = 30

# --------------------------------------------------------------------------- #
# Severance — DOCUMENTED, NOT CALCULATED (§97)
# --------------------------------------------------------------------------- #
SEVERANCE_METHOD = {
	"marker": REVIEW_MARKER,
	"law": LAW,
	"objective_or_collective_dismissal": (
		"Base salary at termination × years of seniority, capped at 5 years; for each year "
		"beyond 5, add 50% of base salary. A fraction of a year of 3 months or more counts "
		"as a full year."),
	"wrongful_dismissal_without_reinstatement": (
		"Base salary at dismissal × years of seniority, with a minimum of 3 months' base "
		"salary."),
	"lawful_disciplinary_dismissal": "No severance is due.",
	"notice": "Minimum 30 days. Failure to give notice obliges payment in lieu.",
	"why_not_automated": (
		"The amount depends on the reason for termination, on seniority rounding and, in "
		"the wrongful-dismissal case, on a court ruling. The Final Settlement now records "
		"the termination reason explicitly and routes it to the article that governs it "
		"(see TERMINATION_REASONS and compensation() below), so the figure IS calculated "
		"once that reason is known. Where the reason is not recorded, or where it depends "
		"on a court ruling this app cannot see, the settlement returns "
		"'LEGAL INPUT REQUIRED' rather than a silent zero."),
}


# =========================================================================== #
# FINAL SETTLEMENT — Lei n.º 12/23 (Diário da República, I Série n.º 245)
# =========================================================================== #
# Everything below is quoted from, or derived directly from, the Diário da República
# text. Each constant names its article. Where the statute does NOT settle a question,
# the constant says so and the value becomes company configuration, never "law".

#: Artigo 204.º n.º 1 — "O período de férias é de 22 dias úteis em cada ano".
ANNUAL_LEAVE_WORKING_DAYS = 22
#: Artigo 204.º n.º 2 / Artigo 212.º n.os 2 e 3 — leave accrues at two working days for
#: each complete month of service.
LEAVE_DAYS_PER_COMPLETE_MONTH = 2
#: Artigo 204.º n.º 2 — in the year of admission the leave PERIOD has a floor of six
#: working days. The article sets a floor for the period actually taken; it does not say
#: the floor applies to the money paid under artigo 212.º n.º 3 on an early termination.
ADMISSION_YEAR_MIN_LEAVE_DAYS = 6
APPLY_ADMISSION_YEAR_FLOOR_TO_SETTLEMENT = None  # None => LEGAL VERIFICATION REQUIRED
#: Artigo 205.º n.º 1 — fixed-term contract of one year or less: two working days per
#: complete month, capped at 22 working days.
FIXED_TERM_LEAVE_CAP_DAYS = 22

#: Artigo 238.º n.º 1 — annual supplements, each a MINIMUM of 50% of the salário-base.
#: Artigo 238.º n.º 2 lets a collective or individual contract raise them.
VACATION_ALLOWANCE_MIN_PCT = 50.0
CHRISTMAS_BONUS_MIN_PCT = 50.0

#: Artigo 237.º n.º 7 — the ONLY salary-proration formula in the statute:
#:     S/H = (Sm × 12) / (52 × Hs)
#: Sm = monthly salário-base, 52 = working weeks in the year, Hs = normal weekly hours.
#: Artigo 240.º n.º 2 directs that this same formula is used to compute the amount to
#: deduct for absence. It yields an HOURLY figure; the statute gives no monthly-to-daily
#: divisor at all.
WORKING_WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12
#: Artigo 245.º n.º 4 — "Em caso de cessação do Contrato de Trabalho, o salário,
#: indemnização e demais valores devidos ao trabalhador seja a que título for, são pagos
#: dentro dos três dias subsequentes à cessação."  (This is artigo 245.º, NOT artigo
#: 240.º, which is about salary reduction for absence.)
SETTLEMENT_PAYMENT_DAYS = 3
#: Artigo 296.º — in a collective dismissal the credits and the compensation are instead
#: paid "até ao término do processo de despedimento".
COLLECTIVE_DISMISSAL_DEADLINE_ARTICLE = "Artigo 296.º"

#: Artigo 311.º — "contam-se como um ano de antiguidade as fracções iguais ou superiores
#: a três meses."
SENIORITY_ROUNDUP_MONTHS = 3

#: Artigo 308.º — the objective-dismissal compensation changes rate after five years.
COMPENSATION_308_FULL_YEARS = 5
COMPENSATION_308_EXCESS_RATE = 0.5
#: Artigo 307.º — insolvency / extinction of the employer.
COMPENSATION_307_RATE = 0.5
#: Artigo 309.º — indemnity for non-reinstatement.
INDEMNITY_309_RATE = 1.0  # of 50% of base — see compensation() for the exact expression
#: Artigo 310.º n.º 3 — the individual-dismissal indemnity has a floor of three months.
INDEMNITY_310_MIN_MONTHS = 3

#: Artigo 305.º n.º 1 / n.º 2 — resignation without just cause needs 30 days' written
#: notice; missing notice obliges the WORKER to compensate the EMPLOYER with the salary
#: for the notice not given. Artigo 306.º n.º 5 applies the same rule to abandonment.
RESIGNATION_NOTICE_DAYS = 30
#: Artigo 17.º n.º 4 — if the EMPLOYER fails to give the 30 days' notice of non-renewal
#: of a fixed-term contract, it owes the worker 30 days' compensation.
NON_RENEWAL_MISSED_NOTICE_DAYS = 30

#: Artigo 213.º n.º 1 — "A remuneração do trabalhador durante o período de férias é igual
#: ao salário-base mais os complementos técnicos e de disponibilidade."
#: Artigo 213.º n.º 2 — meal and transport subsidies are NOT paid during leave "salvo
#: acordo das partes".
LEAVE_PAY_INCLUDES = ("base", "technical_supplement", "availability_supplement")
LEAVE_PAY_EXCLUDES_BY_DEFAULT = ("food_allowance", "transport_allowance")

#: The statute fixes the leave PERIOD at 22 working days (artigo 204.º n.º 1). It never
#: says "divide the monthly salary by 22" — or by any other number — to price one day of
#: leave. Any divisor used here is a company convention and must be labelled as one.
LEAVE_DAY_DIVISOR_IS_STATUTORY = False
LEAVE_DAY_DIVISOR_NOTE = (
	"Lei n.º 12/23 sets the leave PERIOD at 22 working days (artigo 204.º n.º 1) and "
	"gives an hourly formula for salary deductions (artigo 237.º n.º 7). It fixes no "
	"monthly-to-daily divisor for pricing a day of leave. The divisor used here is a "
	"COMPANY CALCULATION BASIS, not a statutory rate.")


# --------------------------------------------------------------------------- #
# Termination reasons — the legal driver of the whole settlement
# --------------------------------------------------------------------------- #
#: ``compensation`` is the key consumed by :func:`compensation`; ``None`` means the law
#: provides no compensation for this reason, ``"unknown"`` means the app cannot decide.
TERMINATION_REASONS = {
	"resignation_with_notice": {
		"label": "Resignation with notice (denúncia)",
		"article": "Artigo 305.º n.º 1",
		"compensation": None,
		"note": "No compensation is due. 30 days' written notice is required.",
	},
	"resignation_without_notice": {
		"label": "Resignation without the required notice",
		"article": "Artigo 305.º n.º 2",
		"compensation": None,
		"employee_owes_notice": True,
		"note": "No compensation is due to the worker. Missing notice obliges the WORKER "
		        "to compensate the employer with the salary for the notice not given.",
	},
	"abandonment": {
		"label": "Abandonment of work",
		"article": "Artigo 306.º n.º 5",
		"compensation": None,
		"employee_owes_notice": True,
		"note": "Counts as denúncia without notice; artigo 305.º n.º 2 applies.",
	},
	"mutual_agreement": {
		"label": "Mutual agreement (revogação)",
		"article": "Artigo 280.º",
		"compensation": "agreed",
		"note": "The law fixes no amount. Any compensation is whatever the written "
		        "agreement states, and artigo 280.º n.º 4 presumes it does NOT absorb the "
		        "credits already owed unless the agreement says so.",
	},
	"fixed_term_expiry": {
		"label": "Expiry of a fixed-term contract",
		"article": "Artigo 17.º n.º 3",
		"compensation": None,
		"note": "Ordinary expiry carries no severance under Lei n.º 12/23. If the party "
		        "declining renewal gave no 30 days' notice, artigo 17.º n.º 4 applies.",
	},
	"objective_dismissal": {
		"label": "Individual dismissal for objective just cause",
		"article": "Artigo 289.º → Artigo 308.º",
		"compensation": "308",
	},
	"collective_dismissal": {
		"label": "Collective dismissal",
		"article": "Artigo 295.º → Artigo 308.º",
		"compensation": "308",
		"deadline_article": COLLECTIVE_DISMISSAL_DEADLINE_ARTICLE,
	},
	"extinction_after_suspension": {
		"label": "Extinction after suspension for objective reasons",
		"article": "Artigo 271.º al. b) → Artigo 308.º",
		"compensation": "308",
	},
	"employer_insolvency_extinction": {
		"label": "Employer insolvency or extinction",
		"article": "Artigo 278.º → Artigo 307.º",
		"compensation": "307",
	},
	"disciplinary_dismissal": {
		"label": "Disciplinary dismissal with just cause",
		"article": "Artigo 281.º",
		"compensation": None,
		"note": "A lawful disciplinary dismissal carries no compensation.",
	},
	"indirect_dismissal": {
		"label": "Indirect dismissal (employer fault)",
		"article": "Artigo 303.º n.º 5 → Artigo 310.º",
		"compensation": "310",
	},
	"unlawful_dismissal_no_reinstatement": {
		"label": "Unlawful individual dismissal, no reinstatement",
		"article": "Artigo 300.º n.º 3 → Artigo 310.º",
		"compensation": "310",
		"requires_court_ruling": True,
	},
	"non_reinstatement": {
		"label": "Non-reinstatement indemnity",
		"article": "Artigo 309.º",
		"compensation": "309",
		"requires_court_ruling": True,
	},
	"death_incapacity_retirement": {
		"label": "Death, permanent incapacity or retirement of the worker",
		"article": "Artigo 277.º al. a) b) c)",
		"compensation": None,
		"note": "Artigo 278.º grants compensation for the caducidade grounds in al. e) "
		        "and, conditionally, d) and g) — not for these.",
	},
	"other": {
		"label": "Other / not yet determined",
		"article": None,
		"compensation": "unknown",
		"note": "The reason has not been mapped to an article, so no compensation can be "
		        "calculated. This is NOT the same as zero.",
	},
}

#: Order the reasons are offered in, HR-first.
TERMINATION_REASON_ORDER = (
	"resignation_with_notice", "resignation_without_notice", "abandonment",
	"mutual_agreement", "fixed_term_expiry", "objective_dismissal",
	"collective_dismissal", "extinction_after_suspension",
	"employer_insolvency_extinction", "disciplinary_dismissal", "indirect_dismissal",
	"unlawful_dismissal_no_reinstatement", "non_reinstatement",
	"death_incapacity_retirement", "other",
)

#: Legacy free-text reasons that the pre-audit Final Settlement offered, mapped onto the
#: catalogue above so old records still render. "Other" stays unmapped on purpose.
LEGACY_REASON_MAP = {
	"Resignation": "resignation_with_notice",
	"Dismissal for Just Cause": "disciplinary_dismissal",
	"Mutual Agreement": "mutual_agreement",
	"End of Contract": "fixed_term_expiry",
	"Redundancy": "objective_dismissal",
	"Other": "other",
}


def complete_months(start, end):
	"""Complete months of service between two INCLUSIVE dates — artigo 204.º n.º 4.

	A month is complete when the whole of it has elapsed: one complete month from the
	1st of January ends on the 31st of January, not on the 1st of February. So
	1 Jan → 21 Aug is **seven** complete months, not eight.

	This is deliberately NOT :func:`months_between`, which answers a different question
	(how long does this contract run) and counts a started month as a month.
	"""
	if not (start and end):
		return 0
	start, end = getdate(start), getdate(end)
	if end < start:
		return 0
	# k months are complete when the day before the k-th monthly anniversary of `start`
	# has been reached. Counting anniversaries rather than doing day arithmetic keeps
	# month lengths and leap years out of it.
	months = (end.year - start.year) * 12 + (end.month - start.month) + 1
	while months > 0 and getdate(add_days(add_months(start, months), -1)) > end:
		months -= 1
	return months


def seniority_years(joining_date, termination_date):
	"""Years of seniority under **artigo 311.º**: whole years, plus one more when the
	remaining fraction is three months or more.

	Never ``termination_year - joining_year``: that would give an employee hired on
	31 December 2025 and leaving on 1 January 2026 a full year of seniority.
	"""
	months = complete_months(joining_date, termination_date)
	whole, remainder = divmod(months, MONTHS_PER_YEAR)
	return whole + (1 if remainder >= SENIORITY_ROUNDUP_MONTHS else 0)


def hourly_rate(monthly_base, weekly_hours):
	"""**Artigo 237.º n.º 7** — S/H = (Sm × 12) / (52 × Hs).

	The statute's only salary-proration rule, and the one artigo 240.º n.º 2 points at
	for computing a deduction. Returns 0 when the weekly hours are unknown, because
	guessing them would be inventing the divisor this module refuses to invent.
	"""
	monthly_base, weekly_hours = flt(monthly_base), flt(weekly_hours)
	if not weekly_hours:
		return 0.0
	return flt((monthly_base * MONTHS_PER_YEAR) / (WORKING_WEEKS_PER_YEAR * weekly_hours), 6)


def leave_entitlement(joining_date, termination_date, vested_untaken_days=0.0,
                      leave_vested=None, fixed_term_under_one_year=False):
	"""Leave days payable on termination — **artigo 212.º**, with artigo 205.º for short
	fixed-term contracts.

	Three mutually exclusive situations, exactly as the article draws them:

	``212.º n.º 1 + n.º 2`` — the normal case. The worker is paid (1) the leave already
	vested and not taken, **plus** (2) two working days for each complete month from
	1 January to the termination date.

	``212.º n.º 3`` — termination *before the leave right has vested*. Paragraphs 1 and 2
	expressly do NOT apply; instead the worker is paid two working days for each complete
	month worked **from the date of admission**. The right vests on 1 January (artigo
	201.º n.º 2), so this is the worker who leaves in the same civil year they joined.

	``205.º n.º 1`` — a fixed-term contract of a year or less accrues on the same two-days
	basis but is capped at 22 working days.

	:param leave_vested: force the branch. ``None`` derives it from the dates.
	:returns: dict with the days, the branch taken and the article to display.
	"""
	term = getdate(termination_date)
	joined = getdate(joining_date) if joining_date else None
	if leave_vested is None:
		# Vested on 1 January (artigo 201.º n.º 2) — so the right exists only if the
		# worker was already employed before the 1st of January of the termination year.
		leave_vested = bool(joined and joined.year < term.year)

	year_start = getdate("{0}-01-01".format(term.year))
	out = {"vested_untaken_days": 0.0, "proportional_days": 0.0, "total_days": 0.0,
	       "cap_applied": False, "floor_note": None}

	if not leave_vested:
		# --- artigo 212.º n.º 3 -------------------------------------------------- #
		months = complete_months(joined, term) if joined else 0
		days = float(months * LEAVE_DAYS_PER_COMPLETE_MONTH)
		out.update({
			"branch": "212.3",
			"article": "Artigo 212.º n.º 3",
			"proportional_months": months,
			"proportional_from": str(joined) if joined else None,
			"proportional_days": days,
			"explanation": "Terminated before the leave right vested: two working days for "
			               "each complete month worked since admission. Artigo 212.º n.os 1 "
			               "and 2 do not apply.",
		})
		if ADMISSION_YEAR_MIN_LEAVE_DAYS and days and days < ADMISSION_YEAR_MIN_LEAVE_DAYS:
			out["floor_note"] = (
				"Artigo 204.º n.º 2 sets a floor of {0} working days for the leave PERIOD "
				"in the year of admission. It does not say the floor applies to the money "
				"paid under artigo 212.º n.º 3, so it has NOT been applied. "
				"{1}.".format(ADMISSION_YEAR_MIN_LEAVE_DAYS, REVIEW_MARKER))
	else:
		# --- artigo 212.º n.os 1 e 2 --------------------------------------------- #
		months = complete_months(year_start, term)
		days = float(months * LEAVE_DAYS_PER_COMPLETE_MONTH)
		out.update({
			"branch": "212.1+2",
			"article": "Artigo 212.º n.os 1 e 2",
			"vested_untaken_days": flt(vested_untaken_days, 2),
			"proportional_months": months,
			"proportional_from": str(year_start),
			"proportional_days": days,
			"explanation": "Leave already vested and not taken, plus two working days for "
			               "each complete month from 1 January to the termination date.",
		})

	if fixed_term_under_one_year:
		total = out["vested_untaken_days"] + out["proportional_days"]
		if total > FIXED_TERM_LEAVE_CAP_DAYS:
			out["cap_applied"] = True
			out["article"] = "Artigo 205.º n.º 1"
			out["proportional_days"] = max(0.0, FIXED_TERM_LEAVE_CAP_DAYS - out["vested_untaken_days"])
			out["explanation"] = (
				"Fixed-term contract of a year or less: two working days per complete "
				"month, capped at {0} working days.".format(FIXED_TERM_LEAVE_CAP_DAYS))

	out["total_days"] = flt(out["vested_untaken_days"] + out["proportional_days"], 2)
	return out


def _n(v):
	"""Format an operand for a human-readable formula string."""
	return "{0:,.2f}".format(flt(v))


def compensation(reason, base_salary, years, agreed_amount=0.0):
	"""Termination compensation / indemnity for one reason — artigos 307.º to 311.º.

	There is **no universal severance** in Lei n.º 12/23. The reason decides both whether
	anything is owed and which formula applies, so an unrecorded reason returns
	``status="legal_input_required"`` and never a silent zero.

	:param years: seniority already rounded under artigo 311.º
    :returns: dict with amount, article, formula (a string that evaluates to the amount)
	          and status in ``applicable`` / ``not_applicable`` / ``legal_input_required``.
	"""
	base, years = flt(base_salary), cint(years)
	spec = TERMINATION_REASONS.get(reason)
	if not spec:
		return {"amount": 0.0, "article": None, "formula": None,
		        "status": "legal_input_required",
		        "message": "No termination reason has been recorded, so no compensation "
		                   "can be determined. This is not the same as zero."}
	rule = spec.get("compensation")

	if rule is None:
		return {"amount": 0.0, "article": spec.get("article"), "formula": None,
		        "status": "not_applicable",
		        "message": spec.get("note") or "No compensation is due for this reason."}

	if rule == "unknown":
		return {"amount": 0.0, "article": None, "formula": None,
		        "status": "legal_input_required",
		        "message": spec.get("note") or "LEGAL INPUT REQUIRED."}

	if rule == "agreed":
		amount = flt(agreed_amount, 2)
		return {"amount": amount, "article": spec.get("article"),
		        "formula": "As agreed in writing",
		        "status": "applicable" if amount else "legal_input_required",
		        "message": spec.get("note"),
		        "is_agreed": True}

	if rule == "307":
		# "multiplicando 50% do valor do salário-base pelo número de anos de serviço"
		amount = flt(COMPENSATION_307_RATE * base * years, 2)
		return {"amount": amount, "article": "Artigo 307.º", "status": "applicable",
		        "formula": "50% × {base} × {y} years".format(base=_n(base), y=years),
		        "formula_check": "50% * {base} * {y}".format(base=base, y=years)}

	if rule == "308":
		# "o salário-base ... multiplicado pelo número de anos de antiguidade, com o
		#  limite de cinco, sendo o valor assim obtido acrescido de 50% do salário-base
		#  multiplicado pelo número de anos de antiguidade que excedam aquele limite"
		first = min(years, COMPENSATION_308_FULL_YEARS)
		excess = max(0, years - COMPENSATION_308_FULL_YEARS)
		amount = flt(base * first + COMPENSATION_308_EXCESS_RATE * base * excess, 2)
		formula = "{base} × {f} years".format(base=_n(base), f=first)
		check = "{base} * {f}".format(base=base, f=first)
		if excess:
			formula += " + 50% × {base} × {e} years".format(base=_n(base), e=excess)
			check += " + 50% * {base} * {e}".format(base=base, e=excess)
		return {"amount": amount, "article": "Artigo 308.º", "status": "applicable",
		        "formula": formula, "formula_check": check,
		        "years_first": first, "years_excess": excess}

	if rule == "309":
		# "50% do valor do salário-base ... multiplicado pelo número de anos de serviço"
		amount = flt(0.5 * base * years, 2)
		return {"amount": amount, "article": "Artigo 309.º", "status": "applicable",
		        "formula": "50% × {base} × {y} years".format(base=_n(base), y=years),
		        "formula_check": "50% * {base} * {y}".format(base=base, y=years),
		        "requires_court_ruling": bool(spec.get("requires_court_ruling"))}

	if rule == "310":
		# "multiplicando o valor do salário-base ... pelo número de anos de antiguidade",
		# with n.º 3: "tem sempre como valor mínimo o correspondente ao salário-base de
		# três meses".
		raw = flt(base * years, 2)
		floor = flt(base * INDEMNITY_310_MIN_MONTHS, 2)
		amount = max(raw, floor)
		formula = "{base} × {y} years".format(base=_n(base), y=years)
		check = "{base} * {y}".format(base=base, y=years)
		if amount > raw:
			formula = "{base} × {m} months (statutory minimum)".format(
				base=_n(base), m=INDEMNITY_310_MIN_MONTHS)
			check = "{base} * {m}".format(base=base, m=INDEMNITY_310_MIN_MONTHS)
		return {"amount": amount, "article": "Artigo 310.º", "status": "applicable",
		        "formula": formula, "formula_check": check, "floor_applied": amount > raw,
		        "requires_court_ruling": bool(spec.get("requires_court_ruling"))}

	return {"amount": 0.0, "article": None, "formula": None,
	        "status": "legal_input_required",
	        "message": "Unmapped compensation rule {0!r}.".format(rule)}


def settlement_reference():
	"""The final-settlement legal reference, for the UI help panel and the docs."""
	return {
		"law": LAW,
		"source": "Diário da República, I Série n.º 245, de 27 de Dezembro de 2023",
		"marker": REVIEW_MARKER,
		"reasons": [dict(key=k, **TERMINATION_REASONS[k]) for k in TERMINATION_REASON_ORDER],
		"articles": {
			"leave_duration": "Artigo 204.º — 22 working days a year; two working days per "
			                  "complete month in the year of admission (floor of six).",
			"leave_fixed_term": "Artigo 205.º — fixed term of a year or less: two working "
			                    "days per complete month, capped at 22.",
			"leave_on_termination": "Artigo 212.º — vested untaken leave, plus two working "
			                        "days per complete month from 1 January; or, before the "
			                        "right vests, from the date of admission.",
			"leave_pay_base": "Artigo 213.º — leave pay is the salário-base plus technical "
			                  "and availability supplements. Meal and transport subsidies "
			                  "are excluded unless the parties agree otherwise.",
			"hourly_rate": "Artigo 237.º n.º 7 — S/H = (Sm × 12) / (52 × Hs).",
			"absence_deduction": "Artigo 240.º n.º 2 — deductions for absence use the "
			                     "artigo 237.º n.º 7 formula.",
			"annual_supplements": "Artigo 238.º — vacation gratuity and Christmas bonus, "
			                      "each a minimum of 50% of the salário-base, proportional "
			                      "to complete months when a full year was not served.",
			"payment_deadline": "Artigo 245.º n.º 4 — salary, indemnity and every other "
			                    "amount owed are paid within three days of cessation.",
			"compensation_307": "Artigo 307.º — insolvency or extinction of the employer: "
			                    "50% × salário-base × years of service.",
			"compensation_308": "Artigo 308.º — objective or collective dismissal: "
			                    "salário-base × years up to five, plus 50% × salário-base × "
			                    "years beyond five.",
			"indemnity_309": "Artigo 309.º — non-reinstatement: 50% × salário-base × years.",
			"indemnity_310": "Artigo 310.º — unlawful individual dismissal or indirect "
			                 "dismissal: salário-base × years, minimum three months.",
			"seniority": "Artigo 311.º — a fraction of three months or more counts as a "
			             "full year.",
			"resignation_notice": "Artigo 305.º — 30 days' written notice; missing notice "
			                      "obliges the worker to compensate the employer.",
		},
		"open_questions": [
			{"question": "The monetary divisor that converts a monthly salary into one "
			             "day of leave pay.",
			 "answer": LEAVE_DAY_DIVISOR_NOTE},
			{"question": "Whether the six-day floor of artigo 204.º n.º 2 applies to the "
			             "money paid under artigo 212.º n.º 3.",
			 "answer": "The article sets a floor for the leave period taken, not for the "
			           "compensation. Not applied. " + REVIEW_MARKER},
			{"question": "IRT and INSS incidence on termination compensation.",
			 "answer": "The consolidated Código do IRT excludes compensation for contract "
			           "termination up to the Lei Geral do Trabalho limits (artigo 2.º "
			           "n.º 1 al. g)), while commentary on Lei n.º 28/20 states such "
			           "compensation became fully taxable. Decreto Presidencial n.º 227/18 "
			           "artigo 13.º defines the social-security base as gross remuneration "
			           "and excludes the subsídio de férias, but does not mention "
			           "termination compensation. The position is a company setting, shown "
			           "as such on every settlement. " + REVIEW_MARKER},
		],
	}


def months_between(start, end):
	if not (start and end):
		return 0
	start, end = getdate(start), getdate(end)
	months = (end.year - start.year) * 12 + (end.month - start.month)
	if end.day >= start.day:
		months += 1
	return max(0, months)


def check_contract(contract):
	"""Warnings for one contract, each citing its article. Never blocks.

	Returns a list of ``{code, article, message, severity}``. ``severity`` is always
	"warning" — a hard stop here would mean this app deciding a question of law.
	"""
	warnings = []

	def warn(code, article, message):
		warnings.append({"code": code, "article": article, "message": message,
		                 "law": LAW, "marker": REVIEW_MARKER, "severity": "warning"})

	open_ended = cint(contract.get("is_open_ended"))
	start, end = contract.get("start_date"), contract.get("end_date")

	# --- fixed-term duration (artigo 16.º / 17.º) --------------------------- #
	if not open_ended and start and end:
		duration = months_between(start, end)
		if duration > FIXED_TERM_ABSOLUTE_MAX_MONTHS:
			warn("LGT-016",
			     "Artigo 16.º n.º 1 / Artigo 17.º n.º 5",
			     _("This fixed-term contract runs for {0} months. The longest ground in "
			       "artigo 16.º allows {1} months; beyond the applicable maximum the "
			       "contract converts to an open-ended one by operation of law. Confirm "
			       "the legal ground with your legal adviser.").format(
				     duration, FIXED_TERM_ABSOLUTE_MAX_MONTHS))
		elif duration > FIXED_TERM_LIMITS_MONTHS["temporary_increase"]:
			warn("LGT-016-INFO",
			     "Artigo 16.º n.º 1",
			     _("This fixed-term contract runs for {0} months. Only some legal grounds "
			       "permit a term this long (36 months for substitution or construction, "
			       "60 months for a new activity). Check the ground is recorded "
			       "elsewhere.").format(duration))

	# --- probation (artigo 18.º) -------------------------------------------- #
	p_start, p_end = contract.get("probation_start"), contract.get("probation_end")
	if p_start and p_end:
		days = (getdate(p_end) - getdate(p_start)).days + 1
		if not open_ended and days > PROBATION_MAX_DAYS_FIXED_TERM:
			warn("LGT-018-FT", "Artigo 18.º",
			     _("Probation of {0} days on a fixed-term contract. The statutory maximum "
			       "for a fixed-term contract is {1} days.").format(
				     days, PROBATION_MAX_DAYS_FIXED_TERM))
		elif open_ended and days > PROBATION_MAX_DAYS_MANAGEMENT:
			warn("LGT-018-MAX", "Artigo 18.º n.º 2",
			     _("Probation of {0} days exceeds every statutory maximum — {1} days by "
			       "default, {2} days by written agreement, {3} days for management "
			       "functions.").format(days, PROBATION_DEFAULT_DAYS, PROBATION_MAX_DAYS,
			                            PROBATION_MAX_DAYS_MANAGEMENT))
		elif open_ended and days > PROBATION_MAX_DAYS:
			warn("LGT-018-MGT", "Artigo 18.º n.º 2",
			     _("Probation of {0} days is only lawful for workers performing management "
			       "functions (up to {1} days). Otherwise the maximum is {2} days by "
			       "written agreement.").format(days, PROBATION_MAX_DAYS_MANAGEMENT,
			                                    PROBATION_MAX_DAYS))
		elif days > PROBATION_DEFAULT_DAYS:
			warn("LGT-018-AGR", "Artigo 18.º n.º 2",
			     _("Probation of {0} days exceeds the default {1} days. An extension "
			       "requires written agreement between the parties.").format(
				     days, PROBATION_DEFAULT_DAYS))

	# --- notice (minimum 30 days) ------------------------------------------- #
	notice = cint(contract.get("notice_days"))
	if notice and notice < MINIMUM_NOTICE_DAYS:
		warn("LGT-NOTICE", LAW,
		     _("A notice period of {0} days is recorded. The statutory minimum for "
		       "dismissal for objective cause, collective dismissal and resignation "
		       "without just cause is {1} days.").format(notice, MINIMUM_NOTICE_DAYS))

	return warnings


def reference():
	"""The whole reference set, for the UI and the documentation."""
	return {
		"law": LAW,
		"marker": REVIEW_MARKER,
		"replaced": "Lei n.º 7/15, de 15 de Junho (revoked by Lei n.º 12/23)",
		"probation": {
			"article": "Artigo 18.º",
			"default_days": PROBATION_DEFAULT_DAYS,
			"max_days_by_agreement": PROBATION_MAX_DAYS,
			"max_days_management": PROBATION_MAX_DAYS_MANAGEMENT,
			"max_days_fixed_term": PROBATION_MAX_DAYS_FIXED_TERM,
			"note": _("Either party may terminate during probation without notice, "
			          "compensation or justification. Once it passes, seniority counts "
			          "from the start of the contract."),
		},
		"fixed_term": {
			"article": "Artigo 16.º / Artigo 17.º",
			"limits_months": FIXED_TERM_LIMITS_MONTHS,
			"absolute_max_months": FIXED_TERM_ABSOLUTE_MAX_MONTHS,
			"non_renewal_notice_days": NON_RENEWAL_NOTICE_DAYS,
			"note": _("Open-ended contracts are the rule under Lei n.º 12/23; a fixed term "
			          "is permitted only on the grounds listed in artigo 16.º. Exceeding "
			          "the applicable maximum converts the contract to open-ended."),
		},
		"notice": {"minimum_days": MINIMUM_NOTICE_DAYS},
		"severance": SEVERANCE_METHOD,
		"sources": [
			"LEX.AO — Lei n.º 12/23 de 27 de Dezembro",
			"Angolex — Lei Geral do Trabalho (Lei n.º 12/23)",
			"PwC Angola — Flash regulatório: Nova Lei Geral do Trabalho",
			"WageIndicator Angola — Aviso prévio e indemnização por despedimento",
		],
		"caveat": _("The Ministry's published PDF is a scanned image and could not be read "
		            "mechanically. These figures come from reproductions of the statute and "
		            "must be confirmed against the Diário da República by your legal "
		            "adviser before they are relied on."),
	}
