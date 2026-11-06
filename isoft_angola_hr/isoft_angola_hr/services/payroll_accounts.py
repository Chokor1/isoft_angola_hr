# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Payroll account mapping integrity (audit finding F-04).

Payroll reaches the general ledger through one mapping: Isoft HR Settings → *Account per
Component* (one GL account per engine component), plus the Payroll Payable account (net
pay) and the Salary Payment account (bank). The mapping is validated when Settings is
saved, but nothing re-checked it at the moment that matters — when a Journal Entry is
about to be written. On the ITEC site two components (Salary Advance, Employer INSS cost)
were unmapped and the employee-INSS account had been re-pointed after July was posted;
posting August would have failed on the first employee with an advance, after other
employees had already been booked, and the closed July run no longer reconciled against
the mapping in force.

This module answers three questions and changes nothing:

* :func:`assert_postable` — may these slips be posted with the mapping as it stands?
  Every component that will produce a ledger line must have a usable account of the
  right nature; otherwise posting is refused *before* any Journal Entry exists, naming
  the component, the account type it needs and where to configure it.
* :func:`assert_payable` — the same for the payment (payable + bank).
* :func:`mapping_drift` — for a run already in the ledger, did the mapping move since it
  was posted? Historical vouchers are never touched; drift is reported so Finance can
  decide.

Nothing here chooses an account. That is Finance's decision.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
	EXPECTED_ROOT_TYPE,
)
from isoft_angola_hr.isoft_angola_hr.payroll import engine

#: Where every payroll account is configured. Quoted in every refusal.
WHERE_COMPONENTS = "Isoft HR Settings → Account per Component"
WHERE_PAYABLE = "Isoft HR Settings → Payroll Payable Account"
WHERE_BANK = "Isoft HR Settings → Salary Payment Account"

#: Plain-English names for the components Finance asks about by function rather than by
#: the engine's Portuguese line name. Others print the engine name.
FRIENDLY = {
	"ADT": "Salary Advance",
	"CTSS3": "Employee INSS",
	"CTSSE": "Employer INSS (cost)",
	"CTSSP": "Employer INSS (payable)",
	"IRT": "IRT",
}


#: What an HR user reads when an account is missing. No component codes, no account
#: natures: a sentence a person who has never opened the chart of accounts can act on —
#: by contacting Finance, which is the only action available to them.
BUSINESS = {
	"ADT": "Salary advance account",
	"CTSS3": "Employee Social Security contribution account",
	"CTSSE": "Employer Social Security expense account",
	"CTSSP": "Employer Social Security payable account",
	"IRT": "IRT withholding account",
	"payroll_payable_account": "Payroll payable account (net salaries)",
	"salary_payment_account": "Bank account for salary payments",
}

CONTACT_FINANCE = "Please contact Finance."


def business_label(key):
	"""Plain-language name of a payroll account, for HR-facing messages."""
	if key in BUSINESS:
		return _(BUSINESS[key])
	comp = engine.COMPONENTS.get(key) or {}
	if comp.get("name"):
		return _("{0} expense account").format(_(comp["name"]))
	return key


def business_message(problem):
	"""One sentence HR can act on, for a problem row from :func:`accrual_problems`,
	:func:`payment_problems` or the checklist: *Employer Social Security expense account
	is not configured. Please contact Finance.*"""
	key = problem.get("abbr") or problem.get("key")
	label = business_label(key)
	if not problem.get("account"):
		return _("{0} is not configured. {1}").format(label, _(CONTACT_FINANCE))
	return _("{0} cannot be used for posting. {1}").format(label, _(CONTACT_FINANCE))


def component_label(abbr):
	name = engine.COMPONENTS.get(abbr, {}).get("name") or abbr
	friendly = FRIENDLY.get(abbr)
	return "{0} — {1} ({2})".format(friendly, name, abbr) if friendly else "{0} ({1})".format(name, abbr)


def requirement(abbr):
	"""What a component needs from the chart of accounts."""
	comp = engine.COMPONENTS.get(abbr) or {}
	kind = comp.get("kind")
	types = EXPECTED_ROOT_TYPE.get(kind, ())
	return {
		"abbr": abbr,
		"component": comp.get("name") or abbr,
		"label": component_label(abbr),
		"kind": kind,
		"required_types": list(types),
		"required": _(" or ").join(_(t) for t in types),
		"where": WHERE_COMPONENTS,
	}


def account_problem(account, required_types, company=None, currency=None):
	"""Why ``account`` cannot be posted to, or None when it can.

	Mirrors the checks Settings applies on save, because an account can change after it
	was mapped (disabled, frozen, turned into a group, re-typed) and the mapping can be
	written by other means than the Settings form.
	"""
	if not account:
		return _("not configured")
	row = frappe.db.get_value(
		"Account", account,
		["company", "root_type", "is_group", "disabled", "freeze_account", "account_currency"],
		as_dict=True)
	if not row:
		return _("account {0} does not exist").format(account)
	problems = []
	if company and row.company != company:
		problems.append(_("belongs to {0}, not to {1}").format(row.company, company))
	if cint(row.is_group):
		problems.append(_("is a group account — postings need a leaf account"))
	if cint(row.disabled):
		problems.append(_("is disabled"))
	if row.get("freeze_account") == "Yes":
		problems.append(_("is frozen"))
	if required_types and row.root_type not in required_types:
		problems.append(_("is {0}; {1} is required").format(
			_(row.root_type or "?"), _(" or ").join(_(t) for t in required_types)))
	if currency and row.account_currency and row.account_currency != currency:
		problems.append(_("is in {0} but payroll is in {1}").format(row.account_currency, currency))
	if problems:
		return _("account {0} {1}").format(account, "; ".join(problems))
	return None


def _settings(settings=None):
	return settings or frappe.get_cached_doc("Isoft HR Settings")


def _mapping(settings):
	return {r.abbr: r.account for r in settings.get("component_accounts") or [] if r.account}


def _currency(settings, company):
	return settings.get("currency") or (
		frappe.db.get_value("Company", company, "default_currency") if company else None)


def components_on(slips):
	"""The components that will produce accrual lines for these slips, in ledger order."""
	needed = []

	def add(abbr):
		if abbr and abbr not in needed:
			needed.append(abbr)

	for slip in slips:
		for e in slip.get("earnings") or []:
			if not cint(e.get("do_not_include_in_total")) and flt(e.get("amount")):
				add((e.get("abbr") or "").strip())
		for d in slip.get("deductions") or []:
			if flt(d.get("amount")):
				add((d.get("abbr") or "").strip())
		if flt(slip.get("ss_employer_amount")):
			add("CTSSE")
			add("CTSSP")
	return needed


def accrual_problems(slips, settings=None):
	"""Every reason the accrual of these slips cannot be posted, one per account."""
	slips = list(slips)
	if not slips:
		return []
	s = _settings(settings)
	company = slips[0].get("company")
	currency = _currency(s, company)
	mapping = _mapping(s)
	out = []
	for abbr in components_on(slips):
		req = requirement(abbr)
		problem = account_problem(mapping.get(abbr), req["required_types"], company, currency)
		if problem:
			out.append(dict(req, key="account:" + abbr, account=mapping.get(abbr), problem=problem))

	payables = {s.get("payroll_payable_account")}
	for slip in slips:
		override = frappe.db.get_value("Employee", slip.get("employee"), "custom_payroll_payable_account") \
			if frappe.get_meta("Employee").has_field("custom_payroll_payable_account") else None
		if override:
			payables.add(override)
	for account in sorted(a or "" for a in payables):
		problem = account_problem(account or None, ("Liability",), company, currency)
		if problem:
			out.append({
				"key": "payroll_payable_account", "abbr": None,
				"label": _("Payroll Payable (net pay)"), "required": _("Liability"),
				"required_types": ["Liability"], "where": WHERE_PAYABLE,
				"account": account or None, "problem": problem,
			})
	return out


def payment_problems(slips, settings=None, payment_account=None):
	"""Every reason the payment of these slips cannot be posted."""
	slips = list(slips)
	if not slips:
		return []
	s = _settings(settings)
	company = slips[0].get("company")
	currency = _currency(s, company)
	out = []
	for slip in slips:
		payable = None
		if frappe.get_meta("Employee").has_field("custom_payroll_payable_account"):
			payable = frappe.db.get_value("Employee", slip.get("employee"), "custom_payroll_payable_account")
		payable = payable or s.get("payroll_payable_account")
		problem = account_problem(payable, ("Liability",), company, currency)
		if problem and not any(o["key"] == "payroll_payable_account" and o["account"] == payable for o in out):
			out.append({"key": "payroll_payable_account", "abbr": None,
			            "label": _("Payroll Payable (net pay)"), "required": _("Liability"),
			            "required_types": ["Liability"], "where": WHERE_PAYABLE,
			            "account": payable, "problem": problem})
	bank = payment_account or s.get("salary_payment_account")
	problem = account_problem(bank, ("Asset", "Liability"), company, currency)
	if problem:
		out.append({"key": "salary_payment_account", "abbr": None,
		            "label": _("Salary Payment account (bank / cash)"),
		            "required": _("Asset or Liability"), "required_types": ["Asset", "Liability"],
		            "where": WHERE_BANK, "account": bank, "problem": problem})
	return out


def _refuse(problems, title, lead):
	"""Business language first (what HR reads), the technical detail after it (what Finance
	needs to fix it): the component, what is wrong with the account, the nature it must
	have and where it is configured."""
	business = []
	detail = []
	for p in problems:
		business.append("• " + business_message(p))
		detail.append(_("• {0}: {1} — required: {2} — configure in {3}").format(
			p["label"], p["problem"], p["required"], p["where"]))
	frappe.throw(
		"{0}<br><br>{1}<br><br><span class='text-muted small'>{2}<br>{3}</span>".format(
			lead, "<br>".join(business), _("Details for Finance:"), "<br>".join(detail)),
		title=title)


def assert_postable(slips, settings=None):
	"""Refuse to post while any account the accrual needs is missing or unusable.

	Called BEFORE any Journal Entry is built, and for a bulk run before the first slip,
	so a half-mapped chart can never post half a payroll.
	"""
	problems = accrual_problems(slips, settings)
	if problems:
		names = ", ".join(p["label"].split(" (")[0] for p in problems[:4])
		_refuse(problems, _("Cannot Post Payroll"),
		        _("Cannot post payroll. {0} account(s) are not configured or not usable: {1}. "
		          "Payroll cannot proceed until Finance configuration is complete.").format(
			        len(problems), names))
	return True


def assert_payable(slips, settings=None, payment_account=None):
	"""Refuse to pay while the payable or the bank account is missing or unusable."""
	problems = payment_problems(slips, settings, payment_account=payment_account)
	if problems:
		_refuse(problems, _("Cannot Pay Payroll"),
		        _("Cannot post the salary payment. {0} account(s) are not configured or not "
		          "usable. Payroll cannot proceed until Finance configuration is "
		          "complete.").format(len(problems)))
	return True


# --------------------------------------------------------------------------- #
# History — read only
# --------------------------------------------------------------------------- #
def voucher_accounts(vouchers):
	"""Accounts actually hit by these vouchers, with their movement."""
	if not vouchers:
		return {}
	rows = frappe.db.sql(
		"""select account, round(sum(debit), 2) as debit, round(sum(credit), 2) as credit,
		          count(*) as `lines`
		from `tabJournal Entry Account`
		where parent in ({0}) group by account""".format(", ".join(["%s"] * len(vouchers))),
		list(vouchers), as_dict=True)
	return {r.account: {"debit": flt(r.debit), "credit": flt(r.credit), "lines": cint(r.lines)}
	        for r in rows}


def mapping_drift(entry, accrual_vouchers, payment_vouchers=None, settings=None):
	"""Has the mapping moved since this run was posted?

	Compares the accounts the run's own vouchers hit with the accounts the current
	mapping would use for the same components. The vouchers are the record and are
	never rewritten; drift only means the configuration in force today is not the one
	the run was booked with, which is what Finance has to know before trusting a
	reconciliation that reads today's mapping.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	s = _settings(settings)
	mapping = _mapping(s)
	used = voucher_accounts(list(accrual_vouchers or []) + list(payment_vouchers or []))

	posted = [r["name"] for r in wf.slip_rows(entry) if cint(r["docstatus"]) == 1 and r.get("journal_entry")]
	needed = []
	if posted:
		placeholders = ", ".join(["%s"] * len(posted))
		for (abbr,) in frappe.db.sql(
			"""select distinct abbr from `tabIsoft Salary Detail`
			where parent in ({0}) and ifnull(do_not_include_in_total, 0) = 0 and ifnull(amount, 0) != 0
			order by parentfield desc, idx""".format(placeholders), posted):
			if abbr and abbr not in needed:
				needed.append(abbr)
		if frappe.db.sql("select 1 from `tabIsoft Salary Slip` where name in ({0}) "
		                 "and ifnull(ss_employer_amount, 0) > 0 limit 1".format(placeholders), posted):
			needed += [a for a in ("CTSSE", "CTSSP") if a not in needed]
	expected = {}
	for abbr in needed:
		expected[mapping.get(abbr)] = abbr
	expected[s.get("payroll_payable_account")] = "payroll_payable_account"
	if payment_vouchers:
		expected[s.get("salary_payment_account")] = "salary_payment_account"
	expected.pop(None, None)

	posted_elsewhere = [{"account": a, **m} for a, m in sorted(used.items()) if a not in expected]
	mapped_unused = [{"abbr": abbr, "account": account} for account, abbr in sorted(expected.items())
	                 if account not in used]
	unmapped = [abbr for abbr in needed if not mapping.get(abbr)]
	return {
		"drifted": bool(posted_elsewhere or mapped_unused or unmapped),
		"posted_to_unmapped_accounts": posted_elsewhere,
		"mapped_accounts_without_postings": mapped_unused,
		"components_now_unmapped": unmapped,
	}


# --------------------------------------------------------------------------- #
# Finance checklist
# --------------------------------------------------------------------------- #
READY = "READY"
BLOCKED = "BLOCKED"

#: The nine things Finance confirms before a payroll is approved, in the order they are
#: shown. Keys are stable: the dashboard, production readiness and the tests read them.
CHECKLIST_ORDER = ("mapping_complete", "employee_inss", "employer_inss", "advance", "payable",
                   "bank", "mapping_drift", "components", "readiness")


def last_posted_run(company):
	"""The most recent payroll of this company that reached the ledger, or None."""
	return frappe.db.get_value(
		"Isoft Payroll Entry",
		{"company": company, "status": ("in", ("Posted", "Payment Ready", "Paid", "Closed"))},
		["name", "status", "end_date"], order_by="end_date desc, name desc", as_dict=True)


class unlogged_mapping(object):
	"""Context under which a Settings save does NOT write mapping-change records.

	Exists for the test fixture alone: it points the mapping at its own throw-away
	accounts and back again, and those moves are not Finance decisions. Outside a test
	run the context has no effect — production always logs.
	"""

	def __enter__(self):
		self._previous = frappe.flags.get("isoft_unlogged_mapping")
		frappe.flags.isoft_unlogged_mapping = True
		return self

	def __exit__(self, *exc):
		frappe.flags.isoft_unlogged_mapping = self._previous
		return False


def mapping_logging_suspended():
	return bool(frappe.flags.in_test and frappe.flags.get("isoft_unlogged_mapping"))


#: The log row Finance writes to say "the mapping in force is the one we want", which is
#: what clears drift against a run booked under an earlier mapping.
MAPPING_CONFIRMED = "mapping_confirmed"


def latest_mapping_change(company):
	"""The most recent recorded change of any payroll account mapping, or None."""
	if not frappe.db.exists("DocType", "Isoft Payroll Account Change"):
		return None
	rows = frappe.get_all(
		"Isoft Payroll Account Change",
		filters={"company": company, "account_key": ("!=", MAPPING_CONFIRMED)},
		fields=["name", "account_key", "label", "changed_by", "changed_on", "old_account",
		        "new_account", "after_posting", "posted_payroll"],
		order_by="changed_on desc, name desc", limit=1)
	return rows[0] if rows else None


def mapping_confirmation(company):
	"""The most recent Finance confirmation of the current mapping, or None."""
	if not frappe.db.exists("DocType", "Isoft Payroll Account Change"):
		return None
	rows = frappe.get_all(
		"Isoft Payroll Account Change",
		filters={"company": company, "account_key": MAPPING_CONFIRMED},
		fields=["name", "changed_by", "changed_on", "posted_payroll", "note"],
		order_by="changed_on desc, name desc", limit=1)
	return rows[0] if rows else None


def mapping_snapshot(settings=None):
	"""The mapping in force, as one line, for the confirmation record."""
	s = _settings(settings)
	parts = ["{0}={1}".format(r.abbr, r.account or "—") for r in s.get("component_accounts") or [] if r.abbr]
	parts.append("payable={0}".format(s.get("payroll_payable_account") or "—"))
	parts.append("bank={0}".format(s.get("salary_payment_account") or "—"))
	return "; ".join(parts)


def drift_confirmed(company, last_posted=None, settings=None):
	"""Has Finance confirmed the mapping in force since it last moved?

	A confirmation counts only if it is later than the last recorded mapping change and
	later than the posting of the last posted run, and if the mapping it confirmed is
	the mapping in force now. Anything else is stale, and stale is the same as absent.
	"""
	confirmation = mapping_confirmation(company)
	if not confirmation:
		return None
	last_change = latest_mapping_change(company)
	if last_change and str(last_change.changed_on) > str(confirmation.changed_on):
		return None
	if last_posted:
		posted_at = frappe.db.get_value("Isoft Payroll Entry", last_posted.name, "posted_at")
		if posted_at and str(posted_at) > str(confirmation.changed_on):
			return None
	if (confirmation.note or "") != mapping_snapshot(settings):
		return None
	return confirmation


def drift_against_last_posted(company, settings=None):
	"""Mapping drift of the last posted run of ``company`` under the mapping in force."""
	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	last = last_posted_run(company)
	if not last:
		return None, None
	entry = frappe.get_doc("Isoft Payroll Entry", last.name)
	rows = [r for r in wf.slip_rows(entry) if cint(r["docstatus"]) == 1]
	accruals = sorted({r["journal_entry"] for r in rows if r.get("journal_entry")})
	payments = sorted({r["payment_entry"] for r in rows if r.get("payment_entry")})
	if not accruals:
		return last, None
	return last, mapping_drift(entry, accruals, payments, settings)


def _readiness_blockers(company, entry=None, period=None, report=None):
	"""Blocking readiness exceptions for the period a payroll would run in.

	The full evaluation needs ``payroll.preview``; a Finance user reading the checklist
	does not hold it, so for them the answer is assembled from the structural counts
	(missing or ambiguous salary profiles) and the configuration — the same blockers,
	without the per-employee simulation.
	"""
	from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
	from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

	if report is None and perms.can(perms.PAYROLL_PREVIEW):
		only = None
		if entry is not None:
			start, end = entry.get("start_date"), entry.get("end_date")
			only = [r.employee for r in entry.get("employees") or [] if r.get("employee")]
		elif period:
			start, end = period
		else:
			from isoft_angola_hr.isoft_angola_hr import api
			start, end = api._default_cycle_period()
		report = readiness.evaluate(company, start, end, include_variance=False, employees=only)
	if report is not None:
		exceptions = [e for e in report["exceptions"] if e["severity"] == readiness.BLOCKING]
		if entry is not None:
			members = {r.employee for r in entry.get("employees") or []}
			exceptions = [e for e in exceptions if not e.get("employee") or e["employee"] in members]
			if entry.get("run_type") == "Christmas Bonus":
				# Working days, the month's net, a mid-period salary change and "already
				# processed" are about the salary run of the period; the bonus slip has its
				# own checks (one bonus a year, the profile, the ceiling).
				exceptions = [e for e in exceptions if e.get("code") not in readiness.SALARY_ONLY]
		return exceptions
	# No preview permission: counts only.
	from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr
	out = []
	for c in pr.data_counts(company):
		if c["status"] == pr.BLOCKED:
			out.append({"code": c["key"], "label": c["label"], "severity": readiness.BLOCKING,
			            "employee": None, "employee_name": None,
			            "message": "{0}: {1}".format(c["label"], c["current"])})
	for c in readiness.configuration_status(company):
		if not c["ok"] and c["severity"] == readiness.BLOCKING:
			out.append({"code": "EXC-006", "label": c["label"], "severity": readiness.BLOCKING,
			            "employee": None, "employee_name": None,
			            "message": "{0}: {1}".format(c["label"], c["status"])})
	return out


def finance_checklist(company, settings=None, entry=None, period=None, readiness_report=None,
                      include_readiness=True):
	"""What Finance must confirm before a payroll is approved. Plain facts, no choices.

	Nine items, each with ``ok``, a technical ``detail`` (for Finance) and ``reasons`` in
	business language (for HR). ``entry`` scopes the readiness item to that run; without
	it the next payroll period is assessed.
	"""
	s = _settings(settings)
	currency = _currency(s, company)
	mapping = _mapping(s)
	items = []

	def item(key, label, problems, where, reasons=None):
		items.append({
			"key": key, "label": label, "ok": not problems,
			"detail": "; ".join(problems) if problems else _("configured"),
			"where": where,
			"reasons": reasons if reasons is not None else [],
		})

	def account_item(key, label, abbr):
		req = requirement(abbr)
		problem = account_problem(mapping.get(abbr), req["required_types"], company, currency)
		row = dict(req, key="account:" + abbr, account=mapping.get(abbr), problem=problem)
		item(key, label,
		     ["{0}: {1} (required: {2})".format(req["label"], problem, req["required"])] if problem else [],
		     WHERE_COMPONENTS, [business_message(row)] if problem else [])

	# 1. the whole mapping: every component that can produce a ledger line, the payable
	#    and the bank. One line, so the answer to "is Finance configuration complete" is
	#    a single READY or BLOCKED.
	all_problems, all_reasons = [], []
	for jc in engine.journal_components():
		req = requirement(jc["abbr"])
		problem = account_problem(mapping.get(jc["abbr"]), req["required_types"], company, currency)
		if problem:
			all_problems.append("{0}: {1}".format(req["label"], problem))
			all_reasons.append(business_message(dict(req, account=mapping.get(jc["abbr"]))))
	for key, field, types, where in (
			("payroll_payable_account", "payroll_payable_account", ("Liability",), WHERE_PAYABLE),
			("salary_payment_account", "salary_payment_account", ("Asset", "Liability"), WHERE_BANK)):
		problem = account_problem(s.get(field), types, company, currency)
		if problem:
			all_problems.append("{0}: {1}".format(business_label(key), problem))
			all_reasons.append(business_message({"key": key, "account": s.get(field)}))
	item("mapping_complete", _("Payroll account mapping complete"), all_problems,
	     WHERE_COMPONENTS, all_reasons)

	# 2. employee INSS liability
	account_item("employee_inss", _("Employee INSS liability account configured"), "CTSS3")

	# 3. employer INSS — expense (and the payable it is booked against)
	inss_problems, inss_reasons = [], []
	for abbr in ("CTSSE", "CTSSP"):
		req = requirement(abbr)
		problem = account_problem(mapping.get(abbr), req["required_types"], company, currency)
		if problem:
			inss_problems.append("{0}: {1} (required: {2})".format(req["label"], problem, req["required"]))
			inss_reasons.append(business_message(dict(req, account=mapping.get(abbr))))
	item("employer_inss", _("Employer INSS expense account configured (and its payable)"),
	     inss_problems, WHERE_COMPONENTS, inss_reasons)

	# 4. advance
	account_item("advance", _("Salary Advance account configured"), "ADT")

	# 5. payroll payable
	problem = account_problem(s.get("payroll_payable_account"), ("Liability",), company, currency)
	item("payable", _("Payroll Payable account configured"), [problem] if problem else [], WHERE_PAYABLE,
	     [business_message({"key": "payroll_payable_account", "account": s.get("payroll_payable_account")})]
	     if problem else [])

	# 6. bank
	problem = account_problem(s.get("salary_payment_account"), ("Asset", "Liability"), company, currency)
	item("bank", _("Bank account configured"), [problem] if problem else [], WHERE_BANK,
	     [business_message({"key": "salary_payment_account", "account": s.get("salary_payment_account")})]
	     if problem else [])

	# 7. drift against the previous (last posted) payroll — cleared only by Finance
	#    confirming, on record, that the mapping in force is the intended one.
	last, drift = drift_against_last_posted(company, s)
	confirmation = drift_confirmed(company, last, s) if (last and drift and drift["drifted"]) else None
	if last and drift and drift["drifted"] and not confirmation:
		moved = ", ".join(d["account"] for d in drift["posted_to_unmapped_accounts"]) or "—"
		item("mapping_drift", _("No mapping drift against the previous payroll ({0})").format(last.name),
		     [_("{0} was posted to accounts that are no longer mapped: {1}").format(last.name, moved)],
		     WHERE_COMPONENTS,
		     [_("The payroll accounts were changed after the previous payroll ({0}) was posted. "
		        "Finance must confirm the current accounts before the next payroll is "
		        "approved.").format(last.name)])
	elif confirmation:
		item("mapping_drift", _("No mapping drift against the previous payroll ({0})").format(last.name),
		     [], WHERE_COMPONENTS)
		items[-1]["detail"] = _("{0} was posted under an earlier mapping; the current mapping was "
		                        "confirmed by {1} on {2}").format(
			last.name, confirmation.changed_by, confirmation.changed_on)
	else:
		item("mapping_drift",
		     _("No mapping drift against the previous payroll ({0})").format(last.name)
		     if last else _("No mapping drift against the previous payroll"),
		     [], WHERE_COMPONENTS)
		if not last:
			items[-1]["detail"] = _("no payroll has been posted yet")

	# 8. every salary component mapped (as distinct from the ones this run uses)
	comp_problems, comp_reasons = [], []
	for jc in engine.journal_components():
		req = requirement(jc["abbr"])
		problem = account_problem(mapping.get(jc["abbr"]), req["required_types"], company, currency)
		if problem:
			comp_problems.append("{0}: {1}".format(req["label"], problem))
			comp_reasons.append(business_message(dict(req, account=mapping.get(jc["abbr"]))))
	item("components", _("All salary components mapped"), comp_problems, WHERE_COMPONENTS, comp_reasons)

	# 9. blocking readiness exceptions for the period
	if include_readiness:
		blockers = _readiness_blockers(company, entry=entry, period=period, report=readiness_report)
		by_code = {}
		for b in blockers:
			by_code.setdefault(b["code"], []).append(b)
		problems = ["{0} — {1} ({2})".format(code, rows[0]["label"], len(rows))
		            for code, rows in sorted(by_code.items())]
		reasons = []
		for code, rows in sorted(by_code.items()):
			who = [r["employee_name"] or r["employee"] for r in rows if r.get("employee")]
			if who:
				reasons.append(_("{0}: {1} employee(s) — {2}{3}").format(
					_(rows[0]["label"]), len(who), ", ".join(who[:5]), "…" if len(who) > 5 else ""))
			else:
				reasons.append(rows[0]["message"])
		item("readiness", _("No blocking readiness exceptions"), problems,
		     _("Payroll → Payroll Readiness"), reasons)
	return items


def finance_approval_status(company, settings=None, entry=None, period=None, readiness_report=None,
                            include_readiness=True):
	"""READY or BLOCKED, with the exact reasons — the Finance approval checklist as one
	answer. ``reasons`` are business sentences; ``items`` carry the detail."""
	items = finance_checklist(company, settings, entry=entry, period=period,
	                          readiness_report=readiness_report, include_readiness=include_readiness)
	reasons = []
	for i in items:
		if i["ok"]:
			continue
		for r in i["reasons"] or [i["detail"]]:
			if r not in reasons:
				reasons.append(r)
	blocked = [i for i in items if not i["ok"]]
	return {
		"status": BLOCKED if blocked else READY,
		"ok": not blocked,
		"items": items,
		"blocked_items": [i["key"] for i in blocked],
		"reasons": reasons,
		"headline": _("Payroll cannot proceed until Finance configuration is complete.")
		if blocked else _("Finance configuration is complete."),
		"last_posted": (last_posted_run(company) or {}).get("name"),
	}
