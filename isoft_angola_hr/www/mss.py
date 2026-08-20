# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Manager Self-Service portal page (``/mss``).

Note what this file does NOT do: it does not check whether the caller is a manager.
"Manager" is not a role, so there is nothing here to check — the team is derived from
``Employee.reports_to`` inside every endpoint. A person with no reports can open this
page and will simply be told nobody reports to them, having seen no data, because none
of the endpoints will return any.

Gating the page instead of the endpoints would be the wrong way round: it would protect
the screen while leaving the API open.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/mss"
		raise frappe.Redirect

	context.no_cache = 1
	context.show_sidebar = False
	context.full_width = True
	# Marks the page for isoft_ss.css, which trims the ERP site chrome —
	# a newsletter sign-up and a product footer have no place in an
	# employee's payslip screen.
	context.body_class = "isoft-ss-page"
	context.title = _("Área da Chefia")
	context.parents = []
	return context
