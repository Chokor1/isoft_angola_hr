# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""How many days a leave request takes — the one rule, for every path that counts one.

The rule (HR, October 2026: "Saturdays and Sundays cannot be included in the day count
for the leave request"):

    Monday to Friday      1 day each
    Saturday, Sunday      0, always
    public holiday        0 on a weekday when the Leave Type excludes holidays — ERPNext's
                          existing per-type setting, "Include holidays within leaves as
                          leaves", unchanged

so 01-10-2026 → 30-10-2026 is 22 days, not the 30 the site showed.

Why ERPNext's own count gave 30
-------------------------------
ERPNext counts calendar days and subtracts the dates in the employee's Holiday List. It only
excludes a weekend if the Holiday List lists it as a weekly off, and on this site none does:
"Holidays 2026" holds the fifteen public holidays and nothing else. That is deliberate,
not an oversight. Payroll takes the working week from each employee's Shift Type (a 4-hour
Saturday is a working half-day there; see isoft_salary_slip.shift_day_units), so writing
Saturdays into the Holiday List would take them out of payroll too.

Leave entitlement is a different question from payroll working time. A Saturday on the
shift does not make Saturday a leave day, so this rule reads no shift hours and no day
weights. The Shift Type matters only in one place: it can name the Holiday List, and
the holiday dates come from work_calendar.holiday_dates. That is the application's single
answer to "which Holiday List governs this employee on this date": Shift Type, then
Employee, then Company, skipping a list whose year has ended. Payroll already uses it.
ERPNext's resolver would read the expired "Standard Holiday List Angola 2025" that most
employees still point at, and so would find no 2026 holiday at all.

Where it applies
----------------
install_patches() puts get_number_of_leave_days below in place of ERPNext's function of
the same name, which every leave count on this bench goes through:
  * the Leave Application's own validation, so creating, editing and approving a request
    (the console, /ess, the Desk form) all store the same ``total_leave_days``;
  * the Leave Ledger Entries written on approval, which ARE the balance deduction,
    including the split when a leave crosses an allocation's expiry;
  * the partial-period recounts behind balances and reports (get_leaves_for_period);
  * the Desk form's live figure and the self-service preview (whitelisted call).

Approval still marks Attendance through ERPNext's update_attendance, unchanged. Payroll
reads leave from that Attendance, never from ``total_leave_days``, so nothing here can move
a salary figure. Existing requests are not rewritten. A request is recounted only when its
own workflow saves it again (edit, approval), as before.
"""

import inspect

import frappe
from frappe.utils import add_days, cint, getdate

from isoft_angola_hr.isoft_angola_hr.services import work_calendar

#: date.weekday() of Saturday and Sunday.
WEEKEND = (5, 6)

#: The ERPNext function this module replaces, and the arguments its callers pass.
TARGET_MODULE = "erpnext.hr.doctype.leave_application.leave_application"
TARGET_FUNCTION = "get_number_of_leave_days"
EXPECTED_PARAMETERS = ("employee", "leave_type", "from_date", "to_date", "half_day",
                       "half_day_date", "holiday_list")

#: ERPNext's own function, kept once replaced (the tests put it back to reproduce a process
#: in which the rule has not been installed yet).
UPSTREAM = {}


def leave_dates(employee, leave_type, from_date, to_date):
	"""The dates between ``from_date`` and ``to_date`` that count as leave days."""
	start, end = getdate(from_date), getdate(to_date)
	if end < start:
		return []
	holidays = set()
	if not cint(frappe.db.get_value("Leave Type", leave_type, "include_holiday")):
		holidays = work_calendar.holiday_dates(employee, start, end)
	out, d = [], start
	while d <= end:
		if d.weekday() not in WEEKEND and d not in holidays:
			out.append(d)
		d = add_days(d, 1)
	return out


@frappe.whitelist()
def get_number_of_leave_days(employee, leave_type, from_date, to_date, half_day=None,
                             half_day_date=None, holiday_list=None):
	"""Leave days between two dates, inclusive. Same signature as ERPNext's function.

	Half days keep ERPNext's meaning. A one-day request is 0.5. A longer request loses
	half a day on ``half_day_date``, but only if that date counts at all: half of a
	Saturday is still nothing.

	``holiday_list`` is accepted for compatibility and not used. ERPNext passes the list it
	stored on a ledger entry when it recounts part of a leave, and that is the
	employee-level list, often the expired 2025 one. Resolving through work_calendar on
	every count keeps a recount equal to the count made when the leave was approved.
	"""
	dates = leave_dates(employee, leave_type, from_date, to_date)
	days = float(len(dates))
	if cint(half_day) and dates:
		if getdate(from_date) == getdate(to_date):
			days = 0.5
		elif half_day_date and getdate(half_day_date) in dates:
			days -= 0.5
	return days


def install_patches():
	"""Put get_number_of_leave_days in place of ERPNext's. Idempotent.

	Safe before ERPNext is importable, when it does nothing. If an ERPNext upgrade changes
	the function's parameters, it refuses to patch and writes an Error Log entry, so
	callers never pass arguments this replacement does not understand.
	"""
	try:
		import importlib

		origin = importlib.import_module(TARGET_MODULE)
	except Exception:
		return False
	current = getattr(origin, TARGET_FUNCTION, None)
	if current is get_number_of_leave_days:
		return True
	if current is None:
		# ERPNext's module is still being imported (this ran from inside its import chain)
		# and has not defined the function yet. Not drift: the next ensure_installed retries.
		return False
	try:
		params = tuple(inspect.signature(current).parameters)
	except Exception:
		params = None
	if params != EXPECTED_PARAMETERS:
		frappe.log_error(
			"ERPNext's {0} now takes {1}. The Monday-to-Friday leave-day rule is NOT applied "
			"until isoft_angola_hr/services/leave_days.py is updated.".format(TARGET_FUNCTION, params),
			"isoft_angola_hr: leave-day patch skipped")
		return False
	UPSTREAM.setdefault(TARGET_FUNCTION, current)
	setattr(origin, TARGET_FUNCTION, get_number_of_leave_days)
	return True


def ensure_installed(*args, **kwargs):
	"""Hook entry point (Leave Application before_validate, and before_request).

	The app's __init__ installs the rule, but only once something has imported this app
	in the process, and with the hooks cached in Redis nothing has to. A worker whose first
	job saves a Leave Application would then count with ERPNext's function. before_validate
	runs ahead of ERPNext's validate, which is where total_leave_days is computed, and
	before_request covers the read paths (the Desk form's live figure, self-service,
	balances). Once installed it is a single identity check.
	"""
	install_patches()
