"""Per-weekday shift hours — owned by this app, layered onto ERPNext at runtime.

A Shift Type can give each weekday its own Start/End Time and a working-day flag
(the `Shift Weekday Hours` child table, which this app also owns). Three ERPNext
behaviours follow from that, and all three used to be edits inside erpnext/hr:

  1. a weekday marked non-working is treated like a holiday, so no attendance is
     expected and none is marked absent;
  2. shift start/end for a date come from that weekday's row when one exists;
  3. the Half Day / Absent thresholds scale to the day's scheduled hours, so a 4h
     Saturday is not judged against full-day thresholds.

(1) and (2) are module-level functions in ERPNext with no class to override, so they
are installed over the originals by install_patches(), called from this app's
__init__. (3) is a method on Shift Type and is handled properly, by overriding the
DocType class — see shift_type_override.py.

erpnext/hr/doctype/shift_assignment/shift_assignment.py and shift_type.py are
byte-identical to upstream v13.49.12 again.

get_employee_shift() below is upstream's function with one block added, marked
inline. It is a copy rather than a wrapper because the weekday rule has to apply
*before* the next-shift search inside the function, and there is no seam there.
That copy is the maintenance cost of keeping core clean: re-check it against
upstream on any ERPNext upgrade. install_patches() verifies at import time that the
upstream source still matches what this copy was derived from, and refuses to patch
(loudly, in the error log) if it has drifted.
"""
import hashlib
from datetime import timedelta

import frappe
from frappe.utils import get_datetime, getdate, nowdate

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# sha256 of the upstream get_employee_shift source this copy was taken from.
UPSTREAM_FINGERPRINT = "519285fca2ffb6e5e9cc9dbb59c6b9685f149f67ebc8c2cfdd6798b33e2167e0"


def get_weekday_shift_hours(shift_type_name, for_date):
	"""Resolve per-weekday Start/End Time overrides for a Shift Type on a given date.

	Returns (start_time, end_time, is_working_day):
	  * start_time/end_time are timedeltas from the matching Weekday Hours row, or
	    None when there is no override — the caller then keeps the Shift Type's own
	    Start Time / End Time.
	  * is_working_day is False only when the weekday is explicitly marked non-working.

	A Shift Type with no weekly schedule returns (None, None, True), which is what
	makes every caller behave exactly like stock ERPNext.
	"""
	if not shift_type_name:
		return None, None, True

	weekday = WEEKDAYS[getdate(for_date).weekday()]
	shift_doc = frappe.get_cached_doc("Shift Type", shift_type_name)
	for row in shift_doc.get("weekday_hours") or []:
		if row.weekday == weekday:
			if not row.is_working_day:
				return None, None, False
			return row.start_time, row.end_time, True
	return None, None, True


def is_shift_working_day(shift_type_name, for_date):
	"""False only when this date's weekday is explicitly marked non-working."""
	return get_weekday_shift_hours(shift_type_name, for_date)[2]


def get_default_shift_hours(shift_type):
	"""Scheduled hours of a full default day for this shift (handles overnight shifts)."""
	if not (shift_type.start_time and shift_type.end_time):
		return 0
	hours = (shift_type.end_time - shift_type.start_time).total_seconds() / 3600
	if hours <= 0:
		hours += 24
	return hours


def get_threshold_scale_factor(shift_type, log):
	"""Ratio of this day's scheduled hours to the shift's default full-day hours.

	The checkin's shift window already reflects any weekday override, so this needs no
	date of its own. Returns 1.0 when the shift has no weekly schedule, which keeps
	the thresholds identical to stock ERPNext.
	"""
	default_hours = get_default_shift_hours(shift_type)
	if not default_hours or not (log.get("shift_start") and log.get("shift_end")):
		return 1.0
	day_hours = (get_datetime(log.shift_end) - get_datetime(log.shift_start)).total_seconds() / 3600
	if day_hours <= 0:
		return 1.0
	return day_hours / default_hours


def get_employee_shift(
	employee, for_date=None, consider_default_shift=False, next_shift_direction=None
):
	"""Returns a Shift Type for the given employee on the given date. (excluding the holidays)

	:param employee: Employee for which shift is required.
	:param for_date: Date on which shift are required
	:param consider_default_shift: If set to true, default shift is taken when no shift assignment is found.
	:param next_shift_direction: One of: None, 'forward', 'reverse'. Direction to look for next shift if shift not found on given date.
	"""
	if for_date is None:
		for_date = nowdate()
	default_shift = frappe.get_cached_value("Employee", employee, "default_shift")
	shift_type_name = None
	shift_assignment_details = frappe.db.get_value(
		"Shift Assignment",
		{"employee": employee, "start_date": ("<=", for_date), "docstatus": "1", "status": "Active"},
		["shift_type", "end_date"],
	)

	if shift_assignment_details:
		shift_type_name = shift_assignment_details[0]

		# if end_date present means that shift is over after end_date else it is a ongoing shift.
		if shift_assignment_details[1] and for_date >= shift_assignment_details[1]:
			shift_type_name = None

	if not shift_type_name and consider_default_shift:
		shift_type_name = default_shift
	if shift_type_name:
		holiday_list_name = frappe.get_cached_value("Shift Type", shift_type_name, "holiday_list")
		if not holiday_list_name:
			holiday_list_name = get_holiday_list_for_employee(employee, False)
		if holiday_list_name and is_holiday(holiday_list_name, for_date):
			shift_type_name = None

	# --- this app's addition ---
	# A weekday explicitly marked non-working in the Shift Type's weekly schedule is
	# treated exactly like a holiday. Placed here, before the next-shift search, so the
	# search skips it the same way it skips a holiday.
	if shift_type_name and not is_shift_working_day(shift_type_name, for_date):
		shift_type_name = None

	if not shift_type_name and next_shift_direction:
		MAX_DAYS = 366
		if consider_default_shift and default_shift:
			direction = -1 if next_shift_direction == "reverse" else +1
			for i in range(MAX_DAYS):
				date = for_date + timedelta(days=direction * (i + 1))
				shift_details = get_employee_shift(employee, date, consider_default_shift, None)
				if shift_details:
					shift_type_name = shift_details.shift_type.name
					for_date = date
					break
		else:
			direction = "<" if next_shift_direction == "reverse" else ">"
			sort_order = "desc" if next_shift_direction == "reverse" else "asc"
			dates = frappe.db.get_all(
				"Shift Assignment",
				["start_date", "end_date"],
				{
					"employee": employee,
					"start_date": (direction, for_date),
					"docstatus": "1",
					"status": "Active",
				},
				as_list=True,
				limit=MAX_DAYS,
				order_by="start_date " + sort_order,
			)

			if dates:
				for date in dates:
					if date[1] and date[1] < for_date:
						continue
					shift_details = get_employee_shift(employee, date[0], consider_default_shift, None)
					if shift_details:
						shift_type_name = shift_details.shift_type.name
						for_date = date[0]
						break

	return get_shift_details(shift_type_name, for_date)


def get_shift_details(shift_type_name, for_date=None):
	"""Upstream's get_shift_details with this date's weekday override applied.

	A wrapper rather than a copy: upstream computes the four datetimes from the Shift
	Type's own Start/End Time, so when a weekday row overrides them the same arithmetic
	is simply redone here off the shift_type dict upstream already returned.
	"""
	details = _upstream_get_shift_details(shift_type_name, for_date)
	if not details:
		return details

	if not for_date:
		for_date = nowdate()
	start_time, end_time, _working = get_weekday_shift_hours(shift_type_name, for_date)
	if start_time is None and end_time is None:
		return details

	shift_type = details.shift_type
	start_time = start_time if start_time is not None else shift_type.start_time
	end_time = end_time if end_time is not None else shift_type.end_time

	from datetime import datetime

	date = getdate(for_date)
	start_datetime = datetime.combine(date, datetime.min.time()) + start_time
	end_date = date + timedelta(days=1) if start_time > end_time else date
	end_datetime = datetime.combine(end_date, datetime.min.time()) + end_time

	details.start_datetime = start_datetime
	details.end_datetime = end_datetime
	details.actual_start = start_datetime - timedelta(
		minutes=shift_type.begin_check_in_before_shift_start_time
	)
	details.actual_end = end_datetime + timedelta(
		minutes=shift_type.allow_check_out_after_shift_end_time
	)
	return details


# Filled in by install_patches(); referenced by get_shift_details above and by the
# copied get_employee_shift, so both always reach the genuine upstream implementation.
_upstream_get_shift_details = None

# Every module namespace that holds its own reference to the two functions. ERPNext
# binds them with `from ... import`, so replacing the attribute on shift_assignment
# alone would leave those copies pointing at the originals.
_TARGET_MODULES = (
	"erpnext.hr.doctype.shift_assignment.shift_assignment",
	"erpnext.hr.doctype.shift_type.shift_type",
	"erpnext.hr.doctype.employee_checkin.employee_checkin",
	"erpnext.hr.doctype.attendance.attendance",
)

_patched = False


def install_patches():
	"""Point ERPNext's shift resolution at this app's weekday-aware versions.

	Idempotent, and safe to call before ERPNext is importable — it simply does nothing
	then. Refuses to patch if upstream's get_employee_shift no longer matches the copy
	above, so an ERPNext upgrade surfaces as a log entry and stock behaviour rather
	than as a silently stale override.
	"""
	global _patched, _upstream_get_shift_details
	if _patched:
		return True
	try:
		import importlib
		import inspect

		origin = importlib.import_module("erpnext.hr.doctype.shift_assignment.shift_assignment")
	except Exception:
		return False

	# Already installed in this process — nothing to do. Checked before the drift
	# fingerprint below, because once patched the bound function is this module's copy
	# and hashing it would read as drift.
	if getattr(origin, "get_employee_shift", None) is get_employee_shift:
		_patched = True
		return True

	try:
		current = inspect.getsource(origin.get_employee_shift)
		fingerprint = hashlib.sha256(current.encode("utf-8")).hexdigest()
		if fingerprint != UPSTREAM_FINGERPRINT:
			frappe.log_error(
				"ERPNext's get_employee_shift has changed (%s). Per-weekday shift hours are "
				"NOT applied until isoft_angola_hr/shift_weekday.py is re-synced." % fingerprint[:12],
				"isoft_angola_hr: shift patch skipped",
			)
			return False
	except Exception:
		pass

	_upstream_get_shift_details = origin.get_shift_details

	for name in _TARGET_MODULES:
		try:
			mod = importlib.import_module(name)
		except Exception:
			continue
		if hasattr(mod, "get_shift_details"):
			mod.get_shift_details = get_shift_details
		if hasattr(mod, "get_employee_shift"):
			mod.get_employee_shift = get_employee_shift

	_patched = True
	return True
