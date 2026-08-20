# Final Settlement — legal basis under Lei n.º 12/23

**Statute:** Lei n.º 12/23, de 27 de Dezembro (Lei Geral do Trabalho), which revoked
Lei n.º 7/15, de 15 de Junho.
**Text used:** *Diário da República, I Série n.º 245, de 27 de Dezembro de 2023* — the
machine-readable copy published by the ILO's NATLEX database. Every article quoted in
`services/angola_labour_law.py` is quoted from that text.
**Tax and social security:** Código do IRT (Lei n.º 18/14, alterada pela Lei n.º 28/20);
Decreto Presidencial n.º 227/18, de 27 de Setembro (which revoked Decreto n.º 38/08).

> **LEGAL VERIFICATION REQUIRED.** Reading a statute correctly is not legal advice.
> Where the law settles a question this app follows it and cites the article. Where the
> law is silent, the app says so and exposes the choice as configuration. Neither is a
> substitute for the customer's own legal adviser confirming the position before a real
> dismissal or severance payment.

---

## 1. Component-by-component

| Component | Legal article | Implemented rule | Configuration override | Known limitation |
|---|---|---|---|---|
| Salary for the final period — full period | Artigo 245.º n.º 1 | The whole monthly remuneration. No proration is shown, because none happens. | `salary_method = full_period` | — |
| Salary — partial period, statutory basis | Artigo 237.º n.º 7 + Artigo 240.º n.º 2 | `S/H = (Sm × 12) / (52 × Hs)` on the salário-base; the deduction is unworked hours × S/H. | `salary_method = hourly_237_7`; needs `weekly_hours` | Artigo 240.º governs *absence*. Days after a termination are not absences, and the statute gives no rule for them; using this basis for a leaver is an interpretation. |
| Salary — partial period, company basis | **none** | `monthly remuneration ÷ divisor × days worked`. | `salary_method = company_divisor`, `settlement_salary_days` | Labelled **Company Calculation Basis**. Lei n.º 12/23 contains no such divisor. |
| Vested leave not taken | Artigo 212.º n.º 1 | Paid in full, on the artigo 213.º base. | `vested_untaken_days` (HR confirms) | Derived from leave balances; leave carried over under artigo 208.º or tracked outside the system is not visible and must be corrected by HR. |
| Proportional leave to termination | Artigo 212.º n.º 2 | 2 working days × complete months from 1 January to the termination date. | `leave_vested = Yes` forces this branch | — |
| Leave before the right has vested | Artigo 212.º n.º 3 | 2 working days × complete months from the **date of admission**. Paragraphs 1 and 2 expressly do not apply, so vested days are *not* also paid. | `leave_vested = No` | The six-day floor of artigo 204.º n.º 2 is **not** applied — see §2. |
| Leave, short fixed-term contract | Artigo 205.º n.º 1 | Same accrual, capped at 22 working days. | `fixed_term_under_one_year` | Derived from the linked contract when one exists. |
| Leave remuneration base | Artigo 213.º n.os 1 e 2 | salário-base + technical supplement + availability supplement. Meal and transport **excluded**. | `leave_base_includes_allowances` (artigo 213.º n.º 2 — "salvo acordo das partes") | The app has no field for technical/availability supplements on the Salary Profile; they are entered on the settlement. |
| Price of one leave day | **none** | `leave remuneration base ÷ divisor`. | `settlement_leave_days` (default 22), or `leave_rate_method = hourly_237_7` | Labelled **Company Calculation Basis**. See §2. |
| Vacation gratuity (gratificação de férias) | Artigo 238.º n.º 1 al. a), n.º 3 | `salário-base × rate% × complete months ÷ 12`. Statutory minimum 50%. | `ferias_rate` (artigo 238.º n.º 2 permits more) | Complete months are counted over the civil year window; suspension periods are not deducted automatically. |
| Christmas bonus (subsídio de Natal) | Artigo 238.º n.º 1 al. b), n.º 3 | Same formula. Statutory minimum 50%. | `natal_rate` | As above. |
| Compensation — objective or collective dismissal | Artigo 308.º (via artigos 289.º, 295.º, 271.º al. b) | `base × min(years, 5) + 50% × base × max(0, years − 5)` | — | — |
| Compensation — insolvency or extinction | Artigo 307.º (via artigo 278.º) | `50% × base × years` | — | Applies to the artigo 277.º grounds e) and, conditionally, d) and g). |
| Indemnity — non-reinstatement | Artigo 309.º | `50% × base × years` | — | Depends on a court ruling the app cannot see; flagged. |
| Indemnity — unlawful or indirect dismissal | Artigo 310.º n.os 1 e 3 | `base × years`, with a floor of three months' base. | — | Depends on a court ruling; flagged. |
| Seniority used by all of the above | Artigo 311.º | Whole years, plus one more when the remaining fraction is ≥ 3 months. | `seniority_years_override` (reason, user and time recorded) | — |
| Notice not given by the worker | Artigo 305.º n.º 2 (and artigo 306.º n.º 5) | Deducted: `base ÷ 30 × missing days`. | `notice_required_days`, `notice_given_days` | If the days given are not recorded the settlement **refuses to complete** rather than assuming any. |
| Notice of non-renewal not given by the employer | Artigo 17.º n.º 4 | Paid: 30 days' base. | `employer_missed_renewal_notice` | — |
| Employee social security | Decreto Presidencial n.º 227/18, artigo 13.º | 3% of gross remuneration; the vacation gratuity is excluded, the Christmas bonus is not. Same base split the salary slip applies. | `Isoft Statutory Rate` | Whether termination compensation enters the base is not addressed by the decree — excluded here and flagged. |
| IRT | Código do IRT | The effective-dated `IRT Table`, applied to the taxable components less the employee social security — the same order as a salary slip. | `IRT Table` | The table is monthly and is applied once to the whole settlement; see §2. |
| Salary advance recovery | — | Capped at what the settlement can carry; the remainder stays outstanding. | `recover_advance` | Reuses `services/advances.outstanding_for`. |
| Payment deadline | Artigo 245.º n.º 4 | Termination date + 3 days. | — | Artigo 296.º replaces it for a collective dismissal (end of the dismissal process). |

## 2. Open questions — configurable, never presented as law

These are surfaced on every settlement that touches them, and are listed by
`angola_labour_law.settlement_reference()["open_questions"]`.

1. **The monetary divisor for one day of leave.** Artigo 204.º n.º 1 fixes the leave
   *period* at 22 working days, and artigo 237.º n.º 7 gives an *hourly* rate for salary
   deductions. Nothing in Lei n.º 12/23 converts a monthly salary into the price of one
   day of leave. The divisor is therefore a **Company Calculation Basis** and is labelled
   as one wherever it appears.
2. **The six-day floor of artigo 204.º n.º 2.** The article sets a floor for the leave
   *period* in the year of admission. It does not say the floor applies to the money paid
   under artigo 212.º n.º 3. It is **not** applied; a settlement below six days carries a
   note saying so.
3. **IRT and INSS on termination compensation.** The consolidated Código do IRT excludes
   compensation for contract termination within the Lei Geral do Trabalho limits
   (artigo 2.º n.º 1 al. g)), while commentary on Lei n.º 28/20 states that such
   compensation became fully taxable. Decreto Presidencial n.º 227/18 artigo 13.º defines
   the contribution base as gross remuneration and excludes the subsídio de férias, but
   does not mention termination compensation. The position is a company setting
   (`settlement_compensation_tax_position`), shown on the settlement as a company position
   and defaulting to `verification_required`, which applies no IRT and says so.
4. **A lump sum against a monthly IRT table.** The table is applied once to the whole
   taxable settlement. Whether a termination settlement is taxed as one month or spread
   has not been verified; every settlement carries the flag.
5. **Complete months and suspension.** Artigo 204.º n.º 4 counts days of actual work,
   justified paid absence and parental leave. The app counts calendar complete months
   between the relevant dates; an unpaid suspension is not deducted automatically. Where
   it matters, HR overrides the count and records why.

## 3. What the corrected engine replaced

Calculation version 1 (`payroll/engine.compute_settlement`) is kept unchanged so
settlements produced by it stay reproducible. It is **not** the law:

* it paid a whole month whenever the days worked reached the divisor while the screen
  went on printing `daily rate × days`, so the arithmetic shown did not equal the amount
  paid;
* it counted months by month *number*, making a termination on 21 August eight months
  instead of seven;
* it had one undifferentiated "untaken leave days" input where artigo 212.º draws a sharp
  line between vested and proportional leave;
* it priced leave over a divisor presented as a legal rate;
* it never recorded why the employment ended, so it could neither compute the
  compensation of artigos 307.º–310.º nor correctly refuse to;
* it was gross-only, with a footnote saying IRT and INSS "apply" and no figure.

Version-1 records keep their stored amounts. `recalculate_settlement` restates one under
the corrected rules, but only on an explicit request and after showing the difference.

## 4. Sources

* Lei n.º 12/23, de 27 de Dezembro — *Diário da República, I Série n.º 245*
  (ILO NATLEX: `natlex.ilo.org/dyn/natlex2/natlex2/files/download/117287/`)
* Código do Imposto sobre os Rendimentos do Trabalho — Lei n.º 18/14, alterada pela
  Lei n.º 28/20, de 22 de Julho
* Decreto Presidencial n.º 227/18, de 27 de Setembro — regime de vinculação e
  contribuição da Protecção Social Obrigatória
