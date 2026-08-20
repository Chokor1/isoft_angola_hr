# Backup and restore

```bash
bench --site <site> backup --with-files
```
Produces four files in `sites/<site>/private/backups`: database, public files, private
files, and a copy of `site_config.json`.

## The config backup is credential material
`…-site_config_backup.json` contains the database password, the site **encryption key**
and any API keys stored in site config, in plaintext. Treat backup archives as secrets:
restrict the directory, encrypt anything copied off the machine, and never attach one to a
ticket.

Losing the encryption key makes every stored password on the site undecryptable. Keep it
with the backup, and keep the backup safe.

## Verifying a backup
```bash
gzip -t <backup>.sql.gz                       # integrity
zcat <backup>.sql.gz | tail -3                # must end with "Dump completed"
zcat <backup>.sql.gz | grep -c '^CREATE TABLE'
tar -tf <backup>-files.tar | wc -l
```

## Restoring
```bash
bench --site <target> --force restore <backup>.sql.gz \
  --with-public-files <public>.tar --with-private-files <private>.tar
bench --site <target> migrate && bench restart
bench --site <target> execute isoft_angola_hr.isoft_angola_hr.services.production_readiness.health_check
```
A backup is not proven until it has been restored somewhere. Do that on a schedule.
