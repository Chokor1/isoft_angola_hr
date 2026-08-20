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
