# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""An occurrence whose 5-day justification window has closed (HR, November 2026).

Recording an occurrence dated more than five days ago produced one that was locked from
birth: still "Pending Justification", with no "Mark Unjustified" button — only the HR
Manager's Extraordinary Re-justify. Once the window has closed, Unjustified is the outcome
the daily job applies anyway, so marking it is always allowed; what stays locked is
justifying it, or reopening it to Pending, without the Extraordinary override.
"""

import io
import os

import frappe
from frappe.utils import add_days, nowdate

from isoft_angola_hr.isoft_angola_hr import api
from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PREFIX, PayrollFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

DASHBOARD_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "page", "angola_hr_dashboard", "angola_hr_dashboard.js")


class TestAClosedWindow(PayrollFixture):
	def occurrence(self, days_ago, status="Pending Justification"):
		doc = frappe.get_doc({
			"doctype": "Isoft Attendance Occurrence", "employee": self.employee.name,
			"occurrence_date": add_days(nowdate(), -days_ago), "occurrence_type": "Full Day",
			"hours": 0, "status": status})
		doc.flags.ignore_permissions = True
		return doc.insert(ignore_permissions=True)

	def reason(self):
		name = PREFIX + " Motivo"
		if not frappe.db.exists("Isoft Absence Reason", name):
			frappe.get_doc({"doctype": "Isoft Absence Reason", "reason": name}).insert(ignore_permissions=True)
			self._created.append(("Isoft Absence Reason", name))
		return name

	def test_a_late_recorded_occurrence_can_be_marked_unjustified(self):
		occ = self.occurrence(13)
		api.set_occurrence_status(occ.name, "Unjustified")
		self.assertEqual(frappe.db.get_value(occ.doctype, occ.name, "status"), "Unjustified")

	def test_it_still_cannot_be_justified_without_the_override(self):
		occ = self.occurrence(13)
		occ.reload()
		occ.status, occ.justification_reason = "Justified", self.reason()
		with self.assertRaises(frappe.ValidationError):
			occ.save()

	def test_it_cannot_be_reopened_to_pending(self):
		occ = self.occurrence(13)
		api.set_occurrence_status(occ.name, "Unjustified")
		with self.assertRaises(frappe.ValidationError):
			api.set_occurrence_status(occ.name, "Pending Justification")

	def test_the_dialog_offers_mark_unjustified_on_a_locked_pending_occurrence(self):
		src = io.open(DASHBOARD_JS, encoding="utf-8").read()
		line = [l for l in src.splitlines() if "occ-unjust\"" in l and "<button" in l][0]
		self.assertNotIn("!locked &&", line)
		self.assertIn('o.status !== "Unjustified"', line)
