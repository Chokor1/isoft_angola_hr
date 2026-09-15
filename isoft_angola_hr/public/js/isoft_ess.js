/* Isoft Angola HR — Employee Self-Service (/ess).
 *
 * Seven screens: Início, Perfil, Recibos, Férias, Assiduidade, Documentos, Pedidos.
 * Every one of them is a thin renderer over a Phase 3 service endpoint. There is no
 * employee identifier anywhere in this file — not in a URL, not in a request body —
 * because the server derives it from the session. There is nothing here to tamper with.
 */
/* eslint-env browser */
/* global IsoftSS */

(function (SS) {
	"use strict";

	var __ = SS.__, esc = SS.esc, money = SS.money, date = SS.date;

	function card(title, inner, action) {
		return '<section class="ss-card">' +
			(title ? '<div class="ss-card-head"><h2>' + esc(title) + "</h2>" +
				(action || "") + "</div>" : "") + inner + "</section>";
	}

	function kv(label, value, cls) {
		return '<div class="ss-kv"><dt>' + esc(label) + "</dt>" +
			'<dd class="' + (cls || "") + '">' + (value === null || value === undefined ||
				value === "" ? "—" : value) + "</dd></div>";
	}

	/* ==================================================================== HOME */
	function home(app) {
		return SS.api("my_dashboard").then(function (d) {
			app.data = d;
			var p = d.profile || {};

			// --- headline cards -------------------------------------------------
			var totalLeave = (d.leave_balance || []).reduce(function (a, r) {
				return a + (parseFloat(r.available) || 0);
			}, 0);
			var latest = (d.latest_payslips || [])[0];
			var att = d.attendance_summary || {};
			var openCount = (d.open_requests || []).length + (d.open_leave || []).length;

			var cards =
				'<div class="ss-grid">' +
				'<div class="ss-metric ' + (totalLeave > 0 ? "ok" : "warn") + '">' +
					'<div class="k">' + esc(__("Leave balance")) + "</div>" +
					'<div class="v">' + SS.num(totalLeave, 1) + "</div>" +
					'<div class="h">' + esc(__("days available")) + "</div></div>" +
				'<div class="ss-metric">' +
					'<div class="k">' + esc(__("Latest payslip")) + "</div>" +
					'<div class="v">' + (latest ? money(latest.net_pay, { bare: true }) : "—") + "</div>" +
					'<div class="h">' + (latest ? esc(SS.period(latest.start_date, latest.end_date))
						: esc(__("no payslips"))) + "</div></div>" +
				'<div class="ss-metric ' + (att.open_occurrences ? "warn" : "ok") + '">' +
					'<div class="k">' + esc(__("Attendance this month")) + "</div>" +
					'<div class="v">' + SS.num(att.Present || 0) + "</div>" +
					'<div class="h">' + (att.open_occurrences
						? esc(__("{0} occurrence(s) to explain", [att.open_occurrences]))
						: esc(__("no occurrences"))) + "</div></div>" +
				'<div class="ss-metric ' + (openCount ? "warn" : "") + '">' +
					'<div class="k">' + esc(__("Requests in progress")) + "</div>" +
					'<div class="v">' + SS.num(openCount) + "</div>" +
					'<div class="h">' + esc(__("awaiting a decision")) + "</div></div>" +
				"</div>";

			// --- contract -------------------------------------------------------
			var c = p.contract;
			var contractHtml;
			if (c) {
				var days = null;
				if (c.end_date && !c.is_open_ended) {
					days = Math.round((SS.parseDate(c.end_date) - SS.parseDate(SS.today()))
						/ 86400000);
				}
				contractHtml = card(__("My contract"),
					"<dl>" +
					kv(__("Type"), esc(c.contract_type)) +
					kv(__("Start"), date(c.start_date)) +
					kv(__("Fixed term"), c.is_open_ended
						? esc(__("Open-ended")) : date(c.end_date)) +
					kv(__("Status"), SS.statusBadge(c.status)) +
					(c.probation_end && c.probation_status !== "Not Applicable"
						? kv(__("Probation"), date(c.probation_end) + " " +
							SS.statusBadge(c.probation_status)) : "") +
					"</dl>" +
					(days !== null && days <= 90
						? '<div class="ss-alert warn" style="margin:12px 0 0"><i class="fa fa-clock-o"' +
						' aria-hidden="true"></i><span>' +
						esc(__("Your contract ends in {0} days. HR will be notified" +
							"automaticamente.", [days])) + "</span></div>"
						: ""));
			} else {
				contractHtml = card(__("My contract"),
					SS.empty("fa-file-text-o", __("You have no contract recorded in the system yet."),
						__("This does not affect your employment. Contact HR if you need a copy.")));
			}

			// --- leave balance --------------------------------------------------
			var leaveHtml = card(__("Leave balance"),
				(d.leave_balance || []).length
					? '<ul class="ss-list">' + d.leave_balance.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.leave_type) + "</div><div class='ss-li-sub'>" +
							esc(__("Entitlement {0} · Taken {1} · Pending {2}",
								[SS.num(r.entitlement, 1), SS.num(r.used, 1),
									SS.num(r.pending, 1)])) +
							"</div></div><div class='ss-li-right'><div class='ss-li-amount'>" +
							SS.num(r.available, 1) + "</div><div class='ss-li-sub'>" +
							esc(__("available")) + "</div></div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-calendar-o", __("No leave entitlement allocated."),
						__("HR allocates the days at the start of each period.")),
				'<button class="ss-btn primary sm" data-go="leave">' +
					esc(__("Request leave")) + "</button>");

			// --- open items -----------------------------------------------------
			var pend = (d.open_leave || []).map(function (r) {
				return { t: __("Leave"), s: r.leave_type + " · " + date(r.from_date) +
					" → " + date(r.to_date), st: r.status };
			}).concat((d.open_requests || []).map(function (r) {
				return { t: r.type, s: String(r.detail || ""), st: r.status };
			}));

			var pendHtml = card(__("Awaiting a decision"),
				pend.length
					? '<ul class="ss-list">' + pend.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.t) + "</div><div class='ss-li-sub'>" + esc(r.s) +
							"</div></div><div class='ss-li-right'>" +
							SS.statusBadge(r.st) + "</div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-check-circle-o", __("You have no pending requests."),
						__("Everything you submitted has been decided.")));

			// --- expiring documents ---------------------------------------------
			var docs = d.expiring_documents || [];
			var docHtml = docs.length ? card(__("Documents expiring"),
				'<div class="ss-alert warn"><i class="fa fa-exclamation-triangle" aria-hidden="true">' +
				"</i><span>" + esc(__("{0} document(s) need renewing.", [docs.length])) +
				"</span></div>" +
				'<ul class="ss-list">' + docs.map(function (r) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.document_type) + "</div><div class='ss-li-sub'>" +
						esc(__("Expiry")) + ": " + date(r.expiry_date) +
						"</div></div><div class='ss-li-right'>" + SS.statusBadge(r.status) +
						"</div></div></li>";
				}).join("") + "</ul>") : "";

			app.render(cards + '<div class="ss-two">' + contractHtml + leaveHtml + "</div>" +
				'<div class="ss-two">' + pendHtml + docHtml + "</div>");

			SS.on(app.$view, "[data-go]", "click", function () {
				location.hash = "#" + this.dataset.go;
			});
		});
	}

	/* ================================================================= PROFILE */
	function profile(app) {
		return SS.api("my_profile").then(function (p) {
			var editable = p.editable_fields || [];

			var personal = card(__("Personal details"), "<dl>" +
				kv(__("Name"), esc(p.employee_name)) +
				kv(__("Employee No."), esc(p.name)) +
				kv(__("Date of birth"), date(p.date_of_birth)) +
				kv(__("Mobile"), esc(p.cell_number)) +
				kv(__("Personal e-mail"), esc(p.personal_email)) +
				kv(__("Address"), esc(p.current_address)) +
				kv(__("Emergency contact"), esc(p.person_to_be_contacted)) +
				kv(__("Emergency phone"), esc(p.emergency_phone_number)) +
				"</dl>",
				'<button class="ss-btn sm" id="ss-edit">' + esc(__("Edit")) + "</button>");

			var employment = card(__("Employment"), "<dl>" +
				kv(__("Company"), esc(p.company)) +
				kv(__("Department"), esc(p.department)) +
				kv(__("Job title"), esc(p.designation)) +
				kv(__("Local"), esc(p.branch)) +
				kv(__("Line manager"), esc(p.reports_to)) +
				kv(__("Joined"), date(p.date_of_joining)) +
				kv(__("Contract type"), esc(p.contract ? p.contract.contract_type
					: p.employment_type)) +
				kv(__("Status"), SS.statusBadge(p.status)) +
				"</dl>");

			// Statutory identifiers are shown to the person they belong to, but are never
			// editable here and never appear in any list view (§83).
			var statutory = card(__("Tax and bank details"), "<dl>" +
				kv(__("NIF"), esc(p.custom_nif)) +
				kv(__("Social Security No."), esc(p.custom_inss_number)) +
				kv(__("IBAN"), esc(p.iban_masked)) +
				"</dl>" +
				'<div class="ss-alert info" style="margin-top:10px"><i class="fa fa-lock" ' +
				'aria-hidden="true"></i><span>' +
				esc(__("Only HR can change these details. To change the bank account," +
					"submeta um pedido — será validado antes de ser aplicado.")) + "</span></div>" +
				'<button class="ss-btn primary block" id="ss-bank" style="margin-top:10px">' +
				'<i class="fa fa-university" aria-hidden="true"></i> ' +
				esc(__("Request a bank account change")) + "</button>");

			app.render('<div class="ss-two">' + personal + employment + "</div>" + statutory);

			SS.qs("#ss-edit", app.$view).addEventListener("click", function () {
				var fields = [
					{ name: "cell_number", label: __("Mobile"), value: p.cell_number, type: "tel" },
					{ name: "personal_email", label: __("Personal e-mail"),
						value: p.personal_email, type: "email" },
					{ name: "current_address", label: __("Address"), type: "textarea",
						value: p.current_address },
					{ name: "person_to_be_contacted", label: __("Emergency contact"),
						value: p.person_to_be_contacted },
					{ name: "relation", label: __("Relationship"), value: p.relation },
					{ name: "emergency_phone_number", label: __("Emergency phone"),
						value: p.emergency_phone_number, type: "tel" }
				].filter(function (f) { return editable.indexOf(f.name) !== -1; });

				SS.modal({
					title: __("Edit contact details"),
					html: '<p class="hint" style="margin:0 0 12px;color:var(--ss-muted);' +
						'font-size:12.5px">' +
						esc(__("Only these fields can be changed by you. Salary, job title," +
							"NIF, Segurança Social e IBAN requerem o RH.")) + "</p>",
					fields: fields,
					okLabel: __("Save"),
					onSubmit: function (v) {
						return SS.api("update_my_profile", { values: JSON.stringify(v) })
							.then(function () {
								SS.toast(__("Details updated."), "ok");
								SS.announce(__("Details updated."));
								app.reload();
							});
					}
				});
			});

			SS.qs("#ss-bank", app.$view).addEventListener("click", function () {
				SS.modal({
					title: __("Bank account change"),
					html: '<div class="ss-alert warn"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("The IBAN is only changed once HR has verified the proof." +
							"O seu salário continua a ser pago para a conta actual até lá.")) +
						"</span></div>",
					fields: [
						{ name: "new_iban", label: __("New IBAN"), required: true,
							placeholder: "AO06 0000 0000 0000 0000 0000 0",
							hint: __("21 digits, as shown on the bank statement.") },
						{ name: "bank_name", label: __("Bank") }
					],
					okLabel: __("Submit request"),
					onSubmit: function (v) {
						return SS.api("request_bank_change", v).then(function (name) {
							SS.toast(__("Request {0} submitted for HR verification.", [name]), "ok");
							SS.announce(__("Bank change request submitted."));
						});
					}
				});
			});
		});
	}

	/* ================================================================ PAYSLIPS */
	function payslips(app, arg) {
		if (arg) return payslipDetail(app, arg);
		return SS.api("my_payslips", { limit: 36 }).then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("My payslips"),
					SS.empty("fa-file-text-o", __("You have no payslips available yet."),
						__("Only payslips already approved by the department appear here" +
							"de pessoal."))));
			}
			var list = '<ul class="ss-list">' + rows.map(function (r) {
				return '<li class="tap" tabindex="0" role="button" data-slip="' + esc(r.name) +
					'"><div class="ss-li-top"><div><div class="ss-li-title">' +
					esc(SS.period(r.start_date, r.end_date)) + "</div>" +
					'<div class="ss-li-sub">' + esc(__("Gross")) + " " +
					money(r.gross_pay, { bare: true }) + " · " + esc(__("Deductions")) + " " +
					money(r.total_deduction, { bare: true }) + "</div></div>" +
					'<div class="ss-li-right"><div class="ss-li-amount">' +
					money(r.net_pay, { bare: true }) + '</div><div class="ss-li-sub">' +
					esc(__("net")) + "</div></div></div></li>";
			}).join("") + "</ul>";

			var table = '<div class="ss-table-wrap"><table class="ss-table">' +
				"<thead><tr><th>" + esc(__("Period")) + "</th><th>" + esc(__("Date")) +
				'</th><th class="num">' + esc(__("Gross")) + '</th><th class="num">' +
				esc(__("Social Security")) + '</th><th class="num">' + esc(__("IRT")) +
				'</th><th class="num">' + esc(__("Net")) + "</th><th></th></tr></thead><tbody>" +
				rows.map(function (r) {
					return '<tr><td>' + esc(SS.period(r.start_date, r.end_date)) + "</td><td>" +
						date(r.posting_date) + '</td><td class="num">' +
						money(r.gross_pay, { bare: true }) + '</td><td class="num">' +
						money(r.ss_employee_amount, { bare: true }) + '</td><td class="num">' +
						money(r.irt_amount, { bare: true }) + '</td><td class="num"><b>' +
						money(r.net_pay, { bare: true }) + "</b></td><td>" +
						'<button class="ss-btn sm" data-slip="' + esc(r.name) + '">' +
						esc(__("View")) + "</button></td></tr>";
				}).join("") + "</tbody></table></div>";

			app.render('<section class="ss-card ss-desktop-table"><div class="ss-card-head">' +
				"<h2>" + esc(__("My payslips")) + "</h2></div>" + list + table + "</section>");

			SS.on(app.$view, "[data-slip]", "click", function () {
				location.hash = "#payslips/" + encodeURIComponent(this.dataset.slip);
			});
			SS.on(app.$view, "[data-slip]", "keydown", function (e) {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					location.hash = "#payslips/" + encodeURIComponent(this.dataset.slip);
				}
			});
		});
	}

	function payslipDetail(app, name) {
		return SS.api("my_payslip", { name: name }).then(function (s) {
			var ex = s.explanation || {};
			function lines(rows) {
				if (!rows || !rows.length) {
					return '<p class="ss-li-sub">' + esc(__("Nothing to show.")) + "</p>";
				}
				return "<dl>" + rows.map(function (r) {
					return kv(r.salary_component, money(r.amount, { bare: true }), "num");
				}).join("") + "</dl>";
			}

			// The PDF is produced by Frappe's own print engine. It is reachable only
			// because doc_permissions.py allows this employee to read this slip.
			var pdf = "/api/method/frappe.utils.print_format.download_pdf?doctype=" +
				encodeURIComponent("Isoft Salary Slip") + "&name=" + encodeURIComponent(s.name) +
				"&format=" + encodeURIComponent("Recibo de Vencimento") + "&no_letterhead=0";

			app.render(
				'<button class="ss-btn sm" data-back style="margin-bottom:12px">' +
					'<i class="fa fa-arrow-left" aria-hidden="true"></i> ' +
					esc(__("Payslips")) + "</button>" +
				card(SS.period(s.start_date, s.end_date),
					"<dl>" +
					kv(__("Payroll date"), date(s.posting_date)) +
					kv(__("Paid days"), esc(ex.days || "")) +
					"</dl>" +
					'<h3 style="font-size:13px;font-weight:800;margin:14px 0 6px">' +
					esc(__("Earnings")) + "</h3>" + lines(s.earnings) +
					'<h3 style="font-size:13px;font-weight:800;margin:14px 0 6px">' +
					esc(__("Deductions")) + "</h3>" + lines(s.deductions) +
					'<div class="ss-pay-total"><span class="l">' + esc(__("Net to receive")) +
					'</span><span class="v">' + money(s.net_pay) + "</span></div>" +

					// "How this was calculated" — the statutory trace, in words (§10).
					'<div class="ss-explain"><h3>' + esc(__("How this was calculated")) + "</h3>" +
					(ex.social_security ? '<div class="grp"><b>' +
						esc(ex.social_security.label) + "</b>" +
						"<span>" + esc(ex.social_security.base) + "</span>" +
						"<span>" + esc(ex.social_security.rate) + "</span>" +
						"<span>" + esc(ex.social_security.amount) + "</span></div>" : "") +
					(ex.irt ? '<div class="grp"><b>' + esc(ex.irt.label) + "</b>" +
						"<span>" + esc(ex.irt.taxable) + "</span>" +
						"<span>" + esc(ex.irt.bracket) + "</span>" +
						"<span>" + esc(ex.irt.rate) + "</span>" +
						"<span>" + esc(ex.irt.fixed) + "</span>" +
						"<span>" + esc(ex.irt.amount) + "</span></div>" : "") +
					"</div>",
					'<a class="ss-btn primary sm" href="' + pdf + '" target="_blank" rel="noopener">' +
						'<i class="fa fa-download" aria-hidden="true"></i> ' +
						esc(__("PDF")) + "</a>"));

			SS.on(app.$view, "[data-back]", "click", function () {
				location.hash = "#payslips";
			});
		});
	}

	/* ==================================================================== LEAVE */
	function leave(app) {
		return Promise.all([
			SS.api("my_leave_balance"),
			SS.api("my_leave")
		]).then(function (res) {
			var balance = res[0] || [], apps = res[1] || [];

			var balHtml = card(__("Balance by type"),
				balance.length
					? '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
						"<th>" + esc(__("Type")) + '</th><th class="num">' + esc(__("Entitlement")) +
						'</th><th class="num">' + esc(__("Taken")) + '</th><th class="num">' +
						esc(__("Pending")) + '</th><th class="num">' + esc(__("Available days")) +
						"</th></tr></thead><tbody>" + balance.map(function (r) {
							return "<tr><td>" + esc(r.leave_type) + '</td><td class="num">' +
								SS.num(r.entitlement, 1) + '</td><td class="num">' +
								SS.num(r.used, 1) + '</td><td class="num">' +
								SS.num(r.pending, 1) + '</td><td class="num"><b>' +
								SS.num(r.available, 1) + "</b></td></tr>";
						}).join("") + "</tbody></table></div>"
					: SS.empty("fa-calendar-o", __("No entitlements allocated."),
						__("The days are allocated by HR at the start of the period.")),
				'<button class="ss-btn primary sm" id="ss-req-leave">' +
					'<i class="fa fa-plus" aria-hidden="true"></i> ' +
					esc(__("Request leave")) + "</button>");

			var listHtml = card(__("My requests"),
				apps.length
					? '<ul class="ss-list">' + apps.map(function (r) {
						var canCancel = r.status === "Open" && !r.docstatus;
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.leave_type) + "</div><div class='ss-li-sub'>" +
							date(r.from_date) + " → " + date(r.to_date) + " · " +
							esc(__("{0} day(s)", [SS.num(r.total_leave_days, 1)])) +
							(r.half_day ? " · " + esc(__("half day")) : "") +
							"</div></div><div class='ss-li-right'>" +
							SS.statusBadge(r.status) + "</div></div>" +
							(r.description ? "<div class='ss-li-sub' style='margin-top:6px'>" +
								esc(r.description) + "</div>" : "") +
							(canCancel ? '<div class="ss-li-actions">' +
								'<button class="ss-btn bad sm" data-cancel="' + esc(r.name) +
								'">' + esc(__("Withdraw request")) + "</button></div>" : "") +
							"</li>";
					}).join("") + "</ul>"
					: SS.empty("fa-plane", __("You have not requested leave yet."),
						__("Use the “Request leave” button to submit your first request.")));

			app.render(balHtml + listHtml);

			SS.qs("#ss-req-leave", app.$view).addEventListener("click", function () {
				openLeaveForm(app, balance);
			});

			SS.on(app.$view, "[data-cancel]", "click", function () {
				var name = this.dataset.cancel;
				SS.confirm(__("Withdraw request"),
					__("Request {0} will be deleted. This cannot be undone.", [name]),
					__("Withdraw"),
					function () {
						return SS.api("cancel_leave", { name: name }).then(function () {
							SS.toast(__("Request withdrawn."), "ok");
							app.reload();
						});
					});
			});
		});
	}

	function openLeaveForm(app, balance) {
		var types = balance.map(function (r) {
			return { value: r.leave_type,
				label: r.leave_type + " (" + SS.num(r.available, 1) + " " + __("avail.") + ")" };
		});
		if (!types.length) {
			SS.toast(__("You have no leave types allocated. Contact HR."), "bad", 5000);
			return;
		}
		var m = SS.modal({
			title: __("Leave request"),
			fields: [
				{ name: "leave_type", label: __("Type"), type: "select", options: types,
					required: true },
				{ name: "from_date", label: __("From"), type: "date", required: true,
					value: SS.today() },
				{ name: "to_date", label: __("To"), type: "date", required: true,
					value: SS.today() },
				{ name: "half_day", label: __("Half day"), type: "checkbox" },
				{ name: "description", label: __("Reason"), type: "textarea",
					placeholder: __("Optional — it helps your manager decide.") }
			],
			html: '<div class="ss-preview" aria-live="polite"></div>',
			okLabel: __("Submit"),
			onSubmit: function (v) {
				return SS.api("apply_leave", {
					leave_type: v.leave_type, from_date: v.from_date, to_date: v.to_date,
					half_day: v.half_day, description: v.description
				}).then(function (r) {
					SS.toast(__("Request {0} submitted ({1} day(s)).",
						[r.name, SS.num(r.total_leave_days, 1)]), "ok");
					SS.announce(__("Leave request submitted."));
					app.reload();
				});
			}
		});

		// Live preview of days requested and the balance that would remain (§13).
		// Advisory only — the server recomputes and can still refuse.
		var box = SS.qs(".ss-preview", m.form);
		var timer = null;
		function preview() {
			var f = m.form.elements;
			if (!f.from_date.value || !f.to_date.value) return;
			SS.api("leave_preview", {
				leave_type: f.leave_type.value, from_date: f.from_date.value,
				to_date: f.to_date.value, half_day: f.half_day.checked ? 1 : 0
			}).then(function (p) {
				box.innerHTML = '<div class="ss-alert ' + (p.sufficient ? "info" : "warn") +
					'"><i class="fa fa-' + (p.sufficient ? "calculator" : "exclamation-triangle") +
					'" aria-hidden="true"></i><span>' +
					esc(__("{0} day(s) requested. Current balance {1}, you would be left with {2}.",
						[SS.num(p.days, 1), SS.num(p.balance_before, 1),
							SS.num(p.balance_after, 1)])) +
					(p.sufficient ? "" : "<br><b>" +
						esc(__("Not enough balance — the request will be refused.")) + "</b>") +
					"</span></div>";
			}).catch(function () { box.innerHTML = ""; });
		}
		["change", "input"].forEach(function (evt) {
			m.form.addEventListener(evt, function () {
				clearTimeout(timer);
				timer = setTimeout(preview, 250);
			});
		});
		preview();
	}

	/* =============================================================== ATTENDANCE */
	function attendance(app, arg) {
		var month = arg || SS.today().slice(0, 7);
		var first = month + "-01";
		var d0 = SS.parseDate(first);
		var last = new Date(d0.getFullYear(), d0.getMonth() + 1, 0);
		var to = month + "-" + ("0" + last.getDate()).slice(-2);

		return SS.api("my_attendance", { from_date: first, to_date: to }).then(function (r) {
			var byDate = {};
			(r.attendance || []).forEach(function (a) { byDate[String(a.attendance_date)] = a; });

			var CLS = { Present: "p", Absent: "a", "On Leave": "l", "Half Day": "h",
				"Work From Home": "p" };
			var MK = { Present: "P", Absent: "F", "On Leave": "FE", "Half Day": "½",
				"Work From Home": "T" };

			// Monday-first grid, which is how a payroll month is read here.
			var lead = (d0.getDay() + 6) % 7;
			var cells = "";
			["S", "T", "Q", "Q", "S", "S", "D"].forEach(function (w) {
				cells += '<div class="dow">' + w + "</div>";
			});
			for (var i = 0; i < lead; i++) cells += '<div class="day pad"></div>';
			for (var day = 1; day <= last.getDate(); day++) {
				var key = month + "-" + ("0" + day).slice(-2);
				var a = byDate[key];
				cells += '<div class="day ' + (a ? (CLS[a.status] || "") : "") + '"' +
					(a ? ' title="' + esc(a.status) + '"' : "") + ">" +
					"<span>" + day + "</span>" +
					(a ? '<span class="mk">' + esc(MK[a.status] || a.status.slice(0, 1)) +
						"</span>" : "") + "</div>";
			}

			var key = '<div class="ss-cal-key">' +
				'<span><i style="background:rgba(5,150,105,.35)"></i>' + esc(__("Present")) +
				"</span>" +
				'<span><i style="background:rgba(220,38,38,.35)"></i>' + esc(__("Absence")) +
				"</span>" +
				'<span><i style="background:rgba(67,56,202,.35)"></i>' + esc(__("Leave")) +
				"</span>" +
				'<span><i style="background:rgba(180,83,9,.35)"></i>' + esc(__("Half day")) +
				"</span></div>";

			// Icon-only controls carry an aria-label: the glyph is hidden from assistive
			// technology, so without one these read as an unnamed button (§21).
			var nav = '<div class="ss-filters">' +
				'<button class="ss-btn sm" data-mo="-1" aria-label="' +
				esc(__("Previous month")) + '">' +
				'<i class="fa fa-chevron-left" aria-hidden="true"></i></button>' +
				'<div style="flex:1;text-align:center;font-weight:800;line-height:40px">' +
				esc(SS.date(first).replace(/^\d+ /, "")) + "</div>" +
				'<button class="ss-btn sm" data-mo="1" aria-label="' +
				esc(__("Next month")) + '">' +
				'<i class="fa fa-chevron-right" aria-hidden="true"></i></button></div>';

			// Occurrences get their own card: date, why, deadline and HR's decision (§15).
			var occ = r.occurrences || [];
			var occHtml = card(__("Occurrences"),
				occ.length
					? '<ul class="ss-list">' + occ.map(function (o) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(o.occurrence_type) + " — " + date(o.occurrence_date) +
							"</div><div class='ss-li-sub'>" +
							(o.hours ? esc(__("{0} hour(s)", [SS.num(o.hours, 2)])) + " · " : "") +
							esc(__("Status")) + ": " + esc(o.status) +
							"</div></div><div class='ss-li-right'>" + SS.statusBadge(o.status) +
							"</div></div>" +
							(["Pending Justification", "Unjustified"].indexOf(o.status) !== -1
								? '<div class="ss-li-actions">' +
									'<button class="ss-btn primary sm" data-justify="' +
									esc(o.name) + '">' +
									'<i class="fa fa-paperclip" aria-hidden="true"></i> ' +
									esc(__("Explain")) + "</button></div>"
								: "") +
						(o.justification_document
								? "<div class='ss-li-sub' style='margin-top:7px'>" +
									esc(__("Document handed in.")) + "</div>"
								: "") + "</li>";
					}).join("") + "</ul>"
					: SS.empty("fa-check-circle-o", __("No occurrences in this period."),
						__("Absences, lateness and early exits would appear here.")));

			app.render(card(__("Attendance"), nav + '<div class="ss-cal">' + cells + "</div>" + key) +
				occHtml);

			SS.on(app.$view, "[data-justify]", "click", function () {
				var name = this.dataset.justify;
				SS.api("justification_reasons").then(function (reasons) {
					SS.modal({
						title: __("Explain an absence"),
						html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
							'aria-hidden="true"></i><span>' +
							esc(__("Explain the absence and attach the proof. The decision" +
								"de aceitar a justificação é da sua chefia ou do RH.")) +
							"</span></div>",
						fields: [
							{ name: "reason", label: __("Reason"), type: "select",
								options: [{ value: "", label: __("— select —") }].concat(
									(reasons || []).map(function (r) {
										return { value: r.name, label: r.reason_name || r.name };
									})) },
							{ name: "explanation", label: __("Explanation"), type: "textarea",
								placeholder: __("What happened?") },
							{ name: "document", label: __("Proof"), type: "file",
								hint: __("PDF or photo, up to 8 MB.") }
						],
						okLabel: __("Submit"),
						onSubmit: function (v) {
							var args = { name: name, reason: v.reason || null,
								explanation: v.explanation || null };
							if (v.document) {
								args.filename = v.document.filename;
								args.content = v.document.content;
							}
							return SS.api("submit_justification", args).then(function (r) {
								SS.toast(r.note || __("Explanation submitted."), "ok", 5000);
								app.reload();
							});
						}
					});
				});
			});

			SS.on(app.$view, "[data-mo]", "click", function () {
				var delta = parseInt(this.dataset.mo, 10);
				var nd = new Date(d0.getFullYear(), d0.getMonth() + delta, 1);
				location.hash = "#attendance/" + nd.getFullYear() + "-" +
					("0" + (nd.getMonth() + 1)).slice(-2);
			});
		});
	}

	/* ================================================================ DOCUMENTS */
	function documents(app) {
		return SS.api("my_documents").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("My documents"),
					SS.empty("fa-folder-open-o", __("No documents on file."),
						__("HR files contracts, certificates and identification here.")),
					'<button class="ss-btn primary sm" id="ss-upload">' +
						'<i class="fa fa-upload" aria-hidden="true"></i> ' +
						esc(__("Upload document")) + "</button>") + uploadHint());
			}
			app.render(card(__("My documents"),
				'<ul class="ss-list">' + rows.map(function (r) {
					return '<li><div class="ss-li-top"><div><div class="ss-li-title">' +
						esc(r.document_type) + "</div>" +
						'<div class="ss-li-sub">' +
						(r.document_number ? esc(r.document_number) + " · " : "") +
						(r.expiry_date ? esc(__("Expiry")) + " " + date(r.expiry_date)
							: esc(__("No expiry"))) +
						"</div></div>" +
						'<div class="ss-li-right">' + SS.statusBadge(r.status) + "</div></div>" +
						'<div class="ss-li-actions">' +
						'<button class="ss-btn sm" data-doc="' + esc(r.name) + '">' +
						'<i class="fa fa-eye" aria-hidden="true"></i> ' + esc(__("Open")) +
						"</button></div></li>";
				}).join("") + "</ul>" +
				'<div class="ss-alert info" style="margin-top:10px">' +
				'<i class="fa fa-shield" aria-hidden="true"></i><span>' +
				esc(__("Medical and confidential documents are held by HR and are not" +
					"aparecem aqui.")) + "</span></div>",
				'<button class="ss-btn primary sm" id="ss-upload">' +
					'<i class="fa fa-upload" aria-hidden="true"></i> ' +
					esc(__("Upload document")) + "</button>"));

			var upload = SS.qs("#ss-upload", app.$view);
			if (upload) upload.addEventListener("click", function () { openUpload(app); });

			SS.on(app.$view, "[data-doc]", "click", function () {
				var name = this.dataset.doc;
				SS.api("my_document", { name: name }).then(function (d) {
					SS.modal({
						title: d.document_type,
						html: "<dl>" +
							kv(__("Number"), esc(d.document_number)) +
							kv(__("Issued on"), date(d.issue_date)) +
							kv(__("Expiry"), date(d.expiry_date)) +
							kv(__("Issuing authority"), esc(d.issuing_authority)) +
							kv(__("Status"), SS.statusBadge(d.status)) + "</dl>" +
							(d.attachment
								? '<a class="ss-btn primary block" style="margin-top:12px" href="' +
									esc(d.attachment) + '" target="_blank" rel="noopener">' +
									'<i class="fa fa-download" aria-hidden="true"></i> ' +
									esc(__("Download")) + "</a>"
								: '<div class="ss-alert warn" style="margin-top:12px">' +
									'<i class="fa fa-paperclip" aria-hidden="true"></i><span>' +
									esc(__("No file attached. Ask HR for a copy.")) +
									"</span></div>"),
						okLabel: __("Close"),
						cancelLabel: __("Close"),
						onSubmit: function () { return true; }
					});
				}).catch(function (err) {
					SS.toast(SS.describeError(err), "bad", 5000);
				});
			});
		});
	}

	function uploadHint() {
		return "";
	}

	function openUpload(app) {
		SS.api("uploadable_document_types").then(function (types) {
			if (!types.length) {
				SS.toast(__("HR has not yet enabled document uploads. Contact HR."),
					"bad", 5000);
				return;
			}
			SS.modal({
				title: __("Upload document"),
				html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
					'aria-hidden="true"></i><span>' +
					esc(__("The document stays pending HR verification. It only becomes" +
						"fazer parte do seu processo depois de verificado.")) +
					"</span></div>",
				fields: [
					{ name: "document_type", label: __("Type"), type: "select", required: true,
						options: types.map(function (t) {
							return { value: t.name, label: t.label || t.document_type };
						}) },
					{ name: "document_number", label: __("Number") },
					{ name: "issue_date", label: __("Issue date"), type: "date" },
					{ name: "expiry_date", label: __("Expiry"), type: "date",
						hint: __("Required for some document types.") },
					{ name: "file", label: __("File"), type: "file", required: true,
						hint: __("PDF or image, up to 8 MB.") }
				],
				okLabel: __("Upload"),
				onSubmit: function (v) {
					if (!v.file) {
						return Promise.reject(new Error(__("Choose a file.")));
					}
					return SS.api("upload_my_document", {
						document_type: v.document_type, filename: v.file.filename,
						content: v.file.content, document_number: v.document_number || null,
						issue_date: v.issue_date || null, expiry_date: v.expiry_date || null
					}).then(function (r) {
						SS.toast(r.note || __("Document uploaded."), "ok", 5000);
						app.reload();
					});
				}
			});
		});
	}

	/* ================================================================= REQUESTS */
	function requests(app) {
		return Promise.all([
			SS.api("my_requests"),
			SS.api("my_advances"),
			SS.api("my_leave")
		]).then(function (res) {
			var reqs = res[0] || [], advs = res[1] || [], leaves = res[2] || [];

			var all = leaves.map(function (r) {
				return { type: __("Leave"), detail: r.leave_type + " · " +
					date(r.from_date) + " → " + date(r.to_date), status: r.status,
					when_: r.from_date, name: r.name };
			}).concat(reqs.map(function (r) {
				return { type: r.type, detail: String(r.detail || ""), status: r.status,
					when_: r.when_, name: r.name };
			}));
			all.sort(function (a, b) {
				return String(b.when_ || "").localeCompare(String(a.when_ || ""));
			});

			var actions = '<div class="ss-two" style="margin-bottom:14px">' +
				'<button class="ss-btn primary block" id="ss-new-leave">' +
					'<i class="fa fa-plane" aria-hidden="true"></i> ' +
					esc(__("Request leave")) + "</button>" +
				'<button class="ss-btn primary block" id="ss-new-adv">' +
					'<i class="fa fa-money" aria-hidden="true"></i> ' +
					esc(__("Request an advance")) + "</button></div>";

			var advHtml = advs.length ? card(__("Advances"),
				'<ul class="ss-list">' + advs.map(function (a) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(a.name) + "</div><div class='ss-li-sub'>" +
						esc(__("Requested on")) + " " + date(a.request_date) +
						(a.installments ? " · " + esc(__("{0} instalments", [a.installments]))
							: "") + "</div></div>" +
						"<div class='ss-li-right'><div class='ss-li-amount'>" +
						money(a.approved_amount || 0, { bare: true }) + "</div>" +
						SS.statusBadge(a.status) + "</div></div>" +
						'<dl style="margin-top:8px">' +
						kv(__("Recovered"), money(a.recovered_amount || 0, { bare: true }), "num") +
						kv(__("Outstanding"), money(a.outstanding_amount || 0, { bare: true }), "num") +
						"</dl></li>";
				}).join("") + "</ul>") : "";

			var listHtml = card(__("Request history"),
				all.length
					? '<ul class="ss-list">' + all.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.type) + "</div><div class='ss-li-sub'>" + esc(r.detail) +
							"</div><div class='ss-li-sub'>" + esc(r.name) + " · " +
							date(String(r.when_ || "").slice(0, 10)) + "</div></div>" +
							"<div class='ss-li-right'>" + SS.statusBadge(r.status) +
							"</div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-inbox", __("You have not submitted any request yet."),
						__("Leave, advances and bank changes appear here.")));

			app.render(actions + advHtml + listHtml);

			SS.qs("#ss-new-leave", app.$view).addEventListener("click", function () {
				SS.api("my_leave_balance").then(function (b) { openLeaveForm(app, b); });
			});

			SS.qs("#ss-new-adv", app.$view).addEventListener("click", function () {
				SS.modal({
					title: __("Advance request"),
					html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("Say what you need. The approved amount, the number of instalments" +
							"e a data de início da recuperação são decididos pelo RH.")) +
						"</span></div>",
					fields: [
						{ name: "requested_amount", label: __("Amount requested (AKZ)"),
							type: "number", step: "0.01", min: "0", required: true },
						{ name: "installments", label: __("Instalments requested"),
							type: "number", min: "1", value: 1,
							hint: __("Indicative. HR sets the final schedule.") },
						{ name: "recovery_start_date", label: __("Recovery starts"),
							type: "date" },
						{ name: "reason", label: __("Reason"), type: "textarea", required: true }
					],
					okLabel: __("Submit"),
					onSubmit: function (v) {
						return SS.api("request_advance", v).then(function (r) {
							SS.toast(__("Request {0} submitted.", [r.name]), "ok");
							app.reload();
						});
					}
				});
			});
		});
	}


	/* ============================================================== PERFORMANCE */
	function performance(app) {
		return SS.api("my_reviews").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("My performance"),
					SS.empty("fa-star-o", __("You have no completed reviews yet."),
						__("Reviews appear here once HR has finalised them."))));
			}
			app.render(card(__("My performance"),
				'<ul class="ss-list">' + rows.map(function (r) {
					var pending = r.action_required;
					return '<li><div class="ss-li-top"><div>' +
						'<div class="ss-li-title">' +
						esc(SS.date(r.start_date, { short: true })) + " → " +
						esc(SS.date(r.end_date)) + "</div>" +
						'<div class="ss-li-sub">' + esc(r.kra_template || "") + "</div>" +
						"</div>" +
						'<div class="ss-li-right"><div class="ss-li-amount">' +
						SS.num(r.total_score, 2) + "</div>" +
						SS.statusBadge(pending ? "Pending Employee" : "Finalised") +
						"</div></div>" +
						(r.custom_manager_comments
							? '<div class="ss-li-sub" style="margin-top:8px"><b>' +
								esc(__("Manager's comment")) + ":</b> " +
								esc(r.custom_manager_comments) + "</div>"
							: "") +
						'<div class="ss-li-actions">' +
						'<button class="ss-btn sm" data-review="' + esc(r.name) + '">' +
						esc(__("View objectives")) + "</button>" +
						(pending
							? '<button class="ss-btn primary sm" data-ack="' + esc(r.name) +
								'">' + esc(__("Confirm you have read it")) + "</button>"
							: "") + "</div></li>";
				}).join("") + "</ul>"));

			SS.on(app.$view, "[data-review]", "click", function () {
				var name = this.dataset.review;
				SS.api("my_review", { name: name }).then(function (d) {
					SS.modal({
						title: __("Review {0}", [SS.date(d.end_date)]),
						html: '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
							"<th>" + esc(__("Objective")) + '</th><th class="num">' +
							esc(__("Weight")) + '</th><th class="num">' + esc(__("Note")) +
							"</th></tr></thead><tbody>" +
							(d.goals || []).map(function (g) {
								return "<tr><td>" + esc(g.kra) + '</td><td class="num">' +
									SS.num(g.per_weightage, 0) + '%</td><td class="num">' +
									SS.num(g.score, 2) + "</td></tr>";
							}).join("") + "</tbody></table></div>" +
							'<div class="ss-pay-total"><span class="l">' +
							esc(__("Result")) + '</span><span class="v">' +
							SS.num(d.total_score, 2) + "</span></div>" +
							(d.custom_manager_comments
								? '<div class="ss-explain"><h3>' +
									esc(__("Manager's comment")) + "</h3><span>" +
									esc(d.custom_manager_comments) + "</span></div>"
								: ""),
						okLabel: __("Close"), cancelLabel: __("Close"),
						onSubmit: function () { return true; }
					});
				});
			});

			SS.on(app.$view, "[data-ack]", "click", function () {
				var name = this.dataset.ack;
				SS.modal({
					title: __("Confirm you have read it"),
					html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("Confirming that you have read the review does not mean agreeing with" +
							"ela. Pode deixar um comentário.")) + "</span></div>",
					fields: [{ name: "comments", label: __("Comment (optional)"),
						type: "textarea" }],
					okLabel: __("Confirm you have read it"),
					onSubmit: function (v) {
						return SS.api("acknowledge_review",
							{ name: name, comments: v.comments }).then(function () {
								SS.toast(__("Read receipt recorded."), "ok");
								app.reload();
							});
					}
				});
			});
		});
	}

	/* ===================================================================== BOOT */
	document.addEventListener("DOMContentLoaded", function () {
		if (!SS.qs("#ess-root")) return;
		var app = new SS.App({
			mount: "#ess-root",
			title: "Employee Area",
			nav: [
				{ key: "home", label: "Home", icon: "fa-home" },
				{ key: "payslips", label: "Payslips", icon: "fa-file-text-o" },
				{ key: "leave", label: "Leave", icon: "fa-plane" },
				{ key: "attendance", label: "Attendance", icon: "fa-calendar-check-o" },
				{ key: "documents", label: "Documents", icon: "fa-folder-open-o" },
				{ key: "performance", label: "Performance", icon: "fa-star-o" },
				{ key: "requests", label: "Requests", icon: "fa-inbox" },
				{ key: "profile", label: "Profile", icon: "fa-user-circle-o" }
			],
			views: {
				home: home, profile: profile, payslips: payslips, leave: leave,
				attendance: attendance, documents: documents, requests: requests,
				performance: performance
			},
			boot: function (a) {
				return SS.api("self_service_context").then(function (ctx) {
					a.ctx = ctx;
					if (!ctx.employee) {
						var e = new Error(__("Your account is not yet linked to an employee" +
							"de colaborador. Contacte o RH."));
						throw e;
					}
					var links = [];
					if (ctx.is_manager) {
						links.push({ href: "/mss", icon: "fa-users",
							label: __("Team ({0})", [ctx.team_size]) });
					}
					if (ctx.is_hr) {
						links.push({ href: "/app/angola-hr-dashboard", icon: "fa-cogs",
							label: __("HR") });
					}
					a.setIdentity(ctx.employee_name, ctx.employee, links);
				});
			}
		});
		app.start();
	});
})(window.IsoftSS);
