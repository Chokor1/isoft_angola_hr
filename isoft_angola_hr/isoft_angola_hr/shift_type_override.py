"""Shift Type with Half Day / Absent thresholds scaled to the day's scheduled hours.

Registered through `override_doctype_class`, which is the supported way to change a
DocType's behaviour from another app — no patching, and no copy of upstream logic:
get_attendance() below scales the two threshold fields for the duration of the call
and lets ERPNext's own implementation do the work.

Without a weekly schedule the scale factor is 1.0 and this is a straight pass-through,
so a Shift Type that has no Weekday Hours rows behaves exactly like stock ERPNext.
"""
from erpnext.hr.doctype.shift_type.shift_type import ShiftType as _ERPNextShiftType

from isoft_angola_hr.isoft_angola_hr.shift_weekday import get_threshold_scale_factor


class ShiftType(_ERPNextShiftType):
	def get_attendance(self, logs):
		"""Judge the day against its own scheduled hours rather than a full day's.

		A 4-hour Saturday would otherwise be marked Half Day or Absent against
		thresholds written for a 9.5-hour weekday.
		"""
		scale = get_threshold_scale_factor(self, logs[0]) if logs else 1.0
		if scale == 1.0:
			return super().get_attendance(logs)

		absent = self.working_hours_threshold_for_absent
		half_day = self.working_hours_threshold_for_half_day
		try:
			self.working_hours_threshold_for_absent = (absent or 0) * scale
			self.working_hours_threshold_for_half_day = (half_day or 0) * scale
			return super().get_attendance(logs)
		finally:
			self.working_hours_threshold_for_absent = absent
			self.working_hours_threshold_for_half_day = half_day
