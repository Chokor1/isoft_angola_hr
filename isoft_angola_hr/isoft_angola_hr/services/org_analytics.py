# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Organisation chart and workforce analytics.

Two things worth saying up front about the numbers here.

**Headcount is reconstructed, not recorded.** This site has no daily headcount snapshot,
so a month-end figure is derived from ``date_of_joining`` and ``relieving_date`` on the
employees who exist *today*. That is accurate for joiners and leavers and for the totals,
and it is the standard way to do it — but it cannot see anybody whose record was deleted,
and it attributes people to their **current** department, not the one they were in at the
time. Phase 3's Employee Transfer records could refine that; ERPNext only stores the
transfer, not a dated department history, so reconstructing per-department history would
mean inventing data. :func:`headcount_trend` therefore states its own limitation in the
payload rather than quietly presenting a number that looks more precise than it is (§54).

**Absenteeism has a definition, and it is stated.** Approved annual leave is NOT
absenteeism here: it is an entitlement being used as intended. What counts is unjustified
absence and unresolved occurrences. Any other choice is defensible, but leaving it
unstated is not (§56).
"""

import os

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, get_first_day, get_last_day, getdate, nowdate

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


# --------------------------------------------------------------------------- #
# Organisation chart (§52, §53)
# --------------------------------------------------------------------------- #
def _drop_dead_images(rows):
	"""Blank out Employee.image where the file is not actually there.

	On this site all 33 employee photos have a File record but no file on disk — a
	database restored without its private files. The chart falls back to an initials
	avatar either way, so nothing looks broken, but every dead link is still a failed
	request: 19 of them on one screen load.

	Checked on the filesystem, not by querying, so the query count stays constant. One
	stat per employee WITH a photo, and only for locally stored files — anything else
	(a remote URL, a different storage backend) is left alone rather than guessed at,
	because a false negative here would hide a perfectly good photo.
	"""
	seen = {}
	for row in rows:
		url = (row.get("image") or "").strip()
		if not url:
			continue
		if url not in seen:
			seen[url] = _image_exists(url)
		if not seen[url]:
			row["image"] = None


def _image_exists(url):
	if not url.startswith("/files/") and not url.startswith("/private/files/"):
		return True                      # external or unrecognised — do not second-guess
	try:
		path = frappe.get_site_path(url.lstrip("/"))
		return os.path.exists(path)
	except Exception:
		# A storage backend that does not map to a path. Assume the image is fine and
		# let the browser's own fallback handle it.
		return True


def org_chart(company=None, department=None, root=None):
	"""The reporting tree, built from Employee.reports_to.

	Deliberately contains no salary field at any level. There is nothing to hide in the
	UI because there is nothing in the payload.
	"""
	perms.require(perms.EMPLOYEE_READ)
	conditions, values = ["e.status = 'Active'"], []
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	if department:
		conditions.append("e.department = %s")
		values.append(department)
	scope, scope_values = perms.company_filter_sql(alias="e")
	if scope:
		conditions.append(scope)
		values.extend(scope_values)

	# `status` is selected because chart_quality needs it to spot a manager who is no
	# longer active. Leaving it out made the whole chart raise KeyError on any tree that
	# had a parent inside the filtered set.
	rows = frappe.db.sql(
		"""select e.name, e.employee_name, e.designation, e.department, e.company,
			e.reports_to, e.image, e.date_of_joining, e.status
		from `tabEmployee` e where {0}
		order by e.employee_name""".format(" and ".join(conditions)), values, as_dict=True)

	_drop_dead_images(rows)

	by_name = {r["name"]: dict(r, children=[], reports_total=0) for r in rows}
	present = set(by_name)
	roots, orphans = [], []

	for row in by_name.values():
		parent = row["reports_to"]
		if not parent:
			roots.append(row)
		elif parent in present:
			by_name[parent]["children"].append(row)
		else:
			# A manager outside the filtered set (different company, or inactive). The
			# person is still shown — as a root — rather than silently dropped.
			row["detached_parent"] = parent
			orphans.append(row)
			roots.append(row)

	# Subtree sizes, computed iteratively so a cycle cannot blow the stack.
	def count(node, seen):
		if node["name"] in seen:
			return 0
		seen = seen | {node["name"]}
		total = len(node["children"])
		for child in node["children"]:
			total += count(child, seen)
		node["reports_total"] = total
		return total

	for node in roots:
		count(node, frozenset())

	# Sort children by team size then name, so the widest branches read left to right and
	# the tree does not reshuffle between loads.
	for row in by_name.values():
		row["children"].sort(key=lambda c: (-c["reports_total"], c["employee_name"] or ""))
		row["direct_reports"] = len(row["children"])

	departments = sorted({(r["department"] or "").strip() for r in rows if r["department"]})
	return {
		"roots": sorted(roots, key=lambda r: (-r["reports_total"], r["employee_name"] or "")),
		"total": len(rows),
		# Counted here rather than in the browser: the chart shows only the filtered set,
		# but the summary should describe the organisation, not the current filter.
		"departments": departments,
		"department_count": len(departments),
		"with_reports": len([r for r in by_name.values() if r["children"]]),
		"quality": chart_quality(rows, present),
	}


def chart_quality(rows=None, present=None):
	"""Data-quality warnings the chart itself makes visible (§53)."""
	if rows is None:
		rows = frappe.db.sql(
			"""select name, employee_name, reports_to, company, department, status
			from `tabEmployee` where status = 'Active'""", as_dict=True)
		present = {r["name"] for r in rows}
	by_name = {r["name"]: r for r in rows}

	no_manager = [{"employee": r["name"], "employee_name": r["employee_name"]}
	              for r in rows if not r["reports_to"]]
	no_department = [{"employee": r["name"], "employee_name": r["employee_name"]}
	                 for r in rows if not (r.get("department") or "").strip()]

	# Managers referenced but not in the filtered set — a different company, or inactive.
	# Fetched in ONE query. This was two get_value calls per affected employee, so the
	# endpoint cost 1 + 2N queries and grew with the org: 37 queries at 84 employees on
	# this site. The chart is supposed to have a constant query count, and now does.
	missing = {r["reports_to"] for r in rows
	           if r["reports_to"] and r["reports_to"] not in by_name}
	external = {}
	if missing:
		external = {r["name"]: r for r in frappe.db.sql(
			"""select name, employee_name, company, status, department
			from `tabEmployee` where name in ({0})""".format(
				", ".join(["%s"] * len(missing))), tuple(missing), as_dict=True)}

	def parent_field(parent, field):
		if parent in by_name:
			return by_name[parent].get(field)
		row = external.get(parent)
		return row.get(field) if row else None

	outside = []
	for r in rows:
		parent = r["reports_to"]
		if not parent:
			continue
		pcompany = parent_field(parent, "company")
		pstatus = parent_field(parent, "status")
		if pstatus is None and parent not in by_name:
			# The manager record no longer exists at all — a broken reporting link, not a
			# scoping question. Reported rather than silently ignored.
			outside.append({"employee": r["name"], "employee_name": r["employee_name"],
			                "manager": parent,
			                "reason": _("Manager record no longer exists.")})
		elif pstatus and pstatus != "Active":
			outside.append({"employee": r["name"], "employee_name": r["employee_name"],
			                "manager": parent, "reason": _("Manager is not active.")})
		elif pcompany and pcompany != r["company"]:
			outside.append({"employee": r["name"], "employee_name": r["employee_name"],
			                "manager": parent, "reason": _("Manager is in another company.")})

	# Cycles. Frappe's NestedSet does not prevent A→B→A on reports_to, and one cycle
	# makes an org chart render forever.
	cycles = []
	for r in rows:
		seen, node = [], r["name"]
		while node and node in by_name and node not in seen:
			seen.append(node)
			node = by_name[node]["reports_to"]
		if node and node in seen:
			cycle = seen[seen.index(node):] + [node]
			key = tuple(sorted(set(cycle)))
			if key not in {tuple(sorted(set(c["chain"]))) for c in cycles}:
				cycles.append({"chain": cycle})

	return {
		"without_manager": no_manager, "without_manager_count": len(no_manager),
		"without_department": no_department,
		"without_department_count": len(no_department),
		"manager_outside_scope": outside, "manager_outside_scope_count": len(outside),
		"cycles": cycles, "cycle_count": len(cycles),
		# "no manager" is NOT counted as a fault: on a flat organisation most people
		# legitimately have none recorded. It is surfaced as information; only a broken
		# link or a cycle is a genuine defect.
		"ok": not (outside or cycles),
	}


# --------------------------------------------------------------------------- #
# Headcount and turnover (§54, §55)
# --------------------------------------------------------------------------- #
#: How turnover is computed here, stated in the payload so nobody has to guess.
TURNOVER_METHOD = (
	"Turnover % = leavers in the period ÷ average headcount "
	"((opening + closing) ÷ 2) × 100. Headcount is reconstructed from date_of_joining "
	"and relieving_date on the employee records that exist now; employees whose records "
	"were deleted are invisible to it."
)


def headcount_trend(company=None, months=12, department=None):
	"""Month-end headcount, joiners and leavers for the last ``months`` months."""
	perms.require(perms.EMPLOYEE_READ)
	months = max(1, min(cint(months) or 12, 60))
	conditions, values = ["1=1"], []
	if company:
		conditions.append("company = %s")
		values.append(company)
	if department:
		conditions.append("department = %s")
		values.append(department)
	scope, scope_values = perms.company_filter_sql()
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	where = " and ".join(conditions)

	today = getdate(nowdate())
	rows = []
	for offset in range(months - 1, -1, -1):
		month_start = get_first_day(add_months(today, -offset))
		month_end = get_last_day(month_start)

		# Anybody who had joined by month end and had not left before it.
		closing = frappe.db.sql(
			"""select count(*) from `tabEmployee`
			where {0} and date_of_joining <= %s
			  and (relieving_date is null or relieving_date > %s)""".format(where),
			values + [month_end, month_end])[0][0]
		joiners = frappe.db.sql(
			"""select count(*) from `tabEmployee`
			where {0} and date_of_joining between %s and %s""".format(where),
			values + [month_start, month_end])[0][0]
		leavers = frappe.db.sql(
			"""select count(*) from `tabEmployee`
			where {0} and relieving_date between %s and %s""".format(where),
			values + [month_start, month_end])[0][0]

		rows.append({
			"month": month_start.strftime("%Y-%m"),
			"label": month_start.strftime("%b %Y"),
			"opening": cint(closing) - cint(joiners) + cint(leavers),
			"joiners": cint(joiners), "leavers": cint(leavers), "closing": cint(closing),
		})

	for row in rows:
		average = (row["opening"] + row["closing"]) / 2.0
		row["turnover_pct"] = round((row["leavers"] / average * 100.0), 2) if average else 0.0

	total_leavers = sum(r["leavers"] for r in rows)
	avg_headcount = (rows[0]["opening"] + rows[-1]["closing"]) / 2.0 if rows else 0

	return {
		"rows": rows,
		"period_turnover_pct": round(total_leavers / avg_headcount * 100.0, 2)
		if avg_headcount else 0.0,
		"total_joiners": sum(r["joiners"] for r in rows),
		"total_leavers": total_leavers,
		"method": TURNOVER_METHOD,
		# Said out loud rather than implied by a caveat in a tooltip.
		"limitations": [
			_("Headcount is reconstructed from joining and relieving dates, not from a "
			  "stored month-end snapshot."),
			_("Employees are counted under their CURRENT department. Historical department "
			  "membership is not recorded on this site, so a per-department trend before a "
			  "transfer is not reliable."),
			_("Deleted employee records cannot be seen by this calculation."),
		],
		"department_history_reliable": False,
	}


def turnover(company=None, from_date=None, to_date=None, department=None):
	"""Opening, joiners, leavers, closing and turnover % for one explicit period (§55)."""
	perms.require(perms.EMPLOYEE_READ)
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else get_first_day(add_months(to_date, -11))

	conditions, values = ["1=1"], []
	if company:
		conditions.append("company = %s")
		values.append(company)
	if department:
		conditions.append("department = %s")
		values.append(department)
	scope, scope_values = perms.company_filter_sql()
	if scope:
		conditions.append(scope)
		values.extend(scope_values)
	where = " and ".join(conditions)

	def at(day):
		return cint(frappe.db.sql(
			"""select count(*) from `tabEmployee`
			where {0} and date_of_joining <= %s
			  and (relieving_date is null or relieving_date > %s)""".format(where),
			values + [day, day])[0][0])

	opening = at(from_date)
	closing = at(to_date)
	joiners = cint(frappe.db.sql(
		"""select count(*) from `tabEmployee`
		where {0} and date_of_joining between %s and %s""".format(where),
		values + [from_date, to_date])[0][0])
	leavers = cint(frappe.db.sql(
		"""select count(*) from `tabEmployee`
		where {0} and relieving_date between %s and %s""".format(where),
		values + [from_date, to_date])[0][0])

	average = (opening + closing) / 2.0
	return {
		"from_date": str(from_date), "to_date": str(to_date),
		"opening": opening, "joiners": joiners, "leavers": leavers, "closing": closing,
		"average_headcount": round(average, 1),
		"turnover_pct": round(leavers / average * 100.0, 2) if average else 0.0,
		"method": TURNOVER_METHOD,
	}


# --------------------------------------------------------------------------- #
# Absenteeism (§56)
# --------------------------------------------------------------------------- #
#: The definition, in one place, returned with every result.
ABSENTEEISM_DEFINITION = {
	"counts_as_absence": [
		"Attendance marked Absent",
		"Attendance Occurrence with status Unjustified",
		"Attendance Occurrence still Pending Justification past its deadline",
	],
	"excluded": [
		"Approved leave of any type — an entitlement used as intended is not absenteeism",
		"Justified occurrences",
		"Public holidays and non-working days",
	],
	"formula": "Absenteeism % = absent days ÷ (present + absent + half days) × 100",
}


def absenteeism(company=None, from_date=None, to_date=None, department=None):
	perms.require(perms.EMPLOYEE_READ)
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else get_first_day(add_months(to_date, -2))

	conditions, values = ["a.docstatus = 1", "a.attendance_date between %s and %s"], \
		[from_date, to_date]
	if company:
		conditions.append("e.company = %s")
		values.append(company)
	if department:
		conditions.append("e.department = %s")
		values.append(department)

	rows = frappe.db.sql(
		"""select e.name as employee, e.employee_name, e.department,
			sum(case when a.status = 'Present' then 1 else 0 end) as present,
			sum(case when a.status = 'Absent' then 1 else 0 end) as absent,
			sum(case when a.status = 'Half Day' then 0.5 else 0 end) as half_days,
			sum(case when a.status = 'On Leave' then 1 else 0 end) as on_leave
		from `tabAttendance` a join `tabEmployee` e on e.name = a.employee
		where {0} group by e.name order by absent desc""".format(" and ".join(conditions)),
		values, as_dict=True)

	occ_conditions, occ_values = ["o.occurrence_date between %s and %s"], [from_date, to_date]
	if company:
		occ_conditions.append("e.company = %s")
		occ_values.append(company)
	if department:
		occ_conditions.append("e.department = %s")
		occ_values.append(department)
	occurrences = {r["employee"]: r for r in frappe.db.sql(
		"""select o.employee,
			sum(case when o.status = 'Unjustified' then 1 else 0 end) as unjustified,
			sum(case when o.status = 'Justified' then 1 else 0 end) as justified,
			sum(case when o.status = 'Pending Justification' then 1 else 0 end) as pending
		from `tabIsoft Attendance Occurrence` o join `tabEmployee` e on e.name = o.employee
		where {0} group by o.employee""".format(" and ".join(occ_conditions)),
		occ_values, as_dict=True)}

	total_absent = total_scheduled = 0
	for row in rows:
		occ = occurrences.get(row["employee"], {})
		row["unjustified_occurrences"] = cint(occ.get("unjustified"))
		row["justified_occurrences"] = cint(occ.get("justified"))
		row["pending_occurrences"] = cint(occ.get("pending"))
		scheduled = flt(row["present"]) + flt(row["absent"]) + flt(row["half_days"])
		absent = flt(row["absent"]) + flt(row["half_days"])
		row["scheduled_days"] = scheduled
		row["absent_days"] = absent
		row["absenteeism_pct"] = round(absent / scheduled * 100.0, 2) if scheduled else 0.0
		total_absent += absent
		total_scheduled += scheduled

	return {
		"from_date": str(from_date), "to_date": str(to_date),
		"rows": rows,
		"absenteeism_pct": round(total_absent / total_scheduled * 100.0, 2)
		if total_scheduled else 0.0,
		"total_absent_days": total_absent,
		"total_scheduled_days": total_scheduled,
		"approved_leave_days": sum(flt(r["on_leave"]) for r in rows),
		"definition": ABSENTEEISM_DEFINITION,
	}


def analytics_dashboard(company=None, months=12):
	"""The small set of trends that are actually operational (§57).

	No decorative charts: every series here corresponds to something HR has to act on.
	"""
	perms.require(perms.EMPLOYEE_READ)
	company = company or frappe.defaults.get_user_default("Company")
	trend = headcount_trend(company=company, months=months)
	absence = absenteeism(company=company)
	expiries = frappe.db.sql(
		"""select date_format(c.end_date, '%%Y-%%m') as month, count(*) as n
		from `tabIsoft Employment Contract` c
		where c.status in ('Active', 'Expiring') and c.end_date is not null
		  and ifnull(c.is_open_ended, 0) = 0 and c.end_date >= curdate()
		  and (%s is null or c.company = %s)
		group by month order by month limit 12""", (company, company), as_dict=True)
	leave_usage = frappe.db.sql(
		"""select la.leave_type, sum(la.total_leave_days) as days, count(*) as requests
		from `tabLeave Application` la join `tabEmployee` e on e.name = la.employee
		where la.docstatus = 1 and la.status = 'Approved'
		  and la.from_date >= %s and (%s is null or e.company = %s)
		group by la.leave_type order by days desc""",
		(get_first_day(add_months(getdate(nowdate()), -11)), company, company), as_dict=True)

	return {
		"company": company,
		"headcount": trend,
		"absenteeism": absence,
		"contract_expiries": expiries,
		"leave_usage": leave_usage,
		"org_quality": chart_quality(),
	}
