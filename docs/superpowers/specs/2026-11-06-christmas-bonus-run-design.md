# Christmas Bonus Run (Subsídio de Natal) — design

Date: 2026-11-06 · App: `isoft_angola_hr` · Source: HR document "Natal.pdf" (November 2026)

## Intent

HR pays the Christmas bonus in the **first fortnight of December**, before the December
salary is processed. Today the app can only pay it as a line (SNA) on the December salary
slip, so it is calculated, approved and paid together with the salary. HR needs it as its
own payroll run with its own formula, paid on its own date.

## The rule (HR document, confirmed)

- 12 or more months of service: `SN = Basic Salary × 50%`
- Fewer than 12 months: `SN = ((Basic Salary × 50%) / 12) × M`, M = months worked
- `IRT = FP + (SN − EXC) × %` from the IRT table, on the bonus **alone**
- `T = SN − IRT`. **No INSS**, neither the employee 3% nor the employer 8% (confirmed by
  the user, 2026-11-06).

Worked examples that must reproduce exactly:

| SB | M | SN | IRT | T |
|---|---|---|---|---|
| 400 000,00 | 12 | 200 000,00 | 20 500,00 | 179 500,00 |
| 400 000,00 | 8 | 133 333,33 | 0,00 | 133 333,33 |

## Goals

- A payroll run of type **Christmas Bonus**, chosen when the run is created, next to the
  existing **Salary** type.
- One bonus slip per employee: earning SNA = SN; deduction IRT; net = SN − IRT.
- The same lifecycle and controls as a salary run: Draft → Calculated → submitted for
  approval → Approved (by a second person) → Posted by Finance → payment ready / bank
  file → Paid → Closed; cancel and correction as today.
- A printed receipt for the bonus.
- The December salary slip no longer carries the Christmas bonus.

## Non-goals

- No change to how salary runs calculate.
- No change to the Christmas bonus inside a final settlement (termination); it keeps its
  own rule.
- No change to vacation allowance (SFE).
- No retroactive change to slips already submitted.

## Anti-goals (would be a failure even if it "works")

- A salary run whose figures change in any way because of this work.
- An employee paid the Christmas bonus twice in a year (bonus run + December salary, or
  two bonus runs).
- INSS appearing anywhere on a bonus slip, its ledger entry or the INSS report.
- A bonus run that bypasses the separate-approval or Finance-posting rules.

## Constraints

- Frappe/ERPNext v13; no edits to Frappe or ERPNext; everything inside this app.
- Database-writing tests run only through the protected runner
  (`payroll/testing_guard.py`) on dev.isoft.ao.
- dev.isoft.ao holds ITEC's real data; production is updated by the user (git pull +
  migrate), so schema changes ship as DocType JSON plus idempotent install code.
- Existing records: every run and slip created before this change is a Salary run/slip.

## Approaches considered

- **A. Run type on the existing Isoft Payroll Entry / Isoft Salary Slip — chosen by the
  user.** Reuses approval, posting, bank file, receipt and reports; costs careful changes
  to the duplicate controls, slip naming, reports and receipt, plus regression tests so
  salary runs stay identical.
- B. Separate Christmas Bonus doctypes. Salary payroll untouched, but approval, posting,
  bank file, receipt and reports would all be duplicated. Rejected.
- C. Keep SNA on the December slip and only tax it separately. Still paid with the
  salary, which is what HR wants to stop. Rejected.

## Design (decided)

### Data

- `Isoft Payroll Entry.run_type` — Select `Salary` / `Christmas Bonus`, default `Salary`,
  set on creation and read-only afterwards. Existing rows read as `Salary`.
- `Isoft Salary Slip.run_type` — same options, copied from the run, read-only.
- Bonus slip fields used: `subsidio_natal` (SN), `irt_*` trace, `gross_pay`, `net_pay`,
  plus new `natal_months` (Int, M) and `natal_base` (Currency, the basic salary used).
  INSS fields are written as 0.

### Calculation (bonus run)

- Basic salary = `Isoft Salary Profile.base` of the profile active on the run's end date
  (contractual; no absence deductions, no allowances).
- Rate = `Isoft HR Settings.natal_rate` (50 on every site; the document's 50%).
- M = `engine.natal_months(date_of_joining, end of the year)`: complete months of service
  in the payment year, 12 when hired in a previous year. Same definition the app already
  uses for Natal and for final settlements. The reference date is **31 December** of the
  bonus year even though payment is in the first fortnight (the bonus is the year's).
- SN = base × rate/100 × M/12, rounded to the cent; 12 months gives base × rate/100.
- IRT = existing `engine.resolve_irt(SN, …)` with the IRT table in force on the run's
  posting date. Nothing else is taxable on this slip; no food/transport exemption logic.
- Net = SN − IRT. No advance recovery and no manual adiantamento on a bonus slip.
- A new pure function `engine.compute_christmas_bonus(...)` returns the same dict shape as
  `compute_slip` (earnings, deductions, totals, IRT trace, ss fields = 0) so the slip,
  posting and receipt code read it unchanged.

### Who is included

- Employees Active **or Suspended** on the run's end date, of the run's company (and
  branch/department filters, as a salary run), with a resolvable Salary Profile and M ≥ 1.
  Suspended employees are flagged in the preview so HR adjusts them (builder's proposal:
  the app records no suspension dates, so it cannot prorate them itself).
- Excluded, and listed in the "Employees Excluded" message like today: M = 0 (hired on or
  after 2 December — a 1 December hire has one complete month and gets 1/12, per
  `law.complete_months`), or a submitted Final Settlement in the bonus year that already
  paid the Christmas bonus.

### Per-employee adjustment (builder's proposal)

- In the preview HR may override SN for one employee, as the Natal amount is editable
  today. An override requires a reason, is stored on the slip (`natal_override_reason`),
  shown in the approval summary, and may not exceed the full-year amount (base × rate).
  M itself is not editable; it is shown for reference.

### Period, naming and duplicate controls

- Bonus run dates follow the **payroll cycle**, not the calendar: start and end are the
  cycle period that ends in December (on ITEC, cycle day 23: 23 November – 22 December),
  i.e. the same dates as the December salary run. This puts the bonus in December for the
  reports (filtered by end date), the monthly filings (slips inside the period) and the
  bank-file reference (built from the end date). Posting date = the payment date HR enters
  (first fortnight of December by default, editable); it does not move the period.
- M is still counted to **31 December** of the year (the bonus is the year's), not to the
  period end.
- Slip name: salary slips keep `ISS-{employee}-{start_date}`; bonus slips are named
  `ISN-{employee}-{year}` (set in the class's `autoname`), since they share the December
  salary slip's dates.
- Every place that reads "the employee's latest slip" or "slips overlapping a period" must
  say which run type it means. Known cases: `settlement.collect_unpaid_overtime` (salary
  slips only, or a bonus slip would erase unpaid December overtime), the employee
  self-service payslip list (both, labelled), advances' recovery (salary only).
- Run duplicate check: one live Salary run per company + period + group (unchanged); one
  live Christmas Bonus run per company + year + group.
- Slip duplicate check: salary slips check overlap with other **salary** slips only
  (unchanged otherwise); a bonus slip checks for another submitted bonus slip of the same
  employee and year.

### December salary run

- The salary slip stops auto-filling SNA (`default_natal`) and the preview's Natal
  column defaults to 0. If HR types an SNA amount on a salary slip in a year where the
  employee has a submitted bonus slip, the slip refuses to save ("already paid by
  Christmas Bonus run X").

### Workflow, posting, payment

- The run uses `services/payroll_workflow.py` unchanged: same states, same actors, same
  separate-approval and Finance-posting rules.
- Accrual Journal Entry: debit the SNA component's mapped expense account, credit IRT
  payable and net salaries payable, using the existing account mapping. No INSS lines.
  Posting date = the run's posting date.
- Bank file and payment confirmation: unchanged code, scoped to the run as today.

### Receipt

- Same "Recibo de Vencimento" print format; when `run_type` is Christmas Bonus the title
  reads "Recibo de Subsídio de Natal" / "Christmas Bonus Receipt", the period reads the
  year, the earnings table shows the single SNA line (label "Subsídio de Natal - M/12"
  when M < 12), and the Summary shows IRT and net (INSS line shows 0,00 or is hidden —
  builder's call, consistent across PT/EN).

### HR console

- The payroll screen's "new payroll" gets a run-type choice. Choosing Christmas Bonus
  asks for the year and payment date, and the preview shows per employee: base, M, SN,
  IRT, net. Monthly inputs (overtime, advances, absences) are not shown for a bonus run.

### Reports

- Payroll Register, IRT report, bank payment list, audit trail: include bonus runs and
  show the run type as a column / filter.
- IRT report and the monthly IRT working file (Mapa de Remunerações): **one row per
  employee, with the bonus in its own columns** (decided by the user, 2026-11-06): the
  employee's December salary figures as today, plus "Subsídio de Natal" (SN) and "IRT
  Subsídio de Natal" columns. December's IRT total = salary IRT + bonus IRT. The
  "employee appears twice" check (STF-005) counts salary slips only; the bonus slip is
  folded into the employee's row. An employee with a bonus slip and no December salary
  slip still gets a row, salary columns at zero.
- INSS contribution report and filing: bonus runs excluded (they carry no INSS).
- Payroll readiness checks that reason about Natal on the December salary run are updated
  to point at the bonus run instead.

### Settings

- `enable_natal` must be on to create a bonus run. `natal_payment_month` no longer drives
  any salary-slip amount; it is kept for display only.

### Leavers after the bonus (decided by the user, 2026-11-06)

- A final settlement in the same year **deducts the Christmas bonus already paid** by a
  submitted bonus slip: settlement Christmas bonus = max(0, settlement's own amount − SN
  already paid). No claw-back when the bonus run paid more. The settlement shows the
  deduction as its own line with the bonus slip's name, so the figure is explainable.

## Testing

- Pure tests: the two worked examples; M boundaries (hired 1 Jan, 15 May, 1 Dec, previous
  year); IRT bracket edges.
- Settlement after a bonus: full bonus paid, leaver on 20 December → settlement Christmas
  bonus 0, no negative line; partial bonus paid → settlement pays the difference.
- Protected-runner tests: create, calculate, approve (second user), post and pay a bonus
  run; duplicates refused (two bonus runs, two bonus slips, SNA on a December salary slip
  after a bonus); a salary run for the same December still works and is unchanged; the
  receipt renders PT/EN; INSS report excludes the bonus; IRT report adds it to December.
- Regression: the full existing receipt, absence, overtime and workflow suites stay green.

## Left to the builder

- Field placement and labels on the forms; translation strings (PT/EN).
- Whether the bonus receipt hides or zeroes the INSS line.
- Internal structure of the console change.
- Patch vs install code for back-filling `run_type = 'Salary'` on existing rows.
