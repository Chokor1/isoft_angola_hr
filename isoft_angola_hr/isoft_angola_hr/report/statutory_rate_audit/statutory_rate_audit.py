# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Statutory Rate Audit — which statutory configuration existed when, and what used it.

Two kinds of rule appear side by side: the effective-dated Isoft Statutory Rate records
(contribution rates and exemption thresholds) and the IRT Tables. For each the report
shows its effective window, who created and last changed it, and — the column that
matters for control — whether submitted payroll already depends on it. Anything marked
"in use" is protected from structural editing by the Phase 1 guards.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.REPORT_AUDIT, filters)
	data = _statutory_rates() + _irt_tables()
	data.sort(key=lambda r: (str(r.get("effective_from") or ""), r.get("rule") or ""))
	_set_effective_to(data)
	return _columns(), data


def _statutory_rates():
	rows = frappe.get_all(
		"Isoft Statutory Rate",
		fields=["name", "company", "effective_from", "disabled", "ss_employee_rate",
		        "ss_employer_rate", "food_allowance_exemption", "transport_allowance_exemption",
		        "owner", "modified_by", "modified"],
		order_by="effective_from asc")
	used = _usage_count("statutory_rate", [r.name for r in rows])
	out = []
	for r in rows:
		out.append({
			"kind": _("Statutory Rate"),
			"rule": r.name,
			"company": r.company,
			"effective_from": r.effective_from,
			"disabled": r.disabled,
			"ss_employee_rate": flt(r.ss_employee_rate),
			"ss_employer_rate": flt(r.ss_employer_rate),
			"food_exemption": flt(r.food_allowance_exemption),
			"transport_exemption": flt(r.transport_allowance_exemption),
			"created_by": r.owner,
			"modified_by": r.modified_by,
			"modified": r.modified,
			"used_by_submitted_payroll": used.get(r.name, 0),
			"locked": 1 if used.get(r.name) else 0,
		})
	return out


def _irt_tables():
	rows = frappe.get_all(
		"IRT Table",
		fields=["name", "title", "company", "effective_from", "owner", "modified_by", "modified"],
		order_by="effective_from asc")
	used = _usage_count("irt_table", [r.name for r in rows])
	out = []
	for r in rows:
		out.append({
			"kind": _("IRT Table"),
			"rule": r.name,
			"company": r.company,
			"effective_from": r.effective_from,
			"created_by": r.owner,
			"modified_by": r.modified_by,
			"modified": r.modified,
			"used_by_submitted_payroll": used.get(r.name, 0),
			"locked": 1 if used.get(r.name) else 0,
		})
	return out


def _usage_count(fieldname, names):
	"""How many SUBMITTED salary slips depend on each rule. Draft payroll does not lock
	configuration — it can still be recalculated."""
	if not names:
		return {}
	rows = frappe.db.sql(
		"""select `{0}` as rule, count(*) as n from `tabIsoft Salary Slip`
		where docstatus = 1 and `{0}` in ({1}) group by `{0}`""".format(
			fieldname, ", ".join(["%s"] * len(names))),
		names, as_dict=True)
	return {r.rule: r.n for r in rows}


def _set_effective_to(data):
	"""Close each rule's window at the day before the next rule of the same kind and
	company starts — the windows are implicit in the data, and an auditor should not have
	to derive them by eye."""
	by_scope = {}
	for row in data:
		by_scope.setdefault((row["kind"], row.get("company") or ""), []).append(row)
	for rows in by_scope.values():
		rows.sort(key=lambda r: str(r.get("effective_from") or ""))
		for i, row in enumerate(rows):
			nxt = rows[i + 1] if i + 1 < len(rows) else None
			row["effective_to"] = (frappe.utils.add_days(getdate(nxt["effective_from"]), -1)
			                       if nxt and nxt.get("effective_from") else None)


def _columns():
	return [
		ru.column(_("Kind"), "kind", "Data", 130),
		ru.column(_("Rule"), "rule", "Data", 220),
		ru.column(_("Company"), "company", "Link", 150, "Company"),
		ru.column(_("Effective From"), "effective_from", "Date", 120),
		ru.column(_("Effective To"), "effective_to", "Date", 120),
		ru.column(_("Disabled"), "disabled", "Check", 80),
		ru.column(_("Employee INSS Rate (%)"), "ss_employee_rate", "Percent", 160),
		ru.column(_("Employer INSS Rate (%)"), "ss_employer_rate", "Percent", 160),
		ru.money(_("Food Exemption"), "food_exemption", 130),
		ru.money(_("Transport Exemption"), "transport_exemption", 150),
		ru.column(_("Created By"), "created_by", "Link", 160, "User"),
		ru.column(_("Modified By"), "modified_by", "Link", 160, "User"),
		ru.column(_("Last Modified"), "modified", "Datetime", 160),
		ru.column(_("Submitted Slips Using It"), "used_by_submitted_payroll", "Int", 170),
		ru.column(_("Locked"), "locked", "Check", 80),
	]
