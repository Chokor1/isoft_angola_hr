# Recibo de Vencimento / Payslip — the printed salary receipt

**Print Format:** `Recibo de Vencimento` on *Isoft Salary Slip* (the DocType's default print
format). Every entry point uses it: the Desk **Print** button, the HR dashboard *Print / PDF*
action, the employee self-service PDF link (`/ess`) and the release gate check.

**Source files** (`isoft_angola_hr/isoft_angola_hr/`):

| File | Role |
|---|---|
| `print_format/recibo_de_vencimento/recibo_de_vencimento.html` | Jinja template — layout and labels |
| `print_format/recibo_de_vencimento/recibo_de_vencimento.css` | Stylesheet (wkhtmltopdf-safe: tables, pt units) |
| `print_format/recibo_de_vencimento/recibo_de_vencimento.json` | Print Format fixture; `html`/`css` are copies of the two files above |
| `payroll/recibo.py` | `get_context(doc)` — reads the slip and the masters, computes nothing |
| `install.py::create_payslip_print_format` | Copies the files into the Print Format record on every install / migrate |
| `translations/pt.csv` (rows with context `Recibo de Vencimento`) | Portuguese wording |
| `payroll/test_p42_payslip_receipt.py` | Automated tests |

To change the layout edit the `.html` / `.css`, regenerate the JSON (`html` and `css` fields
must equal the files — `test_json_fixture_matches_the_template_files` enforces it) and run
`bench --site <site> migrate` (or `bench execute
isoft_angola_hr.isoft_angola_hr.install.create_payslip_print_format`).

## What is printed

Two A4 portrait pages per slip, identical data:

1. **ORIGINAL** — for the employee.
2. **DUPLICADO - ARQUIVO EMPRESA** — for the company file.

Each page: company header (name, NIF, address — no logo), title, copy label, document number, period and issue date; employee block
(name, ID, NIF, social-security number, payroll reference, category, designation,
department); processing box (period, paid days, total working days, absence days, leave
days, overtime hours when any); earnings table (code, description, amount — read from the
slip's own child rows, never hard-coded); payment information (method, date of the payment
Journal Entry, currency, bank, IBAN, bank reference); summary (taxable, employee INSS, IRT,
total deductions, net, **TOTAL A RECEBER**); employee confirmation with a signature line;
footer with the copy label and page number.

Removed at the HR manager's review of 10 September 2026: the company logo, the company
phone / e-mail / website line, employment type, admission date, the payroll period repeated in the employee
block, the itemised deductions table (INSS, IRT and the total of deductions stay in the
summary), the gross line of the summary (it equals Total Earnings) and the date field beside
the signature. An advance recovered through the slip is therefore visible only inside
*Total de Descontos*. Draft and cancelled slips are stamped; a slip whose printed columns
do not add up to its stored totals carries a warning instead of printing wrong arithmetic.

Component codes follow the Angolan payroll convention already used by the earlier format
(R101 Salário Base, R201 Subsídio de Alimentação, R204 Subsídio de Transporte, R210 Abono de
Família, R301/R302 Férias/Natal, R401 Prémio, R501 Horas Extras, D01 INSS, D40 Adiantamento;
IRT keeps its abbreviation). Statistical rows (`do_not_include_in_total`) print in italics,
marked informative, and move no total.

## Every figure is copied, none is calculated

`recibo.get_context` reads `gross_pay`, `taxable_income`, `ss_employee_amount`, `irt_amount`,
`total_deduction`, `net_pay`, `payment_days`, `total_working_days` and the child rows the
payroll engine stored on the slip. The only derived numbers are counts for display: absence
days = total working days − paid days (the same subtraction the HR "explanation" panel shows),
leave days = submitted *On Leave* Attendance in the period (shared helper `_leave_days`, used
by both the receipt and `hr_api.salary_slip_explanation`), and approved overtime hours from
Attendance. The payroll engine, tax and INSS resolvers, attendance rules and accounting were
not touched.

## Language

Labels are translated **while the receipt renders**, from `frappe.local.lang`, i.e. the
language of the user who prints (User → Language; System Settings as fallback). Nothing is
translated at import time (`test_p37_runtime_language` guards the whole app).

Every label is looked up with the translation context `Recibo de Vencimento`
(`_("Payslip", context="Recibo de Vencimento")`). The context matters: translations are
merged from every app in `sites/apps.txt` order, and on the ITEC bench ERPNext is listed after
this app, so its Brazilian catalogue ("Folha de Pagamento", "Funcionário", "Rem. Líquida")
used to override the Angolan wording for the same English words. A contexted key belongs to
this document alone. Regional Portuguese users (`pt-BR`) inherit the `pt` rows.

Names, company, NIF, social-security numbers, IBAN, codes, document numbers and amounts are
data and are never translated. Component descriptions are translated from an English msgid
per engine component (`recibo.COMPONENT_LABELS`); their Portuguese translation is exactly
the name the engine stores on the slip, so a Portuguese receipt prints the slip's own words.

| English | Português |
|---|---|
| PAYSLIP | RECIBO DE VENCIMENTO |
| Employee | Funcionário |
| Basic Salary | Salário Base |
| Deductions | Descontos |
| TOTAL TO RECEIVE | TOTAL A RECEBER |
| I confirm receipt of the amount stated in this payslip. | Declaro que recebi a quantia constante neste recibo. |
| Employee Signature | Assinatura do Trabalhador |
| DUPLICATE - COMPANY FILE | DUPLICADO - ARQUIVO EMPRESA |

## Permissions

Unchanged. The print pipeline calls `check_permission("print")`, which applies the DocType
permissions and the record-level hook `doc_permissions.salary_slip_permission`: an Employee
sees only their own **submitted** slips; HR Manager, Payroll Officer, Payroll Manager,
Payroll Finance Approver and Accounts Manager see what their DocType permissions allow.
`recibo.get_context`, reachable over HTTP by slip name, checks `read` itself. The tests prove
Employee A cannot open, download or list Employee B's payslip.

## PDF geometry

wkhtmltopdf, A4 portrait, margins 10 mm top/bottom and 12 mm left/right declared in the
stylesheet (`.print-format { margin-…: Nmm; }` is parsed by `frappe.utils.pdf`). Frappe's
own `.print-format` padding is zeroed for print media — in wkhtmltopdf's narrow viewport it
was 0.2 in top and bottom and pushed a blank third page out whenever a copy was full. Table
headers repeat if a table ever breaks; rows never split. The stress test (45-character name,
long department and designation, seven-figure amounts, every optional earning, five
deductions, two informative rows, the reconciliation warning) still fits one page per copy.

## Tests

```
bench --site <site> run-tests --skip-before-tests --skip-test-records \
    --module isoft_angola_hr.isoft_angola_hr.payroll.test_p42_payslip_receipt
```

(`--skip-before-tests` is needed on this bench because another app's `before_tests` hook
tries to create ERPNext demo records.) Optional: install `pymupdf` in the bench environment
to also assert the text of every PDF page; without it that one test is skipped.
