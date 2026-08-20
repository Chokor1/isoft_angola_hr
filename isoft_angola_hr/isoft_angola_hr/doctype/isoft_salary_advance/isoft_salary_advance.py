# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""A salary advance with a balance, a schedule and an audit trail."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, flt, getdate, now

from isoft_angola_hr.isoft_angola_hr.services import advances
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


class IsoftSalaryAdvance(Document):
	def validate(self):
		self.set_defaults()
		self.validate_amounts()
		self.validate_no_competing_advance()

	def set_defaults(self):
		if not self.status:
			self.status = advances.DRAFT
		if not self.requested_by:
			self.requested_by = self.owner or frappe.session.user
		if not self.requested_at:
			self.requested_at = now()
		if not self.company and self.employee:
			self.company = frappe.db.get_value("Employee", self.employee, "company")
		if not self.installments or cint(self.installments) < 1:
			self.installments = 1

	def validate_amounts(self):
		if flt(self.requested_amount) <= 0:
			frappe.throw(_("The requested amount must be greater than zero."))
		if flt(self.approved_amount) < 0:
			frappe.throw(_("The approved amount cannot be negative."))
		if flt(self.approved_amount) > flt(self.requested_amount):
			frappe.throw(_("The approved amount ({0}) cannot exceed the requested amount "
			               "({1}).").format(flt(self.approved_amount), flt(self.requested_amount)))
		amount = flt(self.approved_amount) or flt(self.requested_amount)
		self.installment_amount = flt(amount / cint(self.installments), 2)
		if self.status in advances.OPEN_STATES or self.status == advances.SETTLED:
			recovered = flt(sum(flt(i.recovered) for i in self.schedule))
			self.recovered_amount = flt(recovered, 2)
			self.outstanding_amount = max(0.0, flt(flt(self.approved_amount) - recovered, 2))

	def validate_no_competing_advance(self):
		"""One open advance at a time.

		Two concurrent advances would each schedule their own installments against the
		same pay, and the engine's cap would silently starve one of them. If a second
		advance is genuinely needed, the first has to be settled or cancelled first.
		"""
		if self.status in (advances.DRAFT, advances.REJECTED, advances.CANCELLED,
		                   advances.SETTLED):
			return
		clash = frappe.db.sql(
			"""select name, status, outstanding_amount from `tabIsoft Salary Advance`
			where employee = %s and name != %s and status in ('Approved','Disbursed','Recovering')
			limit 1""", (self.employee, self.name or ""), as_dict=True)
		if clash:
			frappe.throw(
				_("{0} already has an open salary advance ({1}, {2}, outstanding {3}). Settle or "
				  "cancel it before granting another.").format(
					frappe.bold(self.employee_name or self.employee), clash[0].name,
					_(clash[0].status), flt(clash[0].outstanding_amount)),
				title=_("Advance Already Open"))

	# ------------------------------------------------------------------ #
	@frappe.whitelist()
	def build_schedule(self):
		"""Lay the recovery out over the payroll periods that will actually run.

		The periods come from the configured payroll cycle, so an installment always
		lines up with a real payroll run rather than a calendar month that payroll never
		processes.
		"""
		from isoft_angola_hr.isoft_angola_hr import api

		amount = flt(self.approved_amount) or flt(self.requested_amount)
		count = max(1, cint(self.installments))
		anchor = getdate(self.recovery_start_date) if self.recovery_start_date else getdate()
		start, end = api._cycle_period(anchor)

		self.set("schedule", [])
		per = flt(amount / count, 2)
		allocated = 0.0
		for i in range(count):
			# The last installment absorbs the rounding, so the schedule always sums to
			# exactly the approved amount.
			value = flt(amount - allocated, 2) if i == count - 1 else per
			allocated = flt(allocated + value, 2)
			self.append("schedule", {
				"period_start": start, "period_end": end, "amount": value, "recovered": 0,
				"status": "Pending",
			})
			start = add_months(start, 1)
			end = frappe.utils.add_days(add_months(frappe.utils.add_days(end, 1), 1), -1)
		self.installment_amount = per
		self.outstanding_amount = flt(amount, 2)
		return len(self.schedule)

	def on_trash(self):
		if self.status not in (advances.DRAFT, advances.REJECTED, advances.CANCELLED):
			frappe.throw(_("An advance that has been {0} cannot be deleted — cancel it "
			               "instead.").format(_(self.status)))
		if self.disbursement_entry:
			frappe.throw(_("Cancel the disbursement entry {0} first.").format(
				self.disbursement_entry))

	@frappe.whitelist()
	def summary(self):
		perms.require(perms.ADVANCE_REQUEST)
		perms.require_company(self.company)
		return {"name": self.name, "status": self.status,
		        "allowed_actions": advances.allowed_actions(self),
		        "outstanding": flt(self.outstanding_amount)}
