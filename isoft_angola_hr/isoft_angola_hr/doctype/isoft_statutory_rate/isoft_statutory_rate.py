# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Effective-dated Angola statutory contribution rates and IRT exemption thresholds.

Rates change by law and payroll must stay reproducible across those changes, so they
are resolved per payroll date rather than read from a single mutable settings record:

    payroll period end date  ->  most recent Isoft Statutory Rate effective on or
                                 before that date  ->  rates used for the slip

When no rate record exists the resolver falls back to "Isoft HR Settings", which keeps
existing sites calculating exactly as before this DocType was introduced. Creating the
first rate record is therefore opt-in and non-breaking.

LEGAL VERIFICATION REQUIRED — the contribution base (which components the rates apply
to), any contribution ceiling, and the proration of the exemption thresholds for
partial months are NOT decided here. This DocType only makes the *values* and their
*effective dates* configurable; the incidence base remains as implemented in
``payroll/engine.py`` and is unchanged by Phase 1.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


# Fields resolved by :func:`get_statutory_rates`, in the order they are looked up.
RATE_FIELDS = (
	"ss_employee_rate",
	"ss_employer_rate",
	"food_allowance_exemption",
	"transport_allowance_exemption",
)


class IsoftStatutoryRate(Document):
	def validate(self):
		if flt(self.ss_employee_rate) < 0 or flt(self.ss_employee_rate) > 100:
			frappe.throw(_("The employee Social Security rate must be between 0 and 100."))
		if flt(self.ss_employer_rate) < 0 or flt(self.ss_employer_rate) > 100:
			frappe.throw(_("The employer Social Security rate must be between 0 and 100."))
		if flt(self.food_allowance_exemption) < 0 or flt(self.transport_allowance_exemption) < 0:
			frappe.throw(_("Exemption thresholds cannot be negative."))
		self.validate_unique_effective_date()
		self.validate_not_in_use()

	def validate_not_in_use(self):
		"""A rate that submitted payroll was calculated with is historical evidence.

		The slips keep their own snapshot, so the money would not change — but the record
		explaining WHY that money was correct would, and a statutory audit would then be
		reading a rule that no longer matches the payroll it produced. Create a new
		effective-dated record instead.
		"""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		locked_fields = ("effective_from",) + RATE_FIELDS
		changed = [f for f in locked_fields
		           if str(before.get(f) or "") != str(self.get(f) or "")]
		if not changed:
			return
		used_by = frappe.db.count("Isoft Salary Slip", {"statutory_rate": self.name, "docstatus": 1})
		if used_by:
			frappe.throw(
				_("{0} has already been used by {1} submitted salary slip(s), so {2} cannot be "
				  "changed. Create a new Statutory Rate with a later effective date instead, so "
				  "historical payroll stays explainable.").format(
					frappe.bold(self.name), used_by, ", ".join(changed)),
				title=_("Statutory Rate In Use"),
			)

	def validate_unique_effective_date(self):
		"""Two records with the same scope and effective date would make the resolution
		order arbitrary — exactly the ambiguity this DocType exists to remove."""
		clash = frappe.db.sql(
			"""select name from `tabIsoft Statutory Rate`
			where effective_from=%s and ifnull(company,'')=%s and name!=%s limit 1""",
			(getdate(self.effective_from), self.company or "", self.name or ""),
		)
		if clash:
			frappe.throw(
				_("Statutory Rate {0} already takes effect on {1} for the same company. "
				  "Edit that record instead of creating a second one.").format(
					frappe.bold(clash[0][0]), self.effective_from)
			)


def get_statutory_rates(company=None, on_date=None, settings=None):
	"""Resolve the statutory rates governing a payroll date.

	:param company: prefers a company-specific record over a global one
	:param on_date: payroll period end date; defaults to today
	:param settings: an already-loaded "Isoft HR Settings" used as the fallback
	:returns: frappe._dict with the rate fields plus ``statutory_rate`` (source record
	          name, or None when the settings fallback was used)
	"""
	on_date = getdate(on_date) if on_date else getdate()
	rows = frappe.get_all(
		"Isoft Statutory Rate",
		filters={"disabled": 0, "effective_from": ("<=", on_date)},
		or_filters=[{"company": company}, {"company": ("in", ["", None])}] if company else None,
		fields=["name", "company", "effective_from"],
		order_by="effective_from desc",
	)
	if rows:
		# Prefer a company-specific record over a global one on the same date.
		rows.sort(key=lambda r: (r.effective_from, 1 if r.company else 0), reverse=True)
		doc = frappe.get_cached_doc("Isoft Statutory Rate", rows[0].name)
		out = frappe._dict({f: doc.get(f) for f in RATE_FIELDS})
		out.statutory_rate = doc.name
		out.effective_from = getdate(doc.effective_from)
		return out

	if settings is None:
		settings = frappe.get_cached_doc("Isoft HR Settings")
	out = frappe._dict({f: settings.get(f) for f in RATE_FIELDS})
	out.statutory_rate = None
	out.effective_from = None
	return out


def require_rate(rates, fieldname, label):
	"""Statutory rates must be configured, never defaulted in code.

	A rate that was never configured is a setup error; a rate explicitly set to 0 is a
	legitimate configuration and is respected.
	"""
	value = rates.get(fieldname)
	if value is None or value == "":
		frappe.throw(
			_("{0} is not configured. Set it in Isoft HR Settings, or create an "
			  "Isoft Statutory Rate record effective for this payroll period.").format(label)
		)
	return flt(value)
