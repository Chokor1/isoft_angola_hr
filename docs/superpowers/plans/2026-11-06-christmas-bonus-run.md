# Christmas Bonus Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pay the Christmas bonus (Subsídio de Natal) as its own payroll run — bonus, IRT on the bonus alone, no INSS — separate from the December salary.

**Architecture:** A `run_type` (`Salary` / `Christmas Bonus`) on Isoft Payroll Entry and Isoft Salary Slip. A new pure module `payroll/christmas.py` calculates the bonus and returns the same dict shape as `engine.compute_slip`, so the slip, posting, bank file and receipt code read it unchanged. Every place that reasons about "the employee's slips for a period" is made run-type aware.

**Tech Stack:** Frappe/ERPNext v13, Python 3.8, Jinja print format, desk page JS (`page/angola_hr_dashboard`).

**Spec:** `docs/superpowers/specs/2026-11-06-christmas-bonus-run-design.md`

All paths below are relative to `apps/isoft_angola_hr/isoft_angola_hr/isoft_angola_hr/` unless they start with `apps/` or `docs/`.

## Global Constraints

- No edits to Frappe or ERPNext; everything in `isoft_angola_hr`.
- Database-writing tests run ONLY via the protected runner, from `~/frappe-bench/sites`:
  `redis-server --port 16399 --bind 127.0.0.1 --save '' --appendonly no --daemonize yes` then
  `../env/bin/python -m isoft_angola_hr.isoft_angola_hr.payroll.testing_guard dev.isoft.ao redis://127.0.0.1:16399 <dotted modules>` then `redis-cli -p 16399 shutdown nosave`. Never `bench run-tests`.
- Pure test modules (no DB writes) must be listed in `READ_ONLY` in `payroll/test_p49_test_isolation_guard.py`; every other test module starts with `from isoft_angola_hr.isoft_angola_hr.payroll.testing_guard import (  # noqa: F401\n\trequire_isolated_site as setUpModule)`.
- Run types, verbatim: `Salary`, `Christmas Bonus`. Existing rows are `Salary`.
- Bonus formula: `SN = base × natal_rate/100 × M/12`, rounded half-up to the cent; `IRT = engine.resolve_irt(SN, …)`; `net = SN − IRT`; INSS employee and employer = 0.
- Worked examples that must reproduce: base 400 000, M 12 → SN 200 000,00, IRT 20 500,00, net 179 500,00; M 8 → SN 133 333,33, IRT 0,00, net 133 333,33 (IRT Table "Tabela IRT 2026 - NDA" on dev).
- M = complete months of service in the bonus year, counted to 31 December (`engine.natal_months(joining, date(year,12,31))`).
- Bonus run period = the payroll cycle period that ends in December: `api._period_for_month("{year}-12")` (dev: 23 Nov – 22 Dec).
- Bonus slip name: `ISN-{employee}-{year}`.
- Every DocType JSON edit bumps its `modified`. Python edits on dev need `supervisorctl restart frappe-bench-web: frappe-bench-workers:` (check RQ idle first) before a browser check.
- Commit straight to `main`, message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push.
- Do not change `enable_natal` or any other Settings value on the live site; tests set what they need inside the protected run.

## Review Focus

1. **A December salary run created after the bonus run** — it must calculate exactly as before (no SNA, same INSS/IRT), not be refused as a duplicate. Test in Task 4.
2. **A second bonus run for the same year, or a bonus slip amended after cancel** — the second run is refused; cancel-and-amend of one bonus slip still works. Test in Task 4.
3. **An employee hired in the bonus year on the 1st vs the 2nd of a month** — 1 December gets M 1; 2 December gets M 0 and is listed as excluded, never a 0,00 slip. Test in Tasks 1 and 4.
4. **HR override above the full-year amount or without a reason** — refused with a message naming the employee. Test in Task 5.
5. **Final settlement after a bonus, with unpaid December overtime** — overtime still collected (bonus slip ignored for "paid up to"), Christmas bonus netted to ≥ 0. Test in Task 8.

---

### Task 1: Bonus calculation module

**Files:**
- Create: `payroll/christmas.py`
- Create: `payroll/test_p52_christmas_formula.py` (pure; add to `READ_ONLY` in `payroll/test_p49_test_isolation_guard.py` with reason `"pure bonus arithmetic and IRT table reads"`)

**Interfaces:**
- Produces:
  - `RUN_SALARY = "Salary"`, `RUN_CHRISTMAS = "Christmas Bonus"`
  - `bonus_months(joining_date, year: int) -> int` — `engine.natal_months(joining_date, date(year, 12, 31))`
  - `full_year_amount(base, rate) -> float` — `base × rate / 100`, 2 dp
  - `compute_bonus(base, rate, months, on_date, company=None, irt_table=None, override=None) -> dict` — keys exactly as `engine.compute_slip` returns (`earnings`, `deductions`, `gross_pay`, `total_deduction`, `net_pay`, `taxable_income`, all `ss_*` = 0, all `irt_*` trace, `employer_cost` = gross) plus `natal_months`, `natal_base`, `natal_amount`. Earnings: one `SNA` row; deductions: one `IRT` row when non-zero. `override` (an amount) replaces the computed SN.
  - `bonus_period(year: int) -> (date, date)` — wraps `api._period_for_month("{year}-12")`.

- [ ] **Step 1: Write the failing tests** in `test_p52_christmas_formula.py`, class `TestTheDocumentExamples` and `TestMonths`:
  - `test_twelve_months`: `compute_bonus(400000, 50, 12, "2026-12-15")` → `natal_amount == 200000.0`, `irt_amount == 20500.0`, `net_pay == 179500.0`, `ss_employee_amount == 0`, `ss_employer_amount == 0`, earnings abbrs `["SNA"]`.
  - `test_eight_months`: same with 8 → `133333.33`, IRT `0.0`, net `133333.33`, deductions `[]`.
  - `test_override_replaces_the_amount`: override 150000 → `natal_amount == 150000.0`, IRT from the table for 150000.
  - `TestMonths`: joined `2020-03-10` → 12; `2026-01-01` → 12; `2026-05-15` → 7; `2026-12-01` → 1; `2026-12-02` → 0; `2027-01-05` → 0 (year 2026 in every case).
- [ ] **Step 2: Run, expect ImportError** — protected runner with `isoft_angola_hr.isoft_angola_hr.payroll.test_p52_christmas_formula` (or plain unittest under `frappe.init/connect`, it is read-only).
- [ ] **Step 3: Implement `payroll/christmas.py`** as specified; IRT through `engine.resolve_irt(sn, company=company, on_date=on_date, table=irt_table)`; `SNA` row built with `engine.COMPONENTS["SNA"]["name"]`.
- [ ] **Step 4: Run, expect PASS** (and `test_p49` still OK).
- [ ] **Step 5: Commit** `Christmas bonus: calculation module`.

### Task 2: Data model

**Files:**
- Modify: `doctype/isoft_payroll_entry/isoft_payroll_entry.json` — add `run_type` Select `Salary\nChristmas Bonus`, default `Salary`, `set_only_once` 1, in list view, placed right after `company`.
- Modify: `doctype/isoft_salary_slip/isoft_salary_slip.json` — add `run_type` (same, read_only 1, in standard filter), `natal_months` Int read_only, `natal_base` Currency read_only, `natal_override_reason` Small Text.
- Modify: `install.py` — `backfill_run_type()` (`update ... set run_type='Salary' where ifnull(run_type,'')=''` on both tables), called from `after_install` after `setup_custom_fields()`.
- Test: `payroll/test_p53_christmas_run.py` (guarded; created here, grows in later tasks)

**Interfaces:**
- Produces: fields `run_type`, `natal_months`, `natal_base`, `natal_override_reason`; `install.backfill_run_type()`.

- [ ] **Step 1: Failing test** `TestDataModel.test_existing_runs_read_as_salary`: after `install.backfill_run_type()`, `frappe.db.count("Isoft Payroll Entry", {"run_type": ("in", ("", None))}) == 0` and same for slips; `frappe.get_meta("Isoft Payroll Entry").get_field("run_type").options.split("\n") == ["Salary", "Christmas Bonus"]`.
- [ ] **Step 2: Run, expect FAIL** (no field).
- [ ] **Step 3: Edit JSONs (bump `modified`), add `backfill_run_type`, run `bench --site dev.isoft.ao migrate`.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `Christmas bonus: run type on payroll runs and slips`.

### Task 3: Bonus slip

**Files:**
- Modify: `doctype/isoft_salary_slip/isoft_salary_slip.py`
- Test: `payroll/test_p53_christmas_run.py`

**Interfaces:**
- Consumes: Task 1 `christmas.*`; Task 2 fields.
- Produces: `IsoftSalarySlip.autoname()`; `compute()` branches on `run_type`; `validate_no_duplicate_payroll()` run-type aware; `christmas.paid_in_year(employee, year) -> float` (sum of `subsidio_natal` on submitted bonus slips whose `end_date` year = year) added to `payroll/christmas.py`.

- [ ] **Step 1: Failing tests** in `TestBonusSlip` (fixture: reuse `PayrollFixture` from `test_p0_integration`; base 400000, joined in a prior year, `enable_natal` = 1 set inside the test):
  - `test_bonus_slip_matches_the_document`: a draft slip with `run_type="Christmas Bonus"` and the bonus period → name `ISN-{employee}-2026`, `subsidio_natal 200000`, `irt_amount 20500`, `net_pay 179500`, `ss_employee_amount 0`, `natal_months 12`, `natal_base 400000`.
  - `test_bonus_and_december_salary_coexist`: a salary slip and a bonus slip for the same employee and period both submit.
  - `test_two_bonus_slips_in_a_year_are_refused`: second submitted bonus slip → `frappe.ValidationError`.
  - `test_salary_slip_no_longer_adds_the_bonus`: a new December salary slip with `enable_natal` = 1 → `subsidio_natal == 0`.
  - `test_salary_slip_refuses_sna_after_a_bonus`: bonus submitted, then a salary slip with `subsidio_natal=1000` → `ValidationError` naming the bonus slip.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `autoname`: bonus → `ISN-{employee}-{end_date.year}`, salary → current format. `compute()`: bonus branch reads profile base + `settings.natal_rate`, `christmas.bonus_months(date_of_joining, end_date.year)`, override = `subsidio_natal` only when `natal_override_reason` is set, then copies the dict exactly as the salary branch does; skips `set_working_days`, absences, overtime and advance recovery. Remove the December `default_natal` auto-fill. Duplicate check: salary slips compare against salary slips only (current SQL + `and ifnull(run_type,'Salary')='Salary'`); bonus slips against submitted bonus slips of the same year. `validate_enabled_components`: a bonus slip requires SNA enabled; a salary slip with SNA > 0 and `christmas.paid_in_year > 0` throws.
- [ ] **Step 4: Run Task 3 tests + `test_p42_payslip_receipt`, `test_p50_absences`, `test_p48_overtime`; expect all PASS.**
- [ ] **Step 5: Commit** `Christmas bonus: bonus salary slips`.

### Task 4: Bonus payroll run

**Files:**
- Modify: `doctype/isoft_payroll_entry/isoft_payroll_entry.py`
- Test: `payroll/test_p53_christmas_run.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: run-type aware `validate_no_duplicate_run`, `fill_employees() -> int` (bonus: Active + Suspended, M ≥ 1, no settlement that paid Natal this year; excluded ones in the existing "Employees Excluded" message with the reason), `create_salary_slips` passes `run_type` and finds existing slips by `run_type`.

- [ ] **Step 1: Failing tests** `TestBonusRun`:
  - `test_full_lifecycle`: create bonus run → fill → calculate → submit for approval (user A) → approve (user B) → post and pay (Finance user), mirroring the existing salary lifecycle test in `test_p0_integration`; journal entry has no INSS account lines; total = SN.
  - `test_second_bonus_run_same_year_refused`.
  - `test_salary_run_for_december_still_allowed` and its slip totals equal a slip computed before this change (compare with `engine.compute_slip` on the same inputs).
  - `test_december_second_hire_is_excluded_first_is_included`: joined 2026-12-02 not in `employees`; joined 2026-12-01 included with `natal_months 1`.
  - `test_cancel_and_amend_one_bonus_slip` works.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Bonus duplicate rule: same company + `run_type` + `year(end_date)` + group, not Cancelled. `approval_summary()` lists overridden employees with their reason for a bonus run.
- [ ] **Step 4: Run Task 4 tests + existing workflow modules (`test_p0_integration`, every `test_p3*`/`test_p4*` payroll module); expect PASS.**
- [ ] **Step 5: Commit** `Christmas bonus: payroll run`.

### Task 5: Console — preview and creation

**Files:**
- Modify: `api.py` — `payroll_preview(..., run_type="Salary", year=None)`, `create_payroll_from_preview(..., run_type="Salary", year=None)`; new `christmas_preview_rows(company, year, filters) -> list` in `payroll/christmas.py` (rows: `employee`, `employee_name`, `base`, `months`, `christmas`, `irt`, `net`, `suspended`, `override_reason`, `blocked`, `exception`).
- Modify: `page/angola_hr_dashboard/angola_hr_dashboard.js` — run-type select beside the month picker; Christmas Bonus shows a year and a payment-date field, hides the monthly inputs and preview columns, shows Base / Months / Christmas Bonus (editable) / Reason / IRT / Net; a "Suspended" badge on flagged rows.
- Modify: `translations/pt.csv` — every new label (e.g. `"Christmas Bonus","Subsídio de Natal"`, `"Run Type","Tipo de Processamento"`, `"Salary","Salário"`, `"Payment Date","Data de Pagamento"`).
- Test: `payroll/test_p53_christmas_run.py`

- [ ] **Step 1: Failing tests** `TestConsole`: preview for 2026 returns the fixture employee with `christmas 200000`, `irt 20500`, `net 179500`; `create_payroll_from_preview(run_type="Christmas Bonus", year=2026, rows=…)` creates a run with `run_type` set and period = `christmas.bonus_period(2026)`; an override row without reason → `ValidationError`; override above `full_year_amount` → `ValidationError`.
- [ ] **Step 2–4: Fail, implement, pass.** Salary path unchanged except the Natal column's default (`natal_default`) becomes 0: existing preview tests stay green after updating any that expected a December default.
- [ ] **Step 5: Browser check** on dev (restart web/workers first): Payroll → Christmas Bonus → preview → figures match; Salary preview unchanged. Screenshot.
- [ ] **Step 6: Commit** `Christmas bonus: console preview and creation`.

### Task 6: Posting and bank file

**Files:**
- Modify: `api.py::_salary_reference(end_date, run_type="Salary")` — bonus → `Natal_{year}`; callers in `services/bank_export.py` pass `entry.run_type`.
- Test: `payroll/test_p53_christmas_run.py`

- [ ] **Step 1: Failing tests** `TestPostingAndBank`: accrual JE of a posted bonus run debits the SNA expense account by SN, credits IRT payable by IRT and net payable by net, and has no `CTSS*` line; `bank_export.generate` reference `Natal_2026`.
- [ ] **Step 2–4: Fail, implement, pass.** If `services/payroll_accounts.accrual_problems` demands accounts for components absent on bonus slips, scope it with `components_on(slips)` (already exists).
- [ ] **Step 5: Commit** `Christmas bonus: posting and bank reference`.

### Task 7: Receipt

**Files:**
- Modify: `payroll/recibo.py` — context gains `run_type`; `_period_label` returns the year for a bonus; the SNA earning label is `"Christmas Bonus - {0}/12"` (context "Recibo de Vencimento") when M < 12.
- Modify: `print_format/recibo_de_vencimento/recibo_de_vencimento.html` — title `t("Christmas Bonus Receipt")` for a bonus; Summary hides Salary Advance and Employee INSS rows for a bonus. Publish per the receipt procedure (check DB `modified`, save DB record, sync JSON html + `modified`).
- Modify: `translations/pt.csv` — `"Christmas Bonus Receipt","Recibo de Subsídio de Natal","Recibo de Vencimento"`, `"Christmas Bonus - {0}/12","Subsídio de Natal - {0}/12","Recibo de Vencimento"`.
- Test: `payroll/test_p42_payslip_receipt.py`

- [ ] **Step 1: Failing tests** `test_the_bonus_receipt`: PT text contains `Recibo de Subsídio de Natal`, `2026`, `200 000,00`, `20 500,00`, `179 500,00`, not `Segurança Social`; EN contains `Christmas Bonus Receipt`; an 8-month slip shows `Subsídio de Natal - 8/12`; a salary receipt's text is byte-identical to before (snapshot taken in the test from the same slip rendered with the old template file).
- [ ] **Step 2–4: Fail, implement, pass;** render PT/EN PDFs with the scratchpad harness and look at them.
- [ ] **Step 5: Commit** `Christmas bonus: receipt`.

### Task 8: Final settlement

**Files:**
- Modify: `payroll/settlement.py` — `_supplement_lines` reads `inp["christmas_bonus_paid"]` and adds a negative line `"Christmas bonus already paid ({0})"` capped so the Christmas bonus total never goes below 0; `collect_unpaid_overtime` counts salary slips only.
- Modify: `doctype/isoft_final_settlement/isoft_final_settlement.py` — pass `christmas_bonus_paid = christmas.paid_in_year(employee, termination_date.year)` and the bonus slip names.
- Test: `payroll/test_p53_christmas_run.py`

- [ ] **Step 1: Failing tests** `TestSettlementAfterBonus`: full bonus paid, termination 2026-12-20 → Christmas bonus net 0, no negative total; paid 133 333,33 against a settlement amount of 150 000 → pays 16 666,67; unpaid overtime dated 2026-12-01..19 is still collected.
- [ ] **Step 2–4: Fail, implement, pass** (+ existing settlement test modules green).
- [ ] **Step 5: Commit** `Christmas bonus: settlement deducts the bonus already paid`.

### Task 9: Reports, IRT/INSS working files, readiness

**Files:**
- Modify: `report/report_utils.py` — `slip_conditions` honours `filters["run_type"]`; `PERIOD_FILTERS` gains a `run_type` Select (`\nSalary\nChristmas Bonus`).
- Modify: `report/payroll_register`, `report/irt_payroll_report`, `report/payroll_bank_payment_list`, `report/payroll_audit_trail` — add a Run Type column.
- Modify: `report/inss_contribution_report/inss_contribution_report.py` — salary slips only.
- Modify: `services/statutory_filing.py` — `_slips` returns salary slips; IRT rows get `natal_amount` and `natal_irt` from the employee's bonus slip in the period (an employee with only a bonus gets a row with salary columns 0); IRT `_columns` adds `("natal_amount", _("Subsídio de Natal"))`, `("natal_irt", _("IRT Subsídio de Natal"))` before `net_pay`; IRT totals add `natal_irt`; INSS unchanged (salary only); STF-005 counts salary slips only.
- Modify: `services/employee_self_service.py` — the payslip list labels bonus slips "Christmas Bonus {year}".
- Modify: `services/payroll_readiness.py` — the `enable_natal` check on a salary period becomes: "Christmas bonus is paid by its own run" info, and on a bonus run it requires `enable_natal`.
- Test: `payroll/test_p53_christmas_run.py`

- [ ] **Step 1: Failing tests** `TestReportsAndFilings`: December IRT working file has one row for the employee with `natal_amount 200000`, `natal_irt 20500`, and the total IRT = salary IRT + 20500; validation passes (no STF-005); INSS working file total excludes the bonus; payroll register filtered `run_type=Christmas Bonus` returns only bonus slips.
- [ ] **Step 2–4: Fail, implement, pass** (+ existing report/filing tests green).
- [ ] **Step 5: Commit** `Christmas bonus: reports and statutory working files`.

### Task 10: Documentation and full regression

**Files:**
- Modify: `apps/isoft_angola_hr/docs/PAYROLL_RUNBOOK.md` — "Christmas bonus run" section: when, who, the formula, the two examples, enabling `enable_natal`.

- [ ] **Step 1: Write the section.**
- [ ] **Step 2: Run every `payroll/test_*.py` module through the protected runner;** expect `RESULT OK`, 0 failures, 0 errors.
- [ ] **Step 3: Restart web/workers on dev; browser-run a full bonus run end to end as HR (prepare), a second HR Manager (approve) and Finance (post), on a throwaway company or inside a test-only session, then cancel it.** Do not leave a bonus run on ITEC's real company.
- [ ] **Step 4: Commit** `Christmas bonus: runbook`.
