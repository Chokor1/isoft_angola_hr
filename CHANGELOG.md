# Isoft Angola HR — Release Notes

## Payslip — HR review (10 September 2026)

The HR manager reviewed a printed "Recibo de Vencimento" and marked what to remove. Gone
from both copies: the company logo; the company phone / e-mail / website line; *Tipo de Contrato*; *Data de
Admissão*; the *Período de Processamento* row of the employee block (the period is in the
header and in the processing box); the itemised **Descontos** table — employee INSS, IRT
and *Total de Descontos* remain in the summary; the *Salário Bruto* line of the summary
(it is *Total de Remunerações*); and the *Data* field beside the employee's signature.
Nothing about the figures changed. Test added: `test_the_elements_hr_asked_to_remove_are_not_printed`.

## Final production readiness — Finance checklist, mapping governance, approval gate (September 2026)

Closes the payroll audit: the four findings (F-01 … F-04) are in place; this release turns
them into one answer — **READY** or **BLOCKED**, with the exact reasons — and stops a
payroll before approval rather than at posting.

* **Finance approval checklist** (`payroll_accounts.finance_checklist`, nine items): mapping
  complete · employee INSS liability · employer INSS expense (and payable) · salary advance
  · payroll payable · bank · no mapping drift against the previous payroll · all salary
  components mapped · no blocking readiness exceptions. Shown at the top of the Payroll
  screen, inside every run before approval ("Approval check"), and in Production
  Readiness. Endpoint `finance_approval_checklist`.
* **Approval gate** (`services/payroll_approval.py`): submitting for approval and approving
  are refused, with every reason at once, while any account the run posts to is missing or
  unusable, a salary component is unmapped, a readiness exception blocks an employee of the
  run, an employee has no (or an ambiguous / zero-base) salary profile, a slip is orphaned,
  cancelled, duplicated or covered by another slip, or a journal requirement (payable,
  cost centre, currency, fiscal year) is missing. Codes APR-001 … APR-008.
* **Account mapping governance**: every change to a payroll account mapping is recorded in
  the new **Isoft Payroll Account Change** (changed by, changed on, old account, new
  account, whether a payroll had already been posted). A change after posting is announced
  — *This account mapping was changed after payroll posting. Previous payroll vouchers
  remain unchanged. New payroll will use the new mapping.* — and blocks new approvals until
  Finance confirms the current mapping on record (`confirm_account_mapping`, Settings →
  Account mapping history). History is never rewritten; the log itself cannot be edited or
  deleted.
* **Bank payment validation**: the file is refused unless the run carries an approval and
  a release, every submitted slip has its accrual in the ledger, the payable booked equals
  the payroll total, every employee has a valid IBAN, nobody is already paid and no earlier
  file was submitted to or executed by the bank (BNK-010 … BNK-015).
* **Business language for HR**: *Employer Social Security expense account is not
  configured. Please contact Finance.* — with the technical detail (component, required
  nature, where to configure) underneath for Finance. Approve / Submit for Approval are
  inert while the approval check is BLOCKED; the reasons are on screen.
* Tests: `test_p46_final_readiness` — missing account blocks posting; mapping drift warning;
  historical vouchers unchanged; approval refused with missing configuration; manager
  approves and Finance posts and pays when correct; HR cannot bypass Finance controls by
  any channel; bank file requires approved, posted, released payroll and refuses a second
  payment; a paid payroll is immutable.

## Payroll account mapping integrity — audit finding F-04 (September 2026)

Two components (Salary Advance, Employer INSS cost) were unmapped on the ITEC site and the
employee-INSS account had been re-pointed after July was posted. Nothing chose an account
for Finance; the system now protects the ledger until Finance does.

* **Posting is checked before any voucher exists** (`services/payroll_accounts.py`).
  Every component that will produce a line on the slips being posted must have an
  account that exists, is a leaf, is enabled and not frozen, belongs to the company, is
  in the payroll currency and has the right nature (earnings Expense; deductions Liability
  or Asset; employer cost Expense; employer payable Liability; payable Liability; bank
  Asset or Liability). A bulk run is checked as a whole first, so a missing account can
  no longer post half a payroll. The refusal names the component, the required nature
  and where it is configured. Payment is checked the same way (payable + bank).
* **Calculation is never blocked** by the mapping — HR keeps working; submission for
  approval and posting are.
* **Readiness says what to do**: *BLOCKING — Missing payroll account mapping: Salary
  Advance — Adiantamento (ADT) — Missing — required account type: Liability or Asset —
  configure in Isoft HR Settings → Account per Component.*
* **Finance checklist** in Production Readiness: component accounts, employer INSS (cost
  and payable), advance, payroll payable, bank — plus whether the mapping still matches
  the last posted payroll. Documented in docs/FINANCE_GUIDE.md.
* **History is protected**: no Journal Entry, payroll or slip is rewritten. A mapping
  change after posting is reported by the reconciliation as *mapping drift*, with the
  accounts the vouchers actually hold.
* Tests: `test_p45_payroll_accounts` — missing ADT, missing CTSSE, missing payable/bank,
  wrong nature (with Settings bypassed), group/disabled accounts, fully mapped posting
  with sides and balance verified, no partial bulk posting, historical protection and
  drift, readiness wording, checklist.

## HR / Finance segregation — audit finding F-03 (September 2026)

Every ITEC HR user also held Accounts Manager, so the role table said Finance had posted,
released and paid the July payroll while the same HR user had done all of it.

* **A capability, not a role name, decides the finance actions.** `permissions.can()` now
  asks two questions for post, bank file and payment: does the user hold a finance role,
  and are they Finance for payroll purposes (`is_payroll_finance`)? Payroll Finance
  Approver always is. Accounts Manager is — unless the same user also holds HR User,
  HR Manager, Payroll Officer or Payroll Manager, in which case the accounting role was
  granted for something else and does not make them payroll Finance.
* **The preparer never posts or pays.** Whoever calculated or submitted a run cannot
  post it, submit its slips, release it, generate its bank file, record the bank's answer
  or confirm its payment — whatever roles they hold, Administrator included. The approver
  still cannot pay (existing rule).
* **Three visible switches** in Isoft HR Settings → Payroll Controls: *Allow HR
  self-approval* (default OFF), *Payroll posting requires Finance* (default ON), *Payroll
  payment requires Finance* (default ON). An absent switch reads as its safe default.
* **Buttons follow the server.** Post, Submit Slips, Make Payment and Export Bank File are
  rendered from `finance_flags` (role, Finance rule, company, preparer, state), so an HR
  Manager sees Calculate and Submit for Approval and nothing of Finance's.
* Production readiness gains *Finance users who may post and pay* (BLOCKED when every
  Accounts Manager is neutralised by an HR role).
* Tests: `test_p44_finance_segregation` — the complete matrix for HR Manager, Payroll
  Officer, Payroll Manager, Finance Approver, Accounts Manager, an HR Manager who is
  handed Accounts Manager, an unauthorised user and Administrator, through the API,
  `frappe.client` and direct document actions.

## Workflow integrity — audit findings F-01 and F-02 (September 2026)

* **The payroll lifecycle can no longer be edited outside the workflow.** The state, every
  audit stamp and the approval snapshot of an Isoft Payroll Entry were read-only in the
  form only: `frappe.client.set_value`, `frappe.client.save`, the REST resource API and any
  hand-written `doc.save()` accepted them from whoever held *write* on the DocType, and one
  such call marked a payroll Paid — or Approved with a forged approver — with no approval
  and no ledger entry. `payroll_workflow.assert_lifecycle_untouched` now refuses every
  such save, Administrator included; only `perform()` and `refresh_payment_state()` write
  those fields, inside `payroll_workflow.transition()`. A new run may be born Draft with
  its creator as preparer, and nothing else.
* **A salary slip exists only inside its payroll run.** A slip with no Payroll Entry had no
  approval to check, so it could be created in the Desk, submitted by an HR Manager and
  posted and paid by Finance. Slips now require their run from creation, must match its
  company and period, can never be re-pointed, and can be submitted, posted or paid only
  when the run is approved **and lists the slip** in its employee table.
* Tests: `test_p43_workflow_integrity` (both findings, every channel, every role);
  `test_p41`'s status-laundering test now performs the write instead of reading a docstring;
  the shared `PayrollFixture` creates test slips inside a real run.

## Payslip redesigned — "Recibo de Vencimento" / "Payslip" (September 2026)

The printed salary receipt is now a professional A4 document, printed twice per slip —
**ORIGINAL** for the employee and **DUPLICADO - ARQUIVO EMPRESA** for the company file —
in the language of the person printing it. See **docs/PAYSLIP_RECEIPT.md**.

* **One format, wired everywhere.** "Recibo de Vencimento" is now the DocType's default
  print format, so the Desk *Print* button no longer opens the raw Standard field dump. The
  ESS PDF link, the HR dashboard action and the release gate already pointed at it.
* **Complete.** Company NIF, address and logo; employee ID, NIF, social-security number,
  category, designation, department, employment type, admission date, payroll period and
  reference; paid / total / absence / leave days and approved overtime hours; earnings and
  deductions with codes, read from the slip's own rows; gross, taxable income, employee
  INSS, IRT, total deductions and **TOTAL A RECEBER**; payment method, date, currency,
  bank and IBAN; declaration, signature and date lines.
* **Follows the reader's language, at render time.** A Portuguese user gets *Recibo de
  Vencimento / Funcionário / Salário Base / Descontos / Assinatura do Trabalhador*; an
  English user gets *Payslip / Employee / Basic Salary / Deductions / Employee Signature*.
  Names, identifiers, codes and amounts are never translated. Labels carry their own
  translation context because, with ERPNext listed after this app in `apps.txt`, its
  Brazilian catalogue silently replaced the Angolan wording for shared English words
  ("Folha de Pagamento", "Rem. Líquida").
* **Payroll untouched.** Every figure is copied from the fields the engine stored;
  `payroll/recibo.py` only reads. Absence days is the same subtraction the HR explanation
  panel shows, and leave days now come from one shared helper for both.
* **Fits.** Stress-tested with a 45-character name, long department and designation,
  seven-figure amounts, every optional earning, five deductions and informative rows: still
  one page per copy. Frappe's own `.print-format` padding, which wkhtmltopdf applied at
  0.2 in top and bottom, is zeroed for print — it used to push a blank third page out.
* **Template in files.** `recibo_de_vencimento.html` / `.css` next to the Print Format
  fixture; `install.py` copies them into the record on every migrate instead of carrying
  the HTML as a Python constant.
* **Tests.** `test_p42_payslip_receipt` — content, both languages, two A4 pages, stress
  layout, installation and record-level security (an employee cannot open, download or
  list another employee's payslip). The existing `test_p8_recibo` fixture now uses the
  previous calendar month: it marked Attendance on future dates and failed on the first
  days of every month.

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
