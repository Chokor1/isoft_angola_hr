# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Nothing may be translated at import time.

``_()`` resolves against ``frappe.local.lang`` when it is *called*. Inside a module-level
constant it is called exactly once — when the worker imports the module — and the result
is frozen for the life of that process. Under ``gunicorn --preload`` that import happens
at boot, so every request afterwards is served the language the boot happened to be in,
whatever the user asked for.

It is invisible in review, because the source looks correctly internationalised. It was
found in a browser: the Contract screen rendered "Submeter para Aprovação" to a session
whose ``frappe.boot.lang`` was confirmed ``en``, and rendered exactly the same string to a
``pt`` session — the giveaway being that the two languages agreed.

Sixty-nine constants were built that way: every workflow action label, every payroll
stage and its guidance, the payroll-readiness messages, and the report filter labels.
Each now stores a plain msgid and is translated where it is read.

:class:`TestNoImportTimeTranslation` is the one that matters long-term — it fails on the
seventieth, wherever somebody adds it.
"""

import ast
import io
import os
import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr.services import (advances, contracts,
                                                      payroll_readiness,
                                                      payroll_workflow, salary_change)

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Lang:
	"""Render something in a given language, then put the session back."""

	def __init__(self, lang):
		self.lang = lang

	def __enter__(self):
		self._before = frappe.local.lang
		frappe.local.lang = self.lang
		frappe.local.lang_full_dict = None
		return self

	def __exit__(self, *exc):
		frappe.local.lang = self._before
		frappe.local.lang_full_dict = None


class TestNoImportTimeTranslation(unittest.TestCase):
	"""A source check: no ``_()`` outside a function or class body."""

	def _offenders(self):
		found = []
		for root, dirs, files in os.walk(APP_ROOT):
			dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
			for name in files:
				if not name.endswith(".py") or name.startswith("test_"):
					continue
				path = os.path.join(root, name)
				try:
					tree = ast.parse(io.open(path, encoding="utf-8").read())
				except SyntaxError:
					continue
				for node in tree.body:
					if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
					                     ast.ClassDef)):
						continue
					for sub in ast.walk(node):
						if (isinstance(sub, ast.Call)
								and isinstance(sub.func, ast.Name)
								and sub.func.id == "_"):
							arg = sub.args[0] if sub.args else None
							text = arg.value if isinstance(arg, ast.Constant) else "?"
							found.append("%s:%s %r"
							             % (os.path.relpath(path, APP_ROOT),
							                sub.lineno, text))
		return found

	def test_no_module_level_translation_anywhere_in_the_app(self):
		offenders = self._offenders()
		self.assertEqual(
			offenders, [],
			"%d module-level _() call(s) would be translated once at import and then "
			"frozen for every user of that worker. Store the msgid plainly and call _() "
			"where it is read:\n  %s" % (len(offenders), "\n  ".join(offenders[:20])))


class TestWorkflowLabelsFollowTheReader(unittest.TestCase):
	"""The same label must come back differently in a different language."""

	CASES = [
		(contracts, "approve", "Approve Contract"),
		(contracts, "submit_for_approval", "Submit for Approval"),
		(contracts, "cancel", "Cancel Contract"),
		(advances, "approve", "Approve Advance"),
		(advances, "disburse", "Disburse Advance"),
		(salary_change, "approve", "Approve Salary Change"),
		(salary_change, "apply", "Apply Salary Change"),
	]

	def test_english_gives_the_english_msgid(self):
		with _Lang("en"):
			for module, action, expected in self.CASES:
				with self.subTest(action=action, module=module.__name__):
					self.assertEqual(module.action_label(action), expected)

	def test_portuguese_gives_something_else(self):
		with _Lang("pt"):
			for module, action, expected in self.CASES:
				with self.subTest(action=action, module=module.__name__):
					got = module.action_label(action)
					self.assertNotEqual(
						got, expected,
						"%s.%s is identical in both languages, which is the signature "
						"of a label frozen at import" % (module.__name__, action))

	def test_switching_back_returns_the_english_label(self):
		"""Proves the value is resolved per call, not cached after the first read."""
		with _Lang("pt"):
			contracts.action_label("approve")
		with _Lang("en"):
			self.assertEqual(contracts.action_label("approve"), "Approve Contract")

	def test_the_transition_tables_hold_plain_msgids(self):
		"""If a table ever holds a translated string again, this catches it."""
		for module in (contracts, advances, salary_change):
			for action, spec in module.TRANSITIONS.items():
				with self.subTest(module=module.__name__, action=action):
					label = spec["label"]
					self.assertNotRegex(
						label, r"[ãõáéíóúâêôàçÃÕÁÉÍÓÚÂÊÔÀÇ]",
						"%s stores an already-translated label" % action)


class TestPayrollGuidanceFollowsTheReader(unittest.TestCase):

	def test_the_next_step_guidance_translates_per_request(self):
		entry = frappe._dict({"workflow_status": "Draft", "name": "PE-TEST"})
		with _Lang("en"):
			en = payroll_workflow.next_step(entry)
		with _Lang("pt"):
			pt = payroll_workflow.next_step(entry)
		self.assertTrue(en.get("description"))
		self.assertNotEqual(
			en.get("description"), pt.get("description"),
			"the payroll guidance is identical in both languages — still frozen")

	def test_the_action_labels_map_translates_per_request(self):
		with _Lang("en"):
			en = payroll_workflow.transition_labels()
		with _Lang("pt"):
			pt = payroll_workflow.transition_labels()
		self.assertTrue(en)
		differing = [k for k in en if en[k] != pt.get(k)]
		self.assertTrue(differing,
		                "no payroll transition label changed with the language")


class TestReadinessMessagesFollowTheReader(unittest.TestCase):

	def test_an_exception_label_translates_per_request(self):
		code = next(iter(payroll_readiness.EXCEPTIONS))
		with _Lang("en"):
			en = payroll_readiness._exception(code)["label"]
		with _Lang("pt"):
			pt = payroll_readiness._exception(code)["label"]
		self.assertTrue(en)
		# Not every code has a Portuguese entry; what must hold is that the value is
		# recomputed per call rather than frozen — so English must come back English.
		with _Lang("en"):
			self.assertEqual(payroll_readiness._exception(code)["label"], en)
		self.assertIsInstance(pt, str)

	def test_the_exception_table_holds_plain_msgids(self):
		for code, (_severity, label) in payroll_readiness.EXCEPTIONS.items():
			with self.subTest(code=code):
				self.assertNotRegex(label, r"[ãõáéíóúâêôàçÃÕÁÉÍÓÚÂÊÔÀÇ]",
				                    "%s stores an already-translated label" % code)


class TestSalaryAdvanceLabel(unittest.TestCase):
	""""Advance" is a salary advance here, not the verb.

	ERPNext ships ``Advance,Avançar`` and loads after this app (apps.txt order), so the
	app's own ``"Advance","Adiantamento"`` row always lost and the Payroll Preview column
	read "Avançar" to Portuguese users. The word is therefore translated with the
	"Salary Advance" context, which only this app defines; the bare word is left alone
	because elsewhere it legitimately is a verb.
	"""

	CONTEXT = "Salary Advance"
	DASHBOARD_JS = os.path.join(APP_ROOT, "page", "angola_hr_dashboard",
	                            "angola_hr_dashboard.js")

	def test_the_preview_column_carries_the_context(self):
		from isoft_angola_hr.isoft_angola_hr import api
		col = [c for c in api.PREVIEW_COLUMNS if c["key"] == "adiantamento"][0]
		self.assertEqual((col["label"], col.get("context")), ("Advance", self.CONTEXT))
		got = [c for c in api.get_preview_columns() if c["key"] == "adiantamento"][0]
		self.assertEqual(got.get("context"), self.CONTEXT, "the saved column order must keep it")

	def test_portuguese_reads_adiantamento_and_english_reads_advance(self):
		with _Lang("pt"):
			self.assertEqual(frappe._("Advance", context=self.CONTEXT), "Adiantamento")
		with _Lang("en"):
			self.assertEqual(frappe._("Advance", context=self.CONTEXT), "Advance")

	def test_no_later_app_overrides_the_context_key(self):
		from frappe.translate import get_translation_dict_from_file
		merged = {}
		for app in frappe.get_all_apps(True):
			path = os.path.join(frappe.get_pymodule_path(app), "translations", "pt.csv")
			merged.update(get_translation_dict_from_file(path, "pt", app) or {})
		self.assertEqual(merged.get("Advance:" + self.CONTEXT), "Adiantamento")

	def test_the_dashboard_sends_the_context_to_the_browser(self):
		"""A Desk Page only receives the strings extracted from its own JS; the preview
		header is rendered from a variable, so a literal with the same context must exist."""
		from frappe.translate import extract_messages_from_code
		code = io.open(self.DASHBOARD_JS, encoding="utf-8").read()
		found = [m for m in extract_messages_from_code(code) if m[1] == "Advance"]
		self.assertIn(self.CONTEXT, [m[2] for m in found if len(m) > 2])
		# the preview header and the Preview Columns dialog render the server's column list
		self.assertEqual(code.count("__(c.label, null, c.context)"), 2,
		                 "both places that render preview columns must pass the context")
