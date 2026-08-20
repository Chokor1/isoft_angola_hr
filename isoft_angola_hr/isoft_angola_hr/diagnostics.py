# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Read-only data diagnostics for existing payroll data.

Phase 1 fixed the CODE. Data that was created while the defects existed still needs
human reconciliation, and this module finds it.

    bench --site <site> execute isoft_angola_hr.isoft_angola_hr.diagnostics.report

NOTHING HERE MODIFIES ANY RECORD. Reconciliation is an HR decision — choosing which of
two conflicting salary profiles is correct, or whether historic draft payroll should be
posted, changes what people are paid and must never be automated.

``report()`` prints counts only. ``detail(section)`` prints the affected records and is
therefore restricted to HR Managers.
"""

import frappe
from frappe import _
from frappe.utils import flt


def _ambiguous_profiles():
	"""Employees with two or more Salary Profiles sharing an effective date."""
	return frappe.db.sql(
		"""select employee, employee_name, from_date, count(*) n,
		       group_concat(concat(name, ' = ', format(base, 2)) separator ' | ') detail
		from `tabIsoft Salary Profile`
		group by employee, from_date having n > 1
		order by employee""", as_dict=True)


def _profiles_named_inconsistently():
	"""Profiles whose document name disagrees with their own from_date.

	Harmless on its own, but it means the effective date was edited after creation, so
	the name can no longer be trusted to describe the record.

	Compared in Python rather than SQL: a ``date_format`` mask is not unescaped when a
	query carries no parameters, which silently made every row look inconsistent.
	"""
	rows = frappe.db.sql(
		"""select name, employee, employee_name, from_date
		from `tabIsoft Salary Profile` order by employee""", as_dict=True)
	return [r for r in rows
	        if r.name != "ISP-{0}-{1}".format(r.employee, r.from_date)]


def _profiles_missing_company():
	return frappe.db.sql(
		"""select name, employee, employee_name from `tabIsoft Salary Profile`
		where ifnull(company,'') = ''""", as_dict=True)


def _active_employees_without_profile():
	return frappe.db.sql(
		"""select e.name, e.employee_name, e.company from `tabEmployee` e
		where e.status='Active' and not exists (
			select 1 from `tabIsoft Salary Profile` p where p.employee = e.name)
		order by e.employee_name""", as_dict=True)


def _employees_missing_statutory_ids():
	return frappe.db.sql(
		"""select name, employee_name,
		       case when ifnull(custom_nif,'')='' then 1 else 0 end no_nif,
		       case when ifnull(custom_inss_number,'')='' then 1 else 0 end no_inss,
		       case when ifnull(custom_iban,'')='' then 1 else 0 end no_iban
		from `tabEmployee` where status='Active'
		having no_nif or no_inss or no_iban
		order by employee_name""", as_dict=True)


def _negative_net_slips():
	return frappe.db.sql(
		"""select name, employee_name, start_date, end_date, net_pay, docstatus
		from `tabIsoft Salary Slip` where net_pay < 0 and docstatus < 2
		order by net_pay""", as_dict=True)


def _slips_with_invalid_days():
	"""Slips whose paid days exceed their working days, or that have no working days —
	both were possible before the proration guards existed."""
	return frappe.db.sql(
		"""select name, employee_name, total_working_days, payment_days, gross_pay, docstatus
		from `tabIsoft Salary Slip`
		where docstatus < 2 and (payment_days > total_working_days or ifnull(total_working_days,0) <= 0)
		order by name""", as_dict=True)


def _draft_journal_entries():
	"""Payroll entries that were created but never posted — the original P0-01 defect."""
	return frappe.db.sql(
		"""select s.name slip, s.employee_name, s.journal_entry, s.payment_entry,
		       je.docstatus je_docstatus, pe.docstatus pe_docstatus
		from `tabIsoft Salary Slip` s
		left join `tabJournal Entry` je on je.name = s.journal_entry
		left join `tabJournal Entry` pe on pe.name = s.payment_entry
		where (s.journal_entry is not null and ifnull(je.docstatus, 9) = 0)
		   or (s.payment_entry is not null and ifnull(pe.docstatus, 9) = 0)""", as_dict=True)


def _submitted_slips_not_posted():
	return frappe.db.sql(
		"""select name, employee_name, start_date, end_date, net_pay
		from `tabIsoft Salary Slip`
		where docstatus = 1 and ifnull(journal_entry,'') = ''
		order by start_date desc""", as_dict=True)


def _draft_slips():
	return frappe.db.sql(
		"""select count(*) n, coalesce(sum(net_pay), 0) total
		from `tabIsoft Salary Slip` where docstatus = 0""", as_dict=True)[0]


def _slips_without_statutory_snapshot():
	"""Slips calculated before the trace fields existed. They keep their amounts, but the
	employer contribution and the IRT bracket that produced them are unknown."""
	return frappe.db.sql(
		"""select count(*) n from `tabIsoft Salary Slip`
		where docstatus < 2 and ifnull(ss_base, 0) = 0 and ifnull(gross_pay, 0) > 0""",
		as_dict=True)[0]


SECTIONS = (
	("ambiguous_profiles", "Salary Profiles sharing an effective date (payroll BLOCKED)", _ambiguous_profiles),
	("profiles_named_inconsistently", "Salary Profiles whose name disagrees with from_date", _profiles_named_inconsistently),
	("profiles_missing_company", "Salary Profiles with no company", _profiles_missing_company),
	("employees_without_profile", "Active employees with no Salary Profile (excluded from payroll)", _active_employees_without_profile),
	("employees_missing_ids", "Active employees missing NIF / INSS number / IBAN", _employees_missing_statutory_ids),
	("negative_net", "Salary Slips with negative net pay (cannot be submitted or paid)", _negative_net_slips),
	("invalid_days", "Salary Slips with impossible working/payment days", _slips_with_invalid_days),
	("draft_journal_entries", "Salary Slips linked to a DRAFT Journal Entry (no ledger effect)", _draft_journal_entries),
	("submitted_not_posted", "Submitted Salary Slips with no accrual posted", _submitted_slips_not_posted),
)


def report():
	"""Print counts for every diagnostic. Read-only."""
	print("\n=== Isoft Angola HR — data diagnostics (read-only) ===\n")
	drafts = _draft_slips()
	print("  Draft salary slips: {0} (net {1:,.2f}) — not payroll yet, review before posting".format(
		drafts.n, flt(drafts.total)))
	no_snapshot = _slips_without_statutory_snapshot()
	print("  Slips without a statutory snapshot: {0} — calculated before Phase 1; amounts".format(no_snapshot.n))
	print("      are intact but the employer contribution and IRT bracket are not recorded")
	print()
	total = 0
	for key, label, fn in SECTIONS:
		rows = fn()
		total += len(rows)
		flag = "!!" if rows and key in ("ambiguous_profiles", "negative_net", "draft_journal_entries") else "  "
		print("  {0} {1:<62} {2}".format(flag, label, len(rows)))
	print("\n  {0} record(s) need human review. Nothing has been changed.".format(total))
	print("  Detail: bench --site <site> execute "
	      "isoft_angola_hr.isoft_angola_hr.diagnostics.detail --args \"['<section>']\"")
	print("  Sections: {0}\n".format(", ".join(k for k, _l, _f in SECTIONS)))
	return {key: len(fn()) for key, _label, fn in SECTIONS}


@frappe.whitelist()
def detail(section):
	"""Rows for one diagnostic section. HR Manager only — this exposes salary data."""
	if "HR Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	for key, label, fn in SECTIONS:
		if key == section:
			rows = fn()
			print("\n=== {0} ({1}) ===".format(label, len(rows)))
			for r in rows:
				print("   ", dict(r))
			return rows
	frappe.throw(_("Unknown diagnostic section {0}. Available: {1}").format(
		section, ", ".join(k for k, _l, _f in SECTIONS)))
