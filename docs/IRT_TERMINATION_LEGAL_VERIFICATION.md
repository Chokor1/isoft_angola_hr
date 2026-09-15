# IRT on a Termination Settlement — Legal Verification Note

**Status: LEGAL VERIFICATION REQUIRED — not confirmed against Angolan tax law.**

This note documents exactly what the application currently computes so that an Angolan
tax or legal specialist can confirm or correct it. It describes behaviour; it does not
assert that the behaviour is correct.

The calculation described here **has not been changed** and must not be changed on the
strength of an assumption. It is surfaced to the user on every settlement as flag
`IRT-LUMP`, and the application continues to display:

> "IRT treatment for termination settlement requires legal confirmation."

Implementation: `isoft_angola_hr/isoft_angola_hr/payroll/settlement.py`
(the `IRT-LUMP` block, and `_line(...)` for the per-component flags).

---

## 1. Current calculation

Performed once, in this order:

1. **`irt_gross`** — sum of every payable settlement line marked `irt_taxable`.
2. **`inss`** — employee social security = `inss_base × ss_employee_rate`, where
   `inss_base` is the sum of payable lines marked `inss_base`.
3. **`irt_base` = max(0, `irt_gross` − `inss`)** — social security comes off before IRT,
   the same order the monthly salary slip uses.
4. **`irt` = resolve_irt(`irt_base`, company, on_date = termination date)** — the base is
   passed **once** through the **monthly** IRT table in force on the termination date.

There is no spreading across periods, no annualisation, and no separate rate or schedule
for any individual component.

## 2. Taxable components

Whether a line enters `irt_gross` (IRT) and `inss_base` (social security):

| Settlement component | In IRT base | In INSS base | Notes |
|---|---|---|---|
| Salary for the final period | Yes | Yes | |
| Overtime (approved, unpaid) | Yes | Yes | |
| Leave — vested / proportional / untaken | Yes | Yes | |
| Christmas bonus (13.º) | Yes | **Yes** | |
| Vacation supplement (subsídio de férias) | Yes | **No** | Excluded from the contribution base by Decreto Presidencial n.º 227/18, artigo 13.º |
| Compensation / indemnity | **Configurable** | **No** | See §5 — the disputed item |
| Other earnings (free-form) | Defaults to Yes | Defaults to Yes | Set per line by the operator |

## 3. INSS treatment

- Employee social security is **subtracted from the IRT base** before the table is
  applied (`irt_base = irt_gross − inss`).
- The contribution base deliberately **excludes** the vacation supplement and any
  termination compensation.
- Rate: `ss_employee_rate` from `Isoft Statutory Rate`, effective on the termination
  date. Article cited on the settlement: Decreto Presidencial n.º 227/18, artigo 13.º.

## 4. IRT table used and effective date

- **Table:** the standard **monthly** IRT table (`resolve_irt`), i.e. the same table a
  single month's salary is taxed on.
- **Effective date:** the **termination date** — not the payment date, not the date the
  settlement is prepared, and not the dates the individual amounts accrued.
- **Legal source cited:** Código do IRT (Lei n.º 18/14, alterada pela Lei n.º 28/20).

## 5. Current assumption

> **A termination settlement is taxed as a single month's income.**

Consequences of that assumption, stated plainly:

- The IRT table is **progressive**. A settlement typically bundles amounts that accrued
  over many months (proportional leave, 13.º, supplements, compensation). Taxing them as
  one month pushes the total into a **higher bracket** than spreading them over the
  months of accrual would.
- The result is therefore **more tax withheld**, not less, in most cases. An incorrect
  treatment here is an over-withholding exposure toward the employee, and a
  misdeclaration exposure toward the AGT.
- **Termination compensation** is the one component whose tax treatment the sources
  genuinely conflict on. The application does not decide it: `compensation_tax_position`
  defaults to `verification_required`, and the settlement carries the company's stated
  position rather than presenting either answer as settled law. Código do IRT artigo 2.º
  n.º 1 al. g) is cited by the exemption position.

## 6. What an Angolan tax/legal specialist must confirm

1. **Single-month vs. spread.** Is a termination settlement taxed once on the monthly
   table (current behaviour), or must the accrued components be attributed to the months
   in which they accrued?
2. **Effective table date.** Is the correct table the one in force on the **termination
   date** (current behaviour), the payment date, or the accrual periods?
3. **Compensation / indemnity.** Is termination compensation subject to IRT, exempt, or
   exempt only within the Lei Geral do Trabalho limits? Under which article, and with
   what ceiling?
4. **Vacation supplement (subsídio de férias).** Confirmed excluded from the INSS
   contribution base by DP 227/18 artigo 13.º — but is it within the **IRT** base?
   (Currently treated as taxable.)
5. **Christmas bonus (13.º).** Confirm it belongs in **both** bases (current behaviour).
6. **Order of operations.** Confirm social security is deductible from the IRT base for a
   settlement, as it is for ordinary monthly salary (current behaviour).
7. **Proportional leave paid on termination.** Confirm it is ordinary taxable income
   rather than compensation of a different nature.

## 7. Until it is confirmed

- The calculation stays as described. **Do not silently change it.**
- Flag `IRT-LUMP` (`level: verify`) is raised on every settlement and shows the actual
  `irt_base` and the table date used, so the assumption is visible on the document
  itself and never applied silently.
- The application does **not** claim legal compliance for this treatment.

Owner: ISOFT + the client's Angolan tax adviser. Until signed off, Final Settlement is
**CONDITIONAL** for statutory compliance purposes.
