// Copyright (c) 2026, Abbass Chokor and contributors
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

	new AngolaHR(page);
};

const API = "isoft_angola_hr.isoft_angola_hr.api.";
const NAV = [
	{ key: "overview", label: "Overview", icon: "fa-th-large" },
	{ key: "employees", label: "Employees", icon: "fa-users" },
	{ key: "attendance", label: "Attendance", icon: "fa-calendar-check-o" },
	{ key: "occurrences", label: "Occurrences", icon: "fa-exclamation-triangle" },
	{ key: "timesheets", label: "Timesheets", icon: "fa-list-alt" },
	{ key: "profiles", label: "Salary Profiles", icon: "fa-id-card-o" },
	{ key: "payroll", label: "Payroll", icon: "fa-cogs" },
	{ key: "slips", label: "Salary Slips", icon: "fa-file-text-o" },
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
		this.state = { company: null, companies: [], currency: "AOA", view: "overview" };
		this.build();
		this.boot();
	}

	call(method, args = {}) {
		return frappe.call({ method: API + method, args }).then((r) => r.message);
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
						<select class="ahr-company form-control"></select>
						<button class="btn btn-default ahr-fs" title="${__("Fullscreen")}"><i class="fa fa-arrows-alt"></i></button>
						<button class="btn btn-default ahr-refresh" title="${__("Refresh")}"><i class="fa fa-refresh"></i></button>
					</div>
				</div>
				<div class="ahr-content"></div>
			</div>`).appendTo(this.page.body);

		this.$shell = shell;
		this.$tabs = shell.find(".ahr-tabs");
		this.$content = shell.find(".ahr-content");
		this.$company = shell.find(".ahr-company");

		this.renderNav();
		shell.find(".ahr-refresh").on("click", () => this.render());
		shell.find(".ahr-fs").on("click", () => this.toggleFullscreen());
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
		this.call("get_overview").then((o) => {
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
			this.render(o);
		});
	}

	go(view) {
		this.state.view = view;
		this.render();
	}

	render(preOverview) {
		this.$tabs.find(".ahr-tab, .ahr-dd-item").removeClass("active");
		this.$tabs.find(`[data-key="${this.state.view}"]`).addClass("active");
		// Highlight the dropdown tab that owns the active view (Settings group).
		const grp = NAV.find((n) => n.children && n.children.some((c) => c.key === this.state.view));
		if (grp) this.$tabs.find(`.ahr-tab-wrap[data-group="${grp.group}"] .ahr-tab-dd`).addClass("active");
		this.$content.html(`<div class="ahr-empty"><i class="fa fa-spinner fa-spin"></i> ${__("Loading")}…</div>`);
		const fn = this["view_" + this.state.view];
		if (fn) fn.call(this, preOverview);
	}

	// ---- helpers ----
	table(cols, rows, opts = {}) {
		if (!rows || !rows.length) return `<div class="ahr-empty">${__("No records")}</div>`;
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
	// Smart salary-slip lifecycle badge: Draft → Submitted → Accrued → Paid; Cancelled.
	slipStatus(status) {
		const cls = { Draft: "draft", Submitted: "submitted", Accrued: "accrued", Paid: "paid", Cancelled: "cancelled" };
		const s = status || "Draft";
		return `<span class="ahr-badge ${cls[s] || "draft"}">${__(s)}</span>`;
	}
	panel(title, inner) {
		return `<div class="ahr-panel"><h5>${title}</h5>${inner}</div>`;
	}

	// ============================ VIEWS ============================
	view_overview(pre) {
		const esc = frappe.utils.escape_html;
		const done = (o) => {
			const c = o.cards;
			const cards = [
				{ l: "Active Employees", v: c.active_employees },
				{ l: "Salary Profiles", v: c.salary_profiles },
				{ l: "Submitted Slips", v: c.submitted_slips },
				{ l: "Net Paid (month)", v: this.money(c.net_paid_month) },
			]
				.map((x) => `<div class="ahr-card"><div class="lbl">${__(x.l)}</div><div class="val">${x.v}</div></div>`)
				.join("");

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
				`<div class="ahr-cards">${cards}</div>` +
					`<div class="ahr-chart-grid">
						${this.panel(__("Net Pay Trend"), `<div class="ahr-chart" data-ch="trend"></div>`)}
						${this.panel(__("Salary Slips by Status"), `<div class="ahr-chart" data-ch="status"></div>`)}
					</div>` +
					`<div class="ahr-chart-grid">
						${this.panel(__("Headcount by Department"), `<div class="ahr-chart" data-ch="dept"></div>`)}
						${this.panel(__("Upcoming Holidays"), holHtml)}
					</div>`
			);
			this.renderOverviewCharts(o);
		};
		pre ? done(pre) : this.call("get_overview", { company: this.state.company }).then(done);
	}

	renderOverviewCharts(o) {
		if (typeof frappe.Chart === "undefined") return;
		const el = (k) => this.$content.find(`.ahr-chart[data-ch="${k}"]`)[0];
		const SC = { Draft: "#f59e0b", Submitted: "#3b82f6", Accrued: "#6366f1", Paid: "#10b981", Cancelled: "#ef4444" };

		const t = o.net_pay_trend || [];
		if (t.length && el("trend"))
			new frappe.Chart(el("trend"), {
				data: { labels: t.map((x) => x.label), datasets: [{ name: __("Net Pay"), values: t.map((x) => x.total) }] },
				type: "line", height: 240, colors: ["#2563eb"],
				lineOptions: { regionFill: 1, hideDots: 0 }, axisOptions: { xIsSeries: 1 },
				tooltipOptions: { formatTooltipY: (d) => this.money(d) },
			});

		const st = o.slip_status || [];
		if (st.length && el("status"))
			new frappe.Chart(el("status"), {
				data: { labels: st.map((x) => __(x.status)), datasets: [{ values: st.map((x) => x.count) }] },
				type: "donut", height: 240, colors: st.map((x) => SC[x.status] || "#6c5ce7"),
			});

		const dp = o.headcount_by_dept || [];
		if (dp.length && el("dept"))
			new frappe.Chart(el("dept"), {
				data: { labels: dp.map((x) => x.department), datasets: [{ name: __("Employees"), values: dp.map((x) => x.count) }] },
				type: "bar", height: 260, colors: ["#1e40af"],
			});
	}

	view_employees() {
		const load = (search) =>
			this.call("list_employees", { company: this.state.company, search }).then((rows) => {
				const tbl = this.table(
					[
						{ key: "employee_name", label: "Name" },
						{ key: "designation", label: "Designation" },
						{ key: "department", label: "Department" },
						{ key: "custom_nif", label: "NIF" },
						{ key: "date_of_joining", label: "Joined", date: true },
					],
					rows,
					{ id: "name" }
				);
				this.$content.find(".ahr-emp-table").html(tbl);
				this.$content.find("tr.clickable").on("click", (e) =>
					this.openEmployee($(e.currentTarget).data("id"))
				);
			});
		this.$content.html(
			`<div class="ahr-filters"><div class="ahr-field"><label>${__("Search")}</label>
				<input type="text" class="ahr-emp-search" placeholder="${__("Name or ID")}"></div>
					<button class="btn btn-primary btn-sm ahr-new-emp" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("New Employee")}</button></div>
			<div class="ahr-panel ahr-emp-table"></div>`
		);
		const $s = this.$content.find(".ahr-emp-search");
		$s.on("keyup", frappe.utils.debounce(() => load($s.val()), 300));
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
				this.panel(__("Holiday List"),
					`<div class="ahr-form-grid"><div><b>${__("From")}:</b> ${this.d(h.from_date)}</div>
					<div><b>${__("To")}:</b> ${this.d(h.to_date)}</div>
					<div><b>${__("Holidays")}:</b> ${h.total_holidays}</div></div>`) +
				this.panel(__("Holidays"), tbl)
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
				<div><b>${__("Payroll Payable Account")}:</b> ${e.custom_payroll_payable_account || "-"}</div></div>`;
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
					this.panel(__("Recent Slips"), slips)
			);
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
			{ dates: true, employee: true }
		);
		const $btn = $(`<button class="btn btn-primary btn-sm" style="align-self:flex-end;"><i class="fa fa-plus"></i> ${__("Mark")}</button>`);
		this.$content.find(".ahr-filters").append($btn);
		$btn.on("click", () => this.markAttendanceDialog());
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
				this.$content.find(".ahr-list").html(
					`<div class="ahr-list-meta">${rows.length} ${__("occurrences")}</div>` +
						this.table(cols, rows, { id: "name" })
				);
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
		const info = `<div class="ahr-form-grid">
			<div><b>${__("Employee")}:</b> ${esc(o.employee_name || o.employee)}</div>
			<div><b>${__("Occurrence Date")}:</b> ${this.d(o.occurrence_date)}</div>
			<div><b>${__("Type")}:</b> ${__(o.occurrence_type)}</div>
			<div><b>${__("Missing Hours")}:</b> ${flt(o.hours)}</div>
			<div><b>${__("Status")}:</b> ${this.occStatus(o.status)}</div>
			<div><b>${__("Justification Deadline")}:</b> ${this.d(o.justification_deadline)}</div>
			<div><b>${__("Reason")}:</b> ${o.justification_reason ? esc(o.justification_reason) : "—"}</div>
			<div><b>${__("Remarks")}:</b> ${o.remarks ? esc(o.remarks) : "—"}</div></div>`;
		const d = new frappe.ui.Dialog({ title: o.name, size: "large" });
		$(d.body).html(this.panel(__("Occurrence"), info) +
			`<div class="ahr-doc-actions">
				${o.status !== "Justified" ? `<button class="btn btn-xs btn-primary occ-justify">${__("Justify")}</button>` : ""}
				${o.status !== "Unjustified" ? `<button class="btn btn-xs btn-default occ-unjust">${__("Mark Unjustified")}</button>` : ""}
				${o.status !== "Pending Justification" ? `<button class="btn btn-xs btn-default occ-pending">${__("Reset to Pending")}</button>` : ""}
				<button class="btn btn-xs btn-danger occ-delete">${__("Delete")}</button>
			</div>`);
		$(d.body).find(".occ-justify").on("click", () => { d.hide(); this.justifyOccurrence(o.name); });
		$(d.body).find(".occ-unjust").on("click", () =>
			this.call("set_occurrence_status", { name: o.name, status: "Unjustified" }).then(() => { d.hide(); this.go("occurrences"); }));
		$(d.body).find(".occ-pending").on("click", () =>
			this.call("set_occurrence_status", { name: o.name, status: "Pending Justification" }).then(() => { d.hide(); this.go("occurrences"); }));
		$(d.body).find(".occ-delete").on("click", () =>
			frappe.confirm(__("Delete this occurrence?"), () =>
				this.call("delete_occurrence", { name: o.name }).then(() => { d.hide(); this.go("occurrences"); })));
		d.show();
	}

	justifyOccurrence(name) {
		const d = new frappe.ui.Dialog({
			title: __("Justify Occurrence"),
			fields: [
				{ fieldname: "reason", label: __("Justification Reason"), fieldtype: "Link", options: "Isoft Absence Reason", reqd: 1,
				  get_query: () => ({ filters: { is_active: 1 } }) },
				{ fieldname: "document", label: __("Supporting Document"), fieldtype: "Attach" },
				{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
			],
			primary_action_label: __("Mark Justified"),
			primary_action: (v) => {
				this.call("justify_occurrence", { name, reason: v.reason, document: v.document || null, remarks: v.remarks || null })
					.then(() => { d.hide(); frappe.show_alert({ message: __("Justified"), indicator: "green" }); this.go("occurrences"); });
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
		const STATUSES = ["", "Draft", "Submitted", "Accrued", "Paid", "Cancelled"];
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
			const dlg = new frappe.ui.Dialog({ title: `${s.employee_name} · ${this.d(s.start_date)} → ${this.d(s.end_date)}`, size: "large" });
			$(dlg.body).html(
				this.panel(__("Salary Slip"), info) +
					this.panel(__("Earnings"), e) + this.panel(__("Deductions"), d) + this.panel(__("Totals"), tot)
			);
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
					${locked ? `<span class="ahr-lock-note">${pe ? __("Paid — delete the Payment Entry first to cancel or delete this slip.") : __("Accrued — delete the Journal Entry first to cancel or delete this slip.")}</span>` : ""}
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

	// ---- Salary Profiles ----
	view_profiles() {
		this.call("list_salary_profiles", { company: this.state.company }).then((rows) => {
			this.$content.html(
				`<div class="ahr-filters"><button class="btn btn-primary btn-sm ahr-new-prof"><i class="fa fa-plus"></i> ${__("New Profile")}</button></div>
				<div class="ahr-panel ahr-prof-list"></div>`
			);
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
			this.$content.find(".ahr-new-prof").on("click", () => this.editProfile({}));
			this.$content.find("tr.clickable").on("click", (e) => {
				const row = rows.find((r) => r.name === $(e.currentTarget).data("id"));
				this.editProfile(row);
			});
		});
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
	}

	// ---- Payroll Entries ----
	view_payroll() {
		const esc = frappe.utils.escape_html;
		this._excluded = new Set();
		const opts = (this.state.companies || [])
			.map((c) => `<option value="${esc(c)}" ${c === this.state.company ? "selected" : ""}>${esc(c)}</option>`)
			.join("");
		this.$content.html(
			this.panel(
				__("New Payroll Entry"),
				`<div class="ahr-form-grid">
					<div class="ahr-field"><label>${__("Company")}</label><select class="pe-company"><option value="">--</option>${opts}</select></div>
					<div class="ahr-field"><label>${__("Start Date")}</label><input type="date" class="pe-start"></div>
					<div class="ahr-field"><label>${__("End Date")}</label><input type="date" class="pe-end"></div>
					<div class="ahr-field"><label>${__("Department")}</label><select class="pe-dept"><option value="">${__("All")}</option></select></div>
					<div class="ahr-field"><label>${__("Branch")}</label><select class="pe-branch"><option value="">${__("All")}</option></select></div>
					<div class="ahr-field"><label>${__("Designation")}</label><select class="pe-desig"><option value="">${__("All")}</option></select></div>
				</div><br>
				<label style="margin-right:16px;"><input type="checkbox" class="pe-valatt"> ${__("Validate Attendance")}</label>
				<label style="margin-right:16px;"><input type="checkbox" class="pe-timesheet"> ${__("Based on Timesheet")}</label><br><br>
				<button class="btn btn-primary btn-sm pe-preview"><i class="fa fa-eye"></i> ${__("Preview")}</button>`
			) +
				`<div class="ahr-preview-wrap"></div>` +
				this.panel(
					__("History"),
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
							{ key: "salary_slips_submitted", label: "Status", render: (v) => this.statusBadge(v ? 1 : 0) },
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

		// Prefill the period from the configured payroll cycle (e.g. 23 → 22).
		if (this.state.default_period) {
			this.$content.find(".pe-start").val(this.state.default_period.start);
			this.$content.find(".pe-end").val(this.state.default_period.end);
		}

		this._refreshHistory = list;
		this.$content.find(".pe-company").val(this.state.company || "");
		this.$content.find(".pe-company").on("change", (e) => {
			loadOpts($(e.currentTarget).val());
			list();
		});
		this.$content.find(".pe-f1, .pe-f2").on("change", list);
		this.$content.find(".pe-preview").on("click", () => this.runPreview());
		loadOpts(this.state.company);
		list();
	}

	peFilters() {
		return {
			company: this.$content.find(".pe-company").val(),
			start_date: this.$content.find(".pe-start").val(),
			end_date: this.$content.find(".pe-end").val(),
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
		if (!f.start_date || !f.end_date) return frappe.msgprint(__("Select start and end date"));
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
		const included = (r) => !this._excluded.has(r.employee);
		const total = rows.filter(included).reduce((s, r) => s + flt(r.net_pay), 0);
		const count = rows.filter(included).length;
		const showNatal = rows.some((r) => flt(r.christmas) > 0 || flt(r.natal_default) > 0);

		const body = rows
			.map((r) => {
				const off = this._excluded.has(r.employee);
				const hay = esc(`${r.employee_name || ""} ${r.designation || ""} ${r.department || ""} ${r.employee || ""}`.toLowerCase());
				return `<tr data-emp="${esc(r.employee)}" data-name="${esc(r.employee_name)}" data-search="${hay}" class="${off ? "pv-off" : ""}">
				<td class="pv-c"><input type="checkbox" class="pv-include" ${off ? "" : "checked"}></td>
				<td>${esc(r.employee_name || "")}<div class="pv-sub">${esc(r.designation || "")}</div></td>
				<td>${esc(r.department || "")}</td>
				<td class="num">${flt(r.payment_days)}/${flt(r.total_working_days)}</td>
				<td class="num">${this.money(r.base)}</td>
				<td class="num"><label class="pv-ferias-lbl"><input type="checkbox" class="pv-ferias" data-full="${flt(r.ferias_full)}" ${flt(r.vacation) > 0 ? "checked" : ""}> <span class="pv-ferias-amt">${flt(r.vacation) > 0 ? this.money(r.ferias_full) : "—"}</span></label></td>
				${showNatal ? `<td><input type="number" class="form-control input-xs pv-natal" value="${flt(r.christmas)}"></td>` : ""}
				<td><input type="number" class="form-control input-xs pv-f" data-k="overtime_amount" value="${flt(r.overtime_amount)}"></td>
				<td><input type="number" class="form-control input-xs pv-f" data-k="productivity_bonus" value="${flt(r.productivity_bonus)}"></td>
				<td><input type="number" class="form-control input-xs pv-f" data-k="adiantamento" value="${flt(r.adiantamento)}"></td>
				<td class="num">${this.money(r.taxable_income)}</td>
				<td class="num">${this.money(r.ss)}</td>
				<td class="num">${this.money(r.irt)}</td>
				<td class="num">${this.money(r.gross_pay)}</td>
				<td class="num"><b>${this.money(r.net_pay)}</b></td></tr>`;
			})
			.join("");

		$w.html(
			this.panel(
				__("Preview") +
					` &middot; <span class="text-muted pv-summary">${count} ${__("employees")} &middot; ${__("Total Net")}: ${this.money(total)}</span>`,
				`<div class="pv-search-wrap"><input type="text" class="form-control input-sm pv-search" placeholder="${__("Search employee, department or designation...")}"></div>
					<div class="pv-scroll"><table class="ahr-table pv-table"><thead><tr>
					<th class="pv-c"><input type="checkbox" class="pv-all" checked></th>
					<th>${__("Employee")}</th><th>${__("Department")}</th><th class="num">${__("Days")}</th>
					<th class="num">${__("Base")}</th><th class="num">${__("Vacation")}</th>${showNatal ? `<th class="num">${__("Christmas")}</th>` : ""}<th>${__("Overtime")}</th><th>${__("Bonus")}</th><th>${__("Advance")}</th>
					<th class="num">${__("Taxable")}</th><th class="num">${__("SS")}</th><th class="num">${__("IRT")}</th>
					<th class="num">${__("Gross")}</th><th class="num">${__("Net")}</th></tr></thead>
					<tbody>${body}</tbody></table></div>
				<br><button class="btn btn-default btn-sm pv-recalc"><i class="fa fa-refresh"></i> ${__("Recalculate")}</button>
				<button class="btn btn-primary btn-sm pv-create" style="margin-left:8px;"><i class="fa fa-cogs"></i> ${__("Create Salary Slips")} (${count})</button>`
			)
		);

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
			$w.find(".pv-include").prop("checked", on).each((_, c) => {
				const emp = $(c).closest("tr").data("emp");
				on ? this._excluded.delete(emp) : this._excluded.add(emp);
			});
			$w.find("tbody tr").toggleClass("pv-off", !on);
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
		// approximate net for the live summary using last computed value in the Net cell
		const txt = $tr.find("td:last").text().replace(/[^\d.-]/g, "");
		return flt(txt);
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

	_bulkResult(label, res) {
		let msg = `${label}: ${res.created} ${__("created")}, ${res.skipped} ${__("skipped")}`;
		if (res.total) msg += ` · ${__("Total")}: ${this.money(res.total)}`;
		const bad = res.errors && res.errors.length;
		frappe.show_alert({ message: msg, indicator: bad ? "orange" : "green" });
		if (bad) frappe.msgprint({ title: __("Some entries were not created"), message: res.errors.join("<br>"), indicator: "orange" });
	}

	openPayrollEntry(name) {
		const esc = frappe.utils.escape_html;
		this.call("get_payroll_entry", { name }).then((r) => {
			const doc = r.doc;
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
			const head = `<div class="ahr-form-grid">
				<div><b>${__("Period")}:</b> ${this.d(doc.start_date)} → ${this.d(doc.end_date)}</div>
				<div><b>${__("Employees")}:</b> ${doc.number_of_employees}</div>
				<div><b>${__("Total Net Pay")}:</b> ${this.money(doc.total_net_pay)}</div>
				<div><b>${__("Status")}:</b> ${this.statusBadge(doc.salary_slips_submitted ? 1 : 0)}</div></div>`;
			const d = new frappe.ui.Dialog({ title: doc.name, size: "extra-large" });
			$(d.body).html(
				this.panel(__("Payroll Entry"), head) +
					this.panel(__("Employees"), emp) +
					`<div class="ahr-doc-actions">
						${doc.salary_slips_submitted ? `<button class="btn btn-xs btn-primary pe-bulk-je">${__("Create Journal Entries")}</button>` : ""}
						${doc.salary_slips_submitted ? `<button class="btn btn-xs btn-primary pe-bulk-pe">${__("Make Payment")}</button>` : ""}
						${doc.salary_slips_submitted ? `<button class="btn btn-xs btn-default pe-cancel">${__("Cancel Slips")}</button>` : ""}
						<button class="btn btn-xs btn-danger pe-delete">${__("Delete Entry")}</button>
					</div>
					<div class="text-muted small" style="margin-top:6px;">${__("Tick employees to act on a subset; actions apply to the ticked rows.")}</div>`
			);
			const selectedEmps = () =>
				$(d.body).find(".pe-sel:checked").not(":disabled").map((_, c) => $(c).closest("tr").data("emp")).get();
			$(d.body).find(".pe-all").on("change", (e) => {
				$(d.body).find(".pe-sel").not(":disabled").prop("checked", e.currentTarget.checked);
			});
			$(d.body).find(".pe-bulk-je").on("click", () => {
				const emps = selectedEmps();
				if (!emps.length) return frappe.msgprint(__("Select at least one employee"));
				frappe.confirm(__("Create the accrual Journal Entry for {0} selected slip(s)?", [emps.length]), () => {
					frappe.dom.freeze(__("Creating journal entries..."));
					this.call("make_bulk_journal_entry", { name, employees: JSON.stringify(emps) })
						.then((res) => { frappe.dom.unfreeze(); this._bulkResult(__("Journal Entries"), res); d.hide(); this.openPayrollEntry(name); })
						.catch(() => frappe.dom.unfreeze());
				});
			});
			$(d.body).find(".pe-bulk-pe").on("click", () => {
				const emps = selectedEmps();
				if (!emps.length) return frappe.msgprint(__("Select at least one employee"));
				this.paymentDialog(`${__("Make Payment")} (${emps.length})`, (v) => {
					frappe.dom.freeze(__("Creating payment entries..."));
					this.call("make_bulk_payment_entry", { name, payment_account: v.payment_account, posting_date: v.posting_date, employees: JSON.stringify(emps) })
						.then((res) => { frappe.dom.unfreeze(); this._bulkResult(__("Payment Entries"), res); d.hide(); this.openPayrollEntry(name); })
						.catch(() => frappe.dom.unfreeze());
				});
			});
			if (!doc.salary_slips_submitted) {
				d.set_primary_action(__("Submit All Slips"), () => {
					this.call("submit_payroll_entry", { name }).then((res) => {
						d.hide();
						frappe.show_alert({ message: __("Submitted {0} slips", [res.submitted]), indicator: "green" });
						this.render();
					});
				});
			}
			$(d.body).find(".pe-cancel").on("click", () => {
				frappe.confirm(__("Cancel all salary slips of this entry?"), () => {
					this.call("cancel_payroll_entry", { name }).then((n) => {
						frappe.show_alert({ message: __("Cancelled {0} slips", [n]), indicator: "orange" });
						d.hide();
						this.render();
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
			this.call("save_irt_table", { brackets: JSON.stringify(this.collectIrt()) }).then((n) =>
				frappe.show_alert({ message: __("Saved {0} brackets", [n]), indicator: "green" })
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
				this.panel(
					__("Company"),
					`<div class="ahr-form-grid">
						<div class="ahr-field"><label>${__("Default Holiday List")}</label><div class="set-link-hl"></div></div>
						${num("payroll_cycle_start_day", "Payroll Cycle Start Day")}
					</div>
					<div class="text-muted small" style="margin-top:6px;">${__("Used for working-day calculation and the Upcoming Holidays panel. Applies to {0}.", [s._company || __("the default company")])}</div>`
				) +
				this.panel(
					__("Parameters"),
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
						__("Working Days"),
						`<div class="ahr-form-grid">
							${sel("working_days_basis", "Working Days Basis", ["Auto (Holiday List)", "Standard (Fixed)"])}
							${num("standard_working_days", "Standard Working Days")}
						</div>`
					) +
					this.panel(
						__("Enabled Components"),
						chk("enable_productivity_bonus", "Prémio de Produtividade") +
							chk("enable_overtime", "Horas Extras") +
							chk("enable_adiantamento", "Adiantamento") +
							chk("enable_family_allowance", "Abono de Família")
					) +
					this.panel(
						__("Net Pay (Journal Entry)"),
						`<div class="ahr-form-grid">
							${acc("payroll_payable_account", "Payroll Payable Account")}
							${acc("salary_payment_account", "Salary Payment Account")}
						</div>
						<div class="text-muted small" style="margin-top:6px;">${__("Payroll Payable is credited with the net pay (accrual); the Salary Payment account is credited when salaries are paid. An Employee's own Payroll Payable Account overrides this. All other accounts are set per component below.")}</div>`
					) +
					this.panel(
					__("Account per Component"),
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
}
