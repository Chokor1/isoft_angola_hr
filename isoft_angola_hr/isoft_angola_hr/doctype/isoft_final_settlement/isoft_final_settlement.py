# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Final Settlement controller.

Two calculation versions coexist on purpose:

* **version 1** — ``engine.compute_settlement``, the pre-audit calculation. Records
  created by it keep their stored amounts for ever; they are never re-derived, because
  re-deriving them under the corrected rules would silently rewrite a payment somebody
  has already received (prompt section 46).
* **version 2** — ``settlement.compute``, written against Lei n.º 12/23. Every new
  settlement uses it.

A submitted settlement of either version is frozen: ``validate`` does not recompute it.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime

from isoft_angola_hr.isoft_angola_hr.payroll import engine, settlement
from isoft_angola_hr.isoft_angola_hr.services import angola_labour_law as law

#: Statuses from which HR may still edit and recompute.
EDITABLE_STATUSES = ("Draft", "Rejected")


class IsoftFinalSettlement(Document):
	def validate(self):
		if self.docstatus == 0 and cint(self.calc_version or 0) >= settlement.CALC_VERSION:
			self.recompute()
		elif self.docstatus == 0 and not cint(self.calc_version or 0) and self.is_new():
			# A brand-new record with no version stamp: use the corrected engine.
			self.calc_version = settlement.CALC_VERSION
			self.recompute()
		elif self.docstatus == 0:
			# An existing version-1 draft. Recompute with the engine that made it, so
			# editing an old draft does not silently move it onto new rules.
			self.recompute_v1()
		self._stamp_overrides()

	# ------------------------------------------------------------------ #
	# Calculation
	# ------------------------------------------------------------------ #
	def inputs(self):
		"""The engine inputs, resolved from this document alone."""
		vested = self.leave_vested or "Auto"
		return {
			"employee": self.employee,
			"company": self.company,
			"contract": self.contract,
			"joining_date": self.date_of_joining,
			"termination_date": self.termination_date,
			"reason_key": self.reason_key or law.LEGACY_REASON_MAP.get(self.reason or ""),
			"base": flt(self.base),
			"technical_supplement": flt(self.technical_supplement),
			"availability_supplement": flt(self.availability_supplement),
			"food_allowance": flt(self.food_allowance),
			"transport_allowance": flt(self.transport_allowance),
			"period_start": self.salary_period_start,
			"period_end": self.salary_period_end,
			"period_days": flt(self.period_days),
			"days_worked": flt(self.salary_days_worked),
			"salary_method": self.salary_method or "auto",
			"salary_divisor": cint(self.salary_days) or 26,
			"weekly_hours": flt(self.weekly_hours),
			"working_days_per_week": flt(self.working_days_per_week) or 5,
			"vested_untaken_days": flt(self.vested_untaken_days),
			"leave_vested": None if vested == "Auto" else (vested == "Yes"),
			"leave_divisor": cint(self.leave_days) or law.ANNUAL_LEAVE_WORKING_DAYS,
			"leave_rate_method": self.leave_rate_method or "company_divisor",
			"leave_base_includes_allowances": cint(self.leave_base_includes_allowances),
			"fixed_term_under_one_year": cint(self.fixed_term_under_one_year),
			"ferias_rate": flt(self.ferias_rate),
			"natal_rate": flt(self.natal_rate),
			"supplement_months_override": self.supplement_months_override or None,
			"seniority_years_override": self.seniority_years_override or None,
			"agreed_compensation": flt(self.agreed_compensation),
			"compensation_tax_position": self.compensation_tax_position,
			"notice_required_days": cint(self.notice_required_days),
			"notice_given_days": (None if self.notice_given_days in (None, "")
			                      else cint(self.notice_given_days)),
			"employer_missed_renewal_notice": cint(self.employer_missed_renewal_notice),
			"advance_outstanding": flt(self.advance_outstanding),
			"recover_advance": cint(self.recover_advance),
			"override_reason": self.override_reason,
			"override_by": self.override_by,
			"override_at": self.override_at,
		}

	def recompute(self):
		"""Recompute every derived amount under Lei n.º 12/23."""
		res = settlement.compute(self.inputs())
		by_key = {ln["key"]: ln for ln in res["lines"]}

		def amt(key):
			return flt((by_key.get(key) or {}).get("amount"))

		self.calc_version = settlement.CALC_VERSION
		self.monthly_remuneration = res["monthly_remuneration"]
		self.period_salary = amt("salary")

		leave = res["leave"]
		self.vested_untaken_days = flt(leave["vested_untaken_days"], 2)
		self.proportional_leave_days = flt(leave["proportional_days"], 2)
		self.total_leave_days = flt(leave["total_days"], 2)
		self.leave_remuneration_base = flt(leave["remuneration_base"], 2)
		self.leave_daily_rate = flt(leave["daily_rate"], 2)
		self.vested_leave_amount = amt("leave_vested")
		self.proportional_leave_amount = amt("leave_proportional")
		# The legacy fields keep meaning something for the list view and the old export.
		self.untaken_leave_days = self.total_leave_days
		self.untaken_leave_amount = flt(self.vested_leave_amount + self.proportional_leave_amount, 2)

		self.supplement_months = cint(res["supplements"]["months"])
		self.months_worked = self.supplement_months
		self.vacation_allowance = amt("vacation_allowance")
		self.christmas_bonus = amt("christmas_bonus")
		self.vacation_monthly = flt(flt(self.base) * flt(self.ferias_rate) / 1200.0, 2)
		self.christmas_monthly = flt(flt(self.base) * flt(self.natal_rate) / 1200.0, 2)
		self.salary_daily_rate = flt(
			flt(self.monthly_remuneration) / (cint(self.salary_days) or 26), 2)

		comp = by_key.get("compensation") or {}
		self.compensation_amount = flt(comp.get("amount"))
		self.compensation_article = comp.get("article")
		self.compensation_status = comp.get("status")
		self.compensation_formula = comp.get("formula")
		self.seniority_years = cint(res["seniority_years"])

		self.notice_amount = flt(
			amt("notice_employer") - amt("notice_employee"), 2)

		self.inss_base_amount = res["inss_base"]
		self.inss_amount = res["inss"]
		self.irt_base_amount = res["irt_base"]
		self.irt_amount = res["irt"]
		self.advance_recovered = res["advance_recovered"]
		self.advance_deferred = res["advance_deferred"]

		self.total_gross = res["gross"]
		self.total_deductions = res["total_deductions"]
		self.net_payable = res["net"]
		self.shortfall = res["shortfall"]

		deadline = settlement.payment_deadline(self.termination_date,
		                                       self.inputs().get("reason_key"))
		self.settlement_due_date = deadline["due_date"]
		self.payment_deadline_article = deadline["article"]

		self.calculation_trace = json.dumps(res["trace"], indent=1, default=str)
		self.calculation_flags = json.dumps(res["flags"], indent=1, default=str)
		return res

	def recompute_v1(self):
		"""The pre-audit calculation, kept so version-1 drafts behave as they always did."""
		res = engine.compute_settlement({
			"base": flt(self.base),
			"food_allowance": flt(self.food_allowance),
			"transport_allowance": flt(self.transport_allowance),
			"salary_days_worked": flt(self.salary_days_worked),
			"salary_days": cint(self.salary_days) or 26,
			"months_worked": cint(self.months_worked),
			"ferias_rate": flt(self.ferias_rate),
			"natal_rate": flt(self.natal_rate),
			"untaken_leave_days": flt(self.untaken_leave_days),
			"leave_days": cint(self.leave_days) or 22,
		})
		for field in ("monthly_remuneration", "salary_daily_rate", "period_salary",
		              "vacation_monthly", "vacation_allowance", "christmas_monthly",
		              "christmas_bonus", "leave_daily_rate", "untaken_leave_amount",
		              "total_gross"):
			self.set(field, res[field])
		return res

	def _stamp_overrides(self):
		"""A derived legal quantity may be overridden, but never anonymously."""
		overridden = bool(self.supplement_months_override or self.seniority_years_override)
		if not overridden:
			self.override_reason = None
			self.override_by = None
			self.override_at = None
			return
		if not (self.override_reason or "").strip():
			frappe.throw(_(
				"Complete months and seniority are derived from the dates under Lei "
				"n.º 12/23. To override one, record the reason."))
		if self.has_value_changed("supplement_months_override") \
			or self.has_value_changed("seniority_years_override") \
			or self.has_value_changed("override_reason") or not self.override_by:
			self.override_by = frappe.session.user
			self.override_at = now_datetime()

	# ------------------------------------------------------------------ #
	# Lifecycle
	# ------------------------------------------------------------------ #
	def before_submit(self):
		if cint(self.calc_version or 0) >= settlement.CALC_VERSION:
			res = settlement.compute(self.inputs())
			if res["blocking"]:
				frappe.throw(_("This settlement cannot be finalised yet: {0}").format(
					" ".join(f["message"] for f in res["blocking"])))
		if self.workflow_status not in ("Approved", "Draft", None, ""):
			return
		if not self.workflow_status:
			self.workflow_status = "Draft"

	def on_submit(self):
		"""Finalising the settlement closes the employee's record: Relieving Date =
		termination date, status = Left."""
		if not self.employee or not frappe.db.exists("Employee", self.employee):
			return
		emp = frappe.get_doc("Employee", self.employee)
		emp.relieving_date = getdate(self.termination_date)
		emp.status = "Left"
		emp.save(ignore_permissions=True)

	def on_cancel(self):
		"""Reopen the employee if this settlement had closed them."""
		self.workflow_status = "Cancelled"
		if not self.employee or not frappe.db.exists("Employee", self.employee):
			return
		emp = frappe.get_doc("Employee", self.employee)
		if emp.status == "Left" and getdate(emp.relieving_date or 0) == getdate(self.termination_date):
			emp.status = "Active"
			emp.relieving_date = None
			emp.save(ignore_permissions=True)
