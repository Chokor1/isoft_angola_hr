# Monthly payroll runbook

Three people, three roles. One person cannot run this alone, by design.

## 1. Payroll Officer — prepare
1. **Angola HR → Payroll → Readiness.** Resolve every blocker before creating the run:
   missing salary profile, ambiguous profile, attendance not closed.
2. **Create Payroll Entry** for the period. Check the employee list.
3. **Calculate.** Review the exception list; nothing should be unexplained.
4. **Compare with last month.** Anything beyond the variance threshold is flagged —
   explain it before submitting, not after.
5. **Check the Finance configuration line** at the top of the Payroll screen. It says
   **READY** or **BLOCKED** with the reasons in plain words — *Employer Social Security
   expense account is not configured. Please contact Finance.* While it is BLOCKED the
   run can be calculated but not submitted for approval, and the buttons are inert. Only
   Finance can fix it; you cannot work around it.
6. **Submit for approval.** Refused, with every reason at once, while any Finance account
   is missing, a salary component is unmapped, an employee of the run has no salary
   profile, a slip is orphaned or duplicated, or the mapping changed after the previous
   payroll was posted and Finance has not yet confirmed it.

## 2. Payroll Manager — approve
1. Review the register, the variance report and the exceptions.
2. Read the **Approval check** on the run: READY, or BLOCKED with the reasons and who owns
   each one (Finance, HR, Payroll). The same check is re-run when you press Approve.
3. **Approve** or **Reject with a reason**. You cannot approve a run you prepared.
4. Approval fingerprints the run. If a slip changes afterwards, the approval is void and
   the run returns for re-approval — that is not a fault.

## 3. Finance — post, pay, close
1. **Post accounting.** One balanced Journal Entry: salary expense, IRT payable, employee
   and employer INSS, net payable.
2. **Release for payment.** Blocked while an employee has no IBAN.
3. **Generate the payment file.** Pre-flight refuses it unless the run is approved and
   released, every slip's accrual is in the ledger, the payable booked equals the payroll
   total, every IBAN is valid, nobody is already paid and no earlier file was submitted to
   or executed by the bank. The file is fingerprinted (SHA-256) and recorded in **Isoft
   Bank Export**.
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

## 5. Christmas bonus (Subsídio de Natal) — its own run, first fortnight of December
Paid before, and separately from, the December salary. Same three people, same steps.

| Service in the year | Bonus (SN) |
|---|---|
| 12 months or more | Basic Salary × 50% |
| fewer than 12 months | (Basic Salary × 50%) ÷ 12 × complete months |

IRT is calculated on the bonus alone, from the IRT table. **No INSS** (employee or
employer). Net = bonus − IRT. Examples: 400 000,00 for 12 months → bonus 200 000,00,
IRT 20 500,00, net 179 500,00; 8 months → 133 333,33, IRT 0,00, net 133 333,33.

1. **Settings → Enabled Components → Subsídio de Natal** must be on, and the rate is
   *Natal rate* (50). A bonus run cannot be created while it is off.
2. **Payroll → Run Type: Christmas Bonus**, choose the year and the payment date
   (15 December by default), **Preview**. Each employee shows the basic salary (Salary
   Profile, no absences), complete months to 31 December, bonus, IRT and net. Someone
   admitted on 1 December gets 1/12; from 2 December, nothing. Suspended employees are
   flagged — adjust them by hand.
3. To change one employee's bonus, type the amount and **the reason** (required; never
   above a full year's bonus). The approver sees every change with its reason.
4. **Create Christmas Bonus Slips**, then submit for approval, approve, post and pay
   exactly as for a salary run. The bank file is referenced `Natal_<year>`.
5. The December **salary** run no longer adds the bonus; a bonus typed on a salary slip
   after the bonus run is refused. One bonus run per company per year.
6. **Payslip:** "Recibo de Subsídio de Natal", dated the payment date.
7. **Declarations:** the December IRT map keeps one row per employee, with *Subsídio de
   Natal* and *IRT Subsídio de Natal* in their own columns; the INSS map leaves the bonus
   out.
8. **Leavers:** a final settlement in the same year deducts the bonus already paid from
   its own Christmas bonus line (never below zero, nothing clawed back).

## Changing a payroll account
Accounts are chosen by Finance in **Settings → Account per Component**. Every change is
recorded (who, when, old, new). After a payroll has been posted the change is flagged —
*Previous payroll vouchers remain unchanged. New payroll will use the new mapping.* — and
no new payroll can be approved until Finance presses **Confirm current mapping** in
Settings → Account mapping history. Nothing historical is rewritten.

## If something goes wrong
- **Wrong amount, not yet posted** — reject, correct the profile or attendance,
  recalculate.
- **Wrong amount, already posted** — cancel the Journal Entry (which releases the slips),
  correct, repost. Never edit a submitted slip.
- **Paid the wrong amount** — do not amend history. Correct it in the next period and
  record why.
