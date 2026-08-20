# Isoft Angola HR — HR Operating Guide

**Who this is for:** the HR department. Not employees, not line managers.

This system is operated by HR. Employees and line managers do **not** need a login for
anything. Somebody asks HR for something — at the desk, by e-mail, on paper — HR records
it here, and an authorised HR person decides it.

```
Employee or management asks HR
        ↓
HR records the request
        ↓
Pending Approval
        ↓
An authorised HR person reviews it
        ↓
Approve / Reject
        ↓
The system applies the result
        ↓
Full audit history kept
```

Self-service (`/ess`) and the manager area (`/mss`) still exist and still work. They are
**optional conveniences**. If an employee has a login they can raise their own request,
and it lands in exactly the same HR queue. Nothing in this guide depends on that.

**Where everything lives:** the *Angola HR* console — `Angola HR Dashboard` from the
awesome bar, or the star icon in the navbar. You should never need to type an `/app/...`
URL.

---

## 1. Daily HR operations

Open **Insights → HR Dashboard**. The first panel answers one question:

> **What needs HR action today?**

Each row is a count, what it means, and a click that takes you to the screen that clears
it. Work down the list. When it is empty, there is genuinely nothing waiting.

The **New** button at the top left opens every routine job from anywhere in the
application — new employee, contract, leave, justification, advance, salary change, bank
change, document, job opening, performance cycle. You never have to find the right screen
first.

---

## 2. Hiring

| | |
|---|---|
| **Where** | Employees → Employees |
| **Click** | **New Employee** |
| **Enter** | Name, date of birth, gender, joining date, company, department, designation |
| **Next** | Salary Profile, then Contract |
| **Approves** | Nobody — creating an employee record is not an approval step |
| **Result** | An employee who can be administered and paid |

**A User ID is optional.** Leave it blank. It only controls whether that person can open
`/ess` to look at their own payslips; it has no effect on payroll, contracts, leave,
documents or reviews. On this site most employees deliberately have none.

**Reports To is optional too.** It drives the org chart and team views. Where it is blank,
HR handles everything that a line manager would otherwise have contributed.

### Recruiting into the role first

**Talent → Recruitment.** New Job Opening → New Applicant → Schedule Interview →
**Record Interview Result** → New Job Offer → mark it Accepted → **Create Employee**.

Interview panels do not need accounts. After the interview, HR opens *Record Interview
Result*, enters Cleared or Rejected, and names the panel in **Panel / decided by** so the
decision is not attributed to whoever typed it.

An applicant needs a date of birth before they can be converted into an employee.

---

## 3. Contracts

| | |
|---|---|
| **Where** | Employees → Contracts |
| **Click** | **New Contract** |
| **Enter** | Employee, contract type, start date (duration, probation and notice default from the contract type) |
| **Then** | Submit for Approval |
| **Approves** | **An HR Manager, and not the person who prepared it** |
| **Result** | Active contract; probation and expiry tracked automatically |

- **Renew** — on an existing contract. It creates the next contract and leaves the
  previous one intact, so the history stays readable.
- **Probation** — Confirm, Extend or End, with a dated and attributed decision. Where a
  line manager gave a view, record it as the manager recommendation; the decision itself
  is still HR's.
- **Terminate** — requires a reason and stamps the date. This starts offboarding.
- **Bulk Contracts** (Employees → Bulk Contracts) — for staff who joined before the
  contract module existed. Preview first: it names every employee it would skip and why.
  Nothing is written until you press Create.

---

## 4. Leave

| | |
|---|---|
| **Where** | Requests & Approvals → Leave Requests |
| **Click** | **New Leave Request** |
| **Enter** | Employee, leave type, dates, reason, and **Request Source** — how they asked you |
| **Approves** | An HR Manager (Approve / Reject on the request) |
| **Result** | Approved leave, drawn from the employee's allocation |

Allocate before you approve: **Time & Attendance → Leave Allocations** decides how many
days exist to spend. **Leave Balances** shows what is left.

The same HR person may both record and approve leave. No money moves, and ERPNext's leave
ledger refuses an over-allocation whoever approves it.

---

## 5. Attendance

**Time & Attendance → Attendance** — daily attendance, entered or imported. Payroll uses
it to work out payable days.

**Requests & Approvals → Attendance Justifications** — anything unexplained.

| | |
|---|---|
| **Click** | **New Occurrence** (or open one that already exists) |
| **Enter** | Employee, date, type, missing hours |
| **Then** | **Justify** — record the reason, attach the certificate the employee handed you, and say how it reached HR |
| **Decide** | Justified or Unjustified |

After five days an occurrence locks. Only an HR Manager can reopen it, as an
**Extraordinary Re-justify**, and must state the exceptional circumstance.

---

## 6. Salary advances

| | |
|---|---|
| **Where** | Requests & Approvals → Salary Advances |
| **Click** | **New Salary Advance** |
| **Enter** | Employee, amount requested, instalments, reason, request source |
| **Then** | Submit for Approval |
| **Approves** | **An HR or Payroll Manager — not the person who recorded it** |
| **Then** | Finance disburses (posts the journal entry) |
| **Result** | Recovered automatically from payroll, one instalment per run |

**A draft shows no instalment schedule.** It is built at approval, because the approved
amount may differ from the amount requested. Recovery is capped so net pay can never go
negative; anything uncollectable stays outstanding and is reported.

---

## 7. Salary changes

| | |
|---|---|
| **Where** | Requests & Approvals → Salary Changes |
| **Click** | **New Salary Change** |
| **Enter** | Employee (current salary is filled in), new salary, effective date, justification |
| **Then** | Submit for Approval → Approve → Apply |
| **Approves** | **An HR Manager, and not the requester** |
| **Result** | The old Salary Profile is closed and a new one opens on the effective date |

> **Never create a second Salary Profile by hand for a normal increase.** Salary Profiles
> are for a first salary or an authorised historical correction only. Creating them
> manually is what produced the overlapping salary histories the system now refuses.

The effective date must be the **first day of a payroll period** — the engine resolves one
salary per period and cannot split one. The screen shows you the next valid date.

---

## 8. Bank changes

| | |
|---|---|
| **Where** | Requests & Approvals → Bank Change Requests |
| **Click** | **New Bank Change** |
| **Enter** | Employee, new IBAN, bank, and attach the written request |
| **Approves** | **An HR Manager. Recording and approving are different permissions** |
| **Result** | Only on approval is the employee's IBAN updated |

Redirecting where a salary is paid is the highest-value fraud target in a payroll system.
Recording the request changes nothing; the account currently on file is kept, masked, so
the approver can see what is being changed from.

---

## 9. Employee documents

| | |
|---|---|
| **Where** | Employees → Employee Documents |
| **Click** | **Add Employee Document** |
| **Enter** | Employee, type, number, issue and expiry dates, and the scan |
| **Result** | Filed and **verified** — because HR saw the original |

Expiry is tracked and appears on the HR action queue before a document lapses.

Confidential and medical documents (sick notes, medical reports) can only be filed and
read by an **HR Manager**. An HR User is not offered those types at all.

Employees with self-service can upload their own; those arrive marked *Pending
Verification* for HR to check against the original.

---

## 10. Performance

| | |
|---|---|
| **Where** | Talent → Performance |
| **First** | **Appraisal Templates** — the objectives and their weightings, which must total 100% |
| **Then** | **New Performance Cycle** — period, template, who is in scope |
| **Then** | **Preview** (who would be reviewed, and who is skipped and why), then **Generate** |

Generating creates one appraisal per eligible employee. **Employees with no line manager
are included** — HR conducts those reviews.

For each review in *Reviews waiting for HR*:

1. **Record Evaluation** — enter the scores. Say whether this is a line manager's decision
   you are recording (and **name them**) or HR's own. The system stores who decided
   separately from who typed it.
2. **Record Acknowledgement** — the employee has seen it. If they signed a printed copy,
   enter that here; it is a statement of fact, not an approval.
3. **Finalise** — an HR Manager closes the review.

**Performance never changes salary.** A recommendation creates a draft Salary Change,
which goes through the salary-change approval like any other.

---

## 11. Transfers, promotions and offboarding

- **Promotion** — record it as a Salary Change with type *Promotion*; it can carry the new
  designation and department.
- **Transfer** — ERPNext Employee Transfer.
- **Offboarding** — Employees → Offboarding. The exit checklist, what is outstanding and
  what has to be returned. Terminate the contract first (HR Manager, with a reason).
- **Final Settlement** — Payroll → Final Settlement: outstanding salary, unused leave,
  13th month and any advance still to recover.

Severance and notice are **not** calculated. They depend on the legal ground for
termination and on facts the system does not hold; the statutory limits are shown as
warnings against Lei n.º 12/23.

---

## 12. Payroll preparation

**This is unchanged, and deliberately so.** HR-operated mode is about employee processes.
It does not merge payroll preparation, approval and payment into one person.

```
Payroll Officer prepares  →  Payroll Manager approves  →  Finance posts and pays
```

Payroll → Payroll: readiness pre-flight, create the entry, calculate, submit for approval,
approve, post, release for payment, confirm payment, close.

Run **Payroll Readiness** first. It names every employee who cannot be calculated and why.

---

## 13. Approvals

**Requests & Approvals → Approval Inbox.** Everything HR has submitted and not yet
decided, in one list: what it is, whose it is, how the request reached HR, who recorded
it, its status, and which role may approve it. **Decide** takes you to the screen that
owns the record — approval logic lives there and is never duplicated.

You do not create anything here.

### Who may approve what they recorded

| Process | Same HR person may record **and** decide? | Why |
|---|---|---|
| Leave | **Yes** | No money moves; the leave ledger enforces the entitlement whoever approves |
| Attendance justification | **Yes** | Low value; the five-day lock and the HR-Manager-only override are the real controls |
| Employee document | **Yes** | Filing evidence is clerical; confidentiality is the control that matters |
| Contract | **No** | It sets the legal terms of employment |
| Salary change | **No** | It changes pay — the highest-value unaudited action in an HR system |
| Salary advance | **No** | Money leaves before it is earned, and Finance pays on the approval |
| Bank change | **No** | Redirecting salary is the highest-value fraud target |
| Performance | **No** | Evaluation and sign-off are different judgements |
| Termination | **No** | Not reversible in any meaningful sense |
| Payroll | **No** | Three roles, unchanged — this is a financial control |

The two "No" rows for salary change and salary advance are configurable in **Isoft HR
Settings**. They are on by default and should stay on.

---

## 14. Reports

Payroll → Salary Slips, and the report list: payroll register, IRT, INSS, bank payment
list, audit trail, contract expiry, document expiry, salary change history, advance
balance, master-data completeness.

Insights → Org Chart, Headcount & Turnover.

---

## 15. Compliance

**Insights → IRT / INSS Declarations.**

1. **Validate** — missing NIF, missing social-security number, a contribution with no
   base, an employee appearing twice.
2. **Generate working file** — the figures to key in.
3. Enter the declaration in the **AGT or INSS portal yourself**.
4. **Record the reference** the portal gives you.

> This screen does **not** submit anything electronically. Neither AGT nor INSS publishes a
> machine format; both are portal-entry processes. What you get is a working file to key
> from and reconcile against.

---

## 16. Roles

| Role | Can do |
|---|---|
| **HR User** | Create and prepare: employees, contracts, leave, attendance justifications, salary change requests, advance requests, bank change requests, documents, recruitment, performance |
| **HR Manager** | Everything an HR User can, plus: approve contracts, salary changes, bank changes, advances and leave; file and read confidential documents; finalise reviews; terminate contracts |
| **Payroll Officer** | Prepare payroll; read salary profiles; record attendance |
| **Payroll Manager** | Approve payroll; approve advances |
| **Payroll Finance Approver** / **Accounts Manager** | Post accounting, generate the payment file, confirm payment, disburse advances |
| **Employee** *(optional)* | `/ess` only — own payslips, own leave, own documents. Never required |

Give HR staff **HR User**. Give the person who signs things off **HR Manager**. That is
the whole HR side of the model.

Do **not** create employee or manager accounts merely to make a process work. None of them
need one.

---

## 17. What still needs the ERPNext desk

Two things, both rare and both deliberate:

1. **Interview Rounds** — configuring a panel and its expected rating. Set up once. The
   Schedule Interview dialog tells you if none exists rather than failing.
2. **Contract Templates** — authoring the legal wording. A rich-text job that belongs on a
   full form. Bulk Contracts works without one; only document *generation* needs it.

Neither blocks a normal workflow.
