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
					'<div class="k">' + esc(__("Saldo de férias")) + "</div>" +
					'<div class="v">' + SS.num(totalLeave, 1) + "</div>" +
					'<div class="h">' + esc(__("dias disponíveis")) + "</div></div>" +
				'<div class="ss-metric">' +
					'<div class="k">' + esc(__("Último recibo")) + "</div>" +
					'<div class="v">' + (latest ? money(latest.net_pay, { bare: true }) : "—") + "</div>" +
					'<div class="h">' + (latest ? esc(SS.period(latest.start_date, latest.end_date))
						: esc(__("sem recibos"))) + "</div></div>" +
				'<div class="ss-metric ' + (att.open_occurrences ? "warn" : "ok") + '">' +
					'<div class="k">' + esc(__("Presenças este mês")) + "</div>" +
					'<div class="v">' + SS.num(att.Present || 0) + "</div>" +
					'<div class="h">' + (att.open_occurrences
						? esc(__("{0} ocorrência(s) por justificar", [att.open_occurrences]))
						: esc(__("sem ocorrências"))) + "</div></div>" +
				'<div class="ss-metric ' + (openCount ? "warn" : "") + '">' +
					'<div class="k">' + esc(__("Pedidos em curso")) + "</div>" +
					'<div class="v">' + SS.num(openCount) + "</div>" +
					'<div class="h">' + esc(__("a aguardar decisão")) + "</div></div>" +
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
				contractHtml = card(__("O meu contrato"),
					"<dl>" +
					kv(__("Tipo"), esc(c.contract_type)) +
					kv(__("Início"), date(c.start_date)) +
					kv(__("Termo"), c.is_open_ended
						? esc(__("Sem termo")) : date(c.end_date)) +
					kv(__("Estado"), SS.statusBadge(c.status)) +
					(c.probation_end && c.probation_status !== "Not Applicable"
						? kv(__("Período experimental"), date(c.probation_end) + " " +
							SS.statusBadge(c.probation_status)) : "") +
					"</dl>" +
					(days !== null && days <= 90
						? '<div class="ss-alert warn" style="margin:12px 0 0"><i class="fa fa-clock-o"' +
						' aria-hidden="true"></i><span>' +
						esc(__("O seu contrato termina dentro de {0} dias. O RH será notificado " +
							"automaticamente.", [days])) + "</span></div>"
						: ""));
			} else {
				contractHtml = card(__("O meu contrato"),
					SS.empty("fa-file-text-o", __("Ainda não tem contrato registado no sistema."),
						__("Isto não afecta o seu vínculo. Contacte o RH se precisar de uma cópia.")));
			}

			// --- leave balance --------------------------------------------------
			var leaveHtml = card(__("Saldo de férias"),
				(d.leave_balance || []).length
					? '<ul class="ss-list">' + d.leave_balance.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.leave_type) + "</div><div class='ss-li-sub'>" +
							esc(__("Direito {0} · Gozados {1} · Pendentes {2}",
								[SS.num(r.entitlement, 1), SS.num(r.used, 1),
									SS.num(r.pending, 1)])) +
							"</div></div><div class='ss-li-right'><div class='ss-li-amount'>" +
							SS.num(r.available, 1) + "</div><div class='ss-li-sub'>" +
							esc(__("disponíveis")) + "</div></div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-calendar-o", __("Sem direitos de férias atribuídos."),
						__("O RH atribui os dias no início de cada período.")),
				'<button class="ss-btn primary sm" data-go="leave">' +
					esc(__("Pedir férias")) + "</button>");

			// --- open items -----------------------------------------------------
			var pend = (d.open_leave || []).map(function (r) {
				return { t: __("Férias"), s: r.leave_type + " · " + date(r.from_date) +
					" → " + date(r.to_date), st: r.status };
			}).concat((d.open_requests || []).map(function (r) {
				return { t: r.type, s: String(r.detail || ""), st: r.status };
			}));

			var pendHtml = card(__("A aguardar decisão"),
				pend.length
					? '<ul class="ss-list">' + pend.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.t) + "</div><div class='ss-li-sub'>" + esc(r.s) +
							"</div></div><div class='ss-li-right'>" +
							SS.statusBadge(r.st) + "</div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-check-circle-o", __("Não tem pedidos pendentes."),
						__("Tudo o que submeteu já foi decidido.")));

			// --- expiring documents ---------------------------------------------
			var docs = d.expiring_documents || [];
			var docHtml = docs.length ? card(__("Documentos a expirar"),
				'<div class="ss-alert warn"><i class="fa fa-exclamation-triangle" aria-hidden="true">' +
				"</i><span>" + esc(__("{0} documento(s) precisam de renovação.", [docs.length])) +
				"</span></div>" +
				'<ul class="ss-list">' + docs.map(function (r) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.document_type) + "</div><div class='ss-li-sub'>" +
						esc(__("Validade")) + ": " + date(r.expiry_date) +
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

			var personal = card(__("Dados pessoais"), "<dl>" +
				kv(__("Nome"), esc(p.employee_name)) +
				kv(__("Nº de colaborador"), esc(p.name)) +
				kv(__("Data de nascimento"), date(p.date_of_birth)) +
				kv(__("Telemóvel"), esc(p.cell_number)) +
				kv(__("E-mail pessoal"), esc(p.personal_email)) +
				kv(__("Morada"), esc(p.current_address)) +
				kv(__("Contacto de emergência"), esc(p.person_to_be_contacted)) +
				kv(__("Telefone de emergência"), esc(p.emergency_phone_number)) +
				"</dl>",
				'<button class="ss-btn sm" id="ss-edit">' + esc(__("Editar")) + "</button>");

			var employment = card(__("Vínculo"), "<dl>" +
				kv(__("Empresa"), esc(p.company)) +
				kv(__("Departamento"), esc(p.department)) +
				kv(__("Função"), esc(p.designation)) +
				kv(__("Local"), esc(p.branch)) +
				kv(__("Chefia"), esc(p.reports_to)) +
				kv(__("Admissão"), date(p.date_of_joining)) +
				kv(__("Tipo de contrato"), esc(p.contract ? p.contract.contract_type
					: p.employment_type)) +
				kv(__("Estado"), SS.statusBadge(p.status)) +
				"</dl>");

			// Statutory identifiers are shown to the person they belong to, but are never
			// editable here and never appear in any list view (§83).
			var statutory = card(__("Dados fiscais e bancários"), "<dl>" +
				kv(__("NIF"), esc(p.custom_nif)) +
				kv(__("Nº de Segurança Social"), esc(p.custom_inss_number)) +
				kv(__("IBAN"), esc(p.iban_masked)) +
				"</dl>" +
				'<div class="ss-alert info" style="margin-top:10px"><i class="fa fa-lock" ' +
				'aria-hidden="true"></i><span>' +
				esc(__("Estes dados só podem ser alterados pelo RH. Para mudar a conta bancária, " +
					"submeta um pedido — será validado antes de ser aplicado.")) + "</span></div>" +
				'<button class="ss-btn primary block" id="ss-bank" style="margin-top:10px">' +
				'<i class="fa fa-university" aria-hidden="true"></i> ' +
				esc(__("Pedir alteração de conta bancária")) + "</button>");

			app.render('<div class="ss-two">' + personal + employment + "</div>" + statutory);

			SS.qs("#ss-edit", app.$view).addEventListener("click", function () {
				var fields = [
					{ name: "cell_number", label: __("Telemóvel"), value: p.cell_number, type: "tel" },
					{ name: "personal_email", label: __("E-mail pessoal"),
						value: p.personal_email, type: "email" },
					{ name: "current_address", label: __("Morada"), type: "textarea",
						value: p.current_address },
					{ name: "person_to_be_contacted", label: __("Contacto de emergência"),
						value: p.person_to_be_contacted },
					{ name: "relation", label: __("Parentesco"), value: p.relation },
					{ name: "emergency_phone_number", label: __("Telefone de emergência"),
						value: p.emergency_phone_number, type: "tel" }
				].filter(function (f) { return editable.indexOf(f.name) !== -1; });

				SS.modal({
					title: __("Editar dados de contacto"),
					html: '<p class="hint" style="margin:0 0 12px;color:var(--ss-muted);' +
						'font-size:12.5px">' +
						esc(__("Só estes campos podem ser alterados por si. Salário, função, " +
							"NIF, Segurança Social e IBAN requerem o RH.")) + "</p>",
					fields: fields,
					okLabel: __("Guardar"),
					onSubmit: function (v) {
						return SS.api("update_my_profile", { values: JSON.stringify(v) })
							.then(function () {
								SS.toast(__("Dados actualizados."), "ok");
								SS.announce(__("Dados actualizados."));
								app.reload();
							});
					}
				});
			});

			SS.qs("#ss-bank", app.$view).addEventListener("click", function () {
				SS.modal({
					title: __("Alteração de conta bancária"),
					html: '<div class="ss-alert warn"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("O IBAN só é alterado depois de o RH validar o comprovativo. " +
							"O seu salário continua a ser pago para a conta actual até lá.")) +
						"</span></div>",
					fields: [
						{ name: "new_iban", label: __("Novo IBAN"), required: true,
							placeholder: "AO06 0000 0000 0000 0000 0000 0",
							hint: __("21 dígitos, como aparece no comprovativo do banco.") },
						{ name: "bank_name", label: __("Banco") }
					],
					okLabel: __("Submeter pedido"),
					onSubmit: function (v) {
						return SS.api("request_bank_change", v).then(function (name) {
							SS.toast(__("Pedido {0} submetido para validação do RH.", [name]), "ok");
							SS.announce(__("Pedido de alteração bancária submetido."));
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
				return app.render(card(__("Os meus recibos"),
					SS.empty("fa-file-text-o", __("Ainda não tem recibos disponíveis."),
						__("Só aparecem aqui os recibos já aprovados pelo departamento " +
							"de pessoal."))));
			}
			var list = '<ul class="ss-list">' + rows.map(function (r) {
				return '<li class="tap" tabindex="0" role="button" data-slip="' + esc(r.name) +
					'"><div class="ss-li-top"><div><div class="ss-li-title">' +
					esc(SS.period(r.start_date, r.end_date)) + "</div>" +
					'<div class="ss-li-sub">' + esc(__("Bruto")) + " " +
					money(r.gross_pay, { bare: true }) + " · " + esc(__("Descontos")) + " " +
					money(r.total_deduction, { bare: true }) + "</div></div>" +
					'<div class="ss-li-right"><div class="ss-li-amount">' +
					money(r.net_pay, { bare: true }) + '</div><div class="ss-li-sub">' +
					esc(__("líquido")) + "</div></div></div></li>";
			}).join("") + "</ul>";

			var table = '<div class="ss-table-wrap"><table class="ss-table">' +
				"<thead><tr><th>" + esc(__("Período")) + "</th><th>" + esc(__("Data")) +
				'</th><th class="num">' + esc(__("Bruto")) + '</th><th class="num">' +
				esc(__("Segurança Social")) + '</th><th class="num">' + esc(__("IRT")) +
				'</th><th class="num">' + esc(__("Líquido")) + "</th><th></th></tr></thead><tbody>" +
				rows.map(function (r) {
					return '<tr><td>' + esc(SS.period(r.start_date, r.end_date)) + "</td><td>" +
						date(r.posting_date) + '</td><td class="num">' +
						money(r.gross_pay, { bare: true }) + '</td><td class="num">' +
						money(r.ss_employee_amount, { bare: true }) + '</td><td class="num">' +
						money(r.irt_amount, { bare: true }) + '</td><td class="num"><b>' +
						money(r.net_pay, { bare: true }) + "</b></td><td>" +
						'<button class="ss-btn sm" data-slip="' + esc(r.name) + '">' +
						esc(__("Ver")) + "</button></td></tr>";
				}).join("") + "</tbody></table></div>";

			app.render('<section class="ss-card ss-desktop-table"><div class="ss-card-head">' +
				"<h2>" + esc(__("Os meus recibos")) + "</h2></div>" + list + table + "</section>");

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
					return '<p class="ss-li-sub">' + esc(__("Nada a mostrar.")) + "</p>";
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
					esc(__("Recibos")) + "</button>" +
				card(SS.period(s.start_date, s.end_date),
					"<dl>" +
					kv(__("Data de processamento"), date(s.posting_date)) +
					kv(__("Dias pagos"), esc(ex.days || "")) +
					"</dl>" +
					'<h3 style="font-size:13px;font-weight:800;margin:14px 0 6px">' +
					esc(__("Remunerações")) + "</h3>" + lines(s.earnings) +
					'<h3 style="font-size:13px;font-weight:800;margin:14px 0 6px">' +
					esc(__("Descontos")) + "</h3>" + lines(s.deductions) +
					'<div class="ss-pay-total"><span class="l">' + esc(__("Líquido a receber")) +
					'</span><span class="v">' + money(s.net_pay) + "</span></div>" +

					// "How this was calculated" — the statutory trace, in words (§10).
					'<div class="ss-explain"><h3>' + esc(__("Como foi calculado")) + "</h3>" +
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

			var balHtml = card(__("Saldo por tipo"),
				balance.length
					? '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
						"<th>" + esc(__("Tipo")) + '</th><th class="num">' + esc(__("Direito")) +
						'</th><th class="num">' + esc(__("Gozados")) + '</th><th class="num">' +
						esc(__("Pendentes")) + '</th><th class="num">' + esc(__("Disponíveis")) +
						"</th></tr></thead><tbody>" + balance.map(function (r) {
							return "<tr><td>" + esc(r.leave_type) + '</td><td class="num">' +
								SS.num(r.entitlement, 1) + '</td><td class="num">' +
								SS.num(r.used, 1) + '</td><td class="num">' +
								SS.num(r.pending, 1) + '</td><td class="num"><b>' +
								SS.num(r.available, 1) + "</b></td></tr>";
						}).join("") + "</tbody></table></div>"
					: SS.empty("fa-calendar-o", __("Sem direitos atribuídos."),
						__("Os dias são atribuídos pelo RH no início do período.")),
				'<button class="ss-btn primary sm" id="ss-req-leave">' +
					'<i class="fa fa-plus" aria-hidden="true"></i> ' +
					esc(__("Pedir férias")) + "</button>");

			var listHtml = card(__("Os meus pedidos"),
				apps.length
					? '<ul class="ss-list">' + apps.map(function (r) {
						var canCancel = r.status === "Open" && !r.docstatus;
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.leave_type) + "</div><div class='ss-li-sub'>" +
							date(r.from_date) + " → " + date(r.to_date) + " · " +
							esc(__("{0} dia(s)", [SS.num(r.total_leave_days, 1)])) +
							(r.half_day ? " · " + esc(__("meio-dia")) : "") +
							"</div></div><div class='ss-li-right'>" +
							SS.statusBadge(r.status) + "</div></div>" +
							(r.description ? "<div class='ss-li-sub' style='margin-top:6px'>" +
								esc(r.description) + "</div>" : "") +
							(canCancel ? '<div class="ss-li-actions">' +
								'<button class="ss-btn bad sm" data-cancel="' + esc(r.name) +
								'">' + esc(__("Retirar pedido")) + "</button></div>" : "") +
							"</li>";
					}).join("") + "</ul>"
					: SS.empty("fa-plane", __("Ainda não pediu férias."),
						__("Use o botão “Pedir férias” para submeter o primeiro pedido.")));

			app.render(balHtml + listHtml);

			SS.qs("#ss-req-leave", app.$view).addEventListener("click", function () {
				openLeaveForm(app, balance);
			});

			SS.on(app.$view, "[data-cancel]", "click", function () {
				var name = this.dataset.cancel;
				SS.confirm(__("Retirar pedido"),
					__("O pedido {0} será eliminado. Esta acção não pode ser desfeita.", [name]),
					__("Retirar"),
					function () {
						return SS.api("cancel_leave", { name: name }).then(function () {
							SS.toast(__("Pedido retirado."), "ok");
							app.reload();
						});
					});
			});
		});
	}

	function openLeaveForm(app, balance) {
		var types = balance.map(function (r) {
			return { value: r.leave_type,
				label: r.leave_type + " (" + SS.num(r.available, 1) + " " + __("disp.") + ")" };
		});
		if (!types.length) {
			SS.toast(__("Não tem tipos de ausência atribuídos. Contacte o RH."), "bad", 5000);
			return;
		}
		var m = SS.modal({
			title: __("Pedido de férias"),
			fields: [
				{ name: "leave_type", label: __("Tipo"), type: "select", options: types,
					required: true },
				{ name: "from_date", label: __("De"), type: "date", required: true,
					value: SS.today() },
				{ name: "to_date", label: __("Até"), type: "date", required: true,
					value: SS.today() },
				{ name: "half_day", label: __("Meio-dia"), type: "checkbox" },
				{ name: "description", label: __("Motivo"), type: "textarea",
					placeholder: __("Opcional — ajuda a chefia a decidir.") }
			],
			html: '<div class="ss-preview" aria-live="polite"></div>',
			okLabel: __("Submeter"),
			onSubmit: function (v) {
				return SS.api("apply_leave", {
					leave_type: v.leave_type, from_date: v.from_date, to_date: v.to_date,
					half_day: v.half_day, description: v.description
				}).then(function (r) {
					SS.toast(__("Pedido {0} submetido ({1} dia(s)).",
						[r.name, SS.num(r.total_leave_days, 1)]), "ok");
					SS.announce(__("Pedido de férias submetido."));
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
					esc(__("{0} dia(s) pedidos. Saldo actual {1}, ficaria com {2}.",
						[SS.num(p.days, 1), SS.num(p.balance_before, 1),
							SS.num(p.balance_after, 1)])) +
					(p.sufficient ? "" : "<br><b>" +
						esc(__("Saldo insuficiente — o pedido será recusado.")) + "</b>") +
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
				'<span><i style="background:rgba(5,150,105,.35)"></i>' + esc(__("Presente")) +
				"</span>" +
				'<span><i style="background:rgba(220,38,38,.35)"></i>' + esc(__("Falta")) +
				"</span>" +
				'<span><i style="background:rgba(67,56,202,.35)"></i>' + esc(__("Férias")) +
				"</span>" +
				'<span><i style="background:rgba(180,83,9,.35)"></i>' + esc(__("Meio-dia")) +
				"</span></div>";

			// Icon-only controls carry an aria-label: the glyph is hidden from assistive
			// technology, so without one these read as an unnamed button (§21).
			var nav = '<div class="ss-filters">' +
				'<button class="ss-btn sm" data-mo="-1" aria-label="' +
				esc(__("Mês anterior")) + '">' +
				'<i class="fa fa-chevron-left" aria-hidden="true"></i></button>' +
				'<div style="flex:1;text-align:center;font-weight:800;line-height:40px">' +
				esc(SS.date(first).replace(/^\d+ /, "")) + "</div>" +
				'<button class="ss-btn sm" data-mo="1" aria-label="' +
				esc(__("Mês seguinte")) + '">' +
				'<i class="fa fa-chevron-right" aria-hidden="true"></i></button></div>';

			// Occurrences get their own card: date, why, deadline and HR's decision (§15).
			var occ = r.occurrences || [];
			var occHtml = card(__("Ocorrências"),
				occ.length
					? '<ul class="ss-list">' + occ.map(function (o) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(o.occurrence_type) + " — " + date(o.occurrence_date) +
							"</div><div class='ss-li-sub'>" +
							(o.hours ? esc(__("{0} hora(s)", [SS.num(o.hours, 2)])) + " · " : "") +
							esc(__("Estado")) + ": " + esc(o.status) +
							"</div></div><div class='ss-li-right'>" + SS.statusBadge(o.status) +
							"</div></div>" +
							(["Pending Justification", "Unjustified"].indexOf(o.status) !== -1
								? '<div class="ss-li-actions">' +
									'<button class="ss-btn primary sm" data-justify="' +
									esc(o.name) + '">' +
									'<i class="fa fa-paperclip" aria-hidden="true"></i> ' +
									esc(__("Justificar")) + "</button></div>"
								: "") +
						(o.justification_document
								? "<div class='ss-li-sub' style='margin-top:7px'>" +
									esc(__("Documento entregue.")) + "</div>"
								: "") + "</li>";
					}).join("") + "</ul>"
					: SS.empty("fa-check-circle-o", __("Sem ocorrências neste período."),
						__("Faltas, atrasos e saídas antecipadas apareceriam aqui.")));

			app.render(card(__("Assiduidade"), nav + '<div class="ss-cal">' + cells + "</div>" + key) +
				occHtml);

			SS.on(app.$view, "[data-justify]", "click", function () {
				var name = this.dataset.justify;
				SS.api("justification_reasons").then(function (reasons) {
					SS.modal({
						title: __("Justificar ausência"),
						html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
							'aria-hidden="true"></i><span>' +
							esc(__("Explique a ausência e junte o comprovativo. A decisão " +
								"de aceitar a justificação é da sua chefia ou do RH.")) +
							"</span></div>",
						fields: [
							{ name: "reason", label: __("Motivo"), type: "select",
								options: [{ value: "", label: __("— seleccionar —") }].concat(
									(reasons || []).map(function (r) {
										return { value: r.name, label: r.reason_name || r.name };
									})) },
							{ name: "explanation", label: __("Explicação"), type: "textarea",
								placeholder: __("O que aconteceu?") },
							{ name: "document", label: __("Comprovativo"), type: "file",
								hint: __("PDF ou fotografia, até 8 MB.") }
						],
						okLabel: __("Submeter"),
						onSubmit: function (v) {
							var args = { name: name, reason: v.reason || null,
								explanation: v.explanation || null };
							if (v.document) {
								args.filename = v.document.filename;
								args.content = v.document.content;
							}
							return SS.api("submit_justification", args).then(function (r) {
								SS.toast(r.note || __("Justificação submetida."), "ok", 5000);
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
				return app.render(card(__("Os meus documentos"),
					SS.empty("fa-folder-open-o", __("Sem documentos registados."),
						__("O RH regista aqui contratos, certificados e identificação.")),
					'<button class="ss-btn primary sm" id="ss-upload">' +
						'<i class="fa fa-upload" aria-hidden="true"></i> ' +
						esc(__("Enviar documento")) + "</button>") + uploadHint());
			}
			app.render(card(__("Os meus documentos"),
				'<ul class="ss-list">' + rows.map(function (r) {
					return '<li><div class="ss-li-top"><div><div class="ss-li-title">' +
						esc(r.document_type) + "</div>" +
						'<div class="ss-li-sub">' +
						(r.document_number ? esc(r.document_number) + " · " : "") +
						(r.expiry_date ? esc(__("Validade")) + " " + date(r.expiry_date)
							: esc(__("Sem validade"))) +
						"</div></div>" +
						'<div class="ss-li-right">' + SS.statusBadge(r.status) + "</div></div>" +
						'<div class="ss-li-actions">' +
						'<button class="ss-btn sm" data-doc="' + esc(r.name) + '">' +
						'<i class="fa fa-eye" aria-hidden="true"></i> ' + esc(__("Abrir")) +
						"</button></div></li>";
				}).join("") + "</ul>" +
				'<div class="ss-alert info" style="margin-top:10px">' +
				'<i class="fa fa-shield" aria-hidden="true"></i><span>' +
				esc(__("Documentos médicos e confidenciais são guardados pelo RH e não " +
					"aparecem aqui.")) + "</span></div>",
				'<button class="ss-btn primary sm" id="ss-upload">' +
					'<i class="fa fa-upload" aria-hidden="true"></i> ' +
					esc(__("Enviar documento")) + "</button>"));

			var upload = SS.qs("#ss-upload", app.$view);
			if (upload) upload.addEventListener("click", function () { openUpload(app); });

			SS.on(app.$view, "[data-doc]", "click", function () {
				var name = this.dataset.doc;
				SS.api("my_document", { name: name }).then(function (d) {
					SS.modal({
						title: d.document_type,
						html: "<dl>" +
							kv(__("Número"), esc(d.document_number)) +
							kv(__("Emitido em"), date(d.issue_date)) +
							kv(__("Validade"), date(d.expiry_date)) +
							kv(__("Entidade emissora"), esc(d.issuing_authority)) +
							kv(__("Estado"), SS.statusBadge(d.status)) + "</dl>" +
							(d.attachment
								? '<a class="ss-btn primary block" style="margin-top:12px" href="' +
									esc(d.attachment) + '" target="_blank" rel="noopener">' +
									'<i class="fa fa-download" aria-hidden="true"></i> ' +
									esc(__("Descarregar")) + "</a>"
								: '<div class="ss-alert warn" style="margin-top:12px">' +
									'<i class="fa fa-paperclip" aria-hidden="true"></i><span>' +
									esc(__("Sem ficheiro anexado. Peça uma cópia ao RH.")) +
									"</span></div>"),
						okLabel: __("Fechar"),
						cancelLabel: __("Fechar"),
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
				SS.toast(__("O RH ainda não autorizou o envio de documentos. Contacte o RH."),
					"bad", 5000);
				return;
			}
			SS.modal({
				title: __("Enviar documento"),
				html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
					'aria-hidden="true"></i><span>' +
					esc(__("O documento fica pendente de verificação pelo RH. Só passa a " +
						"fazer parte do seu processo depois de verificado.")) +
					"</span></div>",
				fields: [
					{ name: "document_type", label: __("Tipo"), type: "select", required: true,
						options: types.map(function (t) {
							return { value: t.name, label: t.label || t.document_type };
						}) },
					{ name: "document_number", label: __("Número") },
					{ name: "issue_date", label: __("Data de emissão"), type: "date" },
					{ name: "expiry_date", label: __("Validade"), type: "date",
						hint: __("Obrigatória para alguns tipos de documento.") },
					{ name: "file", label: __("Ficheiro"), type: "file", required: true,
						hint: __("PDF ou imagem, até 8 MB.") }
				],
				okLabel: __("Enviar"),
				onSubmit: function (v) {
					if (!v.file) {
						return Promise.reject(new Error(__("Escolha um ficheiro.")));
					}
					return SS.api("upload_my_document", {
						document_type: v.document_type, filename: v.file.filename,
						content: v.file.content, document_number: v.document_number || null,
						issue_date: v.issue_date || null, expiry_date: v.expiry_date || null
					}).then(function (r) {
						SS.toast(r.note || __("Documento enviado."), "ok", 5000);
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
				return { type: __("Férias"), detail: r.leave_type + " · " +
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
					esc(__("Pedir férias")) + "</button>" +
				'<button class="ss-btn primary block" id="ss-new-adv">' +
					'<i class="fa fa-money" aria-hidden="true"></i> ' +
					esc(__("Pedir adiantamento")) + "</button></div>";

			var advHtml = advs.length ? card(__("Adiantamentos"),
				'<ul class="ss-list">' + advs.map(function (a) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(a.name) + "</div><div class='ss-li-sub'>" +
						esc(__("Pedido em")) + " " + date(a.request_date) +
						(a.installments ? " · " + esc(__("{0} prestações", [a.installments]))
							: "") + "</div></div>" +
						"<div class='ss-li-right'><div class='ss-li-amount'>" +
						money(a.approved_amount || 0, { bare: true }) + "</div>" +
						SS.statusBadge(a.status) + "</div></div>" +
						'<dl style="margin-top:8px">' +
						kv(__("Recuperado"), money(a.recovered_amount || 0, { bare: true }), "num") +
						kv(__("Em dívida"), money(a.outstanding_amount || 0, { bare: true }), "num") +
						"</dl></li>";
				}).join("") + "</ul>") : "";

			var listHtml = card(__("Histórico de pedidos"),
				all.length
					? '<ul class="ss-list">' + all.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.type) + "</div><div class='ss-li-sub'>" + esc(r.detail) +
							"</div><div class='ss-li-sub'>" + esc(r.name) + " · " +
							date(String(r.when_ || "").slice(0, 10)) + "</div></div>" +
							"<div class='ss-li-right'>" + SS.statusBadge(r.status) +
							"</div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-inbox", __("Ainda não submeteu pedidos."),
						__("Férias, adiantamentos e alterações bancárias aparecem aqui.")));

			app.render(actions + advHtml + listHtml);

			SS.qs("#ss-new-leave", app.$view).addEventListener("click", function () {
				SS.api("my_leave_balance").then(function (b) { openLeaveForm(app, b); });
			});

			SS.qs("#ss-new-adv", app.$view).addEventListener("click", function () {
				SS.modal({
					title: __("Pedido de adiantamento"),
					html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("Indica o que precisa. O valor aprovado, o número de prestações " +
							"e a data de início da recuperação são decididos pelo RH.")) +
						"</span></div>",
					fields: [
						{ name: "requested_amount", label: __("Valor pretendido (AKZ)"),
							type: "number", step: "0.01", min: "0", required: true },
						{ name: "installments", label: __("Prestações pretendidas"),
							type: "number", min: "1", value: 1,
							hint: __("Indicativo. O RH define o plano final.") },
						{ name: "recovery_start_date", label: __("Início de recuperação"),
							type: "date" },
						{ name: "reason", label: __("Motivo"), type: "textarea", required: true }
					],
					okLabel: __("Submeter"),
					onSubmit: function (v) {
						return SS.api("request_advance", v).then(function (r) {
							SS.toast(__("Pedido {0} submetido.", [r.name]), "ok");
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
				return app.render(card(__("O meu desempenho"),
					SS.empty("fa-star-o", __("Ainda não tem avaliações concluídas."),
						__("As avaliações aparecem aqui depois de o RH as concluir."))));
			}
			app.render(card(__("O meu desempenho"),
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
								esc(__("Comentário da chefia")) + ":</b> " +
								esc(r.custom_manager_comments) + "</div>"
							: "") +
						'<div class="ss-li-actions">' +
						'<button class="ss-btn sm" data-review="' + esc(r.name) + '">' +
						esc(__("Ver objectivos")) + "</button>" +
						(pending
							? '<button class="ss-btn primary sm" data-ack="' + esc(r.name) +
								'">' + esc(__("Confirmar leitura")) + "</button>"
							: "") + "</div></li>";
				}).join("") + "</ul>"));

			SS.on(app.$view, "[data-review]", "click", function () {
				var name = this.dataset.review;
				SS.api("my_review", { name: name }).then(function (d) {
					SS.modal({
						title: __("Avaliação {0}", [SS.date(d.end_date)]),
						html: '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
							"<th>" + esc(__("Objectivo")) + '</th><th class="num">' +
							esc(__("Peso")) + '</th><th class="num">' + esc(__("Nota")) +
							"</th></tr></thead><tbody>" +
							(d.goals || []).map(function (g) {
								return "<tr><td>" + esc(g.kra) + '</td><td class="num">' +
									SS.num(g.per_weightage, 0) + '%</td><td class="num">' +
									SS.num(g.score, 2) + "</td></tr>";
							}).join("") + "</tbody></table></div>" +
							'<div class="ss-pay-total"><span class="l">' +
							esc(__("Resultado")) + '</span><span class="v">' +
							SS.num(d.total_score, 2) + "</span></div>" +
							(d.custom_manager_comments
								? '<div class="ss-explain"><h3>' +
									esc(__("Comentário da chefia")) + "</h3><span>" +
									esc(d.custom_manager_comments) + "</span></div>"
								: ""),
						okLabel: __("Fechar"), cancelLabel: __("Fechar"),
						onSubmit: function () { return true; }
					});
				});
			});

			SS.on(app.$view, "[data-ack]", "click", function () {
				var name = this.dataset.ack;
				SS.modal({
					title: __("Confirmar leitura"),
					html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
						'aria-hidden="true"></i><span>' +
						esc(__("Confirmar que leu a avaliação não significa concordar com " +
							"ela. Pode deixar um comentário.")) + "</span></div>",
					fields: [{ name: "comments", label: __("Comentário (opcional)"),
						type: "textarea" }],
					okLabel: __("Confirmar leitura"),
					onSubmit: function (v) {
						return SS.api("acknowledge_review",
							{ name: name, comments: v.comments }).then(function () {
								SS.toast(__("Leitura confirmada."), "ok");
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
			title: "Área do Colaborador",
			nav: [
				{ key: "home", label: "Início", icon: "fa-home" },
				{ key: "payslips", label: "Recibos", icon: "fa-file-text-o" },
				{ key: "leave", label: "Férias", icon: "fa-plane" },
				{ key: "attendance", label: "Assiduidade", icon: "fa-calendar-check-o" },
				{ key: "documents", label: "Documentos", icon: "fa-folder-open-o" },
				{ key: "performance", label: "Desempenho", icon: "fa-star-o" },
				{ key: "requests", label: "Pedidos", icon: "fa-inbox" },
				{ key: "profile", label: "Perfil", icon: "fa-user-circle-o" }
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
						var e = new Error(__("A sua conta ainda não está associada a uma ficha " +
							"de colaborador. Contacte o RH."));
						throw e;
					}
					var links = [];
					if (ctx.is_manager) {
						links.push({ href: "/mss", icon: "fa-users",
							label: __("Equipa ({0})", [ctx.team_size]) });
					}
					if (ctx.is_hr) {
						links.push({ href: "/app/angola-hr-dashboard", icon: "fa-cogs",
							label: __("RH") });
					}
					a.setIdentity(ctx.employee_name, ctx.employee, links);
				});
			}
		});
		app.start();
	});
})(window.IsoftSS);
