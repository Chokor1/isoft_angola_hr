# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Does the payroll the company calculated equal the payroll it booked and paid?

Payroll can be perfectly calculated and still be wrong in the books: an entry cancelled
without the slip noticing, a payment posted for a different amount, a liability that
never made it to the ledger. This module compares the two sides for a payroll run:

    salary slips  ⟷  general ledger  ⟷  payments

Each line reconciles or it does not, and the difference is stated. Nothing is corrected
automatically — a reconciliation tool that adjusts the ledger to match its own
expectation is not a reconciliation tool.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from isoft_angola_hr.isoft_angola_hr.payroll import engine
from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

TOLERANCE = 0.01


def _account_map(settings=None):
	s = settings or frappe.get_cached_doc("Isoft HR Settings")
	return {r.abbr: r.account for r in s.get("component_accounts") or [] if r.account}


def _gl_balance(vouchers, account, side):
	"""Net movement on one account across the payroll's own vouchers only.

	Scoped to the vouchers this payroll produced rather than to a date range, so an
	unrelated manual journal on the same account cannot make payroll look unbalanced.
	"""
	if not vouchers or not account:
		return 0.0
	rows = frappe.db.sql(
		"""select sum(debit) d, sum(credit) c from `tabGL Entry`
		where voucher_type = 'Journal Entry' and is_cancelled = 0
		  and account = %s and voucher_no in ({0})""".format(", ".join(["%s"] * len(vouchers))),
		[account] + list(vouchers), as_dict=True)
	row = rows[0] if rows else {}
	return flt(flt(row.get("c")) - flt(row.get("d")), 2) if side == "credit" \
		else flt(flt(row.get("d")) - flt(row.get("c")), 2)


def reconcile(entry):
	"""Reconcile one payroll run. Read-only.

	:returns: dict with a line per check, each carrying expected, actual, difference and
	          whether it reconciles.
	"""
	rows = wf.slip_rows(entry)
	live = [r for r in rows if cint(r["docstatus"]) == 1]
	totals = wf.compute_totals(entry, rows=live)

	accrual_vouchers = sorted({r["journal_entry"] for r in live
	                           if r.get("journal_entry") and _submitted(r["journal_entry"])})
	payment_vouchers = sorted({r["payment_entry"] for r in live
	                           if r.get("payment_entry") and _submitted(r["payment_entry"])})

	settings = frappe.get_cached_doc("Isoft HR Settings")
	accounts = _account_map(settings)
	payable = settings.get("payroll_payable_account")

	lines = []

	def line(key, label, expected, actual, note=None):
		difference = flt(flt(actual) - flt(expected), 2)
		lines.append({
			"key": key, "label": label, "expected": flt(expected, 2), "actual": flt(actual, 2),
			"difference": difference, "reconciled": abs(difference) <= TOLERANCE, "note": note,
		})

	line("net_payable", _("Net payroll vs Payroll Payable credited"),
	     totals["net"], _gl_balance(accrual_vouchers, payable, "credit"),
	     _("Every net salary must appear as a credit to the payable account."))

	line("employee_inss", _("Employee INSS vs INSS liability"),
	     totals["employee_inss"], _gl_balance(accrual_vouchers, accounts.get("CTSS3"), "credit"))

	line("employer_inss", _("Employer INSS vs employer INSS liability"),
	     totals["employer_inss"], _gl_balance(accrual_vouchers, accounts.get("CTSSP"), "credit"))

	line("employer_inss_expense", _("Employer INSS vs employer INSS expense"),
	     totals["employer_inss"], _gl_balance(accrual_vouchers, accounts.get("CTSSE"), "debit"))

	line("irt", _("IRT vs IRT liability"),
	     totals["irt"], _gl_balance(accrual_vouchers, accounts.get("IRT"), "credit"))

	line("payment", _("Net payroll vs Payroll Payable settled"),
	     totals["net"], _gl_balance(payment_vouchers, payable, "debit"),
	     _("Payment must clear exactly the payable the accrual booked."))

	# The accrual must balance in its own right.
	debit, credit = _voucher_totals(accrual_vouchers)
	line("accrual_balance", _("Accrual debits vs credits"), debit, credit)

	unposted = [r["employee_name"] or r["employee"] for r in live
	            if not (r.get("journal_entry") and _submitted(r["journal_entry"]))]
	unpaid = [r["employee_name"] or r["employee"] for r in live
	          if flt(r["net_pay"]) > 0 and not (r.get("payment_entry") and _submitted(r["payment_entry"]))]
	drafts = [r["employee_name"] or r["employee"] for r in rows if cint(r["docstatus"]) == 0]

	return {
		"payroll_entry": entry.name,
		"status": wf.state_of(entry),
		"employees": len(live),
		"totals": totals,
		"lines": lines,
		"reconciled": all(l["reconciled"] for l in lines),
		"unposted": unposted,
		"unpaid": unpaid,
		"draft_slips": drafts,
		"accrual_vouchers": accrual_vouchers,
		"payment_vouchers": payment_vouchers,
	}


def _submitted(journal_entry):
	return cint(frappe.db.get_value("Journal Entry", journal_entry, "docstatus")) == 1


def _voucher_totals(vouchers):
	if not vouchers:
		return 0.0, 0.0
	row = frappe.db.sql(
		"""select sum(debit) d, sum(credit) c from `tabGL Entry`
		where is_cancelled = 0 and voucher_no in ({0})""".format(", ".join(["%s"] * len(vouchers))),
		list(vouchers), as_dict=True)[0]
	return flt(row.d, 2), flt(row.c, 2)


def assert_ready_to_close(entry):
	"""A payroll may only be closed when it is genuinely finished.

	Closing is what protects a period from further change, so closing an incomplete run
	would freeze exactly the problems that still needed fixing.
	"""
	report = reconcile(entry)

	if report["draft_slips"]:
		frappe.throw(
			_("Não é possível fechar o processamento: {0} recibo(s) continuam em rascunho ({1}). "
			  "Submeta-os ou cancele-os primeiro.").format(
				len(report["draft_slips"]), ", ".join(report["draft_slips"][:8])),
			title=_("Payroll Incomplete"))

	if report["unposted"]:
		frappe.throw(
			_("Não é possível fechar o processamento: {0} recibo(s) não foram contabilizados "
			  "({1}).").format(len(report["unposted"]), ", ".join(report["unposted"][:8])),
			title=_("Accounting Incomplete"))

	failed = [l for l in report["lines"] if not l["reconciled"]]
	if failed:
		frappe.throw(
			_("O processamento não reconcilia com a contabilidade e não pode ser fechado:"
			  "<br><br>{0}").format("<br>".join(
				"• {0}: {1} vs {2} (diferença {3})".format(
					l["label"], l["expected"], l["actual"], l["difference"]) for l in failed)),
			title=_("Payroll Does Not Reconcile"))

	# Unpaid employees are reported, not blocked: a company may legitimately close a
	# period while one transfer is still in the banking system.
	if report["unpaid"]:
		frappe.msgprint(
			_("{0} employee(s) have no submitted payment entry: {1}. The payroll is being "
			  "closed with those payments still outstanding.").format(
				len(report["unpaid"]), ", ".join(report["unpaid"][:8])),
			title=_("Payments Outstanding"), indicator="orange")
	return report


@frappe.whitelist()
def payroll_reconciliation(name):
	"""Month-end reconciliation for one payroll run."""
	from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

	perms.require(perms.REPORT_PAYROLL)
	entry = frappe.get_doc("Isoft Payroll Entry", name)
	perms.require_company(entry.company)
	return reconcile(entry)
