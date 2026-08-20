# Monthly payroll runbook

Three people, three roles. One person cannot run this alone, by design.

## 1. Payroll Officer — prepare
1. **Angola HR → Payroll → Readiness.** Resolve every blocker before creating the run:
   missing salary profile, ambiguous profile, attendance not closed.
2. **Create Payroll Entry** for the period. Check the employee list.
3. **Calculate.** Review the exception list; nothing should be unexplained.
4. **Compare with last month.** Anything beyond the variance threshold is flagged —
   explain it before submitting, not after.
5. **Submit for approval.**

## 2. Payroll Manager — approve
1. Review the register, the variance report and the exceptions.
2. **Approve** or **Reject with a reason**. You cannot approve a run you prepared.
3. Approval fingerprints the run. If a slip changes afterwards, the approval is void and
   the run returns for re-approval — that is not a fault.

## 3. Finance — post, pay, close
1. **Post accounting.** One balanced Journal Entry: salary expense, IRT payable, employee
   and employer INSS, net payable.
2. **Release for payment.** Blocked while an employee has no IBAN.
3. **Generate the payment file.** Pre-flight reports malformed IBANs, duplicate lines,
   a mixed-currency run and any total that disagrees with the payroll. The file is
   fingerprinted (SHA-256) and recorded in **Isoft Bank Export**.
4. **Upload it to corporate internet banking.** There is no automated bank integration —
   see CHANGELOG *Known Limitations*.
5. **Record the bank's reference** against the export. Producing a file is not a payment,
   and the register refuses to pretend otherwise.
6. **Confirm payment**, then **Close the period**.

## 4. Statutory declarations
1. **Angola HR → Statutory Filing.** Validate the period; fix any employee named.
2. Generate the working file.
3. Key the declaration into the portal — AGT (Portal do Contribuinte → Declarações → IRT
   → Mapa de Remunerações) and INSS Virtual.
4. **Record the portal's reference.** Status stays `Generated` until you do.

## If something goes wrong
- **Wrong amount, not yet posted** — reject, correct the profile or attendance,
  recalculate.
- **Wrong amount, already posted** — cancel the Journal Entry (which releases the slips),
  correct, repost. Never edit a submitted slip.
- **Paid the wrong amount** — do not amend history. Correct it in the next period and
  record why.
