# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The Christmas bonus run (run_type "Christmas Bonus") — HR's document of November 2026.

See payroll/christmas.py for the rule. These tests write payroll documents, so they run
only through the protected runner (payroll/testing_guard.py).
"""

import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr import install
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only


class TestDataModel(unittest.TestCase):
	def test_existing_runs_read_as_salary(self):
		install.backfill_run_type()
		for dt in ("Isoft Payroll Entry", "Isoft Salary Slip"):
			self.assertEqual(frappe.get_meta(dt).get_field("run_type").options.split("\n"),
			                 ["Salary", "Christmas Bonus"], dt)
			self.assertEqual(frappe.db.count(dt, {"run_type": ("in", ("", None))}), 0, dt)
			self.assertEqual(frappe.db.sql("select count(*) from `tab{0}` where ifnull(run_type,'')=''"
			                               .format(dt))[0][0], 0, dt)
