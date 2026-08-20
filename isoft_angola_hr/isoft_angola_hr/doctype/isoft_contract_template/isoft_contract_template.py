# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from isoft_angola_hr.isoft_angola_hr.services import contract_documents as cd


class IsoftContractTemplate(Document):
	def validate(self):
		self.bump_version()
		self.warn_about_placeholders()

	def bump_version(self):
		"""A changed body is a new version (§32).

		Documents already issued keep the version number they were produced from, so this
		can never rewrite history — it only makes the history legible.
		"""
		if self.is_new():
			self.version = cint(self.version) or 1
			return
		previous = frappe.db.get_value("Isoft Contract Template", self.name, "body")
		if (previous or "") != (self.body or ""):
			self.version = cint(self.version) + 1

	def warn_about_placeholders(self):
		report = cd.validate_template(self.body)
		if report["unknown"]:
			# A warning, not an error: HR may be drafting and the body is often pasted in
			# stages. The generator repeats the warning and leaves the text visible.
			frappe.msgprint(
				_("These placeholders are not recognised and will be printed as written: "
				  "{0}").format(", ".join(report["unknown"])),
				title=_("Unknown placeholders"), indicator="orange")
		if report["suspicious"]:
			# Refused outright. Nothing here executes, but a `{% if %}` left in a template
			# prints the tag onto a signed contract, and an expression suggests the author
			# expects code to run — which it never will.
			frappe.throw(
				_("Template expressions and code are not supported and were found in the "
				  "body: {0}. Use only simple placeholders such as {{{{ employee_name }}}}."
				  ).format(", ".join(report["suspicious"])))

	def onload(self):
		self.set_onload("placeholders", cd.available_variables(
			include_salary=bool(cint(self.include_salary))))
