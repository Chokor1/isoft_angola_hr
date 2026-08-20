# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Payroll bank export: validation, adapters, checksum and history.

WHICH BANK, AND WHY THERE IS STILL NO BANK-SPECIFIC ADAPTER
-----------------------------------------------------------
§18 asks which bank this company actually pays salaries through. It is **BAI**: the
configured salary payment account is ``42101 - Banco Bai - 072056928 - ITEC``, and the
employee IBANs carry bank code ``0040``, which is BAI's.

§19 then requires a real specification before an adapter may be written — an official
layout, a bank template, a corporate sample, or a previously accepted file. None exists
here. BAI publishes a *Serviço de pagamento em massa* (bulk payment by uploaded file
through corporate internet banking) but not its file layout; that is issued to corporate
clients under agreement. There is no accepted historical file on this site either — zero
bank files have ever been generated.

So the answer is **BANK FORMAT SPECIFICATION REQUIRED**, and no BAI adapter is written.
Writing one from a plausible guess would be strictly worse than the spreadsheet that
exists today: it would look authoritative and be rejected at the counter, after payroll
had been signed off.

WHAT IS BUILT INSTEAD, AND WHY IT IS WORTH BUILDING
----------------------------------------------------
Everything around the format is format-independent, and all of it was missing:

* **Validation** that catches what actually breaks a payment run — a malformed IBAN, a
  duplicated employee line, a total that disagrees with the payroll. On this site the
  check immediately found two IBANs that would have produced silently unpayable rows:
  one with an embedded space and one that is a raw account number, not an IBAN at all.
* **A checksum and a history record**, so Finance can prove exactly which file was
  handed over — and cannot mistake having produced one for having been paid.
* **An adapter interface**, so that the day BAI supplies its layout, one class is added
  and nothing else changes.

The generic spreadsheet is registered as the first adapter rather than being special-cased.
"""

import hashlib
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms

# --------------------------------------------------------------------------- #
# IBAN
# --------------------------------------------------------------------------- #
#: Angola: AO + 2 check digits + 21 digits = 25 characters.
ANGOLA_IBAN = re.compile(r"^AO\d{23}$")


def normalise_iban(value):
	"""Strip the formatting humans type. Spaces and dots are presentation, not data."""
	return re.sub(r"[\s.\-]", "", (value or "")).upper()


def iban_check_digits_ok(iban):
	"""ISO 13616 mod-97. Catches a transposed pair, which a length check never will."""
	rearranged = iban[4:] + iban[:4]
	digits = ""
	for char in rearranged:
		if char.isdigit():
			digits += char
		elif char.isalpha():
			digits += str(ord(char) - 55)
		else:
			return False
	try:
		return int(digits) % 97 == 1
	except ValueError:
		return False


def validate_iban(value):
	"""``(ok, normalised, reason)`` — never raises, so a whole run can be reported at once."""
	raw = (value or "").strip()
	if not raw:
		return False, "", _("no IBAN recorded")
	iban = normalise_iban(raw)
	if not iban.startswith("AO"):
		return False, iban, _("does not start with the AO country code")
	if not ANGOLA_IBAN.match(iban):
		return False, iban, _("an Angolan IBAN is AO plus 23 digits ({0} found)").format(
			len(iban))
	if not iban_check_digits_ok(iban):
		return False, iban, _("the IBAN check digits do not verify")
	return True, iban, ""


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
class PayrollBankExporter(object):
	"""One payment file format.

	``validate`` reports everything wrong with a run; ``generate`` produces the bytes.
	Splitting them is deliberate: Finance must be able to see every problem before a file
	exists, rather than discovering them one throw at a time.
	"""

	key = "generic"
	label = "Generic spreadsheet"
	bank = None
	specification = None

	def validate(self, entry, rows):
		return []

	def generate(self, entry, rows, reference):
		raise NotImplementedError


class GenericXlsxExporter(PayrollBankExporter):
	"""The format Phase 2 shipped: IBAN | Montante | Nome | Referencia.

	Not a bank format. It is a payment list a human uploads or keys into corporate
	internet banking, and it is the safe default until a real layout is supplied.
	"""

	key = "generic_xlsx"
	label = "Generic payroll transfer list (.xlsx)"
	specification = "Internal — not a bank-specified layout"

	def generate(self, entry, rows, reference):
		from frappe.utils.xlsxutils import make_xlsx

		data = [["IBAN", "Montante", "Nome", "Referencia"]]
		for row in rows:
			data.append([row["iban"], flt(row["amount"], 2), row["employee_name"], reference])
		return make_xlsx(data, "Bank Transfer").getvalue(), "xlsx"


#: Registered formats. A bank-specific adapter is added here — and only here — once its
#: specification is in hand. Deliberately not pre-populated with BAI/BFA/BIC stubs: an
#: empty adapter that appears in a dropdown is a promise the software cannot keep.
ADAPTERS = {a.key: a for a in (GenericXlsxExporter,)}

DEFAULT_ADAPTER = GenericXlsxExporter.key

#: Reported by the release gate and the console so the gap is visible rather than assumed.
BANK_STATUS = {
	"identified_bank": "BAI (Banco Angolano de Investimentos)",
	"evidence": [
		"Salary payment account: 42101 - Banco Bai - 072056928 - ITEC",
		"Employee IBANs carry bank code 0040 (BAI)",
	],
	"status": "BLOCKED — SPECIFICATION REQUIRED",
	"implemented": ["generic_xlsx"],
	"not_implemented": ["BAI", "BFA", "BIC", "Standard Bank Angola", "ATLANTICO",
	                    "BCI", "BPC"],
	"reason": "BAI operates a bulk-payment file service through corporate internet "
	          "banking, but does not publish the file layout — it is issued to corporate "
	          "clients under agreement. No official specification, bank template, "
	          "corporate sample or previously accepted file is available on this site.",
	"to_unblock": "Obtain the 'Serviço de pagamento em massa' file specification from "
	              "BAI corporate banking, then register one adapter in "
	              "services/bank_export.ADAPTERS.",
	"safe_interim": "Generate the generic transfer list and upload or key it into "
	                "corporate internet banking. This is an operational integration gap, "
	                "not a software defect.",
}


# --------------------------------------------------------------------------- #
# Validation (§21)
# --------------------------------------------------------------------------- #
def collect(entry):
	"""The payable rows for a payroll run, with every problem attached rather than raised."""
	rows, problems = [], []
	seen = {}

	for line in entry.employees:
		who = line.employee_name or line.employee
		if not line.salary_slip or not frappe.db.exists("Isoft Salary Slip", line.salary_slip):
			problems.append({"code": "BNK-001", "employee": line.employee,
			                 "message": _("{0} has no salary slip in this run.").format(who)})
			continue
		slip = frappe.db.get_value(
			"Isoft Salary Slip", line.salary_slip,
			["name", "net_pay", "docstatus", "currency"], as_dict=True)
		if not slip or cint(slip.docstatus) == 2:
			continue  # cancelled payroll is simply not part of the run
		if cint(slip.docstatus) != 1:
			problems.append({"code": "BNK-002", "employee": line.employee,
			                 "message": _("{0}: the salary slip is not submitted.").format(who)})
			continue
		if flt(slip.net_pay) <= 0:
			problems.append({"code": "BNK-003", "employee": line.employee,
			                 "message": _("{0}: net pay is {1}.").format(
				                 who, flt(slip.net_pay, 2))})
			continue

		ok, iban, reason = validate_iban(
			frappe.db.get_value("Employee", line.employee, "custom_iban"))
		if not ok:
			problems.append({"code": "BNK-004", "employee": line.employee,
			                 "message": _("{0}: {1}.").format(who, reason)})
			continue

		# A duplicated line pays somebody twice. The bank will not catch it.
		if line.employee in seen:
			problems.append({"code": "BNK-005", "employee": line.employee,
			                 "message": _("{0} appears more than once in this run.").format(who)})
			continue
		seen[line.employee] = True

		rows.append({"employee": line.employee, "employee_name": who, "iban": iban,
		             "amount": flt(slip.net_pay, 2), "slip": slip.name,
		             "currency": slip.currency or "AKZ"})

	currencies = {r["currency"] for r in rows}
	if len(currencies) > 1:
		problems.append({"code": "BNK-006", "employee": None,
		                 "message": _("The run mixes currencies: {0}.").format(
			                 ", ".join(sorted(currencies)))})
	return rows, problems


def validate_export(entry_name, adapter=None):
	"""Everything wrong with this run, before any file exists."""
	perms.require(perms.PAYROLL_EXPORT_BANK)
	entry = frappe.get_doc("Isoft Payroll Entry", entry_name)
	perms.require_company(entry.company)

	from isoft_angola_hr.isoft_angola_hr.services import payroll_workflow as wf

	problems, state_error = [], None
	try:
		wf.assert_can_export(entry)
	except Exception as exc:
		state_error = str(exc)
		problems.append({"code": "BNK-000", "employee": None,
		                 "message": _("Payroll is not released for payment: {0}").format(
			                 str(exc)[:160])})

	rows, row_problems = collect(entry)
	problems.extend(row_problems)

	exporter = ADAPTERS.get(adapter or DEFAULT_ADAPTER)
	if not exporter:
		frappe.throw(_("Unknown bank format {0}.").format(adapter))
	problems.extend(exporter().validate(entry, rows) or [])

	# The file total must equal what payroll says it is paying. A silent divergence here
	# is the difference between the ledger and the bank statement.
	file_total = flt(sum(r["amount"] for r in rows), 2)
	payroll_total = flt(frappe.db.sql(
		"""select ifnull(sum(s.net_pay), 0) from `tabIsoft Salary Slip` s
		join `tabIsoft Payroll Employee` pe on pe.salary_slip = s.name
		where pe.parent = %s and s.docstatus = 1""", entry.name)[0][0], 2)
	if rows and abs(file_total - payroll_total) > 0.01:
		problems.append({"code": "BNK-007", "employee": None,
		                 "message": _("The file totals {0} but the payroll totals {1}. "
		                              "{2} employee(s) were excluded.").format(
			                 file_total, payroll_total, len(row_problems))})

	if not rows and not problems:
		problems.append({"code": "BNK-008", "employee": None,
		                 "message": _("There is nothing payable in this run.")})

	return {
		"payroll_entry": entry.name, "adapter": adapter or DEFAULT_ADAPTER,
		"rows": len(rows), "total": file_total, "payroll_total": payroll_total,
		"problems": problems, "valid": not problems, "state_error": state_error,
	}


# --------------------------------------------------------------------------- #
# Generation (§22, §23)
# --------------------------------------------------------------------------- #
def generate(entry_name, adapter=None):
	"""Produce the payment file, record its checksum, and keep the history."""
	report = validate_export(entry_name, adapter=adapter)
	if not report["valid"]:
		frappe.throw(
			_("Cannot generate the payment file — {0} problem(s):<br><br>{1}").format(
				len(report["problems"]),
				"<br>".join(p["message"] for p in report["problems"][:20])),
			title=_("Bank Export Blocked"))

	entry = frappe.get_doc("Isoft Payroll Entry", entry_name)
	rows, _problems = collect(entry)
	key = adapter or DEFAULT_ADAPTER
	exporter = ADAPTERS[key]()

	from isoft_angola_hr.isoft_angola_hr.api import _salary_reference

	reference = _salary_reference(entry.end_date)
	content, extension = exporter.generate(entry, rows, reference)
	checksum = hashlib.sha256(content).hexdigest()

	# Regenerating supersedes; it never rewrites history. If payroll changed underneath,
	# the existing workflow has already invalidated its approval (§23).
	previous = frappe.get_all("Isoft Bank Export",
	                          filters={"payroll_entry": entry.name,
	                                   "status": ("in", ("Generated",))},
	                          fields=["name", "version"], order_by="version desc")
	for row in previous:
		frappe.db.set_value("Isoft Bank Export", row.name, "status", "Superseded")
	version = cint(previous[0].version) + 1 if previous else 1

	filename = "BankTransfer_{0}_v{1}.{2}".format(reference, version, extension)
	record = frappe.get_doc({
		"doctype": "Isoft Bank Export",
		"payroll_entry": entry.name, "company": entry.company,
		"adapter": exporter.label, "version": version, "status": "Generated",
		"employee_count": len(rows), "total_amount": flt(sum(r["amount"] for r in rows), 2),
		"file_name": filename, "checksum": checksum,
		"generated_by": frappe.session.user, "generated_at": now(),
	}).insert(ignore_permissions=True)

	# Preserve the Phase 2 audit fields on the payroll entry itself.
	entry.db_set("exported_by", frappe.session.user, update_modified=False)
	entry.db_set("exported_at", now(), update_modified=False)
	entry.db_set("export_count", cint(entry.export_count) + 1, update_modified=False)
	entry.db_set("export_employee_count", len(rows), update_modified=False)
	entry.db_set("export_total", flt(sum(r["amount"] for r in rows), 2),
	             update_modified=False)

	return {"content": content, "filename": filename, "checksum": checksum,
	        "export": record.name, "version": version, "rows": len(rows),
	        "total": flt(sum(r["amount"] for r in rows), 2)}


def record_bank_response(name, bank_reference, status="Submitted to Bank",
                         submitted_on=None, executed_on=None, notes=None):
	"""Record what the bank said (§24). Generating a file is never a payment."""
	perms.require(perms.PAYROLL_CONFIRM_PAYMENT)
	doc = frappe.get_doc("Isoft Bank Export", name)
	perms.require_company(doc.company)
	if status not in ("Submitted to Bank", "Executed", "Rejected"):
		frappe.throw(_("Unknown status {0}.").format(status))
	if status != "Rejected" and not (bank_reference or "").strip():
		frappe.throw(_("Record the bank's reference."))
	doc.db_set({
		"status": status, "bank_reference": bank_reference,
		"submitted_on": getdate(submitted_on) if submitted_on else getdate(nowdate()),
		"executed_on": getdate(executed_on) if executed_on else None,
		"recorded_by": frappe.session.user,
		"notes": notes,
	})
	return {"name": doc.name, "status": doc.status, "reference": doc.bank_reference}


def history(payroll_entry=None, company=None, limit=100):
	perms.require(perms.REPORT_BANK)
	conditions, values = ["1=1"], []
	for field, value in (("payroll_entry", payroll_entry), ("company", company)):
		if value:
			conditions.append("b.{0} = %s".format(field))
			values.append(value)
	scope, scope_values = perms.company_filter_sql(alias="b")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	return frappe.db.sql(
		"""select b.name, b.payroll_entry, b.adapter, b.version, b.status,
			b.employee_count, b.total_amount, b.file_name, b.checksum,
			b.generated_by, b.generated_at, b.bank_reference, b.submitted_on, b.executed_on
		from `tabIsoft Bank Export` b where {0}
		order by b.creation desc limit {1}""".format(" and ".join(conditions),
		                                             cint(limit) or 100),
		values, as_dict=True)


def available_formats():
	return {
		"default": DEFAULT_ADAPTER,
		"adapters": [{"key": a.key, "label": a.label, "bank": a.bank,
		              "specification": a.specification} for a in ADAPTERS.values()],
		"bank_status": BANK_STATUS,
	}


def audit_ibans(company=None):
	"""Every employee IBAN that would fail at the bank, checked now rather than on payday.

	Read-only. Fixing an IBAN is a business-data change and stays with HR.
	"""
	perms.require(perms.EMPLOYEE_READ)
	conditions, values = ["e.status = 'Active'"], []
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	rows = frappe.db.sql(
		"""select e.name, e.employee_name, e.company, e.custom_iban
		from `tabEmployee` e where {0} order by e.employee_name""".format(
			" and ".join(conditions)), values, as_dict=True)

	invalid, missing, ok = [], [], 0
	for row in rows:
		if not (row.custom_iban or "").strip():
			missing.append({"employee": row.name, "employee_name": row.employee_name})
			continue
		valid, normalised, reason = validate_iban(row.custom_iban)
		if valid:
			ok += 1
		else:
			invalid.append({"employee": row.name, "employee_name": row.employee_name,
			                "stored": row.custom_iban, "normalised": normalised,
			                "reason": reason})
	return {"total": len(rows), "valid": ok, "invalid": invalid, "missing": len(missing),
	        "note": _("An invalid IBAN is silently unpayable — the row is written to the "
	                  "file and rejected by the bank. Correcting it is an HR action.")}
