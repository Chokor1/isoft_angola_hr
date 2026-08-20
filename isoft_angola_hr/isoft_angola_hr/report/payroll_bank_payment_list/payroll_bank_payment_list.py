# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll Bank Payment List — a readable view of what will be (or was) transferred.

Deliberately separate from the bank export file: the export refuses to produce anything
while an IBAN is missing, but Finance still needs to SEE which employees are missing one.
This report therefore lists everybody and flags the gaps instead of failing.

Restricted to the finance roles — it pairs each person's name with their bank account and
take-home pay.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.REPORT_BANK, filters)
	conditions, values = ru.slip_conditions(filters)

	rows = frappe.db.sql(
		"""select s.name, s.employee, s.employee_name, e.custom_iban as iban, e.bank_name,
			s.net_pay, s.docstatus, s.journal_entry, s.payment_entry, s.end_date, s.payroll_entry
		from `tabIsoft Salary Slip` s
		left join `tabEmployee` e on e.name = s.employee
		where {0}
		order by s.employee_name""".format(conditions),
		values, as_dict=True,
	)

	from isoft_angola_hr.isoft_angola_hr import api

	data = []
	for r in rows:
		iban = (r.iban or "").strip()
		data.append({
			"employee": r.employee,
			"employee_name": r.employee_name,
			"bank_name": r.bank_name,
			"iban": iban or _("MISSING"),
			"payable": 1 if iban and flt(r.net_pay) > 0 else 0,
			"net_pay": flt(r.net_pay),
			"payment_status": api._slip_status(r.docstatus, r.journal_entry, r.payment_entry),
			"payment_entry": r.payment_entry,
			"payroll_entry": r.payroll_entry,
			"end_date": r.end_date,
		})

	total = ru.totals_row(data, ("net_pay",), "employee_name")
	if total:
		data.append(total)
	return _columns(), data


def _columns():
	return [
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 190),
		ru.column(_("Bank"), "bank_name", "Data", 140),
		ru.column(_("IBAN"), "iban", "Data", 220),
		ru.column(_("Payable"), "payable", "Check", 80),
		ru.money(_("Net Amount"), "net_pay", 140),
		ru.column(_("Payment Status"), "payment_status", "Data", 120),
		ru.column(_("Payment Entry"), "payment_entry", "Link", 150, "Journal Entry"),
		ru.column(_("Payroll Entry"), "payroll_entry", "Link", 120, "Isoft Payroll Entry"),
		ru.column(_("Period End"), "end_date", "Date", 95),
	]
