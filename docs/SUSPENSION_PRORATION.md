# Suspension proration — decision record

**Status: NOT SUPPORTED. Option B, taken deliberately.**
Date: 2026-08-27. Applies to `isoft_angola_hr` at the 2026 go-live.

## The question

Lei n.º 12/23, artigo 238.º n.º 3 makes the Christmas supplement (subsídio de Natal)
proportional to the **complete months of service** where a full year of service was not
given. A year interrupted by a suspension of the employment contract is, on the face of
the article, such a year. The same reasoning reaches the vacation supplement and the
proportional figures in a Final Settlement.

The application cannot compute that, and this document records why, what it does
instead, and what would have to change.

## What the data model actually holds

`Employee.status` offers `Active / Inactive / Suspended / Left`. That is the whole of it:

* there is **no** `suspension_from` or `suspension_to` field, on Employee or anywhere else;
* there is **no** Suspension DocType;
* `Employee` carries **no Version rows at all** on either site, so the history of past
  status changes was never recorded and cannot be recovered.

So a suspension has no duration — not going forward, and not retrospectively. There is
no quantity to prorate against.

## Why Option A was not taken

Option A (a `Suspension Period` DocType feeding salary, Natal and settlement) was
considered and rejected for this release on three grounds.

1. **The legal rule is not settled.** Whether a suspension interrupts "months of
   service" depends on the *kind* of suspension; several kinds under the LGT preserve
   the employee's rights. Wiring proration in would encode a guess about which ones do,
   in the statutory calculation, with no published determination behind it. That is
   precisely the "invented legal treatment" this release is not permitted to contain.
2. **It cannot fix the past.** With no history, a new DocType would start from empty.
   The 2026 supplement would still be computed as though nobody had been suspended.
3. **There is no exposure.** Production has **0** employees with status `Suspended`
   (85 Active, 24 Inactive, 18 Left, checked read-only on 2026-08-27). QA has 0. The
   change would add a statutory code path days before go-live to serve nobody.

## What the application does instead

A suspended employee is **excluded from payroll altogether** — every eligibility query
filters `status = "Active"` — so nobody is paid a wrong prorated figure. The exposure is
narrower than "suspension is mishandled": it is an employee **suspended for part of the
year and reinstated before December**, who receives the supplement counted from their
admission date with no reduction for the suspended months.

That case is now visible rather than silent:

| Where | What it does |
|---|---|
| `payroll_readiness.EXC-025` | Names the *status* of an employee who was paid last period and is not eligible now, so "Suspended" is stated instead of "not eligible". |
| `payroll_readiness.EXC-026` | Raised for a suspended employee: no salary is calculated, and any adjustment under artigo 238.º n.º 3 must be entered by hand. |
| `configuration_status → suspension_proration` | A run-level warning, raised **only** where the company actually has a suspended employee, naming the manual remedy. |
| `test_p34.TestSuspensionIsNotProrated` | Pins the gap, so it cannot be closed by accident and thought to be solved. |

## The manual remedy, which already exists

* **Payroll** — `subsidio_natal` is editable per employee in the payroll preview, so HR
  can enter the reduced figure.
* **Final Settlement** — `supplement_months_override` sets the month count directly, and
  the settlement prints the formula and the article alongside it.

Both leave the usual audit trail. Neither requires a code change.

## What would close it

1. A legal determination of which suspensions interrupt months of service for
   artigo 238.º. **LEGAL VERIFICATION REQUIRED** — this is the blocking item, not the code.
2. A `Suspension Period` DocType (`employee`, `from_date`, `to_date`, `reason`,
   `approved_by`, `company`) written whenever the status changes, so duration exists.
3. `engine.natal_months` and `law.complete_months` to subtract whole suspended months,
   under a setting that is off by default.
4. Backfill for any suspension already served — which, absent history, means entering it
   by hand.

Until (1) exists, (2)–(4) would be a guess with a schema around it.
