# -*- coding: utf-8 -*-
# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""What a workflow screen has to answer, computed from the rules that enforce it.

Every approval screen must tell an HR user four things without them asking a developer:

    What is the status?    Who must act next?    What can I do?    What happens after?

All four are derived here from the service's own ``TRANSITIONS`` table and from
``permissions.ACTION_ROLES`` — never written out by hand and never duplicated in
JavaScript. A screen therefore cannot describe a workflow differently from the rule that
governs it, and adding a transition updates every screen at once.
"""

import frappe
from frappe import _

from isoft_angola_hr.isoft_angola_hr.services import permissions as perms


def _roles_for(service, action):
	"""Roles allowed to perform one transition, from the permission matrix itself."""
	spec = service.TRANSITIONS.get(action) or {}
	return sorted(perms.ACTION_ROLES.get(spec.get("permission"), ()))


def _forward_actions(service, state):
	"""Transitions from ``state`` that ADVANCE the record along the happy path.

	Rejecting or cancelling also leaves the current state, but neither is "what happens
	next" — telling HR that the next step is "Reject" would be actively misleading.
	"""
	stages = list(service.STAGES)
	here = stages.index(state) if state in stages else -1
	out = []
	for action, spec in service.TRANSITIONS.items():
		if state not in spec["from"]:
			continue
		target = spec["to"]
		if target in stages and (here < 0 or stages.index(target) > here):
			out.append(action)
	return out


def progress(service, state):
	"""The stage strip: each stage marked done / current / pending.

	A record that has left the happy path (Rejected, Cancelled) reports the stages it
	completed and then that terminal state, rather than pretending it is still moving.
	"""
	stages = list(service.STAGES)
	rows = []
	if state in stages:
		here = stages.index(state)
		for i, stage in enumerate(stages):
			rows.append({
				"stage": stage,
				"label": _(stage),
				"state": "done" if i < here else ("current" if i == here else "pending"),
			})
		return rows

	# Off the happy path: everything before the branch is done, then the terminal state.
	for stage in stages:
		rows.append({"stage": stage, "label": _(stage), "state": "pending"})
	rows.append({"stage": state, "label": _(state), "state": "current"})
	return rows


def summary(service, doc, user=None):
	"""Everything an approval screen needs for one record."""
	state = service.state_of(doc)
	forward = _forward_actions(service, state)

	waiting_roles = sorted({r for a in forward for r in _roles_for(service, a)})
	next_label = None
	if forward:
		# When several forward moves exist, name the first in stage order.
		stages = list(service.STAGES)
		forward.sort(key=lambda a: stages.index(service.TRANSITIONS[a]["to"])
		             if service.TRANSITIONS[a]["to"] in stages else 99)
		next_label = service.action_label(forward[0])

	stages = list(service.STAGES)
	if waiting_roles:
		waiting_for = ", ".join(_(r) for r in waiting_roles)
		next_action = _("{0} — {1}").format(next_label, waiting_for)
	elif state in stages and stages.index(state) < len(stages) - 1:
		# Still moving, but the next stage is not reached by pressing anything: an advance
		# goes Disbursed -> Recovering when payroll takes an instalment, a contract goes
		# Active -> Expiring on its own dates. Saying "no further action" here would send
		# HR looking for a button that should not exist.
		waiting_for = None
		next_action = _("{0} — happens automatically.").format(_(stages[stages.index(state) + 1]))
	elif state in stages:
		waiting_for, next_action = None, _("Complete — no further action.")
	else:
		waiting_for, next_action = None, _("Closed — no further action.")

	mine = service.actions_for(doc, user=user) if _accepts_user(service) else service.actions_for(doc)
	return {
		"status": state,
		"status_label": _(state),
		"waiting_for": waiting_for,
		"next_action": next_action,
		"actions": mine,
		"progress": progress(service, state),
		# True when the record is parked waiting for somebody else to act.
		"awaiting_someone_else": bool(waiting_roles) and not any(
			a["action"] in forward for a in mine),
	}


def _accepts_user(service):
	import inspect
	try:
		return "user" in inspect.signature(service.actions_for).parameters
	except (TypeError, ValueError):
		return False
