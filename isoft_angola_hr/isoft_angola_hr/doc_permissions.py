# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Record-level permission for the self-service DocTypes.

Phase 3's self-service services were safe because they never accepted an employee
parameter. Phase 4 adds two things that bypass a service entirely:

* a **payslip PDF**, rendered by Frappe's own print engine, and
* a **private file download**, served by Frappe's file handler.

Both ask the framework — not this app — whether the caller may read the underlying
document. So the boundary has to move down to the record itself.

The mechanism is the standard one ERPNext uses for its own Salary Slip: the Employee role
is granted a bare ``read`` on the DocType, and these two hooks narrow that to *your own
records only*.

* :func:`has_permission` guards a single document — a PDF, a file, a form.
* ``get_permission_query_conditions`` guards every list, report and ``get_list`` call.

Both are required. A ``has_permission`` hook alone still lets a list view enumerate rows,
and a query condition alone still lets somebody open a document by name. Frappe applies
hooks *after* role permissions, so these can only ever restrict — never grant — which is
why the DocType JSON gives the Employee role ``read`` and ``print`` but deliberately
withholds ``report`` and ``export``: an employee must not be able to pull the whole table
through the reporting API (§82).
"""

import frappe
from frappe.utils import cint

#: Roles that already hold the record through a normal HR/payroll permission. For these
#: the hook is a no-op — their access is decided by the DocType permissions and by
#: ``services.permissions``, not by whose name is on the record.
_STAFF_ROLES = frozenset({
	"HR User", "HR Manager", "Payroll Officer", "Payroll Manager",
	"Payroll Finance Approver", "Accounts Manager", "System Manager", "Administrator",
})


def _is_staff(user):
	if user == "Administrator":
		return True
	return bool(_STAFF_ROLES & set(frappe.get_roles(user)))


def employee_for(user=None):
	"""The Employee linked to ``user``, or None.

	Cached for the life of the request: the file handler and the print engine both call
	into here several times for a single download.
	"""
	user = user or frappe.session.user
	if user in ("Guest", None):
		return None
	# frappe.flags is a per-request dict that the framework resets between requests.
	# frappe.local is a werkzeug Local proxy and has no usable __dict__, so caching there
	# raises KeyError('__dict__') the first time a permission is checked.
	cache = frappe.flags.setdefault("_isoft_ess_employee", {})
	if user not in cache:
		cache[user] = frappe.db.get_value("Employee", {"user_id": user}, "name")
	return cache[user]


def _condition(doctype, user, extra=""):
	"""SQL restricting a list to the caller's own records, or ``1=0`` when they have none."""
	if _is_staff(user):
		return None
	me = employee_for(user)
	if not me:
		# A user with the Employee role but no Employee record sees nothing, rather than
		# everything. `1=0` is deliberate: returning None here would mean "no restriction".
		return "1=0"
	table = "`tab{0}`".format(doctype)
	return "({0}.employee = {1}{2})".format(table, frappe.db.escape(me), extra)


# --------------------------------------------------------------------------- #
# Isoft Salary Slip — own, and only once approved
# --------------------------------------------------------------------------- #
def salary_slip_query(user):
	# A draft slip is a calculation somebody is still working on. Employees see approved
	# payroll only — the same rule the ESS service applies, enforced a second time here
	# because the print engine never goes through that service.
	return _condition("Isoft Salary Slip", user, extra=" and `tabIsoft Salary Slip`.docstatus = 1")


def salary_slip_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _is_staff(user):
		return True
	if doc.get("employee") != employee_for(user):
		return False
	return cint(doc.get("docstatus")) == 1


# --------------------------------------------------------------------------- #
# Isoft Employee Document — own, and never confidential
# --------------------------------------------------------------------------- #
def employee_document_query(user):
	return _condition(
		"Isoft Employee Document", user,
		extra=" and ifnull(`tabIsoft Employee Document`.confidential, 0) = 0")


def employee_document_permission(doc, ptype=None, user=None):
	"""Confidential and medical documents stay with HR — even from the person they concern.

	They are HR-held records (criminal record checks, occupational medical certificates)
	and surfacing them through self-service would create a second, uncontrolled
	distribution channel for exactly the data that most needs one.
	"""
	user = user or frappe.session.user
	if _is_staff(user):
		return True
	if doc.get("employee") != employee_for(user):
		return False
	return not cint(doc.get("confidential"))


# --------------------------------------------------------------------------- #
# Isoft Employment Contract — own, and not while still a draft
# --------------------------------------------------------------------------- #
def contract_query(user):
	return _condition(
		"Isoft Employment Contract", user,
		extra=" and `tabIsoft Employment Contract`.status not in ('Draft', 'Pending Approval')")


def contract_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _is_staff(user):
		return True
	if doc.get("employee") != employee_for(user):
		return False
	# An unapproved contract is a proposal. Showing it to the employee would turn an
	# internal draft into something they reasonably believe they have been offered.
	return doc.get("status") not in ("Draft", "Pending Approval")


# --------------------------------------------------------------------------- #
# Requests the employee raises themselves
# --------------------------------------------------------------------------- #
def own_record_query(doctype):
	def _query(user):
		return _condition(doctype, user)

	_query.__name__ = "{0}_query".format(doctype.lower().replace(" ", "_"))
	return _query


def own_record_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _is_staff(user):
		return True
	return doc.get("employee") == employee_for(user)


salary_advance_query = own_record_query("Isoft Salary Advance")
bank_change_query = own_record_query("Isoft Bank Change Request")
