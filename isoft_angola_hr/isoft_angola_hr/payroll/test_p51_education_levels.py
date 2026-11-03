# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""HR, November 2026: the Employee's Education table offers "High School" as a level.

Added by install.setup_education_levels through a Property Setter on
Employee Education.level; ERPNext's DocType is not edited.
"""

import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr import install
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


def levels():
	frappe.clear_cache(doctype="Employee Education")
	return frappe.get_meta("Employee Education").get_field("level").options.split("\n")


def setters():
	return frappe.get_all("Property Setter", filters={
		"doc_type": "Employee Education", "field_name": "level", "property": "options"}, pluck="name")


class TestEducationLevels(unittest.TestCase):
	def setUp(self):
		for name in setters():
			frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
		self.assertNotIn("High School", levels())

	def tearDown(self):
		frappe.clear_cache(doctype="Employee Education")

	def test_high_school_is_offered_and_graduate_stays_the_default(self):
		install.setup_education_levels()
		self.assertEqual(levels(), ["Graduate", "High School", "Post Graduate", "Under Graduate"])

	def test_running_it_again_changes_nothing(self):
		install.setup_education_levels()
		first = setters()
		install.setup_education_levels()
		self.assertEqual(setters(), first)
		self.assertEqual(len(first), 1)
		self.assertEqual(levels().count("High School"), 1)

	def test_a_level_the_site_added_itself_is_kept(self):
		frappe.make_property_setter({
			"doctype": "Employee Education", "doctype_or_field": "DocField", "fieldname": "level",
			"property": "options", "value": "Graduate\nPost Graduate\nUnder Graduate\nDoctorate",
			"property_type": "Text"}, validate_fields_for_doctype=False)
		install.setup_education_levels()
		self.assertEqual(levels(), ["Graduate", "High School", "Post Graduate", "Under Graduate", "Doctorate"])

	def test_the_level_reads_in_portuguese(self):
		import os
		path = os.path.join(frappe.get_app_path("isoft_angola_hr"), "translations", "pt.csv")
		self.assertIn('"High School","Ensino Médio"', open(path, encoding="utf-8").read())
