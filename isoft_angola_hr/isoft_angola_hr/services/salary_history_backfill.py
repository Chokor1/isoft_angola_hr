# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""One-off reconstruction of Isoft Salary History from Frappe's Version log.

Why this exists
---------------
Salary History is written by ``Isoft Salary Profile.on_update``. That hook was added
after the profiles themselves had been created, so every profile that predates it has no
history at all — including no baseline row for the salary the employee started on. HR
opens the salary history of a long-standing employee and sees either nothing or a single
"Updated" row with no "before".

What it reconstructs, and what it refuses to
--------------------------------------------
Only what Frappe's own Version log proves. For a profile with Version entries this module
can recover the exact value of every tracked amount before and after each recorded edit,
so it emits:

  * one baseline row, at the profile's effective date, holding the values as they stood
    before the first recorded edit;
  * one row per recorded edit, holding the values after it.

For a profile with no Version entries nothing is written. The original salary genuinely
is not recoverable there, and inventing a baseline from today's figures would put a
number into the pay record that nobody can support.

Every row it writes is marked ``(reconstruído)`` in change_type and carries a note naming
Version as its source, so a reconstructed row can never be mistaken for one that was
recorded as it happened.

Safety
------
Idempotent: an event that already has a history row within ``_MATCH_WINDOW_SECONDS`` is
skipped, so running it twice does not double up. Amounts are parsed strictly — Version
stores Currency fields as FORMATTED strings ("KZ 220 000,00"), and a profile whose values
cannot all be parsed with certainty is skipped whole and reported rather than guessed at.
"""

import json
import re

import frappe
from frappe.utils import flt, get_datetime

#: Amount fields tracked in the salary history.
TRACKED = ("base", "food_allowance", "transport_allowance", "family_allowance")

#: An event this close to an existing history row is taken to be that same row.
_MATCH_WINDOW_SECONDS = 5

_RECONSTRUCTED = "(reconstruído)"
_NOTE = ("Reconstruído a partir do registo de versões do Frappe (Version). "
         "Esta linha não foi registada no momento da alteração.")


class UnparseableAmount(ValueError):
	"""Raised when a Version value cannot be read back as a number with certainty."""


def _parse_amount(value):
	"""Read a Version amount back to a float.

	Version stores Currency fields already formatted for display, so the value arriving
	here may be ``"KZ 220 000,00"``, ``"0.0"``, ``0`` or ``None``. Anything that cannot be
	read unambiguously raises rather than returning a plausible-looking number: this feeds
	a pay record.
	"""
	if value is None or value == "":
		return 0.0
	if isinstance(value, (int, float)):
		return flt(value)

	text = str(value).replace(" ", " ").strip()
	# Drop the currency symbol and any other non-numeric decoration.
	cleaned = re.sub(r"[^\d.,\-]", "", text)
	if not cleaned or cleaned in ("-", ".", ","):
		raise UnparseableAmount(value)

	has_dot, has_comma = "." in cleaned, "," in cleaned
	if has_dot and has_comma:
		# Both present: the LAST one is the decimal separator, the other groups thousands.
		if cleaned.rfind(",") > cleaned.rfind("."):
			cleaned = cleaned.replace(".", "").replace(",", ".")
		else:
			cleaned = cleaned.replace(",", "")
	elif has_comma:
		# The site's number format is "# ###,##", so a lone comma is the decimal point.
		cleaned = cleaned.replace(",", ".")

	try:
		return flt(float(cleaned))
	except (TypeError, ValueError):
		raise UnparseableAmount(value)


def _tracked_changes(version_row):
	"""[(field, old, new)] for the tracked amounts in one Version row."""
	try:
		data = json.loads(version_row.data or "{}")
	except ValueError:
		raise UnparseableAmount(version_row.name)
	out = []
	for entry in data.get("changed") or []:
		if not entry or len(entry) < 3:
			continue
		field, old, new = entry[0], entry[1], entry[2]
		if field in TRACKED:
			out.append((field, _parse_amount(old), _parse_amount(new)))
	return out


def _existing_events(profile):
	"""Datetimes of the history rows a profile already has."""
	return [
		get_datetime(d)
		for d in frappe.get_all("Isoft Salary History", filters={"salary_profile": profile},
		                        pluck="change_date")
		if d
	]


def _already_recorded(when, existing):
	return any(abs((when - e).total_seconds()) <= _MATCH_WINDOW_SECONDS for e in existing)


def plan(profile=None):
	"""Work out what would be written, without writing anything.

	Returns ``{"events": [...], "skipped": [...], "profiles": n}``.
	"""
	filters = {"ref_doctype": "Isoft Salary Profile"}
	if profile:
		filters["docname"] = profile
	versions = frappe.get_all("Version", filters=filters,
	                          fields=["name", "docname", "creation", "data"],
	                          order_by="docname asc, creation asc")

	by_profile = {}
	for v in versions:
		by_profile.setdefault(v.docname, []).append(v)

	events, skipped = [], []
	for name, rows in by_profile.items():
		prof = frappe.db.get_value(
			"Isoft Salary Profile", name,
			["name", "employee", "employee_name", "from_date", "creation"] + list(TRACKED),
			as_dict=True)
		if not prof:
			skipped.append({"profile": name, "reason": "profile no longer exists"})
			continue

		try:
			chain = [(get_datetime(r.creation), _tracked_changes(r)) for r in rows]
		except UnparseableAmount as e:
			# Whole profile, not just the field: a half-read chain reconstructs the rest
			# of the timeline wrongly.
			skipped.append({"profile": name, "employee": prof.employee,
			                "employee_name": prof.employee_name,
			                "reason": "unreadable Version amount ({0})".format(e)})
			continue

		chain = [(when, ch) for when, ch in chain if ch]
		if not chain:
			continue  # versions exist but touched no tracked amount

		# Walk backwards from today's values to the state before the first recorded edit.
		state = {f: flt(prof.get(f)) for f in TRACKED}
		for _when, changes in reversed(chain):
			for field, old, _new in changes:
				state[field] = old
		baseline = dict(state)

		existing = _existing_events(name)
		profile_events = [{
			"profile": name, "employee": prof.employee, "employee_name": prof.employee_name,
			"from_date": prof.from_date, "when": get_datetime(prof.creation),
			"change_type": "Created " + _RECONSTRUCTED, "values": dict(baseline),
			"duplicate": _already_recorded(get_datetime(prof.creation), existing),
		}]
		for when, changes in chain:
			for field, _old, new in changes:
				state[field] = new
			profile_events.append({
				"profile": name, "employee": prof.employee,
				"employee_name": prof.employee_name, "from_date": prof.from_date,
				"when": when, "change_type": "Updated " + _RECONSTRUCTED,
				"values": dict(state), "duplicate": _already_recorded(when, existing),
			})
		events.extend(profile_events)

	return {"events": events, "skipped": skipped, "profiles": len(by_profile)}


def run(dry_run=True, profile=None):
	"""Reconstruct the missing history. Pass ``dry_run=False`` to actually write."""
	result = plan(profile=profile)
	to_write = [e for e in result["events"] if not e["duplicate"]]

	written = 0
	if not dry_run:
		for e in to_write:
			doc = frappe.get_doc({
				"doctype": "Isoft Salary History",
				"employee": e["employee"],
				"employee_name": e["employee_name"],
				"salary_profile": e["profile"],
				"from_date": e["from_date"],
				"change_date": e["when"],
				"changed_by": None,
				"change_type": e["change_type"],
				"note": _NOTE,
				**e["values"],
			})
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			written += 1
		frappe.db.commit()

	return {
		"dry_run": dry_run,
		"profiles_with_versions": result["profiles"],
		"events_found": len(result["events"]),
		"already_recorded": len(result["events"]) - len(to_write),
		"to_write": len(to_write),
		"written": written,
		"skipped": result["skipped"],
		"events": to_write,
	}


def backfill_from_dates():
	"""Fill ``from_date`` on history rows written before the field existed.

	Provable and separate from the reconstruction above: the effective date is read off
	the linked Salary Profile, not inferred.
	"""
	rows = frappe.get_all("Isoft Salary History",
	                      filters={"from_date": ["is", "not set"]},
	                      fields=["name", "salary_profile"])
	filled = 0
	for r in rows:
		if not r.salary_profile:
			continue
		from_date = frappe.db.get_value("Isoft Salary Profile", r.salary_profile, "from_date")
		if from_date:
			frappe.db.set_value("Isoft Salary History", r.name, "from_date", from_date,
			                    update_modified=False)
			filled += 1
	frappe.db.commit()
	return {"candidates": len(rows), "filled": filled}
