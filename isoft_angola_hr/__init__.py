# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt

#: Release version.
#:
#: 0.10.0, NOT 1.0.0, and the difference is deliberate. Semantic versioning reserves 1.0.0
#: for a release you are prepared to support in the field, and this build has never been
#: installed from scratch: `bench new-site` requires a MariaDB account that can CREATE
#: DATABASE, and no such credential exists on this bench. Everything else that would gate
#: a 1.0.0 has passed — 423 automated tests, 134 browser checks across an HR User and an
#: HR Manager, 118 live security probes, an upgrade rehearsal with an identical database
#: fingerprint, and a verified backup.
#:
#: This becomes 1.0.0 when, and only when:
#:     bench new-site ahr-clean.test --mariadb-root-password <root>
#:     bench --site ahr-clean.test install-app erpnext
#:     bench --site ahr-clean.test install-app isoft_angola_hr
#:     bench --site ahr-clean.test execute \
#:         isoft_angola_hr.isoft_angola_hr.services.release_gate.accept_clean_install
#:
#: See services/release_gate.py — the gate refuses to report PRODUCTION READY until that
#: acceptance run records its evidence, so the version and the software agree.
__version__ = "0.10.0"


def _install_runtime_patches():
	"""Layer this app's per-weekday shift resolution onto ERPNext.

	Two of the three weekday behaviours live in module-level functions with no class to
	override, so they are installed over the originals here — the one point that runs in
	every process (web, worker, scheduler) before any of them is called. Deliberately
	silent and defensive: during `bench new-site`, before erpnext is importable, it does
	nothing and the import must not fail.

	See isoft_angola_hr/shift_weekday.py for what is patched and why.
	"""
	try:
		from isoft_angola_hr.isoft_angola_hr.shift_weekday import install_patches

		install_patches()
	except Exception:
		pass


_install_runtime_patches()
