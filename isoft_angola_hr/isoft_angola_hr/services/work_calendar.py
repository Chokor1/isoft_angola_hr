# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The one place that answers "which calendar does this employee work to?".

Two questions, one answer each, for the whole application:

    shift_on(employee, date)          which Shift Type governs this date
    resolve_holiday_list(employee...) which Holiday List governs this date

Why this module exists
----------------------
The site used to answer the holiday question in two different ways. Payroll asked
ERPNext's ``get_holiday_list_for_employee``, which reads ``Employee.holiday_list`` and
falls back to the Company default. Attendance and shift assignment asked
``shift_weekday.get_employee_shift``, which reads the **Shift Type's** holiday list first
and only then the employee's. Both were defensible; having both was not.

It surfaced in September 2026. ``ITEC - Working Hours`` carries
``holiday_list = "Holidays 2026"``, which contains the 17th and 18th (Dia do Herói
Nacional and its ponte). Attendance honoured it. Payroll did not: it read each
employee's own field, and sixty-five employees still pointed at
``Standard Holiday List Angola 2025`` - a list that expired in 2025 and contains no 2026
date at all. Those employees were credited with two working days that the company does
not work, while their colleagues, whose field happened to be empty and therefore fell
through to the correct company default, were not. Nobody was told; the two figures simply
appeared side by side in the preview.

Precedence
----------
Most specific configuration wins::

    1. the effective Shift Type's Holiday List   (Shift Assignment, else default shift)
    2. Employee.holiday_list
    3. Company.default_holiday_list

The shift comes first deliberately. A Shift Type is a working pattern - a set of days
and hours - and the holidays that interrupt it belong to the same statement. It is also
the field an administrator reaches for when a schedule changes, and it applies to
everyone on that shift at once, so it cannot go stale one employee at a time the way
eighty-five individual fields can.

A stale employee field is therefore no longer silently authoritative. It is still used,
because an employee without a shift has nothing better, and it is still reported: see
:func:`diagnose`, which payroll readiness turns into a warning naming the list that will
actually be used.

Nothing here decides how much a day is worth. Day weights come from the Shift Type's own
schedule (see ``isoft_salary_slip.shift_day_units``); this module only says which dates
are holidays and which Shift Type applies.
"""

import frappe
from frappe.utils import add_days, getdate

#: Returned by :func:`resolve_holiday_list` to say where the answer came from.
FROM_SHIFT = "Shift Type"
FROM_EMPLOYEE = "Employee"
FROM_COMPANY = "Company default"
FROM_NONE = "none"


def shift_on(employee, for_date, cache=None):
	"""The Shift Type governing an employee on a date, or None.

	A submitted Shift Assignment covering the date wins; otherwise the Employee's
	Default Shift. None means the caller falls back to the default calendar.
	"""
	d = getdate(for_date)

	def fetch():
		row = frappe.db.sql(
			"""select shift_type from `tabShift Assignment`
			where employee=%s and docstatus=1 and start_date<=%s
			and (end_date is null or end_date>=%s) and ifnull(shift_type,'')!=''
			order by start_date desc limit 1""",
			(employee, d, d),
		)
		if row and row[0][0]:
			return row[0][0]
		return frappe.db.get_value("Employee", employee, "default_shift")

	return _remember(cache, ("shift", employee, d), fetch)


def _remember(cache, key, fetch):
	"""Memo scoped to ONE computation, never to the request or the process.

	Resolving a month asks the same three questions thirty times, so some memo is needed.
	It must not outlive the call, though. ``frappe.get_cached_value`` reads the document
	cache, which survives between requests; a request-level memo survives a write made
	later in the same request. Both were tried, and both produce the same class of bug -
	a caller changes an employee's Holiday List, recomputes, and is handed the answer
	from before the change. These fields decide what somebody is paid; the only safe
	lifetime is the calculation that is using them.
	"""
	if cache is None:
		cache = {}
	if key not in cache:
		cache[key] = fetch()
	return cache[key]


def shift_holiday_list(shift, cache=None):
	"""The Holiday List a Shift Type declares, if any."""
	if not shift:
		return None
	return _remember(cache, ("shift_hl", shift),
	                 lambda: frappe.db.get_value("Shift Type", shift, "holiday_list")) or None


def covers(holiday_list, for_date, cache=None):
	"""Is this date inside the Holiday List's own validity range?

	A Holiday List states the year it describes. Outside that range it is not a calendar
	with no holidays in it - it is a calendar that says nothing about the date at all,
	and the two are very different things to pay somebody on.
	"""
	if not holiday_list:
		return False
	rng = _remember(cache, ("hl_range", holiday_list),
	                lambda: frappe.db.get_value("Holiday List", holiday_list,
	                                            ["from_date", "to_date"], as_dict=True))
	if not rng or not rng.get("from_date") or not rng.get("to_date"):
		return False
	return getdate(rng["from_date"]) <= getdate(for_date) <= getdate(rng["to_date"])


def candidates(employee, for_date=None, shift=False, cache=None):
	"""The configured Holiday Lists for this employee, most specific first."""
	if shift is False:
		shift = shift_on(employee, for_date or frappe.utils.nowdate(), cache)
	row = _remember(cache, ("emp", employee), lambda: frappe.db.get_value(
		"Employee", employee, ["holiday_list", "company"], as_dict=True)) if employee else None
	employee_list = (row or {}).get("holiday_list")
	company = (row or {}).get("company")
	company_list = _remember(cache, ("company_hl", company), lambda: frappe.db.get_value(
		"Company", company, "default_holiday_list")) if company else None
	return [(name, source) for name, source in (
		(shift_holiday_list(shift, cache), FROM_SHIFT),
		(employee_list, FROM_EMPLOYEE),
		(company_list, FROM_COMPANY),
	) if name]


def resolve_holiday_list(employee, for_date=None, shift=False, cache=None):
	"""Return ``(holiday_list, source)`` governing this employee on this date.

	Precedence is Shift Type, then Employee, then Company default - but only among the
	lists that actually cover the date. An assigned list whose validity ended before the
	payroll period is skipped in favour of the next one that does cover it.

	That second rule is the one that matters in practice. Sixty-four employees here still
	pointed at ``Standard Holiday List Angola 2025``, which expired on 31 December 2025.
	Treating it as authoritative in September 2026 meant every Angolan public holiday was
	paid to them as an ordinary working day, silently, while colleagues whose field
	happened to be empty were paid correctly - two people doing the same job, on the same
	shift, credited with different months. An expired list is not a statement that there
	are no holidays; it is a statement about a year that has passed.

	If nothing covers the date, the most specific configured list is still returned rather
	than none: the calendar is then genuinely unknown, and inventing one would be worse
	than reporting the one that was configured. Payroll readiness raises that case.

	``shift`` may be passed when the caller has already resolved it (False means "not
	supplied - look it up"; None means "explicitly no shift").
	"""
	on = for_date or frappe.utils.nowdate()
	found = candidates(employee, on, shift, cache)
	for name, source in found:
		if covers(name, on, cache):
			return name, source
	return found[0] if found else (None, FROM_NONE)


def list_holiday_dates(holiday_list, start, end):
	"""Dates in ``holiday_list`` between ``start`` and ``end``."""
	if not holiday_list:
		return set()
	rows = frappe.db.sql(
		"""select holiday_date from `tabHoliday`
		where parent=%s and parenttype='Holiday List'
		and holiday_date between %s and %s""",
		(holiday_list, getdate(start), getdate(end)),
	)
	return {getdate(r[0]) for r in rows}


def holiday_dates(employee, start, end, cache=None):
	"""Every holiday date for this employee across the period.

	The Shift Type can change mid-period through a Shift Assignment, and with it the
	holiday list, so the resolution is per date rather than once for the period. The
	lists themselves are read once each: a month resolving to a single list costs one
	query, not thirty.
	"""
	start, end = getdate(start), getdate(end)
	if end < start:
		return set()
	cache = {} if cache is None else cache
	lists, out = {}, set()
	d = start
	while d <= end:
		name, _src = resolve_holiday_list(employee, d, cache=cache)
		if name not in lists:
			lists[name] = list_holiday_dates(name, start, end)
		if d in lists[name]:
			out.add(d)
		d = add_days(d, 1)
	return out


def holiday_descriptions(holiday_list, start, end):
	"""``{date: description}`` for one list, for tracing and readiness messages."""
	if not holiday_list:
		return {}
	return {getdate(r[0]): r[1] for r in frappe.db.sql(
		"""select holiday_date, description from `tabHoliday`
		where parent=%s and parenttype='Holiday List'
		and holiday_date between %s and %s""",
		(holiday_list, getdate(start), getdate(end)))}



def diagnose(employee, start, end):
	"""What calendar this employee will actually be paid against, and what is odd about it.

	Returns a dict the readiness check and the date-by-date trace both read, so the
	warning HR sees and the trace an engineer runs can never disagree.
	"""
	start, end = getdate(start), getdate(end)
	cache = {}
	shift = shift_on(employee, end, cache)
	resolved, source = resolve_holiday_list(employee, end, shift=shift, cache=cache)
	emp = frappe.db.get_value("Employee", employee, ["holiday_list", "company"],
	                          as_dict=True) or {}
	company_list = frappe.db.get_value("Company", emp.get("company"),
	                                   "default_holiday_list") if emp.get("company") else None
	rng = frappe.db.get_value("Holiday List", resolved, ["from_date", "to_date"],
	                          as_dict=True) if resolved else None
	dates = holiday_dates(employee, start, end, cache)
	# Every configured list that is NOT the one being used, in either direction: one the
	# resolution skipped because it had expired, and one it outranked. Both matter to
	# whoever is about to pay this person, because both mean the record in front of them
	# names a calendar the payroll did not use.
	bypassed = []
	for name, from_where in candidates(employee, end, shift=shift, cache=cache):
		if name == resolved:
			continue        # the same list configured in two places is not a conflict
		r = frappe.db.get_value("Holiday List", name, ["from_date", "to_date"],
		                        as_dict=True) or {}
		bypassed.append({"list": name, "source": from_where,
		                 "from_date": r.get("from_date"), "to_date": r.get("to_date"),
		                 "expired": not covers(name, end)})
	return {
		"bypassed": bypassed,
		"any_covers": any(covers(n, end, cache)
		                  for n, _s in candidates(employee, end, shift=shift, cache=cache)),
		"employee": employee,
		"shift": shift,
		"shift_holiday_list": shift_holiday_list(shift, cache),
		"employee_holiday_list": emp.get("holiday_list"),
		"company_holiday_list": company_list,
		"resolved_holiday_list": resolved,
		"source": source,
		"covers_period": bool(rng and getdate(rng.from_date) <= start
		                      and getdate(rng.to_date) >= end) if rng else False,
		"from_date": rng.from_date if rng else None,
		"to_date": rng.to_date if rng else None,
		"holidays": sorted(dates),
	}


def explain(employee, start, end):
	"""Date-by-date trace of how the period's working days were arrived at.

	Deliberately built from the same functions the payroll uses - a trace that
	re-implements the rule can agree with itself while disagreeing with the payslip.
	"""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
		day_value, eligible_range, shift_day_hours, shift_day_units, working_days_basis,
	)

	start, end = getdate(start), getdate(end)
	cache = {}
	basis = working_days_basis()
	std = frappe.utils.flt(
		frappe.get_cached_doc("Isoft HR Settings").get("standard_daily_hours")) or 8
	info = diagnose(employee, start, end)
	elig_start, elig_end = eligible_range(employee, start, end)
	descs = holiday_descriptions(info["resolved_holiday_list"], start, end)

	rows, total = [], 0.0
	d = start
	while d <= end:
		employed = elig_start <= d <= elig_end
		shift = shift_on(employee, d, cache)
		hl, src = resolve_holiday_list(employee, d, shift=shift, cache=cache)
		hours = shift_day_hours(shift, d)
		is_holiday = d in set(info["holidays"])
		if not employed:
			contrib, reason = 0.0, "outside the employment period"
		elif is_holiday:
			contrib, reason = 0.0, "official holiday"
		elif hours <= 0:
			contrib, reason = 0.0, "the shift schedules no work on this weekday"
		else:
			contrib = day_value(shift, d, std, basis)
			reason = ("full scheduled day" if shift_day_units(shift, d) == 1.0
			          else "short scheduled day")
		total += contrib
		rows.append({
			"date": d, "day": d.strftime("%a"), "employed": employed,
			"shift": shift, "shift_holiday_list": shift_holiday_list(shift, cache),
			"employee_holiday_list": info["employee_holiday_list"],
			"company_holiday_list": info["company_holiday_list"],
			"resolved_holiday_list": hl, "source": src,
			"scheduled": hours > 0, "hours": hours,
			"day_weight": shift_day_units(shift, d) if hours > 0 else 0.0,
			"holiday": is_holiday, "description": descs.get(d, ""),
			"contribution": contrib, "reason": reason,
		})
		d = add_days(d, 1)
	return {"employee": employee, "start": start, "end": end, "basis": basis,
	        "calendar": info, "days": rows, "total_working_days": total}
