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
from isoft_angola_hr.isoft_angola_hr.payroll import christmas, engine
from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import overtime
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf


class IsoftSalarySlip(Document):
	def is_bonus(self):
		return self.run_type == christmas.RUN_CHRISTMAS

	def autoname(self):
		"""Salary slips: ISS-{employee}-{start_date}. Christmas bonus slips share the December
		salary slip's dates, so they are named by year: ISN-{employee}-{year} (-2, -3… after
		a cancelled one that was not amended)."""
		if not self.is_bonus():
			self.name = "ISS-{0}-{1}".format(self.employee, getdate(self.start_date))
			return
		base = "ISN-{0}-{1}".format(self.employee, getdate(self.end_date).year)
		name, n = base, 1
		while frappe.db.exists(self.doctype, name):
			n += 1
			name = "{0}-{1}".format(base, n)
		self.name = name

	def validate(self):
		self.run_type = self.run_type or christmas.RUN_SALARY
		self.validate_belongs_to_run()
		wf.assert_slip_not_locked(self)
		self.validate_no_duplicate_payroll()
		self.validate_enabled_components()
		self.resolve_profile()
		if self.is_bonus():
			self.compute_bonus()
			return
		self.set_working_days()
		self.compute()

	def validate_belongs_to_run(self):
		"""A salary slip exists only inside a payroll run.

		A slip with no Payroll Entry had no approval to check, so the posting and payment
		guards let it through: an HR Manager could create one in the Desk (or over REST),
		submit it, and Finance could post and pay it without anybody having approved a
		payroll. The run is therefore mandatory from the moment the slip is created, it
		must be the run for this company and period, and the link may never be re-pointed
		afterwards — a slip cannot migrate into a run that was approved without it.
		"""
		if not self.payroll_entry:
			frappe.throw(
				_("A salary slip can only be created by a payroll run. Create the Payroll "
				  "Entry for {0} – {1} and calculate it; slips are never entered by hand.").format(
					getdate(self.start_date) if self.start_date else "?",
					getdate(self.end_date) if self.end_date else "?"),
				frappe.PermissionError, title=_("Not Part of a Payroll Run"))

		if not self.is_new():
			before = self.get_doc_before_save() or frappe.get_doc(self.doctype, self.name)
			if before.get("payroll_entry") and before.get("payroll_entry") != self.payroll_entry:
				frappe.throw(
					_("Salary slip {0} belongs to payroll {1} and cannot be moved to {2}.").format(
						self.name, frappe.bold(before.get("payroll_entry")), frappe.bold(self.payroll_entry)),
					frappe.PermissionError, title=_("Not Part of a Payroll Run"))

		run = frappe.db.get_value("Isoft Payroll Entry", self.payroll_entry,
		                          ["company", "start_date", "end_date", "run_type"], as_dict=True)
		if not run:
			frappe.throw(_("Payroll {0} does not exist.").format(self.payroll_entry),
			             frappe.PermissionError, title=_("Not Part of a Payroll Run"))
		if (run.company != self.company or getdate(run.start_date) != getdate(self.start_date)
				or getdate(run.end_date) != getdate(self.end_date)):
			frappe.throw(
				_("Salary slip {0} ({1}, {2} – {3}) does not match payroll {4} ({5}, {6} – {7}).").format(
					self.name or _("new"), self.company, getdate(self.start_date), getdate(self.end_date),
					frappe.bold(self.payroll_entry), run.company, getdate(run.start_date), getdate(run.end_date)),
				frappe.PermissionError, title=_("Not Part of a Payroll Run"))
		if (run.run_type or christmas.RUN_SALARY) != self.run_type:
			frappe.throw(_("Salary slip {0} is a {1} slip but payroll {2} is a {3} run.").format(
				self.name or _("new"), _(self.run_type), frappe.bold(self.payroll_entry),
				_(run.run_type or christmas.RUN_SALARY)),
				frappe.PermissionError, title=_("Not Part of a Payroll Run"))

	def validate_payroll_approved(self):
		"""A slip may only be submitted once its payroll run is approved and lists it.

		Submitting the slip is what turns a calculation into a payroll document, so it is
		the point where approval has to be proven. Without this, calling ``submit`` on the
		slip directly would sidestep the whole approval workflow while the Payroll Entry
		still showed "Calculated". A slip that is not in the run's employee table was not
		part of what the approver signed off, however its Link field reads.
		"""
		if not self.payroll_entry:
			frappe.throw(
				_("Salary slip {0} does not belong to a payroll run and cannot be submitted.").format(
					self.name), frappe.PermissionError, title=_("Not Part of a Payroll Run"))
		state = wf.entry_state(self.payroll_entry)
		if state not in (wf.APPROVED, wf.POSTED, wf.PAYMENT_READY, wf.PAID):
			frappe.throw(
				_("Payroll {0} has not been approved yet (state: {1}). Salary slips may only "
				  "be submitted after approval.").format(
					frappe.bold(self.payroll_entry), _(state or wf.DRAFT)),
				title=_("Payroll Not Approved"),
			)
		wf.assert_slip_in_run(self.payroll_entry, self.name)

	def validate_no_duplicate_payroll(self):
		"""One employee, one submitted salary slip per period — across ALL payroll entries.

		The per-entry check only prevented a second slip inside the same run. Two separate
		Payroll Entries covering the same month would each produce a slip for the same
		employee and both could be submitted, posted and paid. Cancelled slips are ignored,
		so a cancel-and-amend correction still works.

		Salary slips are compared with salary slips only: the Christmas bonus run shares the
		December salary run's period. A bonus slip clashes with another submitted bonus
		slip of the same employee and year.
		"""
		if self.is_bonus():
			on_salary = christmas.salary_slips_with_natal(self.employee, getdate(self.end_date).year)
			if on_salary:
				frappe.throw(
					_("{0} was already paid a Christmas bonus for {1} on salary slip {2}. It "
					  "cannot be paid again by the Christmas bonus run.").format(
						frappe.bold(self.employee_name or self.employee),
						getdate(self.end_date).year, on_salary[0]),
					title=_("Duplicate Christmas Bonus"))
			others = christmas.bonus_slips_in_year(self.employee, getdate(self.end_date).year,
			                                       exclude=self.name)
			if others:
				frappe.throw(
					_("{0} already has a submitted Christmas bonus slip for {1} ({2}). The "
					  "Christmas bonus is paid once a year — cancel {2} first if it is wrong.").format(
						frappe.bold(self.employee_name or self.employee),
						getdate(self.end_date).year, others[0]),
					title=_("Duplicate Christmas Bonus"))
			return
		clash = frappe.db.sql(
			"""select name, payroll_entry from `tabIsoft Salary Slip`
			where employee=%s and docstatus=1 and name!=%s
			  and ifnull(run_type, 'Salary')='Salary'
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
		("overtime_hours", "HEX"),
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
		if self.is_bonus() and not engine.component_enabled(settings, "SNA"):
			frappe.throw(_("The Christmas bonus (Subsídio de Natal) is disabled in Isoft HR "
			               "Settings. Enable it under Settings → Enabled Components first."),
			             title=_("Component Disabled"))
		if not self.is_bonus() and flt(self.subsidio_natal):
			paid = christmas.bonus_slips_in_year(self.employee, getdate(self.end_date).year)
			if paid:
				frappe.throw(
					_("The Christmas bonus for {0} was already paid by {1}. It cannot also be "
					  "paid on a salary slip.").format(getdate(self.end_date).year, paid[0]),
					title=_("Christmas Bonus Already Paid"))

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

		# No December Natal on a salary slip: since November 2026 the Christmas bonus is
		# paid by its own run (payroll/christmas.py), before the December salary.

		# Overtime is entered as HOURS; the amount is calculated (services/overtime.py) and
		# written back below. A draft from before that change carries only an amount, and
		# recomputing it would zero it silently, so it is refused until somebody enters the
		# hours behind it.
		stored = None if self.is_new() else frappe.db.get_value(
			self.doctype, self.name, ["overtime_hours", "overtime_amount"], as_dict=True)
		if (stored and not flt(stored.overtime_hours) and flt(stored.overtime_amount)
				and not self.overtime_hours):
			frappe.throw(_("{0} has an overtime amount but no overtime hours. Overtime is now "
			               "calculated from hours: enter the hours worked.").format(
				frappe.bold(self.employee_name or self.employee)), title=_("Overtime Hours Required"))
		who = self.employee_name or self.employee
		overtime.assert_possible(self.overtime_hours, self.start_date, self.end_date, who=who)

		days = engine_day_inputs(
			self.employee, self.start_date, self.end_date,
			validate_attendance=self.validate_attendance, based_on_timesheet=self.based_on_timesheet,
			total_working_days=flt(self.total_working_days), payment_days=flt(self.payment_days),
			payment_days_override=cint(self.get("payment_days_override")))
		inputs = {
			"productivity_bonus": flt(self.productivity_bonus),
			"overtime_hours": self.overtime_hours,
			"adiantamento": flt(self.adiantamento),
			"ferias_amount": flt(self.subsidio_ferias),
			"natal_amount": flt(self.subsidio_natal),
			"payment_days": days["engine_payment_days"],
			"total_working_days": days["total_working_days"],
			"absent_days": days["absent_days"],
			"absent_hours": days["absent_hours"],
			"late_minutes": days["late_minutes"],
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

		self.overtime_amount = res["overtime"]["total"]
		self.absent_days = res["absences"]["absent_days"]
		self.absent_hours = res["absences"]["absent_hours"]
		self.late_minutes = res["absences"]["late_minutes"]
		self.absence_deduction = res["absences"]["absence_deduction"]
		self.late_deduction = res["absences"]["late_deduction"]
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

	def compute_bonus(self):
		"""A Christmas bonus slip: the bonus, IRT on the bonus alone, no INSS
		(payroll/christmas.py). Nothing of the month — attendance, absences, overtime,
		allowances, advances — belongs on it."""
		profile = frappe.get_doc("Isoft Salary Profile", self.salary_profile)
		settings = engine.get_settings()
		table_name = profile.irt_table or settings.default_irt_table
		self.irt_table = table_name
		year = getdate(self.end_date).year
		months = christmas.bonus_months(
			frappe.db.get_value("Employee", self.employee, "date_of_joining"), year)
		override = None
		if (self.natal_override_reason or "").strip():
			override = flt(self.subsidio_natal)
			ceiling = christmas.full_year_amount(profile.base, settings.natal_rate)
			if override <= 0:
				frappe.throw(_("The Christmas bonus for {0} must be more than zero. To pay "
				               "nothing, leave the employee out of the run.").format(
					frappe.bold(self.employee_name or self.employee)),
					title=_("Invalid Christmas Bonus"))
			if override > ceiling:
				frappe.throw(_("The Christmas bonus for {0} cannot exceed a full year's bonus "
				               "({1}).").format(frappe.bold(self.employee_name or self.employee),
				                                frappe.utils.fmt_money(ceiling, 2)),
				             title=_("Christmas Bonus Too High"))
		res = christmas.compute_bonus(
			profile.base, settings.natal_rate, months, self.posting_date or self.end_date,
			company=self.company,
			irt_table=frappe.get_cached_doc("IRT Table", table_name) if table_name else None,
			override=override)

		self.set("earnings", [])
		for e in res["earnings"]:
			self.append("earnings", e)
		self.set("deductions", [])
		for d in res["deductions"]:
			self.append("deductions", d)
		for field in ("productivity_bonus", "overtime_hours", "overtime_amount", "adiantamento",
		              "subsidio_ferias", "absent_days", "absent_hours", "late_minutes",
		              "absence_deduction", "late_deduction", "advance_recovery", "advance_deferred"):
			self.set(field, 0)
		self.subsidio_natal = res["natal_amount"]
		self.natal_months = res["natal_months"]
		self.natal_base = res["natal_base"]
		for field in ("taxable_income", "gross_pay", "total_deduction", "net_pay",
		              "ss_base", "ss_employee_rate", "ss_employee_amount", "ss_employer_rate",
		              "ss_employer_amount", "employer_cost", "statutory_rate",
		              "irt_bracket_from", "irt_bracket_to", "irt_excess_over", "irt_rate",
		              "irt_parcela_fixa", "irt_amount", "food_exemption_applied",
		              "transport_exemption_applied", "payment_factor"):
			self.set(field, res.get(field))
		self.irt_table = res.get("irt_table") or self.irt_table

	def on_submit(self):
		"""Book the advance recovery against the installments it came from."""
		if flt(self.advance_recovery) and getattr(self, "_advance_plan", None):
			advances.record_recovery(self, flt(self.advance_recovery), self._advance_plan)


def get_holiday_dates(employee, start_date, end_date):
	"""Set of holiday dates governing this employee within the period.

	Resolution lives in one place for the whole application - see
	:mod:`services.work_calendar`. It used to be ERPNext's
	``get_holiday_list_for_employee`` (Employee field, then Company default), which
	ignored the Holiday List declared on the employee's Shift Type even when the
	employee's own field pointed at a list that had expired years earlier.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import work_calendar

	return work_calendar.holiday_dates(employee, start_date, end_date)


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
	else the Employee's Default Shift. None when none is set (caller uses the default calendar).

	Kept as the name payroll calls; the resolution itself lives beside the holiday
	resolution it has to agree with, in :mod:`services.work_calendar`.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import work_calendar

	return work_calendar.shift_on(employee, for_date)


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


#: Weekdays a shift can schedule, used to find its own full-day length.
_WEEK = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def shift_full_day_hours(shift):
	"""How long a WHOLE working day is for this shift — the shift's own longest scheduled
	day, not a global setting.

	This is the reference a day is measured against. Deriving it from the shift means a
	9.5-hour ITEC weekday is a full day because that IS the shift's full day, while its
	4-hour Saturday is visibly a short one. Using ``standard_daily_hours`` here instead is
	exactly what made a 9.5-hour day worth 1.1875 days.
	"""
	from frappe.utils import add_days, getdate

	# a Monday..Sunday window, so every weekday the shift defines is seen once
	probe = getdate("2026-01-05")  # a Monday
	hours = [shift_day_hours(shift, add_days(probe, i)) for i in range(7)]
	full = max(hours) if hours else 0.0
	return full or 8.0


def shift_day_units(shift, day):
	"""What one scheduled date is worth in DAYS: 1 for a full day, 0.5 for a short one.

	The rule the payroll runs on: a full scheduled weekday is one working day whatever its
	clock length, a short day (the 4-hour Saturday) is half a day, and a non-working day is
	nothing. Length decides only whether the day is FULL or SHORT — it never scales a day
	above 1, and it is never divided by ``standard_daily_hours``.
	"""
	hours = shift_day_hours(shift, day)
	if hours <= 0:
		return 0.0
	return 1.0 if hours >= shift_full_day_hours(shift) else HALF_DAY


#: A scheduled day shorter than the shift's own full day counts as half a working day.
HALF_DAY = 0.5


def working_days_basis():
	"""The configured Working Days Basis, resolved in one place."""
	return frappe.get_cached_doc("Isoft HR Settings").get("working_days_basis") or "Auto (Holiday List)"


#: The only basis that measures in hour-equivalents. Everything else counts DAYS, and a
#: day basis must never divide by an hours figure — that is what made a 9.5-hour shift
#: worth 1.1875 "days" and put 28.625 in a column headed Paid/Total Days.
HOUR_EQUIVALENT_BASIS = "Auto (Holiday List)"


def counts_in_days(basis=None):
	"""True when the configured basis measures whole working DAYS rather than hours."""
	return (basis or working_days_basis()) != HOUR_EQUIVALENT_BASIS


def eligible_range(employee, start, end):
	"""Narrow a payroll period to the dates the employee is actually employed for.

	Joining and leaving mid-period previously counted the whole period: the loop walked
	start..end regardless, so somebody who joined on the 20th was credited with a full
	month of working days and, with nothing to deduct them, paid for it.
	"""
	row = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"],
	                          as_dict=True) or {}
	joined, left = row.get("date_of_joining"), row.get("relieving_date")
	if joined and getdate(joined) > getdate(start):
		start = getdate(joined)
	if left and getdate(left) < getdate(end):
		end = getdate(left)
	return getdate(start), getdate(end)


def day_value(shift, day, std_hours, basis=None):
	"""What one calendar date is worth, in the unit the configured basis counts in.

	* "Auto (Holiday List)" counts standard-day EQUIVALENTS: a 9.5h day is worth 1.1875
	  and a 4h Saturday 0.5, so both the month's total and every absence follow hours.
	* "Calendar Working Days" and "Standard (Fixed)" count DAYS: a full scheduled day is
	  1 whatever its clock length, a short day (the 4-hour Saturday) is 0.5, and a
	  non-working day is 0. A month of 22 weekdays and 5 Saturdays is therefore 24.5 days
	  — not 28.625 (hours dressed as days) and not 27 (a half-day Saturday counted whole).

	The period total and every absence deduction are both measured with this function,
	and they must be. Counting the total in days while deducting absences in
	standard-day equivalents divides two different units and silently mis-prices every
	absence: a 1.5h absence cost 0.72%% of salary under the fixed basis against 0.67%%
	under the auto basis, purely because of the mismatch.
	"""
	if not counts_in_days(basis):
		return shift_day_weight(shift, day, std_hours)
	# A full scheduled weekday is one day whatever its clock length; a short day is half
	# a day. Nothing here divides by standard_daily_hours, so this basis does not depend
	# on that setting at all.
	return shift_day_units(shift, day)


def shift_hours_for(employee, for_date):
	"""Convenience: normal hours for an employee on a date, resolving their shift."""
	return shift_day_hours(employee_shift(employee, for_date), for_date)


def compute_working_days(employee, start_date, end_date, validate_attendance=0, based_on_timesheet=0):
	"""Return (total_working_days, payment_days).

	total_working_days (TWD) depends on the "Working Days Basis" setting:
	    * Standard (Fixed): a fixed number from Settings (e.g. 30/26).
	    * Calendar Working Days: a count of DAYS the shift schedules, each worth 1 whatever
	      its length (a 4h Saturday counts as a day). This is the figure HR expects to read.
	    * Auto: sum of each non-holiday day's weight from the employee's Shift Type schedule
	      (a full weekday = 1, a 4h Saturday = 0.5, a 9.5h day = 1.1875, a non-working
	      day = 0), so the total is in standard-day equivalents rather than days.
	  Whichever is chosen, absences are deducted in the SAME unit — see day_value().

	payment_days (paid days):
	    * based_on_timesheet: logged timesheet hours / standard daily hours (capped at TWD).
	    * validate_attendance: TWD minus each day's shortfall (Absent = full day weight,
	      Half Day = half, unpaid leave = full). A Present day short of its scheduled hours
	      is pro-rated ONLY under the hour-equivalent basis; under a day basis a partial
	      absence must be recorded as a Half Day or an Attendance Occurrence.
	    * otherwise: full TWD.

	Both figures cover only the dates the employee is employed for — joining or leaving
	mid-period shortens the range rather than paying for the whole of it.
	"""
	settings = frappe.get_cached_doc("Isoft HR Settings")
	basis = settings.get("working_days_basis") or "Auto (Holiday List)"
	# Only the hour-equivalent basis divides by this; a day basis never touches it.
	std = flt(settings.get("standard_daily_hours")) or 8
	# Count only the dates this employee is employed for.
	start, end = eligible_range(employee, getdate(start_date), getdate(end_date))
	if start > end:                      # employed for none of this period
		return 0.0, 0.0
	shift = employee_shift(employee, end)  # resolved once for the period
	if basis == "Standard (Fixed)":
		twd = flt(settings.get("standard_working_days")) or 30.0
	else:
		holidays = get_holiday_dates(employee, start, end)
		twd = 0.0
		d = start
		while d <= end:
			if d not in holidays:  # value is 0 on non-working days (per the shift)
				twd += day_value(shift, d, std, basis)
			d = add_days(d, 1)

	if cint(based_on_timesheet):
		hours = frappe.db.sql(
			"""select coalesce(sum(total_hours),0) from `tabTimesheet`
			where employee=%s and docstatus=1 and start_date>=%s and end_date<=%s""",
			(employee, start, end),
		)[0][0]
		return twd, min(twd, flt(hours) / std)

	if cint(validate_attendance):
		# Paid days, for display: the total less the absent days HR's document prices
		# (Absent / Unjustified full day = 1, Half Day = 0,5, unpaid leave = 1). Hours and
		# late minutes are reported beside it, not folded into a fraction of a day. The
		# money comes from services/absences.py through the engine, never from this count.
		return twd, max(0.0, twd - absence_inputs(employee, start, end)["days"])

	return twd, twd


def absence_inputs(employee, start_date, end_date):
	"""What the period's attendance costs, counted the way HR's "Absence and Late Arrival
	Deductions" (October 2026) prices it — see services/absences.py.

	* ``days``: Attendance ``Absent`` or an Unjustified full-day occurrence = 1, a Half
	  Day = 0,5, a day of unpaid leave (``is_lwp``) = 1. Only dates the employee's shift
	  schedules count; an "Absent" on a Sunday deducts nothing.
	* ``hours``: Unjustified partial absences and early exits (the occurrence's hours).
	* ``late_minutes``: Unjustified lateness, accumulated (the occurrence's hours × 60).

	An Attendance Occurrence is authoritative for the date it covers: Justified or still
	Pending Justification means no deduction for that date, whatever Attendance says.
	Only the employed part of the period is read (eligible_range).
	"""
	out = {"days": 0.0, "hours": 0.0, "late_minutes": 0.0, "detail": []}
	start, end = eligible_range(employee, getdate(start_date), getdate(end_date))
	if start > end:
		return out
	shift = employee_shift(employee, end)
	lwp_types = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))
	att = {}
	for r in frappe.db.sql(
			"""select attendance_date, status, leave_type from `tabAttendance`
			where employee=%s and docstatus=1 and attendance_date between %s and %s""",
			(employee, start, end), as_dict=True):
		d = getdate(r.attendance_date)
		if shift_day_hours(shift, d) <= 0:
			continue
		if r.status == "Absent":
			m = 1.0
		elif r.status == "Half Day":
			m = 0.5
		elif r.status == "On Leave" and r.leave_type in lwp_types:
			m = 1.0
		else:
			m = 0.0
		att[d] = max(att.get(d, 0.0), m)
	covered = set()
	for o in frappe.get_all("Isoft Attendance Occurrence",
	                        filters={"employee": employee,
	                                 "occurrence_date": ["between", [start, end]]},
	                        fields=["occurrence_date", "occurrence_type", "hours", "status"],
	                        order_by="occurrence_date"):
		d = getdate(o.occurrence_date)
		covered.add(d)
		if o.status != "Unjustified" or shift_day_hours(shift, d) <= 0:
			continue
		if o.occurrence_type == "Full Day":
			out["days"] += 1.0
			out["detail"].append({"date": str(d), "kind": "day", "value": 1.0})
		elif o.occurrence_type == "Half Day":
			out["days"] += 0.5
			out["detail"].append({"date": str(d), "kind": "day", "value": 0.5})
		elif o.occurrence_type == "Lateness":
			out["late_minutes"] += flt(o.hours) * 60.0
			out["detail"].append({"date": str(d), "kind": "late_minutes", "value": flt(o.hours) * 60.0})
		else:                                   # Partial Absence, Early Exit
			out["hours"] += flt(o.hours)
			out["detail"].append({"date": str(d), "kind": "hours", "value": flt(o.hours)})
	for d in sorted(att):
		if d in covered or not att[d]:
			continue
		out["days"] += att[d]
		out["detail"].append({"date": str(d), "kind": "day", "value": att[d]})
	out["days"], out["hours"] = flt(out["days"], 4), flt(out["hours"], 2)
	out["late_minutes"] = flt(out["late_minutes"], 2)
	return out


def engine_day_inputs(employee, start_date, end_date, validate_attendance=0, based_on_timesheet=0,
                      total_working_days=None, payment_days=None, payment_days_override=0):
	"""The day-related inputs of engine.compute_slip, built the same way for the salary
	slip and the payroll preview so the two cannot disagree.

	Attendance-based: the absences go to the engine as days / hours / minutes and the
	paid days it receives equal the total (no proration on top of the priced absences).
	A paid-days override by HR, or a timesheet-based slip, keeps the proportional paid ÷
	total factor and passes no absences, so nothing is deducted twice.
	"""
	if total_working_days is None or payment_days is None:
		total_working_days, payment_days = compute_working_days(
			employee, start_date, end_date, validate_attendance=validate_attendance,
			based_on_timesheet=based_on_timesheet)
	out = {"total_working_days": flt(total_working_days), "payment_days": flt(payment_days),
	       "engine_payment_days": flt(payment_days),
	       "absent_days": 0.0, "absent_hours": 0.0, "late_minutes": 0.0, "detail": []}
	if cint(validate_attendance) and not cint(based_on_timesheet) and not cint(payment_days_override):
		a = absence_inputs(employee, start_date, end_date)
		out.update(engine_payment_days=flt(total_working_days), absent_days=a["days"],
		           absent_hours=a["hours"], late_minutes=a["late_minutes"], detail=a["detail"])
	return out


def attendance_overtime_hours(employee, start_date, end_date):
	"""Approved overtime hours in a period: the sum of ``custom_overtime_hours`` on
	submitted Attendance. Hours only. What they are worth is services/overtime.py, the
	one overtime rule, priced per month on the basic salary."""
	return flt(frappe.db.sql(
		"""select sum(custom_overtime_hours) from `tabAttendance`
		where employee=%s and docstatus=1 and ifnull(custom_overtime_hours,0) > 0
		and attendance_date between %s and %s""",
		(employee, getdate(start_date), getdate(end_date)))[0][0], 2)
