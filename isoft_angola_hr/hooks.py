from . import __version__ as app_version

app_name = "isoft_angola_hr"
app_title = "Isoft Angola HR"
app_publisher = "Abbass Chokor"
app_description = "Angola HR and Payroll"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "abbasschokor225@gmail.com"
app_license = "MIT"

# Installation / migration
# ------------------------
after_install = "isoft_angola_hr.isoft_angola_hr.install.after_install"
after_migrate = "isoft_angola_hr.isoft_angola_hr.install.after_install"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/isoft_angola_hr/css/isoft_angola_hr.css"
app_include_js = "/assets/isoft_angola_hr/js/angola_hr_icon.js"

# include js, css files in header of web template
# web_include_css = "/assets/isoft_angola_hr/css/isoft_angola_hr.css"
# web_include_js = "/assets/isoft_angola_hr/js/isoft_angola_hr.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "isoft_angola_hr/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "isoft_angola_hr.install.before_install"
# after_install = "isoft_angola_hr.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "isoft_angola_hr.uninstall.before_uninstall"
# after_uninstall = "isoft_angola_hr.uninstall.after_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "isoft_angola_hr.notifications.get_notification_config"

# Permissions
# -----------
# Record-level scoping for self-service. The Employee role holds a bare `read` on these
# DocTypes; these hooks narrow it to the caller's own records. Both hooks are needed —
# the query condition guards lists, has_permission guards a single PDF or private file.
# See doc_permissions.py for why.
_DOC_PERMS = "isoft_angola_hr.isoft_angola_hr.doc_permissions."

permission_query_conditions = {
	"Isoft Salary Slip": _DOC_PERMS + "salary_slip_query",
	"Isoft Employee Document": _DOC_PERMS + "employee_document_query",
	"Isoft Employment Contract": _DOC_PERMS + "contract_query",
	"Isoft Salary Advance": _DOC_PERMS + "salary_advance_query",
	"Isoft Bank Change Request": _DOC_PERMS + "bank_change_query",
}

has_permission = {
	"Isoft Salary Slip": _DOC_PERMS + "salary_slip_permission",
	"Isoft Employee Document": _DOC_PERMS + "employee_document_permission",
	"Isoft Employment Contract": _DOC_PERMS + "contract_permission",
	"Isoft Salary Advance": _DOC_PERMS + "own_record_permission",
	"Isoft Bank Change Request": _DOC_PERMS + "own_record_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	# Half Day / Absent thresholds scaled to the day's scheduled hours, for Shift Types
	# that use this app's per-weekday schedule. Pass-through when they don't.
	"Shift Type": "isoft_angola_hr.isoft_angola_hr.shift_type_override.ShiftType"
}

# override_doctype_class = {
#	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

_NOTIFY = "isoft_angola_hr.isoft_angola_hr.services.hr_notifications."

doc_events = {
	# Cancelling a payroll Journal Entry must release the salary slips that point at it,
	# otherwise the submitted slip's Link blocks the cancellation and the correction
	# workflow deadlocks. Same mechanism ERPNext uses for its own Salary Slip.
	"Journal Entry": {
		"on_cancel": "isoft_angola_hr.isoft_angola_hr.api.unlink_cancelled_payroll_entry",
	},
	# Phase 4 notifications. Each handler swallows its own failures — a notification that
	# cannot be delivered must never roll back the payroll, leave or advance that caused
	# it. None of them contains an amount; they say what happened and link back.
	"Isoft Salary Slip": {"on_submit": _NOTIFY + "notify_payslip_available"},
	"Leave Application": {
		"after_insert": _NOTIFY + "notify_leave_requested",
		"on_submit": _NOTIFY + "notify_leave_decision",
	},
	"Isoft Bank Change Request": {"on_update": _NOTIFY + "notify_bank_change_decision"},
	"Isoft Salary Advance": {"on_update": _NOTIFY + "notify_advance_status"},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
#	"all": [
#		"isoft_angola_hr.tasks.all"
#	],
#	"daily": [
#		"isoft_angola_hr.tasks.daily"
#	],
#	"hourly": [
#		"isoft_angola_hr.tasks.hourly"
#	],
#	"weekly": [
#		"isoft_angola_hr.tasks.weekly"
#	]
#	"monthly": [
#		"isoft_angola_hr.tasks.monthly"
#	]
# }

scheduler_events = {
	"daily": [
		"isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence.isoft_attendance_occurrence.auto_flag_unjustified",
		"isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence.isoft_attendance_occurrence.check_recurrence_alerts",
		"isoft_angola_hr.isoft_angola_hr.doctype.isoft_attendance_occurrence.isoft_attendance_occurrence.check_unjustified_month_alerts",
		# Phase 3 — HR lifecycle upkeep. All three only ever move a record between derived
		# states or notify somebody; none of them approves, renews or pays anything.
		"isoft_angola_hr.isoft_angola_hr.services.contracts.refresh_contract_statuses",
		"isoft_angola_hr.isoft_angola_hr.doctype.isoft_employee_document.isoft_employee_document.refresh_document_statuses",
		"isoft_angola_hr.isoft_angola_hr.services.salary_change.apply_due_changes",
		"isoft_angola_hr.isoft_angola_hr.services.hr_notifications.run_daily_alerts",
	],
}

# Testing
# -------

# before_tests = "isoft_angola_hr.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#	"frappe.desk.doctype.event.event.get_events": "isoft_angola_hr.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#	"Task": "isoft_angola_hr.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Request Events
# ----------------
# before_request = ["isoft_angola_hr.utils.before_request"]
# after_request = ["isoft_angola_hr.utils.after_request"]

# Job Events
# ----------
# before_job = ["isoft_angola_hr.utils.before_job"]
# after_job = ["isoft_angola_hr.utils.after_job"]

# User Data Protection
# --------------------

user_data_fields = [
	{
		"doctype": "{doctype_1}",
		"filter_by": "{filter_by}",
		"redact_fields": ["{field_1}", "{field_2}"],
		"partial": 1,
	},
	{
		"doctype": "{doctype_2}",
		"filter_by": "{filter_by}",
		"partial": 1,
	},
	{
		"doctype": "{doctype_3}",
		"strict": False,
	},
	{
		"doctype": "{doctype_4}"
	}
]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#	"isoft_angola_hr.auth.validate"
# ]

