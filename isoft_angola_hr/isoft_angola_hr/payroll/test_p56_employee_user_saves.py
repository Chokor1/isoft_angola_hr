# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Saving an Employee must not re-save User records that need no change (November 2026).

ERPNext's Employee.on_update calls ``user.add_roles(...)`` for the leave and expense
approvers and ``user.save()`` for the linked user on EVERY save; Frappe's add_roles saves
even when the role is already there. On production an HR Manager with no permission on
User appeared as "modified by" on 13 User records after editing ~60 employees — empty
saves, no Version rows. employee_override.IsoftEmployee saves a User only when something
actually changes; when it does, the core behaviour is unchanged.
"""

import frappe

from isoft_angola_hr.isoft_angola_hr.payroll.test_p2_controls import ControlFixture
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestEmployeeSavesLeaveUsersAlone(ControlFixture):
	USERS = {
		"hrm": ["HR Manager"],                                  # no rights on User
		"approver": ["Leave Approver", "Expense Approver"],     # already has both roles
		"plain": [],                                           # has neither
		"linked": ["Employee"],                                # the employee's own login
	}

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		genders = frappe.get_all("Gender", pluck="name", order_by="name")
		cls.gender, cls.other_gender = genders[0], genders[1]

	def stamp(self, user):
		return frappe.db.get_value("User", user, ["modified", "modified_by"])

	def save_as_hr_manager(self, **values):
		frappe.set_user(self.users["hrm"])
		try:
			emp = frappe.get_doc("Employee", self.employee.name)
			emp.update(values)
			emp.save()
		finally:
			frappe.set_user("Administrator")
		return emp

	def test_the_hr_manager_has_no_permission_on_user(self):
		# On another user's record: Frappe shares every user's own User record with them,
		# so the doctype-level check alone is True for everybody.
		frappe.set_user(self.users["hrm"])
		try:
			for key in ("approver", "plain", "linked"):
				self.assertFalse(frappe.has_permission("User", "write", doc=self.users[key]), key)
		finally:
			frappe.set_user("Administrator")

	def test_an_approver_who_has_the_roles_is_not_resaved(self):
		approver = self.users["approver"]
		self.save_as_hr_manager(leave_approver=approver, expense_approver=approver)
		before = self.stamp(approver)
		self.save_as_hr_manager(leave_approver=approver, expense_approver=approver)
		self.assertEqual(self.stamp(approver), before)

	def test_an_approver_missing_the_roles_gets_them(self):
		plain = self.users["plain"]
		self.save_as_hr_manager(leave_approver=plain, expense_approver=plain)
		roles = frappe.get_roles(plain)
		self.assertIn("Leave Approver", roles)
		self.assertIn("Expense Approver", roles)

	def test_a_user_in_sync_with_the_employee_is_not_resaved(self):
		linked = self.users["linked"]
		self.save_as_hr_manager(user_id=linked, gender=self.gender, date_of_birth="1990-01-01")
		before = self.stamp(linked)
		self.save_as_hr_manager(user_id=linked, gender=self.gender, date_of_birth="1990-01-01")
		self.assertEqual(self.stamp(linked), before)

	def test_a_user_out_of_sync_is_updated(self):
		linked = self.users["linked"]
		self.save_as_hr_manager(user_id=linked, gender=self.gender, date_of_birth="1990-01-01")
		before = self.stamp(linked)
		self.save_as_hr_manager(user_id=linked, gender=self.other_gender, date_of_birth="1990-01-01")
		self.assertEqual(frappe.db.get_value("User", linked, "gender"), self.other_gender)
		self.assertNotEqual(self.stamp(linked), before)
		self.assertIn("Employee", frappe.get_roles(linked))

	def test_a_new_link_copies_the_name_birth_date_gender_and_role(self):
		linked = self.users["plain"]
		frappe.db.set_value("User", linked, {"first_name": "X", "last_name": None})
		self.save_as_hr_manager(user_id=linked, gender=self.gender, date_of_birth="1990-01-01")
		user = frappe.get_doc("User", linked)
		parts = (frappe.db.get_value("Employee", self.employee.name, "employee_name") or "").split(" ")
		self.assertEqual(user.first_name, parts[0])
		self.assertEqual((str(user.birth_date), user.gender), ("1990-01-01", self.gender))
		self.assertIn("Employee", [r.role for r in user.roles])
