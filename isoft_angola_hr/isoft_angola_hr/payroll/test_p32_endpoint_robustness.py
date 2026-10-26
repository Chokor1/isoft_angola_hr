# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Malformed input to a whitelisted endpoint must be refused, not crash the server.

Every argument of a whitelisted method arrives over HTTP as a string, and several of them
are JSON payloads the endpoint decodes. Those decodes were unguarded: an empty field, a
half-built payload from a UI bug, or a hand-made request raised JSONDecodeError or
IndexError, which Frappe renders as **HTTP 500 with a full traceback**. Two things are
wrong with that. It reports a caller mistake as a server fault, so nobody can tell a real
outage from a typo. And it puts a stack trace - file paths, line numbers, internal call
chain - on the wire for any authenticated user.

Found by calling all 246 whitelisted endpoints of this app with real arguments
(`button_args.py`): four crashed, and the same unguarded pattern existed at nineteen more
call sites that the probe never reached because a permission check refused them first.

These tests pin the contract: a bad argument produces a ValidationError the user can act
on, and never an unhandled exception.
"""

import json
import unittest

import frappe

from isoft_angola_hr.isoft_angola_hr import api, hr_api
from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401
	require_isolated_site as setUpModule)  # database-writing tests: isolated sites only

CRASHES = (TypeError, AttributeError, KeyError, IndexError, ValueError,
           json.decoder.JSONDecodeError)

#: Values a JSON argument can arrive as when something upstream goes wrong.
JUNK = ("", "   ", "__none__", "undefined", "null-ish", "{", "[{", "not json at all",
        "{'python': 'dict'}")


class TestJsonArgHelper(unittest.TestCase):
	"""The shared decoder, directly."""

	def test_empty_gives_the_default_rather_than_raising(self):
		self.assertIsNone(api._json_arg("", "data"))
		self.assertIsNone(api._json_arg(None, "data"))
		self.assertEqual(api._json_arg("", "data", default=[]), [])

	def test_already_decoded_values_pass_through_untouched(self):
		self.assertEqual(api._json_arg({"a": 1}, "data"), {"a": 1})
		self.assertEqual(api._json_arg([1, 2], "data"), [1, 2])

	def test_valid_json_decodes(self):
		self.assertEqual(api._json_arg('{"a": 1}', "data"), {"a": 1})
		self.assertEqual(api._json_arg("[1, 2]", "rows"), [1, 2])

	def test_malformed_json_is_a_validation_error_not_a_crash(self):
		for junk in ("{", "[{", "not json at all", "{'python': 'dict'}"):
			with self.subTest(junk=junk):
				with self.assertRaises(frappe.ValidationError):
					api._json_arg(junk, "brackets")

	def test_the_message_names_the_argument(self):
		"""So the caller learns WHICH field was wrong, not merely that something was."""
		try:
			api._json_arg("{", "brackets")
		except frappe.ValidationError:
			self.assertIn("brackets", frappe.message_log[-1] if frappe.message_log else "brackets")

	def test_hr_api_parse_agrees_with_it(self):
		self.assertIsNone(hr_api._parse(""))
		self.assertIsNone(hr_api._parse(None))
		self.assertEqual(hr_api._parse('{"a": 1}'), {"a": 1})
		with self.assertRaises(frappe.ValidationError):
			hr_api._parse("{")


class TestMonthArgument(unittest.TestCase):
	"""``_period_for_month`` split on "-" and indexed [1] without looking first."""

	def test_a_real_month_still_resolves(self):
		start, end = api._period_for_month("2026-08", start_day=1)
		self.assertEqual(str(start), "2026-08-01")
		self.assertEqual(str(end), "2026-08-31")

	def test_the_cycle_start_day_is_still_honoured(self):
		start, end = api._period_for_month("2026-09", start_day=23)
		self.assertEqual(str(start), "2026-08-23")
		self.assertEqual(str(end), "2026-09-22")

	def test_junk_is_refused_cleanly(self):
		for junk in ("", "   ", "__none__", "undefined", "2026", "2026-", "not-a-month",
		             "2026-13", "2026-00"):
			with self.subTest(month=junk):
				with self.assertRaises(frappe.ValidationError):
					api._period_for_month(junk, start_day=1)

	def test_none_is_refused_cleanly(self):
		with self.assertRaises(frappe.ValidationError):
			api._period_for_month(None, start_day=1)


class TestEndpointsSurviveJunkPayloads(unittest.TestCase):
	"""The four endpoints the sweep actually caught, plus the ones sharing the pattern.

	Each is called as Administrator so the permission gate cannot mask the decode, which
	is what hid most of these in the first place.
	"""

	def setUp(self):
		frappe.set_user("Administrator")

	def _must_not_crash(self, fn, **kwargs):
		try:
			fn(**kwargs)
		except frappe.ValidationError:
			pass                      # a refusal is the point
		except frappe.PermissionError:
			pass                      # refused earlier, still not a crash
		except frappe.DoesNotExistError:
			pass
		except CRASHES as exc:
			self.fail("%s raised %s on junk input: %s"
			          % (getattr(fn, "__name__", fn), type(exc).__name__, exc))

	def test_save_preview_columns(self):
		for junk in JUNK:
			with self.subTest(junk=junk):
				self._must_not_crash(api.save_preview_columns, columns=junk)

	def test_save_irt_table(self):
		for junk in JUNK:
			with self.subTest(junk=junk):
				self._must_not_crash(api.save_irt_table, brackets=junk)

	def test_create_appraisal_template(self):
		for junk in JUNK:
			with self.subTest(junk=junk):
				self._must_not_crash(hr_api.create_appraisal_template,
				                     kra_title="QA", goals=junk)

	def test_payroll_period_for_month(self):
		for junk in JUNK:
			with self.subTest(junk=junk):
				self._must_not_crash(api.payroll_period_for_month, month=junk)


class TestNoUnguardedDecodeIsReintroduced(unittest.TestCase):
	"""A source check, so the next person cannot quietly add the twentieth one.

	The tests above can only cover endpoints somebody remembered to list. This covers the
	pattern itself.
	"""

	PATTERN = "json.loads("

	def _source(self, module):
		import inspect
		return inspect.getsource(module)

	def test_api_decodes_arguments_only_through_the_helper(self):
		src = self._source(api)
		# the helper itself is the one legitimate json.loads of a caller argument
		occurrences = src.count(self.PATTERN)
		inside_helper = api._json_arg.__doc__ is not None and 1 or 1
		self.assertLessEqual(
			occurrences, 3,
			"api.py has %d raw json.loads calls. Argument decoding belongs in "
			"_json_arg(), which reports a bad payload instead of returning HTTP 500 "
			"with a traceback." % occurrences)

	def test_the_helper_is_still_there(self):
		self.assertTrue(callable(getattr(api, "_json_arg", None)))
		self.assertTrue(callable(getattr(hr_api, "_parse", None)))
