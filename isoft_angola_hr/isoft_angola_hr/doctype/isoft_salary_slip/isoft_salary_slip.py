# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, flt, getdate

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
	assert_single_profile_for_period,
)
from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf


class IsoftSalarySlip(Document):
	def validate(self):
		wf.assert_slip_not_locked(self)
		self.validate_no_duplicate_payroll()
		self.validate_enabled_components()
		self.resolve_profile()
		self.set_working_days()
		self.compute()

	def validate_payroll_approved(self):
		"""A slip belonging to a payroll run may only be submitted once that run is approved.

		Submitting the slip is what turns a calculation into a payroll document, so it is
		the point where approval has to be proven. Without this, calling ``submit`` on the
		slip directly would sidestep the whole approval workflow while the Payroll Entry
		still showed "Calculated".
		"""
		if not self.payroll_entry:
			return
		state = wf.entry_state(self.payroll_entry)
		if state not in (wf.APPROVED, wf.POSTED, wf.PAYMENT_READY, wf.PAID):
			frappe.throw(
				_("O processamento salarial {0} ainda não foi aprovado (estado: {1}). Os recibos "
				  "só podem ser submetidos depois da aprovação.").format(
					frappe.bold(self.payroll_entry), _(state or wf.DRAFT)),
				title=_("Payroll Not Approved"),
			)

	def validate_no_duplicate_payroll(self):
		"""One employee, one submitted salary slip per period — across ALL payroll entries.

		The per-entry check only prevented a second slip inside the same run. Two separate
		Payroll Entries covering the same month would each produce a slip for the same
		employee and both could be submitted, posted and paid. Cancelled slips are ignored,
		so a cancel-and-amend correction still works.
		"""
		clash = frappe.db.sql(
			"""select name, payroll_entry from `tabIsoft Salary Slip`
			where employee=%s and docstatus=1 and name!=%s
			  and start_date <= %s and end_date >= %s limit 1""",
			(self.employee, self.name or "", getdate(self.end_date), getdate(self.start_date)),
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("{0} already has a submitted Salary Slip ({1}) covering {2} to {3}. An employee "
				  "cannot be paid twice for the same period — cancel {1} first if it is wrong.").format(
					frappe.bold(self.employee_name or self.employee), clash[0].name,
					getdate(self.start_date), getdate(self.end_date)),
				title=_("Duplicate Payroll for Employee"),
			)

	#: Monthly input fields and the Settings checkbox that governs each.
	_INPUT_TOGGLES = (
		("productivity_bonus", "PPD"),
		("overtime_amount", "HEX"),
		("adiantamento", "ADT"),
		("subsidio_ferias", "SFE"),
		("subsidio_natal", "SNA"),
	)

	def validate_enabled_components(self):
		"""Refuse a NEW amount for a component the site has switched off.

		The check deliberately only looks at values that are being introduced or changed.
		Zeroing an amount that already exists would rewrite payroll that was calculated
		while the component was enabled — the toggle governs what may be entered from now
		on, not what an existing record is worth.
		"""
		settings = engine.get_settings()
		before = self.get_doc_before_save()
		blocked = []
		for field, abbr in self._INPUT_TOGGLES:
			amount = flt(self.get(field))
			if not amount or engine.component_enabled(settings, abbr):
				continue
			if before is not None and flt(before.get(field)) == amount:
				continue  # unchanged historical value — left exactly as it is
			blocked.append(engine.COMPONENTS[abbr]["name"])
		if blocked:
			frappe.throw(
				_("These salary components are disabled in Isoft HR Settings and cannot be "
				  "paid: {0}. Enable them under Settings → Enabled Components, or clear the "
				  "amount.").format(", ".join(blocked)),
				title=_("Component Disabled"),
			)

	def on_update_after_submit(self):
		wf.assert_slip_not_locked(self)

	def after_insert(self):
		"""An amendment replaces its original in the payroll entry it belongs to.

		Otherwise the entry keeps pointing at the cancelled slip, so the corrected payroll
		would never be posted, exported or reported — the cancelled version would.
		"""
		if not self.amended_from or not self.payroll_entry:
			return
		frappe.db.sql(
			"""update `tabIsoft Payroll Employee` set salary_slip=%s
			where parenttype='Isoft Payroll Entry' and parent=%s and salary_slip=%s""",
			(self.name, self.payroll_entry, self.amended_from))

	def before_submit(self):
		"""A slip that cannot be paid must not become an approved payroll document.
		It is still allowed to exist as a draft so HR can see and correct the problem."""
		self.validate_payroll_approved()
		if flt(self.net_pay) < 0:
			frappe.throw(
				_("Net pay for {0} is {1}. Deductions exceed remuneration, so this slip "
				  "cannot be submitted. Reduce the Adiantamento or other deductions first.").format(
					frappe.bold(self.employee_name or self.employee), flt(self.net_pay)),
				title=_("Negative Net Pay"),
			)

	def on_cancel(self):
		"""A slip may only be cancelled once nothing of it remains in the ledger.

		Entries are now submitted, so the resolution is to CANCEL them (which reverses
		their GL Entries), not to delete them — a submitted Journal Entry cannot be
		deleted, so the previous "delete it first" instruction was a dead end.

		A slip inside an approved payroll may only be cancelled through the payroll
		correction process, so a single slip cannot be quietly pulled out of an
		approved run.
		"""
		wf.assert_slip_not_locked(self)
		# Give the advance installments back — the deduction is being undone.
		advances.release_recovery(self)
		for field, label in (("journal_entry", _("accrual Journal Entry")),
		                     ("payment_entry", _("payment Journal Entry"))):
			name = self.get(field)
			if not name:
				continue
			docstatus = frappe.db.get_value("Journal Entry", name, "docstatus")
			if docstatus is not None and cint(docstatus) != 2:
				frappe.throw(
					_("Cannot cancel this salary slip: its {0} {1} is still in the ledger. "
					  "Cancel {1} first — that reverses its GL entries.").format(
						label, frappe.bold(name))
				)

	def resolve_profile(self):
		if not self.salary_profile:
			# Raises when two profiles share the latest effective date, and when the
			# salary changes part-way through the period (which cannot be prorated).
			prof = assert_single_profile_for_period(
				self.employee, self.start_date, self.end_date or self.posting_date,
				company=self.company, employee_name=self.employee_name,
			)
			if not prof:
				frappe.throw(
					_("No Isoft Salary Profile found for {0} effective on or before {1}.").format(
						frappe.bold(self.employee_name or self.employee), self.end_date
					)
				)
			self.salary_profile = prof.name

	def set_working_days(self):
		"""Always recompute from attendance/timesheet unless HR explicitly overrode the
		paid days. Previously ``if not self.payment_days`` meant that once a value was
		stored it was never refreshed, so correcting attendance had no effect on pay."""
		twd, pay_days = compute_working_days(
			self.employee, self.start_date, self.end_date,
			validate_attendance=self.validate_attendance,
			based_on_timesheet=self.based_on_timesheet,
		)
		self.total_working_days = twd
		if cint(self.get("payment_days_override")):
			return
		self.payment_days = pay_days

	def compute(self):
		profile = frappe.get_doc("Isoft Salary Profile", self.salary_profile)
		settings = engine.get_settings()
		if not profile.irt_table:
			profile.irt_table = settings.default_irt_table
		self.irt_table = profile.irt_table

		# Auto-fill the December Natal default on a brand-new slip when it wasn't set
		# (e.g. a slip created directly, not via the payroll preview). HR can still edit it.
		if self.is_new() and self.subsidio_natal is None:
			self.subsidio_natal = engine.default_natal(
				profile.base, settings.natal_rate,
				frappe.db.get_value("Employee", self.employee, "date_of_joining"), self.end_date,
				settings.get("natal_payment_month"),
			)

		inputs = {
			"productivity_bonus": flt(self.productivity_bonus),
			"overtime_amount": flt(self.overtime_amount),
			"adiantamento": flt(self.adiantamento),
			"ferias_amount": flt(self.subsidio_ferias),
			"natal_amount": flt(self.subsidio_natal),
			"payment_days": flt(self.payment_days),
			"total_working_days": flt(self.total_working_days),
		}
		inputs["start_date"] = self.start_date
		inputs["end_date"] = self.end_date
		# Salary advance recovery due in this period. Computed here rather than typed by
		# HR, so the deduction and the advance's outstanding balance can never disagree.
		due, self._advance_plan = advances.due_recovery(
			self.employee, self.start_date, self.end_date, exclude_slip=self.name)
		inputs["advance_recovery"] = due
		res = engine.compute_slip(profile, inputs, settings=settings, on_date=self.end_date,
		                          employee=self.employee_name or self.employee)

		self.set("earnings", [])
		for e in res["earnings"]:
			self.append("earnings", e)
		self.set("deductions", [])
		for d in res["deductions"]:
			self.append("deductions", d)

		self.taxable_income = res["taxable_income"]
		self.gross_pay = res["gross_pay"]
		self.total_deduction = res["total_deduction"]
		self.net_pay = res["net_pay"]

		# Statutory calculation snapshot — kept so the slip stays explainable after the
		# IRT table or the contribution rates change.
		for field in ("ss_base", "ss_employee_rate", "ss_employee_amount", "ss_employer_rate",
		              "ss_employer_amount", "employer_cost", "statutory_rate",
		              "irt_bracket_from", "irt_bracket_to", "irt_excess_over", "irt_rate",
		              "irt_parcela_fixa", "irt_amount", "food_exemption_applied",
		              "transport_exemption_applied", "payment_factor"):
			self.set(field, res.get(field))
		self.irt_table = res.get("irt_table") or self.irt_table
		self.advance_recovery = res.get("advance_recovered")
		self.advance_deferred = res.get("advance_deferred")
		if flt(self.advance_deferred):
			# Never silent: the employee keeps owing it and somebody has to know.
			frappe.msgprint(
				_("{0} of the salary advance could not be recovered from {1} without driving "
				  "net pay below zero. It remains outstanding and will be taken from a later "
				  "period.").format(flt(self.advance_deferred, 2),
				                    self.employee_name or self.employee),
				title=_("Advance Partially Recovered"), indicator="orange")

	def on_submit(self):
		"""Book the advance recovery against the installments it came from."""
		if flt(self.advance_recovery) and getattr(self, "_advance_plan", None):
			advances.record_recovery(self, flt(self.advance_recovery), self._advance_plan)


def get_holiday_dates(employee, start_date, end_date):
	"""Set of holiday dates for the employee's holiday list within the period."""
	from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return set()
	rows = frappe.db.sql(
		"""select holiday_date from `tabHoliday`
		where parent=%s and parenttype='Holiday List'
		and holiday_date between %s and %s""",
		(holiday_list, getdate(start_date), getdate(end_date)),
	)
	return {getdate(r[0]) for r in rows}


def get_holiday_count(employee, start_date, end_date):
	return len(get_holiday_dates(employee, start_date, end_date))


# Working hours per day come from the employee's Shift Type weekly schedule (some employees
# work Saturdays, others don't; Saturday may be a short 4h day). When no shift is configured,
# fall back to the Angola default calendar: Mon–Fri 8h, Sat 4h, Sun off.
def _default_weekday_hours(day):
	wd = getdate(day).weekday()  # Mon=0 … Sat=5, Sun=6
	return 0.0 if wd == 6 else (4.0 if wd == 5 else 8.0)


def _td_hours(start, end):
	"""Hours between two Time values (timedeltas), handling an overnight wrap."""
	if start is None or end is None:
		return 0.0
	secs = end.total_seconds() - start.total_seconds()
	if secs < 0:
		secs += 24 * 3600
	return flt(secs / 3600.0, 2)


def employee_shift(employee, for_date):
	"""The Shift Type governing an employee on a date — a covering Shift Assignment if any,
	else the Employee's Default Shift. None when none is set (caller uses the default calendar)."""
	d = getdate(for_date)
	sa = frappe.db.sql(
		"""select shift_type from `tabShift Assignment`
		where employee=%s and docstatus=1 and start_date<=%s
		and (end_date is null or end_date>=%s) and ifnull(shift_type,'')!=''
		order by start_date desc limit 1""",
		(employee, d, d),
	)
	if sa and sa[0][0]:
		return sa[0][0]
	return frappe.db.get_value("Employee", employee, "default_shift")


def shift_day_hours(shift, day):
	"""Normal working hours a Shift Type prescribes for a date (0 on a non-working weekday).
	Falls back to the default calendar when there is no shift or no usable times."""
	if not shift:
		return _default_weekday_hours(day)
	# This app owns the weekday schedule; ERPNext no longer carries these helpers.
	from isoft_angola_hr.isoft_angola_hr.shift_weekday import get_weekday_shift_hours

	start, end, working = get_weekday_shift_hours(shift, day)
	if not working:
		return 0.0
	if start is None or end is None:
		st = frappe.get_cached_doc("Shift Type", shift)
		start = start if start is not None else st.start_time
		end = end if end is not None else st.end_time
	h = _td_hours(start, end)
	return h if h > 0 else _default_weekday_hours(day)


def shift_day_weight(shift, day, std_hours):
	"""How much of a standard working day this date is worth for the shift: shift hours ÷
	standard daily hours (0 on non-working days). A 4h Saturday = 0.5, a non-working day = 0,
	a full 8h weekday = 1."""
	nh = shift_day_hours(shift, day)
	return (nh / (flt(std_hours) or 8.0)) if nh > 0 else 0.0


def shift_hours_for(employee, for_date):
	"""Convenience: normal hours for an employee on a date, resolving their shift."""
	return shift_day_hours(employee_shift(employee, for_date), for_date)


def compute_working_days(employee, start_date, end_date, validate_attendance=0, based_on_timesheet=0):
	"""Return (total_working_days, payment_days).

	total_working_days (TWD) depends on the "Working Days Basis" setting:
	    * Standard (Fixed): a fixed number from Settings (e.g. 30/26).
	    * Auto: sum of each non-holiday day's weight from the employee's Shift Type schedule
	      (a full weekday = 1, a 4h Saturday = 0.5, a non-working day = 0).

	payment_days (paid days):
	    * based_on_timesheet: logged timesheet hours / standard daily hours (capped at TWD).
	    * validate_attendance: TWD minus each day's shortfall (Absent = full day weight,
	      Half Day = half, unpaid leave = full, partial = missing-hours share). Paid leave is
	      not deducted.
	    * otherwise: full TWD.
	"""
	start, end = getdate(start_date), getdate(end_date)
	settings = frappe.get_cached_doc("Isoft HR Settings")
	basis = settings.get("working_days_basis") or "Auto (Holiday List)"
	std = flt(settings.get("standard_daily_hours")) or 8
	shift = employee_shift(employee, end)  # resolved once for the period
	if basis == "Standard (Fixed)":
		twd = flt(settings.get("standard_working_days")) or 30.0
	else:
		holidays = get_holiday_dates(employee, start, end)
		twd = 0.0
		d = start
		while d <= end:
			if d not in holidays:  # weight is 0 on non-working days (per the shift)
				twd += shift_day_weight(shift, d, std)
			d = add_days(d, 1)

	if cint(based_on_timesheet):
		hours = frappe.db.sql(
			"""select coalesce(sum(total_hours),0) from `tabTimesheet`
			where employee=%s and docstatus=1 and start_date>=%s and end_date<=%s""",
			(employee, start, end),
		)[0][0]
		return twd, min(twd, flt(hours) / std)

	if cint(validate_attendance):
		# Hours-aware paid days: each day's shortfall is weighted by that date's shift day
		# (full day = shift day-weight; partial = missing-hours share of it).
		from isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence.isoft_attendance_occurrence import (
			occurrence_missing_by_date,
		)

		rows = frappe.db.sql(
			"""select attendance_date, status, leave_type, working_hours from `tabAttendance`
			where employee=%s and docstatus=1 and attendance_date between %s and %s""",
			(employee, start, end), as_dict=True,
		)
		lwp_types = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))
		att = {}  # date -> missing days from Attendance
		for r in rows:
			d = getdate(r.attendance_date)
			nh = shift_day_hours(shift, d)
			if nh <= 0:
				continue  # non-working day for this employee
			w = shift_day_weight(shift, d, std)  # value of a full day of THIS date
			if r.status == "Absent":
				m = w
			elif r.status == "Half Day":
				m = w / 2.0
			elif r.status == "On Leave":
				m = w if r.leave_type in lwp_types else 0.0
			elif r.status in ("Present", "Work From Home") and r.working_hours and flt(r.working_hours) < nh:
				m = (nh - flt(r.working_hours)) / nh * w
			else:
				m = 0.0
			att[d] = max(att.get(d, 0.0), m)

		# Attendance Occurrences are authoritative for the days they cover: a Justified/Pending
		# occurrence means no deduction; an Unjustified one deducts its missing portion.
		covered, occ_deduct = occurrence_missing_by_date(employee, start, end)
		missing = 0.0
		for d in set(att) | set(covered):
			missing += occ_deduct.get(d, 0.0) if d in covered else att.get(d, 0.0)
		return twd, max(0, twd - missing)

	return twd, twd


def attendance_overtime_amount(employee, base, working_days, start_date, end_date, multiplier):
	"""Overtime pay from logged Attendance overtime hours:
	Σ_day (daily_salary ÷ normal_hours(day)) × multiplier × overtime_hours(day),
	where daily_salary = base ÷ period working days and normal_hours come from the shift.
	Rest/non-working days fall back to the default weekday hours as the divisor."""
	twd = flt(working_days)
	if not twd:
		return 0.0
	daily = flt(base) / twd
	mult = flt(multiplier) or 2.0
	shift = employee_shift(employee, end_date)
	rows = frappe.db.sql(
		"""select attendance_date, custom_overtime_hours from `tabAttendance`
		where employee=%s and docstatus=1 and ifnull(custom_overtime_hours,0) > 0
		and attendance_date between %s and %s""",
		(employee, getdate(start_date), getdate(end_date)), as_dict=True,
	)
	total = 0.0
	for r in rows:
		nh = shift_day_hours(shift, r.attendance_date) or _default_weekday_hours(r.attendance_date) or 8.0
		total += (daily / nh) * mult * flt(r.custom_overtime_hours)
	return flt(total, 2)
