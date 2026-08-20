/* Isoft Angola HR — Manager Self-Service (/mss).
 *
 * The team is whoever reports to the logged-in employee. That set is computed on the
 * server from Employee.reports_to and is never sent by the browser, so there is no
 * "manager scope" for this file to get wrong — asking about somebody outside the team
 * fails at the endpoint, not at a hidden button.
 *
 * COMPENSATION IS NOT SHOWN. Not in the directory, not on a member's page, not in the
 * inbox. `compensation_visible` arrives false for a line manager without a payroll role,
 * and the screens are built so there is nothing to reveal when it is false.
 */
/* eslint-env browser */
/* global IsoftSS */

(function (SS) {
	"use strict";

	var __ = SS.__, esc = SS.esc, date = SS.date;

	function card(title, inner, action) {
		return '<section class="ss-card">' +
			(title ? '<div class="ss-card-head"><h2>' + esc(title) + "</h2>" +
				(action || "") + "</div>" : "") + inner + "</section>";
	}

	function kv(label, value) {
		return '<div class="ss-kv"><dt>' + esc(label) + "</dt><dd>" +
			(value === null || value === undefined || value === "" ? "—" : value) + "</dd></div>";
	}

	function member(name, employee) {
		return '<button class="ss-btn sm" data-member="' + esc(employee) + '">' +
			esc(name) + "</button>";
	}

	/* ==================================================================== HOME */
	function home(app) {
		return SS.api("team_dashboard").then(function (d) {
			var av = d.availability || {};
			var cards = '<div class="ss-grid">' +
				'<div class="ss-metric"><div class="k">' + esc(__("Equipa")) + "</div>" +
					'<div class="v">' + SS.num(d.team_size) + '</div><div class="h">' +
					esc(__("colaboradores directos")) + "</div></div>" +
				'<div class="ss-metric ' + (av.on_leave ? "warn" : "ok") + '">' +
					'<div class="k">' + esc(__("Ausentes hoje")) + "</div>" +
					'<div class="v">' + SS.num(av.on_leave || 0) + '</div><div class="h">' +
					esc(__("{0} disponíveis", [SS.num(av.available || 0)])) + "</div></div>" +
				'<div class="ss-metric ' + (d.inbox_count ? "warn" : "ok") + '">' +
					'<div class="k">' + esc(__("A aprovar")) + "</div>" +
					'<div class="v">' + SS.num(d.inbox_count || 0) + '</div><div class="h">' +
					esc(__("à sua espera")) + "</div></div>" +
				'<div class="ss-metric ' + ((d.probations_due || []).length ? "warn" : "") + '">' +
					'<div class="k">' + esc(__("Períodos experimentais")) + "</div>" +
					'<div class="v">' + SS.num((d.probations_due || []).length) +
					'</div><div class="h">' + esc(__("a rever")) + "</div></div>" +
				"</div>";

			var today = card(__("Disponibilidade hoje"),
				(av.rows || []).length
					? '<ul class="ss-list">' + av.rows.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
							esc(r.designation || "—") + "</div></div>" +
							"<div class='ss-li-right'>" +
							(r.available ? SS.badge(__("Disponível"), "ok")
								: SS.badge(r.reason || __("Ausente"), "info")) +
							"</div></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-users", __("Ainda não tem colaboradores atribuídos."),
						__("O RH define a chefia no campo “Reports To” da ficha.")));

			var pend = card(__("Decisões pendentes"),
				(d.pending_leave || []).length || (d.attendance_exceptions || []).length
					? '<button class="ss-btn primary block" data-go="inbox">' +
						esc(__("Abrir caixa de aprovações")) + "</button>"
					: SS.empty("fa-check-circle-o", __("Nada à sua espera."),
						__("Pedidos de férias e ocorrências apareceriam aqui.")));

			var expiring = (d.contracts_expiring || []);
			var contractsHtml = expiring.length ? card(__("Contratos a terminar"),
				'<ul class="ss-list">' + expiring.map(function (r) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
						esc(r.contract_type || "") + " · " + esc(__("termina")) + " " +
						date(r.end_date) + "</div></div><div class='ss-li-right'>" +
						SS.badge(__("{0} dias", [r.days_left]), r.days_left <= 30 ? "bad" : "warn") +
						"</div></div></li>";
				}).join("") + "</ul>") : "";

			app.render(cards +
				(d.compensation_visible ? "" :
					'<div class="ss-alert info"><i class="fa fa-lock" aria-hidden="true"></i>' +
					"<span>" + esc(__("Esta área não mostra salários, IBAN nem descontos. " +
						"Informação de remuneração requer um perfil de processamento salarial.")) +
					"</span></div>") +
				'<div class="ss-two">' + today + pend + "</div>" + contractsHtml);

			SS.on(app.$view, "[data-go]", "click", function () {
				location.hash = "#" + this.dataset.go;
			});
		});
	}

	/* ==================================================================== TEAM */
	function team(app, arg) {
		if (arg) return memberDetail(app, arg);
		return SS.api("my_team").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("A minha equipa"),
					SS.empty("fa-users", __("Sem colaboradores atribuídos."),
						__("A equipa é construída a partir da chefia definida na ficha " +
							"de cada colaborador."))));
			}
			var list = '<ul class="ss-list">' + rows.map(function (r) {
				return '<li class="tap" tabindex="0" role="button" data-member="' +
					esc(r.name) + '"><div class="ss-li-top"><div>' +
					'<div class="ss-li-title">' + esc(r.employee_name) + "</div>" +
					'<div class="ss-li-sub">' + esc(r.designation || "—") +
					(r.department ? " · " + esc(r.department) : "") + "</div>" +
					'<div class="ss-li-sub">' + esc(__("Admissão")) + " " +
					date(r.date_of_joining) + "</div></div>" +
					'<div class="ss-li-right">' +
					(r.on_leave_today ? SS.badge(__("Ausente"), "info")
						: SS.badge(__("Presente"), "ok")) +
					(r.contract && r.contract.probation_status === "In Progress"
						? "<br>" + SS.badge(__("Exp."), "warn") : "") +
					"</div></div></li>";
			}).join("") + "</ul>";

			// Deliberately no salary column — and none to add: the endpoint does not
			// return one for a line manager (§24).
			var table = '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
				"<th>" + esc(__("Colaborador")) + "</th><th>" + esc(__("Função")) +
				"</th><th>" + esc(__("Departamento")) + "</th><th>" + esc(__("Admissão")) +
				"</th><th>" + esc(__("Hoje")) + "</th><th>" + esc(__("Contacto")) +
				"</th></tr></thead><tbody>" + rows.map(function (r) {
					return '<tr><td>' + member(r.employee_name, r.name) + "</td><td>" +
						esc(r.designation || "—") + "</td><td>" + esc(r.department || "—") +
						"</td><td>" + date(r.date_of_joining) + "</td><td>" +
						(r.on_leave_today ? SS.badge(__("Ausente"), "info")
							: SS.badge(__("Presente"), "ok")) + "</td><td>" +
						esc(r.company_email || "—") + "</td></tr>";
				}).join("") + "</tbody></table></div>";

			app.render('<section class="ss-card ss-desktop-table"><div class="ss-card-head">' +
				"<h2>" + esc(__("A minha equipa")) + "</h2>" +
				'<span class="ss-badge mute">' + SS.num(rows.length) + "</span></div>" +
				'<div class="ss-filters"><input type="search" id="ss-q" placeholder="' +
				esc(__("Procurar por nome ou função…")) + '" aria-label="' +
				esc(__("Procurar")) + '"></div>' + list + table + "</section>");

			var q = SS.qs("#ss-q", app.$view);
			q.addEventListener("input", function () {
				var t = q.value.toLowerCase();
				SS.qsa(".ss-list > li, .ss-table tbody tr", app.$view).forEach(function (el) {
					el.style.display = el.textContent.toLowerCase().indexOf(t) === -1
						? "none" : "";
				});
			});

			SS.on(app.$view, "[data-member]", "click", function () {
				location.hash = "#team/" + encodeURIComponent(this.dataset.member);
			});
			SS.on(app.$view, "[data-member]", "keydown", function (e) {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					location.hash = "#team/" + encodeURIComponent(this.dataset.member);
				}
			});
		});
	}

	function memberDetail(app, employee) {
		return Promise.all([
			SS.api("team_member", { employee: employee }),
			SS.api("team_member_leave", { employee: employee })
		]).then(function (res) {
			var m = res[0] || {}, bal = res[1] || [];
			var c = m.contract;

			app.render('<button class="ss-btn sm" data-back style="margin-bottom:12px">' +
				'<i class="fa fa-arrow-left" aria-hidden="true"></i> ' + esc(__("Equipa")) +
				"</button>" +
				card(m.employee_name, "<dl>" +
					kv(__("Função"), esc(m.designation)) +
					kv(__("Departamento"), esc(m.department)) +
					kv(__("Local"), esc(m.branch)) +
					kv(__("Admissão"), date(m.date_of_joining)) +
					kv(__("E-mail"), esc(m.company_email)) +
					kv(__("Telemóvel"), esc(m.cell_number)) +
					kv(__("Estado"), SS.statusBadge(m.status)) + "</dl>") +
				(c ? card(__("Contrato"), "<dl>" +
					kv(__("Tipo"), esc(c.contract_type)) +
					kv(__("Início"), date(c.start_date)) +
					kv(__("Termo"), c.is_open_ended ? esc(__("Sem termo")) : date(c.end_date)) +
					kv(__("Estado"), SS.statusBadge(c.status)) +
					kv(__("Período experimental"), c.probation_end
						? date(c.probation_end) + " " + SS.statusBadge(c.probation_status)
						: esc(__("Não aplicável"))) + "</dl>" +
					(c.probation_status && ["In Progress", "Review Due", "Overdue"]
						.indexOf(c.probation_status) !== -1
						? '<button class="ss-btn primary block" style="margin-top:10px" ' +
							'data-prob="' + esc(c.name) + '">' +
							esc(__("Dar parecer sobre o período experimental")) + "</button>"
						: "") +
					(!c.is_open_ended && c.end_date
						? '<button class="ss-btn block" style="margin-top:8px" data-renew="' +
							esc(c.name) + '">' +
							esc(__("Recomendar renovação")) + "</button>"
						: "")) : "") +
				card(__("Saldo de ausências"),
					bal.length
						? '<div class="ss-table-wrap"><table class="ss-table"><thead><tr><th>' +
							esc(__("Tipo")) + '</th><th class="num">' + esc(__("Disponíveis")) +
							'</th><th class="num">' + esc(__("Gozados")) +
							"</th></tr></thead><tbody>" + bal.map(function (r) {
								return "<tr><td>" + esc(r.leave_type) + '</td><td class="num"><b>' +
									SS.num(r.available, 1) + '</b></td><td class="num">' +
									SS.num(r.used, 1) + "</td></tr>";
							}).join("") + "</tbody></table></div>"
						: SS.empty("fa-calendar-o", __("Sem direitos atribuídos."), "")) +
				card(__("Documentos"),
					(m.documents || []).length
						? '<ul class="ss-list">' + m.documents.map(function (d) {
							return "<li><div class='ss-li-top'><div class='ss-li-title'>" +
								esc(d.document_type) + "</div><div class='ss-li-right'>" +
								SS.statusBadge(d.status) + "</div></div></li>";
						}).join("") + "</ul>"
						: SS.empty("fa-folder-open-o", __("Sem documentos visíveis."),
							__("Documentos médicos e confidenciais nunca são mostrados " +
								"à chefia."))));

			SS.on(app.$view, "[data-back]", "click", function () { location.hash = "#team"; });
			SS.on(app.$view, "[data-prob]", "click", function () {
				probationForm(app, this.dataset.prob, m.employee_name);
			});
			SS.on(app.$view, "[data-renew]", "click", function () {
				renewalForm(app, this.dataset.renew, m.employee_name);
			});
		});
	}

	/* =================================================================== INBOX */
	function inbox(app) {
		return SS.api("team_approval_inbox").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("Aprovações"),
					SS.empty("fa-check-circle-o", __("Não tem nada por decidir."),
						__("Pedidos de férias, ocorrências de assiduidade e períodos " +
							"experimentais aparecem aqui."))));
			}
			app.render(card(__("Aprovações"),
				'<ul class="ss-list">' + rows.map(function (r) {
					var actions = "";
					if (r.action === "leave") {
						actions = '<button class="ss-btn ok sm" data-leave-ok="' + esc(r.name) +
							'">' + esc(__("Aprovar")) + "</button>" +
							'<button class="ss-btn bad sm" data-leave-no="' + esc(r.name) +
							'">' + esc(__("Recusar")) + "</button>";
					} else if (r.action === "attendance") {
						actions = '<button class="ss-btn ok sm" data-att-ok="' + esc(r.name) +
							'">' + esc(__("Justificar")) + "</button>" +
							'<button class="ss-btn bad sm" data-att-no="' + esc(r.name) +
							'">' + esc(__("Não justificar")) + "</button>";
					} else if (r.action === "probation") {
						actions = '<button class="ss-btn primary sm" data-prob="' + esc(r.name) +
							'" data-who="' + esc(r.employee_name) + '">' +
							esc(__("Dar parecer")) + "</button>";
					}
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
						esc(r.detail) + "</div><div class='ss-li-sub'>" + esc(r.name) +
						"</div></div><div class='ss-li-right'>" +
						SS.badge(r.type, "info") + "</div></div>" +
						'<div class="ss-li-actions">' + actions + "</div></li>";
				}).join("") + "</ul>" +
				'<div class="ss-alert info" style="margin-top:10px">' +
				'<i class="fa fa-balance-scale" aria-hidden="true"></i><span>' +
				esc(__("O parecer sobre períodos experimentais e renovações é registado para " +
					"o RH. A decisão formal continua a ser do RH.")) + "</span></div>"));

			SS.on(app.$view, "[data-leave-ok]", "click", function () {
				var n = this.dataset.leaveOk;
				SS.confirm(__("Aprovar férias"),
					__("Confirma a aprovação do pedido {0}?", [n]), __("Aprovar"), function () {
						return SS.api("leave_decision", { name: n, action: "approve" })
							.then(function () {
								SS.toast(__("Pedido aprovado."), "ok");
								app.reload();
							});
					});
			});
			SS.on(app.$view, "[data-leave-no]", "click", function () {
				var n = this.dataset.leaveNo;
				SS.modal({
					title: __("Recusar férias"),
					fields: [{ name: "reason", label: __("Motivo"), type: "textarea",
						required: true,
						hint: __("O colaborador vê este motivo no pedido.") }],
					okLabel: __("Recusar"),
					onSubmit: function (v) {
						return SS.api("leave_decision",
							{ name: n, action: "reject", reason: v.reason }).then(function () {
								SS.toast(__("Pedido recusado."), "ok");
								app.reload();
							});
					}
				});
			});
			SS.on(app.$view, "[data-att-ok]", "click", function () {
				var n = this.dataset.attOk;
				SS.modal({
					title: __("Justificar ocorrência"),
					fields: [{ name: "reason", label: __("Observações"), type: "textarea" }],
					okLabel: __("Justificar"),
					onSubmit: function (v) {
						return SS.api("attendance_justification_decision",
							{ name: n, action: "justify", reason: v.reason }).then(function () {
								SS.toast(__("Ocorrência justificada."), "ok");
								app.reload();
							});
					}
				});
			});
			SS.on(app.$view, "[data-att-no]", "click", function () {
				var n = this.dataset.attNo;
				SS.modal({
					title: __("Não justificar"),
					fields: [{ name: "reason", label: __("Motivo"), type: "textarea",
						required: true }],
					okLabel: __("Confirmar"),
					onSubmit: function (v) {
						return SS.api("attendance_justification_decision",
							{ name: n, action: "reject", reason: v.reason }).then(function () {
								SS.toast(__("Ocorrência marcada como não justificada."), "ok");
								app.reload();
							});
					}
				});
			});
			SS.on(app.$view, "[data-prob]", "click", function () {
				probationForm(app, this.dataset.prob, this.dataset.who);
			});
		});
	}

	function probationForm(app, contract, who) {
		SS.modal({
			title: __("Parecer — período experimental"),
			html: '<div class="ss-alert info"><i class="fa fa-info-circle" aria-hidden="true">' +
				"</i><span>" + esc(__("O seu parecer sobre {0} fica registado no contrato. " +
					"A confirmação, prorrogação ou cessação é decidida pelo RH.", [who || ""])) +
				"</span></div>",
			fields: [
				{ name: "recommendation", label: __("Recomendação"), type: "select",
					required: true, options: [
						{ value: "Confirm", label: __("Confirmar o colaborador") },
						{ value: "Extend", label: __("Prorrogar o período experimental") },
						{ value: "Terminate", label: __("Não confirmar") }
					] },
				{ name: "notes", label: __("Comentários"), type: "textarea",
					hint: __("Fundamente — o RH usa este texto na decisão.") }
			],
			okLabel: __("Registar parecer"),
			onSubmit: function (v) {
				return SS.api("probation_recommendation", {
					name: contract, recommendation: v.recommendation, notes: v.notes
				}).then(function () {
					SS.toast(__("Parecer registado."), "ok");
					app.reload();
				});
			}
		});
	}

	function renewalForm(app, contract, who) {
		SS.modal({
			title: __("Recomendação de renovação"),
			html: '<div class="ss-alert info"><i class="fa fa-info-circle" aria-hidden="true">' +
				"</i><span>" + esc(__("Recomendação sobre o contrato de {0}. A renovação é " +
					"formalizada pelo RH.", [who || ""])) + "</span></div>",
			fields: [
				{ name: "recommendation", label: __("Recomendação"), type: "select",
					required: true, options: [
						{ value: "Renew", label: __("Renovar") },
						{ value: "Do Not Renew", label: __("Não renovar") }
					] },
				{ name: "notes", label: __("Comentários"), type: "textarea" }
			],
			okLabel: __("Registar"),
			onSubmit: function (v) {
				return SS.api("renewal_recommendation", {
					name: contract, recommendation: v.recommendation, notes: v.notes
				}).then(function () {
					SS.toast(__("Recomendação registada."), "ok");
					app.reload();
				});
			}
		});
	}

	/* =================================================================== LEAVE */
	function teamLeave(app) {
		return SS.api("team_leave_requests", { status: "" }).then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("Férias da equipa"),
					SS.empty("fa-plane", __("Sem pedidos de férias."),
						__("Os pedidos da sua equipa aparecem aqui assim que forem submetidos."))));
			}
			app.render(card(__("Férias da equipa"),
				'<ul class="ss-list">' + rows.map(function (r) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
						esc(r.leave_type) + " · " + date(r.from_date) + " → " +
						date(r.to_date) + " · " +
						esc(__("{0} dia(s)", [SS.num(r.total_leave_days, 1)])) +
						"</div></div><div class='ss-li-right'>" + SS.statusBadge(r.status) +
						"</div></div></li>";
				}).join("") + "</ul>"));
		});
	}

	/* ============================================================== ATTENDANCE */
	function exceptions(app) {
		return SS.api("team_attendance_exceptions").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("Ocorrências de assiduidade"),
					SS.empty("fa-check-circle-o", __("Sem ocorrências por resolver."),
						__("Faltas e atrasos por justificar apareceriam aqui."))));
			}
			app.render(card(__("Ocorrências de assiduidade"),
				'<ul class="ss-list">' + rows.map(function (r) {
					return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
						esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
						esc(r.occurrence_type) + " · " + date(r.occurrence_date) +
						(r.hours ? " · " + esc(__("{0}h", [SS.num(r.hours, 2)])) : "") +
						"</div>" +
						(r.justification_deadline ? "<div class='ss-li-sub'>" +
							esc(__("Prazo para justificar")) + ": " +
							date(r.justification_deadline) + "</div>" : "") +
						"</div><div class='ss-li-right'>" + SS.statusBadge(r.status) +
						"</div></div></li>";
				}).join("") + "</ul>" +
				'<button class="ss-btn primary block" style="margin-top:10px" data-go="inbox">' +
				esc(__("Decidir na caixa de aprovações")) + "</button>"));
			SS.on(app.$view, "[data-go]", "click", function () {
				location.hash = "#" + this.dataset.go;
			});
		});
	}

	/* =============================================================== CONTRACTS */
	function contracts(app) {
		return Promise.all([
			SS.api("team_contract_expiry", { within_days: 180 }),
			SS.api("team_probations")
		]).then(function (res) {
			var exp = res[0] || [], prob = res[1] || [];

			var expHtml = card(__("Contratos a terminar"),
				exp.length
					? '<ul class="ss-list">' + exp.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
							esc(r.contract_type || "") + " · " + esc(__("termina")) + " " +
							date(r.end_date) + "</div><div class='ss-li-sub'>" +
							esc(r.name) + " · " + esc(__("Estado RH")) + ": " +
							esc(r.status || "—") + "</div></div>" +
							"<div class='ss-li-right'>" +
							SS.badge(__("{0} dias", [r.days_left]),
								r.days_left <= 30 ? "bad" : "warn") + "</div></div>" +
							'<div class="ss-li-actions"><button class="ss-btn sm" ' +
							'data-renew="' + esc(r.name) + '" data-who="' +
							esc(r.employee_name) + '">' +
							esc(__("Recomendar renovação")) + "</button></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-file-text-o", __("Nenhum contrato a terminar nos " +
						"próximos 6 meses."), __("Será avisado com 90 dias de antecedência.")));

			var probHtml = card(__("Períodos experimentais"),
				prob.length
					? '<ul class="ss-list">' + prob.map(function (r) {
						return "<li><div class='ss-li-top'><div><div class='ss-li-title'>" +
							esc(r.employee_name) + "</div><div class='ss-li-sub'>" +
							esc(__("Termina")) + " " + date(r.probation_end) +
							"</div></div><div class='ss-li-right'>" +
							SS.badge(__("{0} dias", [r.days_left]),
								r.days_left < 0 ? "bad" : "warn") + "</div></div>" +
							'<div class="ss-li-actions"><button class="ss-btn primary sm" ' +
							'data-prob="' + esc(r.name) + '" data-who="' +
							esc(r.employee_name) + '">' + esc(__("Dar parecer")) +
							"</button></div></li>";
					}).join("") + "</ul>"
					: SS.empty("fa-user-plus", __("Sem períodos experimentais a rever."), ""));

			app.render('<div class="ss-two">' + expHtml + probHtml + "</div>");

			SS.on(app.$view, "[data-renew]", "click", function () {
				renewalForm(app, this.dataset.renew, this.dataset.who);
			});
			SS.on(app.$view, "[data-prob]", "click", function () {
				probationForm(app, this.dataset.prob, this.dataset.who);
			});
		});
	}


	/* ============================================================== PERFORMANCE */
	function reviews(app) {
		return SS.api("my_team_reviews").then(function (rows) {
			if (!rows.length) {
				return app.render(card(__("Avaliações de desempenho"),
					SS.empty("fa-star-o", __("Sem avaliações atribuídas."),
						__("O RH cria as avaliações a partir de um ciclo de desempenho."))));
			}
			var today = SS.today();
			app.render(card(__("Avaliações de desempenho"),
				'<ul class="ss-list">' + rows.map(function (r) {
					var overdue = r.custom_due_date && r.custom_due_date < today &&
						r.custom_review_state !== "Finalised";
					return '<li><div class="ss-li-top"><div>' +
						'<div class="ss-li-title">' + esc(r.employee_name) + "</div>" +
						'<div class="ss-li-sub">' +
						esc(SS.date(r.start_date, { short: true })) + " → " +
						esc(SS.date(r.end_date)) +
						(r.custom_due_date ? " · " + esc(__("prazo")) + " " +
							date(r.custom_due_date) : "") + "</div>" +
						(app.ctx && r.custom_manager && r.custom_manager !== app.ctx.employee
							? '<div class="ss-li-sub">' + esc(__("Delegada")) + "</div>" : "") +
						"</div>" +
						'<div class="ss-li-right">' +
						SS.badge(r.custom_review_state || __("Por iniciar"),
							overdue ? "bad" : (r.custom_review_state === "Finalised"
								? "ok" : "info")) + "</div></div>" +
						'<div class="ss-li-actions">' +
						'<button class="ss-btn primary sm" data-review="' + esc(r.name) +
						'">' + esc(r.custom_review_state === "Pending Manager"
							? __("Avaliar") : __("Ver")) + "</button></div></li>";
				}).join("") + "</ul>" +
				'<div class="ss-alert info" style="margin-top:10px">' +
				'<i class="fa fa-lock" aria-hidden="true"></i><span>' +
				esc(__("Uma avaliação não altera salários. Se propuser um aumento, o RH " +
					"cria um pedido de alteração salarial com aprovação própria.")) +
				"</span></div>"));

			SS.on(app.$view, "[data-review]", "click", function () {
				openReview(app, this.dataset.review);
			});
		});
	}

	function openReview(app, name) {
		SS.api("review_detail", { name: name }).then(function (d) {
			if (!d.editable) {
				SS.modal({
					title: d.employee_name,
					html: '<div class="ss-table-wrap"><table class="ss-table"><thead><tr>' +
						"<th>" + esc(__("Objectivo")) + '</th><th class="num">' +
						esc(__("Peso")) + '</th><th class="num">' + esc(__("Nota")) +
						"</th></tr></thead><tbody>" + (d.goals || []).map(function (g) {
							return "<tr><td>" + esc(g.kra) + '</td><td class="num">' +
								SS.num(g.per_weightage, 0) + '%</td><td class="num">' +
								SS.num(g.score, 2) + "</td></tr>";
						}).join("") + "</tbody></table></div>" +
						'<div class="ss-pay-total"><span class="l">' + esc(__("Resultado")) +
						'</span><span class="v">' + SS.num(d.total_score, 2) + "</span></div>" +
						(d.custom_manager_comments
							? '<div class="ss-explain"><h3>' + esc(__("Comentários")) +
								"</h3><span>" + esc(d.custom_manager_comments) + "</span></div>"
							: ""),
					okLabel: __("Fechar"), cancelLabel: __("Fechar"),
					onSubmit: function () { return true; }
				});
				return;
			}

			var fields = (d.goals || []).map(function (g) {
				return { name: g.name, label: g.kra + "  (" + SS.num(g.per_weightage, 0) + "%)",
					type: "number", step: "0.1", min: "0", value: g.score || "",
					hint: __("Nota de 0 a 5.") };
			});
			fields.push({ name: "comments", label: __("Comentários"), type: "textarea",
				value: d.custom_manager_comments || "" });

			SS.modal({
				title: __("Avaliar {0}", [d.employee_name]),
				html: '<div class="ss-alert info"><i class="fa fa-info-circle" ' +
					'aria-hidden="true"></i><span>' +
					esc(__("Classifique cada objectivo de 0 a 5. O resultado é ponderado " +
						"automaticamente pelos pesos definidos pelo RH.")) + "</span></div>",
				fields: fields,
				okLabel: __("Submeter avaliação"),
				onSubmit: function (v) {
					var goals = {};
					(d.goals || []).forEach(function (g) {
						if (v[g.name] !== undefined && v[g.name] !== "") {
							goals[g.name] = v[g.name];
						}
					});
					return SS.api("submit_review", {
						name: name, goals: JSON.stringify(goals),
						comments: v.comments, submit: 1
					}).then(function (r) {
						SS.toast(__("Avaliação submetida (resultado {0}).",
							[SS.num(r.total_score, 2)]), "ok");
						app.reload();
					});
				}
			});
		});
	}

	/* ================================================================= CALENDAR */
	function calendar(app) {
		return SS.api("team_calendar").then(function (d) {
			if (!d.team_size) {
				return app.render(card(__("Calendário da equipa"),
					SS.empty("fa-calendar-o", __("Sem colaboradores atribuídos."), "")));
			}
			var byEmployee = {};
			(d.leave || []).forEach(function (r) {
				(byEmployee[r.employee_name] = byEmployee[r.employee_name] || []).push(r);
			});
			var names = Object.keys(byEmployee).sort();

			app.render(card(__("Ausências previstas"),
				names.length
					? '<ul class="ss-list">' + names.map(function (n) {
						return "<li><div class='ss-li-title'>" + esc(n) + "</div>" +
							byEmployee[n].map(function (r) {
								return "<div class='ss-li-sub' style='margin-top:5px'>" +
									esc(r.leave_type) + " · " + date(r.from_date) + " → " +
									date(r.to_date) + " · " +
									esc(__("{0} dia(s)", [SS.num(r.total_leave_days, 1)])) +
									" " + SS.statusBadge(r.status) + "</div>";
							}).join("") + "</li>";
					}).join("") + "</ul>"
					: SS.empty("fa-check-circle-o",
						__("Ninguém da equipa tem ausências previstas."),
						__("Mostra os próximos 45 dias."))) +
				((d.holidays || []).length ? card(__("Feriados"),
					'<ul class="ss-list">' + d.holidays.map(function (h) {
						return "<li><div class='ss-li-top'><div class='ss-li-title'>" +
							date(h.holiday_date) + "</div><div class='ss-li-right'>" +
							'<span class="ss-li-sub">' + esc(h.description || "") +
							"</span></div></div></li>";
					}).join("") + "</ul>") : "") +
				'<div class="ss-alert info"><i class="fa fa-shield" aria-hidden="true"></i>' +
				"<span>" + esc(d.privacy_note || "") + "</span></div>");
		});
	}

	/* ===================================================================== BOOT */
	document.addEventListener("DOMContentLoaded", function () {
		if (!SS.qs("#mss-root")) return;
		var app = new SS.App({
			mount: "#mss-root",
			title: "Área da Chefia",
			nav: [
				{ key: "home", label: "Início", icon: "fa-home" },
				{ key: "team", label: "Equipa", icon: "fa-users" },
				{ key: "inbox", label: "Aprovações", icon: "fa-check-square-o" },
				{ key: "leave", label: "Férias", icon: "fa-plane" },
				{ key: "attendance", label: "Assiduidade", icon: "fa-clock-o" },
				{ key: "calendar", label: "Calendário", icon: "fa-calendar" },
				{ key: "reviews", label: "Desempenho", icon: "fa-star-o" },
				{ key: "contracts", label: "Contratos", icon: "fa-file-text-o" }
			],
			views: {
				home: home, team: team, inbox: inbox, leave: teamLeave,
				attendance: exceptions, contracts: contracts,
				calendar: calendar, reviews: reviews
			},
			boot: function (a) {
				return SS.api("self_service_context").then(function (ctx) {
					a.ctx = ctx;
					if (!ctx.employee) {
						throw new Error(__("A sua conta ainda não está associada a uma ficha " +
							"de colaborador. Contacte o RH."));
					}
					if (!ctx.is_manager) {
						// Not an error and not a permission failure — simply nobody reports
						// to this person. Saying so beats an empty screen.
						throw new Error(__("Não tem colaboradores atribuídos. Esta área fica " +
							"disponível assim que o RH o definir como chefia de alguém."));
					}
					a.setIdentity(ctx.employee_name,
						__("{0} colaborador(es) directo(s)", [ctx.team_size]),
						[{ href: "/ess", icon: "fa-user-circle-o", label: __("A minha área") }]);
				});
			}
		});
		app.start();
	});
})(window.IsoftSS);
