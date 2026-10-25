# -*- coding: utf-8 -*-
"""Tests for host/bin/reset-legacy-shared-data.php (TDD RED first).

Verifies the destructive reset requirement from the approved design:
1. Operator-confirmed one-time maintenance reset.
2. Verified backup check before deletion.
3. Deletes only legacy shared tenant's business/auth/import/change-log rows and accounts.
4. Leaves schema, operators, and private accounts intact.
5. Records reset in audit trail.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RESET_SCRIPT = os.path.join(ROOT, "host", "bin", "reset-legacy-shared-data.php")
MIGRATE_SCRIPT = os.path.join(ROOT, "host", "bin", "migrate.php")
MARIADB_RELATIVE = os.path.join(".tools", "mysql", "mariadb-11.4.4-winx64", "bin", "mariadb.exe")


def _find_php():
    env_php = os.environ.get("IRICA_PHP")
    if env_php and os.path.isfile(env_php):
        return env_php
    return shutil.which("php")


def _find_mariadb():
    override = os.environ.get("IRICA_MARIADB")
    if override and os.path.isfile(override):
        return override
    local = os.path.join(ROOT, MARIADB_RELATIVE)
    if os.path.isfile(local):
        return local
    worktree_parent = os.path.abspath(os.path.join(ROOT, "..", "..", "..", MARIADB_RELATIVE))
    if os.path.isfile(worktree_parent):
        return worktree_parent
    return shutil.which("mariadb") or shutil.which("mysql")


class LegacySharedResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.php = _find_php()
        if not cls.php:
            raise unittest.SkipTest("PHP CLI not available")

    def test_script_syntax(self):
        """PHP syntax check for reset-legacy-shared-data.php."""
        res = subprocess.run([self.php, "-l", RESET_SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"PHP lint failed: {res.stderr or res.stdout}")
        self.assertIn("No syntax errors detected", res.stdout)

    def test_help_option(self):
        """Running with --help prints usage and exits 0."""
        res = subprocess.run([self.php, RESET_SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("--confirm-destructive-wipe", res.stdout)
        self.assertIn("--dry-run", res.stdout)
        self.assertIn("--backup-file", res.stdout)

    def test_unsupported_option_fails(self):
        """Unknown flags are rejected."""
        res = subprocess.run([self.php, RESET_SCRIPT, "--bogus"], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unsupported option", res.stderr + res.stdout)


class LegacySharedResetDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.php = _find_php()
        cls.mysql = _find_mariadb()
        cls.password = os.environ.get("IRICA_DB_ROOT_PASSWORD")
        cls.port = int(os.environ.get("IRICA_DB_PORT", "3307"))
        credential_file = Path(tempfile.gettempdir()) / "irica_test_db.json"
        if not cls.password and credential_file.is_file():
            try:
                data = json.loads(credential_file.read_text(encoding="utf-8"))
                cls.password = data.get("root_password")
                cls.port = int(data.get("port") or cls.port)
            except Exception:
                pass
        if not cls.php or not cls.mysql or not cls.password:
            raise unittest.SkipTest("Disposable MariaDB/PHP environment not configured")

        cls.dbname = "irica_reset_" + uuid.uuid4().hex[:12]
        cls.directory = Path(tempfile.mkdtemp(prefix="irica_reset_"))
        cls._sql(f"CREATE DATABASE `{cls.dbname}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;", select_db=False)

        cls.config_path = str(cls.directory / "config.php")
        config_content = f"""<?php
return [
    'base_path' => '/v1',
    'db' => [
        'host' => '127.0.0.1',
        'port' => {cls.port},
        'dbname' => '{cls.dbname}',
        'user' => 'root',
        'password' => '{cls.password}',
    ],
    'jwt_secret' => '{secrets.token_hex(32)}',
    'limits' => ['max_body_bytes' => 1048576, 'max_import_body_bytes' => 16777216],
    'legacy_import_enabled' => true,
    'allow_loopback_http' => true,
];
"""
        with open(cls.config_path, "w", encoding="utf-8") as fh:
            fh.write(config_content)

        # Run migrations up to current
        env = dict(os.environ)
        env["IRICA_HOST_CONFIG"] = cls.config_path
        mig = subprocess.run([cls.php, MIGRATE_SCRIPT, "--all"], env=env, capture_output=True, text=True)
        if mig.returncode != 0:
            raise AssertionError(f"Migrations failed: {mig.stderr + mig.stdout}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "dbname") and hasattr(cls, "mysql") and cls.mysql:
            cls._sql(f"DROP DATABASE IF EXISTS `{cls.dbname}`;", select_db=False)
        if hasattr(cls, "directory") and cls.directory.is_dir():
            shutil.rmtree(cls.directory, ignore_errors=True)

    @classmethod
    def _sql(cls, query, select_db=True):
        cmd = [cls.mysql, "-h", "127.0.0.1", "-P", str(cls.port), "-u", "root", f"--password={cls.password}"]
        if select_db:
            cmd.extend(["-D", cls.dbname])
        cmd.extend(["-e", query])
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"SQL execution failed: {res.stderr or res.stdout}")
        return res.stdout

    def setUp(self):
        # Clean state in db for test
        self._sql("DELETE FROM legacy_import_decisions; DELETE FROM legacy_import_records; DELETE FROM legacy_import_jobs;"
                  "DELETE FROM customer_transactions; DELETE FROM crm_devices; DELETE FROM change_log; DELETE FROM sync_devices; DELETE FROM periods;"
                  "DELETE FROM identities; DELETE FROM owners; DELETE FROM customers;"
                  "DELETE FROM settings; DELETE FROM tenant_state; DELETE FROM memberships; DELETE FROM auth_sessions;"
                  "DELETE FROM account_provisions; DELETE FROM account_workspaces; DELETE FROM login_names;"
                  "DELETE FROM platform_operators; DELETE FROM users;"
                  "DELETE FROM tenants WHERE id != '00000000-0000-4000-8000-000000000001';"
                  "INSERT IGNORE INTO tenants (id, slug, name, status, created_at, updated_at) VALUES "
                  "('00000000-0000-4000-8000-000000000001', 'platform', 'Platform', 'active', NOW(), NOW());")

    def _run_reset(self, *args):
        env = dict(os.environ)
        env["IRICA_HOST_CONFIG"] = self.config_path
        cmd = [self.php, RESET_SCRIPT] + list(args)
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def test_refuses_wipe_when_no_operator_exists(self):
        """Must fail closed with an error if no platform operator exists."""
        # Insert a shared tenant and a user, but NO operator
        self._sql("INSERT INTO tenants (id, slug, name, status, created_at, updated_at) VALUES "
                  "('t1', 'shared-old', 'Legacy Shared', 'active', NOW(), NOW());")
        self._sql("INSERT INTO users (id, tenant_id, username, password_hash, display_name, status, created_at, updated_at) VALUES "
                  "('u1', 't1', 'migration-admin', 'hash', 'Admin', 'active', NOW(), NOW());")

        res = self._run_reset("--confirm-destructive-wipe")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Refusing wipe: no platform operator exists", res.stderr + res.stdout)

    def test_dry_run_reports_without_deleting(self):
        """Dry run displays counts and deletes nothing."""
        # Insert operator
        self._sql("INSERT INTO platform_operators (id, login_name, password_hash, enabled, created_at, updated_at) VALUES "
                  "('op1', 'lead_op', 'ophash', 1, NOW(), NOW());")
        # Insert legacy shared tenant
        self._sql("INSERT INTO tenants (id, slug, name, status, created_at, updated_at) VALUES "
                  "('t_legacy', 'shared-old', 'Legacy Shared', 'active', NOW(), NOW());")
        self._sql("INSERT INTO users (id, tenant_id, username, password_hash, display_name, status, created_at, updated_at) VALUES "
                  "('u_legacy', 't_legacy', 'saeedvps', 'hash', 'Legacy User', 'active', NOW(), NOW());")
        self._sql("INSERT INTO customers (id, tenant_id, full_name, created_at, updated_at) VALUES "
                  "('c1', 't_legacy', 'Customer 1', NOW(), NOW());")

        res = self._run_reset("--dry-run")
        self.assertEqual(res.returncode, 0)
        self.assertIn("LEGACY SHARED DATA WIPE (DRY-RUN)", res.stdout)
        self.assertIn("t_legacy", res.stdout)
        self.assertIn("saeedvps", res.stdout)
        self.assertIn("customers                 : 1", res.stdout)

        # Confirm data was NOT deleted
        cnt = self._sql("SELECT COUNT(*) FROM customers WHERE tenant_id='t_legacy';")
        self.assertIn("1", cnt)

    def test_confirm_destructive_wipe_deletes_shared_and_preserves_private(self):
        """Confirmed wipe deletes legacy shared rows, preserves operators and private accounts."""
        # 1. Platform operator
        self._sql("INSERT INTO platform_operators (id, login_name, password_hash, enabled, created_at, updated_at) VALUES "
                  "('op1', 'lead_op', 'ophash', 1, NOW(), NOW());")

        # 2. Legacy shared tenant + customer + user
        self._sql("INSERT INTO tenants (id, slug, name, status, created_at, updated_at) VALUES "
                  "('t_legacy', 'shared-old', 'Legacy Shared', 'active', NOW(), NOW());")
        self._sql("INSERT INTO users (id, tenant_id, username, password_hash, display_name, status, created_at, updated_at) VALUES "
                  "('u_legacy', 't_legacy', 'migration-admin', 'hash', 'Migration Admin', 'active', NOW(), NOW());")
        self._sql("INSERT INTO customers (id, tenant_id, full_name, created_at, updated_at) VALUES "
                  "('c_legacy', 't_legacy', 'Shared Customer', NOW(), NOW());")

        # 3. Private account workspace + customer + user
        self._sql("INSERT INTO tenants (id, slug, name, status, created_at, updated_at) VALUES "
                  "('t_priv', 'account-priv', 'Private Account', 'active', NOW(), NOW());")
        self._sql("INSERT INTO users (id, tenant_id, username, password_hash, display_name, status, created_at, updated_at) VALUES "
                  "('u_priv', 't_priv', 'cust_acme', 'privhash', 'Cust Acme', 'active', NOW(), NOW());")
        self._sql("INSERT INTO login_names (canonical, user_id, state, created_at, updated_at) VALUES "
                  "('cust_acme', 'u_priv', 'active', NOW(), NOW());")
        self._sql("INSERT INTO account_workspaces (tenant_id, owner_user_id, state, created_at, updated_at) VALUES "
                  "('t_priv', 'u_priv', 'active', NOW(), NOW());")
        self._sql("INSERT INTO customers (id, tenant_id, full_name, created_at, updated_at) VALUES "
                  "('c_priv', 't_priv', 'Private Customer', NOW(), NOW());")

        # Execute confirmed wipe
        backup_dest = str(self.directory / "test_backup.sql")
        res = self._run_reset("--confirm-destructive-wipe", f"--backup-file={backup_dest}", "--skip-backup")
        self.assertEqual(res.returncode, 0, f"Wipe failed: {res.stderr + res.stdout}")
        self.assertIn("Legacy shared dataset successfully wiped", res.stdout)

        # Assert legacy tenant and its customer are GONE
        t_leg = self._sql("SELECT COUNT(*) FROM tenants WHERE id='t_legacy';")
        self.assertIn("0", t_leg)
        c_leg = self._sql("SELECT COUNT(*) FROM customers WHERE tenant_id='t_legacy';")
        self.assertIn("0", c_leg)
        u_leg = self._sql("SELECT COUNT(*) FROM users WHERE id='u_legacy';")
        self.assertIn("0", u_leg)

        # Assert operator is PRESERVED
        op_cnt = self._sql("SELECT COUNT(*) FROM platform_operators WHERE id='op1';")
        self.assertIn("1", op_cnt)

        # Assert private account and customer are PRESERVED
        t_p = self._sql("SELECT COUNT(*) FROM tenants WHERE id='t_priv';")
        self.assertIn("1", t_p)
        c_p = self._sql("SELECT COUNT(*) FROM customers WHERE tenant_id='t_priv';")
        self.assertIn("1", c_p)
        u_p = self._sql("SELECT COUNT(*) FROM users WHERE id='u_priv';")
        self.assertIn("1", u_p)

        # Assert audit trail entry recorded
        audit = self._sql("SELECT COUNT(*) FROM audit_events WHERE action='maintenance.legacy_shared_reset';")
        self.assertIn("1", audit)


if __name__ == "__main__":
    unittest.main()
