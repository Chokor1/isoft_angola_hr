# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Angola IRT (Imposto sobre os Rendimentos do Trabalho) bracket table.

INTERVAL SEMANTICS
------------------
A bracket owns every amount up to and including its ``to_amount``; the last bracket
(``to_amount`` empty) owns everything above. Matching therefore uses the UPPER bound
only:

    first bracket where  taxable_income <= to_amount   (or to_amount is empty)

``from_amount`` is retained for display and validation but is deliberately NOT used to
match, because the published table prints lower bounds as ``previous upper + 1``
(0-150.000, 150.001-200.000, ...). Matching on both bounds leaves a one-kwanza hole at
every boundary, and taxable remuneration carries cêntimos, so amounts fall into those
holes. Under the previous implementation such an amount matched no bracket at all and
was silently taxed at zero.

``excess_over`` is the amount the marginal rate applies over ("parcela a abater").
When blank it falls back to ``from_amount``.

A taxable income that matches no bracket is a CONFIGURATION ERROR and raises. It is
never a zero-tax employee.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate, getdate


class IRTTable(Document):
	def validate(self):
		self.brackets.sort(key=lambda b: flt(b.from_amount))
		self.validate_brackets()
		self.validate_not_in_use()

	def validate_brackets(self):
		"""Reject any table that could silently under-tax: gaps, overlaps, a missing
		floor, a closed top bracket or a decreasing fixed portion."""
		if not self.effective_from:
			frappe.throw(_("Set the Effective From date of the IRT Table."))
		if not self.brackets:
			frappe.throw(_("The IRT Table must have at least one bracket."))

		rows = self.brackets
		if flt(rows[0].from_amount) > 0:
			frappe.throw(
				_("The first IRT bracket must start at 0, but starts at {0}.").format(
					flt(rows[0].from_amount)
				)
			)
		if flt(rows[-1].to_amount):
			frappe.throw(
				_("The last IRT bracket must be open-ended: leave its To Amount empty "
				  "so that every income above {0} is covered.").format(flt(rows[-1].from_amount))
			)

		prev = None
		for b in rows:
			lower, upper = flt(b.from_amount), flt(b.to_amount)
			if lower < 0 or upper < 0:
				frappe.throw(_("IRT bracket {0}: amounts cannot be negative.").format(b.idx))
			if upper and upper <= lower:
				frappe.throw(
					_("IRT bracket {0}: To Amount ({1}) must be greater than From Amount ({2}).").format(
						b.idx, upper, lower)
				)
			if flt(b.rate) < 0 or flt(b.rate) > 100:
				frappe.throw(_("IRT bracket {0}: rate must be between 0 and 100.").format(b.idx))
			if flt(b.parcela_fixa) < 0:
				frappe.throw(_("IRT bracket {0}: the fixed portion cannot be negative.").format(b.idx))
			if b.excess_over and flt(b.excess_over) > lower:
				frappe.throw(
					_("IRT bracket {0}: Excess Over ({1}) cannot be greater than From Amount ({2}).").format(
						b.idx, flt(b.excess_over), lower)
				)

			if prev is not None:
				prev_upper = flt(prev.to_amount)
				# The lower bound may either repeat the previous upper bound (contiguous
				# style) or be one unit above it (the style printed in the law). Anything
				# else is a gap or an overlap.
				if lower < prev_upper:
					frappe.throw(
						_("IRT brackets {0} and {1} overlap: bracket {1} starts at {2} but "
						  "bracket {0} runs to {3}.").format(prev.idx, b.idx, lower, prev_upper)
					)
				if lower > prev_upper + 1:
					frappe.throw(
						_("Gap between IRT brackets {0} and {1}: income between {2} and {3} "
						  "belongs to no bracket and would be taxed at zero.").format(
							prev.idx, b.idx, prev_upper, lower)
					)
				if flt(b.parcela_fixa) < flt(prev.parcela_fixa):
					frappe.throw(
						_("IRT bracket {0}: the fixed portion ({1}) is lower than the previous "
						  "bracket's ({2}), which would make tax fall as income rises.").format(
							b.idx, flt(b.parcela_fixa), flt(prev.parcela_fixa))
					)
			prev = b

	def validate_not_in_use(self):
		"""A table that has already produced submitted payroll is historical evidence.
		Editing it would retroactively change how past payroll can be explained, so
		require a new effective-dated version instead."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if not self._statutory_content_changed(before):
			return
		used_by = frappe.db.count("Isoft Salary Slip", {"irt_table": self.name, "docstatus": 1})
		if used_by:
			frappe.throw(
				_("IRT Table {0} has already been used by {1} submitted salary slip(s) and "
				  "cannot be changed. Create a new IRT Table with a later Effective From date "
				  "so that historical payroll remains reproducible.").format(
					frappe.bold(self.name), used_by)
			)

	def _statutory_content_changed(self, before):
		if getdate(before.effective_from) != getdate(self.effective_from):
			return True
		if len(before.brackets or []) != len(self.brackets or []):
			return True
		fields = ("from_amount", "to_amount", "excess_over", "rate", "parcela_fixa")
		for old, new in zip(before.brackets, self.brackets):
			if any(flt(old.get(f)) != flt(new.get(f)) for f in fields):
				return True
		return False


def get_active_irt_table(company=None, on_date=None):
	"""The most recent enabled IRT Table effective on or before ``on_date``.

	A company-specific table wins over a global one with the same effective date.
	"""
	on_date = getdate(on_date) if on_date else getdate()
	rows = frappe.get_all(
		"IRT Table",
		filters={"disabled": 0, "effective_from": ("<=", on_date)},
		or_filters=[{"company": company}, {"company": ("in", ["", None])}] if company else None,
		fields=["name", "company", "effective_from"],
		order_by="effective_from desc",
	)
	if not rows:
		return None
	rows.sort(key=lambda r: (r.effective_from, 1 if r.company else 0), reverse=True)
	return frappe.get_cached_doc("IRT Table", rows[0].name)


def resolve_irt(taxable_income, company=None, on_date=None, table=None):
	"""Compute IRT and return the full calculation trace.

	Returns a dict with the amount plus every input that produced it, so a submitted
	salary slip can be explained years later even after the statutory table changes.
	Raises when no table is effective or no bracket covers the income.
	"""
	taxable_income = flt(taxable_income)
	if table is None:
		table = get_active_irt_table(company, on_date)
	if not table:
		frappe.throw(
			_("No IRT Table is effective on {0}{1}. Configure the IRT Table before "
			  "running payroll.").format(
				formatdate(on_date) if on_date else _("today"),
				_(" for company {0}").format(company) if company else "",
			)
		)

	empty = frappe._dict(
		amount=0.0, table=table.name, effective_from=getdate(table.effective_from) if table.effective_from else None,
		bracket_from=0.0, bracket_to=0.0, excess_over=0.0, rate=0.0, parcela_fixa=0.0,
	)
	if taxable_income <= 0:
		return empty

	for b in sorted(table.brackets, key=lambda x: flt(x.from_amount)):
		upper = flt(b.to_amount)
		if upper and taxable_income > upper:
			continue
		excess = flt(b.excess_over) if b.excess_over else flt(b.from_amount)
		amount = flt(flt(b.parcela_fixa) + (taxable_income - excess) * flt(b.rate) / 100.0, 2)
		return frappe._dict(
			amount=max(0.0, amount),
			table=table.name,
			effective_from=getdate(table.effective_from) if table.effective_from else None,
			bracket_from=flt(b.from_amount), bracket_to=upper,
			excess_over=excess, rate=flt(b.rate), parcela_fixa=flt(b.parcela_fixa),
		)

	frappe.throw(
		_("Taxable income {0} matches no bracket of IRT Table {1}. The table does not "
		  "cover the full income range — its last bracket must be open-ended. Payroll "
		  "cannot continue.").format(taxable_income, frappe.bold(table.name))
	)


def compute_irt(taxable_income, company=None, on_date=None, table=None):
	"""Monthly IRT amount. Thin wrapper over :func:`resolve_irt`."""
	return resolve_irt(taxable_income, company=company, on_date=on_date, table=table).amount
