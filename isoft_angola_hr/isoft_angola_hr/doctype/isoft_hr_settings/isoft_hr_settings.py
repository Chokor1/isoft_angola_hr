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


#: The mappings whose change is governed (audit finding F-04, governance). Every account
#: mapping is logged; these are the ones whose change after a posted payroll is announced
#: to the person saving, in the words Finance asked for.
GOVERNED_MAPPINGS = ("CTSS3", "CTSSE", "CTSSP", "ADT", "payroll_payable_account",
                     "salary_payment_account")

MAPPING_CHANGE_WARNING = (
	"This account mapping was changed after payroll posting. Previous payroll vouchers "
	"remain unchanged. New payroll will use the new mapping.")


class IsoftHRSettings(Document):
	def validate(self):
		self.validate_accounts()
		self._mapping_changes = self.detect_mapping_changes()

	def on_update(self):
		self.record_mapping_changes()

	# ------------------------------------------------------------------ #
	# Account mapping governance
	# ------------------------------------------------------------------ #
	def current_mapping(self):
		out = {r.abbr: r.account or None for r in self.get("component_accounts") or [] if r.abbr}
		out["payroll_payable_account"] = self.get("payroll_payable_account") or None
		out["salary_payment_account"] = self.get("salary_payment_account") or None
		return out

	def stored_mapping(self):
		"""The mapping as the database holds it right now — read directly, never from the
		document cache, because this decides whether a change is recorded."""
		rows = frappe.db.sql(
			"""select abbr, account from `tabIsoft Component Account`
			where parent = 'Isoft HR Settings' and parenttype = 'Isoft HR Settings'""", as_dict=True)
		out = {r.abbr: (r.account or None) for r in rows if r.abbr}
		for field in ("payroll_payable_account", "salary_payment_account"):
			found = frappe.db.sql(
				"select `value` from `tabSingles` where `doctype`='Isoft HR Settings' and `field`=%s",
				field)
			out[field] = (found[0][0] or None) if found else None
		return out

	def detect_mapping_changes(self):
		"""Every mapping this save changes: (key, old, new)."""
		if self.is_new():
			return []
		before, after = self.stored_mapping(), self.current_mapping()
		changes = []
		for key in sorted(set(before) | set(after)):
			old, new = before.get(key), after.get(key)
			if (old or None) != (new or None):
				changes.append((key, old, new))
		return changes

	def record_mapping_changes(self):
		"""Write one Isoft Payroll Account Change per changed mapping, and say so out loud
		when a payroll has already been posted under the old mapping.

		The change is not refused: choosing the account is Finance's decision and this
		screen is where it is made. What is refused is silence — the change is recorded
		with who, when, from and to, and flagged when history was booked differently.
		No voucher, payroll or slip is touched.
		"""
		changes = getattr(self, "_mapping_changes", None) or []
		if not changes:
			return
		from isoft_angola_hr.isoft_angola_hr.services import payroll_accounts as pa

		if pa.mapping_logging_suspended():
			return          # test fixture moving its own accounts; never true in production

		company = self.default_company
		last = pa.last_posted_run(company) if company else None
		labels = {c["abbr"]: c["component"] for c in engine.journal_components()}
		governed_after_posting = []
		for key, old, new in changes:
			label = labels.get(key) or pa.business_label(key)
			doc = frappe.get_doc({
				"doctype": "Isoft Payroll Account Change",
				"company": company, "account_key": key, "label": label,
				"changed_by": frappe.session.user, "changed_on": frappe.utils.now(),
				"old_account": old, "new_account": new,
				"after_posting": 1 if last else 0,
				"posted_payroll": last.name if last else None,
				"note": _(MAPPING_CHANGE_WARNING) if last else None,
			})
			doc.flags.ignore_permissions = True
			doc.insert()
			if last and key in GOVERNED_MAPPINGS:
				governed_after_posting.append((key, label, old, new))
		self._mapping_change_records = len(changes)
		if governed_after_posting:
			frappe.msgprint(
				"{0}<br><br>{1}<br><br><span class='text-muted small'>{2}</span>".format(
					_(MAPPING_CHANGE_WARNING),
					"<br>".join(_("• {0}: {1} → {2}").format(
						label, old or _("(not configured)"), new or _("(not configured)"))
						for _key, label, old, new in governed_after_posting),
					_("Last posted payroll: {0}. Recorded in Isoft Payroll Account Change by {1}.").format(
						last.name, frappe.session.user)),
				title=_("Account Mapping Changed After Payroll Posting"), indicator="orange")

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

	Whitelisted, so it is reachable directly over HTTP by anyone with a login — and it was:
	an account with no roles at all could read back the company's chart of accounts, number
	by number, because being a link query is not itself an authorisation. The people who
	map payroll to accounts are the ones who may see them: HR Manager owns the mappings
	(settings.write) and the finance roles post to them (payroll.post).
	"""
	from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

	if not perms.any_of([perms.SETTINGS_WRITE, perms.PAYROLL_POST]):
		perms.require(perms.SETTINGS_WRITE)

	# Called directly (rather than through the desk's link-query plumbing) the filters
	# arrive as a JSON string, and reading .get() off a string raised a 500 that said
	# nothing useful.
	if isinstance(filters, str):
		filters = frappe.parse_json(filters) or {}
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


#: The six controls that decide whether a second person must act. They are the only
#: settings whose *absence* has to mean something different from zero, so they are the
#: only ones read through :func:`separation_required`.
SEPARATION_FIELDS = (
	"require_separate_contract_approval",
	"require_separate_salary_change_approval",
	"require_separate_advance_approval",
	"require_separate_payroll_approval",
	"require_separate_payment_approval",
	"require_separate_settlement_approval",
)


def separation_required(fieldname, default=1):
	"""Whether a second person is required for *fieldname* — the ONE reader of these six.

	``frappe.db.get_single_value`` cannot express this. It casts a Check through
	``cint(sbool(value))``, so a field that has never been written returns 0 — identical
	to a field somebody deliberately switched off. Every caller in this app guarded on
	``value is None or value == ""`` to fail closed, and every one of those branches was
	unreachable: on a site that migrated into a new control, the control read as OFF.
	That is exactly backwards for a switch whose purpose is to require a second person.

	Reading the stored row keeps the two cases distinct: an absent row means nobody has
	decided yet, and the safe answer for a segregation-of-duties control is that the
	separation stands. Only an explicit 0 relaxes it.

	``frappe.db.get_value`` is also unusable here — it appends ``order by modified`` and
	``tabSingles`` has no such column — hence the raw SELECT. It is deliberately NOT
	routed through ``db.value_cache`` or ``get_cached_doc``: the value decides whether an
	approval is legal, and it must reflect the row as it stands now, not as it stood when
	something else happened to read it earlier in the request.
	"""
	found = frappe.db.sql(
		"""select `value` from `tabSingles`
		where `doctype` = %s and `field` = %s""",
		("Isoft HR Settings", fieldname))
	row = found[0][0] if found else None
	if row is None or row == "":
		return cint(default)
	return cint(row)
