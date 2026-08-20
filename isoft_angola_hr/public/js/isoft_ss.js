/* Isoft Angola HR — self-service portal runtime.
 *
 * Shared by the Employee (/ess) and Manager (/mss) portals. Deliberately small and
 * dependency-free: it runs on Frappe's website bundle, which has no Desk, no controls
 * library and no datatable.
 *
 * The one rule this file exists to enforce: THE SERVER DECIDES. Nothing here computes a
 * balance, a tax, an entitlement or an eligibility. Every number rendered came from an
 * endpoint, and every action is a call the server is free to refuse. Duplicating a rule
 * in JavaScript is how the two halves start disagreeing.
 */
/* eslint-env browser */
/* global frappe */

(function (root) {
	"use strict";

	var SS = {};

	// ------------------------------------------------------------------ i18n --
	// Portal pages have frappe.__ but no dictionary for our strings unless the site has
	// translations installed; __ falls back to the source string, which is what we want.
	SS.__ = function (txt, args) {
		var out = (root.__ ? root.__(txt) : txt) || txt;
		if (args) {
			args.forEach(function (a, i) {
				out = out.replace(new RegExp("\\{" + i + "\\}", "g"), a);
			});
		}
		return out;
	};
	var __ = SS.__;

	// --------------------------------------------------------------- escaping --
	// Everything interpolated into innerHTML goes through here. No exceptions: employee
	// names, document types and rejection reasons are all user-supplied text.
	SS.esc = function (v) {
		if (v === null || v === undefined) return "";
		return String(v)
			.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
	};
	var esc = SS.esc;

	// -------------------------------------------------------------- formatting --
	SS.currency = "AKZ";

	SS.money = function (v, opts) {
		var n = parseFloat(v);
		if (isNaN(n)) n = 0;
		var s = n.toLocaleString("pt-AO", {
			minimumFractionDigits: 2, maximumFractionDigits: 2
		});
		return (opts && opts.bare) ? s : s + " " + SS.currency;
	};

	SS.num = function (v, dp) {
		var n = parseFloat(v);
		if (isNaN(n)) n = 0;
		return n.toLocaleString("pt-AO", {
			minimumFractionDigits: dp === undefined ? 0 : dp,
			maximumFractionDigits: dp === undefined ? 2 : dp
		});
	};

	var MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
		"Jul", "Ago", "Set", "Out", "Nov", "Dez"];

	SS.date = function (v, opts) {
		if (!v) return "—";
		var d = SS.parseDate(v);
		if (!d) return esc(v);
		var s = ("0" + d.getDate()).slice(-2) + " " + MONTHS[d.getMonth()];
		return (opts && opts.short) ? s : s + " " + d.getFullYear();
	};

	SS.period = function (from, to) {
		var a = SS.parseDate(from), b = SS.parseDate(to);
		if (!a || !b) return esc(from) + " → " + esc(to);
		if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
			return MONTHS[a.getMonth()] + " " + a.getFullYear();
		}
		return SS.date(from, { short: true }) + " → " + SS.date(to);
	};

	// Dates arrive as "YYYY-MM-DD". Parsing that with `new Date(str)` is UTC in some
	// browsers and local in others, which silently shifts a date by a day.
	SS.parseDate = function (v) {
		if (!v) return null;
		if (v instanceof Date) return v;
		var m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/);
		if (!m) return null;
		return new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
	};

	SS.today = function () {
		var d = new Date();
		return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) +
			"-" + ("0" + d.getDate()).slice(-2);
	};

	SS.initials = function (name) {
		var parts = String(name || "?").trim().split(/\s+/);
		if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
		return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
	};

	// -------------------------------------------------------------------- API --
	var HR = "isoft_angola_hr.isoft_angola_hr.hr_api.";

	/* Friendly wording for the errors an employee can actually cause (§70).
	 * Anything not matched here falls back to the server's own message, which the
	 * services were written to phrase in plain language. A Python traceback is never
	 * shown — see `describeError`. */
	var FRIENDLY = [
		[/insufficient|not enough|saldo|balance/i,
			"Não possui saldo suficiente para este pedido."],
		[/overlap|sobrep/i, "Este pedido sobrepõe-se a outro já existente."],
		[/only access your own|your own records/i,
			"Só pode consultar os seus próprios registos."],
		[/not linked to an employee/i,
			"A sua conta ainda não está associada a uma ficha de colaborador. Contacte o RH."],
		[/not authorised|not permitted|PermissionError/i,
			"Não tem permissão para esta operação."]
	];

	SS.describeError = function (err) {
		var msg = (err && err.message) || "";
		// A traceback must never reach the screen. If that is all we have, say something
		// true and useful instead of leaking file paths and line numbers.
		if (/Traceback \(most recent call last\)/.test(msg)) msg = "";
		if (!msg) return __("Não foi possível concluir a operação. Tente novamente ou contacte o RH.");
		for (var i = 0; i < FRIENDLY.length; i++) {
			if (FRIENDLY[i][0].test(msg)) return __(FRIENDLY[i][1]);
		}
		return msg;
	};

	function serverMessage(payload) {
		// Frappe returns thrown messages as _server_messages: a JSON string containing an
		// array of JSON strings. Unwrapping it is the only way to get the real text.
		try {
			var list = JSON.parse(payload._server_messages || "[]");
			for (var i = 0; i < list.length; i++) {
				var m = typeof list[i] === "string" ? JSON.parse(list[i]) : list[i];
				if (m && m.message) {
					return String(m.message).replace(/<[^>]+>/g, "").trim();
				}
			}
		} catch (e) { /* fall through to exc */ }
		if (payload && payload.exception) return String(payload.exception);
		return "";
	}

	SS.api = function (method, args) {
		var dotted = method.indexOf(".") === -1 ? HR + method : method;
		return fetch("/api/method/" + encodeURIComponent(dotted), {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				"Accept": "application/json",
				"X-Frappe-CSRF-Token": (root.frappe && frappe.csrf_token) || ""
			},
			body: JSON.stringify(args || {})
		}).then(function (res) {
			return res.text().then(function (text) {
				var payload = {};
				try { payload = JSON.parse(text); } catch (e) { payload = {}; }
				if (res.ok) return payload.message;
				if (res.status === 401 || res.status === 403) {
					var e401 = new Error(serverMessage(payload) ||
						__("A sua sessão expirou. Inicie sessão novamente."));
					e401.status = res.status;
					throw e401;
				}
				var err = new Error(serverMessage(payload) || res.statusText);
				err.status = res.status;
				throw err;
			});
		});
	};

	// ------------------------------------------------------------------- DOM --
	SS.h = function (html) {
		var t = document.createElement("template");
		t.innerHTML = String(html).trim();
		return t.content.firstElementChild;
	};

	SS.qs = function (sel, ctx) { return (ctx || document).querySelector(sel); };
	SS.qsa = function (sel, ctx) {
		return Array.prototype.slice.call((ctx || document).querySelectorAll(sel));
	};

	SS.on = function (ctx, sel, evt, fn) {
		ctx.addEventListener(evt, function (e) {
			var t = e.target.closest(sel);
			if (t && ctx.contains(t)) fn.call(t, e, t);
		});
	};

	// ------------------------------------------------------------ components --
	SS.skeleton = function (n) {
		var out = "";
		for (var i = 0; i < (n || 3); i++) out += '<div class="ss-skel ss-skel-card"></div>';
		return out;
	};

	SS.empty = function (icon, title, hint) {
		// An empty state always says what to do next, never just "no data" (§69).
		return '<div class="ss-empty"><i class="fa ' + esc(icon) + '" aria-hidden="true"></i>' +
			"<p>" + esc(title) + "</p>" +
			(hint ? "<small>" + esc(hint) + "</small>" : "") + "</div>";
	};

	SS.badge = function (text, tone) {
		return '<span class="ss-badge ' + esc(tone || "mute") + '">' + esc(text) + "</span>";
	};

	/* Status tone. Colour is only ever a reinforcement — the badge always carries the
	 * word too, so the meaning survives greyscale and colour-blindness (§21). */
	var TONES = {
		"Approved": "ok", "Aprovado": "ok", "Active": "ok", "Valid": "ok", "Paid": "ok",
		"Settled": "ok", "Confirmed": "ok", "Justified": "ok", "Present": "ok",
		"Open": "info", "Pending Approval": "info", "Draft": "mute", "Submitted": "info",
		"Disbursed": "info", "Recovering": "info", "In Progress": "info",
		"Expiring": "warn", "Review Due": "warn", "Half Day": "warn",
		"Pending Justification": "warn", "On Leave": "info",
		"Rejected": "bad", "Expired": "bad", "Overdue": "bad", "Cancelled": "bad",
		"Unjustified": "bad", "Terminated": "bad", "Absent": "bad", "Failed": "bad"
	};
	SS.statusBadge = function (status) {
		if (!status) return "";
		return SS.badge(status, TONES[status] || "mute");
	};

	// --------------------------------------------------------------- toasts ---
	function toastWrap() {
		var w = SS.qs(".ss-toast-wrap");
		if (!w) {
			w = SS.h('<div class="ss-toast-wrap" role="status" aria-live="polite"></div>');
			document.body.appendChild(w);
		}
		return w;
	}

	SS.toast = function (msg, tone, ms) {
		var el = SS.h('<div class="ss-toast ' + esc(tone || "") + '">' + esc(msg) + "</div>");
		toastWrap().appendChild(el);
		setTimeout(function () {
			el.style.transition = "opacity .3s";
			el.style.opacity = "0";
			setTimeout(function () { el.remove(); }, 320);
		}, ms || 3600);
	};

	SS.announce = function (msg) {
		// Route status changes to a live region as well, so a screen reader hears them.
		var live = SS.qs("#ss-live");
		if (live) live.textContent = msg;
	};

	// ---------------------------------------------------------------- modal ---
	/* A minimal sheet dialog. Fields are declared, not hand-written, so every form on
	 * both portals gets the same labels, focus handling and error slots. */
	SS.modal = function (opts) {
		var back = SS.h('<div class="ss-modal-back" role="dialog" aria-modal="true" ' +
			'aria-label="' + esc(opts.title) + '"></div>');
		var body = (opts.fields || []).map(function (f) {
			var id = "f_" + f.name;
			var ctl;
			if (f.type === "select") {
				ctl = '<select id="' + id + '" name="' + esc(f.name) + '">' +
					(f.options || []).map(function (o) {
						var v = o.value === undefined ? o : o.value;
						var l = o.label === undefined ? o : o.label;
						return '<option value="' + esc(v) + '"' +
							(String(f.value) === String(v) ? " selected" : "") + ">" +
							esc(l) + "</option>";
					}).join("") + "</select>";
			} else if (f.type === "textarea") {
				ctl = '<textarea id="' + id + '" name="' + esc(f.name) + '" ' +
					(f.required ? "required " : "") + 'placeholder="' +
					esc(f.placeholder || "") + '">' + esc(f.value || "") + "</textarea>";
			} else if (f.type === "file") {
				ctl = '<input type="file" id="' + id + '" name="' + esc(f.name) +
					'" accept="' + esc(f.accept || SS.ACCEPT) + '"' +
					(f.required ? " required" : "") + ">";
			} else if (f.type === "checkbox") {
				return '<div class="ss-check"><input type="checkbox" id="' + id + '" name="' +
					esc(f.name) + '"' + (f.value ? " checked" : "") + '><label for="' + id +
					'">' + esc(f.label) + "</label></div>";
			} else {
				ctl = '<input type="' + esc(f.type || "text") + '" id="' + id + '" name="' +
					esc(f.name) + '" value="' + esc(f.value === undefined ? "" : f.value) +
					'"' + (f.required ? " required" : "") +
					(f.step ? ' step="' + esc(f.step) + '"' : "") +
					(f.min !== undefined ? ' min="' + esc(f.min) + '"' : "") +
					' placeholder="' + esc(f.placeholder || "") + '">';
			}
			return '<div class="ss-field" data-field="' + esc(f.name) + '">' +
				'<label for="' + id + '">' + esc(f.label) +
				(f.required ? ' <span aria-hidden="true">*</span>' : "") + "</label>" + ctl +
				(f.hint ? '<div class="hint">' + esc(f.hint) + "</div>" : "") +
				'<div class="err-msg" hidden></div></div>';
		}).join("");

		back.innerHTML =
			'<form class="ss-modal" novalidate>' +
				'<div class="ss-modal-head"><h2>' + esc(opts.title) + "</h2>" +
					'<button type="button" class="ss-modal-close" aria-label="' +
					esc(__("Fechar")) + '">&times;</button></div>' +
				'<div class="ss-modal-body">' + (opts.html || "") + body +
					'<div class="ss-modal-error"></div></div>' +
				'<div class="ss-modal-foot">' +
					'<button type="button" class="ss-btn ss-cancel">' +
					esc(opts.cancelLabel || __("Cancelar")) + "</button>" +
					'<button type="submit" class="ss-btn primary ss-ok">' +
					esc(opts.okLabel || __("Confirmar")) + "</button>" +
				"</div>" +
			"</form>";

		var form = SS.qs("form", back);
		var errBox = SS.qs(".ss-modal-error", back);

		function close() {
			document.removeEventListener("keydown", onKey);
			back.remove();
			if (opts.onClose) opts.onClose();
		}
		function onKey(e) {
			if (e.key === "Escape") { e.preventDefault(); close(); }
		}
		document.addEventListener("keydown", onKey);
		SS.qs(".ss-modal-close", back).addEventListener("click", close);
		SS.qs(".ss-cancel", back).addEventListener("click", close);
		back.addEventListener("mousedown", function (e) {
			if (e.target === back) close();
		});

		form.addEventListener("submit", function (e) {
			e.preventDefault();
			var values = {};
			var pending = [];
			(opts.fields || []).forEach(function (f) {
				var el = form.elements[f.name];
				if (!el) return;
				if (f.type === "checkbox") {
					values[f.name] = el.checked ? 1 : 0;
				} else if (f.type === "file") {
					pending.push(SS.readFile(el).then(function (r) { values[f.name] = r; }));
				} else {
					values[f.name] = el.value;
				}
			});
			// Required-field checking happens here rather than through the browser's own
			// validation bubble, which is unreadable on a phone and untranslated.
			var bad = null;
			(opts.fields || []).forEach(function (f) {
				var wrap = SS.qs('[data-field="' + f.name + '"]', form);
				if (!wrap) return;
				var msgEl = SS.qs(".err-msg", wrap);
				wrap.classList.remove("err");
				msgEl.hidden = true;
				if (f.required && !String(values[f.name] || "").trim()) {
					wrap.classList.add("err");
					msgEl.textContent = __("Campo obrigatório.");
					msgEl.hidden = false;
					if (!bad) bad = wrap;
				}
			});
			if (bad) {
				SS.qs("input,select,textarea", bad).focus();
				return;
			}
			var okBtn = SS.qs(".ss-ok", form);
			okBtn.disabled = true;
			okBtn.textContent = __("A processar…");
			errBox.innerHTML = "";
			Promise.all(pending)
				.then(function () { return opts.onSubmit(values, form); })
				.then(function (r) { if (r !== false) close(); })
				.catch(function (err) {
					errBox.innerHTML = '<div class="ss-alert bad" role="alert">' +
						'<i class="fa fa-exclamation-circle" aria-hidden="true"></i><span>' +
						esc(SS.describeError(err)) + "</span></div>";
					errBox.scrollIntoView({ block: "nearest" });
				})
				.then(function () {
					okBtn.disabled = false;
					okBtn.textContent = opts.okLabel || __("Confirmar");
				});
		});

		document.body.appendChild(back);
		var first = SS.qs(".ss-modal-body input, .ss-modal-body select, .ss-modal-body textarea", back);
		(first || SS.qs(".ss-ok", back)).focus();
		return { close: close, form: form };
	};

	/* Read a chosen file as base64 for upload.
	 *
	 * Size and type are re-checked on the server, from the decoded bytes — these limits
	 * exist to fail fast on a phone, not to protect anything. The browser's `accept`
	 * attribute is a convenience and is trivially bypassed.
	 */
	SS.MAX_UPLOAD_MB = 8;
	SS.ACCEPT = ".pdf,.jpg,.jpeg,.png,.heic,.doc,.docx";

	SS.readFile = function (input) {
		return new Promise(function (resolve, reject) {
			var file = input && input.files && input.files[0];
			if (!file) return resolve(null);
			if (file.size > SS.MAX_UPLOAD_MB * 1024 * 1024) {
				return reject(new Error(__("O ficheiro tem mais de {0} MB.",
					[SS.MAX_UPLOAD_MB])));
			}
			var reader = new FileReader();
			reader.onload = function () {
				// Strip the data: prefix; the server accepts either but the payload is
				// smaller without it.
				var out = String(reader.result || "");
				resolve({ filename: file.name, content: out.split(",").pop(),
					size: file.size });
			};
			reader.onerror = function () {
				reject(new Error(__("Não foi possível ler o ficheiro.")));
			};
			reader.readAsDataURL(file);
		});
	};

	SS.confirm = function (title, message, okLabel, onOk) {
		return SS.modal({
			title: title,
			html: '<p style="margin:0 0 4px;line-height:1.5">' + esc(message) + "</p>",
			okLabel: okLabel,
			onSubmit: onOk
		});
	};

	// ------------------------------------------------------------------ app ---
	/* The portal shell. Views are functions returning HTML (or setting it themselves);
	 * routing is hash-based so a screen can be linked to and the back button works. */
	SS.App = function (cfg) {
		this.cfg = cfg;
		this.views = cfg.views;
		this.nav = cfg.nav;
		this.mount = SS.qs(cfg.mount);
		this.state = {};
	};

	SS.App.prototype.start = function () {
		var self = this;
		this.mount.innerHTML =
			'<div class="ss-head"><div class="ss-head-row">' +
				'<div class="ss-avatar" aria-hidden="true">…</div>' +
				'<div><h1 class="ss-title">' + esc(this.cfg.title) + "</h1>" +
				'<div class="ss-sub"></div></div></div></div>' +
			'<nav class="ss-nav" aria-label="' + esc(__("Secções")) + '"></nav>' +
			'<div class="ss-body"><div class="ss-view" tabindex="-1"></div></div>' +
			'<div id="ss-live" class="ss-sr" role="status" aria-live="polite"></div>';

		this.$nav = SS.qs(".ss-nav", this.mount);
		this.$view = SS.qs(".ss-view", this.mount);
		this.$sub = SS.qs(".ss-sub", this.mount);
		this.$avatar = SS.qs(".ss-avatar", this.mount);

		this.renderNav();
		window.addEventListener("hashchange", function () { self.route(); });

		this.$view.innerHTML = SS.skeleton(4);
		Promise.resolve(this.cfg.boot ? this.cfg.boot(this) : null)
			.then(function () { self.route(); })
			.catch(function (err) { self.fatal(err); });
	};

	SS.App.prototype.fatal = function (err) {
		var msg = SS.describeError(err);
		this.$view.innerHTML = '<div class="ss-alert bad" role="alert">' +
			'<i class="fa fa-exclamation-triangle" aria-hidden="true"></i><span>' +
			esc(msg) + "</span></div>" +
			(err && err.status === 403
				? '<a class="ss-btn primary block" href="/login">' + esc(__("Iniciar sessão")) + "</a>"
				: "");
	};

	SS.App.prototype.setIdentity = function (name, sub, links) {
		this.$avatar.textContent = SS.initials(name);
		SS.qs(".ss-title", this.mount).textContent = name || this.cfg.title;
		this.$sub.textContent = sub || "";
		var row = SS.qs(".ss-head-row", this.mount);
		SS.qsa(".ss-head-link", row).forEach(function (a) { a.remove(); });
		(links || []).forEach(function (l) {
			row.appendChild(SS.h('<a class="ss-head-link" href="' + esc(l.href) + '">' +
				'<i class="fa ' + esc(l.icon) + '" aria-hidden="true"></i> ' +
				esc(l.label) + "</a>"));
		});
	};

	SS.App.prototype.renderNav = function () {
		var self = this;
		this.$nav.innerHTML = this.nav.filter(function (n) {
			return !n.when || n.when(self);
		}).map(function (n) {
			return '<button type="button" data-key="' + esc(n.key) + '">' +
				'<i class="fa ' + esc(n.icon) + '" aria-hidden="true"></i>' +
				"<span>" + esc(__(n.label)) + "</span></button>";
		}).join("");
		SS.on(this.$nav, "button", "click", function () {
			location.hash = "#" + this.dataset.key;
		});
	};

	SS.App.prototype.route = function () {
		var self = this;
		var key = (location.hash || "").replace(/^#/, "").split("/")[0] || this.nav[0].key;
		var arg = (location.hash || "").replace(/^#/, "").split("/").slice(1).join("/");
		if (!this.views[key]) key = this.nav[0].key;
		this.current = key;

		SS.qsa("button", this.$nav).forEach(function (b) {
			if (b.dataset.key === key) b.setAttribute("aria-current", "page");
			else b.removeAttribute("aria-current");
		});

		this.$view.innerHTML = SS.skeleton(3);
		this.$view.focus();
		Promise.resolve(this.views[key].call(this, this, decodeURIComponent(arg)))
			.catch(function (err) { self.fatal(err); });
	};

	SS.App.prototype.reload = function () { this.route(); };

	SS.App.prototype.render = function (html) { this.$view.innerHTML = html; };

	root.IsoftSS = SS;
})(window);
