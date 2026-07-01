# Copyright (c) 2026, Abbass Chokor and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class IsoftSalaryProfile(Document):
	pass


def get_active_profile(employee, on_date):
	"""Return the latest Isoft Salary Profile for an employee effective on/before a date."""
	rows = frappe.get_all(
		"Isoft Salary Profile",
		filters={"employee": employee, "from_date": ("<=", on_date)},
		fields=["name"],
		order_by="from_date desc",
		limit=1,
	)
	return frappe.get_doc("Isoft Salary Profile", rows[0].name) if rows else None
