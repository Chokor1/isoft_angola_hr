# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Absence and late-arrival deductions — the one calculation, for every path that pays.

Source: "Absence and Late Arrival Deductions" (HR, October 2026). Three rules:

  1.1 Basic salary, per absence:   (SB × 12 / 2288) × 7.333333 × F
      2288 = 52 × 44, so SB × 12 / 2288 is the hourly wage SH of the overtime rule
      (services/overtime.py); 7.333333 = 44 / 6, the hours of one day of a six-day week.
      An absence therefore costs SH × 44/6 whatever the month: 400 000,00 → SH 2 097,90,
      one absence 15 384,60, two 30 769,20.
  1.2 Food and transport allowances are paid on 26 days:  (A / 26) × (26 − F)
      30 000,00 with two absences → 27 692,31.
  2   Late arrivals: minutes accumulate over the payroll period; nothing is deducted
      under 40 minutes; from 40 minutes on, SH × AM / 60 comes off the basic salary only.
      400 000,00 and 40 minutes → 1 398,60; 39 minutes → 0.

What it replaces. Basic and allowances were prorated by paid ÷ total working days, so
an absence was worth a different amount every month (33 333,33 in a 24-day July,
32 653,06 in a 24,5-day August) and the allowances followed the calendar instead of 26
days. Nothing was deducted for lateness.

What F is (HR, 26 October 2026): a day on which the employee was Absent, an Unjustified
full-day occurrence or a day of unpaid leave counts 1; a Half Day counts 0,5; the hours
of an Unjustified partial absence or early exit are priced at SH per hour, with no
threshold; the minutes of Unjustified lateness feed rule 2. Counting is the caller's job
(doctype/isoft_salary_slip/isoft_salary_slip.py::absence_inputs); this module only
prices what it is given.

Rounding, as in overtime.py: SH is rounded to the cent before it is multiplied, every
amount is rounded half up to the cent, and arithmetic is Decimal throughout. The
document's own example prints 30 769,19 for two absences; its formula gives 30 769,1986
with SH rounded and 30 769,20 with 44/6 exact, so 30 769,20 is what this module pays.

Inputs are refused, never coerced: negative or non-numeric days, hours or minutes raise,
and so does any absence against a basic salary of zero.
"""

from decimal import Decimal

import frappe
from frappe import _

from isoft_angola_hr.isoft_angola_hr.services import overtime
from isoft_angola_hr.isoft_angola_hr.services.overtime import _decimal, _money, hourly_wage

#: 44 hours a week over six days: the hours one absence is worth (7,333333…).
DAYS_PER_WEEK = Decimal(6)
DAILY_HOURS = overtime.WEEKLY_HOURS / DAYS_PER_WEEK
#: The allowances are paid on 26 days a month, whatever the calendar says.
ALLOWANCE_DAYS = Decimal(26)
#: Below this many accumulated minutes in the period, lateness costs nothing.
LATE_THRESHOLD_MINUTES = Decimal(40)
MINUTES_PER_HOUR = Decimal(60)

#: The allowances rule 1.2 applies to, by engine abbreviation.
ALLOWANCE_ABBRS = ("SDA", "SDT", "AF")


def _count(value, label, who=None):
	"""A non-negative count (days, hours or minutes); refuses anything else."""
	d = _decimal(value, label)
	if d < 0:
		frappe.throw(_("{0} cannot be negative ({1}).").format(
			label if not who else "{0} {1}".format(label, _("for {0}").format(who)), value),
			title=_("Invalid Absence"))
	return d


def basic_deduction(basic_salary, absent_days=0, absent_hours=0):
	"""Rule 1.1 (days) plus unjustified partial hours at SH: what comes off the basic."""
	days = _count(absent_days, _("Absent days"))
	hours = _count(absent_hours, _("Absent hours"))
	if not days and not hours:
		return Decimal("0.00")
	sh = hourly_wage(basic_salary)
	if sh <= 0:
		frappe.throw(_("Absences cannot be deducted without a basic salary."), title=_("Invalid Absence"))
	return _money(sh * (days * DAILY_HOURS + hours))


def late_deduction(basic_salary, late_minutes=0):
	"""Rule 2: nothing under 40 accumulated minutes, SH × minutes / 60 from 40 on."""
	minutes = _count(late_minutes, _("Late minutes"))
	if minutes < LATE_THRESHOLD_MINUTES:
		return Decimal("0.00")
	sh = hourly_wage(basic_salary)
	if sh <= 0:
		frappe.throw(_("Late arrivals cannot be deducted without a basic salary."), title=_("Invalid Absence"))
	return _money(sh * minutes / MINUTES_PER_HOUR)


def allowance_payable(amount, absent_days=0):
	"""Rule 1.2: the allowance for the month on 26 days, less the absent ones."""
	days = _count(absent_days, _("Absent days"))
	full = _money(_decimal(amount, _("Allowance")))
	if full <= 0:
		return Decimal("0.00")
	remaining = max(Decimal(0), ALLOWANCE_DAYS - days)
	return _money(full / ALLOWANCE_DAYS * remaining)


def calculate(basic_salary, absent_days=0, absent_hours=0, late_minutes=0, allowances=None, who=None):
	"""Every absence figure of a slip, in one dict the engine, the preview and the
	payslip all read.

	:param allowances: {abbr: monthly amount} for the components rule 1.2 applies to
	:returns: hourly_wage, daily_hours, absent_days, absent_hours, absence_deduction,
	          late_minutes, late_applied, late_deduction, total_deduction (off the basic)
	          and allowances {abbr: {"full", "payable", "deduction"}}, all floats
	"""
	days = _count(absent_days, _("Absent days"), who)
	hours = _count(absent_hours, _("Absent hours"), who)
	minutes = _count(late_minutes, _("Late minutes"), who)
	absence = basic_deduction(basic_salary, days, hours)
	late = late_deduction(basic_salary, minutes)
	base = _decimal(basic_salary, _("Basic salary"))
	out = {
		"hourly_wage": float(hourly_wage(base)) if base > 0 else 0.0,
		"daily_hours": float(_money(DAILY_HOURS)),
		# what one absence costs, for screens: SH × 44/6 exact, not × the rounded 7,33
		"per_day": float(_money(hourly_wage(base) * DAILY_HOURS)) if base > 0 else 0.0,
		"absent_days": float(days),
		"absent_hours": float(hours),
		"absence_deduction": float(absence),
		"late_minutes": float(minutes),
		"late_threshold": float(LATE_THRESHOLD_MINUTES),
		"late_applied": bool(minutes >= LATE_THRESHOLD_MINUTES and minutes > 0),
		"late_deduction": float(late),
		"total_deduction": float(absence + late),
		"allowance_days": float(ALLOWANCE_DAYS),
		"allowances": {},
	}
	for abbr, amount in (allowances or {}).items():
		full = _money(_decimal(amount, abbr))
		payable = allowance_payable(full, days)
		out["allowances"][abbr] = {"full": float(full), "payable": float(payable),
		                           "deduction": float(full - payable)}
	return out
