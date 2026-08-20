# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Who is missing what — the single work list for cleaning up HR master data.

Deliberately shows yes/no indicators rather than the values themselves. A completeness
report that prints every NIF, IBAN and ID number on one exportable screen is a data
extract, not a report, and it would be the easiest thing in the system to leak.
"""

import frappe
from frappe import _
from frappe.utils import cint

from isoft_angola_hr.isoft_angola_hr.report import report_utils as ru
from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def execute(filters=None):
	ru.guard(perms.EMPLOYEE_READ, filters)
	filters = filters or {}
	conditions, values = ["e.status = 'Active'"], []
	if filters.get("company"):
		conditions.append("e.company = %s")
		values.append(filters["company"])
	if filters.get("department"):
		conditions.append("e.department = %s")
		values.append(filters["department"])
	scope, scope_values = perms.company_filter_sql(alias="e")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	employees = frappe.db.sql(
		"""select e.name, e.employee_name, e.company, e.department, e.designation,
			e.reports_to, e.holiday_list, e.default_shift, e.custom_nif, e.custom_inss_number,
			e.custom_iban, e.emergency_phone_number, e.user_id
		from `tabEmployee` e where {0} order by e.department, e.employee_name""".format(
			" and ".join(conditions)), values, as_dict=True)
	names = [e.name for e in employees]

	with_contract, with_profile = set(), set()
	if names:
		placeholders = ", ".join(["%s"] * len(names))
		with_contract = set(frappe.db.sql_list(
			"""select distinct employee from `tabIsoft Employment Contract`
			where employee in ({0}) and status in ('Active','Expiring')""".format(placeholders),
			names))
		with_profile = set(frappe.db.sql_list(
			"""select distinct employee from `tabIsoft Salary Profile`
			where employee in ({0})""".format(placeholders), names))

	data = []
	for e in employees:
		flags = {
			"nif": 1 if (e.custom_nif or "").strip() else 0,
			"inss": 1 if (e.custom_inss_number or "").strip() else 0,
			"iban": 1 if (e.custom_iban or "").strip() else 0,
			"contract": 1 if e.name in with_contract else 0,
			"salary_profile": 1 if e.name in with_profile else 0,
			"manager": 1 if e.reports_to else 0,
			"emergency_contact": 1 if e.emergency_phone_number else 0,
			"holiday_list": 1 if e.holiday_list else 0,
			"ess_access": 1 if e.user_id else 0,
		}
		missing = [k for k, v in flags.items() if not v]
		blocking = [k for k in ("contract", "salary_profile") if k in missing]
		data.append(dict({
			"employee": e.name, "employee_name": e.employee_name,
			"department": e.department, "designation": e.designation,
			"missing_count": len(missing),
			"status": _("Blocked") if blocking else (
				_("Incomplete") if missing else _("Complete")),
		}, **flags))
	return _columns(), data


def _columns():
	def check(label, fieldname):
		return ru.column(label, fieldname, "Check", 90)

	return [
		ru.column(_("Employee"), "employee", "Link", 110, "Employee"),
		ru.column(_("Employee Name"), "employee_name", "Data", 190),
		ru.column(_("Department"), "department", "Link", 140, "Department"),
		ru.column(_("Designation"), "designation", "Link", 140, "Designation"),
		check(_("Contract"), "contract"),
		check(_("Salary Profile"), "salary_profile"),
		check(_("NIF"), "nif"),
		check(_("INSS"), "inss"),
		check(_("IBAN"), "iban"),
		check(_("Manager"), "manager"),
		check(_("Emergency Contact"), "emergency_contact"),
		check(_("Holiday List"), "holiday_list"),
		check(_("Self-Service"), "ess_access"),
		ru.column(_("Missing"), "missing_count", "Int", 90),
		ru.column(_("Status"), "status", "Data", 110),
	]
