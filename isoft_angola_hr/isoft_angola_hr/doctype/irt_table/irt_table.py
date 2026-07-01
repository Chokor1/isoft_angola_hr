# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate


class IRTTable(Document):
	def validate(self):
		self.brackets.sort(key=lambda b: flt(b.from_amount))


def get_active_irt_table(company=None, on_date=None):
	"""Return the most recent, enabled IRT Table effective on or before `on_date`.

	Prefers a company-specific table; falls back to a global one (company empty).
	"""
	on_date = getdate(on_date) if on_date else getdate()
	filters = {"disabled": 0, "effective_from": ("<=", on_date)}
	rows = frappe.get_all(
		"IRT Table",
		filters=filters,
		or_filters=[{"company": company}, {"company": ("in", ["", None])}] if company else None,
		fields=["name", "company", "effective_from"],
		order_by="effective_from desc",
	)
	if not rows:
		return None
	# Prefer company-specific over global when both are effective.
	rows.sort(key=lambda r: (r.effective_from, 1 if r.company else 0), reverse=True)
	return frappe.get_cached_doc("IRT Table", rows[0].name)


def compute_irt(taxable_income, company=None, on_date=None, table=None):
	"""Compute monthly IRT for a given taxable income using the Angola bracket formula:

	tax = parcela_fixa + (taxable_income - excess_over) * rate%

	`excess_over` defaults to the bracket's `from_amount` when not set.
	Returns 0.0 when no table/bracket matches.
	"""
	taxable_income = flt(taxable_income)
	if table is None:
		table = get_active_irt_table(company, on_date)
	if not table or taxable_income <= 0:
		return 0.0

	for b in sorted(table.brackets, key=lambda x: flt(x.from_amount)):
		upper_ok = (not b.to_amount) or taxable_income <= flt(b.to_amount)
		if taxable_income >= flt(b.from_amount) and upper_ok:
			excess = flt(b.excess_over) if b.excess_over else flt(b.from_amount)
			return flt(flt(b.parcela_fixa) + (taxable_income - excess) * flt(b.rate) / 100.0, 2)
	return 0.0
