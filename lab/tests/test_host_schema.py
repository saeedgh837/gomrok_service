"""Host schema contract (workstream F) — the drift test for the MySQL side.

Proves that host/migrations/001_initial.sql, host/src/Schema.php and the
grants on the local verification database all say the same thing, so the PHP
API and the SQL cannot silently diverge (spec §13.1, §12).

What it covers
  * Schema.php vs information_schema.columns, exactly: no extra column, no
    missing column, no type/nullability/default mismatch.
  * the foreign-key graph, including the rule that every tenant_id-bearing
    table really carries the constraint (spec §7 — no IDOR oracle).
  * least privilege: irica_api may SELECT/INSERT/UPDATE/DELETE and is refused
    DDL (spec §2, §13.1).
  * envelope invariants: change_log.seq monotonic, row_version defaults to 1,
    deleted_at nullable, id CHAR(36) primary key, utf8mb4 + InnoDB everywhere.
  * migration determinism: applies cleanly to an empty database, refuses a
    second application, and two fresh databases fingerprint identically.
  * Schema.php's validator returns structured failures and never echoes a value.
  * Database.php's allowlist: an identifier outside Schema.php throws.

Run:
  "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_host_schema

Offline. Mock/sample rows only — no real customer PII, no real credentials in
the file. The test database password is read from %TEMP%\\irica_test_db.json
(or IRICA_TEST_DB_JSON / IRICA_DB_ROOT_PASSWORD), never hardcoded, never
printed. Nothing is written to %LOCALAPPDATA%\\irica\\.

If PHP or the database is unavailable the whole module skips with an explicit,
printed reason — never silently.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HOST_SRC = os.path.join(ROOT, "host", "src")
MIGRATIONS = os.path.join(ROOT, "host", "migrations")
SCHEMA_SQL = os.path.join(MIGRATIONS, "001_initial.sql")
SCHEMA_PHP = os.path.join(HOST_SRC, "Schema.php")
DATABASE_PHP = os.path.join(HOST_SRC, "Database.php")

# Spec §13.1 — the provisioned local environment.
PHP_CANDIDATES = (
    r"C:\Users\Saeed\AppData\Local\Microsoft\WinGet\Packages"
    r"\PHP.PHP.8.1_Microsoft.Winget.Source_8wekyb3d8bbwe\php.exe",
)
MARIADB_RELATIVE = os.path.join(
    ".tools", "mysql", "mariadb-11.4.4-winx64", "bin", "mariadb.exe")
DB_HOST = "127.0.0.1"
DB_PORT = 3307
DB_NAME = "irica_test"
API_USER = "irica_api"
SCRATCH_DATABASES = ("irica_test_mig_a", "irica_test_mig_b")

# Spec §3 — the settings allowlist, spelled out here so a change to Schema.php
# cannot quietly redefine the contract the client also implements.
SPEC_SETTINGS_ALLOWLIST = (
    "business_name", "business_phone", "watch_interval_hours",
    "scheduler_enabled", "auto_advance_default", "telegram_report_full_data",
    "registry_fee_amount", "registration_chat_id", "report_chat_id",
    "sound_enabled", "current_period_id", "links",
)

# A handful of foreign keys whose absence would break the product, asserted
# literally so a wrong `ref` declaration in Schema.php cannot self-certify.
CRITICAL_FKS = (
    ("customers", "tenant_id", "tenants", "id"),
    ("crm_devices", "customer_id", "customers", "id"),
    ("crm_devices", "owner_id", "owners", "id"),
    ("crm_devices", "period_id", "periods", "id"),
    ("customer_transactions", "customer_id", "customers", "id"),
    ("customer_transactions", "device_id", "crm_devices", "id"),
    ("settings", "current_period_id", "periods", "id"),
    ("memberships", "user_id", "users", "id"),
    ("memberships", "tenant_id", "tenants", "id"),
    ("auth_sessions", "sync_device_id", "sync_devices", "id"),
    ("change_log", "tenant_id", "tenants", "id"),
    ("tenant_state", "tenant_id", "tenants", "id"),
)

EXPECTED_TABLES = 18
EXPECTED_COLUMNS = 237

_scratch = []          # temp directories to remove at the end
_passwords = set()     # secrets that must never appear in any assertion text
_environment_cache = {}
_ensured = {"done": False}


# ---------------------------------------------------------------------------
# Environment discovery
# ---------------------------------------------------------------------------

def _find_php():
    override = os.environ.get("IRICA_PHP")
    if override and os.path.isfile(override):
        return override
    for candidate in PHP_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("php")


def _find_mariadb():
    override = os.environ.get("IRICA_MARIADB")
    if override and os.path.isfile(override):
        return override
    local = os.path.join(ROOT, MARIADB_RELATIVE)
    if os.path.isfile(local):
        return local
    for name in ("mariadb.exe", "mariadb", "mysql"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _credentials():
    """Read the verification database credentials. Never logged, never printed."""
    path = os.environ.get("IRICA_TEST_DB_JSON") or os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
        "irica_test_db.json")
    root = os.environ.get("IRICA_DB_ROOT_PASSWORD")
    api = os.environ.get("IRICA_DB_API_PASSWORD")
    port = DB_PORT
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            return None, None, DB_PORT, "credential file is unreadable: %s" % exc
        root = root or data.get("root_password")
        api = api or data.get("api_password")
        port = int(data.get("port") or DB_PORT)
    if not root:
        return None, None, port, (
            "no root password: set IRICA_DB_ROOT_PASSWORD or provide %s" % path)
    if not api:
        return None, None, port, (
            "no irica_api password: set IRICA_DB_API_PASSWORD or provide %s"
            % path)
    return root, api, port, None


def _environment():
    """None when the suite can run, otherwise a printable reason."""
    if "reason" in _environment_cache:
        return _environment_cache["reason"]
    reason = None
    php = _find_php()
    if not php:
        reason = ("PHP CLI not found (looked at IRICA_PHP, %s, and PATH)"
                  % ", ".join(PHP_CANDIDATES))
    mariadb = _find_mariadb()
    if not reason and not mariadb:
        reason = ("mariadb client not found (expected %s under the repository, "
                  "or IRICA_MARIADB)" % MARIADB_RELATIVE)
    root, api, port, cred_error = _credentials()
    if not reason and cred_error:
        reason = cred_error
    _environment_cache.update({
        "reason": reason, "php": php, "mariadb": mariadb,
        "root": root, "api": api, "port": port,
    })
    return reason


def _php_bin():
    return _environment_cache["php"]


def _mariadb_bin():
    return _environment_cache["mariadb"]


def _root_password():
    return _environment_cache["root"]


def _api_password():
    return _environment_cache["api"]


def _port():
    return _environment_cache["port"]


def _redact(text):
    """Strip every known secret from text destined for an assertion message."""
    if not text:
        return text
    for secret in _passwords:
        if secret:
            text = text.replace(secret, "***")
    return text


# ---------------------------------------------------------------------------
# SQL over the mariadb CLI (no Python MySQL driver — see spec §1)
# ---------------------------------------------------------------------------

def _run_sql(sql, database=DB_NAME, user="root", password=None,
             expect_ok=True):
    """Run SQL through the mariadb CLI and return (stdout, stderr, code).

    The password travels in MYSQL_PWD, never on the command line, so it cannot
    be read out of a process listing or an echoed command. `database=None` is
    for statements that must run before a database exists.
    """
    if password is None:
        password = _root_password()
    command = [
        _mariadb_bin(), "-h", DB_HOST, "-P", str(_port()), "-u", user,
        "--protocol=TCP", "--ssl=FALSE", "--default-character-set=utf8mb4",
        "--batch", "--skip-column-names",
    ]
    if database:
        command.append(database)
    env = dict(os.environ)
    env["MYSQL_PWD"] = password
    process = subprocess.run(
        command, input=sql or "", capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=120,
    )
    stdout = _redact(process.stdout or "")
    stderr = _redact(process.stderr or "")
    if expect_ok and process.returncode != 0:
        raise AssertionError(
            "SQL failed (exit %d):\n%s\n%s" % (
                process.returncode, stderr.strip(), stdout.strip()))
    return stdout, stderr, process.returncode


def _sql_json(sql, database=DB_NAME, password=None):
    """Run a query that returns exactly one JSON document and parse it."""
    wrapped = "SET SESSION group_concat_max_len = 16777216;\n" + sql
    stdout, stderr, code = _run_sql(wrapped, database=database,
                                    password=password, expect_ok=False)
    if code != 0:
        raise AssertionError("query failed: %s" % stderr.strip())
    text = stdout.strip()
    if not text:
        raise AssertionError("query returned no rows: %s" % sql)
    try:
        return json.loads(text.splitlines()[-1])
    except ValueError as exc:
        raise AssertionError(
            "query did not return JSON (%s): %r" % (exc, text[:400]))


def _sql_value(sql, database=DB_NAME, password=None):
    stdout, stderr, code = _run_sql(sql, database=database, password=password,
                                    expect_ok=False)
    if code != 0:
        raise AssertionError("query failed: %s" % stderr.strip())
    return stdout.strip().splitlines()[-1] if stdout.strip() else ""


def _sql_lines(sql, database=DB_NAME, password=None):
    stdout, stderr, code = _run_sql(sql, database=database, password=password,
                                    expect_ok=False)
    if code != 0:
        raise AssertionError("query failed: %s" % stderr.strip())
    return [line for line in stdout.splitlines() if line]


def _normalize_default(value):
    """information_schema quotes string defaults; Schema.php does not."""
    if value is None:
        return None
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _column_fingerprint(database):
    """Canonical JSON view of one database's column definitions."""
    return _sql_json(
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'t', TABLE_NAME, 'c', COLUMN_NAME,"
        "'sql', CASE"
        " WHEN DATA_TYPE IN ('char','varchar')"
        "   THEN CONCAT(DATA_TYPE, '(', CHARACTER_MAXIMUM_LENGTH, ')')"
        " WHEN DATA_TYPE = 'tinyint' THEN COLUMN_TYPE"
        " ELSE DATA_TYPE END,"
        "'null', IS_NULLABLE,"
        "'def', COLUMN_DEFAULT,"
        "'auto', IF(EXTRA LIKE '%%auto_increment%%', 1, 0)"
        ")) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '%s';"
        % database,
        database=database,
    )


def _structure_fingerprint(database):
    """Tables, columns, indexes and foreign keys — migration determinism."""
    tables = _sql_json(
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'name', TABLE_NAME, 'engine', ENGINE, 'collation', TABLE_COLLATION"
        ")) FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = '%s' AND TABLE_TYPE = 'BASE TABLE';" % database,
        database=database,
    )
    columns = _column_fingerprint(database)
    indexes = _sql_json(
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'t', TABLE_NAME, 'n', INDEX_NAME, 'seq', SEQ_IN_INDEX,"
        "'c', COLUMN_NAME, 'u', NON_UNIQUE"
        ")) FROM information_schema.STATISTICS"
        " WHERE TABLE_SCHEMA = '%s' ORDER BY TABLE_NAME, INDEX_NAME,"
        " SEQ_IN_INDEX;" % database,
        database=database,
    )
    fks = _sql_json(
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'t', TABLE_NAME, 'c', COLUMN_NAME,"
        "'rt', REFERENCED_TABLE_NAME, 'rc', REFERENCED_COLUMN_NAME"
        ")) FROM information_schema.KEY_COLUMN_USAGE"
        " WHERE TABLE_SCHEMA = '%s' AND REFERENCED_TABLE_NAME IS NOT NULL"
        " ORDER BY TABLE_NAME, COLUMN_NAME;" % database,
        database=database,
    )
    return {
        "tables": sorted(tables or [], key=lambda r: r["name"]),
        "columns": sorted(columns or [], key=lambda r: (r["t"], r["c"])),
        "indexes": sorted(indexes or [],
                          key=lambda r: (r["t"], r["n"], r["seq"])),
        "foreign_keys": sorted(fks or [],
                               key=lambda r: (r["t"], r["c"], r["rt"], r["rc"])),
    }


# ---------------------------------------------------------------------------
# PHP invocation
# ---------------------------------------------------------------------------

def _php(script, env=None):
    """Run a PHP snippet with both schema classes loaded; return parsed JSON."""
    directory = tempfile.mkdtemp(prefix="irica_php_")
    _scratch.append(directory)
    path = os.path.join(directory, "probe.php")
    body = (
        "<?php\ndeclare(strict_types=1);\n"
        "require '%s';\nrequire '%s';\n"
        % (SCHEMA_PHP.replace("\\", "/"), DATABASE_PHP.replace("\\", "/"))
        + script
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    run_env = dict(os.environ)
    run_env["IRICA_REPO_ROOT"] = ROOT
    if env:
        run_env.update(env)
    process = subprocess.run(
        [_php_bin(), path], capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=run_env, timeout=120,
    )
    stdout = _redact(process.stdout or "")
    stderr = _redact(process.stderr or "")
    if process.returncode != 0:
        raise AssertionError(
            "php failed (exit %d):\n%s" % (process.returncode, stderr.strip()))
    text = stdout.strip()
    try:
        return json.loads(text.splitlines()[-1])
    except ValueError as exc:
        raise AssertionError(
            "php did not output JSON (%s): %s / %s" % (exc, text[:400],
                                                       stderr[:400]))


def _schema_dump():
    """Schema.php as a JSON document, produced by PHP itself."""
    return _php(
        "echo json_encode(array(\n"
        "  'tables' => Schema::tables(),\n"
        "  'entities' => Schema::entities(),\n"
        "  'allowlist' => Schema::settingsAllowlist(),\n"
        "  'refs' => call_user_func(function () {\n"
        "      $refs = array();\n"
        "      foreach (Schema::tables() as $t) {\n"
        "          foreach (Schema::columnsFor($t) as $c) {\n"
        "              $spec = Schema::column($t, $c);\n"
        "              if (!empty($spec['ref'])) {\n"
        "                  $parts = explode('.', $spec['ref']);\n"
        "                  $refs[] = array($t, $c, $parts[0], $parts[1]);\n"
        "              }\n"
        "          }\n"
        "      }\n"
        "      return $refs;\n"
        "  }),\n"
        "  'columns' => call_user_func(function () {\n"
        "      $out = array();\n"
        "      foreach (Schema::tables() as $t) {\n"
        "          foreach (Schema::columnsFor($t) as $c) {\n"
        "              $s = Schema::column($t, $c);\n"
        "              $out[$t][$c] = array(\n"
        "                  'sql' => $s['sql'],\n"
        "                  'nullable' => $s['nullable'] ? 'YES' : 'NO',\n"
        "                  'default' => $s['default'],\n"
        "                  'required' => (bool) $s['required'],\n"
        "              );\n"
        "          }\n"
        "      }\n"
        "      return $out;\n"
        "  }),\n"
        "  'validation' => array(\n"
        "     'unknown_column' => Schema::validate('settings',"
        "        array('secret_token' => 'x', 'business_name' => 'Acme')),\n"
        "     'missing_required' => Schema::validate('customers',"
        "        array('address' => 'Street 1')),\n"
        "     'missing_envelope' => Schema::validateRow('customers',"
        "        array('full_name' => 'Sample Customer')),\n"
        "     'bad_types' => Schema::validate('crm_devices', array(\n"
        "        'name' => 5, 'serial1' => '123', 'stage' => 'archived',"
        "        'auto_advance' => 7)),\n"
        "     'empty_reference' => Schema::validate('crm_devices', array(\n"
        "        'name' => 'n', 'serial1' => '123456789012345',"
        "        'customer_id' => '')),\n"
        "     'bad_timestamp' => Schema::validate('customers', array(\n"
        "        'full_name' => 'n', 'created' => '2026-10-05 10:00:00')),\n"
        "     'unknown_entity' => Schema::validateEntity('profiles',"
        "        array()),\n"
        "  ),\n"
        "));\n"
    )


# ---------------------------------------------------------------------------
# Migration application
# ---------------------------------------------------------------------------

def _database_exists(name):
    out, _err, _code = _run_sql(
        "SELECT COUNT(*) FROM information_schema.SCHEMATA"
        " WHERE SCHEMA_NAME = '%s';" % name, database=None)
    return out.strip() == "1"


def _create_database(name):
    _run_sql("DROP DATABASE IF EXISTS `%s`;" % name, database=None)
    _run_sql("CREATE DATABASE `%s` DEFAULT CHARACTER SET utf8mb4"
             " COLLATE utf8mb4_unicode_ci;" % name, database=None)


def _apply_migration(database, fresh=False):
    """Apply 001_initial.sql to `database`; drop+recreate it when `fresh`.

    Returns (stdout, stderr, returncode) of the CLI apply. The SQL is fed on
    stdin, never as an argument.
    """
    if fresh:
        _create_database(database)
    with open(SCHEMA_SQL, encoding="utf-8") as handle:
        sql = handle.read()
    command = [
        _mariadb_bin(), "-h", DB_HOST, "-P", str(_port()), "-u", "root",
        "--protocol=TCP", "--ssl=FALSE", "--default-character-set=utf8mb4",
        database,
    ]
    env = dict(os.environ)
    env["MYSQL_PWD"] = _root_password()
    process = subprocess.run(
        command, input=sql, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=180,
    )
    return (_redact(process.stdout or ""), _redact(process.stderr or ""),
            process.returncode)


def _ensure_schema():
    """Make sure `irica_test` carries the current 001_initial.sql.

    The database is a disposable verification sandbox, so a database whose
    column count no longer matches Schema.php is rebuilt from the migration
    rather than left to fail the drift test with a stale-by-one-column error.
    """
    if _ensured["done"]:
        return
    if not _database_exists(DB_NAME):
        _run_sql("CREATE DATABASE `%s` DEFAULT CHARACTER SET utf8mb4"
                 " COLLATE utf8mb4_unicode_ci;" % DB_NAME, database=None)
    present = _sql_value(
        "SELECT COUNT(*) FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = 'customers';" % DB_NAME)
    stale = False
    if present == "1":
        declared = sum(len(columns)
                       for columns in _schema_dump()["columns"].values())
        actual = int(_sql_value(
            "SELECT COUNT(*) FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = '%s';" % DB_NAME))
        stale = declared != actual
    else:
        stale = True
    if stale:
        _create_database(DB_NAME)
        _stdout, stderr, code = _apply_migration(DB_NAME)
        if code != 0:
            raise AssertionError(
                "001_initial.sql does not apply to %s: %s" % (DB_NAME, stderr))
    _ensured["done"] = True


def tearDownModule():
    for directory in _scratch:
        shutil.rmtree(directory, ignore_errors=True)
    if _environment():
        return
    for database in SCRATCH_DATABASES:
        _run_sql("DROP DATABASE IF EXISTS `%s`;" % database, database=None,
                 expect_ok=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class HostSchemaTest(unittest.TestCase):
    """Every test here needs PHP and the local MariaDB from spec §13.1."""

    @classmethod
    def setUpClass(cls):
        reason = _environment()
        if reason:
            print("\nHOST SCHEMA SUITE SKIPPED: %s" % reason)
            raise unittest.SkipTest(
                "host schema verification unavailable — %s" % reason)
        _passwords.update(s for s in (_root_password(), _api_password()) if s)

    # -- static agreement between PHP and the database ----------------------

    def test_schema_php_matches_information_schema_exactly(self):
        """The column set in Schema.php IS the column set in the database."""
        _ensure_schema()
        declared = _schema_dump()["columns"]
        actual = {}
        for row in _column_fingerprint(DB_NAME):
            actual.setdefault(row["t"], {})[row["c"]] = row

        self.assertEqual(sorted(declared), sorted(actual),
                         "table list differs between Schema.php and the "
                         "database")

        problems = []
        for table in sorted(declared):
            for column, spec in declared[table].items():
                found = actual[table].get(column)
                if found is None:
                    problems.append("%s.%s: declared in Schema.php, missing "
                                    "from the database" % (table, column))
                    continue
                if spec["sql"] != found["sql"]:
                    problems.append("%s.%s: type Schema.php=%s database=%s"
                                    % (table, column, spec["sql"], found["sql"]))
                if spec["nullable"] != found["null"]:
                    problems.append("%s.%s: nullability Schema.php=%s "
                                    "database=%s" % (table, column,
                                                     spec["nullable"],
                                                     found["null"]))
                expected_default = spec["default"]
                found_default = _normalize_default(found["def"])
                if expected_default is not None:
                    if str(expected_default) != str(found_default):
                        problems.append(
                            "%s.%s: default Schema.php=%r database=%r"
                            % (table, column, expected_default, found_default))
                elif spec["nullable"] == "NO" and found_default is not None:
                    problems.append(
                        "%s.%s: Schema.php declares no default, database has %r"
                        % (table, column, found_default))
            for column in actual[table]:
                if column not in declared[table]:
                    problems.append("%s.%s: present in the database, missing "
                                    "from Schema.php" % (table, column))

        self.assertEqual(problems, [],
                         "Schema.php and information_schema drifted:\n  "
                         + "\n  ".join(problems))

    def test_settings_allowlist_matches_the_spec(self):
        payload = _schema_dump()
        self.assertEqual(tuple(payload["allowlist"]), SPEC_SETTINGS_ALLOWLIST)
        self.assertEqual(len(set(payload["allowlist"])),
                         len(payload["allowlist"]),
                         "the allowlist contains a duplicate key")

    def test_validation_returns_structured_failures_without_values(self):
        """Failures name the column and a stable code — never the value."""
        payload = _schema_dump()["validation"]

        unknown_columns = [(f["table"], f["column"])
                           for f in payload["unknown_column"]
                           if f["code"] == "UNKNOWN_COLUMN"]
        self.assertEqual(unknown_columns, [("settings", "secret_token")],
                         "exactly one key outside the settings allowlist must "
                         "be rejected, by name")
        self.assertNotIn("business_name",
                         [f["column"] for f in payload["unknown_column"]],
                         "an allowlisted key must not be flagged")

        missing = {(f["code"], f["column"])
                   for f in payload["missing_required"]}
        self.assertIn(("MISSING_COLUMN", "full_name"), missing)
        self.assertIn(
            ("MISSING_COLUMN", "id"), missing,
            "the client mints every id, so a record without one must be "
            "rejected on the wire")
        for column in ("tenant_id", "created_at", "updated_at"):
            self.assertNotIn(
                ("MISSING_COLUMN", column), missing,
                "%s is server-minted and must not be demanded of a push"
                % column)

        # validateRow() is the write-side gate: the whole envelope is required.
        envelope = {(f["code"], f["column"])
                    for f in payload["missing_envelope"]}
        for column in ("id", "tenant_id", "created_at", "updated_at"):
            self.assertIn(("MISSING_COLUMN", column), envelope,
                          "validateRow must require %s" % column)
        self.assertNotIn(("MISSING_COLUMN", "full_name"), envelope)

        bad = {(f["column"], f["code"]) for f in payload["bad_types"]}
        self.assertIn(("name", "TYPE_MISMATCH"), bad)
        self.assertIn(("stage", "ENUM_MISMATCH"), bad)
        self.assertIn(("auto_advance", "ENUM_MISMATCH"), bad)

        empty_refs = [(f["code"], f["column"])
                      for f in payload["empty_reference"]
                      if f["code"] == "EMPTY_REFERENCE"]
        self.assertEqual(
            empty_refs, [("EMPTY_REFERENCE", "customer_id")],
            "an empty reference must be reported, not silently stored as ''")

        bad_time = [(f["code"], f["column"])
                    for f in payload["bad_timestamp"]
                    if f["code"] == "INVALID_TIMESTAMP"]
        self.assertEqual(
            bad_time, [("INVALID_TIMESTAMP", "created")],
            "a timestamp without an offset must be rejected")

        self.assertEqual([f["code"] for f in payload["unknown_entity"]],
                         ["UNKNOWN_ENTITY"])

        # Every failure carries only code/table/column: no value can leak.
        for group in payload.values():
            for failure in group:
                self.assertEqual(sorted(failure), ["code", "column", "table"])

    def test_php_sources_lint(self):
        for path in (SCHEMA_PHP, DATABASE_PHP):
            process = subprocess.run(
                [_php_bin(), "-l", path], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
            self.assertEqual(process.returncode, 0,
                             "%s: %s" % (path, process.stderr))

    # -- foreign keys -------------------------------------------------------

    def test_foreign_key_graph_matches_schema_and_tenant_rule(self):
        _ensure_schema()
        declared_payload = _schema_dump()
        declared = {tuple(ref) for ref in declared_payload["refs"]}
        rows = _sql_json(
            "SELECT JSON_ARRAYAGG(JSON_OBJECT("
            "'t', TABLE_NAME, 'c', COLUMN_NAME,"
            "'rt', REFERENCED_TABLE_NAME, 'rc', REFERENCED_COLUMN_NAME"
            ")) FROM information_schema.KEY_COLUMN_USAGE"
            " WHERE TABLE_SCHEMA = '%s' AND REFERENCED_TABLE_NAME IS NOT NULL;"
            % DB_NAME)
        actual = {(r["t"], r["c"], r["rt"], r["rc"]) for r in rows}

        self.assertEqual(
            declared - actual, set(),
            "Schema.php declares foreign keys the database does not have")
        self.assertEqual(
            actual - declared, set(),
            "the database has foreign keys Schema.php does not declare")

        for expected in CRITICAL_FKS:
            self.assertIn(expected, actual,
                          "critical foreign key missing: %s" % (expected,))

        # Every tenant_id-bearing table must really be constrained (spec §7).
        tenant_tables = _sql_json(
            "SELECT JSON_ARRAYAGG(DISTINCT TABLE_NAME)"
            " FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = '%s' AND COLUMN_NAME = 'tenant_id';"
            % DB_NAME)
        self.assertGreaterEqual(len(tenant_tables), 12)
        for table in tenant_tables:
            self.assertIn(
                (table, "tenant_id", "tenants", "id"), actual,
                "%s.tenant_id has no foreign key to tenants.id" % table)

        # Every declared FK target must exist in the schema.
        for table, column, ref_table, ref_column in declared:
            self.assertIn(ref_table, declared_payload["tables"],
                          "%s.%s references unknown table %s"
                          % (table, column, ref_table))
            self.assertIn(ref_column,
                          declared_payload["columns"][ref_table],
                          "%s.%s references unknown column %s.%s"
                          % (table, column, ref_table, ref_column))

    # -- envelope invariants ------------------------------------------------

    def test_envelope_and_cursor_invariants(self):
        _ensure_schema()

        engines = _sql_json(
            "SELECT JSON_ARRAYAGG(JSON_OBJECT("
            "'t', TABLE_NAME, 'e', ENGINE, 'c', TABLE_COLLATION"
            ")) FROM information_schema.TABLES"
            " WHERE TABLE_SCHEMA = '%s' AND TABLE_TYPE = 'BASE TABLE';"
            % DB_NAME)
        bad = [r for r in engines
               if r["e"] != "InnoDB" or not str(r["c"]).startswith("utf8mb4")]
        self.assertEqual(bad, [], "tables not on utf8mb4/InnoDB: %s" % bad)
        self.assertEqual(len(engines), EXPECTED_TABLES,
                         "unexpected table count")

        cursor = _sql_json(
            "SELECT JSON_OBJECT("
            "'extra', EXTRA, 'key', COLUMN_KEY, 'type', DATA_TYPE"
            ") FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = 'change_log'"
            " AND COLUMN_NAME = 'seq';" % DB_NAME)
        self.assertIn("auto_increment", cursor["extra"])
        self.assertEqual(cursor["key"], "PRI")
        self.assertEqual(cursor["type"], "bigint")
        self.assertTrue(_sql_json(
            "SELECT JSON_ARRAYAGG(INDEX_NAME)"
            " FROM information_schema.STATISTICS"
            " WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = 'change_log'"
            " AND COLUMN_NAME = 'seq';" % DB_NAME),
            "change_log.seq is not indexed")

        # Import provenance (spec §8.3): nullable, recorded once at import.
        provenance = _sql_json(
            "SELECT JSON_OBJECT('sql', CONCAT(DATA_TYPE, '(',"
            "  CHARACTER_MAXIMUM_LENGTH, ')'), 'null', IS_NULLABLE)"
            " FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = 'tenant_state'"
            " AND COLUMN_NAME = 'source_installation_id';" % DB_NAME)
        self.assertEqual(provenance["sql"], "char(36)")
        self.assertEqual(provenance["null"], "YES")

        for table in ("customers", "owners", "identities", "crm_devices",
                      "periods", "settings", "customer_transactions"):
            row = _sql_json(
                "SELECT JSON_OBJECT("
                "'rv', (SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS"
                "  WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s'"
                "  AND COLUMN_NAME='row_version'),"
                "'rvn', (SELECT IS_NULLABLE FROM information_schema.COLUMNS"
                "  WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s'"
                "  AND COLUMN_NAME='row_version'),"
                "'dna', (SELECT IS_NULLABLE FROM information_schema.COLUMNS"
                "  WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s'"
                "  AND COLUMN_NAME='deleted_at'),"
                "'id', (SELECT CONCAT(DATA_TYPE,'(',"
                "  CHARACTER_MAXIMUM_LENGTH,')') FROM information_schema.COLUMNS"
                "  WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s'"
                "  AND COLUMN_NAME='id'),"
                "'idk', (SELECT COLUMN_KEY FROM information_schema.COLUMNS"
                "  WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s'"
                "  AND COLUMN_NAME='id')"
                ")" % (DB_NAME, table, DB_NAME, table, DB_NAME, table,
                       DB_NAME, table, DB_NAME, table))
            self.assertEqual(str(row["rv"]), "1",
                             "%s.row_version default" % table)
            self.assertEqual(row["rvn"], "NO",
                             "%s.row_version must not be nullable" % table)
            self.assertEqual(row["dna"], "YES",
                             "%s.deleted_at must be nullable" % table)
            self.assertEqual(row["id"], "char(36)", "%s.id type" % table)
            self.assertEqual(row["idk"], "PRI",
                             "%s.id must be the primary key" % table)

    def test_change_log_seq_is_monotonic_and_defaults_apply(self):
        """Live insert: seq increases, row_version defaults to 1, and a delete
        is a tombstone rather than a physical removal."""
        _ensure_schema()
        tenant = "11111111-2222-4333-8444-555555555555"
        customer = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        try:
            _run_sql(
                "INSERT INTO tenants (id, slug, name, status, created_at,"
                " updated_at) VALUES ('%s', 'schema-probe', 'Schema probe',"
                " 'active', UTC_TIMESTAMP(), UTC_TIMESTAMP());" % tenant)
            _run_sql(
                "INSERT INTO change_log (tenant_id, entity, id, row_version,"
                " action, changed_at) VALUES"
                " ('%s', 'customers', '%s', 1, 'upsert', UTC_TIMESTAMP()),"
                " ('%s', 'customers', '%s', 2, 'upsert', UTC_TIMESTAMP());"
                % (tenant, customer, tenant, customer))
            seqs = [int(v) for v in _sql_lines(
                "SELECT seq FROM change_log WHERE tenant_id = '%s'"
                " ORDER BY seq;" % tenant)]
            self.assertEqual(len(seqs), 2)
            self.assertGreater(seqs[1], seqs[0],
                               "change_log.seq is not monotonic")

            _run_sql(
                "INSERT INTO customers (id, tenant_id, full_name, created_at,"
                " updated_at) VALUES ('%s', '%s', 'Sample Customer',"
                " UTC_TIMESTAMP(), UTC_TIMESTAMP());"
                % (customer, tenant))
            row = _sql_json(
                "SELECT JSON_OBJECT('rv', row_version, 'da', deleted_at,"
                " 'db', deleted_by) FROM customers WHERE id = '%s';"
                % customer)
            self.assertEqual(str(row["rv"]), "1",
                             "row_version did not default to 1")
            self.assertIsNone(row["da"],
                              "a fresh row must not be a tombstone")
            self.assertIsNone(row["db"])

            _run_sql(
                "UPDATE customers SET deleted_at = UTC_TIMESTAMP(),"
                " row_version = row_version + 1 WHERE id = '%s';" % customer)
            self.assertEqual(
                _sql_value("SELECT COUNT(*) FROM customers WHERE id = '%s';"
                           % customer), "1",
                "tombstoning a row must not remove it")
            tombstone = _sql_json(
                "SELECT JSON_OBJECT('da', deleted_at, 'rv', row_version)"
                " FROM customers WHERE id = '%s';" % customer)
            self.assertIsNotNone(tombstone["da"])
            self.assertEqual(str(tombstone["rv"]), "2")
        finally:
            for statement in (
                    "DELETE FROM customer_transactions WHERE tenant_id = '%s';"
                    % tenant,
                    "DELETE FROM change_log WHERE tenant_id = '%s';" % tenant,
                    "DELETE FROM customers WHERE tenant_id = '%s';" % tenant,
                    "DELETE FROM tenants WHERE id = '%s';" % tenant):
                _run_sql(statement, expect_ok=False)

    def test_reference_columns_reject_empty_string(self):
        """FK columns take NULL and refuse '': the contract C-host codes
        against (crm stores '' for "no customer"; SQL needs NULL)."""
        _ensure_schema()
        tenant = "22222222-3333-4444-8555-666666666666"
        device = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        try:
            _run_sql(
                "INSERT INTO tenants (id, slug, name, status, created_at,"
                " updated_at) VALUES ('%s', 'ref-probe', 'Ref probe',"
                " 'active', UTC_TIMESTAMP(), UTC_TIMESTAMP());" % tenant)
            _stdout, stderr, code = _run_sql(
                "INSERT INTO crm_devices (id, tenant_id, name, serial1,"
                " customer_id, created_at, updated_at) VALUES"
                " ('%s', '%s', 'Probe', '123456789012345', '',"
                " UTC_TIMESTAMP(), UTC_TIMESTAMP());" % (device, tenant),
                expect_ok=False)
            self.assertNotEqual(code, 0,
                                "an empty reference must not satisfy the FK")
            self.assertIn("1452", stderr,
                          "expected a referential integrity error, got: %s"
                          % stderr.strip())

            _run_sql(
                "INSERT INTO crm_devices (id, tenant_id, name, serial1,"
                " customer_id, created_at, updated_at) VALUES"
                " ('%s', '%s', 'Probe', '123456789012345', NULL,"
                " UTC_TIMESTAMP(), UTC_TIMESTAMP());" % (device, tenant))
        finally:
            for statement in (
                    "DELETE FROM crm_devices WHERE tenant_id = '%s';" % tenant,
                    "DELETE FROM tenants WHERE id = '%s';" % tenant):
                _run_sql(statement, expect_ok=False)

    # -- least privilege (spec §2 / §13.1) ---------------------------------

    def test_api_account_is_dml_only(self):
        _ensure_schema()
        grants = "".join(_sql_lines(
            "SHOW GRANTS FOR '%s'@'%s';" % (API_USER, DB_HOST)))
        self.assertIn("SELECT, INSERT, UPDATE, DELETE ON `%s`.*" % DB_NAME,
                      grants)
        for forbidden in ("CREATE", "DROP", "ALTER", "TRUNCATE",
                          "REFERENCES", "GRANT OPTION"):
            self.assertNotIn(forbidden, grants,
                             "irica_api must not hold %s: %s"
                             % (forbidden, grants))

        # The same claim, proven against the live server rather than a grant
        # string: DML succeeds, DDL is refused.
        password = _api_password()
        _run_sql("SELECT 1;", database=DB_NAME, user=API_USER, password=password)
        try:
            _run_sql(
                "INSERT INTO login_attempts (account, ip, attempted_at,"
                " outcome) VALUES ('schema-probe', '127.0.0.1',"
                " UTC_TIMESTAMP(), 'failure');",
                database=DB_NAME, user=API_USER, password=password)
            _run_sql(
                "DELETE FROM login_attempts WHERE account = 'schema-probe';",
                database=DB_NAME, user=API_USER, password=password)
        finally:
            _run_sql(
                "DELETE FROM login_attempts WHERE account = 'schema-probe';",
                expect_ok=False)

        _stdout, stderr, code = _run_sql(
            "CREATE TABLE zz_api_ddl_probe (id INT);", database=DB_NAME,
            user=API_USER, password=password, expect_ok=False)
        self.assertNotEqual(code, 0,
                            "irica_api was allowed to create a table")
        self.assertIn("denied", stderr.lower())

        _stdout, stderr, code = _run_sql(
            "DROP TABLE zz_api_ddl_probe;", database=DB_NAME, user=API_USER,
            password=password, expect_ok=False)
        self.assertNotEqual(code, 0,
                            "irica_api was allowed to drop a table")
        self.assertNotEqual(
            _sql_value(
                "SELECT COUNT(*) FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME"
                " = 'zz_api_ddl_probe';" % DB_NAME), "1",
            "a probe table must not exist")

    # -- migration behaviour ------------------------------------------------

    def test_migration_applies_once_and_a_repeat_fails_cleanly(self):
        """Forward-only: empty database -> success; same database again ->
        clean ER_TABLE_EXISTS_ERROR, never a half-applied schema."""
        _stdout, stderr, code = _apply_migration("irica_test_mig_a",
                                                 fresh=True)
        self.assertEqual(code, 0,
                         "001_initial.sql must apply cleanly on an empty "
                         "database, got: %s" % stderr)
        self.assertNotIn("error", stderr.lower(),
                         "a clean apply must not report an error: %s" % stderr)
        self.assertEqual(
            _sql_value(
                "SELECT COUNT(*) FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = 'irica_test_mig_a' AND TABLE_TYPE"
                " = 'BASE TABLE';"),
            str(EXPECTED_TABLES), "unexpected table count after apply")

        _stdout, stderr, code = _apply_migration("irica_test_mig_a")
        self.assertNotEqual(code, 0,
                            "applying 001_initial.sql twice must fail")
        self.assertIn("already exists", stderr.lower())
        self.assertIn("tenants", stderr.lower(),
                      "the repeat must fail at the first business table, "
                      "after the tracker table (got: %s)" % stderr.strip())
        # The tracker table survives the refused re-apply, so a runner can
        # consult it before it ever tries again.
        self.assertEqual(
            _sql_value(
                "SELECT COUNT(*) FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = 'irica_test_mig_a'"
                " AND TABLE_NAME = 'schema_migrations';"),
            "1")

    def test_two_fresh_databases_are_identical(self):
        _apply_migration("irica_test_mig_a", fresh=True)
        _apply_migration("irica_test_mig_b", fresh=True)
        left = _structure_fingerprint("irica_test_mig_a")
        right = _structure_fingerprint("irica_test_mig_b")
        self.assertEqual(
            left, right,
            "001_initial.sql produced different structures on two fresh "
            "databases")
        self.assertEqual(len(left["tables"]), EXPECTED_TABLES)
        self.assertEqual(len(left["columns"]), EXPECTED_COLUMNS)
        self.assertGreater(len(left["indexes"]), EXPECTED_TABLES)
        self.assertGreater(len(left["foreign_keys"]), 30)

    # -- Database.php -------------------------------------------------------

    def test_database_php_helpers_and_identifier_allowlist(self):
        _ensure_schema()
        config = json.dumps({
            "host": DB_HOST, "port": _port(), "dbname": DB_NAME, "user": "root",
            "password": _root_password(),
            "transaction_attempts": 3, "backoff_base_us": 1000,
        })
        payload = _php(
            "$db = Database::fromConfig(json_decode(getenv('IRICA_DB_CONFIG'),"
            " true));\n"
            "$id = '33333333-4444-4555-8666-777777777777';\n"
            "try {\n"
            "  $committed = $db->transaction(function (Database $tx) use ($id) {\n"
            "    $tx->insert('tenants', array(\n"
            "      'id' => $id, 'slug' => 'php-probe', 'name' => 'PHP probe',"
            "      'status' => 'active',\n"
            "      'created_at' => gmdate('c'), 'updated_at' => gmdate('c')));\n"
            "    $row = $tx->queryOne("
            "      'SELECT name FROM tenants WHERE id = ?', array($id));\n"
            "    if (($row['name'] ?? '') !== 'PHP probe') {\n"
            "      throw new RuntimeException('read back failed');\n"
            "    }\n"
            "    $tx->update('tenants', array('name' => 'PHP probe 2'),"
            "      'id = ?', array($id));\n"
            "    return $tx->queryValue("
            "      'SELECT name FROM tenants WHERE id = ?', array($id));\n"
            "  });\n"
            "  $tz = $db->sessionTimeZone();\n"
            "  $version = $db->serverVersion();\n"
            "  $allowlistRejected = false;\n"
            "  try { $db->insert('no_such_table', array('x' => 1)); }\n"
            "  catch (InvalidArgumentException $e) { $allowlistRejected = true; }\n"
            "  $columnRejected = false;\n"
            "  try { $db->insert('tenants', array('secret' => 'x')); }\n"
            "  catch (InvalidArgumentException $e) { $columnRejected = true; }\n"
            "  $rolledBack = false;\n"
            "  try {\n"
            "    $db->transaction(function (Database $tx) {\n"
            "      $tx->insert('tenants', array(\n"
            "        'id' => '44444444-5555-4666-8777-888888888888',"
            "        'slug' => 'rollback-probe', 'name' => 'x',"
            "        'status' => 'active',"
            "        'created_at' => gmdate('c'), 'updated_at' => gmdate('c')));\n"
            "      throw new RuntimeException('boom');\n"
            "    });\n"
            "  } catch (RuntimeException $e) { $rolledBack = true; }\n"
            "  $leftover = (int) $db->queryValue("
            "    \"SELECT COUNT(*) FROM tenants WHERE slug = 'rollback-probe'\");\n"
            "  $db->transaction(function (Database $tx) use ($id) {\n"
            "    $tx->delete('tenants', 'id = ?', array($id));\n"
            "    $tx->delete('tenants', \"slug = 'rollback-probe'\");\n"
            "  });\n"
            "  echo json_encode(array(\n"
            "    'committed' => $committed, 'tz' => $tz,"
            "    'allowlist' => $allowlistRejected,"
            "    'column' => $columnRejected,"
            "    'rolled_back' => $rolledBack, 'leftover' => $leftover,"
            "    'version' => ($version !== ''))); \n"
            "} catch (Throwable $e) {\n"
            "  try {\n"
            "    $db->delete('tenants', 'id = ?', array($id));\n"
            "    $db->delete('tenants', \"slug = 'rollback-probe'\");\n"
            "  } catch (Throwable $ignore) {}\n"
            "  echo json_encode(array('error' => get_class($e) . ': '"
            "    . $e->getMessage()));\n"
            "}\n",
            env={"IRICA_DB_CONFIG": config})
        self.assertNotIn("error", payload, payload.get("error"))
        self.assertEqual(payload["committed"], "PHP probe 2")
        self.assertEqual(payload["tz"], "+00:00",
                         "the session time zone must be UTC")
        self.assertTrue(payload["version"])
        self.assertTrue(payload["allowlist"],
                        "an unknown table must be rejected before any SQL")
        self.assertTrue(payload["column"],
                        "an unknown column must be rejected before any SQL")
        self.assertTrue(payload["rolled_back"],
                        "an exception inside a transaction must be thrown out")
        self.assertEqual(payload["leftover"], 0,
                         "the failed transaction must have rolled back")

        self.assertEqual(
            _sql_value("SELECT COUNT(*) FROM tenants WHERE slug IN"
                       " ('php-probe', 'rollback-probe');"),
            "0", "probe rows were not cleaned up")


if __name__ == "__main__":
    unittest.main()
