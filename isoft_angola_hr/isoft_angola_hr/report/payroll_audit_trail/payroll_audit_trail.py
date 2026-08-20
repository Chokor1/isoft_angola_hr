# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll Audit Trail — who did what to each payroll run, and what it was worth.

Answers the governance question directly: for any payroll period, which user prepared it,
which different user approved it, who posted the accounting, who authorised the payment,
and what the statutory totals were. The stamps live on the Payroll Entry itself, so the
answer does not have to be reconstructed from Version documents — those remain available
for field-level history through the standard document timeline.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.REPORT_AUDIT, filters)
	filters = filters or {}

	conds, values = ["1=1"], []
	if filters.get("company"):
		conds.append("pe.company = %s")
		values.append(filters["company"])
	if filters.get("from_date"):
		conds.append("pe.end_date >= %s")
		values.append(filters["from_date"])
	if filters.get("to_date"):
		conds.append("pe.end_date <= %s")
		values.append(filters["to_date"])
	if filters.get("status"):
		conds.append("ifnull(pe.status,'Draft') = %s")
		values.append(filters["status"])
	scope, scope_values = perms.company_filter_sql(alias="pe")
	if scope:
		conds.append(scope)
		values.extend(scope_values)

	entries = frappe.db.sql(
		"""select pe.name, pe.company, pe.start_date, pe.end_date, ifnull(pe.status,'Draft') as status,
			pe.number_of_employees, pe.prepared_by, pe.submitted_by, pe.approved_by, pe.posted_by,
			pe.payment_authorized_by, pe.approved_at, pe.posted_at, pe.paid_at, pe.rejected_by,
			pe.rejection_reason, pe.exported_by, pe.export_count, pe.approval_fingerprint
		from `tabIsoft Payroll Entry` pe
		where {0} order by pe.end_date desc""".format(" and ".join(conds)),
		values, as_dict=True,
	)

	totals = _totals_by_entry([e.name for e in entries])
	data = []
	for e in entries:
		t = totals.get(e.name, {})
		data.append({
			"payroll_entry": e.name,
			"company": e.company,
			"start_date": e.start_date,
			"end_date": e.end_date,
			"status": e.status,
			"employees": e.number_of_employees,
			"prepared_by": e.prepared_by,
			"submitted_by": e.submitted_by,
			"approved_by": e.approved_by,
			"approved_at": e.approved_at,
			"posted_by": e.posted_by,
			"posted_at": e.posted_at,
			"payment_authorized_by": e.payment_authorized_by,
			"paid_at": e.paid_at,
			"exported_by": e.exported_by,
			"export_count": e.export_count,
			# The clearest single indicator of segregation of duties in the whole system.
			"segregated": 1 if (e.submitted_by and e.approved_by
			                    and e.submitted_by != e.approved_by) else 0,
			"rejected_by": e.rejected_by,
			"rejection_reason": e.rejection_reason,
			"gross": flt(t.get("gross")),
			"employee_inss": flt(t.get("employee_inss")),
			"employer_inss": flt(t.get("employer_inss")),
			"irt": flt(t.get("irt")),
			"net": flt(t.get("net")),
		})

	total = ru.totals_row(data, ("employees", "gross", "employee_inss", "employer_inss",
	                             "irt", "net"), "company")
	if total:
		data.append(total)
	return _columns(), data


def _totals_by_entry(names):
	"""Aggregate the live slips of each run in one query — no per-row lookups."""
	if not names:
		return {}
	rows = frappe.db.sql(
		"""select payroll_entry, sum(gross_pay) gross, sum(ss_employee_amount) employee_inss,
			sum(ss_employer_amount) employer_inss, sum(irt_amount) irt, sum(net_pay) net
		from `tabIsoft Salary Slip`
		where docstatus < 2 and payroll_entry in ({0})
		group by payroll_entry""".format(", ".join(["%s"] * len(names))),
		names, as_dict=True)
	return {r.payroll_entry: r for r in rows}


def _columns():
	return [
		ru.column(_("Payroll Entry"), "payroll_entry", "Link", 120, "Isoft Payroll Entry"),
		ru.column(_("Company"), "company", "Link", 150, "Company"),
		ru.column(_("From"), "start_date", "Date", 95),
		ru.column(_("To"), "end_date", "Date", 95),
		ru.column(_("Status"), "status", "Data", 120),
		ru.column(_("Employees"), "employees", "Int", 90),
		ru.column(_("Prepared By"), "prepared_by", "Link", 160, "User"),
		ru.column(_("Submitted By"), "submitted_by", "Link", 160, "User"),
		ru.column(_("Approved By"), "approved_by", "Link", 160, "User"),
		ru.column(_("Approved At"), "approved_at", "Datetime", 160),
		ru.column(_("Duties Separated"), "segregated", "Check", 120),
		ru.column(_("Posted By"), "posted_by", "Link", 160, "User"),
		ru.column(_("Posted At"), "posted_at", "Datetime", 160),
		ru.column(_("Payment Authorized By"), "payment_authorized_by", "Link", 170, "User"),
		ru.column(_("Paid At"), "paid_at", "Datetime", 160),
		ru.column(_("Exported By"), "exported_by", "Link", 160, "User"),
		ru.column(_("Times Exported"), "export_count", "Int", 110),
		ru.column(_("Rejected By"), "rejected_by", "Link", 160, "User"),
		ru.column(_("Rejection Reason"), "rejection_reason", "Data", 220),
		ru.money(_("Gross"), "gross"),
		ru.money(_("Employee INSS"), "employee_inss"),
		ru.money(_("Employer INSS"), "employer_inss"),
		ru.money(_("IRT"), "irt"),
		ru.money(_("Net"), "net", 130),
	]
