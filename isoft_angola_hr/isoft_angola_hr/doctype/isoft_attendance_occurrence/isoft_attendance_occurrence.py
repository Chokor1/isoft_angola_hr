# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Daily attendance occurrences (lateness, early exit, partial/half/full absence) with the
Angola Lei Geral do Trabalho justification lifecycle:

  registered -> "Pending Justification" (5 days) -> "Justified" (with a reason + document)
  or, if the deadline passes, auto -> "Unjustified" on day 6.

Unjustified occurrences feed the payroll deduction (see compute_working_days). A recurrence
monitor alerts HR when the same employee justifies the same reason 4+ times in a quarter.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, getdate, nowdate
from frappe.model.document import Document

JUSTIFY_WINDOW_DAYS = 5
RECURRENCE_THRESHOLD = 4            # same justified reason ×N per quarter
UNJUSTIFIED_MONTH_THRESHOLD = 5    # > 4 unjustified in a month (just-cause risk)


class IsoftAttendanceOccurrence(Document):
	def validate(self):
		self.justification_deadline = add_days(getdate(self.occurrence_date), JUSTIFY_WINDOW_DAYS)

		if self.authorized:
			# Pre-approved / scheduled: created already Justified, exempt from the 5-day cycle.
			if not self.justification_reason:
				frappe.throw(_("Select a reason for the authorized occurrence."))
			self.status = "Justified"
			if not self.justification_date:
				self.justification_date = nowdate()
		elif self.status == "Justified":
			if not self.justification_reason:
				frappe.throw(_("Select a justification reason before marking it Justified."))
			if not self.justification_date:
				self.justification_date = nowdate()
		else:
			self.justification_date = None

		if self.is_extraordinary and not (self.extraordinary_note or "").strip():
			frappe.throw(_("Provide the Extraordinary Note when using the override."))

		self._enforce_five_day_lock()

	def _enforce_five_day_lock(self):
		"""After the 5-day window, an occurrence's justification/status is locked. Re-justifying
		is only possible via the Extraordinary override, which is limited to HR Managers."""
		if self.is_new() or self.authorized:
			return
		if getdate(nowdate()) <= getdate(self.justification_deadline):
			return  # still within the window
		before = self.get_doc_before_save()
		if not before:
			return
		changed = (self.status != before.status) or (self.justification_reason != before.justification_reason)
		if not changed:
			return
		if not self.is_extraordinary:
			frappe.throw(_(
				"This occurrence is locked — its 5-day justification window has passed. "
				"Use the Extraordinary override (HR Manager) to re-justify exceptional cases."
			))
		if "HR Manager" not in frappe.get_roles():
			frappe.throw(_("Only an HR Manager may apply the Extraordinary override."))

	def missing_days(self):
		"""How much of a working day this occurrence represents (for payroll deduction),
		relative to the employee's shift for that date."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import shift_hours_for

		if self.occurrence_type == "Full Day":
			return 1.0
		if self.occurrence_type == "Half Day":
			return 0.5
		nh = shift_hours_for(self.employee, self.occurrence_date) or 8.0
		return min(1.0, flt(self.hours) / nh)


def occurrence_missing_by_date(employee, start_date, end_date):
	"""For a period, return two dicts keyed by date:
	  covered[date] = True if any occurrence exists that day (occurrence is authoritative),
	  deduct[date]  = summed missing-days of the UNJUSTIFIED occurrences that day (max 1)."""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import (
		employee_shift, shift_day_hours, shift_day_weight,
	)

	std = flt(frappe.db.get_single_value("Isoft HR Settings", "standard_daily_hours")) or 8
	shift = employee_shift(employee, end_date)
	rows = frappe.get_all(
		"Isoft Attendance Occurrence",
		filters={"employee": employee, "occurrence_date": ["between", [getdate(start_date), getdate(end_date)]]},
		fields=["occurrence_date", "occurrence_type", "hours", "status"],
	)
	covered, deduct = {}, {}
	for r in rows:
		d = getdate(r.occurrence_date)
		covered[d] = True
		if r.status == "Unjustified":
			w = shift_day_weight(shift, d, std)  # value of a full day on this date
			if r.occurrence_type == "Full Day":
				m = w
			elif r.occurrence_type == "Half Day":
				m = w / 2.0
			else:
				nh = shift_day_hours(shift, d) or 8.0
				m = min(w, flt(r.hours) / nh * w)
			deduct[d] = min(w, deduct.get(d, 0.0) + m)
	return covered, deduct


def auto_flag_unjustified():
	"""Scheduler (daily): flip occurrences still Pending past their 5-day deadline to
	Unjustified. Authorized/pre-approved occurrences are never touched."""
	today = getdate(nowdate())
	names = frappe.get_all(
		"Isoft Attendance Occurrence",
		filters={"status": "Pending Justification", "justification_deadline": ["<", today],
		         "authorized": 0},
		pluck="name",
	)
	for n in names:
		frappe.db.set_value("Isoft Attendance Occurrence", n, "status", "Unjustified")
	if names:
		frappe.db.commit()
	return len(names)


def _quarter_range(ref=None):
	ref = getdate(ref or nowdate())
	q_first_month = ((ref.month - 1) // 3) * 3 + 1
	start = getdate(f"{ref.year}-{q_first_month:02d}-01")
	end = add_days(add_months(start, 3), -1)
	return start, end


def _month_range(ref=None):
	ref = getdate(ref or nowdate())
	start = getdate(f"{ref.year}-{ref.month:02d}-01")
	end = add_days(add_months(start, 1), -1)
	return start, end


def check_recurrence_alerts():
	"""Scheduler (daily): alert HR when an employee justifies the same reason 4+ times this
	quarter (pattern of a recurring excuse). One alert per employee+reason per quarter."""
	q_start, q_end = _quarter_range()
	rows = frappe.db.sql(
		"""select employee, employee_name, justification_reason reason, count(*) c
		from `tabIsoft Attendance Occurrence`
		where status='Justified' and ifnull(justification_reason,'')!=''
		and occurrence_date between %s and %s
		group by employee, justification_reason having count(*) >= %s""",
		(q_start, q_end, RECURRENCE_THRESHOLD), as_dict=True,
	)
	for r in rows:
		subject = _("Attendance alert: {0} justified '{1}' {2}× this quarter").format(
			r.employee_name or r.employee, r.reason, r.c)
		_notify_hr(subject, r.employee, q_start)


def check_unjustified_month_alerts():
	"""Scheduler (daily): alert HR when an employee accumulates more than four unjustified
	occurrences in the current month (possible just-cause dismissal risk). One alert per
	employee per month."""
	m_start, m_end = _month_range()
	rows = frappe.db.sql(
		"""select employee, employee_name, count(*) c
		from `tabIsoft Attendance Occurrence`
		where status='Unjustified' and occurrence_date between %s and %s
		group by employee having count(*) >= %s""",
		(m_start, m_end, UNJUSTIFIED_MONTH_THRESHOLD), as_dict=True,
	)
	for r in rows:
		subject = _("Attendance alert: {0} has {1} unjustified occurrences this month (just-cause risk)").format(
			r.employee_name or r.employee, r.c)
		_notify_hr(subject, r.employee, m_start)


def _notify_hr(subject, employee, dedup_since):
	"""Send a one-off Alert notification to every enabled HR Manager, deduplicated by
	subject since `dedup_since`."""
	if frappe.db.exists("Notification Log", {"subject": subject, "creation": [">=", dedup_since]}):
		return
	hr_users = set(frappe.get_all("Has Role", filters={"role": "HR Manager", "parenttype": "User"}, pluck="parent"))
	for u in hr_users:
		if u in ("Administrator", "Guest") or not frappe.db.get_value("User", u, "enabled"):
			continue
		try:
			frappe.get_doc({
				"doctype": "Notification Log", "subject": subject, "for_user": u, "type": "Alert",
				"document_type": "Employee", "document_name": employee,
				"email_content": subject,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Isoft HR attendance alert failed")
	frappe.db.commit()
