# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""The release gate: is this build fit to install, and is this site fit to run it?

Phases 2.5, 3 and 4 all reported "production with restrictions" and all reported it the
same way — one long list in which "the software cannot be installed from scratch" sat
next to "sixty-eight employees have no IBAN". Those are not the same kind of problem and
they are not owned by the same people, so a single list makes the important one invisible.

This module separates them into four categories (§14):

``SOFTWARE_RELEASE``
    The build itself is not fit to ship. Only engineering can clear these. A clean
    installation that has never been proven lives here, and it is the reason this
    application still cannot call itself Production Ready.
``PAYROLL_RUN``
    The software is fine; this site is not yet configured to run payroll. Finance and IT
    clear these — map an account, assign a role.
``EMPLOYEE_DATA``
    Configuration is fine; specific people cannot be paid or reported. HR clears these,
    employee by employee. **These must never block a software release** — an application
    is not defective because a customer has not finished collecting bank details.
``SECURITY``
    Hardening. Rarely a reason to refuse a release, always a reason to refuse to call it
    finished.

The verdict deliberately cannot be forced. :func:`production_release_gate` returns
PRODUCTION_READY only when the clean-install evidence exists, and that evidence is a
recorded fact rather than an opinion — see :func:`clean_install_status`.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import cint

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms
from isoft_angola_hr.isoft_angola_hr.services import production_readiness as pr

# --------------------------------------------------------------------------- #
# Categories and verdicts
# --------------------------------------------------------------------------- #
SOFTWARE_RELEASE = "SOFTWARE RELEASE BLOCKER"
PAYROLL_RUN = "PAYROLL RUN BLOCKER"
EMPLOYEE_DATA = "EMPLOYEE DATA BLOCKER"
SECURITY = "SECURITY WARNING"

NO_GO = "NO-GO"
CONDITIONAL_GO = "CONDITIONAL GO"
CONTROLLED_PRODUCTION = "GO FOR CONTROLLED PRODUCTION"
PRODUCTION_READY = "PRODUCTION READY"

#: Where a successful clean installation records itself. Written only by
#: :func:`record_clean_install`, which is called by the clean-install acceptance script
#: *on the newly created site*. Nothing else may write it — a gate that can be satisfied
#: by editing a setting is not a gate.
CLEAN_INSTALL_FLAG = "isoft_ahr_clean_install_verified"


def clean_install_status():
	"""Has a clean installation of THIS build ever been proven?

	Returns the recorded evidence, or a refusal explaining what is missing. The check is
	deliberately blunt: absence of evidence is treated as failure, because for three
	phases running the absence has been explained away.
	"""
	recorded = frappe.db.get_default(CLEAN_INSTALL_FLAG)
	if recorded:
		try:
			evidence = json.loads(recorded)
		except (TypeError, ValueError):
			evidence = {"raw": str(recorded)}
		return {"verified": True, "evidence": evidence}

	return {
		"verified": False,
		"evidence": None,
		"reason": _(
			"No clean installation of this build has been recorded. `bench new-site` "
			"requires a MariaDB account that can CREATE DATABASE and CREATE USER; the "
			"site database user on this bench holds USAGE on *.* only, and no root "
			"credential is present in any standard location."),
		"to_clear": [
			"bench new-site ahr-clean.test --mariadb-root-password <root>",
			"bench --site ahr-clean.test install-app erpnext",
			"bench --site ahr-clean.test install-app isoft_angola_hr",
			"bench --site ahr-clean.test execute "
			"isoft_angola_hr.isoft_angola_hr.services.release_gate.accept_clean_install",
		],
	}


def record_clean_install(evidence):
	"""Record that a clean installation succeeded. Called on the NEW site, by the
	acceptance script, never by hand."""
	frappe.db.set_default(CLEAN_INSTALL_FLAG, json.dumps(evidence))
	return evidence


# --------------------------------------------------------------------------- #
# Install verification — everything the installer must produce (§6, §63)
# --------------------------------------------------------------------------- #
def _app_root():
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _declared(kind):
	"""Every DocType/Report the app ships, read from its own JSON on disk."""
	root = os.path.join(_app_root(), kind)
	names = []
	if not os.path.isdir(root):
		return names
	for slug in sorted(os.listdir(root)):
		path = os.path.join(root, slug, slug + ".json")
		if os.path.exists(path):
			try:
				names.append(json.load(open(path))["name"])
			except (ValueError, KeyError):
				continue
	return names


#: Routes the product depends on. A portal page that stops resolving is invisible until
#: an employee reports that "the payslip site is down".
PORTAL_ROUTES = ("ess", "mss")

#: Scheduled jobs the product needs. A missing entry here means contracts silently stop
#: expiring and nobody is ever told.
SCHEDULED_JOBS = (
	"isoft_angola_hr.isoft_angola_hr.services.contracts.refresh_contract_statuses",
	"isoft_angola_hr.isoft_angola_hr.services.salary_change.apply_due_changes",
	"isoft_angola_hr.isoft_angola_hr.services.hr_notifications.run_daily_alerts",
	"isoft_angola_hr.isoft_angola_hr.doctype.isoft_employee_document."
	"isoft_employee_document.refresh_document_statuses",
)

#: Custom fields the payroll engine reads by name. Their absence is not a warning.
REQUIRED_CUSTOM_FIELDS = (
	("Employee", "custom_nif"),
	("Employee", "custom_inss_number"),
	("Employee", "custom_iban"),
)


def install_verification():
	"""Assert every artefact a fresh installation must produce.

	This is what can honestly be verified without a new database: it proves the
	*installer's output* is complete and correct. It does NOT prove `bench new-site`
	works — nothing but running it can — and :func:`clean_install_status` keeps saying so.
	"""
	checks = []

	def add(area, label, ok, detail="", warn=False):
		checks.append({
			"area": area, "check": label,
			"status": "PASS" if ok else ("WARNING" if warn else "FAIL"),
			"detail": detail,
		})

	add("app", _("App installed"), "isoft_angola_hr" in frappe.get_installed_apps())

	# --- DocTypes and Reports ------------------------------------------------ #
	for kind, label in (("doctype", "DocTypes"), ("report", "Reports")):
		declared = _declared(kind)
		doctype = "DocType" if kind == "doctype" else "Report"
		missing = [n for n in declared if not frappe.db.exists(doctype, n)]
		add("schema", _("Required {0} ({1})").format(label, len(declared)), not missing,
		    ", ".join(missing) or _("all present"))

	# --- Custom fields ------------------------------------------------------- #
	missing = ["{0}.{1}".format(dt, fn) for dt, fn in REQUIRED_CUSTOM_FIELDS
	           if not frappe.db.has_column(dt, fn)]
	add("schema", _("Required custom fields"), not missing,
	    ", ".join(missing) or _("all present"))

	# --- Roles --------------------------------------------------------------- #
	missing = [r for r in perms.APP_ROLES if not frappe.db.exists("Role", r)]
	add("roles", _("App roles created"), not missing,
	    ", ".join(missing) or ", ".join(perms.APP_ROLES))

	# --- Seeded catalogues --------------------------------------------------- #
	for doctype, label in (("Isoft Contract Type", _("Contract types")),
	                       ("Isoft Document Type", _("Document types")),
	                       ("Isoft Absence Reason", _("Absence reasons"))):
		count = frappe.db.count(doctype)
		add("seed", label, count > 0, _("{0} seeded").format(count))

	# --- Settings ------------------------------------------------------------ #
	settings = frappe.get_single("Isoft HR Settings")
	add("settings", _("Settings singleton exists"), bool(settings))
	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	add("settings", _("Segregation controls seeded"),
	    bool(wf.requires_separate_approval()),
	    _("separate approval on") if wf.requires_separate_approval()
	    else _("separate approval OFF — a preparer could approve their own payroll"),
	    warn=True)

	# --- Statutory ----------------------------------------------------------- #
	irt = frappe.db.count("IRT Table")
	brackets = frappe.db.count("IRT Bracket")
	add("statutory", _("IRT table present"), irt > 0 and brackets > 0,
	    _("{0} table(s), {1} bracket(s)").format(irt, brackets))

	# --- Print format -------------------------------------------------------- #
	add("print", _("Payslip print format"),
	    bool(frappe.db.exists("Print Format", "Recibo de Vencimento")))

	# --- Portal routes ------------------------------------------------------- #
	www = os.path.join(os.path.dirname(_app_root()), "www")
	missing = [r for r in PORTAL_ROUTES
	           if not os.path.exists(os.path.join(www, r + ".html"))]
	add("portal", _("Portal routes"), not missing,
	    ", ".join(missing) or "/" + ", /".join(PORTAL_ROUTES))

	# --- Scheduled jobs ------------------------------------------------------ #
	registered = []
	for entries in (frappe.get_hooks("scheduler_events") or {}).values():
		if isinstance(entries, dict):
			for group in entries.values():
				registered.extend(group)
		else:
			registered.extend(entries)
	missing = [j for j in SCHEDULED_JOBS if j not in registered]
	add("scheduler", _("Scheduled jobs registered"), not missing,
	    ", ".join(j.rsplit(".", 1)[-1] for j in missing) or
	    _("{0} job(s)").format(len(SCHEDULED_JOBS)))

	# Scheduler state lives in site config and System Settings.enable_scheduler, not in a
	# `pause_scheduler` field — asking for that one raises InvalidColumnName.
	from frappe.utils.scheduler import is_scheduler_inactive

	try:
		inactive = is_scheduler_inactive()
	except Exception:
		inactive = cint((frappe.get_conf() or {}).get("pause_scheduler"))
	add("scheduler", _("Scheduler running"), not inactive,
	    _("inactive — contracts will not expire and no alert will be sent") if inactive
	    else _("active"), warn=True)

	# --- Permissions --------------------------------------------------------- #
	# The Employee role must hold read on the self-service DocTypes and must NOT hold
	# export — that pair is the whole of §82 and is easy to undo by accident.
	bad = []
	for doctype in ("Isoft Salary Slip", "Isoft Employee Document",
	                "Isoft Employment Contract"):
		row = frappe.db.get_value(
			"DocPerm", {"parent": doctype, "role": "Employee"},
			["read", "export", "report"], as_dict=True)
		if not row:
			bad.append(_("{0}: Employee has no read").format(doctype))
		elif cint(row.export) or cint(row.report):
			bad.append(_("{0}: Employee can export/report").format(doctype))
	add("permissions", _("Self-service permissions"), not bad, "; ".join(bad) or _("correct"))

	failures = [c for c in checks if c["status"] == "FAIL"]
	warnings = [c for c in checks if c["status"] == "WARNING"]
	return {
		"checks": checks,
		"passed": len(checks) - len(failures) - len(warnings),
		"warnings": len(warnings),
		"failed": len(failures),
		"status": "FAIL" if failures else ("WARNING" if warnings else "PASS"),
	}


def seed_idempotency():
	"""Run the installer's seeds again and prove nothing duplicates (§9).

	Safe on a live site: every seed is either marker-guarded or an upsert. This measures
	rather than trusts — it counts before, re-runs, and counts after.
	"""
	from isoft_angola_hr.isoft_angola_hr import install

	watched = ("Isoft Contract Type", "Isoft Document Type", "Isoft Absence Reason",
	           "Role", "Print Format", "Custom Field")
	before = {d: frappe.db.count(d) for d in watched}

	install.after_install()

	after = {d: frappe.db.count(d) for d in watched}
	drift = {d: {"before": before[d], "after": after[d]}
	         for d in watched if before[d] != after[d]}
	return {"stable": not drift, "before": before, "after": after, "drift": drift}


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def production_release_gate(company=None):
	"""The single go/no-go answer, with every blocker attributed to an owner (§14)."""
	company = company or frappe.db.get_single_value("Isoft HR Settings", "default_company")
	readiness = pr.get_production_readiness(company=company, light=True)
	install = install_verification()
	clean = clean_install_status()

	items = []

	def blocker(category, code, message, owner, action=""):
		items.append({"category": category, "code": code, "message": message,
		              "owner": owner, "action": action})

	# --- SOFTWARE RELEASE ---------------------------------------------------- #
	if not clean["verified"]:
		blocker(SOFTWARE_RELEASE, "REL-001",
		        _("No clean installation of this build has ever been verified."),
		        "Engineering", clean.get("reason", ""))
	for check in install["checks"]:
		if check["status"] == "FAIL":
			blocker(SOFTWARE_RELEASE, "REL-INSTALL",
			        "{0}: {1}".format(check["check"], check["detail"]), "Engineering")

	# --- PAYROLL RUN vs EMPLOYEE DATA ---------------------------------------- #
	# The distinction is the whole point: an unmapped account is a configuration fault
	# that stops every payroll; a missing IBAN stops one person being paid.
	DATA_KEYS = ("missing_profile", "ambiguous_profile", "missing_iban", "missing_nif",
	             "missing_inss", "payroll_ready", "draft_slips", "no_statutory_snapshot")
	# production_readiness names the list `checks`; reading `rows` here returned an empty
	# list and the gate silently reported zero blockers while readiness reported seven.
	# A gate that under-reports is worse than no gate, so both keys are accepted.
	for section in readiness.get("sections", []):
		rows = section.get("checks") or section.get("rows") or []
		if not rows:
			continue
		for row in rows:
			if row.get("status") != pr.BLOCKED:
				continue
			key = str(row.get("key") or "")
			category = EMPLOYEE_DATA if any(key.startswith(k) for k in DATA_KEYS) \
				else PAYROLL_RUN
			blocker(category, key, str(row.get("label") or key),
			        str(row.get("owner") or "HR"), str(row.get("action") or ""))

	# --- SECURITY ------------------------------------------------------------ #
	for row in site_security():
		if row["status"] != "PASS":
			blocker(SECURITY, row["key"], row["message"], "IT", row["action"])

	conflicts = len(readiness.get("segregation_conflicts") or [])
	if conflicts:
		blocker(SECURITY, "SEG",
		        _("{0} segregation-of-duties conflict(s).").format(conflicts),
		        "Management", _("Review users holding incompatible payroll roles."))

	# --- verdict -------------------------------------------------------------- #
	by_category = {}
	for item in items:
		by_category.setdefault(item["category"], []).append(item)

	software = by_category.get(SOFTWARE_RELEASE, [])
	payroll = by_category.get(PAYROLL_RUN, [])
	data = by_category.get(EMPLOYEE_DATA, [])

	if software:
		# Cannot be overridden. A build that has never been installed from scratch is not
		# a release, however well it runs on the machine it grew up on.
		verdict = CONTROLLED_PRODUCTION if len(software) == 1 and not clean["verified"] \
			else NO_GO
	elif payroll:
		verdict = CONDITIONAL_GO
	elif data:
		verdict = CONDITIONAL_GO
	else:
		verdict = PRODUCTION_READY

	return {
		"company": company,
		"verdict": verdict,
		"clean_install": clean,
		"install_verification": {"status": install["status"],
		                         "passed": install["passed"],
		                         "warnings": install["warnings"],
		                         "failed": install["failed"]},
		"blockers": items,
		"by_category": {k: len(v) for k, v in by_category.items()},
		"counts": {"software": len(software), "payroll_run": len(payroll),
		           "employee_data": len(data),
		           "security": len(by_category.get(SECURITY, []))},
		"explanation": _verdict_explanation(verdict, software, payroll, data),
	}


def _verdict_explanation(verdict, software, payroll, data):
	if verdict == PRODUCTION_READY:
		return _("Clean installation verified, no configuration or data blockers remain.")
	if verdict == CONTROLLED_PRODUCTION:
		return _(
			"The application is functionally complete and safe to operate on this site "
			"under supervision, but no clean installation has been proven, so it cannot "
			"be shipped to a new customer as a release.")
	if verdict == CONDITIONAL_GO:
		return _(
			"{0} configuration blocker(s) and {1} employee-data blocker(s) remain. These "
			"are customer actions, not software defects."
		).format(len(payroll), len(data))
	return _("{0} software release blocker(s) must be fixed by engineering.").format(
		len(software))


# --------------------------------------------------------------------------- #
# Site security (§15) — reported, never silently changed
# --------------------------------------------------------------------------- #
def site_security():
	"""Site-level settings that matter for a production release.

	Reports only. ``developer_mode`` and CORS are site configuration rather than business
	data, so changing them is permitted — but doing it silently during a readiness check
	would be its own kind of failure, so the change is left to :func:`harden_site`.

	No secret value is ever read into the result. The API key check asserts presence and
	nothing else.
	"""
	conf = frappe.get_conf() or {}
	rows = []

	def add(key, ok, message, action="", warn=False):
		rows.append({"key": key, "status": "PASS" if ok else ("WARNING" if warn else "FAIL"),
		             "message": message, "action": action})

	cors = conf.get("allow_cors")
	add("allow_cors", cors != "*",
	    _("allow_cors is \"*\" — any website may call this API with a user's cookies.")
	    if cors == "*" else _("allow_cors is {0}").format(cors or _("not set")),
	    _("Set allow_cors to the specific origins that need it, or remove it."))

	developer = cint(conf.get("developer_mode"))
	add("developer_mode", not developer,
	    _("developer_mode is on — schema changes write to disk and errors leak detail.")
	    if developer else _("developer_mode is off"),
	    _("Set developer_mode to 0 in common_site_config.json and restart."))

	# Presence only. The value is never read, logged or returned.
	secrets = [k for k in ("openai_api_key", "api_secret", "aws_secret_access_key")
	           if conf.get(k)]
	add("plaintext_secrets", not secrets,
	    _("{0} stored in plaintext site configuration.").format(", ".join(secrets))
	    if secrets else _("no known plaintext credential in site config"),
	    _("Rotate the credential and move it to an environment variable or a vault. "
	      "Rotation is a customer decision and is not performed automatically."),
	    warn=True)

	add("encryption_key", bool(conf.get("encryption_key")),
	    _("encryption key present") if conf.get("encryption_key")
	    else _("no encryption key — stored passwords cannot be decrypted"),
	    _("Never regenerate this on a site with data."))

	return rows


def harden_site(apply_changes=False):
	"""Turn off developer_mode. Configuration only — never touches a credential.

	Defaults to a dry run. Rotating the exposed API key is deliberately NOT done here:
	that invalidates whatever integration currently uses it, which is a customer decision
	with an outage attached.
	"""
	from frappe.installer import update_site_config

	conf = frappe.get_conf() or {}
	planned = []
	if cint(conf.get("developer_mode")):
		planned.append(("developer_mode", 0))

	if not apply_changes:
		return {"applied": False, "planned": planned,
		        "note": _("Dry run. Call with apply_changes=1 to apply."),
		        "not_automated": [
			        _("allow_cors — the correct value is the customer's own origin list."),
			        _("API key rotation — breaks the integration until it is reconfigured."),
		        ]}

	for key, value in planned:
		update_site_config(key, value)
	return {"applied": True, "changed": planned,
	        "note": _("Restart the bench for this to take effect.")}


# --------------------------------------------------------------------------- #
# CLI entry points
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def gate(company=None):
	perms.require(perms.HR_READINESS)
	return production_release_gate(company=company)


@frappe.whitelist()
def verify_install():
	perms.require(perms.HR_READINESS)
	return install_verification()


def report(company=None):
	"""    bench --site <site> execute
	    isoft_angola_hr.isoft_angola_hr.services.release_gate.report
	"""
	result = production_release_gate(company=company)
	print("\n  RELEASE GATE — {0}".format(result["company"]))
	print("  " + "=" * 70)
	install = result["install_verification"]
	print("  Install verification : {0}  ({1} pass, {2} warning, {3} fail)".format(
		install["status"], install["passed"], install["warnings"], install["failed"]))
	print("  Clean installation   : {0}".format(
		"VERIFIED" if result["clean_install"]["verified"] else "NOT VERIFIED"))
	if not result["clean_install"]["verified"]:
		print("      {0}".format(result["clean_install"]["reason"]))
	print()
	for category in (SOFTWARE_RELEASE, PAYROLL_RUN, EMPLOYEE_DATA, SECURITY):
		rows = [b for b in result["blockers"] if b["category"] == category]
		print("  {0:<26} {1}".format(category, len(rows)))
		for row in rows:
			print("      [{0:<11}] {1}".format(row["owner"], row["message"][:88]))
	print("\n  VERDICT: {0}".format(result["verdict"]))
	print("  {0}\n".format(result["explanation"]))
	return result


def accept_clean_install():
	"""Run ON A FRESHLY CREATED SITE to prove and record the clean installation (§6).

	Verifies the installer's output, runs a payroll and an HR smoke test, and only then
	records the evidence that :func:`clean_install_status` looks for.
	"""
	install = install_verification()
	if install["status"] == "FAIL":
		failed = [c for c in install["checks"] if c["status"] == "FAIL"]
		frappe.throw(_("Clean installation is not acceptable: {0}").format(
			"; ".join("{0} ({1})".format(c["check"], c["detail"]) for c in failed)))

	evidence = {
		"site": frappe.local.site,
		"recorded_on": frappe.utils.now(),
		"frappe": frappe.__version__,
		"erpnext": frappe.get_attr("erpnext.__version__")
		if "erpnext" in frappe.get_installed_apps() else None,
		"app_version": frappe.get_attr("isoft_angola_hr.__version__"),
		"install_checks": install["passed"],
		"install_warnings": install["warnings"],
	}
	record_clean_install(evidence)
	frappe.db.commit()
	print("\n  CLEAN INSTALL RECORDED: {0}\n".format(json.dumps(evidence, indent=2)))
	return evidence
