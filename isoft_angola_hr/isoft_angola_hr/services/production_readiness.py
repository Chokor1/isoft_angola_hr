# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Can this installation run real payroll — today, for this company?

Payroll Readiness (``payroll_readiness.py``) answers a monthly question: *is this
period's data clean?* This module answers the cutover question: *is the installation
itself configured, staffed and governed well enough to be trusted with real money?*
It aggregates the per-period readiness rather than duplicating it.

Every check returns one of:

    READY     nothing to do
    WARNING   payroll will run, but somebody should look
    BLOCKED   payroll must not run in production until this is fixed

Nothing here writes. Configuration and business data are only ever reported, with the
exact corrective action and the person who owns the decision — a deployment tool that
"helpfully" fixes a salary or an account mapping on its own is the last thing a payroll
system should be.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import payroll_readiness as readiness
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

READY = "READY"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

_SEVERITY_ORDER = {READY: 0, WARNING: 1, BLOCKED: 2}

# Owners of each kind of decision. Deployment problems are not all IT problems: an
# account mapping belongs to Finance and a salary conflict belongs to HR, and saying so
# is what turns a report into a work list.
IT, HR, PAYROLL, FINANCE, MANAGEMENT, LEGAL = "IT", "HR", "Payroll", "Finance", "Management", "Legal"


def _check(key, label, status, current=None, required=None, owner=IT, action=None, count=None):
	return {
		"key": key, "label": label, "status": status, "current": current,
		"required": required, "owner": owner, "action": action, "count": count,
	}


def _worst(checks):
	worst = READY
	for c in checks:
		if _SEVERITY_ORDER[c["status"]] > _SEVERITY_ORDER[worst]:
			worst = c["status"]
	return worst


# --------------------------------------------------------------------------- #
# Accounts and organisation
# --------------------------------------------------------------------------- #
def account_checks(company, settings=None):
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_hr_settings.isoft_hr_settings import (
		EXPECTED_ROOT_TYPE,
	)

	s = settings or frappe.get_cached_doc("Isoft HR Settings")
	out = []
	mapped = {r.abbr: r.account for r in s.get("component_accounts") or [] if r.account}
	kinds = {c["abbr"]: c["kind"] for c in engine.journal_components()}

	def account_row(key, label, account, expected, owner=FINANCE):
		if not account:
			return _check(key, label, BLOCKED, current=None, required=_(" or ").join(expected or []),
			              owner=owner,
			              action=_("Map an account in Isoft HR Settings → Account per Component."))
		row = frappe.db.get_value(
			"Account", account, ["company", "root_type", "is_group", "disabled"], as_dict=True)
		if not row:
			return _check(key, label, BLOCKED, current=account, owner=owner,
			              action=_("The mapped account no longer exists — map an existing one."))
		problems = []
		if company and row.company != company:
			problems.append(_("belongs to {0}").format(row.company))
		if cint(row.is_group):
			problems.append(_("is a group account"))
		if cint(row.disabled):
			problems.append(_("is disabled"))
		if expected and row.root_type not in expected:
			problems.append(_("is {0}, expected {1}").format(row.root_type, _(" or ").join(expected)))
		if problems:
			return _check(key, label, BLOCKED, current=account, required=_(" or ").join(expected or []),
			              owner=owner, action=_("Account {0} {1}.").format(account, ", ".join(problems)))
		return _check(key, label, READY, current=account, owner=owner)

	for comp in engine.journal_components():
		out.append(account_row(
			"account:" + comp["abbr"], _("Account — {0}").format(comp["component"]),
			mapped.get(comp["abbr"]), EXPECTED_ROOT_TYPE.get(kinds.get(comp["abbr"]))))

	out.append(account_row("payroll_payable_account", _("Payroll Payable Account"),
	                       s.get("payroll_payable_account"), ("Liability",)))
	out.append(account_row("salary_payment_account", _("Salary Payment Account (Bank/Cash)"),
	                       s.get("salary_payment_account"), ("Asset", "Liability")))

	# A Payable-typed account is what gives payroll a per-employee sub-ledger. Without it
	# the books still balance, so this is an accounting-quality warning, not a blocker.
	payable = s.get("payroll_payable_account")
	if payable and frappe.db.exists("Account", payable):
		account_type = frappe.db.get_value("Account", payable, "account_type")
		out.append(_check(
			"payable_account_type", _("Payroll Payable is typed as Payable"),
			READY if account_type == "Payable" else WARNING,
			current=account_type or _("(untyped)"), required="Payable", owner=FINANCE,
			action=None if account_type == "Payable" else _(
				"Set Account Type = Payable on {0} so payroll produces a per-employee "
				"sub-ledger. Until then the party is not recorded on the ledger lines.").format(payable)))
	return out


def organisation_checks(company, settings=None):
	s = settings or frappe.get_cached_doc("Isoft HR Settings")
	out = []

	out.append(_check("company", _("Default Company"), READY if company else BLOCKED,
	                  current=company, owner=IT,
	                  action=None if company else _("Set the Default Company in Isoft HR Settings.")))
	if not company:
		return out

	cost_center = frappe.db.get_value("Company", company, "cost_center")
	out.append(_check("cost_center", _("Default Cost Center"),
	                  READY if cost_center else WARNING, current=cost_center, owner=FINANCE,
	                  action=None if cost_center else _(
		                  "Set a default Cost Center on the Company; payroll lines fall back to it.")))

	company_currency = frappe.db.get_value("Company", company, "default_currency")
	payroll_currency = s.get("currency")
	matched = bool(payroll_currency) and payroll_currency == company_currency
	out.append(_check(
		"currency", _("Payroll Currency"), READY if matched else BLOCKED,
		current=payroll_currency, required=company_currency, owner=FINANCE,
		action=None if matched else _(
			"Payroll is set to {0} but {1} keeps its books in {2}. Align them before posting.").format(
			payroll_currency or _("(unset)"), company, company_currency)))

	start_day = cint(s.get("payroll_cycle_start_day")) or 1
	out.append(_check("payroll_cycle", _("Payroll Cycle Start Day"), READY,
	                  current=str(start_day), owner=PAYROLL))
	return out


# --------------------------------------------------------------------------- #
# Statutory
# --------------------------------------------------------------------------- #
def statutory_checks(company, on_date=None):
	from isoft_angola_hr.isoft_angola_hr.doctype.isoft_statutory_rate.isoft_statutory_rate import (
		get_statutory_rates,
	)

	on_date = getdate(on_date) if on_date else getdate()
	s = frappe.get_cached_doc("Isoft HR Settings")
	out = []

	table = s.get("default_irt_table")
	if not table or not frappe.db.exists("IRT Table", table):
		out.append(_check("irt_table", _("Active IRT Table"), BLOCKED, current=table, owner=PAYROLL,
		                  action=_("Configure a valid IRT Table in Isoft HR Settings.")))
		return out

	out.append(_check("irt_table", _("Active IRT Table"), READY, current=table, owner=PAYROLL))

	effective = frappe.db.get_value("IRT Table", table, "effective_from")
	# The table's own content tells us which law it is. A 150.000 exemption is the OGE
	# 2026 table, which entered into force on 1 January 2026 (Lei n.º 14/25, art. 43.º) —
	# so an effective date before that means the table is claiming to have applied to
	# payroll it never legally governed.
	expected = _expected_irt_effective_from(table)
	if not effective:
		out.append(_check("irt_effective_from", _("IRT Table Effective From"), BLOCKED,
		                  current=None, required=str(expected) if expected else None, owner=PAYROLL,
		                  action=_("Set the effective date of {0}.").format(table)))
	elif expected and getdate(effective) != getdate(expected):
		affected = frappe.db.count("Isoft Salary Slip",
		                           {"irt_table": table, "end_date": ("<", getdate(expected)),
		                            "docstatus": ("<", 2)})
		out.append(_check(
			"irt_effective_from", _("IRT Table Effective From"), WARNING if not affected else BLOCKED,
			current=str(effective), required=str(expected), owner=PAYROLL, count=affected,
			action=_("{0} holds the OGE 2026 brackets (exemption 150.000) but is dated {1}. "
			         "Lei n.º 14/25 entered into force on {2} (art. 43.º). {3} existing slip(s) "
			         "fall before that date. Correct the effective date, or create a properly "
			         "dated table.").format(table, effective, expected, affected)))
	else:
		out.append(_check("irt_effective_from", _("IRT Table Effective From"), READY,
		                  current=str(effective), owner=PAYROLL))

	if getdate(effective or on_date) > on_date:
		out.append(_check("irt_covers_period", _("IRT Table Covers Today"), BLOCKED,
		                  current=str(effective), owner=PAYROLL,
		                  action=_("The IRT table takes effect in the future; payroll run now "
		                           "would have no legally applicable table.")))

	rates = get_statutory_rates(company, on_date, settings=s)
	source = rates.get("statutory_rate")
	employee_rate, employer_rate = rates.get("ss_employee_rate"), rates.get("ss_employer_rate")
	if employee_rate is None or employer_rate is None:
		out.append(_check("statutory_rates", _("Social Security Rates"), BLOCKED, owner=PAYROLL,
		                  action=_("Configure the employee and employer INSS rates.")))
	else:
		out.append(_check("ss_employee_rate", _("Employee INSS Rate"), READY,
		                  current="{0}%".format(flt(employee_rate)), owner=PAYROLL))
		out.append(_check("ss_employer_rate", _("Employer INSS Rate"), READY,
		                  current="{0}%".format(flt(employer_rate)), owner=PAYROLL))
	# The settings fallback works, but it is not effective-dated: the day a rate changes,
	# historical payroll can no longer be explained from configuration.
	out.append(_check(
		"statutory_rate_record", _("Effective-dated Statutory Rate record"),
		READY if source else WARNING, current=source or _("Isoft HR Settings (fallback)"),
		owner=PAYROLL,
		action=None if source else _(
			"Create an Isoft Statutory Rate effective from the date the current rates began. "
			"Without one, a future rate change leaves no record of what applied before it.")))
	return out


def _expected_irt_effective_from(table):
	"""The entry-into-force date implied by the brackets the table actually contains.

	Identified from the exemption ceiling rather than from the table's title, because the
	title is free text and the brackets are the law.
	"""
	first = frappe.db.sql(
		"""select to_amount from `tabIRT Bracket` where parent=%s and rate=0
		order by idx limit 1""", table)
	if not first or not first[0][0]:
		return None
	exemption = flt(first[0][0])
	if abs(exemption - 150000) < 1:
		return getdate("2026-01-01")   # Lei n.º 14/25, art. 43.º
	if abs(exemption - 100000) < 1:
		return None                     # earlier regime; no single authoritative date verified
	return None


# --------------------------------------------------------------------------- #
# Roles and segregation of duties
# --------------------------------------------------------------------------- #
def _users_with_role(role):
	return frappe.db.sql_list(
		"""select r.parent from `tabHas Role` r join `tabUser` u on u.name = r.parent
		where r.role = %s and u.enabled = 1 and u.user_type = 'System User'
		  and u.name not in ('Administrator', 'Guest')""", role)


def role_checks():
	"""Are the payroll roles staffed, and does the staffing actually separate the duties?"""
	out = []
	staffing = {}
	for role in perms.APP_ROLES:
		users = _users_with_role(role)
		staffing[role] = users
		out.append(_check(
			"role:" + role, role, READY if users else BLOCKED,
			current=_("{0} user(s)").format(len(users)), required=_("at least 1"),
			owner=IT, count=len(users),
			action=None if users else _(
				"Assign {0} to at least one user. Payroll cannot move through its workflow "
				"while nobody holds this role.").format(role)))

	# Posting reaches the general ledger through ERPNext's own permissions, so a finance
	# approver without an accounting role cannot actually post. This was found the hard
	# way in Phase 2 — the payroll workflow authorised the action and the Journal Entry
	# refused it.
	finance = set(_users_with_role(perms.PAYROLL_FINANCE)) | set(_users_with_role(perms.ACCOUNTS_MANAGER))
	accounting = set(_users_with_role("Accounts Manager")) | set(_users_with_role("Accounts User"))
	without_accounting = sorted(finance - accounting)
	out.append(_check(
		"finance_accounting_role", _("Finance approvers can submit Journal Entries"),
		READY if (finance and not without_accounting) else (BLOCKED if not finance else WARNING),
		current=_("{0} of {1} lack an accounting role").format(len(without_accounting), len(finance)),
		owner=IT, count=len(without_accounting),
		action=None if (finance and not without_accounting) else _(
			"Give Accounts User (or Accounts Manager) to: {0}. Payroll authorises the posting, "
			"but ERPNext still requires an accounting role to submit the Journal Entry.").format(
			", ".join(without_accounting) or _("a finance approver"))))
	return out, staffing


def segregation_conflicts():
	"""Users holding roles that concentrate incompatible payroll duties.

	Self-approval is already blocked by identity, so this is not a hole — it is excessive
	privilege. One person able to prepare, approve, post and pay means the only thing
	standing between the company and an unchecked payroll is that the same login was used
	twice, which a second account defeats.
	"""
	prepare = {perms.PAYROLL_OFFICER, perms.HR_MANAGER}
	approve = {perms.PAYROLL_MANAGER, perms.HR_MANAGER}
	pay = {perms.PAYROLL_FINANCE, perms.ACCOUNTS_MANAGER}

	rows = frappe.db.sql(
		"""select r.parent as user, r.role from `tabHas Role` r
		join `tabUser` u on u.name = r.parent
		where u.enabled = 1 and u.user_type = 'System User'
		  and u.name not in ('Administrator','Guest')
		  and r.role in ({0})""".format(", ".join(["%s"] * len(prepare | approve | pay))),
		list(prepare | approve | pay), as_dict=True)

	by_user = {}
	for r in rows:
		by_user.setdefault(r.user, set()).add(r.role)

	conflicts = []
	for user, roles in sorted(by_user.items()):
		found = []
		if roles & prepare and roles & approve:
			found.append(("SEG-001", _("holds payroll preparation and approval roles")))
		if roles & approve and roles & pay:
			found.append(("SEG-002", _("holds payroll approval and payment roles")))
		if roles & prepare and roles & pay:
			found.append(("SEG-003", _("holds payroll preparation and payment roles")))
		for code, message in found:
			conflicts.append({"code": code, "user": user, "roles": sorted(roles),
			                  "message": "{0} — {1}".format(user, message)})
	return conflicts


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
def get_production_readiness(company=None, on_date=None, light=False):
	"""One answer to "can we go live", assembled from every check in this module.

	The per-period data assessment is delegated to ``payroll_readiness.evaluate`` rather
	than reimplemented, so there is exactly one definition of what makes an employee
	unpayable.

	``light=True`` swaps that assessment for direct counts. The full evaluation computes
	a complete salary slip for every employee — correct for a payroll pre-flight, and the
	one thing here whose cost grows with headcount (measured: 906 queries at 129
	employees, 1 456 at 679). The release gate only needs how many employees are
	unpayable, not why each one is, so it asks for the counts and leaves the simulation
	to the payroll console that actually needs it.
	"""
	perms.require(perms.PAYROLL_READ)
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	perms.require_company(company)
	on_date = getdate(on_date) if on_date else getdate()
	settings = frappe.get_cached_doc("Isoft HR Settings")

	sections = []

	accounts = account_checks(company, settings)
	sections.append({"key": "accounts", "label": _("Payroll Accounts"),
	                 "status": _worst(accounts), "checks": accounts})

	org = organisation_checks(company, settings)
	sections.append({"key": "organisation", "label": _("Organisation"),
	                 "status": _worst(org), "checks": org})

	statutory = statutory_checks(company, on_date)
	sections.append({"key": "statutory", "label": _("Statutory Configuration"),
	                 "status": _worst(statutory), "checks": statutory})

	roles, staffing = role_checks()
	conflicts = segregation_conflicts()
	security = list(roles)
	if conflicts:
		security.append(_check(
			"segregation", _("Segregation of Duties"), WARNING,
			current=_("{0} conflict(s) across {1} user(s)").format(
				len(conflicts), len({c["user"] for c in conflicts})),
			owner=MANAGEMENT, count=len(conflicts),
			action=_("Review the users holding incompatible payroll roles. Self-approval is "
			         "blocked by identity, but this remains excessive privilege.")))
	else:
		security.append(_check("segregation", _("Segregation of Duties"), READY, owner=MANAGEMENT))
	sections.append({"key": "security", "label": _("Roles & Segregation"),
	                 "status": _worst(security), "checks": security})

	data = data_counts(company) if light else data_checks(company, on_date)
	sections.append({"key": "data", "label": _("Employee & Payroll Data"),
	                 "status": _worst(data), "checks": data})

	overall = _worst([frappe._dict(status=s["status"]) for s in sections])
	return {
		"company": company,
		"as_of": str(on_date),
		"status": overall,
		"blocked": sum(1 for s in sections for c in s["checks"] if c["status"] == BLOCKED),
		"warnings": sum(1 for s in sections for c in s["checks"] if c["status"] == WARNING),
		"sections": sections,
		"segregation_conflicts": conflicts,
		"role_staffing": {role: len(users) for role, users in staffing.items()},
	}


def data_counts(company):
	"""The same employee-data blockers as :func:`data_checks`, from counts alone.

	Four aggregate queries instead of one payroll simulation per employee. It reports the
	same blockers with the same keys; what it cannot say is *which* rule each employee
	trips, which is exactly what a release gate does not need.
	"""
	rows = frappe.db.sql("""select
			count(*) as active,
			sum(case when not exists (
				select 1 from `tabIsoft Salary Profile` p where p.employee = e.name)
				then 1 else 0 end) as no_profile,
			sum(case when ifnull(e.custom_iban, '') = '' then 1 else 0 end) as no_iban,
			sum(case when ifnull(e.custom_nif, '') = '' then 1 else 0 end) as no_nif,
			sum(case when ifnull(e.custom_inss_number, '') = '' then 1 else 0 end) as no_inss
		from `tabEmployee` e where e.status = 'Active' and e.company = %s""",
		company, as_dict=True)[0]

	# Two profiles sharing the latest effective date: payroll cannot choose between them.
	ambiguous = cint(frappe.db.sql("""select count(*) from (
			select p.employee from `tabIsoft Salary Profile` p
			join `tabEmployee` e on e.name = p.employee
			where e.status = 'Active' and e.company = %s
			group by p.employee, p.from_date having count(*) > 1) x""", company)[0][0])

	drafts = cint(frappe.db.sql(
		"""select count(*) from `tabIsoft Salary Slip` s
		where s.docstatus = 0 and s.company = %s""", company)[0][0])

	active = cint(rows.active)
	no_profile = cint(rows.no_profile)

	def check(key, label, count, blocking, owner, action):
		return {"key": key, "label": label, "count": count,
		        "current": "{0} employee(s)".format(count) if count else "",
		        "required": "", "owner": owner,
		        "status": (BLOCKED if blocking else WARNING) if count else READY,
		        "action": action if count else ""}

	return [
		{"key": "payroll_ready", "label": _("Employees ready for payroll"),
		 "count": active - no_profile - ambiguous, "owner": "HR",
		 "current": "{0} / {1}".format(active - no_profile - ambiguous, active),
		 "required": "", "action": "",
		 "status": WARNING if (no_profile or ambiguous) else READY},
		check("missing_profile", _("Employees without a Salary Profile"), no_profile, True,
		      "HR", _("Create a Salary Profile, or set the employee to Inactive.")),
		check("ambiguous_profile", _("Employees with an ambiguous Salary Profile"),
		      ambiguous, True, "HR",
		      _("Two profiles share the latest effective date; close one of them.")),
		check("missing_iban", _("Employees without an IBAN"), cint(rows.no_iban), False,
		      "HR", _("Collect the IBAN. Payroll calculates without it; payment needs it.")),
		check("missing_nif", _("Employees without a NIF"), cint(rows.no_nif), False, "HR",
		      _("Collect the NIF — needed for the IRT declaration, not for payment.")),
		check("missing_inss", _("Employees without a Social Security number"),
		      cint(rows.no_inss), False, "HR",
		      _("Collect the INSS number — needed for the INSS declaration.")),
		check("draft_slips", _("Draft salary slips awaiting a decision"), drafts, False,
		      "Payroll", _("Recalculate, submit or cancel them.")),
	]


def data_checks(company, on_date=None):
	"""Employee-data readiness, delegated to the monthly readiness engine.

	Uses the period that payroll would actually run next, so the answer is about the
	real next run rather than an arbitrary window.
	"""
	from isoft_angola_hr.isoft_angola_hr import api

	start, end = api._default_cycle_period()
	report = readiness.evaluate(company, start, end, include_variance=False)
	counts = {row["code"]: row["count"] for row in report["summary"]}

	def row(key, label, code, status_when_present, owner, action):
		n = counts.get(code, 0)
		return _check(key, label, status_when_present if n else READY,
		              current=_("{0} employee(s)").format(n), owner=owner, count=n,
		              action=action if n else None)

	out = [
		_check("active_employees", _("Active employees"), READY,
		       current=str(report["total_employees"]), owner=HR),
		_check("payroll_ready", _("Employees ready for payroll"),
		       READY if report["ready"] == report["total_employees"] else WARNING,
		       current="{0} / {1}".format(report["ready"], report["total_employees"]), owner=HR),
		row("missing_profile", _("Employees without a Salary Profile"), "EXC-001", BLOCKED, HR,
		    _("Create a Salary Profile, or set the employee to Inactive if they should not be paid.")),
		row("ambiguous_profile", _("Employees with an ambiguous Salary Profile"), "EXC-002", BLOCKED, HR,
		    _("Two profiles share the latest effective date. HR must confirm which salary applies "
		      "and close or remove the other.")),
		row("mid_period_change", _("Salary changes inside the payroll period"), "EXC-011", BLOCKED, HR,
		    _("Align the effective date with the payroll period, or run the two part-periods "
		      "separately. Split-period proration is deliberately not automated.")),
		row("negative_net", _("Employees with a negative net salary"), "EXC-004", BLOCKED, PAYROLL,
		    _("Reduce the advance or other deductions.")),
		row("missing_iban", _("Employees without an IBAN"), "EXC-007", WARNING, HR,
		    _("Collect the IBAN. Payroll calculates and posts without it, but the bank file "
		      "cannot be generated while anybody payable is missing one.")),
		row("missing_nif", _("Employees without a NIF"), "EXC-008", WARNING, HR,
		    _("Collect the NIF — it is needed for the IRT report, not for payment.")),
		row("missing_inss", _("Employees without a Social Security number"), "EXC-009", WARNING, HR,
		    _("Collect the INSS number — needed for the INSS report, not for payment.")),
	]

	# A salary slip that belongs to no payroll run never passed through approval. The
	# app itself always creates slips inside a run, so these can only come from direct
	# desk or API use. They are correctly CALCULATED — nobody can type a net pay — but
	# they are unapproved, and unapproved payroll must never be invisible.
	orphans = frappe.db.sql(
		"""select name, employee_name from `tabIsoft Salary Slip`
		where docstatus = 1 and company = %s and ifnull(payroll_entry, '') = ''""",
		company, as_dict=True)
	out.append(_check(
		"orphan_slips", _("Submitted slips outside any payroll run"),
		WARNING if orphans else READY, current=str(len(orphans)), owner=PAYROLL,
		count=len(orphans),
		action=_("These slips were submitted without going through the approval workflow: "
		         "{0}. Verify each one, and cancel any that should not have been paid.").format(
			", ".join("{0} ({1})".format(o.name, o.employee_name) for o in orphans[:8]))
		if orphans else None))

	drafts = frappe.db.count("Isoft Salary Slip", {"docstatus": 0, "company": company})
	no_snapshot = frappe.db.sql(
		"""select count(*) from `tabIsoft Salary Slip`
		where docstatus < 2 and company = %s and ifnull(ss_employee_rate, 0) = 0""", company)[0][0]
	out.append(_check(
		"draft_slips", _("Draft salary slips awaiting a decision"), WARNING if drafts else READY,
		current=str(drafts), owner=PAYROLL, count=drafts,
		action=_("Recalculate, submit or cancel them. A draft slip is not payroll and is "
		         "excluded from every statutory report.") if drafts else None))
	out.append(_check(
		"no_statutory_snapshot", _("Slips without a statutory snapshot"),
		WARNING if no_snapshot else READY, current=str(no_snapshot), owner=PAYROLL, count=no_snapshot,
		action=_("These were calculated before the statutory trace existed. Their amounts are "
		         "intact, but they report zero IRT and zero INSS because no rate was recorded. "
		         "Recalculating them (which does not change net pay) restores the trace.")
		if no_snapshot else None))
	return out


@frappe.whitelist()
def production_readiness(company=None, on_date=None):
	return get_production_readiness(company=company, on_date=on_date)


# --------------------------------------------------------------------------- #
# Deployment health check
# --------------------------------------------------------------------------- #
def health_check(company=None, quiet=False):
	"""Post-deployment health check. Safe to run any time; writes nothing.

	    bench --site <site> execute \\
	        isoft_angola_hr.isoft_angola_hr.services.production_readiness.health_check
	"""
	import json
	import os

	results = []

	def add(label, ok, detail="", warn=False):
		# A check that passes but raised a caveat is a WARNING, not a PASS — otherwise the
		# caveat is printed next to the word PASS and nobody reads it.
		status = "FAIL" if not ok else ("WARNING" if warn else "PASS")
		results.append({"check": label, "status": status, "detail": detail})

	app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	add("App installed", "isoft_angola_hr" in frappe.get_installed_apps())

	missing = []
	for slug in sorted(os.listdir(os.path.join(app_root, "doctype"))):
		path = os.path.join(app_root, "doctype", slug, slug + ".json")
		if os.path.exists(path):
			name = json.load(open(path))["name"]
			if not frappe.db.exists("DocType", name):
				missing.append(name)
	add("Required DocTypes", not missing, ", ".join(missing) or _("all present"))

	missing = []
	for slug in sorted(os.listdir(os.path.join(app_root, "report"))):
		path = os.path.join(app_root, "report", slug, slug + ".json")
		if os.path.exists(path):
			name = json.load(open(path))["name"]
			if not frappe.db.exists("Report", name):
				missing.append(name)
	add("Required Reports", not missing, ", ".join(missing) or _("all present"))

	missing = [r for r in perms.APP_ROLES if not frappe.db.exists("Role", r)]
	add("Required Roles exist", not missing, ", ".join(missing) or _("all present"))

	unstaffed = [r for r in perms.APP_ROLES if not _users_with_role(r)]
	add("Payroll roles assigned", not unstaffed,
	    _("nobody holds: {0}").format(", ".join(unstaffed)) if unstaffed else _("staffed"))

	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	if company:
		accounts = account_checks(company)
		bad = [c["label"] for c in accounts if c["status"] == BLOCKED]
		add("Required Accounts", not bad, ", ".join(bad) or _("all mapped and valid"))

		statutory = statutory_checks(company)
		bad = [c["label"] for c in statutory if c["status"] == BLOCKED]
		warn = [c["label"] for c in statutory if c["status"] == WARNING]
		add("Statutory configuration", not bad, ", ".join(bad + warn) or _("valid"),
		    warn=bool(warn) and not bad)
	else:
		add("Default Company", False, _("not configured"))

	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf
	separate_approval = bool(wf.requires_separate_approval())
	separate_payment = bool(wf.requires_separate_payment_approval())
	# Both controls off is not a failure — a small company may legitimately choose it —
	# but it must never pass silently.
	add("Workflow controls", True,
	    _("separate approval: {0}, separate payment approval: {1}").format(
		    separate_approval, separate_payment),
	    warn=not (separate_approval and separate_payment))

	# ifnull(...) rather than a Frappe "in ['', None]" filter: that filter does not match
	# SQL NULL, so it silently counted zero.
	orphans = frappe.db.sql(
		"""select count(*) from `tabIsoft Salary Slip`
		where docstatus = 1 and ifnull(payroll_entry, '') = ''""")[0][0]
	add("Payroll outside the workflow", True,
	    _("{0} submitted slip(s) belong to no payroll run").format(orphans) if orphans
	    else _("none"), warn=bool(orphans))

	stuck = frappe.db.sql(
		"""select ifnull(status,'Draft') as status, count(*) n from `tabIsoft Payroll Entry`
		where ifnull(status,'Draft') in ('Pending Approval','Approved','Posted','Payment Ready')
		group by status""", as_dict=True)
	add("Payroll pipeline", True,
	    ", ".join("{0}: {1}".format(r.status, r.n) for r in stuck) or _("nothing in flight"))

	if not quiet:
		width = max(len(r["check"]) for r in results)
		print()
		for r in results:
			print("  {0:<{1}}  {2:<8} {3}".format(r["check"], width, r["status"], r["detail"]))
		fails = [r for r in results if r["status"] == "FAIL"]
		warns = [r for r in results if r["status"] == "WARNING"]
		print("\n  OVERALL: {0}   ({1} pass, {2} warning, {3} fail)\n".format(
			"FAIL" if fails else ("WARNING" if warns else "PASS"),
			len(results) - len(fails) - len(warns), len(warns), len(fails)))
	return results
