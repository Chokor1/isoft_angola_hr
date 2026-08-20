# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Per-employee salary definition, versioned by effective date.

RESOLUTION RULE
---------------
The profile governing a payroll date is the one with the LATEST ``from_date`` that is
on or before that date and whose ``to_date`` (when set) has not passed.

If two profiles share that latest ``from_date`` the salary is genuinely ambiguous.
Previously the query was ``order by from_date desc limit 1`` with no tiebreak, so the
row returned — and therefore the amount the employee was paid — depended on arbitrary
database ordering. Payroll now refuses to calculate such an employee instead of
silently picking one.

The document name must never be relied on as the business constraint: ``autoname`` is
evaluated once at insert, but ``from_date`` stayed editable afterwards, so names and
data diverge. Uniqueness is enforced here, in validation.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now

# Amount fields whose change is worth recording in the salary history.
_TRACKED = ("base", "food_allowance", "transport_allowance", "family_allowance")


class IsoftSalaryProfile(Document):
	def validate(self):
		self.validate_employee_exists()
		self.set_company()
		self.validate_dates()
		self.validate_no_duplicate_effective_date()
		self.validate_no_overlap()
		self.validate_effective_date_not_locked()

	def validate_employee_exists(self):
		"""Frappe's own link validation does not catch a missing Employee here.

		``BaseDocument.get_invalid_links`` fetches the link target with
		``get_value(..., as_dict=True)``, which returns None for a non-existent record;
		the surrounding ``if values:`` is then falsy and the invalid link is never
		recorded. Any Link field that also carries a fetch_from (this one fetches
		employee_name and company) is affected. A salary profile pointing at nobody
		would silently enter payroll configuration, so it is checked explicitly.
		"""
		if not self.employee or not frappe.db.exists("Employee", self.employee):
			frappe.throw(
				_("Employee {0} does not exist. A Salary Profile must belong to a real "
				  "employee.").format(frappe.bold(self.employee or "")),
				frappe.LinkValidationError,
			)

	def set_company(self):
		"""Company was never populated by the dashboard API, which broke company
		filtering and made cross-company resolution possible."""
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")

	def validate_dates(self):
		if not self.from_date:
			frappe.throw(_("Set the From Date of the Salary Profile."))
		if self.get("to_date") and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To Date ({0}) cannot be before From Date ({1}).").format(
				self.to_date, self.from_date))

	def validate_no_duplicate_effective_date(self):
		"""Two profiles for one employee starting on the same day are always ambiguous.

		The name must only be excluded for an EXISTING record. Frappe assigns the
		autoname before validation runs, and the autoname is derived from employee +
		from_date, so a new duplicate arrives already carrying the existing record's
		name — excluding it would hide exactly the clash being looked for.
		"""
		clash = frappe.db.sql(
			"""select name, base from `tabIsoft Salary Profile`
			where employee=%s and from_date=%s and name!=%s limit 1""",
			(self.employee, getdate(self.from_date), "" if self.is_new() else self.name),
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("Salary Profile {0} already takes effect on {1} for {2} (base {3}). "
				  "Two profiles cannot start on the same date — edit the existing one, or "
				  "give this one a different From Date.").format(
					frappe.bold(clash[0].name), self.from_date,
					self.employee_name or self.employee, flt(clash[0].base))
			)

	def validate_no_overlap(self):
		"""No two profiles for one employee may cover the same day. Ever.

		The previous version returned early when ``to_date`` was empty, on the theory
		that stacking open-ended profiles by From Date was normal versioning and "the
		latest one wins". It is not, and it does not: an open profile from January and
		another open profile from July both claim July onwards, and payroll cannot tell
		which salary applies. Three employees on the live site reached exactly that state
		through this hole.

		Every profile is now treated as the closed-open range
		``[from_date, to_date or forever)`` and any intersection is refused.

		GRANDFATHERING. A record that ALREADY overlapped before this save, and whose
		dates are not being changed, is allowed through with a warning. Blocking it
		outright would stop HR correcting an amount on one of the existing broken
		records without first restructuring their history — and the readiness report
		keeps naming them until somebody does. New writes, and any change to a date,
		are refused.
		"""
		clash = self._overlapping(self.from_date, self.to_date)
		if not clash:
			return

		if not self.is_new():
			before = self.get_doc_before_save()
			dates_unchanged = bool(before) and \
				getdate(before.from_date) == getdate(self.from_date) and \
				(before.get("to_date") or None) == (self.get("to_date") or None)
			if dates_unchanged and self._overlapping(before.from_date, before.get("to_date")):
				frappe.msgprint(
					_("This Salary Profile overlaps {0} ({1} to {2}). The overlap already "
					  "existed and has not been made worse by this change, so it was "
					  "saved — but payroll cannot resolve which salary applies until one "
					  "of the two is closed with a To Date.").format(
						frappe.bold(clash.name), clash.from_date, clash.to_date or _("open")),
					title=_("Existing overlap"), indicator="orange")
				return

		frappe.throw(
			_("Salary Profile {0} already covers {1} to {2} for {3}.<br><br>"
			  "Two profiles cannot apply on the same day. Close the earlier one by "
			  "setting its To Date to the day before this one starts — or, for a normal "
			  "salary increase, use <b>Salary Changes</b>, which does exactly that "
			  "automatically once the change is approved.").format(
				frappe.bold(clash.name), clash.from_date, clash.to_date or _("open-ended"),
				self.employee_name or self.employee),
			title=_("Overlapping Salary Profile"),
		)

	def _overlapping(self, from_date, to_date):
		"""Another profile for this employee intersecting ``[from_date, to_date)``."""
		if not from_date:
			return None
		rows = frappe.db.sql(
			"""select name, from_date, to_date from `tabIsoft Salary Profile`
			where employee = %s and name != %s
			  and from_date <= %s
			  and ifnull(to_date, '2999-12-31') >= %s
			order by from_date limit 1""",
			(self.employee, "" if self.is_new() else self.name,
			 getdate(to_date) if to_date else getdate("2999-12-31"), getdate(from_date)),
			as_dict=True,
		)
		return rows[0] if rows else None

	def validate_effective_date_not_locked(self):
		"""Moving the effective date of a profile that already produced submitted payroll
		would retroactively change which salary applied to a closed period."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before or getdate(before.from_date) == getdate(self.from_date):
			return
		used_by = frappe.db.count("Isoft Salary Slip", {"salary_profile": self.name, "docstatus": 1})
		if used_by:
			frappe.throw(
				_("The From Date of {0} cannot be changed: it has already been used by {1} "
				  "submitted salary slip(s). Create a new Salary Profile instead so that "
				  "historical payroll stays reproducible.").format(frappe.bold(self.name), used_by)
			)

	def on_update(self):
		"""Log a Salary History entry whenever a tracked amount changes (and on creation),
		so the pay progression of each employee is preserved even when the profile is edited."""
		before = self.get_doc_before_save()
		changed = before is None or any(flt(before.get(f)) != flt(self.get(f)) for f in _TRACKED)
		if not changed:
			return
		frappe.get_doc({
			"doctype": "Isoft Salary History",
			"employee": self.employee,
			"salary_profile": self.name,
			"change_date": now(),
			"changed_by": frappe.session.user,
			"change_type": "Created" if before is None else "Updated",
			"base": flt(self.base),
			"food_allowance": flt(self.food_allowance),
			"transport_allowance": flt(self.transport_allowance),
			"family_allowance": flt(self.family_allowance),
		}).insert(ignore_permissions=True)


def get_effective_profiles(employee, on_date, company=None):
	"""Every profile that could govern ``on_date``, most recent effective date first."""
	conditions = ["employee=%s", "from_date <= %s", "ifnull(to_date, '2999-12-31') >= %s"]
	values = [employee, getdate(on_date), getdate(on_date)]
	if company:
		conditions.append("ifnull(company,'') in ('', %s)")
		values.append(company)
	return frappe.db.sql(
		"""select name, from_date, base, company from `tabIsoft Salary Profile`
		where {0} order by from_date desc, creation desc""".format(" and ".join(conditions)),
		values, as_dict=True,
	)


def get_active_profile(employee, on_date, company=None, employee_name=None):
	"""The single Salary Profile governing a date, or None when the employee has none.

	Raises when two profiles share the latest effective date: that is an unresolved
	data conflict and payroll must not guess which salary to pay.
	"""
	rows = get_effective_profiles(employee, on_date, company=company)
	if not rows:
		return None

	latest = getdate(rows[0].from_date)
	tied = [r for r in rows if getdate(r.from_date) == latest]
	if len(tied) > 1:
		frappe.throw(
			_("{0} has {1} Salary Profiles effective on {2} ({3}), so the salary to pay is "
			  "ambiguous. Payroll cannot continue until HR closes or removes the duplicate.").format(
				frappe.bold(employee_name or employee), len(tied), getdate(on_date),
				", ".join("{0} = {1}".format(r.name, flt(r.base)) for r in tied),
			),
			title=_("Ambiguous Salary Profile"),
		)
	return frappe.get_doc("Isoft Salary Profile", rows[0].name)


def assert_single_profile_for_period(employee, start_date, end_date, company=None,
                                     employee_name=None):
	"""Refuse a payroll period during which the salary itself changed.

	Payroll resolves ONE profile per period, from the period end date. If a different
	profile governs the start of the period the salary changed part-way through, and the
	whole month would silently be paid at the later rate — an employee whose pay rose on
	the 16th would be paid the higher amount for the entire month.

	The engine cannot prorate across two profiles, so this fails loudly instead. HR
	either aligns the change to the period boundary or runs two part-period payrolls.

	An employee with no profile at the start of the period is a normal new hire and is
	not affected.
	"""
	at_end = get_active_profile(employee, end_date, company=company, employee_name=employee_name)
	if not at_end:
		return None
	at_start = get_active_profile(employee, start_date, company=company,
	                              employee_name=employee_name)
	if at_start and at_start.name != at_end.name:
		frappe.throw(
			_("The salary of {0} changes during {1} – {2}: {3} applies at the start of the "
			  "period and {4} at the end. Payroll cannot split a period across two Salary "
			  "Profiles. Either align the effective date with the payroll period, or process "
			  "the two part-periods separately.").format(
				frappe.bold(employee_name or employee), getdate(start_date), getdate(end_date),
				at_start.name, at_end.name),
			title=_("Salary Changed Mid-Period"),
		)
	return at_end
