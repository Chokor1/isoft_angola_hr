# Finance — payroll, ledger and payment

## What Finance receives
Payroll that a Payroll Manager has already approved, and that the preparer could not have
approved. If a slip changed after approval, the run comes back for re-approval — Finance
never posts an amount nobody signed off.

## Posting
**Post accounting** produces one balanced Journal Entry per run:
- DR salary expense (per component, per cost centre)
- DR employer social security expense
- CR IRT payable
- CR employee social security payable
- CR employer social security payable
- CR net payable

Reconcile the run against the ledger with **Payroll Reconciliation** before releasing it.

## Paying
1. **Release for payment.** Refused while an employee has no IBAN.
2. **Generate the payment file.** Pre-flight reports, before any file exists: an IBAN that
   is malformed rather than merely missing, an employee appearing twice, a mixed-currency
   run, and any difference between the file total and the payroll total.
3. The file is fingerprinted with SHA-256 and recorded in **Isoft Bank Export** with the
   employee count and total. Regenerating supersedes the previous version; it never
   rewrites it.
4. Upload the file to corporate internet banking. **There is no automated bank
   integration** — BAI does not publish its bulk-payment layout.
5. **Record the bank reference.** The export stays `Generated` until you do. A produced
   file is not evidence that anybody was paid.
6. **Confirm payment**, then **Close the period**.

## Proving what was paid
For any run: the approved register, the Journal Entry, the export record with its
checksum, employee count and total, and the bank's own reference. Those five together
answer "what did we pay, to whom, on whose authority, and how do we know".

## Before a payroll is approved — Finance checklist

Payroll reaches the ledger through one mapping: **Isoft HR Settings → Account per
Component**, plus the Payroll Payable account and the Salary Payment (bank) account. The
system refuses to post while any of these is missing or unusable, *before* a single
Journal Entry is written — never half a payroll — and names the component, the account
type it needs and where to configure it.

The same checklist is what the approval gate enforces: a payroll **cannot be submitted for
approval or approved** while any item is BLOCKED. It is shown as one answer — **READY** or
**BLOCKED**, with the exact reasons — at the top of the Payroll screen, inside every run
before approval ("Approval check"), and in **Production Readiness → Finance checklist**.

| # | Item | Where | Account nature |
|---|---|---|---|
| 1 | Payroll account mapping complete (every component, the payable and the bank) | Account per Component / Net Pay | see below |
| 2 | Employee INSS liability account configured (CTSS3) | Account per Component | Liability or Asset |
| 3 | Employer INSS expense account configured (CTSSE) — and its payable (CTSSP) | Account per Component | Expense · Liability |
| 4 | Salary Advance (Adiantamento) account configured (ADT) | Account per Component | Liability or Asset |
| 5 | Payroll Payable account configured (typed *Payable* for a per-employee sub-ledger) | Payroll Payable Account | Liability |
| 6 | Bank account configured | Salary Payment Account | Asset (Bank) |
| 7 | No mapping drift against the previous payroll — or Finance has confirmed the current mapping | Account mapping history | — |
| 8 | All salary components mapped (base, allowances, bonus, overtime, férias, natal, IRT …) | Account per Component | earnings: Expense · deductions: Liability or Asset |
| 9 | No blocking readiness exceptions for the period (salary profiles, negative net, statutory configuration) | Payroll → Payroll Readiness | — |

HR sees each failure as a sentence it can act on — *Employer Social Security expense
account is not configured. Please contact Finance.* — and the technical detail (component,
required nature, where to configure) underneath, for Finance.

Choosing the account is Finance's decision; the application never creates or guesses one.

## Changing an account mapping after a payroll has been posted

Every change to a payroll account mapping is recorded in **Isoft Payroll Account Change**:
who changed it, when, the old account and the new account. When a payroll had already
been posted under the old mapping the record is flagged, and the person saving is told:

> This account mapping was changed after payroll posting. Previous payroll vouchers
> remain unchanged. New payroll will use the new mapping.

Nothing is rewritten: the posted run's Journal Entries keep the accounts they were booked
to, and its reconciliation reports *mapping drift* with those accounts named. Until
Finance confirms the current mapping, item 7 of the checklist stays BLOCKED and no new
payroll can be approved.

**To confirm the mapping** (Finance only — the same capability as posting): Settings →
Account mapping history → *Confirm current mapping*. The confirmation is itself a record
(who, when, the mapping in force). A later change makes it stale and item 7 blocks again.

## Before generating the bank file

Pre-flight (Payroll run → Export Bank File) refuses the file, naming every problem at
once, unless: the payroll carries an approval (BNK-010) and a release for payment
(BNK-011); every submitted slip has its accrual Journal Entry in the ledger (BNK-012); the
payroll payable booked by those entries equals the payroll total (BNK-013); every employee
has a valid IBAN (BNK-004); nobody in the run already has a submitted payment (BNK-014);
and no earlier file for the run was submitted to or executed by the bank (BNK-015).
