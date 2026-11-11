"""Employee that re-saves a User only when the User actually changes.

ERPNext's Employee.on_update calls update_user() and update_approver_role() on every
save. Both end in a User save even when nothing changes: Frappe's User.add_roles() saves
whether or not the role was already there, and update_user() always calls user.save().
With ignore_permissions set, every Employee edit therefore re-saved the linked user and
both approvers as the current user — on production an HR Manager with no rights on User
became "modified by" on 13 User records after editing ~60 employees, with no field changed
and no Version row.

Registered through `override_doctype_class`. The two methods below make the same changes
as ERPNext's and save the User only when one of them applies; everything else in
Employee is untouched.
"""
import frappe
from frappe.utils import getdate

from erpnext.hr.doctype.employee.employee import Employee as _ERPNextEmployee


class IsoftEmployee(_ERPNextEmployee):
	def update_user(self):
		"""ERPNext's update_user(), saving the User only when something was copied to it."""
		# add employee role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True
		changed = False

		# ERPNext tests `"Employee" not in user.get("roles")` — a list of Has Role rows,
		# so it is always true; compare the role names instead.
		if "Employee" not in [d.role for d in user.get("roles")]:
			user.append_roles("Employee")
			changed = True

		# copy details like Fullname, DOB and Image to User
		if self.employee_name and not (user.first_name and user.last_name):
			employee_name = self.employee_name.split(" ")
			names = {"first_name": employee_name[0]}
			if len(employee_name) >= 3:
				names["last_name"] = " ".join(employee_name[2:])
				names["middle_name"] = employee_name[1]
			elif len(employee_name) == 2:
				names["last_name"] = employee_name[1]
			for field, value in names.items():
				if user.get(field) != value:
					user.set(field, value)
					changed = True

		if self.date_of_birth and (not user.birth_date
		                           or getdate(user.birth_date) != getdate(self.date_of_birth)):
			user.birth_date = self.date_of_birth
			changed = True

		if self.gender and user.gender != self.gender:
			user.gender = self.gender
			changed = True

		if self.image:
			if not user.user_image:
				user.user_image = self.image
				changed = True
				try:
					frappe.get_doc(
						{
							"doctype": "File",
							"file_url": self.image,
							"attached_to_doctype": "User",
							"attached_to_name": self.user_id,
						}
					).insert()
				except frappe.DuplicateEntryError:
					# already exists
					pass

		if changed:
			user.save()

	def update_approver_role(self):
		"""ERPNext's update_approver_role(), skipping an approver who already has the role
		(User.add_roles saves the User even when the role is there)."""
		for field, role in (("leave_approver", "Leave Approver"),
		                    ("expense_approver", "Expense Approver")):
			approver = self.get(field)
			if not approver:
				continue
			if frappe.db.exists("Has Role", {"parent": approver, "parenttype": "User", "role": role}):
				continue
			user = frappe.get_doc("User", approver)
			user.flags.ignore_permissions = True
			user.add_roles(role)
