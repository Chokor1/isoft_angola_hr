// Copyright (c) 2026, ISOFT LDA
// Author: Abbass Chokor
// For license information, please see license.txt
// Angola HR Dashboard - self-contained management console (single page).

frappe.pages["angola-hr-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Angola HR"),
		single_column: true,
	});
	// Drop the standard Frappe page header — the sidebar is the only chrome.
	$(wrapper).find(".page-head").hide();
	$(wrapper).addClass("ahr-page-wrapper");

	// Full-screen the dashboard: hide the global Frappe navbar while on this route,
	// and restore it when navigating away (same behaviour as the other Isoft apps).
	function applyNavbar() {
		const onPage = (frappe.get_route_str() || "").includes("angola-hr-dashboard");
		const $bars = $("header.navbar, .navbar.navbar-default.navbar-fixed-top, .navbar-expand-lg");
		if (onPage) {
			$bars.hide();
			$(".layout-main-section-wrapper").css("margin-top", "0");
			$(".page-container").css("padding-top", "0");
			$("body").addClass("ahr-fullscreen");
		} else {
			$bars.show();
			$(".layout-main-section-wrapper").css("margin-top", "");
			$(".page-container").css("padding-top", "");
			$("body").removeClass("ahr-fullscreen");
		}
	}
	applyNavbar();
	$(document).ready(applyNavbar);
	setTimeout(applyNavbar, 100);
	setTimeout(applyNavbar, 500);
	$(window).on("hashchange", applyNavbar);
	// This build uses path-based routing (pushState), where `hashchange` does NOT fire on
	// navigation. Hook the real router change event so the global navbar is restored when
	// leaving the dashboard (and re-hidden when returning). Bind once, globally.
	if (!window.__ahrNavHook && frappe.router && frappe.router.on) {
		window.__ahrNavHook = true;
		frappe.router.on("change", applyNavbar);
	}

	new AngolaHR(page);
};

const API = "isoft_angola_hr.isoft_angola_hr.api.";
// Phase 3 HR endpoints live in their own module; api.py was already 2 600 lines.
const HR_API = "isoft_angola_hr.isoft_angola_hr.hr_api.";
const HR_METHODS = new Set([
	"hr_dashboard", "hr_readiness", "hr_approval_inbox", "onboarding_checklist",
	"employee_timeline", "employee_360", "list_contracts", "contracts_expiring",
	"probations_due", "create_contract", "contract_action", "renew_contract",
	"probation_decision", "list_salary_changes", "create_salary_change",
	"salary_change_action", "list_advances", "create_advance", "advance_action",
	"bank_change_action", "list_bank_change_requests",
	// Phase 4
	"bulk_candidates", "bulk_contract_preview", "bulk_contract_execute",
	"generate_contract_document", "finalise_contract_document", "contract_documents_for",
	"preview_contract_template", "contract_template_variables", "attach_signed_contract",
	"new_hire_readiness", "exit_checklist", "recruitment_pipeline",
	"recruitment_conversion_check", "recruitment_convert", "performance_summary",
	"employee_training", "org_chart", "org_chart_quality", "headcount_trend",
	"turnover", "absenteeism", "analytics_dashboard", "statutory_validate",
	"statutory_generate", "statutory_history", "statutory_record_submission",
	"bank_format_status", "self_service_context",
	// UX completion — the create endpoints behind the new buttons
	"next_payroll_boundary", "create_performance_cycle", "list_performance_cycles",
	"create_appraisal_template", "list_appraisal_templates", "create_job_opening",
	"create_job_applicant", "create_job_offer", "recruitment_reference_data",
	"schedule_interview", "interview_pipeline", "performance_cycle_preview",
	"performance_cycle_generate", "performance_cycle_close",
	// HR-operated mode — the front doors for work that used to live only in /ess or /mss
	"create_bank_change", "add_employee_document", "record_justification",
	"record_evaluation", "record_acknowledgement", "record_interview_result",
	"hr_action_queue", "self_approval_policy", "login_dependencies", "request_sources",
	"list_employee_documents", "document_type_options", "open_appraisals",
	"appraisal_goals", "verify_employee_document", "documents_pending_verification",
	"finalise_review", "review_recommend_salary_change",
]);

/* The channels a request reaches HR through. Mirrors hr_operations.REQUEST_SOURCES;
 * the server validates against its own list, so a stale copy here can only produce a
 * refusal, never a wrong record. */
const REQUEST_SOURCES = [
	"Employee verbal request", "Email", "Written request",
	"Management instruction", "HR initiated", "Other",
];
const sourceField = (dflt) => ({
	fieldname: "request_source", label: __("Request Source"), fieldtype: "Select",
	options: REQUEST_SOURCES.join("\n"), default: dflt || "Employee verbal request",
	description: __("How the request reached HR. Your own user is recorded separately as the person who entered it."),
});
/* NAVIGATION — organised by HR TASK, not by technical module.
 *
 * The product is operated by the HR department. Employees and line managers are not
 * required to log in at all, so the menu has to answer "what am I doing?" rather than
 * "which DocType is this?". The previous layout grouped by record type, which is why
 * an administrator looking for "record the advance somebody asked me for this morning"
 * had to already know it was stored as an Isoft Salary Advance.
 *
 * REQUESTS & APPROVALS is the operational heart: every one of those screens is something
 * an employee asked HR for, and every one of them HR both creates and decides.
 */
const NAV = [
	{ key: "overview", label: "Overview", icon: "fa-th-large" },
	{
		group: "people", label: "Employees", icon: "fa-users",
		children: [
			{ key: "employees", label: "Employees", icon: "fa-users" },
			{ key: "contracts", label: "Contracts", icon: "fa-file-text" },
			{ key: "bulkcontracts", label: "Bulk Contracts", icon: "fa-files-o" },
			{ key: "documents", label: "Employee Documents", icon: "fa-folder-open-o" },
			{ key: "profiles", label: "Salary Profiles", icon: "fa-id-card-o" },
			{ key: "offboarding", label: "Offboarding", icon: "fa-sign-out" },
		],
	},
	{
		group: "requests", label: "Requests & Approvals", icon: "fa-inbox",
		children: [
			{ key: "hrinbox", label: "Approval Inbox", icon: "fa-inbox" },
			{ key: "leaves", label: "Leave Requests", icon: "fa-plane" },
			{ key: "occurrences", label: "Attendance Justifications", icon: "fa-exclamation-triangle" },
			{ key: "advances", label: "Salary Advances", icon: "fa-money" },
			{ key: "salarychanges", label: "Salary Changes", icon: "fa-line-chart" },
			{ key: "bankchanges", label: "Bank Change Requests", icon: "fa-university" },
		],
	},
	{
		group: "time", label: "Time & Attendance", icon: "fa-calendar-check-o",
		children: [
			{ key: "attendance", label: "Attendance", icon: "fa-calendar-check-o" },
			{ key: "allocations", label: "Leave Allocations", icon: "fa-calendar-plus-o" },
			{ key: "balances", label: "Leave Balances", icon: "fa-balance-scale" },
			{ key: "leavetypes", label: "Leave Types", icon: "fa-tags" },
			{ key: "timesheets", label: "Timesheets", icon: "fa-list-alt" },
		],
	},
	{
		group: "talent", label: "Talent", icon: "fa-star-o",
		children: [
			{ key: "recruitment", label: "Recruitment", icon: "fa-user-plus" },
			{ key: "performance", label: "Performance", icon: "fa-star-o" },
		],
	},
	{
		group: "payrollgrp", label: "Payroll", icon: "fa-cogs",
		children: [
			{ key: "payroll", label: "Payroll", icon: "fa-cogs" },
			{ key: "slips", label: "Salary Slips", icon: "fa-file-text-o" },
			{ key: "settlements", label: "Final Settlement", icon: "fa-handshake-o" },
		],
	},
	{
		group: "insights", label: "Insights", icon: "fa-sitemap",
		children: [
			{ key: "hrdash", label: "HR Dashboard", icon: "fa-tachometer" },
			{ key: "orgchart", label: "Org Chart", icon: "fa-sitemap" },
			{ key: "analytics", label: "Workforce Insights", icon: "fa-area-chart" },
			{ key: "statutory", label: "IRT / INSS Declarations", icon: "fa-institution" },
		],
	},
	{
		group: "settings", label: "Settings", icon: "fa-sliders",
		children: [
			{ key: "settings", label: "General", icon: "fa-cog" },
			{ key: "holidays", label: "Holiday Lists", icon: "fa-calendar-o" },
			{ key: "shifts", label: "Shift Types", icon: "fa-clock-o" },
			{ key: "reasons", label: "Absence Reasons", icon: "fa-list-ul" },
			{ key: "irt", label: "IRT Table", icon: "fa-percent" },
		],
	},
];

/* ---------------------------------------------------------------------------
 * LAYOUT MODE
 *
 * Two kinds of screen live in this console and they must not scroll the same way.
 *
 *   DATA       one primary dataset. The screen is bounded to the viewport, the
 *              title/filters/actions stay put and only the rows move. Right for
 *              Employees, Bulk Contracts, Salary Slips.
 *
 *   DASHBOARD  several independent sections stacked vertically. The page scrolls
 *              normally, top to bottom, and no single section may take the
 *              viewport hostage. Right for Insights, HR Dashboard, Payroll.
 *
 *   CANVAS     owns its own viewport and pans inside it (the org chart).
 *
 * The mode is DECLARED here, per screen. It is never inferred from the presence
 * of a <table>: that inference is exactly what broke Insights — a twelve-month
 * headcount table looked like an operational dataset, the whole screen was
 * bounded to fit it, and Absenteeism and Leave Usage below were squeezed into a
 * 38px sliver with no way to scroll to them.
 *
 * Anything not listed defaults to DASHBOARD, i.e. ordinary page scrolling. A
 * missing entry then costs a screen its pinned toolbar, which is a small loss;
 * the opposite default costs it access to its own content.
 * ------------------------------------------------------------------------- */
const LAYOUT_DATA = "data";
const LAYOUT_DASHBOARD = "dashboard";
const LAYOUT_CANVAS = "canvas";
/* BOARD — a fixed set of tiles that is meant to be taken in at a glance, so it is
 * sized to the viewport and does not scroll. Only for a screen whose content is a
 * known, bounded set of panels (the Overview: four figures and three charts). It is
 * NOT a general dashboard mode: a screen whose sections grow with the data must
 * scroll, or the sections lose their height as the data arrives. */
const LAYOUT_BOARD = "board";

/* Every mode there is. applyLayout clears all of them before setting one, so adding
 * a mode here is all it takes for the switch to keep working. */
const ALL_LAYOUTS = [LAYOUT_DATA, LAYOUT_DASHBOARD, LAYOUT_CANVAS, LAYOUT_BOARD];

const SCREEN_LAYOUT = {
	// --- one primary dataset -------------------------------------------------
	employees: LAYOUT_DATA,
	bulkcontracts: LAYOUT_DATA,
	contracts: LAYOUT_DATA,
	profiles: LAYOUT_DATA,
	documents: LAYOUT_DATA,
	hrinbox: LAYOUT_DATA,
	leaves: LAYOUT_DATA,
	occurrences: LAYOUT_DATA,
	advances: LAYOUT_DATA,
	salarychanges: LAYOUT_DATA,
	bankchanges: LAYOUT_DATA,
	attendance: LAYOUT_DATA,
	allocations: LAYOUT_DATA,
	balances: LAYOUT_DATA,
	leavetypes: LAYOUT_DATA,
	timesheets: LAYOUT_DATA,
	slips: LAYOUT_DATA,
	settlements: LAYOUT_DATA,
	holidays: LAYOUT_DATA,
	shifts: LAYOUT_DATA,
	reasons: LAYOUT_DATA,
	irt: LAYOUT_DATA,

	// --- fits the screen, at a glance ----------------------------------------
	overview: LAYOUT_BOARD,

	// --- several sections, or a form: the page scrolls ------------------------
	hrdash: LAYOUT_DASHBOARD,
	analytics: LAYOUT_DASHBOARD,
	payroll: LAYOUT_DASHBOARD,
	statutory: LAYOUT_DASHBOARD,
	performance: LAYOUT_DASHBOARD,
	// Recruitment is a pipeline overview — counters, accepted offers, open
	// positions, applicants and a legend — not one list.
	recruitment: LAYOUT_DASHBOARD,
	offboarding: LAYOUT_DASHBOARD,
	settings: LAYOUT_DASHBOARD,

	orgchart: LAYOUT_CANVAS,
};

/* A table earns internal scrolling by being long, not by being a table (§21).
 * DATA_MIN_ROWS: below this a list keeps its ordinary flow, so a short list does
 * not sit in a tall empty box. DASH_MIN_ROWS is higher because inside a dashboard
 * a scrollbar interrupts a page the reader is already scrolling — the 12-month
 * headcount table must show all twelve rows. */
const DATA_MIN_ROWS = 12;
const DASH_MIN_ROWS = 25;

/* The quick-action bar (§29). One click from anywhere to every routine HR job, so that
 * starting a piece of work never requires knowing which screen owns it first. */
const QUICK_ACTIONS = [
	{ label: "New Employee", icon: "fa-user-plus", fn: "newEmployee" },
	{ label: "New Contract", icon: "fa-file-text", fn: "newContractDialog" },
	{ label: "New Leave Request", icon: "fa-plane", fn: "newLeave" },
	{ label: "Attendance Justification", icon: "fa-exclamation-triangle", fn: "newOccurrence" },
	{ label: "New Salary Advance", icon: "fa-money", fn: "newAdvanceDialog" },
	{ label: "New Salary Change", icon: "fa-line-chart", fn: "newSalaryChangeDialog" },
	{ label: "New Bank Change", icon: "fa-university", fn: "newBankChangeDialog" },
	{ label: "Add Employee Document", icon: "fa-folder-open-o", fn: "newDocumentDialog" },
	{ label: "New Job Opening", icon: "fa-bullhorn", fn: "newJobOpeningDialog" },
	{ label: "New Performance Cycle", icon: "fa-star-o", fn: "startPerformanceCycle" },
];

// Find a nav entry (top-level or nested child) by view key.
function findNav(key) {
	for (const n of NAV) {
		if (n.key === key) return n;
		if (n.children) {
			const c = n.children.find((x) => x.key === key);
			if (c) return c;
		}
	}
	return null;
}

class AngolaHR {
	constructor(page) {
		this.page = page;
		// Bumped by every render; see call(). Starts at 0 so the first fetch is current.
		this._gen = 0;
		this.state = { company: null, companies: [], currency: "AOA", view: "overview" };
		this.build();
		this.boot();
	}

	/* Every screen fetches through here, and every fetch is async, so a slow response
	 * belonging to the screen you just left would otherwise arrive AFTER the next screen
	 * has replaced the content and write itself into it. That produced intermittent
	 * `removeChild` errors during rapid navigation — jQuery removing nodes that the newer
	 * render had already replaced — and, worse, could paint one screen's rows into
	 * another's table.
	 *
	 * Each render bumps a generation counter. A response that comes back under a stale
	 * generation is dropped: its promise simply never settles, so the view code that was
	 * about to touch the DOM never runs. Nothing is retried and nothing is logged, because
	 * the user has already asked for something else. */
	/* A user-initiated ACTION, as opposed to a screen render.
	 *
	 * call() drops a response whose screen has been replaced, which is right for the
	 * fetch that paints a screen and WRONG for a button the user just pressed: pressing
	 * Preview is not a stale page render, and silently discarding its response is
	 * indistinguishable from the button being dead. This variant always settles, so the
	 * caller's .catch() and its finally-equivalent always run and every failure is
	 * visible. Callers write into their own container, which simply no longer exists if
	 * the user has navigated away — harmless.
	 */
	action(method, args = {}) {
		const base = HR_METHODS.has(method) ? HR_API : API;
		return new Promise((resolve, reject) => {
			frappe.call({ method: base + method, args }).then(
				(r) => resolve(r ? r.message : undefined),
				(err) => reject(err)
			);
		});
	}

	call(method, args = {}) {
		const base = HR_METHODS.has(method) ? HR_API : API;
		const gen = this._gen;
		// The native Promise must WRAP frappe.call, not be returned from inside its
		// .then(). Frappe v13 ships jQuery 2.2.4, whose .then() is pipe-like and does not
		// assimilate a returned thenable — the caller would receive the Promise object
		// itself instead of the data, and every view would silently render its empty
		// state. Wrapping from the outside keeps the result a real Promise, so .then()
		// and Promise.all() both behave.
		return new Promise((resolve, reject) => {
			frappe.call({ method: base + method, args }).then(
				(r) => {
					if (gen === this._gen) resolve(r.message);
				},
				(err) => {
					if (gen === this._gen) reject(err);
				}
			);
		});
	}
	money(v) {
		return format_currency(flt(v), this.state.currency);
	}
	d(v) {
		return v ? frappe.datetime.str_to_user(v) : "";
	}

	build() {
		const shell = $(`
			<div class="ahr-shell">
				<div class="ahr-bar">
					<div class="ahr-brand">
						<span class="ahr-brand-logo"><i class="fa fa-users"></i></span>
						<span class="ahr-brand-meta">
							<span class="ahr-brand-name">Angola HR</span>
							<span class="ahr-brand-tag">${__("HR & Payroll")}</span>
						</span>
					</div>
					<div class="ahr-tabs"></div>
					<div class="ahr-bar-tools">
						<div class="ahr-quick-wrap">
							<button class="btn btn-primary ahr-quick-btn"><i class="fa fa-bolt"></i> <span>${__("New")}</span> <i class="fa fa-caret-down"></i></button>
							<div class="ahr-quick-menu"></div>
						</div>
						<select class="ahr-company form-control"></select>
						<button class="btn btn-default ahr-back" title="${__("Back to ERP")}"><i class="fa fa-arrow-left"></i></button>
						<button class="btn btn-default ahr-fs" title="${__("Fullscreen")}"><i class="fa fa-arrows-alt"></i></button>
						<button class="btn btn-default ahr-refresh" title="${__("Refresh")}"><i class="fa fa-refresh"></i></button>
					</div>
				</div>
				<div class="ahr-pagebar"></div>
				<div class="ahr-content"></div>
			</div>`).appendTo(this.page.body);

		this.$shell = shell;
		this.$tabs = shell.find(".ahr-tabs");
		this.$pagebar = shell.find(".ahr-pagebar");
		this.$content = shell.find(".ahr-content");
		this.$company = shell.find(".ahr-company");

		this.renderNav();
		this.renderQuickActions();
		this.watchTables();
		shell.find(".ahr-refresh").on("click", () => this.render());
		shell.find(".ahr-fs").on("click", () => this.toggleFullscreen());
		// Leave the HR console and return to the ERP desk home. Restore the global ERP chrome
		// explicitly first — with path-based routing the route change won't always fire the
		// navbar handler in time, so the desk would otherwise render without its navbar.
		shell.find(".ahr-back").on("click", () => {
			if (document.fullscreenElement || document.webkitFullscreenElement) this.toggleFullscreen();
			$("body").removeClass("ahr-fullscreen");
			$("header.navbar, .navbar.navbar-default.navbar-fixed-top, .navbar-expand-lg").show();
			$(".layout-main-section-wrapper").css("margin-top", "");
			$(".page-container").css("padding-top", "");
			frappe.set_route("");
		});
		this.$company.on("change", () => {
			this.state.company = this.$company.val();
			this.render();
		});

		// Keep the fullscreen button icon + maximized state in sync with the browser.
		$(document).on("fullscreenchange.ahr webkitfullscreenchange.ahr", () => {
			const active = !!(document.fullscreenElement || document.webkitFullscreenElement);
			shell.toggleClass("ahr-maximized", active);
			shell.find(".ahr-fs i").toggleClass("fa-arrows-alt", !active).toggleClass("fa-compress", active);
			setTimeout(() => window.dispatchEvent(new Event("resize")), 80);
		});
		// Close any open tab dropdown when clicking elsewhere.
		$(document).on("click.ahrdd", () => this.$tabs.find(".ahr-tab-wrap.open").removeClass("open"));
	}

	renderNav() {
		this.$tabs.empty();
		NAV.forEach((n) => {
			if (n.children) {
				const $wrap = $(`<div class="ahr-tab-wrap" data-group="${n.group}"></div>`);
				const $tab = $(`<button class="ahr-tab ahr-tab-dd"><i class="fa ${n.icon}"></i> <span>${__(n.label)}</span> <i class="fa fa-caret-down ahr-caret"></i></button>`)
					.on("click", (e) => { e.stopPropagation(); this.$tabs.find(".ahr-tab-wrap.open").not($wrap).removeClass("open"); $wrap.toggleClass("open"); });
				const $menu = $(`<div class="ahr-dd-menu"></div>`);
				n.children.forEach((c) => {
					$(`<div class="ahr-dd-item" data-key="${c.key}"><i class="fa ${c.icon}"></i> <span>${__(c.label)}</span></div>`)
						.appendTo($menu)
						.on("click", (e) => { e.stopPropagation(); $wrap.removeClass("open"); this.go(c.key); });
				});
				$wrap.append($tab, $menu).appendTo(this.$tabs);
			} else {
				$(`<button class="ahr-tab" data-key="${n.key}"><i class="fa ${n.icon}"></i> <span>${__(n.label)}</span></button>`)
					.appendTo(this.$tabs)
					.on("click", () => this.go(n.key));
			}
		});
	}

	/* §29 — every routine HR job is one click from anywhere.
	 *
	 * Each entry calls the SAME dialog the owning screen calls; nothing is duplicated,
	 * so a change to how an advance is recorded changes it in both places at once. An
	 * entry whose method is missing is dropped rather than rendered as a dead button. */
	renderQuickActions() {
		const $wrap = this.$shell.find(".ahr-quick-wrap");
		const $menu = $wrap.find(".ahr-quick-menu");
		QUICK_ACTIONS.forEach((a) => {
			if (typeof this[a.fn] !== "function") return;
			$(`<div class="ahr-quick-item"><i class="fa ${a.icon}"></i> <span>${__(a.label)}</span></div>`)
				.appendTo($menu)
				.on("click", (e) => {
					e.stopPropagation();
					$wrap.removeClass("open");
					this[a.fn]();
				});
		});
		$wrap.find(".ahr-quick-btn").on("click", (e) => {
			e.stopPropagation();
			this.$tabs.find(".ahr-tab-wrap.open").removeClass("open");
			$wrap.toggleClass("open");
		});
		$(document).on("click.ahrquick", () => $wrap.removeClass("open"));
	}

	/* ---- PART 1: the screen stays put, the rows scroll ---------------------
	 *
	 * Applied CENTRALLY rather than screen by screen. Roughly thirty views build
	 * their own markup; patching each would have meant thirty chances to get it
	 * wrong and thirty places to keep in step. Instead one pass runs after any
	 * render and promotes the biggest table on the screen into a scroll region.
	 *
	 * A MutationObserver, not a scroll listener (§10). It fires when a view
	 * replaces the content — a handful of times a minute — and the scrolling
	 * itself is then pure CSS, so rows cost nothing to scroll.
	 */
	watchTables() {
		// The shell needs a real height for the flex chain to resolve, and that
		// height depends on where Frappe's chrome ends. Measured, never guessed,
		// so 768px and 1080px viewports each use what they actually have (§3).
		// How much vertical space Frappe's own chrome has already taken. Read from the
		// document rather than assumed, so the navbar being hidden (fullscreen), taller
		// or absent all work without a second code path.
		const measure = (bounded) => {
			const el = this.$shell[0];
			// Frappe puts a 60px bottom margin on its page wrapper for the footer this
			// page does not have. Left in place it made the document 60px taller than the
			// viewport, so the window kept a scrollbar however exactly the shell was
			// sized. Cleared on THIS page's wrapper only — and only while a screen is
			// claiming the whole viewport. A dashboard scrolls the page anyway, so it
			// keeps Frappe's own spacing rather than quietly restyling the desk.
			const wrapper = el.closest && el.closest(".layout-main-section-wrapper");
			if (wrapper) wrapper.style.marginBottom = bounded === false ? "" : "0px";
			const top = el.getBoundingClientRect().top + (window.scrollY || 0);
			el.style.setProperty("--ahr-shell-top", Math.max(0, Math.round(top)) + "px");
		};
		this._measure = measure;
		// Frappe finishes laying out its own chrome after this constructor returns, so
		// the first reading can be taken before the navbar exists. Re-read once the
		// browser has painted, and again shortly after, rather than trusting one shot.
		// Routed through applyLayout so the reading is always taken for the mode the
		// current screen is actually in.
		this.applyLayout();
		window.requestAnimationFrame(() => this.applyLayout());
		setTimeout(() => this.applyLayout(), 300);

		let queued = false;
		const schedule = () => {
			// applyLayout moves a node, which is itself a mutation. Without this guard
			// the observer would re-enter on its own work.
			if (queued || this._fitting) return;
			queued = true;
			window.requestAnimationFrame(() => { queued = false; this.applyLayout(); });
		};
		this._fitSoon = schedule;

		new MutationObserver(schedule).observe(this.$content[0],
			{ childList: true, subtree: true });

		/* Dialogs render outside .ahr-content, so the observer above never sees them
		 * — and the tables inside them had the same columns disagreeing with their
		 * headers. Bootstrap announces a dialog opening, and a dialog's table is
		 * often filled after it opens (the content arrives from a request), so the
		 * open modal is watched until it closes rather than checked once. Bound at
		 * the document, once, and torn down on hide. */
		$(document).on("shown.bs.modal.ahrcols", (e) => {
			const el = e.target;
			this.alignNumericColumns($(el));
			if (this._modalObs) this._modalObs.disconnect();
			this._modalObs = new MutationObserver(() => this.alignNumericColumns($(el)));
			this._modalObs.observe(el, { childList: true, subtree: true });
		});
		$(document).on("hidden.bs.modal.ahrcols", () => {
			if (this._modalObs) { this._modalObs.disconnect(); this._modalObs = null; }
		});

		let resizeTimer = null;
		$(window).on("resize.ahrfit", () => {
			// Tear the charts down NOW, not in 120ms. frappe-charts keeps its own
			// ResizeObserver on each container; left connected it redraws during the
			// debounce window, into a container redrawBoard is about to empty, and
			// throws "removeChild: the node to be removed is not a child of this
			// node" from inside desk.min.js. Disconnecting on the first resize event
			// closes that window. Idempotent, so the repeat events cost nothing.
			if (SCREEN_LAYOUT[this.state.view] === LAYOUT_BOARD) this.destroyCharts();
			clearTimeout(resizeTimer);
			resizeTimer = setTimeout(() => {
				this.applyLayout();
				// A board is sized to the window, and a chart's SVG is drawn at a fixed
				// pixel height, so it has to be drawn again when the window changes.
				// Only on the resize path — redrawing on every DOM mutation would make
				// the chart's own drawing trigger the next redraw.
				this.redrawBoard();
			}, 120);
		});
	}

	/* Draw the board's charts again at the size the layout has just given them.
	 * Uses the payload the screen already received, so resizing costs no request. */
	redrawBoard() {
		if (SCREEN_LAYOUT[this.state.view] !== LAYOUT_BOARD) return;
		if (!this._overviewData || !this.$content.find(".ahr-chart").length) return;
		this.destroyCharts();
		this.$content.find(".ahr-chart").empty();
		// Through renderOverviewCharts, not straight to the draw. It waits a frame
		// first, and that wait is the whole point: frappe-charts attaches its
		// ResizeObserver as it constructs, and building a chart in the same task as
		// a layout change makes that observer fire mid-draw. Calling the draw
		// directly here is what brought the intermittent removeChild back.
		this.renderOverviewCharts(this._overviewData);
	}

	/* The one place that decides how the current screen scrolls.
	 *
	 * It reads the declared mode for the screen and does nothing clever: a data
	 * screen is bounded and its main list scrolls inside itself; a dashboard is
	 * left in ordinary document flow and only a genuinely long table inside it
	 * gets its own bounded box. */
	applyLayout() {
		const $content = this.$content;
		if (!$content || !$content.length || !this.$shell || !this.$shell.length) return;

		const mode = SCREEN_LAYOUT[this.state.view] || LAYOUT_DASHBOARD;
		const bounded = mode !== LAYOUT_DASHBOARD;
		// Derived from the modes themselves, never a hand-written list. A hand-written
		// one had already gone stale once: a mode was added without being added to the
		// removal list, so the shell kept it on every screen visited afterwards.
		this.$shell
			.removeClass(ALL_LAYOUTS.map((m) => "ahr-" + m + "-screen").join(" "))
			.addClass("ahr-" + mode + "-screen");

		// Re-measure here rather than only at construction: on_page_load runs while the
		// page is still hidden, so the first reading of the shell's top edge is 0 and the
		// shell ends up one navbar too tall — which is why the window still scrolled by
		// exactly the height of Frappe's navbar.
		// A view that built its own page heading has said something more useful
		// than a label, so the central one gets out of its way.
		if (this.$pagebar && this.$pagebar.length) {
			this.$pagebar.toggleClass("hidden", $content.find(".ahr-pagehead").length > 0);
		}

		if (this._measure) this._measure(bounded);
		this.alignNumericColumns($content);

		if (mode === LAYOUT_DATA) {
			this.fitDataTable();
		} else {
			$content.removeClass("ahr-fit");
			if (mode === LAYOUT_DASHBOARD) this.boundLongTables();
		}
	}

	/* Make every column agree with itself about whether it holds numbers.
	 *
	 * `.num` right-aligns a cell. Most tables are built by table(), which puts the
	 * marker on the header AND the cells; the hand-written ones put it on only one
	 * of the two, and the column then reads as though the label belongs to a
	 * different column from the figures — a "Gross" label at the far left of a
	 * column whose amounts sit at the far right.
	 *
	 * Both directions occur, so neither side can simply follow the other: the
	 * salary-slip tables mark the cells and not the header, and the IRT table marks
	 * the header and not the cells. The marker means "this column is numeric", so
	 * it is applied to the whole column when either end declares it.
	 *
	 * Central and idempotent: it fixes the tables that exist today and any written
	 * later, and adding a class that is already present mutates nothing, so it
	 * cannot re-trigger the observer that calls it.
	 */
	alignNumericColumns($root) {
		if (!$root || !$root.length) return;
		$root.find("table.ahr-table").each((_, table) => {
			const headRows = table.tHead ? table.tHead.rows : [];
			// One header row only. Anything with stacked headers or a colspan is left
			// alone rather than guessed at — a wrong guess is worse than the status quo.
			if (headRows.length !== 1) return;
			const ths = [...headRows[0].cells];
			if (ths.some((th) => th.colSpan > 1)) return;
			const body = table.tBodies[0];
			if (!body || !body.rows.length) return;
			const rows = [...body.rows].filter(
				(r) => r.cells.length === ths.length && ![...r.cells].some((c) => c.colSpan > 1));
			if (!rows.length) return;

			ths.forEach((th, i) => {
				const cells = rows.map((r) => r.cells[i]);
				const numeric = th.classList.contains("num")
					|| cells.some((c) => c.classList.contains("num"));
				if (!numeric) return;
				th.classList.add("num");
				cells.forEach((c) => c.classList.add("num"));
			});
		});
	}

	/* True while the board is actually being sized to the window. Below the mobile
	 * breakpoint the board reverts to ordinary page scrolling — four figures, three
	 * charts and a holiday list squeezed into a phone screen would be unreadable,
	 * and a chart nobody can read is worse than one they have to scroll to. */
	boardIsFitted() {
		return window.innerWidth > 900;
	}

	/* Dashboard mode: leave the page alone, with one exception.
	 *
	 * A section holding hundreds of rows would push everything below it off the
	 * bottom of a page nobody wants to scroll that far, so a long table gets a
	 * bounded box of its own. The bound is a fraction of the viewport, NOT the
	 * remaining height — the section must never grow to fill the screen, because
	 * the sections after it have equal claim on the page (§6). Short tables are
	 * left completely alone: twelve months of headcount shows twelve rows. */
	boundLongTables() {
		this._fitting = true;
		try {
			this.$content.find("table.ahr-table").each((_, el) => {
				const $t = $(el);
				if ($t.closest(".modal, .ahr-no-fit").length) return;
				const long = $t.find("tbody > tr").length >= DASH_MIN_ROWS;
				const $wrap = $t.parent(".ahr-tablewrap");
				if (!long) return;                       // natural flow, no scroll box
				if ($wrap.length) { $wrap.addClass("ahr-dashboard-table"); return; }
				const table = el;
				const holder = document.createElement("div");
				holder.className = "ahr-tablewrap ahr-dashboard-table";
				table.parentNode.insertBefore(holder, table);
				holder.appendChild(table);
			});
		} finally {
			this._fitting = false;
		}
	}

	/* Data mode: promote this screen's main list into a scroll region.
	 *
	 * Only ever called for a screen declared as LAYOUT_DATA, so the question here
	 * is no longer "is this a data screen" — it is "does this screen's list have
	 * enough rows to be worth pinning the toolbar for". Below the threshold the
	 * table keeps its ordinary flow, so a three-row list does not sit in a tall
	 * empty box (§13). Dialogs are untouched because nothing outside .ahr-content
	 * is ever inspected (§14). */
	fitDataTable() {
		const $content = this.$content;
		if (!$content || !$content.length) return;
		if ($content.find(".ahr-no-fit").length) {           // a canvas inside a list screen
			$content.removeClass("ahr-fit");
			return;
		}

		// The main list is the biggest table on the screen; any small tables above
		// or below it are context and keep flowing normally.
		let best = null, bestRows = 0;
		$content.find("table.ahr-table").each((_, el) => {
			const $t = $(el);
			if ($t.closest(".modal, .ahr-no-fit").length) return;
			const rows = $t.find("tbody > tr").length;
			if (rows > bestRows) { bestRows = rows; best = $t; }
		});

		if (!best || bestRows < DATA_MIN_ROWS) {
			$content.removeClass("ahr-fit");
			$content.find(".ahr-fit-panel").removeClass("ahr-fit-panel");
			return;
		}

		// Wrap once. Re-running must not nest wrappers inside wrappers.
		//
		// Moved with native DOM calls rather than jQuery. jQuery's insertion path runs
		// its own detach bookkeeping, and doing that to a node a view is about to
		// replace produced an intermittent "removeChild: the node to be removed is not a
		// child of this node" during rapid navigation. insertBefore/appendChild simply
		// relocate the element and leave the view's own handles alone.
		let $wrap = best.parent(".ahr-tablewrap");
		if (!$wrap.length) {
			this._fitting = true;
			try {
				const table = best[0];
				const holder = document.createElement("div");
				holder.className = "ahr-tablewrap";
				table.parentNode.insertBefore(holder, table);
				holder.appendChild(table);
				$wrap = $(holder);
			} finally {
				this._fitting = false;
			}
		}

		const $panel = $wrap.closest(".ahr-panel");
		const $stop = $panel.length ? $panel : $content;

		// Clear any previous screen's marks before laying this one out.
		$content.find(".ahr-fit-panel").not($panel).removeClass("ahr-fit-panel");
		$content.find(".ahr-fit-chain").removeClass("ahr-fit-chain");
		$content.find(".ahr-tablewrap").removeClass("ahr-fit-scroll");

		$content.addClass("ahr-fit");
		if ($panel.length) $panel.addClass("ahr-fit-panel");
		$wrap.addClass("ahr-fit-scroll");

		// Height only propagates through elements that are themselves flex children.
		// The table is often nested a level or two below the panel (a view's own list
		// container), and every unmarked ancestor in between would stop the chain and
		// let the table grow to its natural height again.
		let $node = $wrap.parent();
		while ($node.length && !$node.is($stop) && !$node.is($content)) {
			$node.addClass("ahr-fit-chain");
			$node = $node.parent();
		}
	}

	toggleFullscreen() {
		const el = document.documentElement;
		const isFs = document.fullscreenElement || document.webkitFullscreenElement;
		if (!isFs) {
			const req = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
			if (req) req.call(el);
		} else {
			const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
			if (exit) exit.call(document);
		}
	}

	boot() {
		// Deliberately NOT this.call(): the overview payload is global state (companies,
		// currency, default period), not screen content, so it must arrive even if the
		// user has already navigated away from Overview.
		const gen = this._gen;
		frappe.call({ method: API + "get_overview" }).then((r) => {
			const o = r.message || {};
			this.state.companies = o.companies || [];
			this.state.company = o.company;
			this.state.currency = o.currency || "AOA";
			this.state.default_period = o.default_period || null;
			this.$company.empty();
			this.$company.append(`<option value="">${__("All Companies")}</option>`);
			this.state.companies.forEach((c) =>
				this.$company.append(`<option value="${frappe.utils.escape_html(c)}">${frappe.utils.escape_html(c)}</option>`)
			);
			if (this.state.company) this.$company.val(this.state.company);
			// Only draw the opening screen if the user has not already asked for another
			// one. Boot used to render unconditionally, so a click made while the overview
			// was still loading was silently thrown away and the user landed back on
			// Overview — or, once stale responses were being dropped, on a blank screen.
			if (gen === this._gen) this.render(o);
		});
	}

	go(key) {
		this.state.view = key;
		this.render();
	}

	/* LEVEL 1 — the page.
	 *
	 * Rendered centrally, once per navigation, so all thirty-odd screens carry
	 * the same heading without any of them having to build one. The nav tab
	 * highlights the GROUP; this names the screen inside it, which is the part
	 * the tab cannot show — "Employees ›" then "Contracts".
	 *
	 * A view that renders its own .ahr-pagehead has said something better than a
	 * label, so this steps aside for it rather than printing a second title
	 * (§3: do not repeat the navigation title unnecessarily). */
	renderPageBar(grp) {
		if (!this.$pagebar || !this.$pagebar.length) return;
		let label = null, icon = null;
		for (const n of NAV) {
			if (n.key === this.state.view) { label = n.label; icon = n.icon; break; }
			const kid = (n.children || []).find((c) => c.key === this.state.view);
			if (kid) { label = kid.label; icon = kid.icon; break; }
		}
		if (!label) { this.$pagebar.empty().addClass("hidden"); return; }
		this.$pagebar.removeClass("hidden").html(
			`<div class="ahr-pagebar-in">
				${grp ? `<span class="ahr-crumb">${__(grp.label)}</span>
					<i class="fa fa-angle-right ahr-crumb-sep" aria-hidden="true"></i>` : ""}
				<h2 class="ahr-pagebar-title">
					${icon ? `<i class="fa ${icon}" aria-hidden="true"></i>` : ""}${__(label)}</h2>
			</div>`);
	}

	render(preOverview) {
		// Anything still in flight for the previous screen is now stale — see call().
		this._gen += 1;
		// Tear the previous screen's charts down BEFORE its container is replaced.
		this.destroyCharts();
		this.$tabs.find(".ahr-tab, .ahr-dd-item").removeClass("active");
		this.$tabs.find(`[data-key="${this.state.view}"]`).addClass("active");
		// Highlight the dropdown tab that owns the active view (Settings group).
		const grp = NAV.find((n) => n.children && n.children.some((c) => c.key === this.state.view));
		if (grp) this.$tabs.find(`.ahr-tab-wrap[data-group="${grp.group}"] .ahr-tab-dd`).addClass("active");
		this.renderPageBar(grp);
		this.$content.html(`<div class="ahr-empty"><i class="fa fa-spinner fa-spin"></i> ${__("Loading")}…</div>`);
		const fn = this["view_" + this.state.view];
		if (fn) fn.call(this, preOverview);
	}

	// ---- helpers ----
	table(cols, rows, opts = {}) {
		// "No records" tells the reader nothing: not what would appear here, not
		// what puts it there (§18). Callers that know better pass `empty`; the
		// default at least names the list and says the filter may be the reason.
		if (!rows || !rows.length) {
			const e = opts.empty || {};
			return `<div class="ahr-empty-state compact">
				<i class="fa ${e.icon || "fa-inbox"}" aria-hidden="true"></i>
				<h4>${e.title || __("Nothing to show here yet")}</h4>
				<p>${e.body || __("No record matches this view. If you have filters set, widen them — otherwise records will appear here as HR creates them.")}</p>
			</div>`;
		}
		const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${__(c.label)}</th>`).join("");
		const body = rows
			.map((r) => {
				const tds = cols
					.map((c) => {
						let v = c.render ? c.render(r[c.key], r) : r[c.key];
						if (c.money) v = this.money(r[c.key]);
						if (c.date) v = this.d(r[c.key]);
						return `<td class="${c.num || c.money ? "num" : ""}">${v != null ? v : ""}</td>`;
					})
					.join("");
				const id = opts.id ? `data-id="${frappe.utils.escape_html(r[opts.id])}"` : "";
				return `<tr class="${opts.id ? "clickable" : ""}" ${id}>${tds}</tr>`;
			})
			.join("");
		return `<table class="ahr-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
	}
	statusBadge(docstatus) {
		return docstatus === 1
			? `<span class="ahr-badge submitted">${__("Submitted")}</span>`
			: `<span class="ahr-badge draft">${__("Draft")}</span>`;
	}
	// Salary-slip lifecycle badge: Draft → Submitted → Posted → Paid; Cancelled.
	// "Posted" means the accrual Journal Entry is submitted and in the ledger; "Paid"
	// means the payment entry is submitted. A draft entry is neither.
	slipStatus(status) {
		const cls = { Draft: "draft", Submitted: "submitted", Posted: "accrued", Paid: "paid", Cancelled: "cancelled" };
		const s = status || "Draft";
		return `<span class="ahr-badge ${cls[s] || "draft"}">${__(s)}</span>`;
	}
	/* A SECTION — the main unit of the interface.
	 *
	 * Every screen is built from these, so the header is where hierarchy is
	 * bought: an icon to identify it at a glance, a title, one line saying what
	 * the section is for, its actions on the right and an optional status tag.
	 *
	 * The signature is still panel(title, inner), which is how all sixty
	 * existing call sites use it — they gain the new header without being
	 * touched, and the ones that benefit from an icon or a subtitle pass one.
	 * The class stays `.ahr-panel` because the table-scroll layout and several
	 * views select on it (§47); `.ahr-section` is the name to use in new
	 * markup. They are the same component.
	 *
	 * opts: {icon, subtitle, actions, tag: {label, kind}, cls, id}
	 */
	panel(title, inner, opts = {}) {
		// The first argument may be the title, or an object carrying the title and
		// its meta: panel({title, icon, subtitle, actions, tag}, inner). The object
		// form exists so a section spanning many lines can be given an icon and a
		// subtitle by editing only its first line.
		if (title && typeof title === "object") {
			opts = Object.assign({}, title, opts);
			title = opts.title;
		}
		// Several screens build their title as `Salary Changes <button …>`, which put
		// the action into the heading and left it floating in the middle of the
		// content (§10). Anything from the first tag onwards is markup, not a title,
		// so it is moved into the actions slot on the right of the header — fixing
		// every such call site at once rather than six at a time.
		let actionHtml = opts.actions || "";
		const cut = String(title).indexOf("<");
		if (cut > -1) {
			actionHtml = String(title).slice(cut) + actionHtml;
			title = String(title).slice(0, cut).trim();
		}
		const ico = opts.icon
			? `<div class="ahr-section-ico"><i class="fa ${opts.icon}" aria-hidden="true"></i></div>`
			: "";
		const tag = opts.tag && opts.tag.label
			? `<span class="ahr-section-tag ${opts.tag.kind || ""}">${opts.tag.label}</span>`
			: "";
		const actions = (actionHtml || tag)
			? `<div class="ahr-section-actions">${tag}${actionHtml}</div>`
			: "";
		return `<div class="ahr-panel ahr-section ${opts.cls || ""}"${opts.id ? ` id="${opts.id}"` : ""}>
			<div class="ahr-section-head ${ico ? "" : "no-ico"}">
				${ico}
				<div class="ahr-section-heading">
					<h5 class="ahr-section-title">${title}</h5>
					${opts.subtitle ? `<div class="ahr-section-sub">${opts.subtitle}</div>` : ""}
				</div>
				${actions}
			</div>
			${inner}
		</div>`;
	}

	/* A SUBSECTION — a division inside a section. Deliberately not another
	 * card: a rule and a label, so the eye reads it as part of the section
	 * above it rather than as a competing box (§7). */
	subsection(title, inner, opts = {}) {
		return `<div class="ahr-subsection ${opts.cls || ""}">
			<div class="ahr-subsection-head">
				<h6 class="ahr-subsection-title">${title}</h6>
				${opts.subtitle ? `<div class="ahr-subsection-sub">${opts.subtitle}</div>` : ""}
				${opts.meta ? `<div class="ahr-subsection-meta">${opts.meta}</div>` : ""}
			</div>
			${inner}
		</div>`;
	}

	/* A PAGE heading, with its actions. Used where the screen is worth naming
	 * above its sections rather than relying on the active tab alone. */
	pageHead(title, subtitle, actions) {
		const head = `<div class="ahr-pagehead"><h2>${title}</h2>
			${subtitle ? `<div class="ahr-pagehead-sub">${subtitle}</div>` : ""}</div>`;
		if (!actions) return head;
		return `<div class="ahr-pagehead-row">${head}
			<div class="ahr-pagehead-actions">${actions}</div></div>`;
	}

	/* A label over a run of sections that belong together (§50). */
	groupLabel(text) {
		return `<div class="ahr-group-label">${text}</div>`;
	}

	/* A METRIC ROW. The value is the point, so it is the largest, darkest thing
	 * in the card and the label is small and muted above it (§9).
	 * items: [{label, value, foot, icon, kind, cls}] */
	metrics(items) {
		return `<div class="ahr-metrics">${items.filter(Boolean).map((m) => `
			<div class="ahr-metric ${m.kind || ""} ${m.cls || ""}">
				<div class="ahr-metric-head">
					${m.icon ? `<i class="fa ${m.icon}" aria-hidden="true"></i>` : ""}
					<span class="ahr-metric-label">${m.label}</span>
				</div>
				<div class="ahr-metric-value">${m.value}</div>
				${m.foot ? `<div class="ahr-metric-foot">${m.foot}</div>` : ""}
			</div>`).join("")}</div>`;
	}

	/* An informational callout — an explanation, a definition, a limitation.
	 * Secondary by design (§16). Pass collapsed:true for long method notes,
	 * which then take one line until asked for (§17). Never use it to hide a
	 * warning somebody needs in order to trust a number. */
	callout(title, body, opts = {}) {
		const kind = opts.kind || "";
		const icon = opts.icon || "fa-info-circle";
		if (!opts.collapsed) {
			return `<div class="ahr-callout ${kind}">
				<b><i class="fa ${icon}" aria-hidden="true"></i> ${title}</b><br>${body}</div>`;
		}
		return `<details class="ahr-callout ${kind}">
			<summary><i class="fa fa-chevron-right" aria-hidden="true"></i>
				<span>${title}</span>
				<span class="ahr-callout-hint">${opts.hint || __("Show")}</span></summary>
			<div class="ahr-callout-body">${body}</div>
		</details>`;
	}

	/* Surface a failure. Never `.catch(() => {})`.
	 *
	 * Frappe raises its own dialog for a server exception, so re-raising the same text
	 * would double it; but a client-side TypeError produces NOTHING, which is how a
	 * button ends up looking dead. This shows whatever the failure actually was, and
	 * always leaves a trace in the console for a developer. */
	fail(title, err) {
		console.error(title, err);            // eslint-disable-line no-console
		const server = err && (err.responseJSON || err._server_messages || err.exc);
		if (server) return;                   // Frappe has already told the user
		const message = (err && (err.message || String(err))) || __("Unknown error.");
		frappe.msgprint({ title, indicator: "red", message: frappe.utils.escape_html(message) });
	}

	/* One line telling the user what a screen is for. The audit found that a screen
	 * which shows only a number and a table cannot be understood without the source. */
	what(text) {
		return `<div class="ahr-what"><i class="fa fa-info-circle" aria-hidden="true"></i>
			<span>${text}</span></div>`;
	}

	/* An empty state that answers: what is this, why is it empty, what creates the
	 * first record, who creates it, and what to click now. "No records" answers none
	 * of those. */
	blank(opts) {
		const actions = (opts.actions || [])
			.map((a) => `<button class="btn btn-sm ${a.primary ? "btn-primary" : "btn-default"} ${a.cls}">${a.label}</button>`)
			.join(" ");
		return `<div class="ahr-blank">
			<i class="fa ${opts.icon || "fa-inbox"}" aria-hidden="true"></i>
			<h4>${opts.title}</h4>
			<p>${opts.body}</p>
			${opts.who ? `<p class="who">${opts.who}</p>` : ""}
			${actions ? `<div class="ahr-blank-actions">${actions}</div>` : ""}
		</div>`;
	}

	// ============================ VIEWS ============================
	view_overview(pre) {
		const esc = frappe.utils.escape_html;
		const done = (o) => {
			// Kept so that resizing the window can redraw the charts at their new size
			// without asking the server for the same figures again.
			this._overviewData = o;
			const c = o.cards;
			const cards = this.metrics([
				{ label: __("Active Employees"), value: c.active_employees, icon: "fa-users",
					foot: __("On the payroll today") },
				{ label: __("Salary Profiles"), value: c.salary_profiles, icon: "fa-id-card-o",
					foot: __("Employees with a pay definition") },
				{ label: __("Submitted Slips"), value: c.submitted_slips, icon: "fa-file-text-o",
					foot: __("Approved this month") },
				{ label: __("Net Paid (month)"), value: this.money(c.net_paid_month),
					icon: "fa-money", foot: __("Total taken home") },
			]);

			const hol = o.upcoming_holidays || [];
			const holHtml = hol.length
				? `<ul class="ahr-holidays">${hol
						.map((h) => `<li>
							<span class="ahr-hol-day">${this.d(h.holiday_date)}</span>
							<span class="ahr-hol-desc">${esc(h.description || "")}</span>
							<span class="ahr-hol-in">${h.days_until === 0 ? __("Today") : __("in {0} days", [h.days_until])}</span>
						</li>`)
						.join("")}</ul>`
				: `<div class="ahr-empty">${o.default_holiday_list ? __("No upcoming holidays.") : __("Set a Default Holiday List in Settings → General.")}</div>`;

			this.$content.html(
				cards +
					`<div class="ahr-chart-grid">
						${this.panel(__("Net Pay Trend"), `<div class="ahr-chart" data-ch="trend"></div>`, { icon: "fa-line-chart", subtitle: __("Total net pay of each approved payroll run.") })}
						${this.panel(__("Salary Slips by Status"), `<div class="ahr-chart" data-ch="status"></div>`, { icon: "fa-pie-chart", subtitle: __("Where this month's slips are in the payroll cycle.") })}
					</div>` +
					`<div class="ahr-chart-grid">
						${this.panel(__("Headcount by Department"), `<div class="ahr-chart" data-ch="dept"></div>`, { icon: "fa-sitemap", subtitle: __("Active employees in each department.") })}
						${this.panel(__("Upcoming Holidays"), holHtml, { icon: "fa-calendar-o", subtitle: __("Public holidays ahead, from the default holiday list.") })}
					</div>`
			);
			this.renderOverviewCharts(o);
		};
		pre ? done(pre) : this.call("get_overview", { company: this.state.company }).then(done);
	}

	/* Charts outlive the screen that drew them unless they are told not to.
	 *
	 * frappe-charts keeps a ResizeObserver on each chart's container. Navigating away
	 * replaces the container, the observer fires anyway, and frappe-charts tries to
	 * remove a node from a parent that is no longer its parent — an intermittent
	 * "removeChild: the node to be removed is not a child of this node" thrown from
	 * inside desk.min.js, on whichever screen happened to be next. Every instance is
	 * now tracked and torn down before the content is replaced. */
	destroyCharts() {
		(this._charts || []).forEach((chart) => {
			if (!chart) return;
			// DISCONNECT FIRST, and in its own try. destroy() can itself throw while
			// unpicking a container that has already changed, and a shared try/catch
			// then swallowed the throw and skipped the disconnect — leaving exactly the
			// observer this method exists to remove.
			try {
				if (chart.resizeObserver && chart.resizeObserver.disconnect) {
					chart.resizeObserver.disconnect();
				}
			} catch (e) { /* already gone */ }
			try {
				if (typeof chart.destroy === "function") chart.destroy();
			} catch (e) { /* already gone */ }
		});
		this._charts = [];
	}

	renderOverviewCharts(o) {
		if (typeof frappe.Chart === "undefined") return;
		// Drawn one frame late, on purpose. frappe-charts attaches a ResizeObserver to the
		// container as it constructs, and that observer's first callback can land while
		// the initial draw is still in flight — the chart then tries to replace an SVG
		// node that is not where it left it. Letting the layout settle for a frame makes
		// the container's size final before the observer starts watching.
		window.requestAnimationFrame(() => {
			// Size the board before measuring it. The chart's height is read from its
			// container, so the flex layout has to have resolved first.
			this.applyLayout();
			this._drawOverviewCharts(o);
		});
	}

	_drawOverviewCharts(o) {
		if (typeof frappe.Chart === "undefined") return;
		this._charts = this._charts || [];
		const el = (k) => this.$content.find(`.ahr-chart[data-ch="${k}"]`)[0];
		const SC = { Draft: "#f59e0b", Submitted: "#3b82f6", Posted: "#6366f1", Paid: "#10b981", Cancelled: "#ef4444" };

		/* frappe-charts draws an SVG at a fixed pixel height — it does not fill its
		 * container. So on a fitted board the height has to be measured and handed to
		 * it; the hard-coded 240/260 were exactly why the Overview was 245px taller
		 * than a 768px screen. The fallbacks are the old values, used whenever the
		 * board is not being fitted (phones) or the container has no height yet. */
		const chartHeight = (node, fallback) => {
			if (!node || !this.boardIsFitted()) return fallback;
			const box = Math.floor(node.getBoundingClientRect().height);
			return box > 60 ? Math.max(120, box - 6) : fallback;
		};

		const t = o.net_pay_trend || [];
		if (t.length && el("trend"))
			this._charts.push(new frappe.Chart(el("trend"), {
				data: { labels: t.map((x) => x.label), datasets: [{ name: __("Net Pay"), values: t.map((x) => x.total) }] },
				type: "line", height: chartHeight(el("trend"), 240), colors: ["#2563eb"],
				lineOptions: { regionFill: 1, hideDots: 0 }, axisOptions: { xIsSeries: 1 },
				tooltipOptions: { formatTooltipY: (d) => this.money(d) },
			}));

		const st = o.slip_status || [];
		if (st.length && el("status"))
			this._charts.push(new frappe.Chart(el("status"), {
				data: { labels: st.map((x) => __(x.status)), datasets: [{ values: st.map((x) => x.count) }] },
				type: "donut", height: chartHeight(el("status"), 240),
				colors: st.map((x) => SC[x.status] || "#6c5ce7"),
			}));

		const dp = o.headcount_by_dept || [];
		if (dp.length && el("dept"))
			this._charts.push(new frappe.Chart(el("dept"), {
				data: { labels: dp.map((x) => x.department), datasets: [{ name: __("Employees"), values: dp.map((x) => x.count) }] },
				type: "bar", height: chartHeight(el("dept"), 260), colors: ["#1e40af"],
			}));
	}

	/* EMPLOYEES (§21) — HR administration, not a bare list.
	 *
	 * The counters are computed from the rows already on screen, so the summary
	 * always describes exactly what is in the table below it — including while
	 * a search is filtering it — and it costs no extra request. */
	view_employees() {
		const load = (search) =>
			this.call("list_employees", { company: this.state.company, search }).then((rows) => {
				const noDept = rows.filter((r) => !(r.department || "").trim()).length;
				const noNif = rows.filter((r) => !(r.custom_nif || "").trim()).length;
				this.$content.find(".ahr-emp-metrics").html(this.metrics([
					{ label: search ? __("Matching") : __("Active employees"),
						value: rows.length, icon: "fa-users",
						foot: search ? __("Matching “{0}”", [frappe.utils.escape_html(search)])
							: __("On the payroll today") },
					{ label: __("No department"), value: noDept, icon: "fa-sitemap",
						kind: noDept ? "warn" : "", foot: __("Cannot be grouped in reports") },
					{ label: __("No NIF"), value: noNif, icon: "fa-id-card-o",
						kind: noNif ? "warn" : "", foot: __("Blocks the IRT declaration") },
				]));
				const tbl = this.table(
					[
						{ key: "employee_name", label: "Name" },
						{ key: "designation", label: "Designation" },
						{ key: "department", label: "Department" },
						{ key: "custom_nif", label: "NIF" },
						{ key: "date_of_joining", label: "Joined", date: true },
					],
					rows,
					{ id: "name",
						empty: { icon: "fa-user-plus",
							title: search ? __("Nobody matches that search")
								: __("No employee records yet"),
							body: search ? __("Try part of a name or an employee ID.")
								: __("An employee record is the starting point for a contract, payroll, leave and documents. Create the first one with New Employee.") } }
				);
				this.$content.find(".ahr-emp-table").html(
					this.subsection(__("Employee directory"), tbl, {
						subtitle: __("Open a person to reach their contract, pay, leave and documents."),
						meta: __("{0} shown", [rows.length]),
					}));
				this.$content.find("tr.clickable").on("click", (e) =>
					this.openEmployee($(e.currentTarget).data("id"))
				);
			});
		this.$content.html(
			this.what(__("Everyone the company employs. HR creates and maintains employee records here; an employee does NOT need a login for HR to run their contract, leave, pay, documents or reviews. Open a person to reach everything you can do for them.")) +
			`<div class="ahr-filters"><div class="ahr-field"><label>${__("Search")}</label>
				<input type="text" class="ahr-emp-search" placeholder="${__("Name or ID")}"></div>
				<button class="btn btn-primary btn-sm ahr-new-emp"><i class="fa fa-plus"></i> ${__("New Employee")}</button></div>
			<div class="ahr-emp-metrics"></div>
			<div class="ahr-panel ahr-emp-table"></div>`
		);
		const $s = this.$content.find(".ahr-emp-search");
		// `input`, not `keyup`: keyup misses a value pasted with the mouse and the
		// browser's own clear button, so the list would keep showing results for a
		// search box that no longer says that.
		$s.on("input", frappe.utils.debounce(() => load($s.val()), 300));
		this.$content.find(".ahr-new-emp").on("click", () => this.newEmployee());
		load("");
	}

	newEmployee() {
		const d = new frappe.ui.Dialog({
			title: __("New Employee"),
			fields: [
				{ fieldname: "first_name", label: __("First Name"), fieldtype: "Data", reqd: 1 },
				{ fieldname: "last_name", label: __("Last Name"), fieldtype: "Data" },
				{ fieldname: "gender", label: __("Gender"), fieldtype: "Link", options: "Gender", reqd: 1 },
				{ fieldname: "date_of_birth", label: __("Date of Birth"), fieldtype: "Date", reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", reqd: 1, default: this.state.company },
				{ fieldname: "date_of_joining", label: __("Date of Joining"), fieldtype: "Date", reqd: 1 },
				{ fieldname: "designation", label: __("Designation"), fieldtype: "Link", options: "Designation" },
				{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
				{ fieldtype: "Section Break", label: __("Angola HR") },
				{ fieldname: "custom_nif", label: __("NIF (Tax ID)"), fieldtype: "Data" },
				{ fieldname: "custom_inss_number", label: __("Social Security No (INSS)"), fieldtype: "Data" },
				{ fieldtype: "Column Break" },
				{ fieldname: "custom_dependents", label: __("Dependents"), fieldtype: "Int", default: 0 },
				{ fieldname: "custom_payroll_payable_account", label: __("Payroll Payable Account"), fieldtype: "Link",
				  options: "Account", get_query: () => ({ filters: { is_group: 0 } }),
				  description: __("Optional. Overrides the default Payroll Payable account for this employee.") },
				{ fieldname: "custom_iban", label: __("IBAN"), fieldtype: "Data",
				  description: __("Bank IBAN — used for the payroll bank-transfer export.") },
				{ fieldname: "custom_insurance", label: __("Seguro"), fieldtype: "Data",
				  description: __("Insurance number — shown on the payslip.") },
				{ fieldname: "default_shift", label: __("Default Shift"), fieldtype: "Link", options: "Shift Type",
				  description: __("Drives working days / Saturday hours in payroll.") },
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				this.call("create_employee", { data: JSON.stringify(v) }).then((r) => {
					d.hide();
					frappe.show_alert({ message: __("Employee {0} created", [r.employee_name || r.name]), indicator: "green" });
					this.go("employees");
				});
			},
		});
		d.show();
	}

	view_holidays() {
		this.$content.html(
			`<div class="ahr-filters"><button class="btn btn-primary btn-sm hl-new"><i class="fa fa-plus"></i> ${__("New Holiday List")}</button></div>
			<div class="ahr-panel hl-list"></div>`
		);
		this.call("list_holiday_lists").then((rows) => {
			this.$content.find(".hl-list").html(
				this.table(
					[{ key: "name", label: "Name" }, { key: "from_date", label: "From", date: true },
					 { key: "to_date", label: "To", date: true }, { key: "total_holidays", label: "Holidays", num: true }],
					rows, { id: "name" }
				)
			);
			this.$content.find(".hl-list tr.clickable").on("click", (e) => this.openHolidayList($(e.currentTarget).data("id")));
		});
		this.$content.find(".hl-new").on("click", () => this.newHolidayList());
	}

	newHolidayList() {
		const d = new frappe.ui.Dialog({
			title: __("New Holiday List"),
			fields: [
				{ fieldname: "holiday_list_name", label: __("Name"), fieldtype: "Data", reqd: 1 },
				{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1 },
				{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1 },
				{ fieldname: "weekly_off", label: __("Weekly Off"), fieldtype: "Select",
				  options: ["", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].join("\n") },
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				this.call("create_holiday_list", v).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Created"), indicator: "green" });
					this.go("holidays");
				});
			},
		});
		d.show();
	}

	openHolidayList(name) {
		this.call("get_holiday_list", { name }).then((h) => {
			const tbl = this.table(
				[{ key: "holiday_date", label: "Date", date: true }, { key: "description", label: "Description" }],
				h.holidays
			);
			const d = new frappe.ui.Dialog({ title: name, size: "large" });
			$(d.body).html(
				this.panel({ title: __("Holiday List"), icon: "fa-calendar-o",
				subtitle: __("The working calendar. Leave, attendance and payroll all count days against it.") },
					`<div class="ahr-form-grid"><div><b>${__("From")}:</b> ${this.d(h.from_date)}</div>
					<div><b>${__("To")}:</b> ${this.d(h.to_date)}</div>
					<div><b>${__("Holidays")}:</b> ${h.total_holidays}</div></div>`) +
				this.panel({ title: __("Holidays"), icon: "fa-calendar-check-o", subtitle: __("Every date in the selected list. Payroll and leave both count working days against these.") }, tbl)
			);
			d.set_primary_action(__("Add Holiday"), () => {
				const a = new frappe.ui.Dialog({
					title: __("Add Holiday"),
					fields: [
						{ fieldname: "holiday_date", label: __("Date"), fieldtype: "Date", reqd: 1 },
						{ fieldname: "description", label: __("Description"), fieldtype: "Data", reqd: 1 },
					],
					primary_action_label: __("Add"),
					primary_action: (v) => {
						this.call("add_holiday", { holiday_list: name, holiday_date: v.holiday_date, description: v.description }).then(() => {
							a.hide();
							d.hide();
							this.openHolidayList(name);
						});
					},
				});
				a.show();
			});
			d.show();
		});
	}

	view_shifts() {
		this.$content.html(
			`<div class="ahr-filters"><button class="btn btn-primary btn-sm st-new"><i class="fa fa-plus"></i> ${__("New Shift Type")}</button></div>
			<div class="ahr-panel st-list"></div>`
		);
		this.call("list_shift_types").then((rows) => {
			this.$content.find(".st-list").html(
				this.table(
					[{ key: "name", label: "Name" }, { key: "start_time", label: "Start" }, { key: "end_time", label: "End" },
					 { key: "enable_auto_attendance", label: "Auto Attendance", render: (v) => (v ? __("Yes") : __("No")) }],
					rows, { id: "name" }
				)
			);
			this.$content.find(".st-list tr.clickable").on("click", (e) => this.editShiftType($(e.currentTarget).data("id")));
		});
		this.$content.find(".st-new").on("click", () => this.editShiftType());
	}

	editShiftType(name) {
		const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
		const hhmm = (t) => {
			if (!t) return "";
			const p = String(t).split(":");
			return `${("0" + p[0]).slice(-2)}:${("0" + (p[1] || "0")).slice(-2)}`;
		};
		const open = (s) => {
			s = s || {};
			const byDay = {};
			(s.weekday_hours || []).forEach((r) => (byDay[r.weekday] = r));
			const grid = WEEKDAYS.map((w) => {
				const r = byDay[w] || {};
				const working = r.is_working_day === undefined ? 1 : r.is_working_day;
				return `<tr data-w="${w}">
					<td>${__(w)}</td>
					<td class="text-center"><input type="checkbox" class="wd-work" ${working ? "checked" : ""}></td>
					<td><input type="time" class="wd-start form-control input-xs" value="${hhmm(r.start_time)}"></td>
					<td><input type="time" class="wd-end form-control input-xs" value="${hhmm(r.end_time)}"></td></tr>`;
			}).join("");
			const d = new frappe.ui.Dialog({
				title: name || __("New Shift Type"),
				size: "large",
				fields: [
					...(name ? [] : [{ fieldname: "shift_name", label: __("Name"), fieldtype: "Data", reqd: 1 }]),
					{ fieldname: "start_time", label: __("Default Start Time"), fieldtype: "Time", reqd: 1, default: s.start_time },
					{ fieldname: "end_time", label: __("Default End Time"), fieldtype: "Time", reqd: 1, default: s.end_time },
					{ fieldtype: "Column Break" },
					{ fieldname: "enable_auto_attendance", label: __("Enable Auto Attendance"), fieldtype: "Check", default: s.enable_auto_attendance },
					{ fieldname: "working_hours_threshold_for_half_day", label: __("Half Day Threshold (hrs)"), fieldtype: "Float", default: s.working_hours_threshold_for_half_day },
					{ fieldname: "working_hours_threshold_for_absent", label: __("Absent Threshold (hrs)"), fieldtype: "Float", default: s.working_hours_threshold_for_absent },
					{ fieldtype: "Section Break", label: __("Weekly Schedule (Optional)") },
					{ fieldname: "weekday_html", fieldtype: "HTML" },
				],
				primary_action_label: __("Save"),
				primary_action: (v) => {
					const wh = [];
					$grid.find("tbody tr").each((_, tr) => {
						const $tr = $(tr);
						const work = $tr.find(".wd-work").is(":checked") ? 1 : 0;
						const st = $tr.find(".wd-start").val();
						const en = $tr.find(".wd-end").val();
						if (!work || st || en)
							wh.push({ weekday: $tr.data("w"), is_working_day: work,
								start_time: st ? st + ":00" : null, end_time: en ? en + ":00" : null });
					});
					this.call("save_shift_type", { data: JSON.stringify({ ...v, name, weekday_hours: wh }) }).then(() => {
						d.hide();
						frappe.show_alert({ message: __("Saved"), indicator: "green" });
						this.go("shifts");
					});
				},
			});
			d.fields_dict.weekday_html.$wrapper.html(
				`<table class="ahr-table"><thead><tr><th>${__("Weekday")}</th><th class="text-center">${__("Working")}</th><th>${__("Start")}</th><th>${__("End")}</th></tr></thead><tbody>${grid}</tbody></table>
				<div class="text-muted" style="margin-top:8px;font-size:12px;">${__("Leave times blank to use the default. Uncheck Working for a non-working day. E.g. Saturday 09:00–13:00, others 08:00–17:00.")}</div>`
			);
			const $grid = d.fields_dict.weekday_html.$wrapper;
			d.show();
		};
		if (name) this.call("get_shift_type", { name }).then(open);
		else open({});
	}

	openEmployee(name) {
		this.call("get_employee", { name }).then((r) => {
			const e = r.employee || {};
			const p = r.profile;
			const info = `<div class="ahr-form-grid">
				<div><b>${__("Name")}:</b> ${e.employee_name || ""}</div>
				<div><b>${__("Designation")}:</b> ${e.designation || "-"}</div>
				<div><b>${__("Department")}:</b> ${e.department || "-"}</div>
				<div><b>NIF:</b> ${e.custom_nif || "-"}</div>
				<div><b>INSS:</b> ${e.custom_inss_number || "-"}</div>
				<div><b>${__("Dependents")}:</b> ${e.custom_dependents || 0}</div>
				<div><b>${__("Payroll Payable Account")}:</b> ${e.custom_payroll_payable_account || "-"}</div>
				<div><b>IBAN:</b> ${e.custom_iban ? frappe.utils.escape_html(e.custom_iban) : "-"}</div>
				<div><b>${__("Seguro")}:</b> ${e.custom_insurance ? frappe.utils.escape_html(e.custom_insurance) : "-"}</div>
				<div><b>${__("Default Shift")}:</b> ${e.default_shift ? frappe.utils.escape_html(e.default_shift) : "-"}</div></div>`;
			const prof = p
				? `<div class="ahr-form-grid"><div><b>${__("Base")}:</b> ${this.money(p.base)}</div>
					<div><b>${__("Food")}:</b> ${this.money(p.food_allowance)}</div>
					<div><b>${__("Transport")}:</b> ${this.money(p.transport_allowance)}</div>
					<div><b>${__("From")}:</b> ${this.d(p.from_date)}</div></div>`
				: `<div class="ahr-empty">${__("No salary profile")}</div>`;
			const slips = this.table(
				[
					{ key: "start_date", label: "From", date: true },
					{ key: "net_pay", label: "Net Pay", money: true },
					{ key: "status", label: "Status", render: (v) => this.slipStatus(v) },
				],
				r.slips
			);
			const d = new frappe.ui.Dialog({ title: e.employee_name || name, size: "large" });
			$(d.body).html(
				this.panel(__("Employee"), info) + this.panel(__("Salary Profile"), prof) +
					this.panel(__("Recent Slips"), slips) +
					// Cross-links: from a person to the things HR actually does to them.
					// Everything an employee might ask for is startable from here with the
					// employee already in front of you.
					`<div class="ahr-xlinks">
						<button class="btn btn-sm btn-default xl-contract">${__("Create Contract")}</button>
						<button class="btn btn-sm btn-default xl-leave">${__("Record Leave")}</button>
						<button class="btn btn-sm btn-default xl-change">${__("Request Salary Change")}</button>
						<button class="btn btn-sm btn-default xl-advance">${__("New Advance")}</button>
						<button class="btn btn-sm btn-default xl-bank">${__("Bank Change")}</button>
						<button class="btn btn-sm btn-default xl-doc">${__("Add Document")}</button>
						<button class="btn btn-sm btn-default xl-profile">${__("Salary Profiles")}</button>
					</div>`
			);
			const jump = (sel, fn) => $(d.body).find(sel).on("click", () => { d.hide(); fn(); });
			jump(".xl-contract", () => this.newContractDialog());
			jump(".xl-leave", () => this.newLeave());
			jump(".xl-change", () => this.newSalaryChangeDialog());
			jump(".xl-advance", () => this.newAdvanceDialog());
			jump(".xl-bank", () => this.newBankChangeDialog());
			jump(".xl-doc", () => this.newDocumentDialog());
			jump(".xl-profile", () => this.go("profiles"));
			d.set_primary_action(__("Edit Profile"), () => {
				d.hide();
				this.editProfile(p ? p : { employee: name });
			});
			d.set_secondary_action_label(__("Edit Details"));
			d.set_secondary_action(() => {
				d.hide();
				this.editEmployee(e);
			});
			d.show();
		});
	}

	editEmployee(e) {
		e = e || {};
		const d = new frappe.ui.Dialog({
			title: `${__("Edit Employee")} · ${e.employee_name || e.name}`,
			fields: [
				{ fieldname: "designation", label: __("Designation"), fieldtype: "Link", options: "Designation", default: e.designation },
				{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department", default: e.department },
				{ fieldtype: "Column Break" },
				{ fieldname: "custom_nif", label: __("NIF (Tax ID)"), fieldtype: "Data", default: e.custom_nif },
				{ fieldname: "custom_inss_number", label: __("Social Security No (INSS)"), fieldtype: "Data", default: e.custom_inss_number },
				{ fieldname: "custom_dependents", label: __("Dependents"), fieldtype: "Int", default: e.custom_dependents },
				{ fieldtype: "Section Break", label: __("Payroll") },
				{ fieldname: "custom_payroll_payable_account", label: __("Payroll Payable Account"), fieldtype: "Link",
				  options: "Account", default: e.custom_payroll_payable_account, get_query: () => ({ filters: { is_group: 0 } }),
				  description: __("Optional. Overrides the default Payroll Payable account for this employee.") },
				{ fieldname: "custom_iban", label: __("IBAN"), fieldtype: "Data", default: e.custom_iban,
				  description: __("Bank IBAN — used for the payroll bank-transfer export.") },
				{ fieldname: "custom_insurance", label: __("Seguro"), fieldtype: "Data", default: e.custom_insurance,
				  description: __("Insurance number — shown on the payslip.") },
				{ fieldname: "default_shift", label: __("Default Shift"), fieldtype: "Link", options: "Shift Type", default: e.default_shift,
				  description: __("Drives working days / Saturday hours in payroll.") },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				this.call("update_employee", { name: e.name, data: JSON.stringify(v) }).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Saved"), indicator: "green" });
					this.openEmployee(e.name);
				});
			},
		});
		d.show();
	}

	view_attendance() {
		this.renderFilterList(
			"list_attendance",
			[
				{ key: "employee_name", label: "Employee" },
				{ key: "attendance_date", label: "Date", date: true },
				{ key: "status", label: "Status" },
				{ key: "working_hours", label: "Hours", num: true },
				{ key: "overtime_hours", label: "Overtime", num: true },
			],
			{ dates: true, employee: true },
			(id) => this.editAttendance(id),
			"name"
		);
		// renderFilterList owns the markup for this screen, so the help strip is prepended
		// afterwards rather than passed in — it must not become an argument every other
		// caller of renderFilterList has to think about.
		this.$content.prepend(this.what(__("Daily attendance, entered or imported by HR. Payroll uses it to work out payable days; anything unexplained becomes an occurrence under Attendance Justifications.")));
		const $bulk = $(`<button class="btn btn-primary btn-sm" style="align-self:flex-end;"><i class="fa fa-users"></i> ${__("Bulk Attendance")}</button>`);
		this.$content.find(".ahr-filters").append($bulk);
		$bulk.on("click", () => this.bulkAttendanceDialog());
		const $btn = $(`<button class="btn btn-default btn-sm" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("Mark One")}</button>`);
		this.$content.find(".ahr-filters").append($btn);
		$btn.on("click", () => this.markAttendanceDialog());
	}

	bulkAttendanceDialog() {
		const esc = frappe.utils.escape_html;
		const STATUSES = ["Present", "Absent", "Half Day", "Work From Home"];
		// Normal daily hours by weekday (JS: 0=Sun … 6=Sat): Sun 0, Sat 4, else 8.
		const normH = (ds) => { const wd = new Date(ds + "T00:00:00").getDay(); return wd === 0 ? 0 : (wd === 6 ? 4 : 8); };
		this.call("list_employees", { company: this.state.company }).then((emps) => {
			const today = frappe.datetime.get_today();
			const d = new frappe.ui.Dialog({ title: __("Bulk Attendance"), size: "extra-large" });
			const rowsHtml = (emps || []).map((e) => `
				<tr data-emp="${esc(e.name)}" data-search="${esc(((e.employee_name || "") + " " + e.name).toLowerCase())}">
					<td>${esc(e.employee_name || e.name)}<div class="pv-sub">${esc(e.designation || "")}</div></td>
					<td><select class="form-control input-xs bk-status">${STATUSES.map((x) => `<option value="${x}">${__(x)}</option>`).join("")}</select></td>
					<td><input type="number" class="form-control input-xs bk-wh"></td>
					<td><input type="number" class="form-control input-xs bk-ot" value="0"></td>
				</tr>`).join("");
			$(d.body).html(`
				<div class="ahr-form-grid" style="grid-template-columns:200px 1fr;">
					<div class="ahr-field"><label>${__("Date")}</label><input type="date" class="bk-date" value="${today}"></div>
				</div>
				<input type="text" class="form-control input-sm bk-search" placeholder="${__("Search employee...")}" style="max-width:320px;margin:8px 0;">
				<div class="text-muted small" style="margin-bottom:8px;">${__("Everyone defaults to Present — change only the absent / incident rows.")}</div>
				<div class="pv-scroll"><table class="ahr-table"><thead><tr>
					<th>${__("Employee")}</th><th>${__("Status")}</th><th class="num">${__("Worked h")}</th><th class="num">${__("Overtime h")}</th>
				</tr></thead><tbody class="bk-body">${rowsHtml}</tbody></table></div>
				<div class="ahr-list-meta bk-meta" style="margin-top:8px;"></div>`);

			const applyDefaults = () => {
				const nh = normH($(d.body).find(".bk-date").val());
				$(d.body).find("tbody tr").each((_, tr) => {
					const $tr = $(tr);
					// Present rows follow the day's normal hours; keep any manual edits on non-Present.
					if ($tr.find(".bk-status").val() === "Present") $tr.find(".bk-wh").val(nh);
				});
			};
			applyDefaults();
			$(d.body).find(".bk-date").on("change", applyDefaults);
			$(d.body).find(".bk-body").on("change", ".bk-status", (e) => {
				const $tr = $(e.currentTarget).closest("tr");
				const st = $(e.currentTarget).val();
				const nh = normH($(d.body).find(".bk-date").val());
				$tr.find(".bk-wh").val(st === "Present" || st === "Work From Home" ? nh : (st === "Half Day" ? nh / 2 : 0));
			});
			$(d.body).find(".bk-search").on("input", (e) => {
				const q = (e.currentTarget.value || "").toLowerCase().trim();
				$(d.body).find("tbody tr").each((_, tr) =>
					$(tr).toggle(!q || ($(tr).attr("data-search") || "").indexOf(q) !== -1));
			});

			d.set_primary_action(__("Save Attendance"), () => {
				const date = $(d.body).find(".bk-date").val();
				if (!date) return frappe.msgprint(__("Pick a date"));
				const rows = [];
				$(d.body).find("tbody tr").each((_, tr) => {
					const $tr = $(tr);
					rows.push({
						employee: $tr.data("emp"),
						status: $tr.find(".bk-status").val(),
						working_hours: flt($tr.find(".bk-wh").val()),
						overtime_hours: flt($tr.find(".bk-ot").val()),
					});
				});
				frappe.dom.freeze(__("Saving attendance..."));
				this.call("bulk_mark_attendance", { attendance_date: date, company: this.state.company, rows: JSON.stringify(rows) })
					.then((res) => {
						frappe.dom.unfreeze();
						this._bulkResult(__("Attendance"), res);
						d.hide();
						if (this._listReload) this._listReload();
					})
					.catch(() => frappe.dom.unfreeze());
			});
			d.show();
		});
	}

	// Edit an attendance record — overtime (and worked hours) can be corrected even after it
	// is submitted, since HR often only gets the overtime the next day.
	editAttendance(name) {
		this.call("get_attendance", { name }).then((a) => {
			if (!a) return;
			const submitted = a.docstatus === 1;
			const d = new frappe.ui.Dialog({
				title: `${a.employee_name || a.employee} · ${this.d(a.attendance_date)}`,
				fields: [
					{ fieldname: "status", label: __("Status"), fieldtype: "Select",
					  options: ["Present", "Absent", "Half Day", "On Leave", "Work From Home"].join("\n"),
					  default: a.status, read_only: submitted ? 1 : 0 },
					{ fieldname: "working_hours", label: __("Working Hours"), fieldtype: "Float", default: a.working_hours },
					{ fieldname: "overtime_hours", label: __("Overtime Hours"), fieldtype: "Float", default: a.overtime_hours },
					...(submitted
						? [{ fieldtype: "HTML", options: `<div class="text-muted small">${__("Submitted — hours and overtime can be corrected here. To change the status, cancel the record first.")}</div>` }]
						: []),
				],
				primary_action_label: __("Save"),
				primary_action: (v) => {
					this.call("update_attendance", {
						name,
						status: submitted ? null : v.status,
						working_hours: v.working_hours,
						overtime_hours: v.overtime_hours,
					}).then(() => {
						d.hide();
						frappe.show_alert({ message: __("Attendance updated"), indicator: "green" });
						if (this._listReload) this._listReload();
					});
				},
			});
			d.show();
		});
	}

	markAttendanceDialog() {
		const d = new frappe.ui.Dialog({
			title: __("Mark Attendance"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1 },
				{ fieldname: "attendance_date", label: __("Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
				{ fieldtype: "Column Break" },
				{ fieldname: "status", label: __("Status"), fieldtype: "Select", reqd: 1,
				  options: ["Present", "Absent", "Half Day", "On Leave", "Work From Home"].join("\n"), default: "Present" },
				{ fieldname: "working_hours", label: __("Working Hours"), fieldtype: "Float", default: 8 },
				{ fieldname: "overtime_hours", label: __("Overtime Hours"), fieldtype: "Float", default: 0 },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				this.call("mark_attendance", v)
					.then(() => {
						d.hide();
						frappe.show_alert({ message: __("Attendance marked"), indicator: "green" });
						this.render();
					})
					.catch(() => {});
			},
		});
		d.show();
	}

	// ---- Attendance Occurrences ----
	occStatus(status) {
		const cls = { "Pending Justification": "draft", "Justified": "paid", "Unjustified": "cancelled" };
		return `<span class="ahr-badge ${cls[status] || "draft"}">${__(status || "")}</span>`;
	}

	view_occurrences() {
		const STATUSES = ["", "Pending Justification", "Justified", "Unjustified"];
		this.$content.html(
			this.what(__("Lateness, early exits and absences that need explaining. The employee hands HR a medical certificate or gives a reason; HR records it here, attaches the document and decides whether the absence is justified.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="occ-f-emp"></div></div>
				<div class="ahr-field"><label>${__("Status")}</label>
					<select class="occ-f-status form-control">
						${STATUSES.map((x) => `<option value="${x}">${x ? __(x) : __("All Statuses")}</option>`).join("")}
					</select></div>
				<div class="ahr-field"><label>${__("From")}</label><input type="date" class="occ-f-from"></div>
				<div class="ahr-field"><label>${__("To")}</label><input type="date" class="occ-f-to"></div>
				<button class="btn btn-primary btn-sm occ-new" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Occurrence")}</button>
			</div>
			<div class="ahr-panel ahr-list"></div>`
		);
		const cols = [
			{ key: "occurrence_date", label: "Date", date: true },
			{ key: "employee_name", label: "Employee" },
			{ key: "occurrence_type", label: "Type", render: (v) => __(v) },
			{ key: "hours", label: "Hours", num: true },
			{ key: "status", label: "Status", render: (v) => this.occStatus(v) },
			{ key: "justification_reason", label: "Reason", render: (v) => (v ? frappe.utils.escape_html(v) : "—") },
			{ key: "justification_deadline", label: "Deadline", date: true },
		];
		let empCtrl = null;
		const load = () => {
			this.call("list_occurrences", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
				status: this.$content.find(".occ-f-status").val() || null,
				from_date: this.$content.find(".occ-f-from").val() || null,
				to_date: this.$content.find(".occ-f-to").val() || null,
			}).then((rows) => {
				this.$content.find(".ahr-list").html(rows.length
					? `<div class="ahr-list-meta">${rows.length} ${__("occurrences")}</div>` +
						this.table(cols, rows, { id: "name" })
					: this.blank({
						icon: "fa-exclamation-triangle",
						title: __("No attendance occurrences match this filter."),
						body: __("An occurrence is a lateness, an early exit or an absence that the employee has to explain. Create one here, then record the explanation and the supporting document the employee handed over, and mark it Justified or Unjustified."),
						who: __("Recorded and decided by HR. The employee does not need a login — if they have one they can upload their own certificate, and it arrives here for you to decide. After five days an occurrence locks, and only an HR Manager can re-justify it as an extraordinary case."),
						actions: [{ label: __("New Occurrence"), cls: "occ-new-blank", primary: true }],
					}));
				this.$content.find(".occ-new-blank").on("click", () => this.newOccurrence());
				this.$content.find(".ahr-list tr.clickable").on("click", (e) =>
					this.openOccurrence(rows.find((r) => r.name === $(e.currentTarget).data("id")))
				);
			});
		};
		this._listReload = load;
		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".occ-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".occ-f-status, .occ-f-from, .occ-f-to").on("change", load);
		this.$content.find(".occ-new").on("click", () => this.newOccurrence());
		load();
	}

	newOccurrence() {
		const d = new frappe.ui.Dialog({
			title: __("New Occurrence"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1 },
				{ fieldname: "occurrence_date", label: __("Occurrence Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
				{ fieldtype: "Column Break" },
				{ fieldname: "occurrence_type", label: __("Type"), fieldtype: "Select", reqd: 1, default: "Full Day",
				  options: ["Lateness", "Early Exit", "Partial Absence", "Half Day", "Full Day"].join("\n") },
				{ fieldname: "hours", label: __("Missing Hours"), fieldtype: "Float",
				  depends_on: "eval:['Lateness','Early Exit','Partial Absence'].includes(doc.occurrence_type)" },
				{ fieldtype: "Section Break", label: __("Authorization") },
				{ fieldname: "authorized", label: __("Authorized / Pre-approved"), fieldtype: "Check",
				  description: __("Scheduled or pre-approved (e.g. medical appointment). Created already justified.") },
				{ fieldname: "justification_reason", label: __("Reason"), fieldtype: "Link", options: "Isoft Absence Reason",
				  depends_on: "authorized", mandatory_depends_on: "authorized", get_query: () => ({ filters: { is_active: 1 } }) },
				{ fieldname: "approved_by", label: __("Approved By"), fieldtype: "Data", depends_on: "authorized" },
				{ fieldtype: "Section Break" },
				{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				this.call("create_occurrence", { data: JSON.stringify(v) }).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Occurrence registered"), indicator: "green" });
					this.go("occurrences");
				});
			},
		});
		d.show();
	}

	openOccurrence(o) {
		if (!o) return;
		const esc = frappe.utils.escape_html;
		// Locked = 5-day window passed, not authorized, and not already justified.
		const windowClosed = o.justification_deadline && frappe.datetime.get_today() > o.justification_deadline;
		const locked = windowClosed && !cint(o.authorized) && o.status !== "Justified";
		const info = `<div class="ahr-form-grid">
			<div><b>${__("Employee")}:</b> ${esc(o.employee_name || o.employee)}</div>
			<div><b>${__("Occurrence Date")}:</b> ${this.d(o.occurrence_date)}</div>
			<div><b>${__("Type")}:</b> ${__(o.occurrence_type)}</div>
			<div><b>${__("Missing Hours")}:</b> ${flt(o.hours)}</div>
			<div><b>${__("Status")}:</b> ${this.occStatus(o.status)}${cint(o.authorized) ? ` · <span class="ahr-badge accrued">${__("Authorized")}</span>` : ""}</div>
			<div><b>${__("Justification Deadline")}:</b> ${this.d(o.justification_deadline)}</div>
			<div><b>${__("Reason")}:</b> ${o.justification_reason ? esc(o.justification_reason) : "—"}</div>
			<div><b>${__("How it reached HR")}:</b> ${o.justification_source ? esc(__(o.justification_source)) : "—"}</div>
			<div><b>${__("Supporting document")}:</b> ${o.justification_document
				? `<a href="${esc(o.justification_document)}" target="_blank">${__("Open")}</a>` : "—"}</div>
			<div><b>${__("Remarks")}:</b> ${o.remarks ? esc(o.remarks) : "—"}</div></div>`;
		const d = new frappe.ui.Dialog({ title: o.name, size: "large" });
		$(d.body).html(this.panel(__("Occurrence"), info) +
			`<div class="ahr-doc-actions">
				${locked ? `<span class="ahr-lock-note">${__("Locked — over 5 days. Re-justify only via Extraordinary override (HR Manager).")}</span>` : ""}
				${!locked && o.status !== "Justified" ? `<button class="btn btn-xs btn-primary occ-justify">${__("Justify")}</button>` : ""}
				${locked ? `<button class="btn btn-xs btn-primary occ-extra">${__("Extraordinary Re-justify")}</button>` : ""}
				${!locked && o.status !== "Unjustified" ? `<button class="btn btn-xs btn-default occ-unjust">${__("Mark Unjustified")}</button>` : ""}
				${!locked && o.status !== "Pending Justification" ? `<button class="btn btn-xs btn-default occ-pending">${__("Reset to Pending")}</button>` : ""}
				<button class="btn btn-xs btn-danger occ-delete">${__("Delete")}</button>
			</div>`);
		$(d.body).find(".occ-justify").on("click", () => { d.hide(); this.justifyOccurrence(o.name, false); });
		$(d.body).find(".occ-extra").on("click", () => { d.hide(); this.justifyOccurrence(o.name, true); });
		$(d.body).find(".occ-unjust").on("click", () =>
			this.call("set_occurrence_status", { name: o.name, status: "Unjustified" }).then(() => { d.hide(); this.go("occurrences"); }));
		$(d.body).find(".occ-pending").on("click", () =>
			this.call("set_occurrence_status", { name: o.name, status: "Pending Justification" }).then(() => { d.hide(); this.go("occurrences"); }));
		$(d.body).find(".occ-delete").on("click", () =>
			frappe.confirm(__("Delete this occurrence?"), () =>
				this.call("delete_occurrence", { name: o.name }).then(() => { d.hide(); this.go("occurrences"); })));
		d.show();
	}

	justifyOccurrence(name, extraordinary) {
		const d = new frappe.ui.Dialog({
			title: extraordinary ? __("Extraordinary Re-justify") : __("Justify Occurrence"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("Record the explanation the employee gave and attach whatever they handed over. Your own user is recorded as the person who entered it.")}</div>` },
				{ fieldname: "reason", label: __("Justification Reason"), fieldtype: "Link", options: "Isoft Absence Reason", reqd: 1,
				  get_query: () => ({ filters: { is_active: 1 } }) },
				{ fieldname: "document", label: __("Supporting Document"), fieldtype: "Attach",
				  description: __("The medical certificate or other proof the employee produced.") },
				{ fieldname: "justification_source", label: __("How it reached HR"), fieldtype: "Select",
				  options: REQUEST_SOURCES.join("\n"), default: "Written request" },
				...(extraordinary ? [{ fieldname: "note", label: __("Extraordinary Note"), fieldtype: "Small Text", reqd: 1,
				  description: __("Exceptional circumstance (hospitalization, death, …).") }] : []),
				{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
			],
			primary_action_label: __("Mark Justified"),
			primary_action: (v) => {
				this.call("justify_occurrence", {
					name, reason: v.reason, document: v.document || null, remarks: v.remarks || null,
					extraordinary: extraordinary ? 1 : 0, note: v.note || null,
					justification_source: v.justification_source || null,
				}).then(() => { d.hide(); frappe.show_alert({ message: __("Justified"), indicator: "green" }); this.go("occurrences"); })
				  .catch(() => {});
			},
		});
		d.show();
	}

	// ---- Leaves ----
	leaveStatus(status) {
		const cls = { Open: "draft", Approved: "paid", Rejected: "cancelled", Cancelled: "submitted" };
		return `<span class="ahr-badge ${cls[status] || "draft"}">${__(status || "Open")}</span>`;
	}

	view_leaves() {
		const STATUSES = ["", "Open", "Approved", "Rejected", "Cancelled"];
		this.$content.html(
			this.what(__("Leave is recorded by HR. The employee tells you — in person, by e-mail or on paper — you enter it here, and an HR Manager approves or rejects it. Employees with self-service can submit their own, and those arrive in the same list.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="lv-f-emp"></div></div>
				<div class="ahr-field"><label>${__("Status")}</label>
					<select class="lv-f-status form-control">
						${STATUSES.map((x) => `<option value="${x}">${x ? __(x) : __("All Statuses")}</option>`).join("")}
					</select></div>
				<div class="ahr-field"><label>${__("From")}</label><input type="date" class="lv-f-from"></div>
				<div class="ahr-field"><label>${__("To")}</label><input type="date" class="lv-f-to"></div>
				<button class="btn btn-primary btn-sm lv-new" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Leave Request")}</button>
			</div>
			<div class="ahr-panel ahr-list"></div>`
		);
		const cols = [
			{ key: "employee_name", label: "Employee" },
			{ key: "leave_type", label: "Leave Type", render: (v) => frappe.utils.escape_html(v || "") },
			{ key: "period", label: "Period", render: (_, r) => `${this.d(r.from_date)} → ${this.d(r.to_date)}${r.half_day ? " ½" : ""}` },
			{ key: "total_leave_days", label: "Days", num: true },
			{ key: "status", label: "Status", render: (v) => this.leaveStatus(v) },
		];
		let empCtrl = null;
		const load = () => {
			this.call("list_leaves", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
				status: this.$content.find(".lv-f-status").val() || null,
				from_date: this.$content.find(".lv-f-from").val() || null,
				to_date: this.$content.find(".lv-f-to").val() || null,
			}).then((rows) => {
				this.$content.find(".ahr-list").html(rows.length
					? `<div class="ahr-list-meta">${rows.length} ${__("leave requests")}</div>` +
						this.table(cols, rows, { id: "name" })
					: this.blank({
						icon: "fa-plane",
						title: __("No leave requests match this filter."),
						body: __("A leave request records that an employee will be away, and draws the days from their allocation. Create it here on the employee's behalf; the balance and entitlement rules are checked when it is approved, so an over-allocation is refused whoever enters it."),
						who: __("Recorded by HR, approved or rejected by an HR Manager. The employee does not need a login — but if they have one, they can request leave themselves from the self-service area and it appears in this same list."),
						actions: [{ label: __("New Leave Request"), cls: "lv-new-blank", primary: true }],
					}));
				this.$content.find(".lv-new-blank").on("click", () => this.newLeave());
				this.$content.find(".ahr-list tr.clickable").on("click", (e) =>
					this.openLeave(rows.find((r) => r.name === $(e.currentTarget).data("id"))));
			});
		};
		this._listReload = load;
		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".lv-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".lv-f-status, .lv-f-from, .lv-f-to").on("change", load);
		this.$content.find(".lv-new").on("click", () => this.newLeave());
		load();
	}

	newLeave() {
		const d = new frappe.ui.Dialog({
			title: __("New Leave Request"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1 },
				{ fieldname: "leave_type", label: __("Leave Type"), fieldtype: "Link", options: "Leave Type", reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
				{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
				{ fieldtype: "Section Break" },
				{ fieldname: "half_day", label: __("Half Day"), fieldtype: "Check" },
				{ fieldname: "half_day_date", label: __("Half Day Date"), fieldtype: "Date", depends_on: "half_day" },
				{ fieldname: "description", label: __("Reason"), fieldtype: "Small Text" },
				sourceField("Employee verbal request"),
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				this.call("create_leave", { data: JSON.stringify(v) })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Leave request created"), indicator: "green" }); this.go("leaves"); })
					.catch(() => {});
			},
		});
		d.show();
	}

	openLeave(l) {
		if (!l) return;
		const esc = frappe.utils.escape_html;
		const info = `<div class="ahr-form-grid">
			<div><b>${__("Employee")}:</b> ${esc(l.employee_name || l.employee)}</div>
			<div><b>${__("Leave Type")}:</b> ${esc(l.leave_type || "")}</div>
			<div><b>${__("Period")}:</b> ${this.d(l.from_date)} → ${this.d(l.to_date)}${l.half_day ? " ½" : ""}</div>
			<div><b>${__("Days")}:</b> ${flt(l.total_leave_days)}</div>
			<div><b>${__("Status")}:</b> ${this.leaveStatus(l.status)}</div>
			<div><b>${__("Reason")}:</b> ${l.description ? esc(l.description) : "—"}</div></div>`;
		const d = new frappe.ui.Dialog({ title: l.name, size: "large" });
		const open = l.docstatus === 0 && l.status === "Open";
		$(d.body).html(this.panel(__("Leave Request"), info) +
			`<div class="ahr-doc-actions">
				${open ? `<button class="btn btn-xs btn-primary lv-approve">${__("Approve")}</button>` : ""}
				${open ? `<button class="btn btn-xs btn-default lv-reject">${__("Reject")}</button>` : ""}
				${l.docstatus === 1 ? `<button class="btn btn-xs btn-default lv-cancel">${__("Cancel")}</button>` : ""}
				<button class="btn btn-xs btn-danger lv-delete">${__("Delete")}</button>
			</div>`);
		const act = (method, confirmMsg) => {
			const run = () => this.call(method, { name: l.name })
				.then(() => { d.hide(); frappe.show_alert({ message: __("Done"), indicator: "green" }); this.go("leaves"); })
				.catch(() => {});
			confirmMsg ? frappe.confirm(confirmMsg, run) : run();
		};
		$(d.body).find(".lv-approve").on("click", () => act("approve_leave"));
		$(d.body).find(".lv-reject").on("click", () => act("reject_leave", __("Reject this leave request?")));
		$(d.body).find(".lv-cancel").on("click", () => act("cancel_leave", __("Cancel this leave?")));
		$(d.body).find(".lv-delete").on("click", () => act("delete_leave", __("Delete this leave request?")));
		d.show();
	}

	// ---- Leave Allocations (balances) ----
	view_allocations() {
		this.$content.html(
			this.what(__("How many days of each leave type an employee is entitled to for a period. A leave request draws from the allocation, so allocate first or the request has no balance to spend.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="al-f-emp"></div></div>
				<button class="btn btn-primary btn-sm al-bulk" style="align-self:flex-end;"><i class="fa fa-users"></i> ${__("Bulk Allocate")}</button>
				<button class="btn btn-default btn-sm al-new" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Allocation")}</button>
			</div>
			<div class="ahr-panel ahr-list"></div>`
		);
		const cols = [
			{ key: "employee_name", label: "Employee" },
			{ key: "leave_type", label: "Leave Type", render: (v) => frappe.utils.escape_html(v || "") },
			{ key: "period", label: "Period", render: (_, r) => `${this.d(r.from_date)} → ${this.d(r.to_date)}` },
			{ key: "total_leaves_allocated", label: "Allocated", num: true },
			{ key: "carry_forward", label: "Carry Fwd", render: (v) => (v ? __("Yes") : "—") },
		];
		let empCtrl = null;
		const load = () => {
			this.call("list_leave_allocations", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
			}).then((rows) => {
				this.$content.find(".ahr-list").html(
					`<div class="ahr-list-meta">${rows.length} ${__("allocations")}</div>` +
						this.table(cols, rows, { id: "name" })
				);
				this.$content.find(".ahr-list tr.clickable").on("click", (e) =>
					this.openAllocation(rows.find((r) => r.name === $(e.currentTarget).data("id"))));
			});
		};
		this._listReload = load;
		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".al-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".al-new").on("click", () => this.newAllocation());
		this.$content.find(".al-bulk").on("click", () => this.bulkAllocateDialog());
		load();
	}

	bulkAllocateDialog() {
		const yr = new Date().getFullYear();
		this.call("list_employees", { company: this.state.company }).then((emps) => {
			const esc = frappe.utils.escape_html;
			const d = new frappe.ui.Dialog({ title: __("Bulk Leave Allocation"), size: "extra-large" });
			const rows = (emps || []).map((e) => `
				<tr data-emp="${esc(e.name)}" data-search="${esc(((e.employee_name || "") + " " + e.name).toLowerCase())}">
					<td class="pv-c"><input type="checkbox" class="ba-sel" checked></td>
					<td>${esc(e.employee_name || e.name)}<div class="pv-sub">${esc(e.designation || "")}</div></td>
				</tr>`).join("");
			$(d.body).html(`
				<div class="ba-head">
					<div class="ahr-form-grid">
						<div class="ahr-field"><label>${__("Leave Type")}</label><div class="ba-lt"></div></div>
						<div class="ahr-field"><label>${__("Days Allocated")}</label><input type="number" class="ba-days form-control"></div>
						<div class="ahr-field"><label>${__("From Date")}</label><input type="date" class="ba-from form-control" value="${yr}-01-01"></div>
						<div class="ahr-field"><label>${__("To Date")}</label><input type="date" class="ba-to form-control" value="${yr}-12-31"></div>
					</div>
					<label style="display:block;margin:8px 0;"><input type="checkbox" class="ba-cf"> ${__("Carry Forward")}</label>
					<input type="text" class="form-control input-sm ba-search" placeholder="${__("Search employee...")}" style="max-width:320px;margin:6px 0;">
					<div class="text-muted small" style="margin-bottom:6px;">${__("Ticked employees will each get this allocation (existing ones are skipped).")}</div>
				</div>
				<div class="pv-scroll"><table class="ahr-table"><thead><tr>
					<th class="pv-c"><input type="checkbox" class="ba-all" checked></th><th>${__("Employee")}</th>
				</tr></thead><tbody>${rows}</tbody></table></div>`);
			const ltCtrl = frappe.ui.form.make_control({
				df: { fieldtype: "Link", options: "Leave Type", placeholder: __("Leave Type") },
				parent: $(d.body).find(".ba-lt")[0], render_input: true, only_input: true,
			});
			$(d.body).find(".ba-all").on("change", (e) => $(d.body).find(".ba-sel").prop("checked", e.currentTarget.checked));
			$(d.body).find(".ba-search").on("input", (e) => {
				const q = (e.currentTarget.value || "").toLowerCase().trim();
				$(d.body).find("tbody tr").each((_, tr) => $(tr).toggle(!q || ($(tr).attr("data-search") || "").indexOf(q) !== -1));
			});
			d.set_primary_action(__("Allocate to selected"), () => {
				const lt = ltCtrl.get_value();
				const days = flt($(d.body).find(".ba-days").val());
				if (!lt) return frappe.msgprint(__("Pick a Leave Type"));
				if (!days) return frappe.msgprint(__("Enter the days to allocate"));
				const chosen = $(d.body).find(".ba-sel:checked").map((_, c) => $(c).closest("tr").data("emp")).get();
				if (!chosen.length) return frappe.msgprint(__("Select at least one employee"));
				frappe.dom.freeze(__("Allocating..."));
				this.call("bulk_allocate_leave", {
					leave_type: lt, from_date: $(d.body).find(".ba-from").val(), to_date: $(d.body).find(".ba-to").val(),
					new_leaves_allocated: days, carry_forward: $(d.body).find(".ba-cf").is(":checked") ? 1 : 0,
					employees: JSON.stringify(chosen), company: this.state.company,
				}).then((res) => { frappe.dom.unfreeze(); this._bulkResult(__("Allocations"), res); d.hide(); this.go("allocations"); })
				  .catch(() => frappe.dom.unfreeze());
			});
			d.show();
		});
	}

	newAllocation() {
		const yr = new Date().getFullYear();
		const d = new frappe.ui.Dialog({
			title: __("New Leave Allocation"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1 },
				{ fieldname: "leave_type", label: __("Leave Type"), fieldtype: "Link", options: "Leave Type", reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: `${yr}-01-01` },
				{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: `${yr}-12-31` },
				{ fieldtype: "Section Break" },
				{ fieldname: "new_leaves_allocated", label: __("Days Allocated"), fieldtype: "Float", reqd: 1 },
				{ fieldname: "carry_forward", label: __("Carry Forward"), fieldtype: "Check" },
			],
			primary_action_label: __("Allocate"),
			primary_action: (v) => {
				this.call("create_leave_allocation", { data: JSON.stringify(v) })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Leave allocated"), indicator: "green" }); this.go("allocations"); })
					.catch(() => {});
			},
		});
		d.show();
	}

	openAllocation(a) {
		if (!a) return;
		const esc = frappe.utils.escape_html;
		const info = `<div class="ahr-form-grid">
			<div><b>${__("Employee")}:</b> ${esc(a.employee_name || a.employee)}</div>
			<div><b>${__("Leave Type")}:</b> ${esc(a.leave_type || "")}</div>
			<div><b>${__("Period")}:</b> ${this.d(a.from_date)} → ${this.d(a.to_date)}</div>
			<div><b>${__("Allocated")}:</b> ${flt(a.total_leaves_allocated)}</div>
			<div><b>${__("Carry Forward")}:</b> ${a.carry_forward ? __("Yes") : "—"}</div></div>`;
		const d = new frappe.ui.Dialog({ title: a.name, size: "large" });
		$(d.body).html(this.panel(__("Leave Allocation"), info) +
			`<div class="ahr-doc-actions"><button class="btn btn-xs btn-danger al-delete">${__("Delete")}</button></div>`);
		$(d.body).find(".al-delete").on("click", () =>
			frappe.confirm(__("Delete this allocation?"), () =>
				this.call("delete_leave_allocation", { name: a.name })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Deleted"), indicator: "red" }); this.go("allocations"); })
					.catch(() => {})));
		d.show();
	}

	// ---- Leave Balances ----
	view_balances() {
		this.$content.html(
			this.what(__("What each employee has left, derived from allocations minus approved leave. Nothing is entered here — it is the answer to \"can they take these days?\".")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Leave Type")}</label><div class="bl-lt"></div></div>
				<div class="ahr-field"><label>${__("As Of")}</label><input type="date" class="bl-date form-control" value="${frappe.datetime.get_today()}"></div>
			</div>
			<div class="ahr-panel bl-list"><div class="ahr-empty">${__("Pick a Leave Type to see balances.")}</div></div>`
		);
		const cols = [
			{ key: "employee_name", label: "Employee" },
			{ key: "allocated", label: "Allocated", num: true },
			{ key: "used", label: "Used", num: true },
			{ key: "remaining", label: "Remaining", num: true },
		];
		const load = () => {
			const lt = ltCtrl.get_value();
			if (!lt) return;
			this.$content.find(".bl-list").html(`<div class="ahr-empty"><i class="fa fa-spinner fa-spin"></i> ${__("Loading")}…</div>`);
			this.call("leave_balances", { leave_type: lt, as_of: this.$content.find(".bl-date").val(), company: this.state.company })
				.then((rows) => {
					this.$content.find(".bl-list").html(
						`<div class="ahr-list-meta">${rows.length} ${__("employees")} · ${frappe.utils.escape_html(lt)}</div>` +
							this.table(cols, rows));
				});
		};
		const ltCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Leave Type", placeholder: __("Leave Type") },
			parent: this.$content.find(".bl-lt")[0], render_input: true, only_input: true,
		});
		ltCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".bl-date").on("change", load);
	}

	// ---- Leave Types ----
	view_leavetypes() {
		this.$content.html(
			`<div class="ahr-filters"><button class="btn btn-primary btn-sm lt-new"><i class="fa fa-plus"></i> ${__("New Leave Type")}</button></div>
			<div class="ahr-panel lt-list"></div>`
		);
		const yesno = (v) => (v ? __("Yes") : "—");
		const load = () => {
			this.call("list_leave_types").then((rows) => {
				this.$content.find(".lt-list").html(
					this.table(
						[{ key: "name", label: "Leave Type" },
						 { key: "is_lwp", label: "Without Pay (LWP)", render: yesno },
						 { key: "is_carry_forward", label: "Carry Forward", render: yesno },
						 { key: "is_compensatory", label: "Compensatory", render: yesno },
						 { key: "max_leaves_allowed", label: "Max Days", num: true }],
						rows, { id: "name" }
					)
				);
				this.$content.find(".lt-list tr.clickable").on("click", (e) =>
					this.editLeaveType(rows.find((r) => r.name === $(e.currentTarget).data("id"))));
			});
		};
		this.$content.find(".lt-new").on("click", () => this.editLeaveType({}));
		load();
	}

	editLeaveType(t) {
		t = t || {};
		const d = new frappe.ui.Dialog({
			title: t.name ? __("Edit Leave Type") : __("New Leave Type"),
			fields: [
				{ fieldname: "leave_type_name", label: __("Leave Type Name"), fieldtype: "Data", reqd: 1, default: t.name },
				{ fieldname: "max_leaves_allowed", label: __("Max Days Allowed"), fieldtype: "Int", default: t.max_leaves_allowed },
				{ fieldtype: "Section Break" },
				{ fieldname: "is_lwp", label: __("Leave Without Pay (LWP)"), fieldtype: "Check", default: t.is_lwp },
				{ fieldname: "is_carry_forward", label: __("Carry Forward"), fieldtype: "Check", default: t.is_carry_forward },
				{ fieldname: "is_compensatory", label: __("Is Compensatory"), fieldtype: "Check", default: t.is_compensatory },
				...(t.name ? [{ fieldname: "del", label: __("Delete this leave type"), fieldtype: "Check" }] : []),
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				if (v.del) {
					this.call("delete_leave_type", { name: t.name }).then(() => { d.hide(); this.go("leavetypes"); }).catch(() => {});
					return;
				}
				this.call("save_leave_type", { data: JSON.stringify(v), old_name: t.name || null })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Saved"), indicator: "green" }); this.go("leavetypes"); })
					.catch(() => {});
			},
		});
		d.show();
	}

	// ---- Absence Reasons (Settings) ----
	view_reasons() {
		this.$content.html(
			`<div class="ahr-filters"><button class="btn btn-primary btn-sm rsn-new"><i class="fa fa-plus"></i> ${__("New Reason")}</button></div>
			<div class="ahr-panel rsn-list"></div>`
		);
		const load = () => {
			this.call("list_absence_reasons").then((rows) => {
				this.$content.find(".rsn-list").html(
					this.table(
						[{ key: "reason", label: "Reason" },
						 { key: "is_active", label: "Active", render: (v) => (v ? __("Yes") : __("No")) }],
						rows, { id: "name" }
					)
				);
				this.$content.find(".rsn-list tr.clickable").on("click", (e) =>
					this.editReason(rows.find((r) => r.name === $(e.currentTarget).data("id"))));
			});
		};
		this.$content.find(".rsn-new").on("click", () => this.editReason({}));
		load();
	}

	editReason(r) {
		r = r || {};
		const d = new frappe.ui.Dialog({
			title: r.name ? __("Edit Reason") : __("New Reason"),
			fields: [
				{ fieldname: "reason", label: __("Reason"), fieldtype: "Data", reqd: 1, default: r.reason },
				{ fieldname: "is_active", label: __("Active"), fieldtype: "Check", default: r.name ? r.is_active : 1 },
				...(r.name ? [{ fieldname: "del", label: __("Delete this reason"), fieldtype: "Check" }] : []),
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				if (v.del) {
					this.call("delete_absence_reason", { name: r.name }).then(() => { d.hide(); this.go("reasons"); });
					return;
				}
				this.call("save_absence_reason", { reason: v.reason, is_active: v.is_active ? 1 : 0, old_name: r.name || null })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Saved"), indicator: "green" }); this.go("reasons"); });
			},
		});
		d.show();
	}

	view_timesheets() {
		this.renderFilterList(
			"list_timesheets",
			[
				{ key: "employee_name", label: "Employee" },
				{ key: "start_date", label: "From", date: true },
				{ key: "end_date", label: "To", date: true },
				{ key: "total_hours", label: "Hours", num: true },
				{ key: "status", label: "Status" },
			],
			{ employee: true }
		);
	}

	view_slips() {
		const STATUSES = ["", "Draft", "Submitted", "Posted", "Paid", "Cancelled"];
		this.$content.html(
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="slip-f-emp"></div></div>
				<div class="ahr-field"><label>${__("Status")}</label>
					<select class="slip-f-status form-control">
						${STATUSES.map((s) => `<option value="${s}">${s ? __(s) : __("All Statuses")}</option>`).join("")}
					</select></div>
				<div class="ahr-field"><label>${__("From")}</label><input type="date" class="slip-f-from"></div>
				<div class="ahr-field"><label>${__("To")}</label><input type="date" class="slip-f-to"></div>
			</div>
			<div class="ahr-panel ahr-list"></div>`
		);

		const cols = [
			{ key: "name", label: "Slip" },
			{ key: "employee_name", label: "Employee" },
			{ key: "period", label: "Period", render: (_, r) => `${this.d(r.start_date)} → ${this.d(r.end_date)}` },
			{ key: "gross_pay", label: "Gross", money: true },
			{ key: "total_deduction", label: "Deductions", money: true },
			{ key: "net_pay", label: "Net", money: true },
			{ key: "status", label: "Status", render: (v) => this.slipStatus(v) },
		];

		let empCtrl = null;
		const load = () => {
			this.call("list_salary_slips", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
				status: this.$content.find(".slip-f-status").val() || null,
				from_date: this.$content.find(".slip-f-from").val() || null,
				to_date: this.$content.find(".slip-f-to").val() || null,
			}).then((rows) => {
				const net = rows.reduce((a, r) => a + flt(r.net_pay), 0);
				this.$content.find(".ahr-list").html(
					`<div class="ahr-list-meta">${rows.length} ${__("salary slips")} &middot; ${__("Total Net")}: ${this.money(net)}</div>` +
						this.table(cols, rows, { id: "name" })
				);
				this.$content.find(".ahr-list tr.clickable").on("click", (e) => this.openSlip($(e.currentTarget).data("id")));
			});
		};
		this._listReload = load;

		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".slip-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".slip-f-status, .slip-f-from, .slip-f-to").on("change", load);
		load();
	}

	renderFilterList(method, cols, flags, onClick, idKey) {
		const fdates = flags.dates
			? `<div class="ahr-field"><label>${__("From")}</label><input type="date" class="ahr-f-from"></div>
			   <div class="ahr-field"><label>${__("To")}</label><input type="date" class="ahr-f-to"></div>`
			: "";
		const femp = flags.employee
			? `<div class="ahr-field"><label>${__("Employee")}</label><input type="text" class="ahr-f-emp" placeholder="HR-EMP-..."></div>`
			: "";
		this.$content.html(
			`<div class="ahr-filters">${fdates}${femp}</div>
			<div class="ahr-panel ahr-list"></div>`
		);
		const load = () => {
			const args = { company: this.state.company };
			if (flags.dates) {
				args.from_date = this.$content.find(".ahr-f-from").val();
				args.to_date = this.$content.find(".ahr-f-to").val();
			}
			if (flags.employee) args.employee = this.$content.find(".ahr-f-emp").val();
			this.call(method, args).then((rows) => {
				this.$content.find(".ahr-list").html(this.table(cols, rows, idKey ? { id: idKey } : {}));
				if (onClick && idKey)
					this.$content.find(".ahr-list tr.clickable").on("click", (e) =>
						onClick($(e.currentTarget).data("id"))
					);
			});
		};
		this._listReload = load;
		this.$content.find(".ahr-f-from, .ahr-f-to").on("change", load);
		this.$content.find(".ahr-f-emp").on("change", load).on("keyup", frappe.utils.debounce(load, 350));
		load();
	}

	openSlip(name) {
		this.call("get_salary_slip", { name }).then((s) => {
			const e = this.table(
				[{ key: "salary_component", label: "Component" },
				 { key: "amount", label: "Amount", money: true }],
				s.earnings.map((x) => ({ ...x, salary_component: x.salary_component + (x.stat ? " *" : "") }))
			);
			const d = this.table(
				[{ key: "salary_component", label: "Component" },
				 { key: "amount", label: "Amount", money: true }],
				s.deductions
			);
			const esc = frappe.utils.escape_html;
			const info = `<div class="ahr-form-grid">
				<div><b>${__("Slip")}:</b> ${esc(s.name)}</div>
				<div><b>${__("Employee")}:</b> ${esc(s.employee_name || "")}</div>
				<div><b>${__("Period")}:</b> ${this.d(s.start_date)} → ${this.d(s.end_date)}</div>
				<div><b>${__("Status")}:</b> ${this.slipStatus(s.status)}</div></div>`;
			const tot = `<div class="ahr-form-grid">
				<div><b>${__("Taxable Income")}:</b> ${this.money(s.taxable_income)}</div>
				<div><b>${__("Gross")}:</b> ${this.money(s.gross_pay)}</div>
				<div><b>${__("Deductions")}:</b> ${this.money(s.total_deduction)}</div>
				<div><b>${__("Net Pay")}:</b> ${this.money(s.net_pay)}</div></div>`;
			// §21 — where this slip stands in the ledger, stated without overclaiming.
			// A slip is Posted because a submitted accrual exists and Paid because a
			// submitted payment exists; nothing here is a stored status.
			const vbtn = (v) => `<button class="btn btn-xs btn-default slip-voucher" data-v="${esc(v)}">
				<i class="fa fa-book"></i> ${esc(v)}</button>`;
			const acctBody = (!s.journal_entry && !s.payment_entry)
				? `<div class="ahr-fin-line todo"><i class="fa fa-circle-o"></i>
						<span>${__("Accounting has not yet been posted for this slip.")}</span></div>`
				: `<div class="ahr-fin-grid">
					<div class="ahr-fin-col"><h6><i class="fa fa-book"></i> ${__("Accrual")}</h6>
						${s.journal_entry
							? `<div class="ahr-fin-line ok"><i class="fa fa-check-circle"></i>
									<span>${__("Posted to the general ledger")}</span></div>
								<div class="ahr-fin-vouchers">${vbtn(s.journal_entry)}</div>`
							: `<div class="ahr-fin-line todo"><i class="fa fa-circle-o"></i>
									<span>${__("Not posted")}</span></div>`}
					</div>
					<div class="ahr-fin-col"><h6><i class="fa fa-money"></i> ${__("Payment")}</h6>
						${s.payment_entry
							? `<div class="ahr-fin-line ok"><i class="fa fa-check-circle"></i>
									<span>${__("Paid")}</span></div>
								<div class="ahr-fin-vouchers">${vbtn(s.payment_entry)}</div>`
							: `<div class="ahr-fin-line warn"><i class="fa fa-exclamation-circle"></i>
									<span>${__("Accounting posted. Payment not yet completed.")}</span></div>`}
					</div>
				</div>`;

			const dlg = new frappe.ui.Dialog({ title: `${s.employee_name} · ${this.d(s.start_date)} → ${this.d(s.end_date)}`, size: "large" });
			$(dlg.body).html(
				this.panel(__("Salary Slip"), info) +
					this.panel({ title: __("Accounting"), icon: "fa-university",
						subtitle: __("What this slip has produced in the general ledger.") }, acctBody) +
					this.panel(__("Earnings"), e) + this.panel(__("Deductions"), d) + this.panel(__("Totals"), tot)
			);
			$(dlg.body).find(".slip-voucher").on("click", (ev) =>
				this.viewVoucher($(ev.currentTarget).data("v")));
			dlg.set_primary_action(__("Print / PDF"), () => {
				const url =
					"/printview?doctype=Isoft Salary Slip&name=" +
					encodeURIComponent(name) +
					"&format=" +
					encodeURIComponent("Recibo de Vencimento") +
					"&trigger_print=1";
				window.open(url, "_blank");
			});
			const je = s.journal_entry, pe = s.payment_entry;
			const locked = !!(je || pe); // accounted for: cannot cancel or delete until JE/Payment removed
			$(dlg.body).append(
				`<div class="ahr-doc-actions">
					${locked ? `<span class="ahr-lock-note">${pe ? __("Paid — cancel the Payment Entry first; that reverses its ledger entries.") : __("Posted — cancel the Journal Entry first; that reverses its ledger entries.")}</span>` : ""}
					${s.docstatus === 1 ? `<button class="btn btn-xs btn-primary slip-je">${je ? __("View Accrual JE") : __("Create Accrual JE")}</button>` : ""}
					${s.docstatus === 1 ? `<button class="btn btn-xs btn-primary slip-pe">${pe ? __("View Payment") : __("Make Payment")}</button>` : ""}
					${s.docstatus === 1 && !locked ? `<button class="btn btn-xs btn-default slip-cancel">${__("Cancel Slip")}</button>` : ""}
					${!locked ? `<button class="btn btn-xs btn-danger slip-delete">${__("Delete")}</button>` : ""}
				</div>`
			);
			$(dlg.body).find(".slip-je").on("click", () => {
				if (je) return frappe.set_route("Form", "Journal Entry", je);
				this.call("make_journal_entry", { salary_slip: name }).then((j) => {
					frappe.show_alert({ message: __("Journal Entry {0} created", [j]), indicator: "green" });
					frappe.set_route("Form", "Journal Entry", j);
				});
			});
			$(dlg.body).find(".slip-pe").on("click", () => {
				if (pe) return frappe.set_route("Form", "Journal Entry", pe);
				this.paymentDialog(`${__("Make Payment")} · ${s.employee_name}`, (v) => {
					this.call("make_payment_entry", { salary_slip: name, payment_account: v.payment_account, posting_date: v.posting_date }).then((p) => {
						frappe.show_alert({ message: __("Payment Entry {0} created", [p]), indicator: "green" });
						frappe.set_route("Form", "Journal Entry", p);
					});
				});
			});
			$(dlg.body).find(".slip-cancel").on("click", () => {
				this.call("cancel_salary_slip", { name }).then(() => {
					frappe.show_alert({ message: __("Cancelled"), indicator: "orange" });
					dlg.hide();
					this.render();
				});
			});
			$(dlg.body).find(".slip-delete").on("click", () => {
				frappe.confirm(__("Delete this salary slip permanently?"), () => {
					this.call("delete_salary_slip", { name }).then(() => {
						frappe.show_alert({ message: __("Deleted"), indicator: "red" });
						dlg.hide();
						this.render();
					});
				});
			});
			dlg.show();
		});
	}

	// ---- Final Settlement (termination) ----
	view_settlements() {
		this.$content.html(
			this.what(__("The final money owed when somebody leaves: outstanding salary, unused leave, 13th month and any advance still to recover. Prepared by HR or Payroll after the contract is terminated.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="fs-f-emp"></div></div>
				<button class="btn btn-primary btn-sm ahr-new-fs" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Settlement")}</button>
			</div>
			<div class="ahr-panel ahr-list"></div>`
		);
		let empCtrl = null;
		const cols = [
			{ key: "name", label: "Ref" },
			{ key: "employee_name", label: "Employee" },
			{ key: "termination_date", label: "Termination", date: true },
			{ key: "reason_label", label: "Reason", render: (v) => (v ? __(v) : `<span class="text-muted">${__("Not recorded")}</span>`) },
			{ key: "total_gross", label: "Gross", money: true },
			{ key: "net_payable", label: "Net", money: true },
			{ key: "status", label: "Status", render: (v) => this.fsBadge(v) },
		];
		const load = () => {
			this.call("list_settlements", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
			}).then((rows) => {
				this.$content.find(".ahr-list").html(this.table(cols, rows, { id: "name" }));
				this.$content.find(".ahr-list tr.clickable").on("click", (e) =>
					this.openSettlement($(e.currentTarget).data("id"))
				);
			});
		};
		this._listReload = load;
		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".fs-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".ahr-new-fs").on("click", () => this.newSettlement());
		load();
	}

	fsBadge(status) {
		const cls = {
			Draft: "draft", "Pending Approval": "pending", Approved: "approved",
			Posted: "posted", Paid: "paid", Rejected: "rejected", Cancelled: "cancelled",
			Finalised: "paid",
		};
		const s = status || "Draft";
		return `<span class="ahr-badge ${cls[s] || "draft"}">${__(s)}</span>`;
	}

	/* ---- Final Settlement: shared rendering ----------------------------------
	 *
	 * The whole point of the rewrite is that the screen never asserts arithmetic the
	 * engine did not perform. So the client renders exactly what the engine returned —
	 * label, formula, article, amount — and computes nothing of its own. */

	/* Where an amount's authority comes from. Law and company convention are drawn
	 * differently on purpose: presenting a chosen divisor as a statutory rate is the
	 * single most misleading thing this screen used to do. */
	fsBasis(line) {
		const esc = frappe.utils.escape_html;
		const bits = [];
		if (line.article) {
			bits.push(`<span class="ahr-basis law" title="${__("Statutory basis")}">${esc(line.article)}</span>`);
		}
		const rateKind = line.rate_basis_kind;
		if (rateKind === "company" || (!line.article && line.basis_kind === "company")) {
			bits.push(`<span class="ahr-basis company" title="${__("Chosen by this company, not by law")}">${__("Company Calculation Basis")}</span>`);
		} else if (line.basis_kind === "company" && line.article) {
			bits.push(`<span class="ahr-basis company">${__("Company Calculation Basis")}</span>`);
		}
		if (line.basis_kind === "input") {
			bits.push(`<span class="ahr-basis input">${__("Entered by HR")}</span>`);
		}
		if (line.basis_kind === "none" && !line.article) {
			bits.push(`<span class="ahr-basis none">${__("No legal basis recorded")}</span>`);
		}
		if (line.status === "legal_input_required") {
			bits.push(`<span class="ahr-basis alert">${__("LEGAL INPUT REQUIRED")}</span>`);
		} else if (line.status === "verify") {
			bits.push(`<span class="ahr-basis verify">${__("VERIFY")}</span>`);
		}
		return bits.join(" ");
	}

	/* One money line: what it is, the arithmetic that produced it, where the rule comes
	 * from, and the amount. The middle column is the engine's own formula string. */
	fsLine(line, opts = {}) {
		const esc = frappe.utils.escape_html;
		const neg = line.sign < 0;
		return `<tr class="${opts.total ? "fs-total" : ""} ${line.status !== "ok" ? "fs-flagged" : ""}">
			<td>
				<div class="fs-line-label">${esc(line.label)}</div>
				${line.note ? `<div class="fs-line-note">${esc(line.note)}</div>` : ""}
			</td>
			<td class="fs-line-calc">${line.formula ? esc(line.formula) : `<span class="text-muted">—</span>`}</td>
			<td class="fs-line-basis">${this.fsBasis(line)}</td>
			<td class="num">${neg ? "−" : ""}${this.money(line.amount)}</td>
		</tr>`;
	}

	/* A whole section of the settlement, or an explicit empty state saying why it is
	 * empty — "Not applicable for the selected termination reason" is information, and
	 * a blank row is not. */
	fsSection(title, lines, opts = {}) {
		if (!lines.length) {
			return this.subsection(title,
				`<div class="ahr-empty-inline">${opts.empty || __("Nothing in this section.")}</div>`);
		}
		return this.subsection(title,
			`<table class="ahr-table fs-breakdown"><thead><tr>
				<th>${__("Description")}</th><th>${__("Calculation")}</th>
				<th>${__("Legal basis")}</th><th class="num">${__("Amount")}</th>
			</tr></thead><tbody>${lines.map((l) => this.fsLine(l)).join("")}</tbody></table>`,
			{ subtitle: opts.subtitle });
	}

	/* Warnings, verification markers and blocking gaps. Never collapsed: a flag that
	 * says an amount cannot be determined is not a footnote. */
	fsFlags(flags) {
		if (!flags || !flags.length) return "";
		const order = { blocking: 0, warning: 1, verify: 2, info: 3 };
		const kind = { blocking: "danger", warning: "warn", verify: "warn", info: "" };
		const label = {
			blocking: __("Cannot be calculated"), warning: __("Warning"),
			verify: __("Legal verification required"), info: __("Note"),
		};
		return flags.slice().sort((a, b) => (order[a.level] || 9) - (order[b.level] || 9))
			.map((f) => this.callout(label[f.level] || __("Note"),
				frappe.utils.escape_html(f.message), { kind: kind[f.level] || "" })).join("");
	}

	/* The complete breakdown, section by section, ending in the net. */
	fsBreakdown(s) {
		const lines = s.lines || [];
		const of = (sec, sign) => lines.filter(
			(l) => l.section === sec && (sign === undefined || (l.sign > 0) === sign));
		const money = (v) => this.money(v);

		const salary = of("salary");
		const leave = of("leave");
		const supplements = of("supplements");
		const compensation = of("compensation");
		const notice = lines.filter((l) => l.section === "notice");
		const deductions = lines.filter((l) => l.section === "other" && l.sign < 0);
		const otherEarnings = lines.filter((l) => l.section === "other" && l.sign > 0);

		const leaveMeta = s.leave
			? __("{0} vested + {1} proportional = {2} days · remuneration base {3}", [
				flt(s.leave.vested_untaken_days), flt(s.leave.proportional_days),
				flt(s.leave.total_days), money(s.leave.remuneration_base)])
			: "";

		return [
			this.fsSection(__("Salary due"), salary),
			this.fsSection(__("Annual leave"), leave, { subtitle: leaveMeta }),
			this.fsSection(__("Annual supplements"), supplements, {
				subtitle: s.supplements
					? __("{0} complete months ({1} to {2})", [s.supplements.months,
						this.d(s.supplements.window_start), this.d(s.supplements.window_end)])
					: "",
			}),
			this.fsSection(__("Compensation / indemnity"), compensation, {
				empty: __("Not applicable for the selected termination reason."),
			}),
			notice.length ? this.fsSection(__("Notice"), notice) : "",
			otherEarnings.length ? this.fsSection(__("Other amounts"), otherEarnings) : "",
			this.fsSection(__("Deductions"), deductions, {
				empty: __("No deductions apply."),
			}),
			`<table class="ahr-table fs-final"><tbody>
				<tr><td>${__("Gross settlement")}</td><td class="num">${money(s.gross)}</td></tr>
				<tr><td>${__("Statutory deductions (INSS, IRT)")}</td><td class="num">−${money(s.statutory_deductions)}</td></tr>
				<tr><td>${__("Other deductions")}</td><td class="num">−${money(flt(s.total_deductions) - flt(s.statutory_deductions))}</td></tr>
				<tr class="fs-total"><td><b>${__("Net final settlement")}</b></td><td class="num"><b>${money(s.net)}</b></td></tr>
				${flt(s.shortfall) ? `<tr class="fs-flagged"><td>${__("Deductions not covered (still outstanding)")}</td><td class="num">${money(s.shortfall)}</td></tr>` : ""}
			</tbody></table>`,
		].filter(Boolean).join("");
	}

	// Step 1: pick the employee + termination date, then open the prefilled settlement form.
	newSettlement() {
		const d = new frappe.ui.Dialog({
			title: __("New Final Settlement"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1 },
				{ fieldname: "termination_date", label: __("Termination Date"), fieldtype: "Date", reqd: 1,
				  default: frappe.datetime.get_today() },
			],
			primary_action_label: __("Continue"),
			primary_action: (v) => {
				d.hide();
				frappe.dom.freeze(__("Loading..."));
				this.call("settlement_defaults", { employee: v.employee, termination_date: v.termination_date })
					.then((def) => { frappe.dom.unfreeze(); this.settlementForm(def); })
					.catch((e) => { frappe.dom.unfreeze(); this.fail(__("Could not prepare the settlement"), e); });
			},
		});
		d.show();
	}

	/* The editable settlement form.
	 *
	 * Only the FACTS are editable — dates, the reason, the remuneration, the leave
	 * actually vested. Every legally derived quantity (complete months, seniority,
	 * proportional leave days, the compensation formula) is computed and shown, not
	 * typed. The two that may be overridden demand a reason, which is stamped with the
	 * user and the time. */
	settlementForm(f, existingName) {
		const esc = frappe.utils.escape_html;
		const num = (k, v, step) =>
			`<input type="number" class="form-control fs-in" data-k="${k}" step="${step || "0.01"}" value="${flt(v)}">`;
		const dt = (k, v) => `<input type="date" class="form-control fs-in" data-k="${k}" value="${v || ""}">`;
		const chk = (k, v, label) =>
			`<div class="checkbox"><label><input type="checkbox" class="fs-in" data-k="${k}" ${cint(v) ? "checked" : ""}> ${label}</label></div>`;
		const sel = (k, v, options) =>
			`<select class="form-control fs-in" data-k="${k}">${options.map(
				([val, lbl]) => `<option value="${esc(val)}" ${val === (v || "") ? "selected" : ""}>${esc(lbl)}</option>`
			).join("")}</select>`;

		const d = new frappe.ui.Dialog({
			title: `${__("Final Settlement")} · ${esc(f.employee_name || f.employee)}`,
			size: "extra-large",
		});

		const reasonOptions = [["", __("— select the reason —")]].concat(
			(this._fsReasons || []).map((r) => [r.key, __(r.label)]));

		$(d.body).html(`
			<div class="fs-form">
				${this.subsection(__("Employee & termination"), `
					<div class="ahr-form-grid">
						<div><label>${__("Employee")}</label>
							<div class="form-control fs-static">${esc(f.employee_name || f.employee)}</div></div>
						<div><label>${__("Date of joining")}</label>
							<div class="form-control fs-static">${this.d(f.date_of_joining) || "—"}</div></div>
						<div><label>${__("Termination Date")}</label>${dt("termination_date", f.termination_date)}</div>
					</div>
					<div class="ahr-form-grid">
						<div class="fs-wide"><label>${__("Reason for Termination")}</label>
							${sel("reason_key", f.reason_key, reasonOptions)}
							<div class="fs-reason-basis text-muted small"></div></div>
						<div><label>${__("Contract")}</label>
							<div class="form-control fs-static">${esc(f.contract || __("Not linked"))}</div></div>
					</div>
					<div class="ahr-form-grid">
						<div>${chk("fixed_term_under_one_year", f.fixed_term_under_one_year, __("Fixed term of one year or less"))}</div>
						<div><label>${__("Notice required (days)")}</label>${num("notice_required_days", f.notice_required_days, "1")}</div>
						<div><label>${__("Notice given (days)")}</label>
							<input type="number" class="form-control fs-in" data-k="notice_given_days" step="1" value="${f.notice_given_days === "" || f.notice_given_days === null || f.notice_given_days === undefined ? "" : cint(f.notice_given_days)}" placeholder="${__("not recorded")}"></div>
						<div class="fs-nonrenewal">${chk("employer_missed_renewal_notice", f.employer_missed_renewal_notice, __("Employer gave no notice of non-renewal"))}</div>
					</div>`)}

				${this.subsection(__("Remuneration"), `
					<div class="ahr-form-grid">
						<div><label>${__("Base salary")}</label>${num("base", f.base)}</div>
						<div><label>${__("Technical supplement")}</label>${num("technical_supplement", f.technical_supplement)}</div>
						<div><label>${__("Availability supplement")}</label>${num("availability_supplement", f.availability_supplement)}</div>
						<div><label>${__("Meal allowance")}</label>${num("food_allowance", f.food_allowance)}</div>
						<div><label>${__("Transport allowance")}</label>${num("transport_allowance", f.transport_allowance)}</div>
					</div>`, {
					subtitle: __("Artigo 213.º n.º 1 — leave is paid on the base salary plus the technical and availability supplements. Meal and transport are excluded unless the parties agreed otherwise."),
				})}

				${this.subsection(__("Salary period"), `
					<div class="ahr-form-grid">
						<div><label>${__("Period start")}</label>${dt("salary_period_start", f.salary_period_start)}</div>
						<div><label>${__("Period end")}</label>${dt("salary_period_end", f.salary_period_end)}</div>
						<div><label>${__("Days worked")}</label>${num("salary_days_worked", f.salary_days_worked)}</div>
						<div><label>${__("Days in period")}</label>${num("period_days", f.period_days)}</div>
					</div>
					<div class="ahr-form-grid">
						<div><label>${__("Calculation basis")}</label>${sel("salary_method", f.salary_method || "auto", [
							["auto", __("Automatic — full salary for a full period")],
							["full_period", __("Full period (whole monthly remuneration)")],
							["hourly_237_7", __("Statutory hourly formula (artigo 237.º n.º 7)")],
							["company_divisor", __("Company divisor (not statutory)")],
						])}</div>
						<div><label>${__("Company divisor")}</label>${num("salary_days", f.salary_days, "1")}</div>
						<div><label>${__("Weekly hours (Hs)")}</label>${num("weekly_hours", f.weekly_hours)}</div>
						<div><label>${__("Working days per week")}</label>${num("working_days_per_week", f.working_days_per_week, "1")}</div>
					</div>`)}

				${this.subsection(__("Annual leave"), `
					<div class="ahr-form-grid">
						<div><label>${__("Leave right vested")}</label>${sel("leave_vested", f.leave_vested || "Auto", [
							["Auto", __("Automatic (from the dates)")],
							["Yes", __("Yes — artigo 212.º n.os 1 e 2")],
							["No", __("No — artigo 212.º n.º 3")],
						])}</div>
						<div><label>${__("Vested leave not taken (days)")}</label>${num("vested_untaken_days", f.vested_untaken_days)}</div>
						<div><label>${__("Leave rate basis")}</label>${sel("leave_rate_method", f.leave_rate_method || "company_divisor", [
							["company_divisor", __("Company divisor (not statutory)")],
							["hourly_237_7", __("Statutory hourly formula (artigo 237.º n.º 7)")],
						])}</div>
						<div><label>${__("Company divisor")}</label>${num("leave_days", f.leave_days, "1")}</div>
					</div>
					<div class="ahr-form-grid">
						<div class="fs-wide">${chk("leave_base_includes_allowances", f.leave_base_includes_allowances, __("Include meal and transport in the leave base (artigo 213.º n.º 2 — only by agreement)"))}</div>
					</div>
					<div class="fs-derived-leave"></div>`, {
					subtitle: __("Proportional leave is derived from the dates under artigo 212.º — it is not typed."),
				})}

				${this.subsection(__("Annual supplements & seniority"), `
					<div class="ahr-form-grid">
						<div><label>${__("Vacation allowance (% of base)")}</label>${num("ferias_rate", f.ferias_rate)}</div>
						<div><label>${__("Christmas bonus (% of base)")}</label>${num("natal_rate", f.natal_rate)}</div>
						<div><label>${__("Agreed compensation")}</label>${num("agreed_compensation", f.agreed_compensation)}</div>
					</div>
					<div class="fs-derived-months"></div>
					${this.callout(__("Override a derived legal quantity"), `
						<div class="ahr-form-grid">
							<div><label>${__("Complete months")}</label>
								<input type="number" class="form-control fs-in" data-k="supplement_months_override" step="1" value="${f.supplement_months_override || ""}" placeholder="${__("derived")}"></div>
							<div><label>${__("Seniority years")}</label>
								<input type="number" class="form-control fs-in" data-k="seniority_years_override" step="1" value="${f.seniority_years_override || ""}" placeholder="${__("derived")}"></div>
							<div class="fs-wide"><label>${__("Reason for the override")}</label>
								<input type="text" class="form-control fs-in" data-k="override_reason" value="${esc(f.override_reason || "")}"></div>
						</div>
						<div class="text-muted small">${__("Complete months and seniority come from the dates under artigos 238.º and 311.º. An override is recorded against your user and the time.")}</div>`,
						{ collapsed: true, hint: __("Only if you must") })}`)}

				${this.subsection(__("Deductions & tax"), `
					<div class="ahr-form-grid">
						<div><label>${__("Compensation tax position")}</label>${sel("compensation_tax_position", f.compensation_tax_position, [
							["verification_required", __("Not verified — no IRT applied, flagged")],
							["exempt_within_lgt_limits", __("Exempt within the LGT limits")],
							["taxable", __("Fully taxable")],
						])}</div>
						<div><label>${__("Outstanding salary advance")}</label>
							<div class="form-control fs-static">${this.money(f.advance_outstanding)}</div></div>
						<div>${chk("recover_advance", f.recover_advance, __("Recover it from this settlement"))}</div>
					</div>`, {
					subtitle: __("IRT incidence on termination compensation is not settled between the Código do IRT and the commentary on Lei n.º 28/20. This is a company position, not a statutory rate."),
				})}

				<div><label>${__("Notes")}</label>
					<textarea class="form-control fs-in" data-k="notes" rows="2">${esc(f.notes || "")}</textarea></div>

				<div class="fs-summary" style="margin-top:14px;"></div>
			</div>`);

		const gather = () => {
			const o = {
				employee: f.employee, employee_name: f.employee_name, company: f.company,
				date_of_joining: f.date_of_joining, contract: f.contract,
				salary_profile: f.salary_profile, advance_outstanding: f.advance_outstanding,
			};
			$(d.body).find(".fs-in").each((_i, el) => {
				const $el = $(el), k = $el.data("k");
				o[k] = el.type === "checkbox" ? ($el.is(":checked") ? 1 : 0) : $el.val();
			});
			return o;
		};

		const render = (o, c) => {
			$(d.body).find(".fs-summary").html(
				this.panel(__("Final Settlement Summary"),
					this.fsFlags(c.flags) + this.fsBreakdown(c) +
					`<div class="text-muted small" style="margin-top:8px;">${__("Payment deadline")}: ` +
					`${c.payment_deadline && c.payment_deadline.due_date ? this.d(c.payment_deadline.due_date) : esc((c.payment_deadline || {}).rule || "")} ` +
					`<span class="ahr-basis law">${esc((c.payment_deadline || {}).article || "")}</span></div>`)
			);
			const leave = c.leave || {};
			$(d.body).find(".fs-derived-leave").html(this.metrics([
				{ label: __("Vested, not taken"), value: `${flt(leave.vested_untaken_days)} ${__("days")}` },
				{ label: __("Proportional"), value: `${flt(leave.proportional_days)} ${__("days")}`,
				  foot: esc(leave.article || "") },
				{ label: __("Total payable"), value: `${flt(leave.total_days)} ${__("days")}` },
				{ label: __("Remuneration base"), value: this.money(leave.remuneration_base) },
			]));
			$(d.body).find(".fs-derived-months").html(this.metrics([
				{ label: __("Complete months"), value: (c.supplements || {}).months,
				  foot: "Artigo 238.º n.º 3" },
				{ label: __("Seniority"), value: `${c.seniority_years} ${__("years")}`,
				  foot: "Artigo 311.º" },
			]));
			const spec = (this._fsReasons || []).find((r) => r.key === o.reason_key);
			$(d.body).find(".fs-reason-basis").html(spec
				? `<span class="ahr-basis law">${esc(spec.article || __("No compensation article"))}</span> ${esc(spec.note || "")}`
				: __("The reason decides whether compensation is owed. Leaving it empty is not the same as none."));
			$(d.body).find(".fs-nonrenewal").toggle(o.reason_key === "fixed_term_expiry");
			d.get_primary_btn().prop("disabled", false);
		};

		const recalc = () => {
			const o = gather();
			this.call("settlement_preview", { data: JSON.stringify(o) })
				.then((c) => render(o, c))
				.catch((e) => this.fail(__("Could not recalculate the settlement"), e));
		};
		const refreshDays = () => {
			const o = gather();
			if (!o.salary_period_start || !o.salary_period_end) return recalc();
			this.call("settlement_period_days", {
				employee: f.employee, start_date: o.salary_period_start, end_date: o.salary_period_end,
			}).then((n) => { $(d.body).find('.fs-in[data-k="salary_days_worked"]').val(n); recalc(); })
				.catch(() => recalc());
		};
		$(d.body).on("change", ".fs-in", (e) => {
			const k = $(e.currentTarget).data("k");
			if (k === "salary_period_start" || k === "salary_period_end") refreshDays();
			else recalc();
		});

		const start = () => recalc();
		if (this._fsReasons) start();
		else {
			this.call("settlement_legal_reference").then((ref) => {
				this._fsReasons = ref.reasons || [];
				this._fsLegal = ref;
				$(d.body).find('.fs-in[data-k="reason_key"]').html(
					[["", __("— select the reason —")]].concat(
						this._fsReasons.map((r) => [r.key, __(r.label)])
					).map(([v, l]) => `<option value="${esc(v)}" ${v === (f.reason_key || "") ? "selected" : ""}>${esc(l)}</option>`).join(""));
				start();
			}).catch((e) => { this.fail(__("Could not load the legal reference"), e); start(); });
		}

		d.set_primary_action(existingName ? __("Save") : __("Save Draft"), () => {
			const payload = gather();
			const method = existingName ? "update_settlement" : "create_settlement";
			const args = existingName ? { name: existingName, data: JSON.stringify(payload) }
				: { data: JSON.stringify(payload) };
			this.call(method, args).then((name) => {
				d.hide();
				frappe.show_alert({ message: __("Settlement {0} saved", [name]), indicator: "green" });
				this.openSettlement(name);
				if (this._listReload) this._listReload();
			}).catch((e) => this.fail(__("Could not save the settlement"), e));
		});
		d.show();
	}

	openSettlement(name) {
		this.call("get_settlement", { name }).then((s) => {
			const esc = frappe.utils.escape_html;
			const actions = s.actions || [];
			const info = `<div class="ahr-form-grid">
				<div><b>${__("Ref")}:</b> ${esc(s.name)}</div>
				<div><b>${__("Employee")}:</b> ${esc(s.employee_name || "")}</div>
				<div><b>${__("Date of joining")}:</b> ${this.d(s.date_of_joining)}</div>
				<div><b>${__("Termination date")}:</b> ${this.d(s.termination_date)}</div>
				<div><b>${__("Reason")}:</b> ${esc(s.reason_label || __("Not recorded"))}
					${s.compensation_article ? `<span class="ahr-basis law">${esc(s.compensation_article)}</span>` : ""}</div>
				<div><b>${__("Service")}:</b> ${cint(s.seniority_years)} ${__("years")} · ${cint(s.supplement_months)} ${__("complete months")}</div>
				<div><b>${__("Status")}:</b> ${this.fsBadge(s.status)}</div>
				<div><b>${__("Due")}:</b> ${s.settlement_due_date ? this.d(s.settlement_due_date) : "—"}
					${s.payment_deadline_article ? `<span class="ahr-basis law">${esc(s.payment_deadline_article)}</span>` : ""}</div>
				${s.override_by ? `<div><b>${__("Override by")}:</b> ${esc(s.override_by)} · ${esc(String(s.override_at || ""))}</div>` : ""}
				${s.rejection_reason ? `<div class="fs-wide"><b>${__("Rejected")}:</b> ${esc(s.rejection_reason)}</div>` : ""}
			</div>`;

			const dlg = new frappe.ui.Dialog({
				title: `${s.employee_name} · ${__("Final Settlement")}`, size: "extra-large",
			});
			$(dlg.body).html(
				this.panel(__("Employee & termination"), info) +
				this.fsFlags(s.flags) +
				this.panel(__("Final Settlement Summary"),
					this.fsBreakdown({
						lines: s.lines, leave: s.leave, supplements: s.supplements,
						gross: s.total_gross, statutory_deductions: flt(s.inss_amount) + flt(s.irt_amount),
						total_deductions: s.total_deductions, net: s.net_payable,
						shortfall: s.shortfall,
					}))
			);

			const btn = (cls, label, kind) =>
				`<button class="btn btn-xs btn-${kind || "default"} ${cls}">${label}</button>`;
			$(dlg.body).append(`<div class="ahr-doc-actions">
				${btn("fs-pdf", `<i class="fa fa-file-pdf-o"></i> ${__("PDF")}`)}
				${btn("fs-xlsx", `<i class="fa fa-file-excel-o"></i> ${__("Excel")}`)}
				${actions.includes("edit") ? btn("fs-edit", __("Edit")) : ""}
				${actions.includes("recalculate") && s.is_legacy ? btn("fs-recalc", __("Recalculate under Lei 12/23"), "warning") : ""}
				${actions.includes("submit_for_approval") ? btn("fs-approval", __("Send for approval"), "primary") : ""}
				${actions.includes("approve") ? btn("fs-approve", __("Approve"), "primary") : ""}
				${actions.includes("reject") ? btn("fs-reject", __("Reject"), "danger") : ""}
				${actions.includes("cancel") ? btn("fs-cancel", __("Cancel")) : ""}
				${actions.includes("delete") ? btn("fs-delete", __("Delete"), "danger") : ""}
			</div>`);

			const after = (msg, colour) => {
				frappe.show_alert({ message: msg, indicator: colour || "green" });
				dlg.hide();
				if (this._listReload) this._listReload();
			};
			const act = (method, args, msg, colour) =>
				this.call(method, Object.assign({ name }, args || {}))
					.then(() => after(msg, colour))
					.catch((e) => this.fail(msg, e));

			const dl = (fmt) => {
				frappe.dom.freeze(__("Exporting..."));
				this.call("export_settlement", { name, file_format: fmt })
					.then((res) => { frappe.dom.unfreeze(); this._downloadB64(res); })
					.catch((e) => { frappe.dom.unfreeze(); this.fail(__("Export failed"), e); });
			};
			$(dlg.body).find(".fs-pdf").on("click", () => dl("pdf"));
			$(dlg.body).find(".fs-xlsx").on("click", () => dl("excel"));
			$(dlg.body).find(".fs-edit").on("click", () => {
				dlg.hide();
				this.settlementForm(Object.assign({}, s), name);
			});
			$(dlg.body).find(".fs-recalc").on("click", () => {
				this.call("recalculate_settlement", { name }).then((r) => {
					frappe.confirm(
						__("Recalculating restates this settlement under Lei n.º 12/23. Gross goes from {0} to {1}. Continue?",
							[this.money(r.before), this.money(r.after)]),
						() => act("recalculate_settlement", { confirm: 1 }, __("Recalculated")));
				}).catch((e) => this.fail(__("Could not recalculate"), e));
			});
			$(dlg.body).find(".fs-approval").on("click", () =>
				act("submit_settlement_for_approval", {}, __("Sent for approval")));
			$(dlg.body).find(".fs-approve").on("click", () =>
				frappe.confirm(__("Approve this final settlement?"),
					() => act("approve_settlement", {}, __("Approved"))));
			$(dlg.body).find(".fs-reject").on("click", () => {
				frappe.prompt({ fieldname: "reason", label: __("Why is it being rejected?"),
					fieldtype: "Small Text", reqd: 1 },
					(v) => act("reject_settlement", { reason: v.reason }, __("Rejected"), "orange"),
					__("Reject settlement"), __("Reject"));
			});
			$(dlg.body).find(".fs-cancel").on("click", () =>
				frappe.confirm(__("Cancel this settlement?"),
					() => act("cancel_settlement", {}, __("Cancelled"), "orange")));
			$(dlg.body).find(".fs-delete").on("click", () =>
				frappe.confirm(__("Delete this settlement permanently?"),
					() => act("delete_settlement", {}, __("Deleted"), "red")));
			dlg.show();
		}).catch((e) => this.fail(__("Could not open the settlement"), e));
	}

	// ---- Salary Profiles ----
	view_profiles() {
		this.$content.html(
			this.what(__("The salary each employee is paid, effective-dated. Use this ONLY for a first salary or an authorised historical correction — for a normal increase use Requests &amp; Approvals → Salary Changes, which closes the old profile and opens the new one for you.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Search")}</label>
					<input type="text" class="ahr-prof-search" placeholder="${__("Name or ID")}"></div>
				<button class="btn btn-primary btn-sm ahr-new-prof" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Profile")}</button>
			</div>
			<div class="ahr-panel ahr-prof-list"></div>`
		);
		const load = (search) =>
			this.call("list_salary_profiles", { company: this.state.company, search }).then((rows) => {
				this.$content.find(".ahr-prof-list").html(
					this.table(
						[
							{ key: "employee_name", label: "Employee" },
							{ key: "from_date", label: "From", date: true },
							{ key: "base", label: "Base", money: true },
							{ key: "food_allowance", label: "Food", money: true },
							{ key: "transport_allowance", label: "Transport", money: true },
						],
						rows,
						{ id: "name" }
					)
				);
				this.$content.find(".ahr-prof-list tr.clickable").on("click", (e) => {
					const row = rows.find((r) => r.name === $(e.currentTarget).data("id"));
					this.editProfile(row);
				});
			});
		this.$content.find(".ahr-new-prof").on("click", () => this.editProfile({}));
		this.$content.find(".ahr-prof-search")
			.on("keyup", frappe.utils.debounce((e) => load(e.currentTarget.value), 300));
		load();
	}

	editProfile(p) {
		p = p || {};
		const d = new frappe.ui.Dialog({
			title: p.name ? __("Edit Salary Profile") : __("New Salary Profile"),
			fields: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee", reqd: 1, default: p.employee },
				{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: p.from_date || frappe.datetime.month_start() },
				{ fieldtype: "Column Break" },
				{ fieldname: "base", label: __("Base Salary"), fieldtype: "Currency", reqd: 1, default: p.base },
				{ fieldname: "food_allowance", label: __("Food Allowance"), fieldtype: "Currency", default: p.food_allowance },
				{ fieldname: "transport_allowance", label: __("Transport Allowance"), fieldtype: "Currency", default: p.transport_allowance },
				{ fieldname: "family_allowance", label: __("Family Allowance"), fieldtype: "Currency", default: p.family_allowance },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => {
				this.call("save_salary_profile", { data: JSON.stringify({ ...v, name: p.name }) }).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Saved"), indicator: "green" });
					this.go("profiles");
				});
			},
		});
		d.show();
		if (p.employee) this.loadSalaryHistory(d, p.employee);
	}

	// Show the salary-change log for an employee inside the profile dialog.
	loadSalaryHistory(dialog, employee) {
		this.call("list_salary_history", { employee }).then((rows) => {
			const $h = $(`<div class="ahr-panel" style="margin-top:14px;"></div>`).appendTo(dialog.body);
			$h.html(
				`<h5>${__("Salary History")}</h5>` +
					this.table(
						[
							{ key: "change_date", label: "When", render: (v) => frappe.datetime.str_to_user(v) },
							{ key: "change_type", label: "Change", render: (v) => __(v || "") },
							{ key: "base", label: "Base", money: true },
							{ key: "food_allowance", label: "Food", money: true },
							{ key: "transport_allowance", label: "Transport", money: true },
							{ key: "changed_by", label: "By" },
						],
						rows
					)
			);
		});
	}

	// ---- Payroll Entries ----
	view_payroll() {
		// Ask the server what this user may do before drawing the screen.
		//
		// Preparing a payroll and completing its accounting are different jobs held by
		// different roles. The preparer's panel resolves the payroll period on load
		// through an endpoint that requires `payroll.preview` — which a Payroll Finance
		// Approver does not hold — so Finance opened this screen into a permission
		// error dialog and could not reach the payroll run they were there to post.
		//
		// This is not an authorisation decision in JavaScript: every action still
		// re-checks on the server. It only stops the screen offering, and silently
		// attempting, work the user is going to be refused.
		this.action("payroll_capabilities")
			.then((caps) => this._renderPayroll(caps || {}))
			.catch(() => this._renderPayroll({}));
	}

	_renderPayroll(caps) {
		const esc = frappe.utils.escape_html;
		this._payrollCaps = caps;
		this._excluded = new Set();
		const opts = (this.state.companies || [])
			.map((c) => `<option value="${esc(c)}" ${c === this.state.company ? "selected" : ""}>${esc(c)}</option>`)
			.join("");
		this.$content.html(
			(caps.preview ? this.panel(
				{ title: __("New Payroll Entry"), icon: "fa-play-circle",
					subtitle: __("Choose the period and scope, check readiness, then prepare the run. Preparing creates draft slips — nothing is paid until the run is approved and posted.") },
				`<div class="ahr-form-grid">
					<div class="ahr-field"><label>${__("Company")}</label><select class="pe-company"><option value="">--</option>${opts}</select></div>
					<div class="ahr-field"><label>${__("Month")}</label><input type="month" class="pe-month"></div>
					<div class="ahr-field"><label>${__("Period")}</label><input type="text" class="pe-period" readonly tabindex="-1"></div>
					<div class="ahr-field"><label>${__("Department")}</label><select class="pe-dept"><option value="">${__("All")}</option></select></div>
					<div class="ahr-field"><label>${__("Branch")}</label><select class="pe-branch"><option value="">${__("All")}</option></select></div>
					<div class="ahr-field"><label>${__("Designation")}</label><select class="pe-desig"><option value="">${__("All")}</option></select></div>
				</div><br>
				<label style="margin-right:16px;"><input type="checkbox" class="pe-valatt"> ${__("Validate Attendance")}</label>
				<label style="margin-right:16px;"><input type="checkbox" class="pe-timesheet"> ${__("Based on Timesheet")}</label><br><br>
				<button class="btn btn-primary btn-sm pe-preview"><i class="fa fa-eye"></i> ${__("Preview")}</button>
				<button class="btn btn-default btn-sm pe-readiness"><i class="fa fa-check-circle-o"></i> ${__("Payroll Readiness")}</button>
				<button class="btn btn-default btn-sm pe-prodready"><i class="fa fa-server"></i> ${__("Production Readiness")}</button>`
			) : this.panel(
				{ title: __("Payroll runs"), icon: "fa-university",
					subtitle: __("Open a run below to complete its accounting and payment.") },
				`<div class="ahr-callout">${__("Preparing a payroll is done by Payroll Officer or HR. Your role completes the accounting: open an approved run below to post it, release it, pay it and close it.")}</div>`)) +
				`<div class="ahr-readiness-wrap"></div>` +
				`<div class="ahr-preview-wrap"></div>` +
				this.panel(
					{ title: __("History"), icon: "fa-history",
						subtitle: __("Every payroll run for this company, and where each one stopped.") },
					`<div class="ahr-filters">
						<div class="ahr-field"><label>${__("From")}</label><input type="date" class="pe-f1"></div>
						<div class="ahr-field"><label>${__("To")}</label><input type="date" class="pe-f2"></div>
					</div>
					<div class="pe-list"></div>`
				)
		);

		const list = () => {
			this.call("list_payroll_entries", {
				company: this.$content.find(".pe-company").val() || this.state.company,
				from_date: this.$content.find(".pe-f1").val(),
				to_date: this.$content.find(".pe-f2").val(),
			}).then((rows) => {
				this.$content.find(".pe-list").html(
					this.table(
						[
							{ key: "name", label: "Entry" },
							{ key: "start_date", label: "From", date: true },
							{ key: "end_date", label: "To", date: true },
							{ key: "number_of_employees", label: "Employees", num: true },
							{ key: "total_net_pay", label: "Net Pay", money: true },
							// The lifecycle state, not "were slips submitted" — that is what tells
							// HR whether a run is waiting for approval, posting or payment.
							{ key: "status", label: "Status", render: (v) => `<span class="ahr-badge ${(v || "Draft").toLowerCase().replace(/ /g, "-")}">${frappe.utils.escape_html(__(v || "Draft"))}</span>` },
							{ key: "approved_by", label: "Approved By" },
						],
						rows,
						{ id: "name" }
					)
				);
				this.$content.find(".pe-list tr.clickable").on("click", (e) =>
					this.openPayrollEntry($(e.currentTarget).data("id"))
				);
			});
		};

		const loadOpts = (company) => {
			this.call("get_filter_options", { company }).then((o) => {
				const fill = (sel, items) => {
					const $s = this.$content.find(sel);
					$s.find("option:not(:first)").remove();
					(items || []).forEach((x) =>
						$s.append(`<option value="${esc(x)}">${esc(x)}</option>`)
					);
				};
				fill(".pe-dept", o.departments);
				fill(".pe-branch", o.branches);
				fill(".pe-desig", o.designations);
			});
		};

		// Month → period (23→22 cycle). Default to the current month.
		this._pePeriod = null;
		const syncPeriod = () => {
			const month = this.$content.find(".pe-month").val();
			// The period lookup is guarded by payroll.preview on the server. Without
			// that permission the preparer panel is not rendered at all, so there is
			// nothing to resolve — and calling it anyway is what put a permission
			// error in front of Finance before they could open a run.
			if (!month || !caps.preview) return Promise.resolve();
			return this.call("payroll_period_for_month", { month }).then((p) => {
				this._pePeriod = p;
				this.$content.find(".pe-period").val(`${this.d(p.start)} → ${this.d(p.end)}`);
			});
		};
		this.$content.find(".pe-month").val(frappe.datetime.get_today().slice(0, 7));
		syncPeriod();
		this.$content.find(".pe-month").on("change", () => syncPeriod());

		this._refreshHistory = list;
		this.$content.find(".pe-company").val(this.state.company || "");
		this.$content.find(".pe-company").on("change", (e) => {
			loadOpts($(e.currentTarget).val());
			list();
		});
		this.$content.find(".pe-f1, .pe-f2").on("change", list);
		this.$content.find(".pe-preview").on("click", () => this.runPreview());
		this.$content.find(".pe-readiness").on("click", () => this.runReadiness());
		this.$content.find(".pe-prodready").on("click", () => this.runProductionReadiness());
		loadOpts(this.state.company);
		list();
	}


	// ---- Payroll readiness (pre-flight) ----
	runReadiness() {
		const f = this.peFilters();
		if (!f.company) return frappe.msgprint(__("Select a company"));
		if (!f.start_date || !f.end_date) return frappe.msgprint(__("Select a month"));
		frappe.dom.freeze(__("Checking payroll readiness..."));
		this.call("payroll_readiness", f)
			.then((r) => {
				frappe.dom.unfreeze();
				this._readiness = r;
				this.renderReadiness(r);
			})
			.catch(() => frappe.dom.unfreeze());
	}

	renderReadiness(r) {
		const esc = frappe.utils.escape_html;
		const $w = this.$content.find(".ahr-readiness-wrap");
		const tile = (cls, n, label) =>
			`<div class="ahr-ready-tile ${cls}"><div class="n">${n}</div><div class="l">${esc(label)}</div></div>`;

		const badConfig = (r.configuration || []).filter((c) => !c.ok);
		const group = (severity) => (r.summary || []).filter((s) => s.severity === severity);
		const line = (s) =>
			`<li><a href="#" class="rd-code" data-code="${esc(s.code)}">${esc(s.code)} — ${esc(s.label)}</a>
				<span class="ahr-badge">${s.count}</span></li>`;

		const section = (title, items, cls) =>
			items.length
				? `<div class="ahr-ready-sec ${cls}"><h6>${esc(title)}</h6><ul>${items.map(line).join("")}</ul></div>`
				: "";

		const configHtml = badConfig.length
			? `<div class="ahr-ready-sec blocking"><h6>${__("Configuration")}</h6><ul>` +
				badConfig
					.map((c) => `<li>${esc(c.label)} <span class="ahr-badge ${c.ok ? "" : "danger"}">${esc(c.status)}</span></li>`)
					.join("") +
				`</ul></div>`
			: "";

		$w.html(
			this.panel(
				`${__("Payroll Readiness")} — ${this.d(r.start_date)} → ${this.d(r.end_date)}`,
				`<div class="ahr-ready-tiles">
					${tile("ok", r.ready, __("Ready"))}
					${tile("danger", r.blocked, __("Blocked"))}
					${tile("warn", r.warnings, __("Warnings"))}
					${tile("pay", r.payment_blocked, __("Payment blockers"))}
					${tile("", r.total_employees, __("Active employees"))}
				</div>
				${configHtml}
				${section(__("Blocking"), group("BLOCKING"), "blocking")}
				${section(__("Payment"), group("PAYMENT"), "payment")}
				${section(__("Warnings"), group("WARNING"), "warning")}
				<div class="text-muted small" style="margin-top:8px;">${
					r.can_calculate
						? __("Payroll can be calculated.")
						: __("Payroll cannot be calculated until the blocking items are resolved.")
				}</div>`
			)
		);

		$w.find(".rd-code").on("click", (e) => {
			e.preventDefault();
			const code = $(e.currentTarget).data("code");
			const rows = (this._readiness.exceptions || []).filter((x) => x.code === code);
			const html = `<table class="ahr-table"><thead><tr><th>${__("Employee")}</th><th>${__("Detail")}</th></tr></thead><tbody>` +
				rows
					.map(
						(x) =>
							`<tr><td>${esc(x.employee_name || x.employee || "—")}</td><td>${esc(x.message)}</td></tr>`
					)
					.join("") +
				`</tbody></table>`;
			// Deliberately no salary amounts here — readiness answers who is blocked, not
			// what anybody earns.
			const d = new frappe.ui.Dialog({ title: `${code} (${rows.length})`, size: "large" });
			$(d.body).html(html);
			d.show();
		});
	}


	// ---- Production readiness (deployment / cutover) ----
	runProductionReadiness() {
		frappe.dom.freeze(__("Checking production readiness..."));
		this.call("get_production_readiness", { company: this.$content.find(".pe-company").val() })
			.then((r) => {
				frappe.dom.unfreeze();
				this.renderProductionReadiness(r);
			})
			.catch(() => frappe.dom.unfreeze());
	}

	renderProductionReadiness(r) {
		const esc = frappe.utils.escape_html;
		const pill = (st) =>
			`<span class="ahr-badge ${st === "READY" ? "paid" : st === "WARNING" ? "draft" : "cancelled"}">${esc(__(st))}</span>`;

		// Every non-READY check is shown with its owner and the exact corrective action:
		// a cutover list is only useful if it says who has to do what.
		const section = (sec) => {
			const rows = sec.checks
				.filter((c) => c.status !== "READY")
				.map(
					(c) => `<tr>
						<td>${esc(c.label)}</td>
						<td>${pill(c.status)}</td>
						<td>${esc(c.current || "—")}</td>
						<td>${esc(c.owner || "")}</td>
						<td>${esc(c.action || "")}</td></tr>`
				)
				.join("");
			const okCount = sec.checks.filter((c) => c.status === "READY").length;
			return this.panel(
				`${esc(sec.label)} ${pill(sec.status)}`,
				rows
					? `<table class="ahr-table"><thead><tr>
							<th>${__("Check")}</th><th>${__("Status")}</th><th>${__("Current")}</th>
							<th>${__("Owner")}</th><th>${__("Required Action")}</th></tr></thead>
						<tbody>${rows}</tbody></table>
						<div class="text-muted small" style="margin-top:6px;">${__("{0} further check(s) passed.", [okCount])}</div>`
					: `<div class="ahr-empty">${__("All {0} checks passed.", [okCount])}</div>`
			);
		};

		const conflicts = (r.segregation_conflicts || [])
			.map((c) => `<tr><td>${esc(c.code)}</td><td>${esc(c.user)}</td><td>${esc(c.roles.join(", "))}</td></tr>`)
			.join("");

		const d = new frappe.ui.Dialog({
			title: `${__("Production Readiness")} — ${esc(r.company || "")} ${r.status}`,
			size: "extra-large",
		});
		$(d.body).html(
			this.panel(
				__("Overall"),
				`<div class="ahr-ready-tiles">
					<div class="ahr-ready-tile ${r.status === "READY" ? "ok" : r.status === "WARNING" ? "warn" : "danger"}">
						<div class="n">${esc(__(r.status))}</div><div class="l">${__("Overall")}</div></div>
					<div class="ahr-ready-tile danger"><div class="n">${r.blocked}</div><div class="l">${__("Blocked")}</div></div>
					<div class="ahr-ready-tile warn"><div class="n">${r.warnings}</div><div class="l">${__("Warnings")}</div></div>
				</div>`
			) +
				r.sections.map(section).join("") +
				(conflicts
					? this.panel(
							__("Segregation of Duties Conflicts"),
							`<table class="ahr-table"><thead><tr><th>${__("Code")}</th><th>${__("User")}</th><th>${__("Roles")}</th></tr></thead><tbody>${conflicts}</tbody></table>`
					  )
					: "")
		);
		d.show();
	}


	// ---- HR Dashboard ----
	view_hrdash() {
		const esc = frappe.utils.escape_html;
		Promise.all([
			this.call("hr_dashboard", { company: this.state.company }),
			this.call("hr_readiness", { company: this.state.company }),
			this.call("hr_action_queue", { company: this.state.company }),
		]).then(([d, r, q]) => {
			// §28 — the dashboard's job is to answer "what needs HR action today?", and to
			// take you to the screen that clears it. A count you cannot act on is a report.
			const queue = q.items.length
				? `<div class="ahr-queue">${q.items.map((i) =>
					`<button class="ahr-queue-row" data-view="${esc(i.view)}">
						<span class="n">${i.count}</span>
						<span class="l">${esc(i.label)}</span>
						<span class="h">${esc(i.hint || "")}</span>
						<i class="fa fa-angle-right"></i>
					</button>`).join("")}</div>`
				: `<div class="ahr-blank"><i class="fa fa-check-circle-o"></i>
					<h4>${__("Nothing is waiting for HR action.")}</h4>
					<p>${__("No approvals pending, no justifications outstanding, no contracts or probations due, and no reviews open. This list fills itself as work arrives — you do not need to go looking for it.")}</p></div>`;
			const tile = (n, label, cls) =>
				`<div class="ahr-ready-tile ${cls || ""}"><div class="n">${n}</div><div class="l">${esc(label)}</div></div>`;
			const list = (rows, cls) =>
				rows.length
					? `<ul class="ahr-ready-sec-list">${rows
							.map(
								(x) =>
									`<li><span>${esc(x.label)}</span><span class="ahr-badge ${cls}">${x.count}</span>
									<div class="text-muted small">${esc(x.action || "")}</div></li>`
							)
							.join("")}</ul>`
					: `<div class="ahr-empty">${__("Nothing outstanding.")}</div>`;
			const head = (rows) =>
				rows.map((x) => `<tr><td>${esc(x.label)}</td><td class="num">${x.n}</td></tr>`).join("");

			this.$content.html(
				this.what(__("This is the HR operating console. Everything below is work HR does — the department records what employees and managers ask for, then decides it here. Employees and line managers are not required to log in.")) +
				this.panel(
					`${__("What needs HR action today")} <span class="ahr-badge ${q.total ? "cancelled" : "paid"}">${q.total}</span>`,
					queue) +
				this.panel(
					__("HR Dashboard"),
					`<div class="ahr-ready-tiles">
						${tile(d.active_employees, __("Active employees"))}
						${tile(d.new_hires_this_month, __("New hires this month"), "ok")}
						${tile(d.leavers_this_month, __("Leavers this month"))}
						${tile(d.on_leave_today, __("On leave today"), "pay")}
						${tile(d.attendance_exceptions, __("Attendance exceptions"), "warn")}
					</div>
					<div class="ahr-ready-tiles" style="margin-top:12px;">
						${tile(d.contracts_expiring, __("Contracts expiring"), "warn")}
						${tile(d.probations_due, __("Probations due"), "warn")}
						${tile(d.documents_expiring, __("Documents expiring"), "warn")}
						${tile(d.open_advances, __("Open advances"), "pay")}
						${tile(d.pending_approvals, __("Pending approvals"), "danger")}
					</div>`
				) +
					this.panel(
						`${__("HR Readiness")} <span class="ahr-badge ${
							r.status === "READY" ? "paid" : r.status === "WARNING" ? "draft" : "cancelled"
						}">${esc(__(r.status))}</span>`,
						`<div class="ahr-ready-sec blocking"><h6>${__("Blocking")}</h6>${list(r.blockers, "danger")}</div>
						 <div class="ahr-ready-sec warning"><h6>${__("Warnings")}</h6>${list(r.warnings, "")}</div>`
					) +
					this.panel(
						{ title: __("Headcount"), icon: "fa-users",
							subtitle: __("Who is on the payroll today, grouped three ways.") },
						`<div class="ahr-form-grid">
							<div><b>${__("By department")}</b><table class="ahr-table">${head(d.headcount.by_department)}</table></div>
							<div><b>${__("By designation")}</b><table class="ahr-table">${head(d.headcount.by_designation)}</table></div>
							<div><b>${__("By employment type")}</b><table class="ahr-table">${head(d.headcount.by_employment_type)}</table></div>
						</div>`
					)
			);
			this.$content.find(".ahr-queue-row").on("click", (e) =>
				this.go($(e.currentTarget).data("view")));
		});
	}

	// ---- Approval inbox ----
	view_hrinbox() {
		const esc = frappe.utils.escape_html;
		this.call("hr_approval_inbox", { company: this.state.company }).then((rows) => {
			const sources = __("Employment Contracts · Salary Changes · Salary Advances · Bank Change Requests · Leave Applications");
			// §19/§20 — the approver has to be able to see who asked, who typed it and who
			// is expected to decide. Deciding happens on the owning screen, which holds the
			// real workflow buttons; approval logic is never duplicated here.
			const body = rows.length
				? this.panel({ title: __("Needs your decision"), actions: `<span class="ahr-section-tag warn">${__("{0} pending", [rows.length])}</span>`,
					icon: "fa-gavel", subtitle: __("Everything HR has submitted and not yet decided. Open a row to approve or reject it.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Type")}</th><th>${__("Employee")}</th><th>${__("Request")}</th>
						<th>${__("Source")}</th><th>${__("Recorded by")}</th><th>${__("Status")}</th>
						<th>${__("Approver")}</th><th>${__("Open")}</th></tr></thead><tbody>${rows.map((r) =>
						`<tr><td>${esc(r.type)}</td><td>${esc(r.employee_name || r.employee || "")}</td>
						<td>${esc(String(r.detail || ""))}</td>
						<td>${esc(__(r.request_source || "—"))}</td>
						<td>${esc(r.recorded_by || "—")}</td>
						<td><span class="ahr-badge draft">${esc(__(r.status || ""))}</span></td>
						<td class="text-muted small">${esc(r.approver || "")}</td>
						<td><button class="btn btn-xs btn-default inbox-go" data-view="${esc(r.view || "")}"
							data-name="${esc(r.name)}">${__("Decide")}</button></td></tr>`)
						.join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-check-circle-o",
					title: __("Nothing is waiting for an HR decision."),
					body: __("Requests appear here the moment HR submits a contract, a salary change, a salary advance, a bank change or a leave request for approval. Employees do not have to log in for that to happen — HR records the request on their behalf, then an authorised HR person decides it here."),
					who: __("This screen is a view, not a source. To start something that needs approving, use the screen that owns it — Requests & Approvals → Leave Requests, Salary Advances, Salary Changes or Bank Change Requests."),
				});

			// §25 — this screen is a to-do list, so it says so in its title and puts
			// the count where the eye lands first. The urgency is carried by one
			// amber figure, not by colouring the whole screen red (§36).
			this.$content.html(
				this.what(__("Everything HR has submitted and not yet decided, in one list. You do not create records here — you decide them. Each row shows who asked, how the request reached HR, who entered it and which role may approve it.")) +
				this.metrics([
					{ label: __("Waiting for a decision"), value: rows.length, icon: "fa-gavel",
						kind: rows.length ? "warn" : "ok",
						foot: rows.length ? __("Nothing moves until these are decided")
							: __("Nothing is outstanding") },
				]) +
				body +
				this.callout(__("What feeds this screen, and who may decide"),
					`${sources}<br><br>
					 ${__("Sensitive requests cannot be approved by the person who recorded them — salary changes, salary advances, bank changes and contracts each require a second HR person.")}`,
					{ collapsed: true, hint: __("Show") }));

			this.$content.find(".inbox-go").on("click", (e) => {
				const view = $(e.currentTarget).data("view");
				if (view) this.go(view);
			});
		});
	}

	/* ---- Bank change requests (§13) ----------------------------------------
	 * Employees are not required to have a login, so they cannot be the only route to
	 * their own IBAN. HR records the request; only APPROVAL writes the Employee record,
	 * and approving needs a different permission from recording. Redirecting somebody's
	 * salary is the highest-value fraud target in the system, which is why the HR path is
	 * a request rather than a shortcut into the employee record. */
	view_bankchanges() {
		const esc = frappe.utils.escape_html;
		this.call("list_bank_change_requests", { company: this.state.company }).then((rows) => {
			const action = `<button class="btn btn-sm btn-primary bc-new">${__("New Bank Change")}</button>`;
			const body = rows.length
				? this.panel({ title: __("Bank Change Requests"), actions: action, icon: "fa-university",
					subtitle: __("A new account only replaces the employee's IBAN once an HR Manager approves it.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Request")}</th><th>${__("Employee")}</th><th>${__("Current IBAN")}</th>
						<th>${__("Bank")}</th><th>${__("Source")}</th><th>${__("Recorded by")}</th>
						<th>${__("Status")}</th><th></th></tr></thead><tbody>${rows.map((r) =>
						`<tr><td>${esc(r.name)}</td><td>${esc(r.employee_name || r.employee || "")}</td>
						<td>${esc(r.current_iban_masked || "—")}</td>
						<td>${esc(r.bank_name || "—")}</td>
						<td>${esc(__(r.request_source || "—"))}</td>
						<td>${esc(r.requested_by || "—")}</td>
						<td><span class="ahr-badge ${(r.status || "").toLowerCase().replace(/ /g, "-")}">${esc(__(r.status))}</span></td>
						<td>${r.status === "Pending Approval"
							? `<button class="btn btn-xs btn-primary bc-approve" data-name="${esc(r.name)}">${__("Approve")}</button>
							   <button class="btn btn-xs btn-default bc-reject" data-name="${esc(r.name)}">${__("Reject")}</button>`
							: ""}</td></tr>`).join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-university",
					title: __("No bank change has been requested."),
					body: __("Use this when an employee tells HR their salary should be paid into a different account. HR records the request here — the employee does not need a login. The IBAN currently on file is stored masked for comparison and is NOT overwritten when you record the request."),
					who: __("Recorded by HR, approved by an HR Manager. Only the approval writes the new IBAN onto the employee record, and the whole request stays on file afterwards as the audit trail."),
					actions: [{ label: __("New Bank Change"), cls: "bc-new", primary: true }],
				});

			this.$content.html(
				this.what(__("Where an employee's salary is paid. HR records the request and an HR Manager approves it; nothing is written to the employee record until then.")) +
				body +
				`<div class="ahr-note" style="margin-top:12px">
					<b>${__("Why this is a request and not an edit")}</b><br>
					${__("Changing where a salary is paid is the highest-value fraud target in a payroll system. Recording the request and approving it are deliberately two different permissions, and the previous account number is kept, masked, so an approver can see what is being changed from.")}
				</div>`);

			this.$content.find(".bc-new").on("click", () => this.newBankChangeDialog());
			this.$content.find(".bc-approve").on("click", (e) => {
				const name = $(e.currentTarget).data("name");
				frappe.confirm(__("Approve this bank change? The employee's IBAN will be updated."), () =>
					this.call("bank_change_action", { name, action: "approve" })
						.then(() => { frappe.show_alert({ message: __("Approved"), indicator: "green" }); this.render(); })
						.catch(() => {}));
			});
			this.$content.find(".bc-reject").on("click", (e) => {
				const name = $(e.currentTarget).data("name");
				frappe.prompt(
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason for rejection"), reqd: 1 },
					(v) => this.call("bank_change_action", { name, action: "reject", reason: v.reason })
						.then(() => { frappe.show_alert({ message: __("Rejected"), indicator: "orange" }); this.render(); })
						.catch(() => {}),
					__("Reject Bank Change"), __("Reject"));
			});
		});
	}

	newBankChangeDialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Bank Change Request"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("This records a request. The employee's IBAN is not changed until an HR Manager approves it, and the approver is shown the account currently on file.")}</div>` },
				{ fieldtype: "Link", fieldname: "employee", options: "Employee", label: __("Employee"), reqd: 1,
					get_query: () => ({ filters: { status: "Active" } }) },
				{ fieldtype: "Data", fieldname: "new_iban", label: __("New IBAN"), reqd: 1,
					description: __("Country prefix followed by the account number, e.g. AO06...") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Data", fieldname: "bank_name", label: __("Bank") },
				{ fieldtype: "Attach", fieldname: "proof_document", label: __("Proof document"),
					description: __("The written request or bank statement the employee handed over.") },
				{ fieldtype: "Section Break" },
				sourceField("Written request"),
			],
			primary_action_label: __("Record Request"),
			primary_action: (v) => {
				this.call("create_bank_change", {
					employee: v.employee, new_iban: v.new_iban, bank_name: v.bank_name || null,
					proof_document: v.proof_document || null, request_source: v.request_source || null,
				}).then((r) => {
					d.hide();
					frappe.msgprint({
						title: __("Bank change {0} recorded", [r.name]),
						indicator: "green",
						message: __("It is waiting for approval. The account on file is still {0} and will only change when an HR Manager approves this request.",
							[r.current_iban_masked || __("not set")]),
					});
					this.go("bankchanges");
				}).catch(() => {});
			},
		});
		d.show();
	}

	/* ---- Employee documents (§14) -----------------------------------------
	 * The employee hands HR a BI, a certificate or a sick note across a desk. Before
	 * this screen the only uploader was the employee's own self-service session, so a
	 * document belonging to somebody without a login had nowhere to go. */
	view_documents() {
		const esc = frappe.utils.escape_html;
		const STATUSES = ["", "Valid", "Expiring", "Expired", "Superseded"];
		const VERIF = ["", "Pending Verification", "Verified", "Rejected"];
		this.$content.html(
			this.what(__("Every document HR holds for an employee, and when it expires. HR files what it is handed; employees with self-service can also upload their own, which then arrives here awaiting verification.")) +
			`<div class="ahr-filters">
				<div class="ahr-field"><label>${__("Employee")}</label><div class="doc-f-emp"></div></div>
				<div class="ahr-field"><label>${__("Status")}</label>
					<select class="doc-f-status form-control">
						${STATUSES.map((x) => `<option value="${x}">${x ? __(x) : __("All Statuses")}</option>`).join("")}
					</select></div>
				<div class="ahr-field"><label>${__("Verification")}</label>
					<select class="doc-f-verif form-control">
						${VERIF.map((x) => `<option value="${x}">${x ? __(x) : __("All")}</option>`).join("")}
					</select></div>
				<button class="btn btn-primary btn-sm doc-new" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("Add Employee Document")}</button>
			</div>
			<div class="ahr-panel ahr-list"></div>`);

		let empCtrl = null;
		const load = () => {
			this.call("list_employee_documents", {
				company: this.state.company,
				employee: empCtrl ? empCtrl.get_value() || null : null,
				status: this.$content.find(".doc-f-status").val() || null,
				verification_status: this.$content.find(".doc-f-verif").val() || null,
			}).then((rows) => {
				const badge = (s) => `<span class="ahr-badge ${
					{ Valid: "paid", Expiring: "draft", Expired: "cancelled", Superseded: "submitted" }[s] || "draft"
				}">${__(s || "")}</span>`;
				this.$content.find(".ahr-list").html(rows.length
					? `<div class="ahr-list-meta">${rows.length} ${__("documents")}</div>
						<table class="ahr-table"><thead><tr>
							<th>${__("Employee")}</th><th>${__("Type")}</th><th>${__("Number")}</th>
							<th>${__("Expires")}</th><th>${__("Status")}</th><th>${__("Verification")}</th>
							<th>${__("Source")}</th><th>${__("File")}</th><th></th></tr></thead><tbody>${rows.map((r) =>
							`<tr><td>${esc(r.employee_name || r.employee)}</td>
							<td>${esc(r.document_type)}${cint(r.confidential) ? ` <span class="ahr-badge cancelled">${__("Confidential")}</span>` : ""}</td>
							<td>${esc(r.document_number || "—")}</td>
							<td>${r.expiry_date ? this.d(r.expiry_date) : "—"}</td>
							<td>${badge(r.status)}</td>
							<td>${esc(__(r.verification_status || "—"))}</td>
							<td>${cint(r.submitted_by_employee) ? __("Employee upload") : __("Filed by HR")}</td>
							<td>${r.attachment ? `<a href="${esc(r.attachment)}" target="_blank">${__("Open")}</a>` : "—"}</td>
							<td>${r.verification_status === "Pending Verification"
								? `<button class="btn btn-xs btn-primary doc-verify" data-name="${esc(r.name)}">${__("Verify")}</button>
								   <button class="btn btn-xs btn-default doc-reject" data-name="${esc(r.name)}">${__("Reject")}</button>`
								: ""}</td></tr>`).join("")}</tbody></table>`
					: this.blank({
						icon: "fa-folder-open-o",
						title: __("No employee documents have been filed."),
						body: __("This is where HR keeps the paperwork it holds for each employee — identity card, passport, NIF and INSS supporting documents, qualifications, medical certificates and signed contracts. File what you are handed; the system tracks expiry and warns you before a document lapses."),
						who: __("Filed by HR, which counts as verified because HR saw the original. Employees with self-service can upload their own, and those arrive marked Pending Verification for HR to check. Confidential and medical documents are visible only to an HR Manager."),
						actions: [{ label: __("Add Employee Document"), cls: "doc-new", primary: true }],
					}));

				this.$content.find(".doc-new").on("click", () => this.newDocumentDialog());
				this.$content.find(".doc-verify").on("click", (e) =>
					this.call("verify_employee_document", { name: $(e.currentTarget).data("name"), decision: "verify" })
						.then(() => { frappe.show_alert({ message: __("Verified"), indicator: "green" }); load(); })
						.catch(() => {}));
				this.$content.find(".doc-reject").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					frappe.prompt(
						{ fieldtype: "Small Text", fieldname: "reason", label: __("Why is it being rejected?"), reqd: 1 },
						(v) => this.call("verify_employee_document", { name, decision: "reject", reason: v.reason })
							.then(() => { frappe.show_alert({ message: __("Rejected"), indicator: "orange" }); load(); })
							.catch(() => {}),
						__("Reject Document"), __("Reject"));
				});
			});
		};
		this._listReload = load;
		empCtrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", placeholder: __("All Employees") },
			parent: this.$content.find(".doc-f-emp")[0], render_input: true, only_input: true,
		});
		empCtrl.$input.on("change awesomplete-selectcomplete", () => load());
		this.$content.find(".doc-f-status, .doc-f-verif").on("change", load);
		this.$content.find(".doc-new").on("click", () => this.newDocumentDialog());
		load();
	}

	newDocumentDialog() {
		this.call("document_type_options").then((types) => {
			if (!types.length) {
				frappe.msgprint({
					title: __("No document types are configured"),
					indicator: "orange",
					message: __("Document types define what HR may file and which of them are confidential. Create them first under the Isoft Document Type list."),
				});
				return;
			}
			// A type the caller may not file is offered but refused on save, which reads as
			// a bug. Only offer what this user is actually allowed to file.
			const allowed = types.filter((t) => t.allowed);
			const blocked = types.length - allowed.length;
			const d = new frappe.ui.Dialog({
				title: __("Add Employee Document"),
				fields: [
					{ fieldtype: "HTML", options:
						`<div class="ahr-note">${__("File a document the employee handed to HR. Because HR saw the original it is recorded as verified.")}${
							blocked ? `<br>${__("{0} confidential or medical type(s) are hidden — filing those requires an HR Manager.", [blocked])}` : ""
						}</div>` },
					{ fieldtype: "Link", fieldname: "employee", options: "Employee", label: __("Employee"), reqd: 1 },
					{ fieldtype: "Select", fieldname: "document_type", label: __("Document type"), reqd: 1,
						options: allowed.map((t) => t.name).join("\n") },
					{ fieldtype: "Data", fieldname: "document_number", label: __("Document number") },
					{ fieldtype: "Column Break" },
					{ fieldtype: "Date", fieldname: "issue_date", label: __("Issued on") },
					{ fieldtype: "Date", fieldname: "expiry_date", label: __("Expires on"),
						description: __("The system warns HR before it lapses.") },
					{ fieldtype: "Data", fieldname: "issuing_authority", label: __("Issued by") },
					{ fieldtype: "Section Break" },
					{ fieldtype: "Attach", fieldname: "file", label: __("Scan or photo") },
					{ fieldtype: "Small Text", fieldname: "notes", label: __("Notes") },
				],
				primary_action_label: __("File Document"),
				primary_action: (v) => {
					this.call("add_employee_document", {
						employee: v.employee, document_type: v.document_type,
						document_number: v.document_number || null,
						issue_date: v.issue_date || null, expiry_date: v.expiry_date || null,
						issuing_authority: v.issuing_authority || null, notes: v.notes || null,
					}).then((r) => {
						d.hide();
						// The Attach control has already uploaded the file to the site; link
						// it to the document rather than re-uploading its bytes through the
						// HR endpoint, which would store the same file twice.
						if (v.file) {
							frappe.call({
								method: "frappe.client.set_value",
								args: { doctype: "Isoft Employee Document", name: r.name,
									fieldname: "attachment", value: v.file },
							});
						}
						frappe.show_alert({ message: __("Document {0} filed", [r.name]), indicator: "green" });
						this.go("documents");
					}).catch(() => {});
				},
			});
			d.show();
		});
	}

	view_contracts() {
		const esc = frappe.utils.escape_html;
		Promise.all([
			this.call("list_contracts", { company: this.state.company }),
			this.call("contracts_expiring", { company: this.state.company }),
			this.call("probations_due", { company: this.state.company }),
		]).then(([all, expiring, probations]) => {
			const actions =
				`<button class="btn btn-sm btn-primary ct-new">${__("New Contract")}</button>
				 <button class="btn btn-sm btn-default ct-bulk">${__("Bulk Contracts")}</button>`;

			const body = all.length
				? this.panel({ title: __("All contracts"), actions: actions, icon: "fa-file-text",
					subtitle: __("Every employment contract, its type, dates and where it is in the approval flow.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Contract")}</th><th>${__("Employee")}</th><th>${__("Type")}</th>
						<th>${__("Start")}</th><th>${__("End")}</th><th>${__("Status")}</th>
						<th>${__("Probation")}</th><th></th></tr></thead><tbody>${all
						.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.employee_name)}</td>
							<td>${esc(r.contract_type || "")}</td><td>${this.d(r.start_date)}</td>
							<td>${r.is_open_ended ? __("Open-ended") : this.d(r.end_date)}</td>
							<td><span class="ahr-badge ${(r.status || "").toLowerCase().replace(/ /g, "-")}">${esc(__(r.status))}</span></td>
							<td>${esc(__(r.probation_status || "—"))}</td>
							<td>${r.status === "Active" || r.status === "Expiring"
								? `<button class="btn btn-xs btn-default ct-renew" data-n="${esc(r.name)}">${__("Renew")}</button>` : ""}</td></tr>`)
						.join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-file-text-o",
					title: __("No employment contracts have been created yet."),
					body: __("An Employment Contract records the contract type, its effective dates, the probation period and the renewal history for one employee. It does not set pay — that is the Salary Profile."),
					who: __("Created by HR, approved by an HR Manager. For one person use New Contract; for employees who joined before this module existed, use Bulk Contracts."),
					actions: [
						{ label: __("New Contract"), cls: "ct-new", primary: true },
						{ label: __("Bulk Contracts"), cls: "ct-bulk" },
					],
				});

			const expiringPanel = this.panel({ title: __("Expiring soon"), icon: "fa-clock-o",
			tag: expiring.length ? { label: __("{0} within 90 days", [expiring.length]), kind: "warn" } : null,
			subtitle: __("Fixed-term contracts ending in the next 90 days. Renew or let them lapse deliberately.") },
				expiring.length
					? this.table([
						{ key: "employee_name", label: __("Employee") },
						{ key: "contract_type", label: __("Type") },
						{ key: "end_date", label: __("Ends"), date: 1 },
						{ key: "days_left", label: __("Days left"), num: 1 },
					], expiring)
					: `<div class="ahr-empty">${__("No contract expires in the next 90 days.")}</div>`);

			const probationPanel = this.panel({ title: __("Probation reviews due"), icon: "fa-hourglass-half",
			tag: probations.length ? { label: __("{0} due", [probations.length]), kind: "warn" } : null,
			subtitle: __("Probation periods reaching their end. Confirm or end employment before the date passes.") },
				probations.length
					? this.table([
						{ key: "employee_name", label: __("Employee") },
						{ key: "probation_end", label: __("Ends"), date: 1 },
						{ key: "days_left", label: __("Days left"), num: 1 },
					], probations)
					: `<div class="ahr-empty">${__("No probation review is due.")}</div>`);

			this.$content.html(
				this.what(__("Employment Contracts record each employee's contract type, dates, probation and renewals. Draft → Pending Approval → Active; an HR Manager approves.")) +
				body + expiringPanel + probationPanel);

			this.$content.find(".ct-bulk").on("click", () => (location.hash = "#bulkcontracts") || this.go("bulkcontracts"));
			this.$content.find(".ct-new").on("click", () => this.newContractDialog());
			this.$content.find(".ct-renew").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				const d = new frappe.ui.Dialog({
					title: __("Renew contract {0}", [name]),
					fields: [
						{ fieldtype: "HTML", options: `<div class="ahr-note">${__("Renewal creates a NEW contract. The existing one is kept unchanged as history.")}</div>` },
						{ fieldtype: "Date", fieldname: "start_date", label: __("New start date"), reqd: 1 },
						{ fieldtype: "Date", fieldname: "end_date", label: __("New end date") },
						{ fieldtype: "Link", fieldname: "contract_type", options: "Isoft Contract Type", label: __("Contract type") },
					],
					primary_action_label: __("Renew"),
					primary_action: (v) => {
						d.hide();
						this.call("renew_contract", { name: name, ...v }).then((r) => {
							frappe.show_alert({ message: __("Renewed as {0}", [r.renewal]), indicator: "green" });
							this.render();
						});
					},
				});
				d.show();
			});
		});
	}

	newContractDialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Employment Contract"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("The contract type supplies the default duration, probation days and notice period. The Salary Profile field only LINKS to the employee's existing pay record — it does not set the salary.")}</div>` },
				{ fieldtype: "Link", fieldname: "employee", options: "Employee", label: __("Employee"), reqd: 1,
					get_query: () => ({ filters: { status: "Active" } }) },
				{ fieldtype: "Link", fieldname: "contract_type", options: "Isoft Contract Type", label: __("Contract type"), reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "start_date", label: __("Start date"), reqd: 1, default: frappe.datetime.get_today() },
				{ fieldtype: "Check", fieldname: "is_open_ended", label: __("Open-ended (no end date)") },
				{ fieldtype: "Date", fieldname: "end_date", label: __("End date"),
					depends_on: "eval:!doc.is_open_ended" },
				{ fieldtype: "Section Break", label: __("Optional") },
				{ fieldtype: "Date", fieldname: "probation_start", label: __("Probation start") },
				{ fieldtype: "Date", fieldname: "probation_end", label: __("Probation end") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Link", fieldname: "department", options: "Department", label: __("Department") },
				{ fieldtype: "Link", fieldname: "designation", options: "Designation", label: __("Designation") },
			],
			primary_action_label: __("Create as Draft"),
			primary_action: (v) => {
				d.hide();
				this.call("create_contract", { data: JSON.stringify(v) }).then((name) => {
					frappe.msgprint({
						title: __("Contract {0} created", [name]),
						indicator: "green",
						message: __("It is a Draft. Submit it for approval, then an HR Manager approves it and the status becomes Active automatically from its start date."),
					});
					this.render();
				});
			},
		});
		d.show();
	}

	view_salarychanges() {
		const esc = frappe.utils.escape_html;
		Promise.all([
			this.call("list_salary_changes", { company: this.state.company }),
			this.call("next_payroll_boundary"),
		]).then(([rows, boundary]) => {
			const action = `<button class="btn btn-sm btn-primary sc-new">${__("New Salary Change")}</button>`;
			const body = rows.length
				? this.panel({ title: __("Salary Changes"), actions: action, icon: "fa-line-chart",
					subtitle: __("Increases, promotions and corrections. A change applies from its effective date and is picked up by the next payroll run.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Employee")}</th><th>${__("Type")}</th><th>${__("Effective")}</th>
						<th class="num">${__("From")}</th><th class="num">${__("To")}</th>
						<th class="num">${__("Change")}</th><th>${__("Status")}</th>
						<th>${__("Requested By")}</th><th>${__("Approved By")}</th></tr></thead>
					<tbody>${rows.map((r) =>
						`<tr><td>${esc(r.employee_name)}</td><td>${esc(__(r.change_type))}</td>
						<td>${this.d(r.effective_date)}</td>
						<td class="num">${this.money(r.current_base)}</td>
						<td class="num">${this.money(r.new_base)}</td>
						<td class="num">${flt(r.percentage_change)}%</td>
						<td><span class="ahr-badge ${(r.status || "").toLowerCase().replace(/ /g, "-")}">${esc(__(r.status))}</span></td>
						<td>${esc(r.requested_by || "")}</td><td>${esc(r.approved_by || "")}</td></tr>`)
						.join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-line-chart",
					title: __("No salary changes have been recorded yet."),
					body: __("Use a Salary Change when an existing employee receives an approved salary increase or decrease. Do NOT create a second Salary Profile by hand — that is what produces overlapping salary history."),
					who: __("Requested by HR or a Payroll Officer, approved by someone else. On approval the current Salary Profile is closed and the new one starts on the effective date, automatically."),
					actions: [{ label: __("New Salary Change"), cls: "sc-new", primary: true }],
				});

			this.$content.html(
				this.what(__("A Salary Change is the controlled way to change pay. After approval the system closes the current Salary Profile and opens the new one — salary history is preserved for you.")) +
				body +
				`<div class="ahr-note" style="margin-top:12px">
					<b>${__("Effective dates")}</b><br>
					${__("A salary change must take effect at the START of a payroll period, because the engine cannot split one period between two salaries. The next valid date is")}
					<b>${boundary.next_start}</b>.
				</div>`);

			this.$content.find(".sc-new").on("click", () => this.newSalaryChangeDialog(boundary));
		});
	}

	newSalaryChangeDialog(boundary) {
		// Reachable from the quick-action bar as well as from the Salary Changes screen,
		// so the payroll boundary may not have been loaded yet. Fetch it and re-enter
		// rather than opening a form whose effective date silently defaults to nothing.
		if (!boundary) {
			return this.call("next_payroll_boundary").then((b) => this.newSalaryChangeDialog(b));
		}
		const d = new frappe.ui.Dialog({
			title: __("New Salary Change"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("After approval the current Salary Profile is closed the day before the effective date and a new profile is created. You do not create the profile yourself.")}</div>` },
				{ fieldtype: "Link", fieldname: "employee", options: "Employee", label: __("Employee"), reqd: 1,
					get_query: () => ({ filters: { status: "Active" } }),
					onchange: () => {
						const emp = d.get_value("employee");
						if (!emp) return;
						frappe.call({
							method: "frappe.client.get_list",
							args: { doctype: "Isoft Salary Profile", filters: { employee: emp },
								fields: ["base", "food_allowance", "transport_allowance"],
								order_by: "from_date desc", limit_page_length: 1 },
						}).then((r) => {
							const p = (r.message || [])[0];
							if (p) {
								d.set_value("current_base", p.base);
								if (!d.get_value("new_base")) d.set_value("new_base", p.base);
							}
						});
					} },
				{ fieldtype: "Select", fieldname: "change_type", label: __("Reason"), reqd: 1,
					options: ["Merit Increase", "Promotion", "Market Adjustment", "Correction", "Other"].join("\n"),
					default: "Merit Increase" },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "effective_date", label: __("Effective from"), reqd: 1,
					default: boundary && boundary.next_start,
					description: __("Must be the first day of a payroll period.") },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Currency", fieldname: "current_base", label: __("Current base salary"), read_only: 1 },
				{ fieldtype: "Currency", fieldname: "new_base", label: __("New base salary"), reqd: 1 },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Justification"), reqd: 1 },
				sourceField("Management instruction"),
			],
			primary_action_label: __("Create as Draft"),
			primary_action: (v) => {
				d.hide();
				this.call("create_salary_change", { data: JSON.stringify(v) }).then((name) => {
					frappe.msgprint({
						title: __("Salary Change {0} created", [name]),
						indicator: "green",
						message: __("It is a Draft. Submit it for approval — somebody other than you must approve it — and then Apply it. Only on Apply is the Salary Profile changed."),
					});
					this.render();
				});
			},
		});
		d.show();
	}

	view_advances() {
		const esc = frappe.utils.escape_html;
		this.call("list_advances", { company: this.state.company }).then((rows) => {
			const action = `<button class="btn btn-sm btn-primary adv-new">${__("New Salary Advance")}</button>`;
			const body = rows.length
				? this.panel({ title: __("Salary Advances"), actions: action, icon: "fa-money",
					subtitle: __("Money paid before payroll and recovered from later periods. The instalment schedule is created on approval.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Advance")}</th><th>${__("Employee")}</th><th>${__("Requested")}</th>
						<th class="num">${__("Approved")}</th><th class="num">${__("Recovered")}</th>
						<th class="num">${__("Outstanding")}</th><th>${__("Instalments")}</th>
						<th>${__("Status")}</th></tr></thead><tbody>${rows.map((r) =>
						`<tr><td>${esc(r.name)}</td><td>${esc(r.employee_name)}</td>
						<td>${this.d(r.request_date)}</td>
						<td class="num">${this.money(r.approved_amount)}</td>
						<td class="num">${this.money(r.recovered_amount)}</td>
						<td class="num">${this.money(r.outstanding_amount)}</td>
						<td>${r.installments || "—"}</td>
						<td><span class="ahr-badge ${(r.status || "").toLowerCase().replace(/ /g, "-")}">${esc(__(r.status))}</span></td></tr>`)
						.join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-money",
					title: __("No salary advance has been requested yet."),
					body: __("An advance is money paid to an employee before payroll and recovered from future salary periods. Employees can request one themselves from the self-service area; HR can also raise one here."),
					who: __("Requested by the employee or HR, approved by an HR or Payroll Manager, disbursed by Finance, then recovered automatically by payroll."),
					actions: [{ label: __("New Salary Advance"), cls: "adv-new", primary: true }],
				});

			this.$content.html(
				this.what(__("Advances are paid before payroll and recovered from later periods. Draft → Pending Approval → Approved → Disbursed → Recovering → Settled.")) +
				body +
				`<div class="ahr-note" style="margin-top:12px">
					<b>${__("Why a draft shows no instalments")}</b><br>
					${__("The instalment schedule is generated when the advance is APPROVED, not when it is created — because the approved amount may differ from the amount requested.")}<br>
					${__("Recovery takes one instalment per payroll run and is capped so that net pay can never become negative; anything uncollectable stays outstanding and is reported.")}
				</div>`);

			this.$content.find(".adv-new").on("click", () => this.newAdvanceDialog());
		});
	}

	newAdvanceDialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Salary Advance"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("Record what the employee is asking for. The approved amount, the number of instalments and the recovery start are confirmed at approval — the instalment schedule is built then.")}</div>` },
				{ fieldtype: "Link", fieldname: "employee", options: "Employee", label: __("Employee"), reqd: 1,
					get_query: () => ({ filters: { status: "Active" } }) },
				{ fieldtype: "Date", fieldname: "request_date", label: __("Request date"), reqd: 1,
					default: frappe.datetime.get_today() },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Currency", fieldname: "requested_amount", label: __("Amount requested"), reqd: 1 },
				{ fieldtype: "Int", fieldname: "installments", label: __("Instalments"), default: 1,
					description: __("Over how many payroll periods it should be recovered.") },
				{ fieldtype: "Date", fieldname: "recovery_start_date", label: __("Recovery starts") },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 },
				sourceField("Employee verbal request"),
			],
			primary_action_label: __("Create as Draft"),
			primary_action: (v) => {
				d.hide();
				this.call("create_advance", { data: JSON.stringify(v) }).then((name) => {
					frappe.msgprint({
						title: __("Advance {0} created", [name]),
						indicator: "green",
						message: __("It is a Draft. Submit it for approval; the instalment schedule appears once it is approved. Finance then disburses it and payroll recovers it."),
					});
					this.render();
				});
			},
		});
		d.show();
	}

	peFilters() {
		const p = this._pePeriod || {};
		return {
			company: this.$content.find(".pe-company").val(),
			start_date: p.start,
			end_date: p.end,
			department: this.$content.find(".pe-dept").val(),
			branch: this.$content.find(".pe-branch").val(),
			designation: this.$content.find(".pe-desig").val(),
			validate_attendance: this.$content.find(".pe-valatt").is(":checked") ? 1 : 0,
			based_on_timesheet: this.$content.find(".pe-timesheet").is(":checked") ? 1 : 0,
		};
	}

	runPreview(inputs) {
		const f = this.peFilters();
		if (!f.company) return frappe.msgprint(__("Select a company"));
		if (!f.start_date || !f.end_date) return frappe.msgprint(__("Select a month"));
		frappe.dom.freeze(__("Calculating preview..."));
		this.call("payroll_preview", { ...f, inputs: JSON.stringify(inputs || {}) })
			.then((rows) => {
				frappe.dom.unfreeze();
				this.renderPreview(rows);
			})
			.catch(() => frappe.dom.unfreeze());
	}

	renderPreview(rows) {
		const $w = this.$content.find(".ahr-preview-wrap");
		const esc = frappe.utils.escape_html;
		if (!rows.length) {
			$w.html(this.panel(__("Preview"), `<div class="ahr-empty">${__("No employees with a Salary Profile match the filters.")}</div>`));
			return;
		}
		this._pvRows = rows;  // keep for re-render when columns change
		if (!this._pvCols) {  // load the column config once, then re-render
			this.call("get_preview_columns").then((c) => { this._pvCols = c; this.renderPreview(rows); });
			return;
		}
		const cols = this._pvCols;
		// Already-processed employees (a non-cancelled slip already exists for this period)
		// are locked: excluded from totals and from creation.
		const included = (r) => !this._excluded.has(r.employee) && !r.already;
		const total = rows.filter(included).reduce((s, r) => s + flt(r.net_pay), 0);
		const count = rows.filter(included).length;

		const cellHtml = (r, c) => {
			const m = (v) => this.money(v);
			switch (c.key) {
				case "employee_name":
					return `${esc(r.employee_name || "")}${r.already ? ` <span class="ahr-badge submitted" title="${esc(r.existing_slip || "")}">${__("Already processed")}</span>` : ""}<div class="pv-sub">${esc(r.designation || "")}</div>`;
				case "department": return esc(r.department || "");
				case "days": return `${flt(r.payment_days)}/${flt(r.total_working_days)}`;
				case "vacation": return `<label class="pv-ferias-lbl"><input type="checkbox" class="pv-ferias" data-full="${flt(r.ferias_full)}" ${flt(r.vacation) > 0 ? "checked" : ""}> <span class="pv-ferias-amt">${flt(r.vacation) > 0 ? m(r.ferias_full) : "—"}</span></label>`;
				case "christmas": return `<input type="number" class="form-control input-xs pv-natal" value="${flt(r.christmas)}">`;
				case "overtime_amount":
				case "productivity_bonus":
				case "adiantamento":
					return `<input type="number" class="form-control input-xs pv-f" data-k="${c.key}" value="${flt(r[c.key])}">`;
				case "net_pay": return `<b>${m(r.net_pay)}</b>`;
				default: return c.money ? m(r[c.key]) : esc(String(r[c.key] == null ? "" : r[c.key]));
			}
		};
		const thCls = (c) => `${c.money ? "num" : ""}${c.visible ? "" : " pv-hide"}`;
		const tdCls = (c) => `${c.money && !c.input ? "num" : ""}${c.key === "net_pay" ? " pv-net" : ""}${c.visible ? "" : " pv-hide"}`;

		const body = rows.map((r) => {
			const off = this._excluded.has(r.employee) || r.already;
			const hay = esc(`${r.employee_name || ""} ${r.designation || ""} ${r.department || ""} ${r.employee || ""}`.toLowerCase());
			const tds = cols.map((c) => `<td class="${tdCls(c)}">${cellHtml(r, c)}</td>`).join("");
			return `<tr data-emp="${esc(r.employee)}" data-name="${esc(r.employee_name)}" data-search="${hay}" class="${off ? "pv-off" : ""}">
				<td class="pv-c"><input type="checkbox" class="pv-include" ${r.already ? "disabled" : (off ? "" : "checked")}></td>${tds}</tr>`;
		}).join("");
		const header = `<th class="pv-c"><input type="checkbox" class="pv-all" checked></th>` +
			cols.map((c) => `<th class="${thCls(c)}">${__(c.label)}</th>`).join("");

		$w.html(
			this.panel(
				__("Preview") +
					` &middot; <span class="text-muted pv-summary">${count} ${__("employees")} &middot; ${__("Total Net")}: ${this.money(total)}</span>`,
				`<div class="pv-search-wrap"><input type="text" class="form-control input-sm pv-search" placeholder="${__("Search employee, department or designation...")}">
						<button class="btn btn-default btn-xs pv-cols" style="margin-left:8px;"><i class="fa fa-columns"></i> ${__("Columns")}</button></div>
					<div class="pv-scroll"><table class="ahr-table pv-table"><thead><tr>${header}</tr></thead>
					<tbody>${body}</tbody></table></div>
				<br><button class="btn btn-default btn-sm pv-recalc"><i class="fa fa-refresh"></i> ${__("Recalculate")}</button>
				<button class="btn btn-default btn-sm pv-xlsx" style="margin-left:8px;"><i class="fa fa-file-excel-o"></i> ${__("Excel")}</button>
				<button class="btn btn-default btn-sm pv-pdf" style="margin-left:4px;"><i class="fa fa-file-pdf-o"></i> ${__("PDF")}</button>
				<button class="btn btn-primary btn-sm pv-create" style="margin-left:8px;"><i class="fa fa-cogs"></i> ${__("Create Salary Slips")} (${count})</button>`
			)
		);
		$w.find(".pv-cols").on("click", () => this.columnsDialog());

		const refreshSummary = () => {
			const inc = $w.find(".pv-table tbody tr").filter((_, tr) => $(tr).find(".pv-include").is(":checked"));
			let t = 0;
			inc.each((_, tr) => (t += this._netOf($(tr))));
			$w.find(".pv-summary").html(`${inc.length} ${__("employees")} &middot; ${__("Total Net")}: ${this.money(t)}`);
			$w.find(".pv-create").html(`<i class="fa fa-cogs"></i> ${__("Create Salary Slips")} (${inc.length})`);
		};
		$w.find(".pv-include").on("change", (e) => {
			const $tr = $(e.currentTarget).closest("tr");
			const emp = $tr.data("emp");
			if (e.currentTarget.checked) this._excluded.delete(emp);
			else this._excluded.add(emp);
			$tr.toggleClass("pv-off", !e.currentTarget.checked);
			refreshSummary();
		});
		$w.find(".pv-all").on("change", (e) => {
			const on = e.currentTarget.checked;
			$w.find(".pv-include:not(:disabled)").prop("checked", on).each((_, c) => {
				const $tr = $(c).closest("tr");
				const emp = $tr.data("emp");
				on ? this._excluded.delete(emp) : this._excluded.add(emp);
				$tr.toggleClass("pv-off", !on);
			});
			refreshSummary();
		});
		// Live search: visually filter rows (does not change include/exclude or totals).
		$w.find(".pv-search").on("input", (e) => {
			const q = (e.currentTarget.value || "").toLowerCase().trim();
			$w.find(".pv-table tbody tr").each((_, tr) => {
				const $tr = $(tr);
				$tr.toggle(!q || ($tr.attr("data-search") || "").indexOf(q) !== -1);
			});
		});
		$w.find(".pv-ferias").on("change", (e) => {
			const $cb = $(e.currentTarget);
			$cb.closest(".pv-ferias-lbl").find(".pv-ferias-amt").text($cb.is(":checked") ? this.money(flt($cb.data("full"))) : "—");
			this.runPreview(this.collectPreview().inputs);
		});
		$w.find(".pv-natal").on("change", () => this.runPreview(this.collectPreview().inputs));
		const doExport = (fmt) => {
			const f = this.peFilters();
			if (!f.company || !f.start_date) return frappe.msgprint(__("Run a preview first"));
			frappe.dom.freeze(__("Exporting..."));
			this.call("export_payroll_preview", { ...f, inputs: JSON.stringify(this.collectPreview().inputs), file_format: fmt })
				.then((res) => { frappe.dom.unfreeze(); this._downloadB64(res); })
				.catch(() => frappe.dom.unfreeze());
		};
		$w.find(".pv-xlsx").on("click", () => doExport("excel"));
		$w.find(".pv-pdf").on("click", () => doExport("pdf"));
		$w.find(".pv-recalc").on("click", () => this.runPreview(this.collectPreview().inputs));
		$w.find(".pv-create").on("click", () => {
			const f = this.peFilters();
			const rowsToCreate = this.collectPreview().rows;
			if (!rowsToCreate.length) return frappe.msgprint(__("Select at least one employee to process"));
			frappe.dom.freeze(__("Creating salary slips..."));
			this.call("create_payroll_from_preview", {
				company: f.company, start_date: f.start_date, end_date: f.end_date,
				rows: JSON.stringify(rowsToCreate),
				validate_attendance: f.validate_attendance, based_on_timesheet: f.based_on_timesheet,
			})
				.then((r) => {
					frappe.dom.unfreeze();
					frappe.show_alert({ message: __("Created {0} slips", [r.employees]), indicator: "green" });
					this.openPayrollEntry(r.name);
					if (this._refreshHistory) this._refreshHistory();
				})
				.catch(() => frappe.dom.unfreeze());
		});
	}

	_netOf($tr) {
		// Net cell carries the pv-net class (position-independent after column reorder).
		const txt = $tr.find(".pv-net").text().replace(/[^\d.-]/g, "");
		return flt(txt);
	}

	columnsDialog() {
		this.call("get_preview_columns").then((cols) => {
			const d = new frappe.ui.Dialog({ title: __("Preview Columns"), size: "small" });
			const render = () => {
				$(d.body).find(".pc-list").html(cols.map((c, i) => `
					<div class="pc-row" data-i="${i}" style="display:flex;align-items:center;gap:8px;padding:6px 2px;border-bottom:1px solid var(--ahr-border);">
						<input type="checkbox" class="pc-vis" ${c.visible ? "checked" : ""}>
						<span style="flex:1;">${__(c.label)}</span>
						<button class="btn btn-xs btn-default pc-up" ${i === 0 ? "disabled" : ""}>▲</button>
						<button class="btn btn-xs btn-default pc-down" ${i === cols.length - 1 ? "disabled" : ""}>▼</button>
					</div>`).join(""));
			};
			$(d.body).html(`<div class="text-muted small" style="margin-bottom:8px;">${__("Tick to show a column; use the arrows to reorder. Applies to the preview and the Excel/PDF export.")}</div><div class="pc-list"></div>`);
			render();
			$(d.body).on("change", ".pc-vis", (e) => {
				const i = $(e.currentTarget).closest(".pc-row").data("i");
				cols[i].visible = e.currentTarget.checked ? 1 : 0;
			});
			$(d.body).on("click", ".pc-up", (e) => {
				const i = $(e.currentTarget).closest(".pc-row").data("i");
				if (i > 0) { [cols[i - 1], cols[i]] = [cols[i], cols[i - 1]]; render(); }
			});
			$(d.body).on("click", ".pc-down", (e) => {
				const i = $(e.currentTarget).closest(".pc-row").data("i");
				if (i < cols.length - 1) { [cols[i + 1], cols[i]] = [cols[i], cols[i + 1]]; render(); }
			});
			d.set_primary_action(__("Save"), () => {
				this.call("save_preview_columns", { columns: JSON.stringify(cols.map((c) => ({ key: c.key, visible: c.visible ? 1 : 0 }))) })
					.then(() => {
						d.hide();
						frappe.show_alert({ message: __("Saved"), indicator: "green" });
						this.call("get_preview_columns").then((nc) => {
							this._pvCols = nc;
							if (this._pvRows) this.renderPreview(this._pvRows);
						});
					});
			});
			d.show();
		});
	}

	collectPreview() {
		const inputs = {};
		const rows = [];
		this.$content.find(".pv-table tbody tr").each((_, tr) => {
			const $tr = $(tr);
			const emp = $tr.data("emp");
			const o = { employee: emp, employee_name: $tr.data("name") };
			$tr.find(".pv-f").each((__, i) => (o[$(i).data("k")] = flt($(i).val())));
			const $fer = $tr.find(".pv-ferias");
			const ferias = $fer.length && $fer.is(":checked") ? flt($fer.data("full")) : 0;
			const $nat = $tr.find(".pv-natal");
			const natal = $nat.length ? flt($nat.val()) : 0;
			o.subsidio_ferias = ferias;
			o.subsidio_natal = natal;
			inputs[emp] = { overtime_amount: o.overtime_amount, productivity_bonus: o.productivity_bonus, adiantamento: o.adiantamento, ferias_amount: ferias, natal_amount: natal };
			if ($tr.find(".pv-include").is(":checked")) rows.push(o);
		});
		return { inputs, rows };
	}

	// Reusable account+date dialog for creating Payment (Bank) Entries.
	paymentDialog(title, onSubmit) {
		this.call("get_settings").then((s) => {
			const d = new frappe.ui.Dialog({
				title: title,
				fields: [
					{ fieldname: "payment_account", label: __("Salary Payment Account"), fieldtype: "Link",
					  options: "Account", reqd: 1, default: s.salary_payment_account,
					  get_query: () => ({ filters: { is_group: 0 } }) },
					{ fieldname: "posting_date", label: __("Payment Date"), fieldtype: "Date",
					  default: frappe.datetime.get_today() },
				],
				primary_action_label: __("Create Payment"),
				primary_action: (v) => { d.hide(); onSubmit(v); },
			});
			d.show();
		});
	}

	/* §9 / §11 / §19 — what actually happened, in accounting terms.
	 *
	 * "created: 0, skipped: 2" is the idempotent case: the vouchers already exist,
	 * so it says so rather than reporting a no-op as a success. Errors are shown
	 * in full — a payroll that posted for 40 of 84 employees must not look green. */
	_accountingResult(title, res, name) {
		const created = res.created || 0;
		const skipped = res.skipped || 0;
		const errors = res.errors || [];
		const already = created === 0 && skipped > 0 && !errors.length;
		const lines = [];
		if (already) {
			lines.push(`<p><b>${__("This payroll has already been posted.")}</b></p>
				<p>${__("{0} salary slip(s) already carry a submitted voucher, so nothing was created a second time.", [skipped])}</p>`);
		} else {
			lines.push(`<div class="ahr-form-grid">
				<div><b>${__("Vouchers created")}:</b> ${created}</div>
				<div><b>${__("Already posted")}:</b> ${skipped}</div>
				${res.total ? `<div><b>${__("Amount")}:</b> ${this.money(res.total)}</div>` : ""}
				<div><b>${__("Payroll")}:</b> ${frappe.utils.escape_html(res.status || "")}</div>
			</div>`);
		}
		if (errors.length) {
			lines.push(`<div class="ahr-callout err" style="margin-top:12px">
				<b>${__("{0} slip(s) were NOT posted:", [errors.length])}</b><br>
				${errors.map((e) => frappe.utils.escape_html(e)).join("<br>")}</div>`);
		}
		frappe.msgprint({
			title: already ? __("Already posted") : title,
			indicator: errors.length ? "orange" : (already ? "blue" : "green"),
			message: lines.join(""),
		});
	}

	_bulkResult(label, res) {
		let msg = `${label}: ${res.created} ${__("created")}, ${res.skipped} ${__("skipped")}`;
		if (res.total) msg += ` · ${__("Total")}: ${this.money(res.total)}`;
		const bad = res.errors && res.errors.length;
		frappe.show_alert({ message: msg, indicator: bad ? "orange" : "green" });
		if (bad) frappe.msgprint({ title: __("Some entries were not created"), message: res.errors.join("<br>"), indicator: "orange" });
	}

	// Trigger a browser download from a {filename, mime, content(base64)} payload.
	_downloadB64({ filename, mime, content }) {
		const bytes = atob(content);
		const arr = new Uint8Array(bytes.length);
		for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
		const blob = new Blob([arr], { type: mime || "application/octet-stream" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = filename || "export";
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}

	/* ------------------------------------------------------------------ *
	 * FINANCE — the payroll run's accounting and payment area.
	 *
	 * Everything here is presentation. The accrual and the payment are built
	 * exclusively by make_bulk_journal_entry / make_bulk_payment_entry, which
	 * already create AND submit the Journal Entry, and the state machine still
	 * decides what may happen: the buttons come from the server's own
	 * allowed_actions, and pressing one that the server refuses produces the
	 * server's refusal. No authorisation is decided in JavaScript.
	 * ------------------------------------------------------------------ */

	/* §24 — where the run has got to. Display only; it reads the state, it does
	 * not influence it. */
	payrollProgress(status) {
		const STEPS = [
			{ key: "Draft", label: __("Prepare"), states: ["Draft", "Calculated"] },
			{ key: "Pending Approval", label: __("Approve"), states: ["Pending Approval", "Rejected"] },
			{ key: "Approved", label: __("Post accounting"), states: ["Approved"] },
			{ key: "Posted", label: __("Release payment"), states: ["Posted"] },
			{ key: "Payment Ready", label: __("Pay"), states: ["Payment Ready"] },
			{ key: "Paid", label: __("Close"), states: ["Paid"] },
		];
		const ORDER = ["Draft", "Calculated", "Pending Approval", "Rejected", "Approved",
			"Posted", "Payment Ready", "Paid", "Closed"];
		const at = ORDER.indexOf(status);
		if (status === "Cancelled") {
			return `<div class="ahr-flow cancelled"><span class="ahr-flow-step err">
				<i class="fa fa-ban"></i> ${__("Cancelled")}</span></div>`;
		}
		return `<div class="ahr-flow">${STEPS.map((s) => {
			const idx = ORDER.indexOf(s.states[0]);
			const current = s.states.indexOf(status) > -1;
			const done = at > idx || status === "Closed";
			const cls = current ? "current" : (done ? "done" : "todo");
			const mark = done ? `<i class="fa fa-check"></i>`
				: (current ? `<i class="fa fa-circle-o"></i>` : `<i class="fa fa-minus"></i>`);
			return `<span class="ahr-flow-step ${cls}">${mark} ${s.label}</span>`;
		}).join("")}</div>`;
	}

	/* §10 / §20 / §31 — what has actually been booked, read from the vouchers
	 * rather than from a status field. A voucher that exists but is not submitted
	 * is reported as such, because only a submitted one reaches the ledger. */
	financePanel(sum, rows, name) {
		const esc = frappe.utils.escape_html;
		const status = sum.status;
		const submitted = rows.filter((r) => r.docstatus === 1);
		const accrued = submitted.filter((r) => r.journal_entry);
		const paid = submitted.filter((r) => r.payment_entry);
		const drafts = rows.length - submitted.length;
		const vouchers = (list, field) => [...new Set(list.map((r) => r[field]).filter(Boolean))];
		const accrualV = vouchers(accrued, "journal_entry");
		const paymentV = vouchers(paid, "payment_entry");

		const tick = (ok, text, warn) =>
			`<div class="ahr-fin-line ${ok ? "ok" : (warn ? "warn" : "todo")}">
				<i class="fa ${ok ? "fa-check-circle" : (warn ? "fa-exclamation-circle" : "fa-circle-o")}"></i>
				<span>${text}</span></div>`;

		const voucherLinks = (list) => list.map((v) =>
			`<button class="btn btn-xs btn-default pe-voucher" data-v="${esc(v)}">
				<i class="fa fa-book"></i> ${esc(v)}</button>`).join(" ");

		const accountingBody =
			tick(drafts === 0 && rows.length > 0,
				drafts ? __("{0} salary slip(s) still draft", [drafts])
					: __("{0} salary slip(s) submitted", [submitted.length]), drafts > 0) +
			tick(accrualV.length > 0 && accrued.length === submitted.length,
				accrued.length
					? __("Accrual posted for {0} of {1}", [accrued.length, submitted.length])
					: __("Payroll accrual not posted yet"),
				accrued.length > 0 && accrued.length < submitted.length) +
			(accrualV.length
				? `<div class="ahr-fin-vouchers">${__("Journal Entry")}: ${voucherLinks(accrualV)}</div>`
				: "") +
			(sum.audit && sum.audit.posted_by
				? `<div class="ahr-fin-meta">${__("Posted by")} ${esc(sum.audit.posted_by)}
					${sum.audit.posted_at ? "· " + this.d(sum.audit.posted_at) : ""}</div>` : "");

		const paymentBody =
			tick(["Payment Ready", "Paid", "Closed"].indexOf(status) > -1,
				["Payment Ready", "Paid", "Closed"].indexOf(status) > -1
					? __("Released for payment") : __("Waiting for release")) +
			tick(paymentV.length > 0 && paid.length === submitted.length,
				paid.length
					? __("Payment posted for {0} of {1}", [paid.length, submitted.length])
					: __("Payment not posted yet"),
				paid.length > 0 && paid.length < submitted.length) +
			(paymentV.length
				? `<div class="ahr-fin-vouchers">${__("Payment Entry")}: ${voucherLinks(paymentV)}</div>`
				: "") +
			(sum.audit && sum.audit.payment_authorized_by
				? `<div class="ahr-fin-meta">${__("Released by")} ${esc(sum.audit.payment_authorized_by)}
					${sum.audit.payment_authorized_at ? "· " + this.d(sum.audit.payment_authorized_at) : ""}</div>` : "") +
			(sum.audit && sum.audit.paid_at
				? `<div class="ahr-fin-meta">${__("Paid")} · ${this.d(sum.audit.paid_at)}</div>` : "");

		return `<div class="ahr-fin-grid">
			<div class="ahr-fin-col"><h6><i class="fa fa-book"></i> ${__("Accounting")}</h6>${accountingBody}</div>
			<div class="ahr-fin-col"><h6><i class="fa fa-money"></i> ${__("Payment")}</h6>${paymentBody}</div>
		</div>
		<div class="ahr-fin-recon" style="display:none"></div>`;
	}

	/* §7 / §8 — show Finance what will be booked, and to which accounts, before
	 * anything is written. The account list and the "missing" detection come from
	 * payroll_configuration_status, which is the same check payroll readiness uses:
	 * an account that is set but points at a deleted record counts as missing here
	 * too, rather than at the moment posting fails on the first employee. */
	confirmPost(name, sum, count, onGo) {
		const esc = frappe.utils.escape_html;
		const t = sum.totals || {};
		this.action("payroll_configuration_status", { company: sum.company })
			.then((cfg) => {
				const items = (cfg && cfg.items) || cfg || [];
				const accounts = items.filter((i) => i.key === "payroll_payable_account"
					|| String(i.key).indexOf("account:") === 0);
				const missing = accounts.filter((i) => !i.ok);
				const rowsHtml = accounts.map((i) =>
					`<tr><td>${esc(i.label.replace(/^Account — /, ""))}</td>
					<td>${i.ok ? esc(i.value) : `<span class="text-danger">${__("not configured")}</span>`}</td>
					</tr>`).join("");

				// §8 — a missing account stops the attempt. Posting would fail on the
				// first employee that uses it, after some others had already posted.
				if (missing.length) {
					frappe.msgprint({
						title: __("Cannot post payroll accounting"),
						indicator: "red",
						message: `<p>${__("These accounts are not configured, so the accrual cannot be booked:")}</p>
							<ul>${missing.map((m) => `<li><b>${esc(m.label.replace(/^Account — /, ""))}</b></li>`).join("")}</ul>
							<p>${__("Set them under Settings → Account per Component, then post again.")}</p>`,
					});
					return;
				}

				const money = (v) => this.money(v);
				const d = new frappe.ui.Dialog({
					title: __("Post payroll accounting"),
					size: "large",
					primary_action_label: __("Post Accounting"),
					primary_action: () => { d.hide(); onGo(); },
				});
				$(d.body).html(
					`<div class="ahr-callout">
						${__("This creates and submits the payroll accrual Journal Entry, posting salary expense and payroll liabilities to the General Ledger.")}
					</div>
					<div class="ahr-form-grid" style="margin-top:14px">
						<div><b>${__("Payroll")}:</b> ${esc(name)}</div>
						<div><b>${__("Period")}:</b> ${this.d(sum.start_date)} → ${this.d(sum.end_date)}</div>
						<div><b>${__("Salary slips to post")}:</b> ${count}</div>
					</div>` +
					this.subsection(__("What will be booked"),
						`<table class="ahr-table"><tbody>
							<tr><td>${__("Gross payroll")}</td><td class="num">${money(t.gross)}</td></tr>
							<tr><td>${__("Employee INSS")}</td><td class="num">${money(t.employee_inss)}</td></tr>
							<tr><td>${__("IRT")}</td><td class="num">${money(t.irt)}</td></tr>
							<tr><td>${__("Other deductions")}</td><td class="num">${money(t.other_deductions)}</td></tr>
							<tr><td><b>${__("Net payroll (credited to Payroll Payable)")}</b></td>
								<td class="num"><b>${money(t.net)}</b></td></tr>
							<tr><td>${__("Employer INSS")}</td><td class="num">${money(t.employer_inss)}</td></tr>
							<tr><td>${__("Total employer cost")}</td><td class="num">${money(t.employer_cost)}</td></tr>
						</tbody></table>`) +
					this.subsection(__("Accounts"),
						`<table class="ahr-table"><thead><tr>
							<th>${__("Component")}</th><th>${__("Account")}</th></tr></thead>
							<tbody>${rowsHtml}</tbody></table>`,
						{ subtitle: __("Every account the accrual will touch, as configured today.") })
				);
				d.show();
			})
			.catch((err) => this.fail(__("Could not read the payroll account configuration"), err));
	}

	/* §15 / §16 / §18 — the payment confirmation. It states the amount and the two
	 * accounts money moves between, and lets Finance record the bank's own
	 * reference. cheque_no/cheque_date are what ERPNext requires on a Bank Entry,
	 * so the reference is carried in those fields rather than a parallel one. */
	confirmPayment(name, sum, count, net, onGo) {
		const esc = frappe.utils.escape_html;
		this.action("get_settings").then((s) => {
			const d = new frappe.ui.Dialog({
				title: __("Pay payroll"),
				size: "large",
				fields: [
					{ fieldname: "info", fieldtype: "HTML" },
					{ fieldname: "payment_account", label: __("Payment account (bank/cash)"),
						fieldtype: "Link", options: "Account", reqd: 1,
						default: s.salary_payment_account,
						// Only a real, non-group account of this company: an arbitrary
						// account here would produce a Journal Entry ERPNext refuses.
						get_query: () => ({ filters: {
							is_group: 0, company: sum.company,
							root_type: ["in", ["Asset", "Liability"]],
						} }) },
					{ fieldname: "posting_date", label: __("Payment date"), fieldtype: "Date",
						default: frappe.datetime.get_today() },
					{ fieldname: "bank_reference", label: __("Bank reference"), fieldtype: "Data",
						description: __("The reference the bank returned. Recorded on the payment entry.") },
				],
				primary_action_label: __("Make Payment"),
				primary_action: (v) => { d.hide(); onGo(v); },
			});
			d.fields_dict.info.$wrapper.html(
				`<div class="ahr-callout">
					${__("This creates and submits the payroll payment Journal Entry, clearing the Payroll Payable account.")}
				</div>
				<div class="ahr-form-grid" style="margin:12px 0">
					<div><b>${__("Payroll")}:</b> ${esc(name)}</div>
					<div><b>${__("Employees")}:</b> ${count}</div>
					<div><b>${__("Net amount to pay")}:</b> ${this.money(net)}</div>
					<div><b>${__("Payroll payable")}:</b> ${esc(s.payroll_payable_account || "—")}</div>
				</div>
				<div class="ahr-callout warn" style="margin-bottom:10px">
					${__("Generating a bank file is not payment. This step is what records the money as paid.")}
				</div>`);
			d.show();
		}).catch((err) => this.fail(__("Could not read the payment configuration"), err));
	}

	/* §32 / §33 — read a posted voucher without leaving the application. */
	viewVoucher(voucher) {
		const esc = frappe.utils.escape_html;
		this.action("payroll_voucher", { name: voucher }).then((v) => {
			const rows = v.accounts.map((a) => `<tr>
				<td>${esc(a.account)}</td>
				<td>${esc(a.party || "")}</td>
				<td class="num">${a.debit ? this.money(a.debit) : ""}</td>
				<td class="num">${a.credit ? this.money(a.credit) : ""}</td></tr>`).join("");
			const d = new frappe.ui.Dialog({ title: v.name, size: "large" });
			$(d.body).html(
				`<div class="ahr-form-grid">
					<div><b>${__("Reference")}:</b> ${esc(v.name)}</div>
					<div><b>${__("Posting date")}:</b> ${this.d(v.posting_date)}</div>
					<div><b>${__("Status")}:</b> <span class="ahr-badge ${v.docstatus === 1 ? "paid" : (v.docstatus === 2 ? "cancelled" : "draft")}">${esc(__(v.status))}</span></div>
					<div><b>${__("In the ledger")}:</b> ${v.in_ledger
						? `${__("yes")} — ${v.gl_entries} ${__("GL entries")}`
						: `<span class="text-danger">${__("no GL entries")}</span>`}</div>
					<div><b>${__("Total debit")}:</b> ${this.money(v.total_debit)}</div>
					<div><b>${__("Total credit")}:</b> ${this.money(v.total_credit)}</div>
					${v.cheque_no ? `<div><b>${__("Bank reference")}:</b> ${esc(v.cheque_no)}</div>` : ""}
				</div>` +
				(v.balanced ? "" : `<div class="ahr-callout err" style="margin-top:10px">${__("This voucher does not balance.")}</div>`) +
				this.subsection(__("Accounts"),
					`<table class="ahr-table"><thead><tr>
						<th>${__("Account")}</th><th>${__("Party")}</th>
						<th class="num">${__("Debit")}</th><th class="num">${__("Credit")}</th>
					</tr></thead><tbody>${rows}</tbody></table>`) +
				(v.remark ? `<div class="ahr-callout" style="margin-top:12px">${esc(v.remark)}</div>` : "") +
				`<div class="ahr-doc-actions">
					<a class="btn btn-xs btn-default" href="${esc(v.desk_url)}" target="_blank">
						${__("Open in ERPNext")}</a>
				</div>`);
			d.show();
		}).catch((err) => this.fail(__("Could not open the voucher"), err));
	}

	/* §28 / §29 — the existing reconciliation, shown where the money was booked. */
	showReconciliation(name, $slot, status) {
		return this.action("payroll_reconciliation", { name }).then((r) => {
			const lines = (r && r.lines) || [];
			if (!lines.length) return;
			// Until the payroll is paid, the payable is SUPPOSED to be outstanding —
			// that is what "posted but not yet paid" means. Reporting it as a figure
			// that does not match the ledger reads as an accounting error when it is
			// the expected state, so before payment it is shown as pending instead.
			const settledPending = ["Paid", "Closed"].indexOf(status) === -1;
			lines.forEach((l) => {
				l._pending = settledPending && l.key === "payment" && !l.reconciled;
			});
			const bad = lines.filter((l) => !l.reconciled && !l._pending);
			$slot.show().html(this.subsection(__("Accounting reconciliation"),
				`<table class="ahr-table"><thead><tr>
					<th>${__("Check")}</th><th class="num">${__("Expected")}</th>
					<th class="num">${__("In the ledger")}</th><th>${__("Result")}</th>
				</tr></thead><tbody>${lines.map((l) => `<tr>
					<td>${frappe.utils.escape_html(l.label)}</td>
					<td class="num">${this.money(l.expected)}</td>
					<td class="num">${this.money(l.actual)}</td>
					<td>${l.reconciled
						? `<span class="ahr-badge paid">${__("Matches")}</span>`
						: (l._pending
							? `<span class="ahr-badge draft">${__("Pending payment")}</span>`
							: `<span class="ahr-badge cancelled">${__("Off by {0}", [this.money(l.difference)])}</span>`)}</td>
				</tr>`).join("")}</tbody></table>`,
				{ subtitle: bad.length
					? __("{0} check(s) do not match the ledger.", [bad.length])
					: (lines.some((l) => l._pending)
						? __("Everything posted matches the ledger. The payable stays open until the payroll is paid.")
						: __("Every figure matches what was posted to the ledger.")) }));
		}).catch((err) => this.fail(__("Could not reconcile"), err));
	}

	openPayrollEntry(name) {
		const esc = frappe.utils.escape_html;
		// Two server calls, no client-side decisions: the grid comes from get_payroll_entry
		// and every button shown comes from allowed_actions, which the server recomputes
		// (role + state + company + who prepared it) on each request.
		Promise.all([
			this.call("get_payroll_entry", { name }),
			this.call("payroll_approval_summary", { name }),
		]).then(([r, sum]) => {
			const doc = r.doc;
			const allowed = new Set(sum.allowed_actions || []);
			const t = sum.totals || {};
			const stat = (v) =>
				v ? `<a href="/app/journal-entry/${encodeURIComponent(v)}" target="_blank" title="${esc(v)}">✓</a>`
				  : `<span class="text-muted">—</span>`;
			const rowsHtml = r.employees
				.map((e) => `<tr data-emp="${esc(e.employee)}" data-slip="${esc(e.salary_slip || "")}">
					<td class="pv-c"><input type="checkbox" class="pe-sel" ${e.docstatus === 1 ? "checked" : "disabled"}></td>
					<td>${esc(e.employee_name || "")}</td>
					<td class="num">${this.money(e.net_pay)}</td>
					<td class="num">${stat(e.journal_entry)}</td>
					<td class="num">${stat(e.payment_entry)}</td>
						<td>${this.slipStatus(e.status)}</td></tr>`)
				.join("");
			const emp = `<table class="ahr-table"><thead><tr>
				<th class="pv-c"><input type="checkbox" class="pe-all" checked></th>
				<th>${__("Employee")}</th><th class="num">${__("Net Pay")}</th>
				<th class="num">${__("Accrual JE")}</th><th class="num">${__("Payment")}</th>
				<th>${__("Status")}</th></tr></thead>
				<tbody>${rowsHtml}</tbody></table>`;

			const diff = sum.difference || {};
			const diffCell = (k) =>
				sum.previous ? `<span class="text-muted small"> (${diff[k] >= 0 ? "+" : ""}${this.money(diff[k])})</span>` : "";
			// The approver sees the whole payroll on one screen instead of opening 80 slips.
			// Users should never have to decode a status: the document states what it is
			// waiting for and who must act.
			const ns = sum.next_step || {};
			const nextHtml = ns.next_action
				? `<div class="ahr-next-step ${ns.can_act_now ? "" : "blocked"}">
						<div><b>${__("Estado")}:</b> ${esc(ns.state_label || ns.state)}</div>
						<div><b>${__("Próxima acção")}:</b> ${esc(ns.next_action_label || "")} — ${esc(ns.responsible || "")}</div>
						<div class="text-muted small">${esc(ns.description || "")}</div>
						${(ns.blockers || []).map((b) => `<div class="text-danger small">${esc(b)}</div>`).join("")}
					</div>`
				: `<div class="ahr-next-step"><div>${esc(ns.description || "")}</div></div>`;

			const summaryHtml = nextHtml + `<div class="ahr-form-grid">
				<div><b>${__("Period")}:</b> ${this.d(doc.start_date)} → ${this.d(doc.end_date)}</div>
				<div><b>${__("Status")}:</b> <span class="ahr-badge">${esc(sum.status)}</span></div>
				<div><b>${__("Employees")}:</b> ${t.employees}</div>
				<div><b>${__("Gross")}:</b> ${this.money(t.gross)}${diffCell("gross")}</div>
				<div><b>${__("Employee INSS")}:</b> ${this.money(t.employee_inss)}${diffCell("employee_inss")}</div>
				<div><b>${__("Employer INSS")}:</b> ${this.money(t.employer_inss)}${diffCell("employer_inss")}</div>
				<div><b>${__("IRT")}:</b> ${this.money(t.irt)}${diffCell("irt")}</div>
				<div><b>${__("Other Deductions")}:</b> ${this.money(t.other_deductions)}</div>
				<div><b>${__("Net Payroll")}:</b> ${this.money(t.net)}${diffCell("net")}</div>
				<div><b>${__("Total Employer Cost")}:</b> ${this.money(t.employer_cost)}${diffCell("employer_cost")}</div>
				<div><b>${__("Negative net")}:</b> ${sum.negative_net}</div>
				<div><b>${__("Payment blockers")}:</b> ${(sum.payment_blockers || []).length}</div>
			</div>${
				sum.approved
					? `<div class="text-muted small" style="margin-top:6px;">${__("Approved snapshot")}: ${
							sum.approved.employees
					  } ${__("employees")}, ${__("net")} ${this.money(sum.approved.net)} — ${esc(
							sum.approved.fingerprint.slice(0, 12)
					  )}</div>`
					: ""
			}${
				doc.rejection_reason
					? `<div class="text-danger small" style="margin-top:6px;">${__("Rejected")}: ${esc(doc.rejection_reason)}</div>`
					: ""
			}`;

			const a = sum.audit || {};
			const auditRow = (label, who, when) =>
				who || when
					? `<tr><td>${esc(label)}</td><td>${esc(who || "—")}</td><td>${when ? this.d(when) : "—"}</td></tr>`
					: "";
			const auditHtml = `<table class="ahr-table"><thead><tr>
				<th>${__("Step")}</th><th>${__("User")}</th><th>${__("When")}</th></tr></thead><tbody>
				${auditRow(__("Prepared"), a.prepared_by, a.prepared_at)}
				${auditRow(__("Submitted for approval"), a.submitted_by, a.submitted_at)}
				${auditRow(__("Approved"), a.approved_by, a.approved_at)}
				${auditRow(__("Rejected"), a.rejected_by, a.rejected_at)}
				${auditRow(__("Posted"), a.posted_by, a.posted_at)}
				${auditRow(__("Payment authorized"), a.payment_authorized_by, a.payment_authorized_at)}
				${auditRow(__("Paid"), "", a.paid_at)}
				${auditRow(__("Bank file generated"), a.exported_by, a.exported_at)}
				${auditRow(__("Closed"), a.closed_by, a.closed_at)}
				${auditRow(__("Cancelled"), a.cancelled_by, a.cancelled_at)}
			</tbody></table>`;

			const btn = (action, label, cls) =>
				allowed.has(action) ? `<button class="btn btn-xs ${cls} pe-act" data-action="${action}">${label}</button>` : "";

			const canPost = sum.status === "Approved" || sum.status === "Posted";
			const canPay = sum.status === "Payment Ready";
			const canExport = sum.status === "Payment Ready" || sum.status === "Paid";
			const canSubmitSlips = sum.draft_slips > 0 && (sum.status === "Approved" || sum.status === "Posted");

			const d = new frappe.ui.Dialog({ title: `${doc.name} — ${sum.status}`, size: "extra-large" });
			// The summary the confirmation dialogs quote comes from the same payload
			// the screen is showing, so what Finance is asked to confirm is exactly
			// what they are looking at.
			const forConfirm = Object.assign({}, sum, {
				company: doc.company, start_date: doc.start_date, end_date: doc.end_date });
			$(d.body).html(
				this.payrollProgress(sum.status) +
				this.panel({ title: __("Finance"), icon: "fa-university",
					subtitle: __("What has been booked to the ledger, and what is still outstanding.") },
					this.financePanel(sum, r.employees || [], name)) +
				this.panel({ title: __("Payroll Summary"), icon: "fa-calculator", subtitle: __("What this run pays in total, and how it splits between earnings, deductions and net pay.") }, summaryHtml) +
					this.panel({ title: __("Employees"), icon: "fa-users", subtitle: __("Every employee in this run, with what they earn, what is deducted and what they take home.") }, emp) +
					this.panel({ title: __("Audit Trail"), icon: "fa-history", subtitle: __("Who did what to this run, and when. Recorded automatically and never editable.") }, auditHtml) +
					`<div class="ahr-doc-actions">
						${btn("submit_for_approval", __("Submit for Approval"), "btn-primary")}
						${btn("approve", __("Approve"), "btn-primary")}
						${btn("reject", __("Reject"), "btn-default")}
						${canSubmitSlips ? `<button class="btn btn-xs btn-primary pe-submit-slips">${__("Submit All Slips")}</button>` : ""}
						${canPost ? `<button class="btn btn-xs btn-primary pe-bulk-je">${__("Post Accounting")}</button>` : ""}
						${btn("release_for_payment", __("Release for Payment"), "btn-primary")}
						${canPay ? `<button class="btn btn-xs btn-primary pe-bulk-pe">${__("Make Payment")}</button>` : ""}
						${canExport ? `<button class="btn btn-xs btn-default pe-bank"><i class="fa fa-file-excel-o"></i> ${__("Export Bank File")}</button>` : ""}
						${btn("close", __("Close Payroll"), "btn-default")}
						${btn("cancel", __("Cancel Payroll"), "btn-danger")}
						<button class="btn btn-xs btn-danger pe-delete">${__("Delete Entry")}</button>
					</div>
					<div class="text-muted small" style="margin-top:6px;">${__("Tick employees to act on a subset; actions apply to the ticked rows.")}</div>`
			);

			const reopen = () => { d.hide(); this.openPayrollEntry(name); };

			$(d.body).find(".pe-act").on("click", (e) => {
				const action = $(e.currentTarget).data("action");
				const run = (reason) => {
					frappe.dom.freeze(__("Updating payroll..."));
					this.call("payroll_action", { name, action, reason })
						.then((res) => {
							frappe.dom.unfreeze();
							frappe.show_alert({ message: __("Payroll is now {0}", [res.status]), indicator: "green" });
							reopen();
							if (this._refreshHistory) this._refreshHistory();
						})
						.catch(() => frappe.dom.unfreeze());
				};
				if (action === "reject") {
					// A rejection reason is mandatory server-side; asking for it here just
					// saves the user a round trip.
					frappe.prompt(
						[{ fieldname: "reason", fieldtype: "Small Text", label: __("Rejection Reason"), reqd: 1 }],
						(v) => run(v.reason), __("Reject Payroll"), __("Reject")
					);
				} else {
					frappe.confirm(__("Confirm: {0}?", [action.replace(/_/g, " ")]), () => run());
				}
			});

			$(d.body).find(".pe-bank").on("click", () => {
				const url = "/api/method/isoft_angola_hr.isoft_angola_hr.api.export_bank_transfer?name=" +
					encodeURIComponent(name);
				window.open(url, "_blank");
			});
			const selectedEmps = () =>
				$(d.body).find(".pe-sel:checked").not(":disabled").map((_, c) => $(c).closest("tr").data("emp")).get();
			$(d.body).find(".pe-all").on("change", (e) => {
				$(d.body).find(".pe-sel").not(":disabled").prop("checked", e.currentTarget.checked);
			});
			$(d.body).find(".pe-submit-slips").on("click", () => {
				this.call("submit_payroll_entry", { name }).then((res) => {
					frappe.show_alert({ message: __("Submitted {0} slips", [res.submitted]), indicator: "green" });
					reopen();
				});
			});
			// §32 — read a posted voucher in place.
			$(d.body).find(".pe-voucher").on("click", (e) =>
				this.viewVoucher($(e.currentTarget).data("v")));

			// Reconciliation is shown for any run that has already been posted, not
			// only immediately after posting (§28).
			if (["Posted", "Payment Ready", "Paid", "Closed"].indexOf(sum.status) > -1) {
				this.showReconciliation(name, $(d.body).find(".ahr-fin-recon"), sum.status);
			}

			$(d.body).find(".pe-bulk-je").on("click", () => {
				const emps = selectedEmps();
				if (!emps.length) return frappe.msgprint(__("Select at least one employee"));
				this.confirmPost(name, forConfirm, emps.length, () => {
					frappe.dom.freeze(__("Posting payroll accounting…"));
					this.action("make_bulk_journal_entry", { name, employees: JSON.stringify(emps) })
						.then((res) => {
							frappe.dom.unfreeze();
							this._accountingResult(__("Payroll accounting posted"), res, name);
							reopen();
						})
						.catch((err) => {
							frappe.dom.unfreeze();
							this.fail(__("Could not post payroll accounting"), err);
						});
				});
			});
			$(d.body).find(".pe-bulk-pe").on("click", () => {
				const emps = selectedEmps();
				if (!emps.length) return frappe.msgprint(__("Select at least one employee"));
				const net = (r.employees || [])
					.filter((x) => emps.indexOf(x.employee) > -1)
					.reduce((a, x) => a + flt(x.net_pay), 0);
				this.confirmPayment(name, forConfirm, emps.length, net, (v) => {
					frappe.dom.freeze(__("Posting payroll payment…"));
					this.action("make_bulk_payment_entry", {
						name, payment_account: v.payment_account, posting_date: v.posting_date,
						bank_reference: v.bank_reference || null,
						employees: JSON.stringify(emps),
					})
						.then((res) => {
							frappe.dom.unfreeze();
							this._accountingResult(__("Payroll payment posted"), res, name);
							reopen();
						})
						.catch((err) => {
							frappe.dom.unfreeze();
							this.fail(__("Could not post the payroll payment"), err);
						});
				});
			});
			$(d.body).find(".pe-delete").on("click", () => {
				frappe.confirm(__("Delete this payroll entry and all its salary slips?"), () => {
					this.call("delete_payroll_entry", { name }).then(() => {
						frappe.show_alert({ message: __("Deleted"), indicator: "red" });
						d.hide();
						this.render();
					});
				});
			});
			d.show();
		});
	}

	// ---- IRT Table (single) ----
	view_irt() {
		this.call("get_irt_table").then((t) => {
			this.renderIrt(t.brackets || []);
		});
	}
	renderIrt(rows) {
		const head = `<tr><th class="num">${__("From")}</th><th class="num">${__("To")}</th>
			<th class="num">${__("Excess Over")}</th><th class="num">${__("Rate %")}</th>
			<th class="num">${__("Parcela Fixa")}</th><th></th></tr>`;
		const body = rows
			.map(
				(r, i) => `<tr data-i="${i}">
				<td><input class="form-control input-xs irt-f" data-k="from_amount" value="${flt(r.from_amount)}"></td>
				<td><input class="form-control input-xs irt-f" data-k="to_amount" value="${flt(r.to_amount)}"></td>
				<td><input class="form-control input-xs irt-f" data-k="excess_over" value="${flt(r.excess_over)}"></td>
				<td><input class="form-control input-xs irt-f" data-k="rate" value="${flt(r.rate)}"></td>
				<td><input class="form-control input-xs irt-f" data-k="parcela_fixa" value="${flt(r.parcela_fixa)}"></td>
				<td><button class="btn btn-xs btn-danger irt-del">&times;</button></td></tr>`
			)
			.join("");
		this.$content.html(
			this.panel(
				__("Tabela IRT (Angola)"),
				`<table class="ahr-table irt-table"><thead>${head}</thead><tbody>${body}</tbody></table>
				<br><button class="btn btn-default btn-sm irt-add"><i class="fa fa-plus"></i> ${__("Add Bracket")}</button>
				<button class="btn btn-primary btn-sm irt-save" style="margin-left:8px;"><i class="fa fa-save"></i> ${__("Save Table")}</button>`
			)
		);
		this._irt = rows;
		this.$content.find(".irt-add").on("click", () => {
			this._irt = this.collectIrt();
			this._irt.push({ from_amount: 0, to_amount: 0, excess_over: 0, rate: 0, parcela_fixa: 0 });
			this.renderIrt(this._irt);
		});
		this.$content.find(".irt-del").on("click", (e) => {
			const i = $(e.currentTarget).closest("tr").data("i");
			this._irt = this.collectIrt();
			this._irt.splice(i, 1);
			this.renderIrt(this._irt);
		});
		this.$content.find(".irt-save").on("click", () => {
			this.call("save_irt_table", { brackets: JSON.stringify(this.collectIrt()) }).then((r) =>
				frappe.show_alert({ message: __("Saved {0} brackets", [(r && r.brackets) || 0]), indicator: "green" })
			);
		});
	}
	collectIrt() {
		const out = [];
		this.$content.find(".irt-table tbody tr").each((_, tr) => {
			const o = {};
			$(tr).find(".irt-f").each((__, inp) => (o[$(inp).data("k")] = flt($(inp).val())));
			out.push(o);
		});
		return out;
	}

	// ---- Settings ----
	view_settings() {
		this.call("get_settings", { company: this.state.company }).then((s) => {
			const num = (k, l) => `<div class="ahr-field"><label>${__(l)}</label><input type="number" class="set-f" data-k="${k}" value="${s[k] != null ? s[k] : ""}"></div>`;
			const chk = (k, l) => `<label style="display:block;margin:6px 0;"><input type="checkbox" class="set-c" data-k="${k}" ${s[k] ? "checked" : ""}> ${__(l)}</label>`;
			// Account fields render as Link controls (autocomplete on typing) — see mkLink below.
			const acc = (k, l) => `<div class="ahr-field"><label>${__(l)}</label><div class="set-link" data-k="${k}"></div></div>`;
			const sel = (k, l, opts) => `<div class="ahr-field"><label>${__(l)}</label><select class="set-sel" data-k="${k}">${opts.map((o) => `<option value="${o}" ${s[k] === o ? "selected" : ""}>${__(o)}</option>`).join("")}</select></div>`;
			this.$content.html(
				this.groupLabel(__("HR configuration")) +
				this.panel(
					{ title: __("Company"), icon: "fa-building-o",
						subtitle: __("The working calendar and the payroll cycle every other calculation is measured against.") },
					`<div class="ahr-form-grid">
						<div class="ahr-field"><label>${__("Default Holiday List")}</label><div class="set-link-hl"></div></div>
						${num("payroll_cycle_start_day", "Payroll Cycle Start Day")}
					</div>
					<div class="text-muted small" style="margin-top:6px;">${__("Used for working-day calculation and the Upcoming Holidays panel. Applies to {0}.", [s._company || __("the default company")])}</div>`
				) +
				this.groupLabel(__("Payroll configuration")) +
				this.panel(
					{ title: __("Statutory rates and allowances"), icon: "fa-percent",
						subtitle: __("Contribution rates, exemption ceilings and the 13th-month rules. Changing these changes what every future payroll pays.") },
					`<div class="ahr-form-grid">
						${num("ss_employee_rate", "Social Security - Employee %")}
						${num("ss_employer_rate", "Social Security - Employer %")}
						${num("food_allowance_exemption", "Food Allowance Exemption")}
						${num("transport_allowance_exemption", "Transport Allowance Exemption")}
							${num("overtime_multiplier", "Overtime Multiplier")}
							${num("ferias_rate", "Subsídio de Férias (% of Base)")}
							${num("natal_rate", "Subsídio de Natal (% of Base, December)")}
							${sel("natal_payment_month", "Natal Payment Month", ["January","February","March","April","May","June","July","August","September","October","November","December"])}
					</div>`
				) +
					this.panel(
					{ title: __("Working Days"), icon: "fa-calendar",
						subtitle: __("How a month's working days are counted — from the holiday list, or a fixed number.") },
						`<div class="ahr-form-grid">
							${sel("working_days_basis", "Working Days Basis", ["Auto (Holiday List)", "Standard (Fixed)"])}
							${num("standard_working_days", "Standard Working Days")}
						</div>
						<div class="text-muted small" style="margin-top:6px;">${__("Working/short days (e.g. Saturday 4h, or non-working) come from each employee's Shift Type weekly schedule.")}</div>`
					) +
					this.panel(
					{ title: __("Final Settlement (Termination)"), icon: "fa-sign-out",
						subtitle: __("How the money owed on the last day is worked out: salary, unused leave and proportional subsidies.") },
						`<div class="ahr-form-grid">
							${num("settlement_salary_days", "Settlement Salary Days (Divisor)")}
							${num("settlement_leave_days", "Settlement Leave Days (Divisor)")}
						</div>
						<div class="text-muted small" style="margin-top:6px;">${__("Divisors for the proportional salary daily rate (÷26) and the untaken-leave daily rate (÷22) used in the Final Settlement.")}</div>`
					) +
					this.groupLabel(__("Components and accounting")) +
				this.panel(
					{ title: __("Enabled Components"), icon: "fa-toggle-on",
						subtitle: __("Which optional pay components appear on a salary slip at all.") },
						chk("enable_productivity_bonus", "Prémio de Produtividade") +
							chk("enable_overtime", "Horas Extras") +
							chk("enable_adiantamento", "Adiantamento") +
							chk("enable_family_allowance", "Abono de Família")
					) +
					this.panel(
					{ title: __("Net Pay (Journal Entry)"), icon: "fa-book",
						subtitle: __("Where net pay lands in the ledger when a run is posted.") },
						`<div class="ahr-form-grid">
							${acc("payroll_payable_account", "Payroll Payable Account")}
							${acc("salary_payment_account", "Salary Payment Account")}
						</div>
						<div class="text-muted small" style="margin-top:6px;">${__("Payroll Payable is credited with the net pay (accrual); the Salary Payment account is credited when salaries are paid. An Employee's own Payroll Payable Account overrides this. All other accounts are set per component below.")}</div>`
					) +
					this.panel(
					{ title: __("Account per Component"), icon: "fa-list-ol",
						subtitle: __("The account each individual earning or deduction is booked to.") },
					`<div class="ahr-form-grid">${(s.component_accounts || [])
						.map((c) => `<div class="ahr-field"><label>${frappe.utils.escape_html(c.component)} (${c.abbr})</label><div class="set-link-ca" data-abbr="${c.abbr}" data-val="${c.account ? frappe.utils.escape_html(c.account) : ""}"></div></div>`)
						.join("")}</div>`
				) +
				`<button class="btn btn-primary btn-sm set-save"><i class="fa fa-save"></i> ${__("Save Settings")}</button>`
			);

			// Instantiate Account Link controls (search-as-you-type, leaf accounts only).
			const accCtrls = {};
			const caCtrls = {};
			const mkLink = (el, value) => {
				const ctrl = frappe.ui.form.make_control({
					df: {
						fieldtype: "Link",
						options: "Account",
						placeholder: __("Account"),
						get_query: () => ({ filters: { is_group: 0 } }),
					},
					parent: el,
					render_input: true,
					only_input: true,
				});
				if (value) ctrl.set_value(value);
				return ctrl;
			};
			this.$content.find(".set-link").each((_, el) => {
				const k = $(el).data("k");
				accCtrls[k] = mkLink(el, s[k]);
			});
			this.$content.find(".set-link-ca").each((_, el) => {
				const abbr = $(el).data("abbr");
				caCtrls[abbr] = mkLink(el, $(el).attr("data-val"));
			});

			// Default Holiday List link (Company-level).
			let hlCtrl = null;
			this.$content.find(".set-link-hl").each((_, el) => {
				hlCtrl = frappe.ui.form.make_control({
					df: { fieldtype: "Link", options: "Holiday List", placeholder: __("Holiday List") },
					parent: el, render_input: true, only_input: true,
				});
				if (s.default_holiday_list) hlCtrl.set_value(s.default_holiday_list);
			});

			this.$content.find(".set-save").on("click", () => {
				const data = {};
				this.$content.find(".set-f").each((_, i) => (data[$(i).data("k")] = flt($(i).val())));
				this.$content.find(".set-c").each((_, i) => (data[$(i).data("k")] = $(i).is(":checked") ? 1 : 0));
				this.$content.find(".set-sel").each((_, i) => (data[$(i).data("k")] = $(i).val()));
				Object.keys(accCtrls).forEach((k) => (data[k] = accCtrls[k].get_value() || null));
				data.component_accounts = Object.keys(caCtrls).map((abbr) => ({
					abbr: abbr,
					account: caCtrls[abbr].get_value() || null,
				}));
				if (hlCtrl) {
					data.default_holiday_list = hlCtrl.get_value() || null;
					data._company = s._company || this.state.company || null;
				}
				this.call("save_settings", { data: JSON.stringify(data) }).then(() =>
					frappe.show_alert({ message: __("Settings saved"), indicator: "green" })
				);
			});
		});
	}

	// ==================================================================== //
	// Phase 4 — bulk contracts, offboarding, org chart, analytics,
	// recruitment, performance and statutory filing.
	// Every screen is a renderer over an hr_api endpoint; no rule is
	// re-implemented here.
	// ==================================================================== //

	/* ---- Bulk contract creation -------------------------------------------
	 *
	 * Select employees → Preview → review → Create. Preview writes nothing.
	 *
	 * Three things this screen has to get right, each of which it previously did not:
	 *
	 *  1. FEEDBACK. Previewing 82 employees takes several seconds. With no loading
	 *     state the button looked completely inert for that whole time, which is
	 *     indistinguishable from a dead button — and is exactly how it was reported.
	 *
	 *  2. VISIBLE FAILURE. Nothing may return silently. Missing input is refused with a
	 *     message naming the field; a server or client error is surfaced, never
	 *     swallowed.
	 *
	 *  3. THE PREVIEW MUST MATCH WHAT GETS CREATED. Create previously re-read the live
	 *     form, so previewing one employee and then ticking five more left the screen
	 *     saying "Create 1 contract(s)" while the button would have created six. Create
	 *     now executes the exact set that was previewed, and any change to the form
	 *     invalidates the preview and withdraws the button.
	 */
	view_bulkcontracts() {
		const esc = frappe.utils.escape_html;
		const state = { selected: new Set(), preview: null, busy: false };

		const draw = (rows) => {
			this.$content.html(
				this.what(__("Creates employment contracts for many employees at once — intended for staff who joined before the Contract module existed. Nothing is written until you press Create, and Create only appears after a preview.")) +
				this.panel(
					__("Bulk Contract Creation"),
					`<div class="ahr-note" style="margin-bottom:12px">
						${__("Preview first. It shows, for every employee you selected, whether a contract would be created, skipped because one already exists, or blocked — and why. Preview changes nothing.")}
					</div>
					<div class="ahr-filters">
						<div class="bc-ct"></div><div class="bc-start"></div><div class="bc-end"></div>
						<div class="bc-prob"></div>
						<label class="ahr-check"><input type="checkbox" class="bc-open"> ${__("Open-ended")}</label>
						<label class="ahr-check"><input type="checkbox" class="bc-joining"> ${__("Start from each joining date")}</label>
						<label class="ahr-check"><input type="checkbox" class="bc-docs"> ${__("Generate documents")}</label>
					</div>
					<div style="margin:10px 0">
						<button class="btn btn-xs btn-default bc-all">${__("Select all")}</button>
						<button class="btn btn-xs btn-default bc-none">${__("Clear")}</button>
						<span class="bc-count text-muted" style="margin-left:10px"></span>
						<button class="btn btn-xs btn-primary bc-preview" style="float:right">${__("Preview")}</button>
					</div>
					`+ (rows.length
						? `<div class="bc-rows"><table class="ahr-table"><thead><tr><th></th><th>${__("Employee")}</th>
							<th>${__("Department")}</th><th>${__("Designation")}</th>
							<th>${__("Joined")}</th></tr></thead><tbody>${rows.map((r) =>
							`<tr><td><input type="checkbox" class="bc-row" value="${esc(r.name)}"></td>
							<td>${esc(r.employee_name)}</td><td>${esc(r.department || "—")}</td>
							<td>${esc(r.designation || "—")}</td><td>${this.d(r.date_of_joining)}</td></tr>`
						).join("")}</tbody></table></div>`
						: this.blank({
							icon: "fa-check-circle-o",
							title: __("Every active employee already has a contract."),
							body: __("This screen lists only employees who do not currently hold an active or expiring employment contract. There are none, so there is nothing to create in bulk."),
							who: __("To create a single contract — for a new joiner, or to replace one that has ended — use Employees → Contracts → New Contract."),
						}))
					+ `<div class="bc-result" style="margin-top:14px"></div>`
				)
			);

			const ct = frappe.ui.form.make_control({
				df: { fieldtype: "Link", options: "Isoft Contract Type", label: __("Contract Type"),
					reqd: 1, placeholder: __("Contract Type") },
				parent: this.$content.find(".bc-ct"), render_input: true });
			const start = frappe.ui.form.make_control({
				df: { fieldtype: "Date", label: __("Start Date") },
				parent: this.$content.find(".bc-start"), render_input: true });
			const end = frappe.ui.form.make_control({
				df: { fieldtype: "Date", label: __("End Date") },
				parent: this.$content.find(".bc-end"), render_input: true });
			const prob = frappe.ui.form.make_control({
				df: { fieldtype: "Int", label: __("Probation (months)") },
				parent: this.$content.find(".bc-prob"), render_input: true });

			const $result = this.$content.find(".bc-result");
			const $preview = this.$content.find(".bc-preview");

			// A preview describes one exact set of inputs. Change any of them and it no
			// longer describes anything, so it is withdrawn rather than left to be acted on.
			const invalidate = () => {
				if (!state.preview) return;
				state.preview = null;
				$result.html(`<div class="ahr-note">${
					__("The selection or the contract details changed, so the previous preview no longer applies. Press <b>Preview</b> again.")
				}</div>`);
			};

			const sync = () => {
				state.selected = new Set(
					this.$content.find(".bc-row:checked").map((_, i) => $(i).val()).get());
				this.$content.find(".bc-count").text(__("{0} selected", [state.selected.size]));
				invalidate();
			};

			// Bound INSIDE the rendered content, not on the persistent $content wrapper.
			// Delegating from $content left one live handler behind on every visit to this
			// screen, each closing over a dead copy of `state`.
			this.$content.find(".bc-rows").on("change", ".bc-row", sync);
			this.$content.find(".bc-all").on("click", () => {
				this.$content.find(".bc-row").prop("checked", true); sync(); });
			this.$content.find(".bc-none").on("click", () => {
				this.$content.find(".bc-row").prop("checked", false); sync(); });
			[ct, start, end, prob].forEach((c) => {
				if (c && c.$input) c.$input.on("change awesomplete-selectcomplete", invalidate);
			});
			this.$content.find(".bc-open, .bc-joining").on("change", invalidate);

			const params = () => ({
				employees: JSON.stringify(Array.from(state.selected)),
				contract_type: ct.get_value(),
				start_date: start.get_value() || null,
				end_date: end.get_value() || null,
				is_open_ended: this.$content.find(".bc-open").is(":checked") ? 1 : 0,
				probation_months: prob.get_value() || null,
				use_joining_date: this.$content.find(".bc-joining").is(":checked") ? 1 : 0,
			});

			/* Say what is missing, in the order the user would fix it. Returns null when
			 * the form is complete. Never returns silently — a bare `return` in a click
			 * handler is the defect this whole screen was reported for. */
			const missing = () => {
				const problems = [];
				if (!ct.get_value()) problems.push(__("Choose a <b>Contract Type</b>."));
				if (!state.selected.size) problems.push(__("Select at least one employee."));
				const joining = this.$content.find(".bc-joining").is(":checked");
				const open = this.$content.find(".bc-open").is(":checked");
				if (!joining && !start.get_value()) {
					problems.push(__("Give a <b>Start Date</b>, or tick <b>Start from each joining date</b>."));
				}
				if (!open && !end.get_value()) {
					problems.push(__("Give an <b>End Date</b>, or tick <b>Open-ended</b>."));
				}
				return problems.length ? problems : null;
			};

			const busy = (on, label) => {
				state.busy = on;
				$preview.prop("disabled", on).text(on ? label : __("Preview"));
			};

			$preview.on("click", () => {
				if (state.busy) return;                     // a second click, not a silent skip
				const problems = missing();
				if (problems) {
					frappe.msgprint({
						title: __("Cannot preview yet"), indicator: "orange",
						message: "<ul><li>" + problems.join("</li><li>") + "</li></ul>",
					});
					return;
				}
				busy(true, __("Previewing…"));
				// The exact inputs this preview describes, captured BEFORE the request, so
				// Create can later act on them rather than on whatever the form says then.
				const previewed = params();
				this.action("bulk_contract_preview", previewed)
					.then((p) => {
						state.preview = { params: previewed, result: p };
						renderPreview(p, previewed);
					})
					.catch((err) => this.fail(__("Preview failed"), err))
					// finally-equivalent: .finally() is not safe to assume on the browsers
					// Frappe v13 supports, and the button must be restored on both paths.
					.then(() => busy(false), () => busy(false));
			});

			const renderPreview = (p, previewed) => {
				const s = p.summary;
				const badge = (a) => a === "Create"
					? `<span class="ahr-badge submitted">${__("CREATE")}</span>`
					: a === "Skipped"
						? `<span class="ahr-badge draft">${__("SKIPPED")}</span>`
						: `<span class="ahr-badge cancelled">${__("BLOCKED")}</span>`;
				$result.html(
					`<div class="ahr-what"><i class="fa fa-info-circle"></i><span>${
						__("This is a preview. Nothing has been written. {0} employee(s) were examined.", [p.rows.length])
					}</span></div>
					<div class="ahr-cards" style="margin-bottom:10px">
						<div class="ahr-card"><div class="k">${__("Would create")}</div><div class="v">${s.create}</div></div>
						<div class="ahr-card"><div class="k">${__("Skipped")}</div><div class="v">${s.skipped}</div></div>
						<div class="ahr-card"><div class="k">${__("Blocked")}</div><div class="v">${s.blocked}</div></div>
						<div class="ahr-card"><div class="k">${__("With warnings")}</div><div class="v">${s.with_warnings}</div></div>
					</div>
					<table class="ahr-table"><thead><tr><th>${__("Employee")}</th><th>${__("Result")}</th>
					<th>${__("Contract type")}</th><th>${__("Start")}</th><th>${__("End")}</th>
					<th>${__("Reason / warning")}</th></tr></thead>
					<tbody>${p.rows.map((r) =>
						`<tr><td>${esc(r.employee_name)}</td><td>${badge(r.action)}</td>
						<td>${esc(previewed.contract_type)}</td>
						<td>${this.d(r.start_date)}</td><td>${this.d(r.end_date)}</td>
						<td>${esc(r.reason || r.warning || "")}</td></tr>`).join("")}</tbody></table>
					${s.create
						? `<button class="btn btn-sm btn-primary bc-run" style="margin-top:12px">
							${__("Create {0} contract(s)", [s.create])}</button>`
						: `<div class="ahr-note" style="margin-top:12px">${
							__("Nothing would be created from this selection, so there is nothing to confirm.")
						}</div>`}`);

				$result.find(".bc-run").on("click", () => {
					if (state.busy) return;
					// The preview is the authority. If anything changed since, invalidate()
					// has already cleared it and this button is gone — but check anyway,
					// because creating a different set from the one on screen is the worst
					// thing this screen could do.
					if (!state.preview) {
						frappe.msgprint({
							title: __("Preview again first"), indicator: "orange",
							message: __("The form changed after this preview was produced. Press Preview again so that what you approve is what gets created."),
						});
						return;
					}
					frappe.confirm(
						__("Create {0} employment contract(s)? Employees already holding a contract are skipped.", [s.create]),
						() => {
							const $run = $result.find(".bc-run");
							$run.prop("disabled", true).text(__("Creating…"));
							const args = Object.assign({}, state.preview.params);
							args.generate_documents =
								this.$content.find(".bc-docs").is(":checked") ? 1 : 0;
							this.action("bulk_contract_execute", args)
								.then((res) => {
									const r = res.summary;
									frappe.msgprint({
										title: __("Bulk creation finished"),
										indicator: r.failed ? "orange" : "green",
										message: `${__("Created")}: ${r.created}<br>${__("Skipped")}: ${r.skipped}<br>${__("Blocked")}: ${r.blocked}<br>${__("Failed")}: ${r.failed}` +
											(res.failed.length
												? `<hr>${res.failed.map((f) => `${esc(f.employee_name || f.employee)}: ${esc(f.error)}`).join("<br>")}`
												: ""),
									});
									this.render();
								})
								.catch((err) => {
									this.fail(__("Contracts could not be created"), err);
									$run.prop("disabled", false)
										.text(__("Create {0} contract(s)", [s.create]));
								});
						});
				});
			};

			sync();
		};

		this.call("bulk_candidates", { company: this.state.company })
			.then(draw)
			.catch((err) => {
				this.$content.html(this.panel({ title: __("Bulk Contract Creation"), icon: "fa-files-o",
				subtitle: __("Create the same contract for many employees at once. Always preview before creating.") },
					`<div class="ahr-empty">${__("The list of employees could not be loaded.")}</div>`));
				this.fail(__("Could not load candidates"), err);
			});
	}

	view_offboarding() {
		const esc = frappe.utils.escape_html;
		this.$content.html(
			this.what(__("Ending employment: the exit checklist, what is still outstanding and what has to be returned. HR runs the whole process; the employee never needs to log in.")) +
			this.panel({ title: __("Offboarding"), icon: "fa-sign-out",
			subtitle: __("What still has to happen before somebody's file can be closed: final pay, documents and company property.") },
			`<div class="ahr-filters"><div class="off-emp"></div></div>
			<div class="off-body"><div class="ahr-empty">${__("Choose an employee to see what still has to happen before their file can be closed.")}</div></div>`));

		const ctrl = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Employee", label: __("Employee"),
				placeholder: __("Employee"),
				onchange: () => {
					const emp = ctrl.get_value();
					if (!emp) return;
					this.call("exit_checklist", { employee: emp }).then((c) => {
						const tone = (s) => s === "Done" ? "submitted"
							: s === "Blocking" ? "cancelled" : "draft";
						this.$content.find(".off-body").html(
							`<div class="ahr-cards" style="margin-bottom:12px">
								<div class="ahr-card"><div class="k">${__("Complete")}</div><div class="v">${c.complete}/${c.total}</div></div>
								<div class="ahr-card"><div class="k">${__("Blocking")}</div><div class="v">${c.blocking}</div></div>
								<div class="ahr-card"><div class="k">${__("Last working date")}</div><div class="v" style="font-size:15px">${c.relieving_date || "—"}</div></div>
								<div class="ahr-card"><div class="k">${__("Can close")}</div><div class="v">${c.can_close ? __("Yes") : __("No")}</div></div>
							</div>
							<div class="ahr-note" style="margin-bottom:12px">${esc(c.guidance)}</div>
							<table class="ahr-table"><thead><tr><th>${__("Step")}</th><th>${__("Status")}</th>
							<th>${__("Detail")}</th></tr></thead><tbody>${c.items.map((i) =>
								`<tr><td>${esc(i.label)}</td>
								<td><span class="ahr-badge ${tone(i.status)}">${__(i.status)}</span></td>
								<td>${i.link ? `<a href="#" class="off-link" data-l="${esc(i.link)}">${esc(i.detail)}</a>` : esc(i.detail || "")}</td></tr>`
							).join("")}</tbody></table>`);
					});
				} },
			parent: this.$content.find(".off-emp"), render_input: true });
	}

	/* ======================================================================
	 * ORGANISATION CHART
	 *
	 * A real top-down tree of employee cards. The hierarchy is Employee.reports_to
	 * and nothing else — no hard-coded levels, no inferred management from
	 * department data, no invented people.
	 *
	 * Built with HTML, CSS and this file. No charting library: the connectors are
	 * CSS borders on the list items and zoom is one transform, so the chart costs
	 * nothing to load and is styled in the same language as the rest of the console.
	 *
	 * Compensation is not in the payload, so there is nothing here to hide.
	 * ====================================================================== */

	//: Department accent colours. A short, muted, fixed cycle — assigned by position
	//: in the sorted department list so a department keeps its colour between loads.
	//: Deliberately few and quiet: a distinct colour per department turns an org
	//: chart into a rainbow and stops carrying meaning at about six.
	static get DEPT_COLOURS() {
		return ["#2563eb", "#0891b2", "#7c3aed", "#059669", "#d97706", "#db2777"];
	}

	orgInitials(name) {
		const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
		if (!parts.length) return "?";
		const first = parts[0][0] || "";
		const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
		return (first + last).toUpperCase();
	}

	view_orgchart() {
		const esc = frappe.utils.escape_html;
		this.call("org_chart", { company: this.state.company }).then((o) => {
			// State lives for as long as the screen does. Rebuilt on every entry, so
			// nothing leaks between a filtered and an unfiltered view.
			const org = this._org = {
				data: o,
				zoom: 1,
				collapsed: new Set(),
				selected: null,
				match: new Set(),
				department: "",
				issue: null,
				index: new Map(),          // employee -> node
				parent: new Map(),         // employee -> parent employee
			};

			// Flatten once for search, ancestry and the side panel. A cycle in the data
			// would otherwise recurse forever here, before anything is even drawn.
			const walk = (node, parent, seen) => {
				if (org.index.has(node.name) || seen.has(node.name)) return;
				org.index.set(node.name, node);
				if (parent) org.parent.set(node.name, parent.name);
				const next = new Set(seen).add(node.name);
				(node.children || []).forEach((c) => walk(c, node, next));
			};
			(o.roots || []).forEach((r) => walk(r, null, new Set()));

			org.deptColour = new Map();
			(o.departments || []).forEach((d, i) => {
				org.deptColour.set(d, AngolaHR.DEPT_COLOURS[i % AngolaHR.DEPT_COLOURS.length]);
			});

			// Anything below the first level starts collapsed: opening a 84-person chart
			// fully expanded is a wall nobody can read (§37). One click reveals a team.
			org.index.forEach((node) => {
				if (org.parent.has(node.name) && (node.children || []).length) {
					org.collapsed.add(node.name);
				}
			});

			this.$content.html(this.orgShell(o));
			this.bindOrg();
			this.renderOrg();
		});
	}

	orgShell(o) {
		const esc = frappe.utils.escape_html;
		const q = o.quality || {};
		const stat = (n, label, cls) =>
			`<div class="ahr-org-stat ${cls || ""}"><span class="n">${n}</span><span class="l">${label}</span></div>`;

		const issues = [];
		if (q.without_manager_count) {
			issues.push(`<button class="ahr-org-issue" data-issue="no_manager"
				aria-label="${__("Highlight employees with no manager recorded")}">
				<i class="fa fa-user-o"></i> ${__("{0} with no manager recorded", [q.without_manager_count])}</button>`);
		}
		if (q.without_department_count) {
			issues.push(`<button class="ahr-org-issue" data-issue="no_department"
				aria-label="${__("Highlight employees with no department")}">
				<i class="fa fa-sitemap"></i> ${__("{0} with no department", [q.without_department_count])}</button>`);
		}
		if (q.manager_outside_scope_count) {
			issues.push(`<button class="ahr-org-issue" data-issue="detached"
				aria-label="${__("Highlight employees whose manager is unavailable")}">
				<i class="fa fa-unlink"></i> ${__("{0} whose manager is inactive, elsewhere or missing", [q.manager_outside_scope_count])}</button>`);
		}
		if (q.cycle_count) {
			issues.push(`<button class="ahr-org-issue" data-issue="cycle"
				aria-label="${__("Circular reporting lines")}">
				<i class="fa fa-refresh"></i> ${__("{0} circular reporting line(s)", [q.cycle_count])}</button>`);
		}

		// "No manager recorded" is stated neutrally, not as an error: on a flat
		// organisation most of these are legitimate (§27).
		const issuePanel = issues.length
			? `<div class="ahr-org-issues">
					<b><i class="fa fa-exclamation-triangle"></i> ${__("Structure")}</b>
					${issues.join("")}
					<span style="opacity:.8">${__("Click to highlight. Missing managers are information, not necessarily a fault.")}</span>
				</div>`
			: "";

		return `<div class="ahr-org-wrap ahr-no-fit">
			<div class="ahr-org-head">
				<div class="ahr-org-title">
					<span class="t">${__("Organisation")}</span>
					<span class="s">${esc(this.state.company || __("All companies"))}</span>
				</div>
				<div class="ahr-org-stats">
					${stat(o.total, __("Active employees"))}
					${stat(o.with_reports, __("With a team"))}
					${stat(o.department_count, __("Departments"))}
					${stat(q.without_manager_count || 0, __("No manager"), (q.without_manager_count ? "warn" : ""))}
				</div>
				<div class="ahr-org-tools">
					<label class="ahr-org-search">
						<i class="fa fa-search" aria-hidden="true"></i>
						<input type="search" class="org-search" autocomplete="off"
							placeholder="${__("Search name, ID, role or department…")}"
							aria-label="${__("Search the organisation chart")}">
					</label>
					<select class="org-dept" aria-label="${__("Filter by department")}">
						<option value="">${__("All departments")}</option>
						${(o.departments || []).map((d) =>
							`<option value="${esc(d)}">${esc(d)}</option>`).join("")}
					</select>
					<div class="ahr-org-btns">
						<button class="btn btn-default btn-sm org-expand" aria-label="${__("Expand every team")}">${__("Expand all")}</button>
						<button class="btn btn-default btn-sm org-collapse" aria-label="${__("Collapse every team")}">${__("Collapse all")}</button>
						<button class="btn btn-default btn-sm org-fit" aria-label="${__("Fit the chart to the screen")}">${__("Fit")}</button>
						<button class="btn btn-default btn-sm org-full" aria-label="${__("Show the chart full screen")}"><i class="fa fa-arrows-alt"></i></button>
					</div>
				</div>
			</div>
			${issuePanel}
			<div class="ahr-org-body">
				<div class="ahr-org-canvas" tabindex="0" aria-label="${__("Organisation chart")}">
					<div class="ahr-org-stage"></div>
					<div class="ahr-org-zoom" role="group" aria-label="${__("Zoom")}">
						<button class="org-zout" aria-label="${__("Zoom out of the organisation chart")}"><i class="fa fa-minus"></i></button>
						<span class="lvl">100%</span>
						<button class="org-zin" aria-label="${__("Zoom in to the organisation chart")}"><i class="fa fa-plus"></i></button>
					</div>
				</div>
				<aside class="ahr-org-side hidden" aria-label="${__("Employee details")}"></aside>
			</div>
		</div>`;
	}

	/* ---- Drawing ---------------------------------------------------------- */
	orgNode(node, depth) {
		const esc = frappe.utils.escape_html;
		const org = this._org;
		const kids = node.children || [];
		const collapsed = org.collapsed.has(node.name);
		const dept = (node.department || "").trim();
		const colour = org.deptColour.get(dept) || "var(--ahr-border-strong)";

		const avatar = node.image
			? `<span class="ahr-node-av"><img src="${esc(node.image)}" alt=""
					onerror="this.parentNode.textContent='${esc(this.orgInitials(node.employee_name))}'"></span>`
			: `<span class="ahr-node-av" aria-hidden="true">${esc(this.orgInitials(node.employee_name))}</span>`;

		const flags = [];
		if (node.detached_parent) flags.push(__("Manager unavailable"));
		if (!dept) flags.push(__("No department"));
		if (!node.reports_to) flags.push(__("No manager"));

		const cls = [
			"ahr-node",
			org.selected === node.name ? "is-selected" : "",
			org.match.size && org.match.has(node.name) ? "is-match" : "",
			(org.match.size || org.issue) && !org.match.has(node.name) ? "is-dim" : "",
		].filter(Boolean).join(" ");

		const toggle = kids.length
			? `<button class="ahr-node-toggle" data-team="${esc(node.name)}"
					aria-expanded="${collapsed ? "false" : "true"}"
					aria-label="${collapsed
						? __("Expand the team of {0}", [esc(node.employee_name)])
						: __("Collapse the team of {0}", [esc(node.employee_name)])}">
					<i class="fa fa-chevron-down" aria-hidden="true"></i>
					${collapsed
						? __("{0} hidden", [kids.length])
						: __("{0} direct", [kids.length])}
				</button>`
			: "";

		const card = `<div class="${cls}" style="--ahr-dept:${colour}" role="button" tabindex="0"
				data-emp="${esc(node.name)}"
				aria-label="${__("Open details for {0}", [esc(node.employee_name)])}">
				${avatar}
				<div class="ahr-node-body">
					<div class="ahr-node-name">${esc(node.employee_name)}</div>
					<div class="ahr-node-role">${esc(node.designation || __("No designation"))}</div>
					${dept ? `<div class="ahr-node-dept">${esc(dept)}</div>` : ""}
					${flags.length ? `<div class="ahr-node-flags">${flags.map((f) =>
						`<span class="ahr-node-flag">${esc(f)}</span>`).join("")}</div>` : ""}
				</div>
				${toggle}
			</div>`;

		const children = kids.length && !collapsed
			? `<ul>${kids.map((c) => this.orgNode(c, depth + 1)).join("")}</ul>` : "";
		return `<li class="${collapsed ? "collapsed" : ""}">${card}${children}</li>`;
	}

	renderOrg() {
		const esc = frappe.utils.escape_html;
		const org = this._org;
		const o = org.data;
		const $stage = this.$content.find(".ahr-org-stage");

		// Roots that manage somebody get a tree each. Roots that manage nobody would
		// each be a one-card "tree", so they are collected into a grid instead — 43
		// lonely stems is noise, one labelled group is a fact.
		const visible = (node) => {
			if (!org.department) return true;
			// A department filter must not sever the reporting line: a manager from
			// another department is kept as context so the branch still makes sense.
			let hit = false;
			const scan = (n, seen) => {
				if (seen.has(n.name)) return;
				if ((n.department || "").trim() === org.department) hit = true;
				const next = new Set(seen).add(n.name);
				(n.children || []).forEach((c) => scan(c, next));
			};
			scan(node, new Set());
			return hit;
		};

		const roots = (o.roots || []).filter(visible);
		const trees = roots.filter((r) => (r.children || []).length);
		const singles = roots.filter((r) => !(r.children || []).length);

		let html = "";
		if (!o.total) {
			html = `<div class="ahr-blank" style="background:transparent;border:none">
				<i class="fa fa-sitemap"></i>
				<h4>${__("No employees are available for this company.")}</h4>
				<p>${__("Create or import employees to build the organisational structure. The chart is drawn from the Reports To field on each employee record.")}</p>
			</div>`;
		} else if (!roots.length) {
			html = `<div class="ahr-blank" style="background:transparent;border:none">
				<i class="fa fa-filter"></i>
				<h4>${__("No one matches this filter.")}</h4>
				<p>${__("No employee in the selected department appears anywhere in the reporting structure. Clear the department filter to see the whole organisation.")}</p>
			</div>`;
		} else {
			// Each independent reporting line gets its own labelled branch. Stacked
			// without a separator the second tree reads as if it hung off the first —
			// the chart appeared to say that a Technical Services Supervisor reported to
			// an HR assistant, which the data never said.
			html = trees.map((r) => `<section class="ahr-org-branch">
					${trees.length > 1
						? `<div class="ahr-org-group-head">${__("Reporting line — {0} ({1} people)",
							[esc(r.employee_name), (r.reports_total || 0) + 1])}</div>`
						: ""}
					<ul class="ahr-tree">${this.orgNode(r, 0)}</ul>
				</section>`).join("");
			if (singles.length) {
				html += `<div class="ahr-org-group">
					<div class="ahr-org-group-head">${__("Top level — nobody reports to them ({0})", [singles.length])}</div>
					<div class="ahr-org-grid">${singles.map((r) => {
						const li = this.orgNode(r, 0);
						return li.replace(/^<li[^>]*>/, "").replace(/<\/li>$/, "");
					}).join("")}</div>
				</div>`;
			}
		}
		$stage.html(html);
		this.applyZoom();
	}

	applyZoom() {
		const org = this._org;
		const $stage = this.$content.find(".ahr-org-stage");
		const el = $stage[0];
		if (!el) return;

		// Reset first, so each pass measures the natural size rather than the size the
		// previous pass imposed.
		el.style.width = "";
		el.style.marginBottom = "";
		$stage.css("transform", "scale(" + org.zoom + ")");

		// A CSS transform does not change layout size, so at anything other than 100%
		// the scroll area has to be told how big the scaled content really is, or
		// panning stops short of the edge. At 100% there is nothing to correct — and
		// pinning the width there was what pushed the cards off the side of a phone,
		// where the stage should simply be as wide as the screen.
		if (org.zoom !== 1) {
			el.style.width = el.scrollWidth + "px";
			el.style.marginBottom =
				Math.max(0, el.scrollHeight * (org.zoom - 1)) + "px";
		}
		this.$content.find(".ahr-org-zoom .lvl").text(Math.round(org.zoom * 100) + "%");
	}

	/* ---- Interaction ------------------------------------------------------ */
	bindOrg() {
		const org = this._org;
		const $c = this.$content;
		const $canvas = $c.find(".ahr-org-canvas");

		// Expand / collapse one team. Only the tree is redrawn, and only after the
		// state change — the DOM is small enough that a redraw is imperceptible and
		// far safer than surgically splicing list items.
		$c.on("click", ".ahr-node-toggle", (e) => {
			e.stopPropagation();
			const name = $(e.currentTarget).data("team");
			if (org.collapsed.has(name)) org.collapsed.delete(name); else org.collapsed.add(name);
			this.renderOrg();
		});

		$c.on("click", ".ahr-node", (e) => {
			const name = $(e.currentTarget).data("emp");
			org.selected = name;
			this.renderOrg();
			this.orgSidePanel(name);
		});
		// Keyboard: the card is a button, so Enter and Space must open it (§40).
		$c.on("keydown", ".ahr-node", (e) => {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				$(e.currentTarget).trigger("click");
			}
		});

		$c.find(".org-expand").on("click", () => {
			org.collapsed.clear();
			this.renderOrg();
		});
		$c.find(".org-collapse").on("click", () => {
			org.collapsed.clear();
			org.index.forEach((node) => {
				if (org.parent.has(node.name) && (node.children || []).length) {
					org.collapsed.add(node.name);
				}
			});
			this.renderOrg();
		});
		$c.find(".org-fit").on("click", () => this.orgFit());
		$c.find(".org-zin").on("click", () => this.orgZoom(0.1));
		$c.find(".org-zout").on("click", () => this.orgZoom(-0.1));
		$c.find(".org-full").on("click", () => this.toggleFullscreen());

		$c.find(".org-dept").on("change", (e) => {
			org.department = $(e.currentTarget).val() || "";
			// A filtered branch is useless collapsed, so opening the filter opens the
			// tree; clearing it returns to the readable default.
			if (org.department) org.collapsed.clear();
			this.renderOrg();
		});

		let searchTimer = null;
		$c.find(".org-search").on("input", (e) => {
			const term = String($(e.currentTarget).val() || "").trim().toLowerCase();
			clearTimeout(searchTimer);
			searchTimer = setTimeout(() => this.orgSearch(term), 180);
		});

		$c.on("click", ".ahr-org-issue", (e) => {
			const key = $(e.currentTarget).data("issue");
			org.issue = org.issue === key ? null : key;
			$c.find(".ahr-org-issue").removeClass("active");
			if (org.issue) $(e.currentTarget).addClass("active");
			this.orgHighlightIssue();
		});

		$c.on("click", ".ahr-org-side-close", () => {
			org.selected = null;
			$c.find(".ahr-org-side").addClass("hidden").empty();
			this.renderOrg();
		});

		// Drag to pan. Native scrolling still works — this only adds grabbing the
		// background, which is what people expect of a chart canvas.
		let dragging = false, sx = 0, sy = 0, sl = 0, st = 0;
		$canvas.on("mousedown", (e) => {
			if ($(e.target).closest(".ahr-node, .ahr-org-zoom").length) return;
			dragging = true; sx = e.pageX; sy = e.pageY;
			sl = $canvas.scrollLeft(); st = $canvas.scrollTop();
			$canvas.addClass("dragging");
		});
		$(document).on("mousemove.ahrorg", (e) => {
			if (!dragging) return;
			$canvas.scrollLeft(sl - (e.pageX - sx));
			$canvas.scrollTop(st - (e.pageY - sy));
		});
		$(document).on("mouseup.ahrorg", () => {
			dragging = false; $canvas.removeClass("dragging");
		});
	}

	orgZoom(delta) {
		const org = this._org;
		org.zoom = Math.min(1.6, Math.max(0.4, Math.round((org.zoom + delta) * 100) / 100));
		this.applyZoom();
	}

	orgFit() {
		const org = this._org;
		const $canvas = this.$content.find(".ahr-org-canvas");
		const $stage = this.$content.find(".ahr-org-stage");
		if (!$canvas.length || !$stage.length) return;
		org.zoom = 1;
		this.applyZoom();
		const need = $stage[0].scrollWidth;
		const have = $canvas[0].clientWidth - 24;
		org.zoom = need > have
			? Math.max(0.4, Math.round((have / need) * 100) / 100)
			: 1;
		this.applyZoom();
		$canvas.scrollLeft(0).scrollTop(0);
	}

	/* Search expands whatever it takes to reveal the match, then scrolls to it and
	 * marks it. The chain of managers above the match stays visible, because an
	 * employee out of context answers half the question (§22). */
	orgSearch(term) {
		const org = this._org;
		org.match = new Set();
		if (!term) {
			this.renderOrg();
			return;
		}
		const hit = (n) => [n.employee_name, n.name, n.designation, n.department]
			.some((v) => String(v || "").toLowerCase().includes(term));

		org.index.forEach((node) => {
			if (!hit(node)) return;
			org.match.add(node.name);
			// Open every manager above it, guarding against a cycle in the data.
			let parent = org.parent.get(node.name);
			const seen = new Set();
			while (parent && !seen.has(parent)) {
				seen.add(parent);
				org.collapsed.delete(parent);
				parent = org.parent.get(parent);
			}
		});
		this.renderOrg();

		const $first = this.$content.find(".ahr-node.is-match").first();
		if ($first.length) {
			$first[0].scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
		}
		this.$content.find(".ahr-org-search input").attr(
			"title", __("{0} match(es)", [org.match.size]));
	}

	/* A structure warning highlights the employees it is about, in place, so HR can
	 * see WHERE in the organisation the problem sits rather than reading a list. */
	orgHighlightIssue() {
		const org = this._org;
		const q = org.data.quality || {};
		org.match = new Set();
		if (org.issue === "no_manager") {
			(q.without_manager || []).forEach((r) => org.match.add(r.employee));
		} else if (org.issue === "no_department") {
			(q.without_department || []).forEach((r) => org.match.add(r.employee));
		} else if (org.issue === "detached") {
			(q.manager_outside_scope || []).forEach((r) => org.match.add(r.employee));
		} else if (org.issue === "cycle") {
			(q.cycles || []).forEach((c) => (c.chain || []).forEach((n) => org.match.add(n)));
		}
		// Reveal them: a highlight inside a collapsed team helps nobody.
		org.match.forEach((name) => {
			let parent = org.parent.get(name);
			const seen = new Set();
			while (parent && !seen.has(parent)) {
				seen.add(parent);
				org.collapsed.delete(parent);
				parent = org.parent.get(parent);
			}
		});
		this.renderOrg();
		const $first = this.$content.find(".ahr-node.is-match").first();
		if ($first.length) $first[0].scrollIntoView({ block: "center", inline: "center" });
	}

	/* The side panel. Reporting facts only — no salary field is in the payload, and
	 * the actions are the ones this screen can legitimately start. */
	orgSidePanel(name) {
		const esc = frappe.utils.escape_html;
		const org = this._org;
		const node = org.index.get(name);
		if (!node) return;
		const $side = this.$content.find(".ahr-org-side");
		const managerName = org.parent.get(name);
		const manager = managerName ? org.index.get(managerName) : null;
		const kids = node.children || [];

		const row = (label, value) =>
			`<dt>${label}</dt><dd>${value || "—"}</dd>`;

		const avatar = node.image
			? `<span class="ahr-node-av"><img src="${esc(node.image)}" alt=""></span>`
			: `<span class="ahr-node-av">${esc(this.orgInitials(node.employee_name))}</span>`;

		$side.removeClass("hidden").html(
			`<div class="ahr-org-side-head">
				${avatar}
				<div style="min-width:0">
					<div style="font-weight:800;font-size:14px;line-height:1.25">${esc(node.employee_name)}</div>
					<div style="font-size:12px;color:var(--ahr-primary);font-weight:600">${esc(node.designation || __("No designation"))}</div>
				</div>
				<button class="ahr-org-side-close" aria-label="${__("Close employee details")}"><i class="fa fa-times"></i></button>
			</div>
			<dl>
				${row(__("Employee ID"), esc(node.name))}
				${row(__("Department"), esc(node.department || ""))}
				${row(__("Reports to"), manager
					? esc(manager.employee_name)
					: (node.detached_parent
						? `<span style="color:#b45309">${__("{0} — not available in this view", [esc(node.detached_parent)])}</span>`
						: `<span style="color:var(--ahr-muted)">${__("No manager recorded")}</span>`))}
				${row(__("Direct reports"), kids.length
					? kids.map((k) => esc(k.employee_name)).join("<br>") : __("None"))}
				${row(__("Team size"), node.reports_total || 0)}
				${row(__("Joined"), this.d(node.date_of_joining))}
				${row(__("Status"), esc(node.status || ""))}
			</dl>
			<div class="ahr-org-side-actions">
				<button class="btn btn-sm btn-primary side-open">${__("Open employee")}</button>
				<button class="btn btn-sm btn-default side-contract">${__("Create contract")}</button>
				<button class="btn btn-sm btn-default side-change">${__("Request salary change")}</button>
				<button class="btn btn-sm btn-default side-doc">${__("Add document")}</button>
			</div>
			<div class="text-muted small" style="margin-top:12px">
				${__("Pay is never shown on the organisation chart.")}
			</div>`);

		$side.find(".side-open").on("click", () => this.openEmployee(name));
		$side.find(".side-contract").on("click", () => this.newContractDialog());
		$side.find(".side-change").on("click", () => this.newSalaryChangeDialog());
		$side.find(".side-doc").on("click", () => this.newDocumentDialog());
	}

	/* INSIGHTS (§50).
	 *
	 * The screen used to be a flat run of "title, table, title, table", which
	 * gave the reader no way to tell an executive figure from a method note.
	 * It now reads as a page: the headline figures first, then three labelled
	 * groups — what the workforce did, who was absent, what leave was taken —
	 * each an explicit section with a sentence saying what it answers.
	 *
	 * The method and limitation notes are collapsed (§17). They matter, so the
	 * summary line says exactly what is inside and one click opens it; what
	 * they must not do is occupy more height than the figures they describe. */
	view_analytics() {
		this.call("analytics_dashboard", { company: this.state.company, months: 12 })
			.then((a) => {
				const h = a.headcount, abs = a.absenteeism;
				const last = h.rows[h.rows.length - 1] || {};
				const net = (h.total_joiners || 0) - (h.total_leavers || 0);
				this.$content.html(
					this.what(__("How the workforce moved over the last 12 months, who was absent and what leave was taken. Figures are read from employee records and approved payroll — nothing here is an estimate.")) +

					this.groupLabel(__("Key metrics")) +
					this.metrics([
						{ label: __("Headcount"), value: last.closing || 0, icon: "fa-users",
							foot: __("Active employees today") },
						{ label: __("Joiners"), value: h.total_joiners, icon: "fa-sign-in",
							foot: __("Last 12 months") },
						{ label: __("Leavers"), value: h.total_leavers, icon: "fa-sign-out",
							foot: __("Last 12 months") },
						{ label: __("Net change"), value: (net > 0 ? "+" : "") + net,
							icon: "fa-exchange", foot: __("Joiners less leavers") },
						{ label: __("Turnover"), value: h.period_turnover_pct + "%",
							icon: "fa-refresh", foot: __("Of average headcount") },
						{ label: __("Absenteeism"), value: abs.absenteeism_pct + "%",
							icon: "fa-calendar-times-o", foot: __("Of scheduled days") },
					]) +

					this.groupLabel(__("Workforce movement")) +
					this.panel(__("Headcount &amp; Turnover"),
						this.subsection(__("Monthly evolution"),
							this.table([
								{ key: "label", label: __("Month") },
								{ key: "opening", label: __("Opening"), num: 1 },
								{ key: "joiners", label: __("Joiners"), num: 1 },
								{ key: "leavers", label: __("Leavers"), num: 1 },
								{ key: "closing", label: __("Closing"), num: 1 },
								{ key: "turnover_pct", label: __("Turnover %"), num: 1 },
							], h.rows),
							{ subtitle: __("Opening and closing headcount for each month, with the movement between them."),
								meta: __("{0} months", [h.rows.length]) }) +
						`<div style="margin-top:16px">` +
						this.callout(__("How this is calculated, and what it cannot see"),
							`<b>${__("Method")}</b><br>${h.method}<br><br>
							 <b>${__("Limitations")}</b><br>${h.limitations.join("<br>")}`,
							{ collapsed: true, hint: __("Show method") }) +
						`</div>`,
						{ icon: "fa-line-chart",
							subtitle: __("Growth, joiners, leavers and turnover across the period."),
							tag: { label: __("12 months") } }) +

					this.groupLabel(__("Attendance")) +
					this.panel(__("Absenteeism"),
						this.subsection(__("By employee"),
							this.table([
								{ key: "employee_name", label: __("Employee") },
								{ key: "department", label: __("Department") },
								{ key: "present", label: __("Present"), num: 1 },
								{ key: "absent", label: __("Absent"), num: 1 },
								{ key: "on_leave", label: __("Approved leave"), num: 1 },
								{ key: "unjustified_occurrences", label: __("Unjustified"), num: 1 },
								{ key: "absenteeism_pct", label: __("%"), num: 1 },
							], abs.rows.slice(0, 40)),
							{ subtitle: __("Highest absence first. Approved leave is shown but never counted as absence."),
								meta: abs.rows.length > 40
									? __("Top 40 of {0}", [abs.rows.length])
									: __("{0} employees", [abs.rows.length]) }) +
						`<div style="margin-top:16px">` +
						this.callout(__("What counts as an absence"),
							`${__("Counts as absence")}: ${abs.definition.counts_as_absence.join("; ")}<br>
							 ${__("Excluded")}: ${abs.definition.excluded.join("; ")}<br><br>
							 ${abs.definition.formula}`,
							{ collapsed: true, hint: __("Show definition") }) +
						`</div>`,
						{ icon: "fa-calendar-times-o",
							subtitle: __("Days lost against days scheduled, per employee."),
							tag: { label: abs.absenteeism_pct + "%",
								kind: parseFloat(abs.absenteeism_pct) >= 5 ? "warn" : "ok" } }) +

					this.groupLabel(__("Leave")) +
					this.panel(__("Leave Usage"),
						this.subsection(__("By leave type"),
							this.table([
								{ key: "leave_type", label: __("Leave Type") },
								{ key: "requests", label: __("Requests"), num: 1 },
								{ key: "days", label: __("Days"), num: 1 },
							], a.leave_usage, {
								empty: { icon: "fa-plane",
									title: __("No leave has been taken in this period"),
									body: __("Approved leave requests are counted here by type. Record leave under Requests & Approvals → Leave Requests.") },
							}),
							{ subtitle: __("Approved leave only — requests still awaiting a decision are not counted.") }),
						{ icon: "fa-plane",
							subtitle: __("Which entitlements are actually being used, and how heavily."),
							tag: { label: __("12 months") } })
				);
			});
	}

	view_recruitment() {
		const esc = frappe.utils.escape_html;
		this.call("recruitment_pipeline", { company: this.state.company }).then((p) => {
			if (!p.available) {
				this.$content.html(this.panel({ title: __("Recruitment"), icon: "fa-user-plus" },
					`<div class="ahr-empty">${esc(p.message)}</div>`));
				return;
			}
			const s = p.stages;
			const actions =
				`<button class="btn btn-sm btn-primary rc-opening">${__("New Job Opening")}</button>
				 <button class="btn btn-sm btn-default rc-applicant">${__("New Applicant")}</button>
				 <button class="btn btn-sm btn-default rc-interview">${__("Schedule Interview")}</button>
				 <button class="btn btn-sm btn-default rc-result">${__("Record Interview Result")}</button>
				 <button class="btn btn-sm btn-default rc-offer">${__("New Job Offer")}</button>`;

			const empty = !s.openings && !s.applicants && !s.offers && !s.hired;
			if (empty) {
				this.$content.html(
					this.what(__("Recruitment runs on ERPNext's own records. This screen is where you start and track them.")) +
					this.blank({
						icon: "fa-user-plus",
						title: __("No recruitment activity yet."),
						body: __("The process is: Job Opening → Job Applicant → Interview → record the panel's result → Job Offer → accepted → Create Employee. Start by publishing an opening."),
						who: __("Run entirely by HR. Interview panels do not need a login — HR records what they decided. Once an offer is accepted, this screen converts the applicant into an Employee, and refuses to create a second employee for the same applicant."),
						actions: [
							{ label: __("New Job Opening"), cls: "rc-opening", primary: true },
							{ label: __("New Applicant"), cls: "rc-applicant" },
						],
					}) + this.recruitmentFlow());
				this.bindRecruitment();
				return;
			}

			this.$content.html(
				this.what(__("Job Opening → Applicant → Interview → Offer → Create Employee. Accepted offers waiting to become employees are listed below.")) +
				`<div class="ahr-actions" style="margin-bottom:12px">${actions}</div>` +
				`<div class="ahr-cards">
					<div class="ahr-card"><div class="k">${__("Open positions")}</div><div class="v">${s.openings}</div></div>
					<div class="ahr-card"><div class="k">${__("Applicants")}</div><div class="v">${s.applicants}</div></div>
					<div class="ahr-card"><div class="k">${__("Interviews")}</div><div class="v">${s.interviews}</div></div>
					<div class="ahr-card"><div class="k">${__("Offers out")}</div><div class="v">${s.offers}</div></div>
					<div class="ahr-card"><div class="k">${__("Ready to hire")}</div><div class="v">${s.accepted}</div></div>
					<div class="ahr-card"><div class="k">${__("Hired")}</div><div class="v">${s.hired}</div></div>
				</div>` +
				this.panel({ title: __("Accepted offers awaiting an employee record"), icon: "fa-user-plus",
					subtitle: __("An accepted offer is not an employee until the record is created. These people are waiting.") },
					p.ready_to_hire.length
						? `<table class="ahr-table"><thead><tr><th>${__("Applicant")}</th>
							<th>${__("Designation")}</th><th>${__("Offer date")}</th><th></th></tr></thead>
							<tbody>${p.ready_to_hire.map((f) =>
								`<tr><td>${esc(f.applicant_name)}</td><td>${esc(f.designation || "—")}</td>
								<td>${this.d(f.offer_date)}</td>
								<td><button class="btn btn-xs btn-primary rc-hire" data-o="${esc(f.name)}">${__("Create employee")}</button></td></tr>`
							).join("")}</tbody></table>`
						: `<div class="ahr-empty">${__("No accepted offer is waiting. Offers appear here once the applicant accepts.")}</div>`) +
				this.panel({ title: __("Open positions"), icon: "fa-bullhorn", subtitle: __("Vacancies currently advertised, and how many people have applied to each.") }, this.table([
					{ key: "job_title", label: __("Position") },
					{ key: "designation", label: __("Designation") },
					{ key: "department", label: __("Department") },
					{ key: "planned_vacancies", label: __("Vacancies"), num: 1 },
					{ key: "applicants", label: __("Applicants"), num: 1 },
					{ key: "status", label: __("Status") },
				], p.openings)) +
				this.panel({ title: __("Applicants"), icon: "fa-users", subtitle: __("Everyone who has applied, with the stage they have reached.") }, this.table([
					{ key: "applicant_name", label: __("Name") },
					{ key: "email_id", label: __("E-mail") },
					{ key: "designation", label: __("Applying for") },
					{ key: "status", label: __("Status") },
					{ key: "applicant_rating", label: __("Rating"), num: 1 },
				], p.applicants.slice(0, 60)))
			);

			this.$content.append(this.recruitmentFlow());
			this.bindRecruitment();
			this.$content.find(".rc-hire").on("click", (e) => {
				const offer = $(e.currentTarget).data("o");
				this.call("recruitment_conversion_check", { job_offer: offer }).then((c) => {
					if (!c.can_convert) {
						frappe.msgprint({ title: __("Cannot convert"), indicator: "red",
							message: c.blockers.join("<br>") });
						return;
					}
					const d = new frappe.ui.Dialog({
						title: __("Create employee from offer"),
						fields: [
							{ fieldtype: "Data", label: __("Applicant"), read_only: 1,
								default: c.applicant_name },
							{ fieldtype: "Date", fieldname: "doj", label: __("Date of joining"),
								reqd: 1, default: frappe.datetime.get_today() },
							{ fieldtype: "Link", fieldname: "dept", options: "Department",
								label: __("Department") },
							{ fieldtype: "Link", fieldname: "mgr", options: "Employee",
								label: __("Reports to") },
							{ fieldtype: "Link", fieldname: "etype", options: "Employment Type",
								label: __("Employment type") },
							{ fieldtype: "Check", fieldname: "onb", label: __("Create onboarding"),
								default: 1 },
						],
						primary_action_label: __("Create"),
						primary_action: (v) => {
							d.hide();
							this.call("recruitment_convert", {
								job_offer: offer, date_of_joining: v.doj, department: v.dept,
								reports_to: v.mgr, employment_type: v.etype,
								create_onboarding: v.onb ? 1 : 0,
							}).then((res) => {
								frappe.msgprint({
									title: __("Employee created"), indicator: "green",
									message: `<b>${esc(res.employee)}</b> — ${esc(res.employee_name)}<br><br>` +
										`${__("Ready for work")}: ${res.readiness.ready_for_work ? __("Yes") : __("No")}<br>` +
										`${__("Ready for payroll")}: ${res.readiness.ready_for_payroll ? __("Yes") : __("No")}<br>` +
										(res.readiness.payroll_missing.length
											? `${__("Still missing")}: ${res.readiness.payroll_missing.join(", ")}<br>` : "") +
										`<hr>${res.next_steps.join("<br>")}`,
								});
								this.render();
							});
						},
					});
					d.show();
				});
			});
		});
	}

	recruitmentFlow() {
		return `<div class="ahr-note" style="margin-top:12px">
			<b>${__("How recruitment works")}</b><br>
			${__("Job Opening (tick Publish to put it online) → Job Applicant → Interview → Record Interview Result → Job Offer → mark Accepted → Create Employee → onboarding → contract → salary profile.")}<br>
			${__("An applicant needs a date of birth before they can become an employee.")}<br>
			${__("Every step is entered by HR. Interviewers and candidates never need an account.")}
		</div>`;
	}

	/* Quick-action entry point for performance: the cycle needs a template, so send the
	 * user to the screen that asks for one rather than opening a form that cannot save. */
	startPerformanceCycle() {
		this.call("list_appraisal_templates").then((templates) => {
			if (!templates.length) {
				this.go("performance");
				frappe.msgprint({
					title: __("An Appraisal Template is needed first"),
					indicator: "orange",
					message: __("A performance cycle generates its appraisals from a template of weighted objectives. Create one on the Performance screen, then start the cycle."),
				});
				return;
			}
			this.newCycleDialog(templates);
		});
	}

	/* Standalone so the quick-action bar and the Recruitment screen open the SAME form.
	 * Two copies of a create dialog is how one of them ends up missing a field. */
	newJobOpeningDialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Job Opening"),
			fields: [
				{ fieldtype: "Data", fieldname: "job_title", label: __("Job title"), reqd: 1 },
				{ fieldtype: "Link", fieldname: "designation", options: "Designation", label: __("Designation"), reqd: 1 },
				{ fieldtype: "Link", fieldname: "company", options: "Company", label: __("Company"), reqd: 1, default: this.state.company },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Link", fieldname: "department", options: "Department", label: __("Department") },
				{ fieldtype: "Int", fieldname: "planned_vacancies", label: __("Vacancies"), default: 1 },
				{ fieldtype: "Check", fieldname: "publish", label: __("Publish online"),
					description: __("Publishes a public page for this opening.") },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Text Editor", fieldname: "description", label: __("Description") },
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				d.hide();
				this.call("create_job_opening", { data: JSON.stringify(v) }).then((r) => {
					frappe.show_alert({ message: __("Opening {0} created", [r.name]), indicator: "green" });
					this.go("recruitment");
				});
			},
		});
		d.show();
	}

	/* §15 — the panel that interviewed the candidate normally has no HRMS login, so the
	 * result has to be enterable by HR or it is never recorded at all. */
	recordInterviewDialog() {
		this.call("interview_pipeline", { company: this.state.company }).then((rows) => {
			const pending = (rows || []).filter((r) => !["Cleared", "Rejected"].includes(r.status));
			if (!pending.length) {
				frappe.msgprint({
					title: __("No interview is waiting for a result"),
					message: __("Schedule an interview first. Once it has taken place, come back here and record what the panel decided."),
				});
				return;
			}
			const d = new frappe.ui.Dialog({
				title: __("Record Interview Result"),
				fields: [
					{ fieldtype: "HTML", options:
						`<div class="ahr-note">${__("Enter the outcome the interview panel reached. The panel does not need a login — name them below so the decision is not attributed to you.")}</div>` },
					{ fieldtype: "Select", fieldname: "interview", label: __("Interview"), reqd: 1,
						options: pending.map((r) => r.name).join("\n") },
					{ fieldtype: "Select", fieldname: "result", label: __("Result"), reqd: 1,
						options: ["Cleared", "Rejected"].join("\n") },
					{ fieldtype: "Data", fieldname: "decision_by", label: __("Panel / decided by") },
					{ fieldtype: "Small Text", fieldname: "feedback", label: __("Feedback") },
				],
				primary_action_label: __("Record"),
				primary_action: (v) => {
					this.call("record_interview_result", {
						interview: v.interview, result: v.result,
						feedback: v.feedback || null, decision_by: v.decision_by || null,
					}).then(() => {
						d.hide();
						frappe.show_alert({ message: __("Result recorded"), indicator: "green" });
						this.go("recruitment");
					}).catch(() => {});
				},
			});
			d.show();
		});
	}

	bindRecruitment() {
		this.$content.find(".rc-opening").on("click", () => this.newJobOpeningDialog());
		this.$content.find(".rc-result").on("click", () => this.recordInterviewDialog());

		this.$content.find(".rc-applicant").on("click", () => {
			this.call("recruitment_reference_data").then((ref) => {
				const d = new frappe.ui.Dialog({
					title: __("New Job Applicant"),
					fields: [
						{ fieldtype: "Data", fieldname: "applicant_name", label: __("Full name"), reqd: 1 },
						{ fieldtype: "Data", fieldname: "email_id", label: __("E-mail"), options: "Email", reqd: 1 },
						{ fieldtype: "Data", fieldname: "phone_number", label: __("Phone") },
						{ fieldtype: "Column Break" },
						{ fieldtype: "Select", fieldname: "job_title", label: __("Applying for"),
							options: [""].concat(ref.openings.map((o) => o.name)).join("\n") },
						{ fieldtype: "Link", fieldname: "designation", options: "Designation", label: __("Designation") },
						{ fieldtype: "Section Break" },
						{ fieldtype: "Text", fieldname: "cover_letter", label: __("Notes") },
					],
					primary_action_label: __("Create"),
					primary_action: (v) => {
						d.hide();
						this.call("create_job_applicant", { data: JSON.stringify(v) }).then((r) => {
							frappe.show_alert({ message: __("Applicant {0} created", [r.name]), indicator: "green" });
							this.render();
						});
					},
				});
				d.show();
			});
		});

		this.$content.find(".rc-interview").on("click", () => {
			this.call("recruitment_reference_data").then((ref) => {
				if (!ref.applicants.length) return frappe.msgprint(__("Create an applicant first."));
				if (!ref.rounds.length) {
					return frappe.msgprint(__("No Interview Round is configured. Create one in ERPNext first — it defines the panel and the expected rating."));
				}
				const d = new frappe.ui.Dialog({
					title: __("Schedule Interview"),
					fields: [
						{ fieldtype: "Select", fieldname: "job_applicant", label: __("Applicant"), reqd: 1,
							options: ref.applicants.map((a) => a.name).join("\n") },
						{ fieldtype: "Select", fieldname: "interview_round", label: __("Round"), reqd: 1,
							options: ref.rounds.map((r) => r.name).join("\n") },
						{ fieldtype: "Column Break" },
						{ fieldtype: "Date", fieldname: "scheduled_on", label: __("Date"), reqd: 1,
							default: frappe.datetime.get_today() },
						{ fieldtype: "Time", fieldname: "from_time", label: __("From") },
						{ fieldtype: "Time", fieldname: "to_time", label: __("To") },
					],
					primary_action_label: __("Schedule"),
					primary_action: (v) => {
						d.hide();
						this.call("schedule_interview", v).then((r) => {
							frappe.show_alert({ message: __("Interview {0} scheduled", [r.name]), indicator: "green" });
							this.render();
						});
					},
				});
				d.show();
			});
		});

		this.$content.find(".rc-offer").on("click", () => {
			this.call("recruitment_reference_data").then((ref) => {
				if (!ref.applicants.length) return frappe.msgprint(__("Create an applicant first."));
				const d = new frappe.ui.Dialog({
					title: __("New Job Offer"),
					fields: [
						{ fieldtype: "HTML", options: `<div class="ahr-note">${__("Create the offer, then submit it and set its status to Accepted. Only an accepted, submitted offer can be converted into an Employee.")}</div>` },
						{ fieldtype: "Select", fieldname: "job_applicant", label: __("Applicant"), reqd: 1,
							options: ref.applicants.map((a) => a.name).join("\n") },
						{ fieldtype: "Link", fieldname: "designation", options: "Designation", label: __("Designation"), reqd: 1 },
						{ fieldtype: "Column Break" },
						{ fieldtype: "Link", fieldname: "company", options: "Company", label: __("Company"), reqd: 1, default: this.state.company },
						{ fieldtype: "Date", fieldname: "offer_date", label: __("Offer date"), reqd: 1, default: frappe.datetime.get_today() },
					],
					primary_action_label: __("Create"),
					primary_action: (v) => {
						d.hide();
						this.call("create_job_offer", { data: JSON.stringify(v) }).then((r) => {
							frappe.show_alert({ message: __("Offer {0} created", [r.name]), indicator: "green" });
							this.render();
						});
					},
				});
				d.show();
			});
		});
	}

	view_performance() {
		const esc = frappe.utils.escape_html;
		Promise.all([
			this.call("performance_summary", { company: this.state.company }),
			this.call("list_appraisal_templates"),
			this.call("list_performance_cycles", { company: this.state.company }),
		]).then(([p, templates, cycles]) => {
			const flow = `<div class="ahr-note">
				<b>${__("How performance reviews run")}</b><br>
				${__("Appraisal Template (objectives + weightings) → Performance Cycle → Generate Appraisals → evaluation → acknowledgement → HR finalises.")}<br>
				<b>${__("No manager needs to log in.")}</b> ${__("HR records the evaluation the line manager gave, and the acknowledgement the employee signed. Both are stored with the name of the person who actually decided, separately from the HR user who entered it. An employee with no line manager is reviewed by HR.")}<br>
				<b>${__("Performance never changes salary.")}</b> ${__("A recommendation creates a Salary Change request, which goes through its own approval.")}
			</div>`;

			// Without a template a cycle cannot generate anything, so that is the first
			// thing the screen asks for rather than an empty list nobody can act on.
			if (!templates.length) {
				this.$content.html(
					this.what(__("Performance reviews are run in cycles. Each cycle generates one appraisal per employee from a template of objectives.")) +
					this.blank({
						icon: "fa-star-o",
						title: __("Performance reviews need an Appraisal Template first."),
						body: __("The template defines the objectives (KRAs) and their weightings, which must total 100%. Every appraisal in a cycle is generated from it."),
						who: __("Created by HR, once. You can reuse the same template for every cycle."),
						actions: [{ label: __("Create Appraisal Template"), cls: "pf-template", primary: true }],
					}) + flow);
				this.$content.find(".pf-template").on("click", () => this.newTemplateDialog());
				return;
			}

			const actions =
				`<button class="btn btn-sm btn-primary pf-cycle">${__("New Performance Cycle")}</button>
				 <button class="btn btn-sm btn-default pf-template">${__("Appraisal Templates")}</button>`;

			const cyclePanel = cycles.length
				? this.panel({ title: __("Performance cycles"), actions: actions, icon: "fa-star-o",
					subtitle: __("Each cycle generates one appraisal per employee from the appraisal template.") },
					`<table class="ahr-table"><thead><tr>
						<th>${__("Cycle")}</th><th>${__("Period")}</th><th>${__("From")}</th>
						<th>${__("To")}</th><th>${__("Due")}</th><th class="num">${__("Appraisals")}</th>
						<th>${__("Status")}</th><th></th></tr></thead><tbody>${cycles.map((c) =>
						`<tr><td>${esc(c.cycle_name)}</td><td>${esc(__(c.period_type || ""))}</td>
						<td>${this.d(c.start_date)}</td><td>${this.d(c.end_date)}</td>
						<td>${this.d(c.due_date)}</td><td class="num">${c.appraisals_created || 0}</td>
						<td><span class="ahr-badge ${(c.status || "").toLowerCase()}">${esc(__(c.status))}</span></td>
						<td><button class="btn btn-xs btn-default pf-preview" data-n="${esc(c.name)}">${__("Preview")}</button>
							<button class="btn btn-xs btn-primary pf-generate" data-n="${esc(c.name)}">${__("Generate")}</button>
							<button class="btn btn-xs btn-default pf-progress" data-n="${esc(c.name)}">${__("Progress")}</button></td></tr>`)
						.join("")}</tbody></table>`)
				: this.blank({
					icon: "fa-star-o",
					title: __("No performance cycle has been created yet."),
					body: __("A cycle defines the review period, the template and who is in scope. Generating it creates one appraisal per eligible employee, including employees who have no line manager — HR conducts those reviews itself."),
					who: __("Created and run by HR. HR records each evaluation and acknowledgement, then finalises. Managers with a login may score their own team themselves, but nothing waits for them."),
					actions: [
						{ label: __("New Performance Cycle"), cls: "pf-cycle", primary: true },
						{ label: __("Appraisal Templates"), cls: "pf-template" },
					],
				});

			this.$content.html(
				this.what(__("Performance reviews are run in cycles. Each cycle generates one appraisal per employee from a template of weighted objectives.")) +
				`<div class="ahr-cards">
					<div class="ahr-card"><div class="k">${__("Appraisals")}</div><div class="v">${p.total}</div></div>
					<div class="ahr-card"><div class="k">${__("Draft")}</div><div class="v">${p.draft}</div></div>
					<div class="ahr-card"><div class="k">${__("Completed")}</div><div class="v">${p.completed}</div></div>
					<div class="ahr-card"><div class="k">${__("Average score")}</div><div class="v">${p.average_score}</div></div>
					<div class="ahr-card"><div class="k">${__("Templates")}</div><div class="v">${templates.length}</div></div>
				</div>` + cyclePanel + flow +
				(p.recent && p.recent.length
					? this.panel({ title: __("Recent appraisals"), icon: "fa-check-square-o", subtitle: __("Reviews already recorded, with their outcome.") }, this.table([
						{ key: "employee_name", label: __("Employee") },
						{ key: "kra_template", label: __("Template") },
						{ key: "end_date", label: __("Period end"), date: 1 },
						{ key: "total_score", label: __("Score"), num: 1 },
						{ key: "status", label: __("Status") },
					], p.recent)) : ""));

			// The reviews HR still has to move. Without this panel a generated cycle was
			// invisible: the counters said "12 appraisals" and there was no way to touch
			// any of them without knowing the ERPNext Appraisal list existed.
			this.renderOpenReviews();

			this.$content.find(".pf-template").on("click", () => this.newTemplateDialog());
			this.$content.find(".pf-cycle").on("click", () => this.newCycleDialog(templates));
			this.$content.find(".pf-preview").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				this.call("performance_cycle_preview", { cycle: name }).then((plan) => {
					frappe.msgprint({
						title: __("Preview — {0}", [name]),
						message: `<p>${__("Would create")}: <b>${plan.summary.create}</b> ·
							${__("Skipped")}: ${plan.summary.skipped} ·
							${__("Blocked")}: ${plan.summary.blocked}</p>
							<table class="table table-bordered"><thead><tr><th>${__("Employee")}</th>
							<th>${__("Action")}</th><th>${__("Reason")}</th></tr></thead><tbody>
							${plan.rows.slice(0, 60).map((r) => `<tr><td>${frappe.utils.escape_html(r.employee_name)}</td>
							<td>${r.action}</td><td>${frappe.utils.escape_html(r.reason || "")}</td></tr>`).join("")}
							</tbody></table>`,
					});
				});
			});
			this.$content.find(".pf-generate").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				frappe.confirm(__("Generate appraisals for cycle {0}? Employees who already have one are skipped.", [name]), () => {
					this.call("performance_cycle_generate", { cycle: name }).then((r) => {
						frappe.msgprint({
							title: __("Appraisals generated"), indicator: "green",
							message: `${__("Created")}: ${r.summary.created}<br>${__("Skipped")}: ${r.summary.skipped}<br>${__("Blocked")}: ${r.summary.blocked}<br>${__("Failed")}: ${r.summary.failed}`,
						});
						this.render();
					});
				});
			});
			this.$content.find(".pf-progress").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				this.call("performance_cycle_progress", { cycle: name }).then((pr) => {
					frappe.msgprint({
						title: __("Progress — {0}", [name]),
						message: `<p>${__("Finalised")}: <b>${pr.finalised}/${pr.total}</b> (${pr.completion_pct}%)</p>
							<p>${pr.by_state.map((x) => `${x.state}: ${x.n}`).join(" · ")}</p>
							${pr.overdue.length ? `<p><b>${__("Overdue")}: ${pr.overdue.length}</b></p>` : ""}
							<p class="text-muted small">${pr.note}</p>`,
					});
				});
			});
		});
	}

	/* Every review HR still has to move, and the one button that moves it.
	 * Appended to the Performance screen rather than being its own tab: an appraisal is
	 * not a separate concept from the cycle that produced it. */
	renderOpenReviews() {
		const esc = frappe.utils.escape_html;
		const $slot = $(`<div class="ahr-open-reviews"></div>`).appendTo(this.$content);
		this.call("open_appraisals", { company: this.state.company }).then((rows) => {
			const open = rows.filter((r) => ["Pending Manager", "Pending Employee", "Pending HR"].includes(r.custom_review_state));
			if (!open.length) {
				$slot.html(rows.length
					? `<div class="ahr-note">${__("All {0} appraisal(s) in scope are finalised. Nothing is waiting on HR.", [rows.length])}</div>`
					: "");
				return;
			}
			const nextAction = (r) => {
				if (r.custom_review_state === "Pending Manager")
					return `<button class="btn btn-xs btn-primary rv-eval" data-n="${esc(r.name)}">${__("Record Evaluation")}</button>`;
				if (r.custom_review_state === "Pending Employee")
					return `<button class="btn btn-xs btn-primary rv-ack" data-n="${esc(r.name)}">${__("Record Acknowledgement")}</button>`;
				return `<button class="btn btn-xs btn-primary rv-final" data-n="${esc(r.name)}">${__("Finalise")}</button>`;
			};
			$slot.html(this.panel({ title: __("Reviews waiting for HR"), icon: "fa-hourglass-half",
				tag: { label: __("{0} open", [open.length]), kind: "warn" },
				subtitle: __("Appraisals generated by a cycle that nobody has completed yet. HR records the manager's evaluation here.") },
				`<table class="ahr-table"><thead><tr>
					<th>${__("Employee")}</th><th>${__("Line manager")}</th><th>${__("Stage")}</th>
					<th>${__("Due")}</th><th class="num">${__("Score")}</th>
					<th>${__("Recorded as")}</th><th>${__("Next step")}</th></tr></thead><tbody>${open.map((r) =>
					`<tr><td>${esc(r.employee_name || r.employee)}</td>
					<td>${r.reports_to ? esc(r.custom_manager || r.reports_to) : `<span class="text-muted">${__("none — HR reviews")}</span>`}</td>
					<td><span class="ahr-badge draft">${esc(__(r.custom_review_state))}</span></td>
					<td>${this.d(r.custom_due_date)}</td>
					<td class="num">${flt(r.total_score)}</td>
					<td class="text-muted small">${esc(r.custom_decision_by || __(r.custom_evaluation_source || "—"))}</td>
					<td>${nextAction(r)}</td></tr>`).join("")}</tbody></table>`));

			$slot.find(".rv-eval").on("click", (e) => this.recordEvaluationDialog($(e.currentTarget).data("n")));
			$slot.find(".rv-ack").on("click", (e) => this.recordAcknowledgementDialog($(e.currentTarget).data("n")));
			$slot.find(".rv-final").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				frappe.confirm(__("Finalise this review? It becomes visible to the employee and can no longer be scored."), () =>
					this.call("finalise_review", { name })
						.then(() => { frappe.show_alert({ message: __("Finalised"), indicator: "green" }); this.render(); })
						.catch(() => {}));
			});
		});
	}

	/* §16/§17 — HR enters the evaluation the line manager gave.
	 * "Decision By" is required when the source says a manager decided, because a review
	 * attributed to nobody reads as HR's own opinion of the employee. */
	recordEvaluationDialog(name) {
		this.call("appraisal_goals", { appraisal: name }).then((a) => {
			if (!a.goals.length) {
				frappe.msgprint(__("This appraisal has no objectives to score."));
				return;
			}
			const hasManager = !!a.custom_manager;
			const d = new frappe.ui.Dialog({
				title: __("Record Evaluation — {0}", [a.employee_name || a.employee]),
				size: "large",
				fields: [
					{ fieldtype: "HTML", options:
						`<div class="ahr-note">${
							hasManager
								? __("Enter the scores the line manager ({0}) gave. Your user is recorded as the person who entered them; name the manager below so the judgement stays attributed to them.", [frappe.utils.escape_html(a.manager_name || a.custom_manager)])
								: __("This employee has no line manager, so HR conducts the review. The evaluation will be recorded as HR's own.")
						}</div>` },
					{ fieldtype: "Select", fieldname: "evaluation_source", label: __("Evaluation source"), reqd: 1,
						options: ["Line manager decision recorded by HR", "HR Manager (no line manager)", "Line manager (self-service)", "Other"].join("\n"),
						default: hasManager ? "Line manager decision recorded by HR" : "HR Manager (no line manager)" },
					{ fieldtype: "Data", fieldname: "decision_by", label: __("Decision by"),
						default: a.manager_name || "",
						description: __("The person whose evaluation this is. Required when you are recording a line manager's decision."),
						depends_on: "eval:doc.evaluation_source=='Line manager decision recorded by HR'",
						mandatory_depends_on: "eval:doc.evaluation_source=='Line manager decision recorded by HR'" },
					{ fieldtype: "Section Break", label: __("Scores") },
					{ fieldtype: "Table", fieldname: "scores", label: __("Objectives"),
						data: a.goals.map((g) => ({ goal: g.name, kra: g.kra, per_weightage: g.per_weightage, score: g.score })),
						cannot_add_rows: true, in_place_edit: true,
						get_data: () => a.goals.map((g) => ({ goal: g.name, kra: g.kra, per_weightage: g.per_weightage, score: g.score })),
						fields: [
							{ fieldtype: "Data", fieldname: "goal", hidden: 1 },
							{ fieldtype: "Small Text", fieldname: "kra", label: __("Objective"), in_list_view: 1, read_only: 1 },
							{ fieldtype: "Float", fieldname: "per_weightage", label: __("Weight %"), in_list_view: 1, read_only: 1 },
							{ fieldtype: "Float", fieldname: "score", label: __("Score (0-5)"), in_list_view: 1, reqd: 1 },
						] },
					{ fieldtype: "Section Break" },
					{ fieldtype: "Small Text", fieldname: "comments", label: __("Comments") },
				],
				primary_action_label: __("Record and Submit"),
				primary_action: (v) => {
					const scores = {};
					(v.scores || []).forEach((row) => { if (row.goal) scores[row.goal] = flt(row.score); });
					this.call("record_evaluation", {
						appraisal: name, goals: JSON.stringify(scores),
						comments: v.comments || null, decision_by: v.decision_by || null,
						evaluation_source: v.evaluation_source, submit: 1,
					}).then((r) => {
						d.hide();
						frappe.msgprint({
							title: __("Evaluation recorded"), indicator: "green",
							message: __("Total score {0}. The review moved to {1}.", [flt(r.total_score), __(r.state)]),
						});
						this.render();
					}).catch(() => {});
				},
			});
			d.show();
		});
	}

	recordAcknowledgementDialog(name) {
		const d = new frappe.ui.Dialog({
			title: __("Record Acknowledgement"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("An acknowledgement records that the employee has SEEN the review. It is not an approval and it does not change any score. Use this when the employee signed a printed copy instead of using self-service.")}</div>` },
				{ fieldtype: "Data", fieldname: "acknowledged_by", label: __("Acknowledged by"),
					description: __("Who signed. Recorded alongside your own user, which is stored as the person who entered it.") },
				{ fieldtype: "Small Text", fieldname: "comments", label: __("Employee comments") },
			],
			primary_action_label: __("Record"),
			primary_action: (v) => {
				this.call("record_acknowledgement", {
					appraisal: name, comments: v.comments || null,
					acknowledged_by: v.acknowledged_by || null,
				}).then(() => {
					d.hide();
					frappe.show_alert({ message: __("Acknowledgement recorded"), indicator: "green" });
					this.render();
				}).catch(() => {});
			},
		});
		d.show();
	}

	newTemplateDialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Appraisal Template"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("Objectives and their weightings. The weightings must total exactly 100%.")}</div>` },
				{ fieldtype: "Data", fieldname: "kra_title", label: __("Template name"), reqd: 1 },
				{ fieldtype: "Table", fieldname: "goals", label: __("Objectives"), reqd: 1,
					cannot_add_rows: false,
					fields: [
						{ fieldtype: "Small Text", fieldname: "kra", label: __("Objective"), in_list_view: 1, reqd: 1 },
						{ fieldtype: "Float", fieldname: "per_weightage", label: __("Weight %"), in_list_view: 1, reqd: 1 },
					] },
			],
			primary_action_label: __("Create"),
			primary_action: (v) => {
				d.hide();
				this.call("create_appraisal_template", {
					kra_title: v.kra_title, goals: JSON.stringify(v.goals || []),
				}).then(() => {
					frappe.show_alert({ message: __("Template created"), indicator: "green" });
					this.render();
				});
			},
		});
		d.show();
	}

	newCycleDialog(templates) {
		const d = new frappe.ui.Dialog({
			title: __("New Performance Cycle"),
			fields: [
				{ fieldtype: "HTML", options:
					`<div class="ahr-note">${__("Creating the cycle does not create any appraisal. Use Preview to see who is in scope, then Generate.")}</div>` },
				{ fieldtype: "Data", fieldname: "cycle_name", label: __("Cycle name"), reqd: 1,
					default: __("Annual Review {0}", [new Date().getFullYear()]) },
				{ fieldtype: "Link", fieldname: "company", options: "Company", label: __("Company"),
					reqd: 1, default: this.state.company },
				{ fieldtype: "Select", fieldname: "period_type", label: __("Period"),
					options: ["Annual", "Semiannual", "Quarterly", "Custom"].join("\n"), default: "Annual" },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Select", fieldname: "appraisal_template", label: __("Appraisal template"),
					reqd: 1, options: templates.map((t) => t.name).join("\n") },
				{ fieldtype: "Link", fieldname: "department", options: "Department",
					label: __("Department (optional)"), description: __("Leave empty for the whole company.") },
				{ fieldtype: "Int", fieldname: "minimum_service_months", label: __("Minimum service (months)"), default: 3 },
				{ fieldtype: "Section Break" },
				{ fieldtype: "Date", fieldname: "start_date", label: __("Period start"), reqd: 1 },
				{ fieldtype: "Date", fieldname: "end_date", label: __("Period end"), reqd: 1 },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "due_date", label: __("Reviews due by"),
					description: __("Managers are reminded as this approaches.") },
				{ fieldtype: "Check", fieldname: "employee_acknowledgement", label: __("Employee must acknowledge"), default: 1 },
			],
			primary_action_label: __("Create cycle"),
			primary_action: (v) => {
				d.hide();
				this.call("create_performance_cycle", { data: JSON.stringify(v) }).then((r) => {
					frappe.msgprint({
						title: __("Cycle {0} created", [r.name]), indicator: "green",
						message: __("Now use Preview to check who is in scope, then Generate to create the appraisals."),
					});
					this.render();
				});
			},
		});
		d.show();
	}

	view_statutory() {
		const esc = frappe.utils.escape_html;
		const IRT = "IRT — Mapa Mensal de Remunerações";
		const INSS = "INSS — Folha de Remunerações";

		this.call("statutory_history", { company: this.state.company }).then((rows) => {
			this.$content.html(
				this.what(__("This screen PREPARES and VALIDATES IRT and INSS declaration data. It does not submit anything to AGT or INSS — you enter the declaration in the government portal and record the reference here.")) +
				this.panel({ title: __("IRT / INSS Declarations"), icon: "fa-institution",
					subtitle: __("Prepare and check declaration data, then key it into the government portal and record the reference.") },
					`<div class="ahr-note" style="margin-bottom:12px">
						<b>${__("This screen does not file anything electronically.")}</b><br>
						${__("Process: Validate → Generate working file → enter the declaration in the government portal → record the portal reference.")}<br>
						<b>${__("Neither AGT nor INSS publishes an upload format.")}</b><br>
						${__("Both declarations are delivered on their own portal — AGT: Serviços → Declarações → IRT → Entregar → Mapa de Remunerações; INSS: INSS Virtual. What this screen produces is a working file to key from and reconcile against, plus a register of what was declared. It is not an official submission format, because none exists.")}
					</div>
					<div class="ahr-filters">
						<div class="st-type"></div><div class="st-from"></div><div class="st-to"></div>
						<button class="btn btn-xs btn-default st-validate">${__("Validate")}</button>
						<button class="btn btn-xs btn-primary st-file">${__("Generate working file")}</button>
					</div>
					<div class="st-result" style="margin-top:12px"></div>`) +
				this.panel({ title: __("Declaration register"), icon: "fa-archive",
					subtitle: __("Every declaration prepared here, and whether it has been filed.") },
					rows.length
						? `<table class="ahr-table"><thead><tr><th>${__("Type")}</th><th>${__("Period")}</th>
							<th>${__("Employees")}</th><th class="num">${__("Total")}</th><th>${__("Status")}</th>
							<th>${__("Reference")}</th><th></th></tr></thead><tbody>${rows.map((r) =>
							`<tr><td>${esc(r.submission_type)}</td>
							<td>${this.d(r.period_start)} → ${this.d(r.period_end)}</td>
							<td>${r.employees}</td><td class="num">${this.money(r.total_amount)}</td>
							<td><span class="ahr-badge ${r.status === "Generated" ? "draft" : "submitted"}">${esc(r.status)}</span></td>
							<td>${esc(r.reference || "—")}</td>
							<td>${r.status === "Generated"
								? `<button class="btn btn-xs btn-default st-ref" data-n="${esc(r.name)}">${__("Record submission")}</button>` : ""}</td></tr>`
						).join("")}</tbody></table>`
						: this.blank({
						icon: "fa-institution",
						title: __("No declaration has been prepared yet."),
						body: __("Validate a period to find employees missing a NIF or social security number, generate the working file, key the declaration into the government portal, then record the reference the portal gives you."),
						who: __("Prepared by Payroll each month. Downloading the file is NOT a submission — the status stays Generated until a reference is recorded."),
					}))
			);

			const type = frappe.ui.form.make_control({
				df: { fieldtype: "Select", label: __("Declaration"), options: [IRT, INSS] },
				parent: this.$content.find(".st-type"), render_input: true });
			const from = frappe.ui.form.make_control({
				df: { fieldtype: "Date", label: __("From") },
				parent: this.$content.find(".st-from"), render_input: true });
			const to = frappe.ui.form.make_control({
				df: { fieldtype: "Date", label: __("To") },
				parent: this.$content.find(".st-to"), render_input: true });
			// `default` in the df is not applied by make_control on this path — the three
			// fields rendered empty, so the screen opened with no declaration type and no
			// period, and pressing Generate asked the server for submission type "".
			// Setting the value explicitly is what the rest of this file does.
			type.set_value(IRT);
			from.set_value(frappe.datetime.month_start());
			to.set_value(frappe.datetime.month_end());

			/* One renderer for both buttons — Validate and Generate ask the same
			 * question of the same service, so they must give the same answer. */
			const showValidation = (v, note) => {
				this.$content.find(".st-result").html(
					(note ? `<div class="ahr-note" style="margin-bottom:10px">${note}</div>` : "") +
					`<div class="ahr-cards" style="margin-bottom:10px">
						<div class="ahr-card"><div class="k">${__("Employees")}</div><div class="v">${v.employees}</div></div>
						<div class="ahr-card"><div class="k">${__("Errors")}</div><div class="v">${v.errors.length}</div></div>
						<div class="ahr-card"><div class="k">${__("Warnings")}</div><div class="v">${v.warnings.length}</div></div>
					</div>` +
					(v.errors.length
						? `<table class="ahr-table"><thead><tr><th>${__("Code")}</th><th>${__("Problem")}</th></tr></thead>
							<tbody>${v.errors.map((e) => `<tr><td>${esc(e.code)}</td><td>${esc(e.message)}</td></tr>`).join("")}</tbody></table>`
						: `<div class="ahr-note">${__("No blocking problems. The declaration can be produced.")}</div>`) +
					(v.warnings.length
						? `<table class="ahr-table" style="margin-top:10px"><thead><tr><th>${__("Code")}</th><th>${__("Warning")}</th></tr></thead>
							<tbody>${v.warnings.map((e) => `<tr><td>${esc(e.code)}</td><td>${esc(e.message)}</td></tr>`).join("")}</tbody></table>` : ""));
			};

			const validate = () => this.action("statutory_validate", {
				submission_type: type.get_value(), company: this.state.company,
				period_start: from.get_value(), period_end: to.get_value(),
			});

			this.$content.find(".st-validate").on("click", () => {
				validate().then((v) => showValidation(v))
					.catch((err) => this.fail(__("Could not validate the period"), err));
			});

			/* Generate checks FIRST, and downloads only if there is something to
			 * download.
			 *
			 * It used to open the endpoint straight into a new tab. The endpoint is
			 * right to refuse when there is no approved payroll for the period — but
			 * because the refusal happened during a page navigation, the user was
			 * shown a raw Python traceback instead of the sentence this screen
			 * already knows how to display. Asking the same service the Validate
			 * button asks means the answer arrives as data, in the app's own words.
			 *
			 * The download itself then goes through a hidden iframe rather than
			 * window.open: after an await the browser no longer treats the call as
			 * part of the click, so a popup blocker would eat a new tab — and if the
			 * server does refuse in the gap between checking and downloading, the
			 * traceback lands in an invisible frame instead of in the user's face. */
			this.$content.find(".st-file").on("click", () => {
				validate().then((v) => {
					if (!v.valid) {
						showValidation(v, __("Nothing was downloaded. Fix the problems below, then generate the working file again."));
						frappe.show_alert({
							message: __("The declaration cannot be produced yet — see the problems listed."),
							indicator: "orange",
						});
						return;
					}
					showValidation(v, __("Generating the working file…"));
					const url = "/api/method/isoft_angola_hr.isoft_angola_hr.hr_api.statutory_working_file"
						+ "?submission_type=" + encodeURIComponent(type.get_value())
						+ "&company=" + encodeURIComponent(this.state.company || "")
						+ "&period_start=" + encodeURIComponent(from.get_value() || "")
						+ "&period_end=" + encodeURIComponent(to.get_value() || "");
					const frame = document.createElement("iframe");
					frame.style.display = "none";
					frame.src = url;
					document.body.appendChild(frame);
					setTimeout(() => { if (frame.parentNode) frame.parentNode.removeChild(frame); }, 60000);
				}).catch((err) => this.fail(__("Could not generate the working file"), err));
			});

			this.$content.find(".st-ref").on("click", (e) => {
				const name = $(e.currentTarget).data("n");
				const d = new frappe.ui.Dialog({
					title: __("Record submission"),
					fields: [
						{ fieldtype: "HTML", options:
							`<div class="ahr-note">${__("Enter the reference the portal issued. A declaration is not marked as submitted just because a file was produced.")}</div>` },
						{ fieldtype: "Data", fieldname: "reference", reqd: 1,
							label: __("Portal reference") },
						{ fieldtype: "Date", fieldname: "on", label: __("Submitted on"),
							default: frappe.datetime.get_today() },
						{ fieldtype: "Select", fieldname: "status", label: __("Status"),
							options: ["Submitted", "Accepted", "Rejected"], default: "Submitted" },
					],
					primary_action_label: __("Save"),
					primary_action: (v) => {
						d.hide();
						this.call("statutory_record_submission", {
							name: name, reference: v.reference, submitted_on: v.on,
							status: v.status,
						}).then(() => this.render());
					},
				});
				d.show();
			});
		});
	}

}
