# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The employment agreement — the document the whole HR lifecycle hangs from."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, now

from isoft_angola_hr.isoft_angola_hr.services import contracts
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


class IsoftEmploymentContract(Document):
	def validate(self):
		self.set_defaults()
		self.validate_dates()
		self.validate_no_overlap()
		self.refresh_derived_status()
		self.warn_about_statutory_limits()

	def warn_about_statutory_limits(self):
		"""Flag terms that sit outside Lei n.º 12/23, citing the article — never block.

		The lawful ceiling on a fixed term depends on the legal ground for using one, and
		the probation ceiling depends on whether the role is a função de direcção. This
		app records neither, so it says what the statute provides and leaves the decision
		with HR. Blocking here would mean the software deciding a question of law on
		facts it does not hold. See services/angola_labour_law.py.
		"""
		from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

		if self.flags.ignore_statutory_warnings:
			return
		warnings = law.check_contract({
			"is_open_ended": self.is_open_ended, "start_date": self.start_date,
			"end_date": self.end_date, "probation_start": self.probation_start,
			"probation_end": self.probation_end, "notice_days": self.notice_days,
		})
		if not warnings:
			return
		frappe.msgprint(
			"<br><br>".join("<b>{0}</b> — {1}<br>{2}".format(
				w["article"], w["message"], law.REVIEW_MARKER) for w in warnings),
			title=_("Check against {0}").format(law.LAW), indicator="orange")

	def set_defaults(self):
		if not self.status:
			self.status = contracts.DRAFT
		if not self.prepared_by:
			self.prepared_by = self.owner or frappe.session.user
		if not self.prepared_at:
			self.prepared_at = now()
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")

		# Fill the terms from the contract type the first time, so HR does not retype
		# them — but never overwrite anything already entered.
		if self.contract_type and self.is_new():
			ct = frappe.db.get_value(
				"Isoft Contract Type",
				self.contract_type,
				["is_fixed_term", "default_duration_months", "default_probation_days",
				 "default_notice_days", "renewable"],
				as_dict=True) or {}
			if not cint(ct.get("is_fixed_term")):
				self.is_open_ended = 1
			if not self.end_date and cint(ct.get("default_duration_months")) and self.start_date:
				self.end_date = add_days(
					frappe.utils.add_months(getdate(self.start_date),
					                        cint(ct["default_duration_months"])), -1)
			if not self.notice_days:
				self.notice_days = cint(ct.get("default_notice_days"))
			if not self.probation_end and cint(ct.get("default_probation_days")) and self.start_date:
				self.probation_start = self.probation_start or getdate(self.start_date)
				self.probation_end = add_days(getdate(self.probation_start),
				                              cint(ct["default_probation_days"]) - 1)
			if self.renewal_allowed is None:
				self.renewal_allowed = cint(ct.get("renewable", 1))

		# The Salary Profile stays authoritative for pay; the contract only points at it.
		if self.employee and not self.salary_profile:
			from isoft_angola_hr.isoft_angola_hr.doctype.isoft_salary_profile.isoft_salary_profile import (
				get_effective_profiles,
			)

			rows = get_effective_profiles(self.employee, self.start_date or getdate(),
			                              company=self.company)
			if len(rows) == 1:
				self.salary_profile = rows[0].name

	def validate_dates(self):
		if not self.start_date:
			frappe.throw(_("Set the contract Start Date."))
		if cint(self.is_open_ended):
			self.end_date = None
		elif not self.end_date:
			frappe.throw(_("A fixed-term contract needs an End Date, or must be marked "
			               "Open Ended."))
		elif getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("The contract End Date cannot be before the Start Date."))

		if self.probation_start and self.probation_end:
			if getdate(self.probation_end) < getdate(self.probation_start):
				frappe.throw(_("Probation cannot end before it starts."))
			if getdate(self.probation_start) < getdate(self.start_date):
				frappe.throw(_("Probation cannot start before the contract does."))
			if self.end_date and getdate(self.probation_end) > getdate(self.end_date):
				frappe.throw(_("Probation cannot end after the contract does."))
		elif self.probation_end and not self.probation_start:
			self.probation_start = getdate(self.start_date)

	def validate_no_overlap(self):
		"""One employment agreement at a time, per employee and company.

		Two overlapping contracts mean two simultaneous sets of agreed terms — different
		notice periods, different end dates, possibly different positions — with nothing
		to say which one governs. Cancelled, rejected and draft contracts are ignored, so
		preparing next year's contract while this year's runs is still possible: it only
		has to start after the current one ends.
		"""
		if self.status in (contracts.CANCELLED, contracts.REJECTED, contracts.DRAFT):
			return
		end = getdate(self.end_date) if self.end_date and not cint(self.is_open_ended) \
			else getdate("2999-12-31")
		clash = frappe.db.sql(
			"""select name, start_date, end_date, status from `tabIsoft Employment Contract`
			where employee = %s and company = %s and name != %s
			  and status in ({0})
			  and start_date <= %s
			  and (ifnull(is_open_ended, 0) = 1 or ifnull(end_date, '2999-12-31') >= %s)
			limit 1""".format(", ".join(["%s"] * len(contracts.OCCUPYING_STATES))),
			[self.employee, self.company, self.name or ""] + list(contracts.OCCUPYING_STATES)
			+ [end, getdate(self.start_date)],
			as_dict=True)
		if clash:
			frappe.throw(
				_("{0} already has contract {1} ({2}) covering {3} to {4}. An employee cannot "
				  "hold two employment contracts for the same period in {5}.").format(
					frappe.bold(self.employee_name or self.employee), clash[0].name,
					_(clash[0].status), clash[0].start_date,
					clash[0].end_date or _("open ended"), self.company),
				title=_("Overlapping Contract"))

	def refresh_derived_status(self):
		self.status = contracts.derive_status(self)
		self.probation_status = contracts.derive_probation_status(self)

	def on_trash(self):
		if self.status not in (contracts.DRAFT, contracts.REJECTED, contracts.CANCELLED):
			frappe.throw(
				_("A contract that has been {0} cannot be deleted — cancel it instead so the "
				  "employment history survives.").format(_(self.status)))
		if self.renewed_to:
			frappe.throw(_("This contract has been renewed by {0} and cannot be deleted.").format(
				self.renewed_to))

	# ------------------------------------------------------------------ #
	@frappe.whitelist()
	def summary(self):
		perms.require(perms.CONTRACT_READ)
		perms.require_company(self.company)
		return {
			"name": self.name,
			"status": self.status,
			"probation_status": self.probation_status,
			"allowed_actions": contracts.allowed_actions(self),
			"days_to_expiry": (frappe.utils.date_diff(self.end_date, frappe.utils.nowdate())
			                   if self.end_date and not cint(self.is_open_ended) else None),
			"can_renew": bool(cint(self.renewal_allowed)) and not self.renewed_to
			and self.status in (contracts.ACTIVE, contracts.EXPIRING, contracts.EXPIRED),
		}
