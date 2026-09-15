// Copyright (c) 2026, ISOFT LDA
// For license information, please see license.txt
//
// Approval buttons for the three HR records whose lifecycle is a service state machine
// rather than a docstatus: Employment Contract, Salary Change and Salary Advance.
//
// These transitions existed and were tested, but nothing in any UI called them, so a
// record created in the Desk stayed in Draft with no visible way to submit or approve it.
//
// The form decides nothing. It asks the server which actions the current user may perform
// and draws exactly those; pressing one re-checks role, state, company scope and the
// no-self-approval rule server-side. A stale page therefore cannot approve anything it
// should not — the button simply fails, and the form refreshes to the real state.

frappe.provide("isoft_angola_hr.workflow");

isoft_angola_hr.workflow = {
	DOCTYPES: [
		"Isoft Employment Contract",
		"Isoft Salary Change",
		"Isoft Salary Advance",
	],

	// Transitions whose button is the obvious next step, so it gets the primary style.
	PRIMARY: ["submit_for_approval", "approve", "apply", "disburse"],
	// Transitions styled as a warning: they refuse, undo or end something.
	DANGER: ["reject", "cancel", "terminate"],

	render(frm) {
		if (frm.is_new() || frm.doc.__islocal || !frm.doc.name) return;

		frappe.call({
			method: "isoft_angola_hr.isoft_angola_hr.hr_api.document_actions",
			args: { doctype: frm.doctype, name: frm.doc.name },
			callback: (r) => {
				const data = (r && r.message) || {};
				const actions = data.actions || [];
				const reasonRequired = new Set(data.reason_required || []);

				frm.dashboard.clear_headline();
				frm.dashboard.set_headline(isoft_angola_hr.workflow.panel(data, actions));

				actions.forEach((a) => {
					const btn = frm.add_custom_button(__(a.label), () =>
						isoft_angola_hr.workflow.run(frm, a, reasonRequired.has(a.action))
					);
					if (isoft_angola_hr.workflow.PRIMARY.includes(a.action)) {
						btn.addClass("btn-primary");
					} else if (isoft_angola_hr.workflow.DANGER.includes(a.action)) {
						btn.addClass("btn-danger");
					}
				});
			},
		});
	},

	// The four questions every approval screen has to answer, in one block:
	// what is the status, who must act, what can I do, what happens next.
	// Everything drawn here is computed server-side from the transition table.
	panel(data, actions) {
		const esc = frappe.utils.escape_html;
		const strip = (data.progress || []).map((p) => {
			const colour = p.state === "done" ? "#1f7a4d"
				: p.state === "current" ? "#0b5cad" : "#8d97a1";
			const weight = p.state === "current" ? "600" : "400";
			const mark = p.state === "done" ? "✓ " : (p.state === "current" ? "▶ " : "");
			return `<span style="color:${colour};font-weight:${weight};white-space:nowrap">
				${mark}${esc(p.label)}</span>`;
		}).join(`<span style="color:#c3cad1;margin:0 6px">›</span>`);

		const rows = [
			[__("Status"), `<b>${esc(data.status_label || data.status || "")}</b>`],
			[__("Next"), esc(data.next_action || "")],
		];
		if (data.waiting_for) rows.push([__("Waiting for"), esc(data.waiting_for)]);
		if (!actions.length) {
			rows.push(["", `<span class="text-muted">${__("You have no action on this record.")}</span>`]);
		}

		return `<div style="line-height:1.7">
			${rows.map(([k, v]) => `<div>${k ? `<span style="display:inline-block;min-width:92px;color:#6b7680">${k}</span>` : `<span style="display:inline-block;min-width:92px"></span>`}${v}</div>`).join("")}
			<div style="margin-top:6px">${strip}</div>
		</div>`;
	},

	run(frm, action, needsReason) {
		const call = (reason) =>
			frappe.call({
				method: "isoft_angola_hr.isoft_angola_hr.hr_api.perform_document_action",
				args: { doctype: frm.doctype, name: frm.doc.name, action: action.action, reason },
				freeze: true,
				freeze_message: __("{0}…", [__(action.label)]),
			}).then((r) => {
				const status = ((r && r.message) || {}).status;
				if (status) frappe.show_alert({ message: __("Status: {0}", [__(status)]), indicator: "green" });
				frm.reload_doc();
			});

		if (needsReason) {
			// A refusal or a termination has to be accountable, so the reason is required
			// rather than optional — it is stamped on the record by the service.
			frappe.prompt(
				[{
					fieldname: "reason", fieldtype: "Small Text",
					label: __("Reason"), reqd: 1,
				}],
				(v) => call(v.reason),
				__(action.label),
				__("Confirm")
			);
			return;
		}

		frappe.confirm(__("{0}?", [__(action.label)]), () => call(null));
	},
};

isoft_angola_hr.workflow.DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			isoft_angola_hr.workflow.render(frm);
		},
	});
});

// ---------------------------------------------------------------------------
// Salary Slip: why this slip pays what it pays.
//
// HR could read Total Working Days and Paid Days on the form but had no way to see what
// those numbers mean, which unit they are counted in, or what turned one into the other.
// The whole panel is server-computed (hr_api.salary_slip_explanation) and read from the
// slip — no payroll arithmetic is repeated here.
frappe.ui.form.on("Isoft Salary Slip", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.name) return;
		frappe.call({
			method: "isoft_angola_hr.isoft_angola_hr.hr_api.salary_slip_explanation",
			args: { name: frm.doc.name },
			callback: (r) => {
				const e = (r && r.message) || {};
				if (!e.name) return;
				const esc = frappe.utils.escape_html;
				const num = (v) => frappe.format(v, { fieldtype: "Float", precision: 2 });
				const row = (k, v) =>
					`<div><span style="display:inline-block;min-width:150px;color:#6b7680">${k}</span>${v}</div>`;

				const parts = [
					row(__("Payroll basis"), `<b>${esc(e.mode)}</b> — ${esc(e.basis_note || "")}`),
					row(__("Total working days"), `<b>${num(e.total_working_days)}</b>`),
					row(__("Paid days"), `<b>${num(e.paid_days)}</b>`),
				];
				if (e.absent_days) parts.push(row(__("Absence"), num(e.absent_days) + " " + __("day(s)")));
				if (e.leave_days) parts.push(row(__("Leave (paid)"), num(e.leave_days) + " " + __("day(s)")));
				if (e.overtime_shown) {
					parts.push(row(__("Overtime"),
						`${num(e.overtime_hours)} h — ${frappe.format(e.overtime_amount, { fieldtype: "Currency" })}`));
				}
				if (e.daily_value_formula) parts.push(row(__("Daily value"), esc(e.daily_value_formula)));
				parts.push(row(__("How paid days were set"), esc(e.how)));
				if (!e.reconciles) {
					parts.push(`<div style="margin-top:6px;color:#a94442"><b>${__(
						"Gross − Deductions does not equal Net on this slip. Contact Payroll."
					)}</b></div>`);
				}

				frm.dashboard.clear_headline();
				frm.dashboard.set_headline(`<div style="line-height:1.7">${parts.join("")}</div>`);
			},
		});
	},
});
