# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Overtime pay — the one calculation, for every path that pays overtime.

Source: "Overtime Calculation" (HR, October 2026), on Lei n.º 12/23:

  Artigo 237.º n.º 7, hourly wage FOR OVERTIME ONLY:
      SH = (SB × M) / (S × H) = (salário-base × 12) / (52 × 44)

  Artigo 188.º, the month's overtime hours NH:
      NH ≤ 30   HE = NH × SH × 100%
      NH > 30   HE = 30 × SH × 100% + (NH − 30) × SH × 75%

Basic salary 400 000,00 Kz: SH 2 097,90; 15 h → 31 468,50; 30 h → 62 937,00;
38 h → 62 937,00 + 12 587,40 = 75 524,40.

What it replaces. The old figure was Σ_day (base ÷ period working days ÷ that day's shift
hours) × Overtime Multiplier (2.0): a shift-hours divisor and a flat 200%, neither of which
the document uses. This formula reads no shift, working day, holiday or Settings value. 44,
52 and 12 are fixed by the document. Settlement's Normal Weekly Hours setting and the
payslip's hourly display are other questions, so the Art. 237(7) helper in
angola_labour_law is not reused here with a configurable H.

Rounding, decided against the document's own figures. SH is rounded to the cent (half up)
BEFORE it is multiplied: 15 × 2 097,90 = 31 468,50 is what the document prints, and full
precision would give 31 468,53. Each tier amount is then rounded to the cent and the total
is their sum, so the printed parts always add up to the total. Arithmetic is Decimal
throughout. Half-up at two places is also what frappe.utils.flt(x, 2) does, so the result
agrees with every other amount on the slip.

Inputs. NH is the month's overtime hours, possibly fractional. Negative, non-numeric or
non-finite hours are refused, never coerced to 0. So is any overtime against a basic salary
that is zero or missing, because that would pay nothing silently. A ceiling is set only by
the caller that knows the period (see max_hours_in_period): no business limit is invented.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import date_diff, getdate

from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

#: Artigo 237.º n.º 7 as the overtime document states it.
MONTHS = Decimal(law.MONTHS_PER_YEAR)               # M  = 12
WEEKS = Decimal(law.WORKING_WEEKS_PER_YEAR)         # S  = 52
WEEKLY_HOURS = Decimal(44)                          # H  = 44
#: Artigo 188.º: the first 30 hours of the month at 100%, the rest at 75%.
FIRST_TIER_HOURS = Decimal(30)
FIRST_TIER_RATE = Decimal("1.00")
SECOND_TIER_RATE = Decimal("0.75")

CENT = Decimal("0.01")


def _money(value):
	return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _decimal(value, label):
	"""A finite Decimal from a number or numeric text. Empty means 0. Anything else fails."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return Decimal(0)
	if isinstance(value, bool):
		raise frappe.ValidationError(_("{0} must be a number.").format(label))
	try:
		number = Decimal(str(value).strip())
	except (InvalidOperation, ValueError):
		frappe.throw(_("{0} must be a number, not {1}.").format(label, frappe.bold(value)),
		             title=_("Invalid Overtime"))
	if not number.is_finite():
		frappe.throw(_("{0} must be a finite number.").format(label), title=_("Invalid Overtime"))
	return number


def parse_hours(value, who=None):
	"""Overtime hours as a Decimal: numeric, finite and not negative."""
	label = _("Overtime hours for {0}").format(who) if who else _("Overtime hours")
	hours = _decimal(value, label)
	if hours < 0:
		frappe.throw(_("{0} cannot be negative ({1}).").format(label, value),
		             title=_("Invalid Overtime"))
	return hours


def hourly_wage(basic_salary):
	"""SH = (basic salary × 12) / (52 × 44), rounded to the cent."""
	return _money(_decimal(basic_salary, _("Basic salary")) * MONTHS / (WEEKS * WEEKLY_HOURS))


def calculate_overtime(basic_salary, overtime_hours, who=None):
	"""Overtime pay for one month's hours, with every figure the payslip or a screen shows.

	Returns floats (the application's currency type), every amount already at two
	decimals: ``hours``, ``basic_salary``, ``hourly_wage``, ``tier1_hours``,
	``tier1_rate`` (100), ``tier1_amount``, ``tier2_hours``, ``tier2_rate`` (75),
	``tier2_amount``, ``total``.
	"""
	hours = parse_hours(overtime_hours, who)
	base = _decimal(basic_salary, _("Basic salary"))
	if hours and base <= 0:
		frappe.throw(_("{0} has {1} overtime hours but no basic salary on the Salary Profile, "
		               "so the overtime hourly wage (Art. 237.º n.º 7) cannot be calculated.")
		             .format(who or _("This employee"), hours), title=_("Invalid Overtime"))
	sh = hourly_wage(base) if base > 0 else Decimal(0)
	tier1 = min(hours, FIRST_TIER_HOURS)
	tier2 = max(hours - FIRST_TIER_HOURS, Decimal(0))
	tier1_amount = _money(tier1 * sh * FIRST_TIER_RATE)
	tier2_amount = _money(tier2 * sh * SECOND_TIER_RATE)
	return {
		"hours": float(hours),
		"basic_salary": float(_money(base)),
		"hourly_wage": float(sh),
		"tier1_hours": float(tier1),
		"tier1_rate": float(FIRST_TIER_RATE * 100),
		"tier1_amount": float(tier1_amount),
		"tier2_hours": float(tier2),
		"tier2_rate": float(SECOND_TIER_RATE * 100),
		"tier2_amount": float(tier2_amount),
		"total": float(tier1_amount + tier2_amount),
	}


def max_hours_in_period(start_date, end_date):
	"""Every hour in the period. Not a policy: overtime above it cannot have been worked."""
	return (date_diff(getdate(end_date), getdate(start_date)) + 1) * 24


def assert_possible(hours, start_date, end_date, who=None):
	"""Refuse hours that exceed the hours that exist in the period (a typing error)."""
	ceiling = max_hours_in_period(start_date, end_date)
	if parse_hours(hours, who) > ceiling:
		frappe.throw(_("{0} overtime hours for {1} exceed the {2} hours in the period {3} to "
		               "{4}. Check the figure.").format(hours, who or _("this employee"), ceiling,
		                                                 getdate(start_date), getdate(end_date)),
		             title=_("Invalid Overtime"))
