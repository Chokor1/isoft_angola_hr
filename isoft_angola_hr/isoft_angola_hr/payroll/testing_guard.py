# Copyright (c) 2026, ISOFT LDA
# For license information, please see license.txt
"""Where, and how, the database-writing tests may run.

The payroll tests create employees, post and cancel Journal Entries, repoint the
payroll account mapping and flip the segregation switches, and several endpoints they
call commit. On 2026-10-14 the suite ran unprotected on dev.isoft.ao, a site holding
ITEC's real employees and books: a cancelled test voucher and test accounts stayed in
the ledger, HR Settings were saved by a test teardown, and every logged error stayed
behind (Error Log is a MyISAM table, so a rollback never removes it).

Two ways to run them, nothing else:

1. On a disposable copy, marked in its OWN site_config.json:
       bench --site <site> set-config isoft_angola_hr_test_site <site>

2. On a site with real data, through the protected runner only:
       cd sites && ../env/bin/python -m isoft_angola_hr.isoft_angola_hr.payroll.testing_guard \\
           <site> redis://127.0.0.1:<private port> <module> [<module> ...]

   The protected runner makes the whole run non-durable:
   * a commit becomes a savepoint (the "last commit point") and a bare rollback goes
     back to it, so every test sees the transaction semantics it was written for while
     nothing ever reaches the disk; the class ends with a real rollback;
   * statements that would commit implicitly (DDL, START TRANSACTION mid-transaction,
     LOCK TABLES, SET autocommit) and a driver-level commit are refused;
   * writes to non-transactional (MyISAM) tables are dropped, and frappe.log_error is
     captured in memory instead of written;
   * cache, queue and realtime go to a PRIVATE redis, so no worker, browser or cached
     document of the live site ever sees the run; global search indexing is off and
     background jobs run inline, inside the protected transaction;
   * files the run writes under the site's files folders are removed afterwards, unless
     a committed File record (another user's upload) refers to them.
   Before any test runs it proves that a commit does not reach the server.

Anything else is refused (an error, never a skip), so a run on the wrong site can not
be mistaken for a green one.
"""

import json
import os
import re
import sys

import frappe

CONFIG_KEY = "isoft_angola_hr_test_site"
COMMIT_POINT = "isoft_hr_commit_point"
NON_TRANSACTIONAL_TABLES = ("`tabError Log`", "tabError Log", "__global_search")
_IMPLICIT_COMMIT = re.compile(
	r"^(alter|create|drop|rename|truncate|grant|revoke|lock\s+tables|unlock\s+tables|"
	r"analyze|optimize|repair|flush|load\s+data|cache\s+index|set\s+(session\s+|@@)?autocommit)\b")
_TEMPORARY = re.compile(r"^(create|drop)\s+temporary\s+table\b")


class NotAnIsolatedTestSite(RuntimeError):
	pass


class ProtectedRunViolation(RuntimeError):
	"""A statement or action that would make the run durable or visible."""


class _State(object):
	active = False
	site = None
	verified = False
	started = None          # datetime string: when the protected run began
	has_commit_point = False
	stats = None


STATE = _State()


# --------------------------------------------------------------------------- #
# The guard every database-writing test module calls (setUpModule)
# --------------------------------------------------------------------------- #
def _common_config_value():
	path = os.path.join(frappe.local.sites_path or ".", "common_site_config.json")
	try:
		with open(path) as f:
			return json.load(f).get(CONFIG_KEY)
	except (IOError, ValueError):
		return None


def protected_run_active():
	site = getattr(frappe.local, "site", None)
	return bool(STATE.active and STATE.verified and site and STATE.site == site)


def require_isolated_site():
	"""Refuse unless this is a marked disposable site or a verified protected run."""
	if protected_run_active():
		return
	site = getattr(frappe.local, "site", None)
	marked = frappe.conf.get(CONFIG_KEY)
	if _common_config_value():
		raise NotAnIsolatedTestSite(
			"{0} is set in common_site_config.json, which would mark every site on the "
			"bench as disposable. Set it in the test site's own site_config.json.".format(CONFIG_KEY))
	if not site or marked != site:
		raise NotAnIsolatedTestSite(
			"Refusing to run isoft_angola_hr's database-writing tests on '{0}': it is neither "
			"marked as a disposable test site ({1} = {2!r}) nor running under the protected "
			"runner. Use: python -m isoft_angola_hr.isoft_angola_hr.payroll.testing_guard "
			"<site> <private redis url> <modules>".format(site, CONFIG_KEY, marked))


def before_tests():
	"""bench run-tests --app isoft_angola_hr calls this before loading any test."""
	require_isolated_site()


# --------------------------------------------------------------------------- #
# Protection
# --------------------------------------------------------------------------- #
def _normalise(query):
	return re.sub(r"\s+", " ", (query or "").strip().rstrip(";").strip()).lower()


def _install_database_protection():
	from frappe.database.database import Database
	import pymysql.connections

	original_sql = Database.sql
	stats = STATE.stats

	def raw(db, query):
		return original_sql(db, query)

	def set_commit_point(db):
		raw(db, "savepoint {0}".format(COMMIT_POINT))
		STATE.has_commit_point = True

	def real_rollback(db):
		raw(db, "rollback")
		STATE.has_commit_point = False

	def protected_sql(self, query, values=(), *args, **kwargs):
		q = _normalise(query) if isinstance(query, str) else ""
		if q in ("commit", "commit work"):
			stats["commits_intercepted"] += 1
			set_commit_point(self)
			return ()
		if q in ("start transaction", "begin", "begin work") or q.startswith("start transaction "):
			return ()      # a new transaction would implicitly commit the open one
		if q in ("rollback", "rollback work"):
			stats["rollbacks_to_commit_point"] += 1
			if STATE.has_commit_point:
				try:
					return raw(self, "rollback to savepoint {0}".format(COMMIT_POINT))
				except Exception:
					pass   # the server already ended the transaction (e.g. a deadlock)
			real_rollback(self)
			return ()
		if q and _IMPLICIT_COMMIT.match(q) and not _TEMPORARY.match(q):
			stats["refused"].append(q[:120])
			raise ProtectedRunViolation("Refused in a protected run (it would commit): " + q[:160])
		if q[:7] in ("insert ", "update ", "delete ", "replace") and any(
				t.lower() in q for t in NON_TRANSACTIONAL_TABLES):
			stats["non_transactional_writes_dropped"].append(q[:120])
			return ()
		return original_sql(self, query, values, *args, **kwargs)

	def protected_commit(self):
		# what Database.commit does, minus everything that leaves the transaction:
		# before_commit callbacks still run (they write inside it); realtime events,
		# after-commit jobs and link counts are dropped instead of flushed.
		for method in (frappe.local.before_commit or []):
			frappe.call(method[0], *(method[1] or []), **(method[2] or {}))
		frappe.local.before_commit = []
		protected_sql(self, "commit")
		frappe.local.rollback_observers = []
		frappe.local.realtime_log = []
		frappe.flags.enqueue_after_commit = []
		if getattr(frappe.local, "link_count", None):
			frappe.local.link_count = {}

	def refuse_driver_commit(conn):
		raise ProtectedRunViolation("Refused in a protected run: a driver-level commit")

	Database.sql = protected_sql
	Database.commit = protected_commit
	pymysql.connections.Connection.commit = refuse_driver_commit
	return original_sql


def _install_side_channel_protection(private_redis):
	conf = frappe.local.conf
	for key in ("redis_cache", "redis_queue", "redis_socketio"):
		if conf.get(key) and conf.get(key).rstrip("/") == private_redis.rstrip("/"):
			raise ProtectedRunViolation("the private redis must not be the site's own " + key)
	shared = {conf.get(k) for k in ("redis_cache", "redis_queue", "redis_socketio")}
	if private_redis in shared:
		raise ProtectedRunViolation("the private redis is one of the site's shared redis servers")
	for key in ("redis_cache", "redis_queue", "redis_socketio"):
		conf[key] = private_redis
	frappe.redis_server = None
	conf["disable_global_search"] = 1

	stats = STATE.stats

	def capture_log_error(message=None, title=None, *args, **kwargs):
		stats["log_error_captured"].append(("{0}".format(title), "{0}".format(message)[:200]))

	control = ("queue", "timeout", "event", "is_async", "async", "job_name", "now",
	           "enqueue_after_commit", "at_front")

	def enqueue_inline(method, *args, **kwargs):
		# A worker would run the job in its own, committed transaction, against the live
		# site. Here it runs inline, inside the protected transaction, and is rolled back
		# with everything else.
		stats["jobs_run_inline"].append("{0}".format(getattr(method, "__name__", method)))
		for key in control:
			kwargs.pop(key, None)
		return frappe.call(method, *args, **kwargs)

	frappe.log_error = capture_log_error
	frappe.enqueue = enqueue_inline
	import frappe.utils.background_jobs as bj
	bj.enqueue = enqueue_inline
	frappe.publish_realtime = lambda *a, **k: None
	import frappe.realtime as rt
	rt.publish_realtime = frappe.publish_realtime


def _self_test(original_sql):
	"""Prove a commit does not reach the server: a real commit destroys savepoints."""
	db = frappe.db
	original_sql(db, "savepoint isoft_hr_probe")
	frappe.db.commit()
	try:
		original_sql(db, "rollback to savepoint isoft_hr_probe")
	except Exception as exc:
		raise ProtectedRunViolation("commit interception is not effective: " + str(exc))
	try:
		frappe.db.sql("create table isoft_hr_probe_never (x int)")
	except ProtectedRunViolation:
		pass
	else:
		raise ProtectedRunViolation("implicit-commit statements are not refused")
	if frappe.cache().connection_pool.connection_kwargs.get("port") == _site_redis_port():
		raise ProtectedRunViolation("cache still points at the site's redis")


def _site_redis_port():
	from urllib.parse import urlparse
	path = os.path.join(frappe.local.sites_path or ".", "common_site_config.json")
	with open(path) as f:
		return urlparse(json.load(f).get("redis_cache") or "").port


def protect(site, private_redis):
	"""Install the protection for this process and prove it works."""
	STATE.stats = {"commits_intercepted": 0, "rollbacks_to_commit_point": 0, "refused": [],
	               "non_transactional_writes_dropped": [], "log_error_captured": [],
	               "jobs_run_inline": []}
	frappe.init(site=site)
	_install_side_channel_protection(private_redis)
	original_sql = _install_database_protection()
	frappe.connect()
	frappe.flags.in_test = True
	frappe.db.sql("rollback")
	_self_test(original_sql)
	STATE.started = frappe.utils.now()
	STATE.active, STATE.site, STATE.verified = True, site, True
	return original_sql


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def _site_files():
	out = {}
	for sub in ("public/files", "private/files"):
		base = os.path.join(frappe.local.site_path, sub)
		for root, _dirs, files in os.walk(base):
			for f in files:
				out[os.path.join(root, f)] = None
	return out


def _remove_new_files(before):
	"""Files the run left on disk. A file a committed File record refers to (another
	user's upload during the run) is kept."""
	frappe.db.sql("rollback")
	removed, kept = [], []
	for path in sorted(set(_site_files()) - set(before)):
		rel = os.path.relpath(path, frappe.local.site_path)
		url = "/" + rel.replace("public/", "", 1) if rel.startswith("public/") else "/" + rel
		if frappe.db.sql("select name from `tabFile` where file_url = %s limit 1", url):
			kept.append(rel)
			continue
		os.remove(path)
		removed.append(rel)
	return removed, kept


def _wrap_class_teardown(suite):
	"""End every test class with a real rollback: nothing durable, locks released."""
	import unittest
	seen = set()

	def walk(s):
		for t in s:
			if isinstance(t, unittest.TestSuite):
				walk(t)
			else:
				cls = type(t)
				if cls in seen:
					continue
				seen.add(cls)
				original = cls.__dict__.get("tearDownClass")

				def make(cls_, orig):
					@classmethod
					def tearDownClass(klass):
						try:
							if orig is not None:
								orig.__func__(klass)
							else:
								super(cls_, klass).tearDownClass()
						finally:
							frappe.db.sql("rollback")
							STATE.has_commit_point = False
					return tearDownClass
				cls.tearDownClass = make(cls, original)
	walk(suite)


def main(argv=None):
	import unittest
	argv = list(argv if argv is not None else sys.argv[1:])
	if len(argv) < 3:
		print(__doc__)
		return 2
	site, private_redis, modules = argv[0], argv[1], argv[2:]
	protect(site, private_redis)
	print("protected run on {0}: commit interception verified, cache/queue/realtime on {1}".format(
		site, private_redis))
	frappe.set_user("Administrator")
	files_before = _site_files()
	suite = unittest.TestSuite()
	loader = unittest.TestLoader()
	for name in modules:
		suite.addTests(loader.loadTestsFromName(name))
	_wrap_class_teardown(suite)
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	frappe.db.sql("rollback")
	removed, kept = _remove_new_files(files_before)
	s = STATE.stats
	print("RESULT", "OK" if result.wasSuccessful() else "FAILED", "ran", result.testsRun,
	      "failures", len(result.failures), "errors", len(result.errors), "skipped", len(result.skipped))
	print("PROTECTION commits_intercepted={0} rollbacks_to_commit_point={1} refused={2} "
	      "non_transactional_writes_dropped={3} log_error_captured={4} jobs_run_inline={5} files_removed={6} "
	      "files_kept={7}".format(
	          s["commits_intercepted"], s["rollbacks_to_commit_point"], len(s["refused"]),
	          len(s["non_transactional_writes_dropped"]), len(s["log_error_captured"]),
	          len(s["jobs_run_inline"]), len(removed), len(kept)))
	for job in sorted(set(s["jobs_run_inline"])):
		print("  job run inline:", job)
	for q in sorted(set(s["refused"])):
		print("  refused:", q)
	for title, msg in sorted(set(s["log_error_captured"]))[:20]:
		print("  log_error (not written):", title, "|", msg[:140])
	for rel in removed:
		print("  file removed:", rel)
	frappe.destroy()
	return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
	# Run through the importable module, not __main__: the tests import testing_guard by
	# its package name, and must see the protection state this run sets up.
	from isoft_angola_hr.isoft_angola_hr.payroll import testing_guard as _guard
	sys.exit(_guard.main())
