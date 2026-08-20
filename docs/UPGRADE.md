# Upgrade

```bash
bench --site <site> backup --with-files      # before anything else
bench update --apps isoft_angola_hr          # or: cd apps/isoft_angola_hr && git pull
bench --site <site> migrate
bench build --app isoft_angola_hr
bench restart
bench --site <site> execute isoft_angola_hr.isoft_angola_hr.services.production_readiness.health_check
```

## Rehearse first
On a copy of production, not on production:
1. Restore the backup into a scratch site.
2. Run the migration there.
3. Compare a fingerprint of salary slips, salary profiles, IRT brackets, payroll entries,
   GL entries and role assignments before and after. They must be identical — migrations
   are additive and must never restate a historical amount.
4. Run the full test suite.

## Rollback
Migrations are additive, so rollback is a restore:
```bash
bench --site <site> --force restore <backup>.sql.gz \
  --with-public-files <public>.tar --with-private-files <private>.tar
bench --site <site> migrate
bench restart
```
Roll back the application code to the previous revision **before** restoring, so the
schema and the code match.
