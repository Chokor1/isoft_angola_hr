# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll configuration, validated when it is SAVED rather than when payroll is posted.

Before this, a wrong account mapping was discovered by the Journal Entry failing to
submit — halfway through a payroll run, after somebody had already approved it. An
account that belongs to another company, is disabled, is a group, or has the wrong
root type is a configuration mistake, and configuration mistakes belong to the person
editing the configuration.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from isoft_angola_hr.isoft_angola_hr.payroll import engine

#: The account nature each payroll component must be booked to. The engine debits
#: earnings and the employer-contribution expense, and credits deductions and the
#: employer-contribution liability, so a reversed mapping silently inverts the books.
EXPECTED_ROOT_TYPE = {
	"earning": ("Expense",),
	"employer_expense": ("Expense",),
	# A deduction is credited. Most are liabilities owed onward (INSS, IRT); an advance
	# repayment credits the asset it was booked to, so both are legitimate.
	"deduction": ("Liability", "Asset"),
	"employer_liability": ("Liability",),
}


class IsoftHRSettings(Document):
	def validate(self):
		self.validate_accounts()

	# ------------------------------------------------------------------ #
	def validate_accounts(self):
		company = self.default_company
		problems = []

		kinds = {c["abbr"]: c["kind"] for c in engine.journal_components()}
		seen = {}
		for row in self.get("component_accounts") or []:
			if not row.account:
				continue
			if row.abbr in seen and seen[row.abbr] != row.account:
				problems.append(_("{0} is mapped to two different accounts ({1} and {2}).").format(
					row.abbr, seen[row.abbr], row.account))
			seen[row.abbr] = row.account
			problems.extend(self._check_account(
				row.account, _("Component {0}").format(row.component or row.abbr), company,
				EXPECTED_ROOT_TYPE.get(kinds.get(row.abbr))))

		problems.extend(self._check_account(
			self.payroll_payable_account, _("Payroll Payable Account"), company, ("Liability",)))
		problems.extend(self._check_account(
			self.salary_payment_account, _("Salary Payment Account"), company, ("Asset", "Liability")))

		if problems:
			frappe.throw(
				_("The payroll account configuration cannot be saved:<br><br>{0}").format(
					"<br>".join("• " + p for p in problems)),
				title=_("Invalid Payroll Account"),
			)

	def _check_account(self, account, label, company, expected_root_types):
		"""Everything that can be wrong with one mapping, reported together."""
		if not account:
			return []
		row = frappe.db.get_value(
			"Account", account,
			["company", "root_type", "is_group", "disabled", "account_currency", "freeze_account"],
			as_dict=True)
		if not row:
			return [_("{0}: account {1} does not exist.").format(label, account)]

		out = []
		if company and row.company != company:
			# The single most dangerous mapping: it posts one company's payroll into
			# another company's ledger.
			out.append(_("{0}: account {1} belongs to {2}, not to {3}.").format(
				label, account, row.company, company))
		if cint(row.is_group):
			out.append(_("{0}: account {1} is a group account — postings need a leaf account.").format(
				label, account))
		if cint(row.disabled):
			out.append(_("{0}: account {1} is disabled.").format(label, account))
		if row.get("freeze_account") == "Yes":
			out.append(_("{0}: account {1} is frozen.").format(label, account))
		if expected_root_types and row.root_type not in expected_root_types:
			out.append(_("{0}: account {1} is {2}; a {3} account is required.").format(
				label, account, _(row.root_type or "?"),
				_(" or ").join(_(r) for r in expected_root_types)))

		currency = self.currency or frappe.db.get_value("Company", company, "default_currency") \
			if company else None
		if currency and row.account_currency and row.account_currency != currency:
			out.append(_("{0}: account {1} is in {2} but payroll is in {3}.").format(
				label, account, row.account_currency, currency))
		return out


@frappe.whitelist()
def account_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field query for payroll account fields.

	Offers only accounts that would actually pass validation — the right company, leaf,
	enabled, and of the nature the component requires — so a wrong mapping is hard to
	make rather than merely reported afterwards.
	"""
	filters = filters or {}
	company = filters.get("company") or frappe.db.get_single_value(
		"Isoft HR Settings", "default_company")
	root_types = filters.get("root_types") or []
	if isinstance(root_types, str):
		root_types = [r for r in root_types.split(",") if r]

	conditions = ["a.is_group = 0", "ifnull(a.disabled, 0) = 0"]
	values = []
	if company:
		conditions.append("a.company = %s")
		values.append(company)
	if root_types:
		conditions.append("a.root_type in ({0})".format(", ".join(["%s"] * len(root_types))))
		values.extend(root_types)
	if txt:
		conditions.append("(a.name like %s or a.account_name like %s or a.account_number like %s)")
		values.extend(["%{0}%".format(txt)] * 3)

	return frappe.db.sql(
		"""select a.name, a.root_type, a.account_number from `tabAccount` a
		where {0} order by a.account_number, a.name limit %s, %s""".format(" and ".join(conditions)),
		values + [cint(start), cint(page_len)],
	)


def component_root_types(abbr):
	"""Account nature required for a component — used by the UI to filter the picker."""
	kinds = {c["abbr"]: c["kind"] for c in engine.journal_components()}
	return list(EXPECTED_ROOT_TYPE.get(kinds.get(abbr), ()))
