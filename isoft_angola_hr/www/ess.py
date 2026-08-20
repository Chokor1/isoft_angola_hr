# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Employee Self-Service portal page (``/ess``).

A portal page rather than a Desk page, deliberately. The Desk is an administrative
console — it assumes a large screen, loads the whole framework, and exposes navigation an
employee has no business seeing. This is one screen, on a phone, with seven tabs.

The page itself carries no data. Everything is fetched by ``isoft_ess.js`` from the
whitelisted endpoints, which derive the employee from the session. That keeps exactly one
copy of the access rules — on the server — and means this file cannot leak anything by
rendering the wrong context variable.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		# Send them to log in and come back, rather than a bare 403.
		frappe.local.flags.redirect_location = "/login?redirect-to=/ess"
		raise frappe.Redirect

	context.no_cache = 1
	context.show_sidebar = False
	context.full_width = True
	# Marks the page for isoft_ss.css, which trims the ERP site chrome —
	# a newsletter sign-up and a product footer have no place in an
	# employee's payslip screen.
	context.body_class = "isoft-ss-page"
	context.title = _("Área do Colaborador")
	context.parents = []
	return context
