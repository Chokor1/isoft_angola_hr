# Isoft Angola HR — Release Notes

## Final Settlement rewritten against Lei n.º 12/23 (August 2026)

The Final Settlement was audited against the current Lei Geral do Trabalho — **Lei
n.º 12/23, de 27 de Dezembro**, read from the *Diário da República, I Série n.º 245* —
and rebuilt. The previous calculation reproduced two hand-written worked examples; it was
not the law, and in one place it printed arithmetic it did not perform.

See **docs/FINAL_SETTLEMENT_LEI_12_23.md** for the article-by-article mapping.

### The defect that started it

The screen printed `6,818.18 × 26 = 150,000`. That product is 177,272.68. The engine had
paid a whole month's remuneration because the days worked reached the divisor, while the
screen went on rendering the daily-rate formula regardless. **Every amount now carries the
arithmetic that produced it, and the engine refuses to return a line whose printed formula
does not evaluate to its own amount** (`settlement._check_reconciles`). A full period now
says "Full period worked — the whole monthly remuneration is due" and shows no
multiplication, because none happens.

### What the law changed

* **Leave is two entitlements, not one** (artigo 212.º). Vested-but-untaken leave and the
  two working days per complete month accruing since 1 January are separate lines. A
  worker who leaves before the right vests falls under n.º 3 instead — from the date of
  admission, and paragraphs 1 and 2 expressly do not apply.
* **Leave is not paid on the monthly remuneration** (artigo 213.º). The base is the
  salário-base plus technical and availability supplements; meal and transport are
  excluded unless the parties agreed otherwise.
* **Complete months are complete** (artigo 238.º n.º 3). Counting by month number made a
  termination on 21 August eight months. It is seven.
* **Seniority rounds by the statute** (artigo 311.º): a fraction of three months or more
  counts as a full year — never `termination year − joining year`.
* **There is no universal severance.** A controlled termination reason now drives the
  settlement, routed to artigo 307.º, 308.º, 309.º or 310.º — or to no compensation at
  all. An unrecorded reason returns **LEGAL INPUT REQUIRED** and blocks approval; it is
  never a silent zero.
* **Notice has money consequences in two directions**: artigo 305.º n.º 2 (the worker owes
  the employer for notice not given) and artigo 17.º n.º 4 (the employer owes 30 days for
  a missed non-renewal notice). Neither is a generic deduction.
* **The payment deadline is artigo 245.º n.º 4**, three days after cessation — not artigo
  240.º, which is about deducting salary for absence. That was a Lei 7/15 memory.

### Configuration is no longer dressed up as law

Lei n.º 12/23 fixes the leave *period* at 22 working days and gives an *hourly* formula
for salary deductions (artigo 237.º n.º 7). It fixes **no** monthly-to-daily divisor. Any
divisor is now labelled **Company Calculation Basis** on screen, in the PDF and in the
Excel export, visually distinct from a statutory article. Three questions the statute does
not settle — the leave divisor, the six-day floor of artigo 204.º n.º 2, and IRT/INSS
incidence on termination compensation — are stated as open and exposed as settings rather
than answered.

### No longer gross-only

IRT and social security are calculated by **the payroll engine's own resolvers** — the
same effective-dated `IRT Table` and `Isoft Statutory Rate` records, in the same order
(social security before IRT), with the same trace. There is no second tax engine. The
vacation gratuity is outside the contribution base and the Christmas bonus is inside it,
exactly as on a salary slip. Outstanding salary advances are recovered, capped so the net
can never go negative; anything uncovered is shown as still outstanding.

### Controlled, and separated

Draft → Pending Approval → Approved, using the existing payroll permissions — no new
roles. The preparer cannot approve their own work and nobody can approve their own
settlement. Complete months and seniority are derived, not typed; overriding one requires
a reason and stamps the user and the time.

### Historical settlements are not rewritten

Settlements calculated before the audit keep their stored amounts for ever. They render
as legacy, with the old salary formula omitted rather than repeated (it never held).
`Recalculate under Lei 12/23` restates one — but only on request, and only after showing
what the figure becomes.

## 0.10.0 — HR-operated mode (August 2026)

The product is now operated by the **HR department**. Employees and line managers are not
required to hold a login for any process. HR records what somebody asks for, and an
authorised HR person decides it. `/ess` and `/mss` remain available and unchanged, but
nothing depends on them.

See **docs/HR_OPERATING_GUIDE.md**.

### New HR front doors
- **Bank Change Requests** — HR records the request; only an HR Manager's approval writes
  the employee's IBAN. Previously an employee could only request their own, so somebody
  without a login could never have their account corrected at all.
- **Employee Documents** — HR files what it is handed, and it counts as verified because
  HR saw the original. Confidential and medical types stay HR Manager only. Previously the
  only uploader was the employee's own self-service session.
- **Attendance justifications** — HR records the explanation and attaches the certificate,
  with the channel it arrived through.
- **Performance** — HR records the line manager's evaluation and the employee's
  acknowledgement. Who **decided** and who **typed it** are stored in different fields.
- **Recruitment** — *Record Interview Result*, for panels who have no account.

### Fixed
- **An employee with no line manager was BLOCKED from every performance cycle.** That
  excluded 43 active employees on this site. They are now included, and HR conducts the
  review.
- **An HR User could not open the application at all** — the Desk page granted HR Manager
  only, so the role that does the day-to-day recording was locked out.
- **An HR User could not record an attendance justification** — the action table
  authorised it, the DocType did not, and the framework refused after the check passed.
- **Bank change requests were invisible to an HR User** — the list required the *approval*
  permission, so the screen rendered blank instead of saying why.
- **Rapid navigation could paint one screen's data into another.** A slow response
  belonging to the screen you just left is now dropped, and boot no longer discards a
  screen you asked for while the overview was still loading.
- The employee quick-action links (Create Contract, Request Salary Change, …) were wired
  to the **Holiday List** dialog instead of the Employee dialog.
- **Insights and the HR Dashboard could not be scrolled.** The viewport-bounded layout
  built for long operational lists was applied to any screen that contained a table, so a
  twelve-month headcount table was mistaken for an operational dataset. The whole screen
  was pinned to the viewport, the 40-row Absenteeism section was squeezed to 38 pixels and
  Leave Usage below it could not be reached at all. Layout mode is now **declared per
  screen** (`SCREEN_LAYOUT`) rather than inferred from the presence of a `<table>`.

- **"Generate working file" could show a Python traceback.** The button opened the
  endpoint as a page navigation, so when the service correctly refused — no approved
  payroll for the period — the user got a raw stack trace instead of the sentence this
  screen already knows how to display. It now asks the same validation the *Validate*
  button asks and lists the problems in place; the download goes through a hidden frame,
  which a popup blocker cannot eat.
- **The IRT / INSS screen opened with no declaration type and no period.** `default` in
  the field definition is not applied by `make_control` on this path, so all three
  controls rendered empty and *Generate* asked the server for submission type `""`. They
  are now set explicitly, to IRT and the current month.

### Payroll accounting and payment from Angola HR
Finance can now complete the whole accounting cycle without opening the ERPNext
Accounting module. No accounting logic was added or duplicated: the accrual and the
payment are still built and submitted only by `make_journal_entry` /
`make_payment_entry`, the state machine still decides what may happen, and the salary
slip status is still derived from submitted vouchers — never stored.

- **Finance area on the payroll run** — a progress strip (Prepare → Approve → Post →
  Release → Pay → Close), and a panel stating what has actually been booked, read from
  the vouchers rather than from a status field.
- **Post Accounting** now confirms with the real figures and the full account mapping
  before writing anything, and **refuses outright when an account is missing**, naming
  it — posting would otherwise fail on the first employee that uses it, after earlier
  employees had already posted.
- **Make Payment** confirms the amount, the bank account and the payroll payable it
  clears, and lets Finance record the bank's own reference (carried in `cheque_no`,
  which ERPNext requires on a Bank Entry, so a reference is never empty).
- **Reconciliation** is shown on any posted run, not left to a separate screen. Before
  payment the payable is reported as *pending payment* rather than as a mismatch — it
  is supposed to be outstanding at that point.
- **Vouchers open inside Angola HR** (`payroll_voucher`, read only): accounts, debit,
  credit, status and how many GL entries it actually produced. "Open in ERPNext"
  remains for accountants who want the full form.
- **Salary slip** gained an Accounting section: accrual and payment, each stated only
  when a submitted voucher exists.

### Fixed
- **A Payroll Finance Approver could not open the Payroll screen at all.** It resolved
  the payroll period on load through an endpoint guarded by `payroll.preview`, which
  Finance does not hold, so the screen opened into a permission error and the payroll
  run could not be reached. The screen now asks the server what the user may do
  (`payroll_capabilities`) and renders accordingly; every action still re-checks
  server-side.
- **The Overview crashed for everyone as soon as any slip reached Posted.** Its status
  counter was still seeded with `Accrued`, a name the status carried before it became
  `Posted`, so the first posted slip produced `KeyError: 'Posted'`. The vocabulary now
  has one definition (`SLIP_STATUSES`) that both sides read.

### Interface — sections and hierarchy
The console read as "title, table, title, table": a method note and an executive
figure looked the same, and the only thing marking a section was a 4px blue tick. It
now has four deliberate levels — **page → section → subsection → content** — built
from reusable components rather than per-screen markup.

- **Section** (`.ahr-panel` / `.ahr-section`) — icon, title, one line saying what the
  section is for, actions on the right, optional status tag. `panel(title, inner)`
  keeps its old signature, so all sixty existing call sites gained the header without
  being rewritten; it also accepts `panel({title, icon, subtitle, actions, tag}, …)`.
- **Actions moved out of titles.** Six screens built headings like
  `Salary Changes <button>`, which left the button floating in the content. Anything
  from the first tag onwards in a title is now routed to the header's action slot.
- **Metric cards** — the figure is the point, so it is large and dark and the label is
  small and muted above it; they no longer share a visual weight, nor carry a gradient
  bar and a floating circle that competed with the number.
- **Filter bar** — its own tinted ground and a legend, so controls never read as
  results; buttons in it are pushed right.
- **Info callout** — explanations and limitations are secondary to the figures they
  describe. Long method notes are collapsed behind a summary that states what is
  inside, instead of occupying more height than the table they explain.
- **Empty states** — `table()` no longer renders a bare "No records"; every empty list
  says what would appear there and what puts it there.
- **Tables** — zebra striping dropped in favour of a consistent row height, a hairline
  and a hover state; the header rule does the separating.
- **Page bar** — every screen now carries `Group › Screen`, rendered centrally once per
  navigation rather than by each view.
- **Watermark** — the desk paints a star on `body::before` at z-index 1, i.e. over the
  page, and it fell across table rows. Pushed behind the content on this route only.
- **Overview** metric strip gained icons and a supporting line; **Insights** regrouped
  into Key metrics / Workforce movement / Attendance / Leave; **Employees** gained a
  live summary (active, no department, no NIF) computed from the rows already loaded;
  **Approval Inbox** now reads as a to-do list; **Settings** grouped into HR, payroll
  and accounting configuration.

### Screen layout
- Modes, chosen per screen and never guessed:
  - **Board** — the Overview. Four figures and three charts, meant to be taken in at a
    glance, so it is sized to the window and nothing scrolls. The chart heights are
    measured and handed to frappe-charts, which draws a fixed-height SVG rather than
    filling its container; the hard-coded 240/260 were why the Overview stood 245px
    taller than a 1366×768 screen. Resizing the window redraws them from the payload
    already held, without another request. Below 900px it reverts to page scrolling —
    three charts compressed into a phone screen would be unreadable.
  - **Data screen** — one primary dataset (Employees, Bulk Contracts, Salary Slips). The
    screen is bounded to the viewport; the toolbar and column headers stay put and only
    the rows move.
  - **Dashboard screen** — several independent sections (Insights, HR Dashboard, Payroll,
    Recruitment, Overview). Ordinary page scrolling; no section may claim the viewport.
    A table only gets its own bounded box once it passes 25 rows, so short analytics
    tables show every row.
  - **Canvas** — the org chart, which pans inside its own viewport.
- A data screen's content region now scrolls rather than clipping when the context above
  the list is unusually tall: unreachable content is a worse failure than a scrollbar.

### Audit
- `request_source` on salary changes, advances, bank changes and leave; a matching field
  on attendance justifications. Who **asked** is recorded separately from who **keyed it**.
- The Approval Inbox shows the request source, who recorded it, its status and which role
  may approve it.
- A **self-approval policy** table, derived from the enforcement code rather than written
  by hand, so it cannot drift away from behaviour.

### Unchanged on purpose
- **Payroll segregation.** Payroll Officer → Payroll Manager → Finance stays three roles.
  HR-operated mode applies to employee processes, not to financial controls.
- Requester ≠ approver for contracts, salary changes, advances, bank changes and
  terminations.
- A missing User ID is now classed **OPTIONAL** — "self-service not available" — and never
  counts as an employee not being ready.

---

## 0.9.0 — Release Candidate (August 2026)

Angola payroll and HR for ERPNext 13. This is the first candidate release: functionally
complete, verified in depth on a live site, and **not yet installable-from-scratch
verified** — see *Known Limitations*.

### Payroll
- IRT calculated from an effective-dated bracket table; the bracket, rate and *parcela
  fixa* used are stored on each salary slip, so loading a new table never changes a
  historical payslip.
- INSS 3% employee / 8% employer, with the incidence base recorded per slip.
- Food and transport exemptions applied against the statutory limits.
- Working-day and payment-day calculation from holiday lists, shifts and attendance.
- Proration, mid-period joiners and leavers, 13th-month and holiday allowances.
- Salary advances recovered from pay, capped so **net pay can never go negative**; the
  uncollectable remainder is reported and carried, never silently written off.
- Final settlement.

### Accounting
- Payroll posts a balanced Journal Entry per run: salary expense, IRT payable, employee
  and employer social security, net payable.
- Cancelling a payroll Journal Entry releases the slips that reference it, so a
  correction cannot deadlock.
- Cost centre and per-component account mapping.

### IRT / INSS
- IRT and INSS reports built from the statutory snapshot on each slip.
- Pre-flight validation before a declaration: missing NIF, missing social-security
  number, contribution without a base, an employee appearing twice in one period.
- A submission register that records period, totals, who generated it and when. Status
  stays `Generated` until somebody enters the portal's own receipt reference.
- **No machine submission format is implemented, because neither AGT nor INSS publishes
  one** — both are portal-entry processes. What is produced is a working file to key from
  and reconcile against, labelled as such.

### Workflow and segregation of duties
- Payroll states: Draft → Calculated → Pending Approval → Approved → Posted →
  Payment Ready → Paid → Closed, with Cancel reachable from every live state.
- Roles: Payroll Officer (prepares), Payroll Manager (approves), Payroll Finance Approver
  (posts, releases, exports). Created on install and **never auto-assigned**.
- Self-approval blocked by identity, not by role.
- Approved payroll is fingerprinted; changing a slip afterwards invalidates the approval.
- Period locking, payroll readiness pre-flight, exception management, audit trail.

### Contracts and the HR lifecycle
- Employment contracts with types, approval, overlap prevention, derived expiry and a
  renewal chain that never rewrites the previous contract's dates.
- Probation with a dated, attributed decision.
- Salary changes that close the old salary profile and open a new one on the effective
  date, atomically; a date mid-period is refused because payroll cannot split a period.
- Contract templates with versioning and PDF generation. **Placeholder substitution is
  not Jinja** — a fixed whitelist, no expressions, nothing executed.
- Bulk contract creation with a preview that names every skipped and blocked employee.
- Employee documents with expiry tracking and enforced confidentiality.
- Onboarding checklist, exit checklist, employee timeline, HR readiness.
- Transfers, promotions, leave and attendance reuse ERPNext.

### Employee Self-Service (`/ess`)
Profile, payslips with a plain-language statutory breakdown and PDF, leave requests with
a live balance preview, attendance calendar, documents, requests, salary advances, bank
change requests, performance reviews, attendance-justification upload and document
upload. Mobile-first; verified in a real browser at 375/390/430/1366/1920.

### Manager Self-Service (`/mss`)
Team directory, approval inbox, leave and attendance decisions, probation and renewal
recommendations, team calendar, performance reviews, delegation. **Compensation is absent
from every payload**, not hidden in the interface.

### Salary advances
Request → approve → disburse (posting DR advance / CR bank) → recover per payroll period
→ settle. One instalment per advance per run.

### Reporting
Payroll register, IRT, INSS, bank payment list, audit trail, statutory rate audit,
contract expiry, document expiry, salary change history, advance balance, master-data
completeness, headcount and turnover, absenteeism, org chart.

### Security
- Every action authorised through one enforcement table.
- Company isolation via standard Frappe user permissions.
- Self-service derives the employee from the session; there is no employee parameter.
- Record-level permission on payslips, documents and contracts, so a PDF or a private
  file is guarded by the framework rather than by a service.
- The Employee role holds `read`/`print` and deliberately **not** `report`/`export`.
- 96 live HTTP security probes pass, including IDOR attempts on payslips, appraisals,
  attendance occurrences and documents.

### Deployment
- `after_install` / `after_migrate` seeds are marker-guarded and idempotent.
- Install verification asserts every DocType, report, role, custom field, seed, portal
  route, scheduled job and self-service permission a fresh install must produce.
- A release gate that separates **software release**, **payroll run**, **employee data**
  and **security** blockers, so a customer's missing IBANs never look like a defect.
- Health check, production readiness, payroll reconciliation.

---

## Known Limitations

| Limitation | Impact | Why |
|---|---|---|
| **Clean installation never verified** | Blocks 1.0.0 | `bench new-site` needs a MariaDB account that can CREATE DATABASE; none is available on this bench. |
| No bank file format | Payment file is uploaded/keyed by a person | The company banks with BAI; BAI does not publish its bulk-payment layout. Inventing one would be worse than the spreadsheet. |
| No AGT/INSS machine submission | Declarations keyed into the portals | Neither authority publishes a format. |
| Severance and notice not calculated | Entered manually | Depends on the reason for termination and facts the app does not hold. Limits are cited as warnings against Lei n.º 12/23. |
| Statutory limits are warnings, not blocks | HR decides | The lawful ceiling depends on the legal ground for a fixed term, which the data model does not record. |
| Restore not tested | Backup verified, restore not | Restoring needs a target database — the same missing credential. |

## Upgrade Notes

- Additive only. Two consecutive `bench migrate` runs produce an identical database
  fingerprint across salary slips, profiles, IRT brackets, payroll entries, GL entries and
  role assignments.
- Run `bench --site <site> migrate`, then `bench restart`, then
  `bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.release_gate.report`.
