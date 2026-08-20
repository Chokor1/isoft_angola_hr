# Installation

## Requirements
Frappe 13.x, ERPNext 13.x (ships the HR module this app builds on), MariaDB 10.3+,
Python 3.8+. A MariaDB account that can `CREATE DATABASE` and `CREATE USER` — needed by
`bench new-site`, not by this app.

## Fresh site
```bash
bench new-site <site> --mariadb-root-password <root>
bench --site <site> install-app erpnext
bench get-app isoft_angola_hr <repo-url>
bench --site <site> install-app isoft_angola_hr
bench --site <site> migrate
bench build --app isoft_angola_hr
bench restart
```

## Verify the installation
```bash
bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.release_gate.verify_install
bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.production_readiness.health_check
```
Both must report PASS before configuration begins.

On a genuinely fresh site, record the evidence:
```bash
bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.release_gate.accept_clean_install
```
Until that has run, the release gate reports `GO FOR CONTROLLED PRODUCTION` and never
`PRODUCTION READY`. That is intentional.

## What installation creates
Roles (Payroll Officer, Payroll Manager, Payroll Finance Approver — created, never
assigned), contract and document type catalogues, absence reasons, the payslip print
format, custom fields on Employee and Appraisal, segregation-of-duties defaults, and the
`/ess` and `/mss` portal routes.

## Configuration order
1. **Isoft HR Settings** — default company, payroll accounts (salary expense, payroll
   payable, IRT payable, employee INSS payable, **employer INSS expense**, **employer INSS
   payable**), bank/cash account, cost centre.
2. **IRT Table** — brackets with an effective-from date.
3. **Statutory rates** — INSS employee/employer percentages.
4. **Role assignment** — at least one person per payroll role, and not the same person.
5. **Employees** — NIF, social security number, IBAN, salary profile, manager, holiday
   list.

Run the release gate after each step:
```bash
bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.release_gate.report
```
