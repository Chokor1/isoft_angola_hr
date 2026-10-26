# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""The database-writing tests can not run on a site holding real data.

On 2026-10-14 the suite ran on dev.isoft.ao, which holds ITEC's real employees and
books. The fixtures repoint the payroll account mapping, the tests flip the segregation
switches, and several endpoints they call commit: a cancelled test voucher and test
accounts stayed in the ledger. payroll/testing_guard.py now refuses unless the site is
marked as disposable. These tests keep it that way. They are pure: no database.
"""

import ast
import glob
import os
import unittest
from unittest import mock

import frappe

from isoft_angola_hr.isoft_angola_hr.payroll import testing_guard

HERE = os.path.dirname(os.path.abspath(__file__))

#: Modules proven not to write (each ran inside a READ ONLY MariaDB transaction without
#: a single refused statement). Everything else must guard at module level.
READ_ONLY = {
	"test_p0_reproduction.py": "pure engine and IRT arithmetic",
	"test_p33_irt_2026.py": "reads the IRT table only",
	"test_p37_runtime_language.py": "translation lookups only",
	"test_p48_overtime.py": "pure formula classes; its payroll class is a PayrollFixture",
	"test_p49_test_isolation_guard.py": "this module",
}


class TestEveryWritingModuleIsGuarded(unittest.TestCase):

	def test_each_module_guards_or_is_declared_read_only(self):
		unguarded = []
		for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
			name = os.path.basename(path)
			if name in READ_ONLY:
				continue
			tree = ast.parse(open(path, encoding="utf-8").read())
			aliased = any(isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("testing_guard")
			              and any(a.name == "require_isolated_site" and a.asname == "setUpModule"
			                      for a in n.names) for n in tree.body)
			if not aliased:
				unguarded.append(name)
		self.assertEqual(unguarded, [], "these test modules can write to any site they run on")

	def test_the_shared_fixture_refuses_before_its_first_write(self):
		from isoft_angola_hr.isoft_angola_hr.payroll.test_p0_integration import PayrollFixture
		import inspect
		src = inspect.getsource(PayrollFixture.setUpClass.__func__)
		self.assertLess(src.index("require_isolated_site()"), src.index("_sweep_payroll_leftovers"))
		self.assertLess(src.index("require_isolated_site()"), src.index("super().setUpClass()"),
		                "FrappeTestCase.setUpClass commits")

	def test_bench_run_tests_is_guarded_too(self):
		hooks = frappe.get_hooks("before_tests", app_name="isoft_angola_hr")
		self.assertIn("isoft_angola_hr.isoft_angola_hr.payroll.testing_guard.before_tests", hooks)


class TestTheGuardDecides(unittest.TestCase):

	def run_guard(self, site, marked, common=None, protected=None):
		"""``protected``: (active, verified, protected site) of the protected runner."""
		active, verified, psite = protected or (False, False, None)
		with mock.patch.object(frappe.local, "site", site), \
				mock.patch.dict(frappe.local.conf, {testing_guard.CONFIG_KEY: marked}), \
				mock.patch.object(testing_guard, "_common_config_value", return_value=common), \
				mock.patch.object(testing_guard.STATE, "active", active), \
				mock.patch.object(testing_guard.STATE, "verified", verified), \
				mock.patch.object(testing_guard.STATE, "site", psite):
			testing_guard.require_isolated_site()

	def test_an_unmarked_site_is_refused(self):
		with self.assertRaises(testing_guard.NotAnIsolatedTestSite):
			self.run_guard("dev.isoft.ao", None)

	def test_a_mark_naming_another_site_is_refused(self):
		with self.assertRaises(testing_guard.NotAnIsolatedTestSite):
			self.run_guard("dev.isoft.ao", "qa-hr.local")

	def test_a_mark_in_common_site_config_is_refused(self):
		with self.assertRaises(testing_guard.NotAnIsolatedTestSite):
			self.run_guard("qa-hr.local", "qa-hr.local", common="qa-hr.local")

	def test_the_site_own_mark_is_accepted(self):
		self.run_guard("qa-hr.local", "qa-hr.local")

	def test_a_verified_protected_run_on_this_site_is_accepted(self):
		self.run_guard("dev.isoft.ao", None, protected=(True, True, "dev.isoft.ao"))

	def test_an_unverified_protection_is_refused(self):
		with self.assertRaises(testing_guard.NotAnIsolatedTestSite):
			self.run_guard("dev.isoft.ao", None, protected=(True, False, "dev.isoft.ao"))

	def test_protection_set_up_for_another_site_is_refused(self):
		with self.assertRaises(testing_guard.NotAnIsolatedTestSite):
			self.run_guard("dev.isoft.ao", None, protected=(True, True, "other.site"))


class TestTheProtectionRefusesWhatWouldCommit(unittest.TestCase):
	"""The statement filter, without a database: each of these would end the transaction."""

	def test_implicit_commit_statements_match(self):
		for q in ("alter table `tabX` add column y int", "CREATE TABLE t (x int)", "drop table t",
		          "truncate `tabX`", "rename table a to b", "lock tables `tabX` write",
		          "set autocommit=1", "SET SESSION autocommit = 1", "grant all on x to y"):
			with self.subTest(q=q):
				self.assertTrue(testing_guard._IMPLICIT_COMMIT.match(testing_guard._normalise(q)))

	def test_temporary_tables_and_ordinary_statements_pass(self):
		for q in ("create temporary table t (x int)", "drop temporary table t",
		          "select * from `tabAccount`", "update `tabAccount` set x = 1", "savepoint s"):
			with self.subTest(q=q):
				n = testing_guard._normalise(q)
				self.assertFalse(testing_guard._IMPLICIT_COMMIT.match(n) and not testing_guard._TEMPORARY.match(n))
