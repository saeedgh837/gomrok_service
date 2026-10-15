"""Legacy administrator CLI regressions using disposable, offline SQLite.

Run: python lab/scripts/run_panel_tests.py test_create_admin
The real CLI, Config, Auth, Schema and Database SQL/writes run in a temporary
host tree. Only PDO connection/session setup and MySQL's FOR UPDATE syntax
are adapted. PHP uses -n and only pdo_sqlite: no real config, MySQL or network.
Credentials are generated in memory and passed through environment variables,
never argv, committed fixtures or assertion output.
"""
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PHP_CANDIDATES = (
    Path(r"C:\Users\Saeed\AppData\Local\Microsoft\WinGet\Packages"
         r"\PHP.PHP.8.1_Microsoft.Winget.Source_8wekyb3d8bbwe\php.exe"),
)
CLASSES = ("Schema", "Database", "Http", "Ids", "Config", "Auth", "Roles", "Tokens")
STAMP = "2020-01-01 00:00:00"

# All columns touched by the CLI, including identity uniqueness and FKs. NOCASE
# deliberately makes SQL accept ASCII case aliases; the CLI must still require
# an exact username, as it must with MySQL's case-insensitive collation.
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE tenants (
    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE users (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    username TEXT NOT NULL COLLATE NOCASE, password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL, must_change_password INTEGER NOT NULL,
    status TEXT NOT NULL, token_version INTEGER NOT NULL, last_login_at TEXT,
    row_version INTEGER NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, deleted_at TEXT, deleted_by TEXT
);
CREATE UNIQUE INDEX uq_users_tenant_username ON users(tenant_id, username);
CREATE TABLE memberships (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, user_id)
);
CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
    user_id TEXT NOT NULL REFERENCES users(id), revoked_at TEXT,
    revoked_reason TEXT NOT NULL DEFAULT ''
);
"""

PDO_ADAPTER = r"""<?php
class FixturePdo extends PDO
{
    public function exec(string $statement): int|false
    {
        if ($statement === "SET time_zone = '+00:00'"
            || $statement === 'SET SESSION innodb_lock_wait_timeout = 5') {
            return 0;
        }
        return parent::exec($statement);
    }

    public function prepare(string $query, array $options = []): PDOStatement|false
    {
        return parent::prepare(preg_replace('/ FOR UPDATE$/', '', $query), $options);
    }
}
"""

CONFIG = """<?php
return [
    'tenant_slug' => 'default',
    'db' => [
        'host' => 'fixture.invalid', 'dbname' => 'disposable', 'user' => 'fixture',
        'password' => getenv('IRICA_FIXTURE_DB_PASSWORD'),
    ],
    'jwt_secret' => getenv('IRICA_FIXTURE_JWT_SECRET'),
];
"""


class CreateAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        php = os.environ.get("IRICA_PHP") or shutil.which("php")
        if not php:
            php = next((str(path) for path in PHP_CANDIDATES if path.is_file()), None)
        if not php:
            raise unittest.SkipTest("PHP CLI is unavailable")
        executable = Path(php).resolve()
        cls.php = [str(executable), "-n"]
        extension = executable.parent / "ext" / "php_pdo_sqlite.dll"
        if extension.is_file():
            cls.php += ["-d", "extension_dir=" + str(extension.parent),
                        "-d", "extension=php_pdo_sqlite.dll"]
        else:
            cls.php += ["-d", "extension=pdo_sqlite"]
        probe = subprocess.run(
            cls.php + ["-r", "print(json_encode(PDO::getAvailableDrivers()));"],
            capture_output=True, text=True, timeout=10, cwd=ROOT,
        )
        if probe.stderr or probe.returncode != 0 or probe.stdout.strip() != '["sqlite"]':
            raise unittest.SkipTest("PHP with only pdo_sqlite is unavailable")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="irica_admin_cli_")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        host = self.directory / "host"
        (host / "bin").mkdir(parents=True)
        (host / "src").mkdir()
        shutil.copyfile(ROOT / "host/bin/create-admin.php", host / "bin/create-admin.php")
        for name in CLASSES:
            source = (ROOT / "host/src" / (name + ".php")).read_text(encoding="utf-8")
            if name == "Database":
                constructor = "$pdo = new \\PDO($dsn, $user, $password, $options);"
                self.assertEqual(source.count(constructor), 1,
                                 "Update the fixture adapter for the PDO factory")
                source = source.replace(constructor,
                    "$pdo = new FixturePdo('sqlite:' . getenv('IRICA_FIXTURE_DB'), null, null, $options);", 1)
            (host / "src" / (name + ".php")).write_text(source, encoding="utf-8")
        self.cli = host / "bin/create-admin.php"
        self.adapter = self.directory / "pdo-adapter.php"
        self.adapter.write_text(PDO_ADAPTER, encoding="utf-8")
        config = self.directory / "config.php"
        config.write_text(CONFIG, encoding="utf-8")
        self.password = secrets.token_urlsafe(24)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("IRICA_")}
        self.env.update({
            "IRICA_HOST_CONFIG": str(config),
            "IRICA_FIXTURE_DB": str(self.directory / "fixture.sqlite"),
            "IRICA_FIXTURE_DB_PASSWORD": secrets.token_urlsafe(24),
            "IRICA_FIXTURE_JWT_SECRET": secrets.token_hex(32),
            "IRICA_ADMIN_PASSWORD": self.password,
        })
        self.db = sqlite3.connect(self.env["IRICA_FIXTURE_DB"])
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def tenant(self, tenant_id="tenant-a", slug="default"):
        self.db.execute("INSERT INTO tenants VALUES (?, ?, ?, ?, ?, ?)",
                        (tenant_id, slug, "Fixture", "active", STAMP, STAMP))
        self.db.commit()

    def account(self, user_id, username, tenant_id="tenant-a", role="admin",
                membership_tenant=None):
        self.db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, tenant_id, username, "unusable fixture hash",
                         "Fixture", 0, "active", 7, None, 1, STAMP, STAMP, None, None))
        self.db.execute("INSERT INTO memberships VALUES (?, ?, ?, ?, ?, ?)",
                        ("membership-" + user_id, membership_tenant or tenant_id,
                         user_id, role, STAMP, STAMP))
        self.db.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, NULL, '')",
                        ("session-" + user_id, tenant_id, user_id))
        self.db.commit()

    def snapshot(self):
        return {table: self.db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
                for table in ("tenants", "users", "memberships", "auth_sessions")}

    def run_cli(self, *args):
        result = subprocess.run(
            self.php + ["-d", "auto_prepend_file=" + str(self.adapter), str(self.cli), *args],
            env=self.env, cwd=self.directory, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
        output = result.stdout + result.stderr
        for secret in (self.password, self.env["IRICA_FIXTURE_DB_PASSWORD"],
                       self.env["IRICA_FIXTURE_JWT_SECRET"]):
            self.assertTrue(secret not in output, "CLI printed a supplied credential")
        if result.returncode == 0:
            self.assertTrue(not result.stderr, "Successful CLI emitted diagnostics")
        return result

    def assert_refused_unchanged(self, *args):
        before = self.snapshot()
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 1, "CLI must refuse an unsafe target")
        self.assertIn("FAIL", result.stderr)
        self.assertTrue(before == self.snapshot(), "Refusal changed fixture rows")

    def assert_password_valid(self, user_id):
        # Only a boolean leaves PHP; neither the hash nor the password is logged.
        result = subprocess.run(
            self.php + ["-r", """
                $pdo = new PDO('sqlite:' . getenv('IRICA_FIXTURE_DB'));
                $stmt = $pdo->prepare('SELECT password_hash FROM users WHERE id = ?');
                $stmt->execute([getenv('IRICA_FIXTURE_USER')]);
                print(json_encode(password_verify(getenv('IRICA_ADMIN_PASSWORD'), $stmt->fetchColumn())));
            """],
            env={**self.env, "IRICA_FIXTURE_USER": user_id}, cwd=self.directory,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "true", "Target password was not rotated")

    def test_named_reset_rotates_only_the_requested_admin(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.account("admin-b", "bob")
        before = self.snapshot()
        result = self.run_cli("--username=bob", "--reset-password")
        self.assertEqual(result.returncode, 0, "Named reset did not succeed")
        after = self.snapshot()
        self.assertTrue(before["users"][0] == after["users"][0], "Other admin changed")
        self.assertTrue(before["auth_sessions"][0] == after["auth_sessions"][0],
                        "Other admin's session changed")
        self.assertTrue(before["memberships"][0] == after["memberships"][0],
                        "Other admin's membership changed")
        self.assertEqual(self.db.execute("SELECT username, token_version, must_change_password"
                                        " FROM users WHERE id = 'admin-b'").fetchone(),
                         ("bob", 8, 1))
        session = self.db.execute("SELECT revoked_at, revoked_reason FROM auth_sessions"
                                  " WHERE user_id = 'admin-b'").fetchone()
        self.assertIsNotNone(session[0])
        self.assertEqual(session[1], "password_rotated")
        self.assert_password_valid("admin-b")

    def test_missing_named_admin_cannot_reset_or_rename_another(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.account("admin-b", "bob")
        self.assert_refused_unchanged("--username=missing", "--reset-password")

    def test_missing_tenant_cannot_be_seeded_by_a_reset(self):
        self.assert_refused_unchanged("--username=alice", "--reset-password")

    def test_reset_in_an_empty_tenant_cannot_seed_an_admin(self):
        self.tenant()
        self.assert_refused_unchanged("--username=alice", "--reset-password")

    def test_non_admin_target_is_refused(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.account("member", "bob", role="user")
        self.assert_refused_unchanged("--username=bob", "--reset-password")

    def test_same_username_in_another_tenant_is_not_a_target(self):
        self.tenant()
        self.tenant("tenant-b", "other")
        self.account("admin-a", "alice")
        self.account("admin-b", "bob", tenant_id="tenant-b")
        self.assert_refused_unchanged("--username=bob", "--reset-password")

    def test_foreign_identity_with_a_local_membership_is_not_a_target(self):
        self.tenant()
        self.tenant("tenant-b", "other")
        self.account("admin-b", "bob", tenant_id="tenant-b", membership_tenant="tenant-a")
        self.assert_refused_unchanged("--username=bob", "--reset-password")

    def test_case_alias_is_not_an_exact_username(self):
        self.tenant()
        self.account("admin-a", "Alice")
        self.assert_refused_unchanged("--username=alice", "--reset-password")

    def test_ambiguous_named_target_is_refused(self):
        self.tenant()
        # Deliberately corrupt uniqueness to prove fail-closed behavior, not
        # a supported production schema or an extra seed path.
        self.db.execute("DROP INDEX uq_users_tenant_username")
        self.account("admin-a", "alice")
        self.account("admin-b", "alice")
        self.assert_refused_unchanged("--username=alice", "--reset-password")

    def test_default_reset_does_not_fall_back_to_a_differently_named_admin(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.assert_refused_unchanged("--reset-password")

    def test_default_reset_targets_only_the_default_username(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.account("admin-b", "admin")
        result = self.run_cli("--reset-password")
        self.assertEqual(result.returncode, 0, "Default named target did not rotate")
        self.assertEqual(self.db.execute("SELECT username, token_version FROM users"
                                        " WHERE id = 'admin-a'").fetchone(), ("alice", 7))
        self.assert_password_valid("admin-b")

    def test_first_bootstrap_creates_a_single_default_admin(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, "First bootstrap did not succeed")
        tenant_id, slug = self.db.execute("SELECT id, slug FROM tenants").fetchone()
        user_id, user_tenant, username, version, must_change = self.db.execute(
            "SELECT id, tenant_id, username, token_version, must_change_password FROM users"
        ).fetchone()
        self.assertEqual((slug, user_tenant, username, version, must_change),
                         ("default", tenant_id, "admin", 1, 1))
        self.assertEqual(self.db.execute("SELECT tenant_id, user_id, role FROM memberships").fetchall(),
                         [(tenant_id, user_id, "admin")])
        self.assert_password_valid(user_id)

    def test_named_bootstrap_uses_an_existing_empty_tenant(self):
        self.tenant()
        result = self.run_cli("--username=alice")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.db.execute("SELECT tenant_id, username FROM users").fetchall(),
                         [("tenant-a", "alice")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tenants").fetchone(), (1,))

    def test_bootstrap_refuses_a_second_seed_even_with_a_different_username(self):
        self.tenant()
        self.account("admin-a", "alice")
        self.assert_refused_unchanged("--username=bob")

    def test_bootstrap_remains_scoped_to_the_selected_tenant(self):
        self.tenant("tenant-b", "other")
        self.account("admin-b", "bob", tenant_id="tenant-b")
        result = self.run_cli("--username=alice")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tenants").fetchone(), (2,))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM users").fetchone(), (2,))


if __name__ == "__main__":
    unittest.main()
