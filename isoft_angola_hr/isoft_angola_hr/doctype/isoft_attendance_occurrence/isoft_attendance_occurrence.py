# Copyright (c) 2026, Abbass Chokor and contributors
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
from frappe.utils import add_days, add_months, flt, getdate, nowdate
from frappe.model.document import Document

JUSTIFY_WINDOW_DAYS = 5
RECURRENCE_THRESHOLD = 4


class IsoftAttendanceOccurrence(Document):
	def validate(self):
		self.justification_deadline = add_days(getdate(self.occurrence_date), JUSTIFY_WINDOW_DAYS)
		if self.status == "Justified":
			if not self.justification_reason:
				frappe.throw(_("Select a justification reason before marking it Justified."))
			if not self.justification_date:
				self.justification_date = nowdate()
		else:
			self.justification_date = None

	def missing_days(self):
		"""How much of a working day this occurrence represents (for payroll deduction)."""
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import normal_hours_for

		if self.occurrence_type == "Full Day":
			return 1.0
		if self.occurrence_type == "Half Day":
			return 0.5
		nh = normal_hours_for(self.occurrence_date) or 8.0
		return min(1.0, flt(self.hours) / nh)


def occurrence_missing_by_date(employee, start_date, end_date):
	"""For a period, return two dicts keyed by date:
	  covered[date] = True if any occurrence exists that day (occurrence is authoritative),
	  deduct[date]  = summed missing-days of the UNJUSTIFIED occurrences that day (max 1)."""
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_slip.isoft_salary_slip import normal_hours_for

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
			if r.occurrence_type == "Full Day":
				m = 1.0
			elif r.occurrence_type == "Half Day":
				m = 0.5
			else:
				nh = normal_hours_for(d) or 8.0
				m = min(1.0, flt(r.hours) / nh)
			deduct[d] = min(1.0, deduct.get(d, 0.0) + m)
	return covered, deduct


def auto_flag_unjustified():
	"""Scheduler (daily): flip occurrences still Pending past their 5-day deadline to Unjustified."""
	today = getdate(nowdate())
	names = frappe.get_all(
		"Isoft Attendance Occurrence",
		filters={"status": "Pending Justification", "justification_deadline": ["<", today]},
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


def check_recurrence_alerts():
	"""Scheduler (daily): alert HR when an employee justifies the same reason 4+ times this
	quarter. Deduplicated to one alert per employee+reason per quarter."""
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
		_alert_hr(r, q_start)


def _alert_hr(r, q_start):
	subject = _("Attendance alert: {0} justified '{1}' {2}× this quarter").format(
		r.employee_name or r.employee, r.reason, r.c)
	# One alert per employee+reason per quarter.
	if frappe.db.exists("Notification Log", {"subject": subject, "creation": [">=", q_start]}):
		return
	hr_users = set(frappe.get_all("Has Role", filters={"role": "HR Manager", "parenttype": "User"}, pluck="parent"))
	for u in hr_users:
		if u in ("Administrator", "Guest") or not frappe.db.get_value("User", u, "enabled"):
			continue
		try:
			frappe.get_doc({
				"doctype": "Notification Log", "subject": subject, "for_user": u, "type": "Alert",
				"document_type": "Employee", "document_name": r.employee,
				"email_content": subject,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Isoft HR recurrence alert failed")
	frappe.db.commit()
