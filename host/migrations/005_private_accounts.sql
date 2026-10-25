-- ===========================================================================
-- 005_private_accounts.sql
--
-- One private dataset per login, and the operator authority that provisions it.
--
-- Before this file the host had exactly one tenant chosen by
-- `config.php: tenant_slug`, so every login saw the same CRM data. That is now
-- a defect. Isolation reuses the tenant boundary the whole schema already
-- carries, instead of adding a per-row owner column that every query would
-- have to remember.
--
-- Vocabulary
--   tenant               the dataset boundary (unchanged, 001_initial.sql)
--   account workspace    the metadata that makes one tenant a private account:
--                        exactly one owner, one lifecycle state
--   legacy tenant        a tenant with NO account_workspaces row. It is not
--                        login-capable. Absence denies — the fail-closed
--                        default for the pre-existing shared dataset.
--   platform operator    the account-management authority, a SEPARATE
--                        credential from any tenant admin
--
-- Safety rules (same as 001-004)
--   * forward-only, additive: no DROP, no destructive rewrite of a live table
--   * the raw legacy upload is never stored; only a digest and the normalized
--     staging rows the 002 tables already define
--   * every tenant-bearing table keeps a real FK to tenants
--   * no seeded credential of any kind: create the operator with
--     bin/create-operator.php, never from a default in this file
-- ===========================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';

-- ---------------------------------------------------------------------------
-- Private account metadata
--
-- A workspace row is what makes a tenant login-capable. `owner_user_id` is
-- immutable: an account never changes hands, and a replacement account gets a
-- replacement tenant. The UNIQUE key makes "one owner, one account" structural
-- rather than a rule a code path could forget.
-- ---------------------------------------------------------------------------
ALTER TABLE users ADD UNIQUE KEY uq_users_identity_tenant (id, tenant_id);

CREATE TABLE account_workspaces (
  tenant_id     CHAR(36)    NOT NULL,
  owner_user_id CHAR(36)    NOT NULL,
  state         VARCHAR(16) NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'active', 'suspended')),
  created_at    DATETIME    NOT NULL,
  updated_at    DATETIME    NOT NULL,
  PRIMARY KEY (tenant_id),
  UNIQUE KEY uq_account_workspaces_owner (owner_user_id),
  KEY ix_account_workspaces_state (state),
  CONSTRAINT fk_account_workspaces_tenant
    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_account_workspaces_owner
    FOREIGN KEY (owner_user_id, tenant_id) REFERENCES users (id, tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- A single-statement trigger also works with the hosting migration splitter.
-- An attempted reassignment violates NOT NULL instead of silently changing owner.
CREATE TRIGGER trg_account_workspaces_identity_immutable
BEFORE UPDATE ON account_workspaces
FOR EACH ROW
SET NEW.owner_user_id = IF(NEW.owner_user_id = OLD.owner_user_id
  AND NEW.tenant_id = OLD.tenant_id, OLD.owner_user_id, NULL);

-- ---------------------------------------------------------------------------
-- Global login-name registry
--
-- `users.username` is unique only per tenant, so it cannot answer "who owns
-- this login name". This registry can: `canonical` is the primary key, so two
-- concurrent provisions of the same name cannot both win.
--
--   state='reserved'  a live name; user_id names its account
--   state='active'    the account was published
--   state='blocked'   a name that must never resolve to an account. A legacy
--                     collision (two users whose names differ only by case)
--                     is blocked rather than merged: picking one silently is
--                     how a login ends up in somebody else's dataset.
--
-- user_id is NULL exactly when the name is blocked. The UNIQUE key still
-- allows many NULLs (both MySQL and MariaDB treat NULLs as distinct), so no
-- special case is needed.
-- ---------------------------------------------------------------------------
CREATE TABLE login_names (
  canonical  VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id    CHAR(36)    NULL,
  state      VARCHAR(16) NOT NULL DEFAULT 'reserved'
             CHECK (state IN ('reserved', 'active', 'blocked')),
  created_at DATETIME    NOT NULL,
  updated_at DATETIME    NOT NULL,
  PRIMARY KEY (canonical),
  UNIQUE KEY uq_login_names_user (user_id),
  KEY ix_login_names_state (state),
  CONSTRAINT fk_login_names_user
    FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Platform operators
--
-- Deliberately NOT a row in `users` and NOT a membership role: a tenant admin
-- is a customer, an operator provisions customers. They therefore have their
-- own credential, their own session table and their own token audience, and no
-- tenant-admin password reset can reach them.
--
-- `user_id` is optional audit attribution only. It grants nothing, so linking
-- it cannot turn a customer account into an operator.
-- ---------------------------------------------------------------------------
CREATE TABLE platform_operator_state (
  id                 TINYINT UNSIGNED NOT NULL,
  bootstrap_consumed TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- This existing row serializes CLI creation and the one-shot web bootstrap.
-- Disabling or removing operators never reopens bootstrap.
INSERT INTO platform_operator_state (id, bootstrap_consumed) VALUES (1, 0);

CREATE TABLE platform_operators (
  id            CHAR(36)     NOT NULL,
  login_name    VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  user_id       CHAR(36)     NULL,
  enabled       TINYINT(1)   NOT NULL DEFAULT 1,
  token_version INT          NOT NULL DEFAULT 1,
  last_login_at DATETIME     NULL,
  created_at    DATETIME     NOT NULL,
  updated_at    DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_platform_operators_login (login_name),
  KEY ix_platform_operators_enabled (enabled),
  CONSTRAINT fk_platform_operators_user
    FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Operator sessions are one-time-use refresh families, exactly like
-- auth_sessions, but they live apart so a customer token check can never read
-- one and an operator token can never satisfy a CRM route.
CREATE TABLE operator_sessions (
  id                  CHAR(36)    NOT NULL,
  operator_id         CHAR(36)    NOT NULL,
  family_id           CHAR(36)    NOT NULL,
  token_hash          CHAR(64)    NOT NULL,
  parent_id           CHAR(36)    NULL,
  issued_at           DATETIME    NOT NULL,
  idle_expires_at     DATETIME    NOT NULL,
  absolute_expires_at DATETIME    NOT NULL,
  used_at             DATETIME    NULL,
  revoked_at          DATETIME    NULL,
  revoked_reason      VARCHAR(64) NOT NULL DEFAULT '',
  rotated_to          CHAR(36)    NULL,
  created_at          DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_operator_sessions_token_hash (token_hash),
  KEY ix_operator_sessions_family (family_id),
  KEY ix_operator_sessions_operator (operator_id, issued_at),
  KEY ix_operator_sessions_expiry (absolute_expires_at),
  CONSTRAINT fk_operator_sessions_operator
    FOREIGN KEY (operator_id) REFERENCES platform_operators (id),
  CONSTRAINT fk_operator_sessions_parent
    FOREIGN KEY (parent_id) REFERENCES operator_sessions (id),
  CONSTRAINT fk_operator_sessions_rotated_to
    FOREIGN KEY (rotated_to) REFERENCES operator_sessions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Provisioning jobs
--
-- One row per "create this account from this file". The row IS the idempotency
-- key: repeating the same provision id returns the stored outcome instead of
-- minting a second account or resetting the password.
--
-- The target is resolved by the server and stored here; a request body can
-- therefore never redirect an import at somebody else's tenant.
-- ---------------------------------------------------------------------------
CREATE TABLE account_provisions (
  id                CHAR(36)     NOT NULL,
  operator_id       CHAR(36)     NOT NULL,
  tenant_id         CHAR(36)     NOT NULL,
  owner_user_id     CHAR(36)     NOT NULL,
  source_sha256     CHAR(64)     NOT NULL,
  source_version    VARCHAR(32)  NOT NULL DEFAULT '',
  original_filename VARCHAR(255) NOT NULL DEFAULT '',
  legacy_job_id     CHAR(36)     NULL,
  state             VARCHAR(24)  NOT NULL DEFAULT 'reserved'
                    CHECK (state IN (
                      'reserved', 'analyzed', 'reviewed', 'applying',
                      'active', 'cancelled', 'failed'
                    )),
  error_code        VARCHAR(40)  NOT NULL DEFAULT '',
  created_at        DATETIME     NOT NULL,
  updated_at        DATETIME     NOT NULL,
  applied_at        DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_account_provisions_owner (owner_user_id),
  KEY ix_account_provisions_state (state, created_at),
  KEY ix_account_provisions_operator (operator_id, created_at),
  CONSTRAINT fk_account_provisions_operator
    FOREIGN KEY (operator_id) REFERENCES platform_operators (id),
  CONSTRAINT fk_account_provisions_tenant
    FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_account_provisions_owner
    FOREIGN KEY (owner_user_id) REFERENCES users (id),
  CONSTRAINT fk_account_provisions_legacy_job
    FOREIGN KEY (legacy_job_id) REFERENCES legacy_import_jobs (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Platform tenant for operator-level audit rows
--
-- audit_events.tenant_id is NOT NULL with a real FK, and an operator login has
-- no customer tenant. Instead of weakening that constraint, operator actions
-- are recorded against this reserved tenant. It deliberately has no
-- account_workspaces row and no users, so it can never be a dataset a login
-- resolves to.
-- ---------------------------------------------------------------------------
INSERT INTO tenants (id, slug, name, status, created_at, updated_at)
VALUES ('00000000-0000-4000-8000-000000000001', 'platform',
        'Platform', 'active', UTC_TIMESTAMP(), UTC_TIMESTAMP());

-- ---------------------------------------------------------------------------
-- Reserve the login names that already exist
--
-- Every surviving name becomes non-reusable, but a collision is blocked rather
-- than resolved: MIN(id) would pick one account and silently hand the other
-- one's data to whoever logged in with that name. Blocked names resolve to
-- nobody, which is the safe answer.
--
-- Match PHP's trim characters before applying the strict ASCII login policy.
-- Replacing edge controls with spaces also keeps interior controls invalid.
-- Invalid legacy names cannot be used to log in and do not enter the registry.
-- One grouped insert blocks canonical collisions without selecting an owner.
-- ---------------------------------------------------------------------------
INSERT INTO login_names (canonical, user_id, state, created_at, updated_at)
SELECT LOWER(trimmed) COLLATE utf8mb4_bin AS canonical,
       CASE WHEN COUNT(*) = 1 THEN MIN(id) ELSE NULL END,
       CASE WHEN COUNT(*) = 1 THEN 'reserved' ELSE 'blocked' END,
       UTC_TIMESTAMP(), UTC_TIMESTAMP()
  FROM (
    SELECT id, TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
             username, CHAR(0), ' '), CHAR(9), ' '), CHAR(10), ' '),
             CHAR(11), ' '), CHAR(13), ' ')) AS trimmed
      FROM users
  ) AS legacy_names
 -- Validate original bytes before Unicode LOWER can turn a non-ASCII name into ASCII.
 WHERE BINARY trimmed REGEXP '^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$'
 GROUP BY canonical;
