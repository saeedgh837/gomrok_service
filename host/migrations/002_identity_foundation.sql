-- ===========================================================================
-- 002_identity_foundation.sql — Phase B central identity schema
--
-- Runs on a database that already carries 001_initial.sql, including one that
-- already holds production rows. This file is therefore strictly ADDITIVE:
-- it creates new tables, adds columns to `tenants`, and adds one unique index
-- to `users`. It never DROPs, never TRUNCATEs, never rewrites an existing
-- value, and never deletes a row.
--
-- Rules for this file (same as 001, plus the Phase B step protocol)
--   * Forward-only. Rollback is a restore from backup (host/INSTALL.md).
--   * Every executable statement carries a stable `-- irica-step: <id>`
--     marker. host/src/Migrations.php records each step in the
--     `migration_steps` journal (started / applied / verified / failed) with
--     the SHA-256 of its step block, so an interrupted run is reconciled from
--     engine metadata instead of being repaired by hand.
--   * DDL is NOT transactional on MySQL or MariaDB. No claim of atomic
--     rollback is made anywhere in the runner.
--   * Postconditions are declared in host/src/Schema.php
--     (Schema::migrationSteps) and compared against normalised
--     information_schema metadata — never against SHOW CREATE TABLE text,
--     which differs between the two engines for an identical logical schema.
--
-- Order that must not be changed
--   s01 adds UNIQUE (tenant_id, id) on `users` BEFORE s14 creates
--   `legacy_account_map`, because InnoDB requires a unique index on the
--   referenced columns of a composite foreign key. s10 creates `accounts`
--   before every table that references it.
--
-- Tenant contract columns
--   Existing tenants are backfilled to active / legacy_v1 / context_version 1
--   by the ALTER defaults themselves, and then swept by the bounded keyset
--   step s06 (cursor persisted in migration_steps.cursor_value). Only after
--   that sweep do s07/s08 flip the column defaults to the v2 values used for
--   newly created workspaces. `status = 'suspended'` stays an independent
--   denial gate and is not renamed, merged or reinterpreted here.
-- ===========================================================================

-- irica-step: s01_users_tenant_id_unique
ALTER TABLE users ADD UNIQUE KEY uq_users_tenant_id (tenant_id, id);

-- irica-step: s02_tenants_add_lifecycle
ALTER TABLE tenants ADD COLUMN lifecycle VARCHAR(16) NOT NULL DEFAULT 'active'
  CHECK (lifecycle IN ('staging', 'active'));

-- irica-step: s03_tenants_add_api_contract
ALTER TABLE tenants ADD COLUMN api_contract VARCHAR(24) NOT NULL DEFAULT 'legacy_v1'
  CHECK (api_contract IN ('legacy_v1', 'authoritative_v2'));

-- irica-step: s04_tenants_add_context_version
ALTER TABLE tenants ADD COLUMN context_version INT NOT NULL DEFAULT 1
  CHECK (context_version >= 1);

-- irica-step: s05_tenants_add_replacement_tenant_id
ALTER TABLE tenants ADD COLUMN replacement_tenant_id CHAR(36) NULL;

-- Bounded keyset backfill. One chunk per execution, cursor persisted after the
-- chunk commits, so a large tenant table is never claimed to fit one
-- transaction and an interrupted sweep resumes where it stopped.
-- irica-step: s06_tenants_backfill_legacy_contract
-- irica-mode: keyset
-- irica-cursor-select: SELECT COALESCE(MAX(irica_batch.id), '') FROM (SELECT `id` FROM `tenants` WHERE `id` > :cursor AND (`lifecycle` <> 'active' OR `api_contract` <> 'legacy_v1' OR `context_version` <> 1) ORDER BY `id` LIMIT 1000) AS irica_batch
UPDATE `tenants`
   SET `lifecycle` = 'active', `api_contract` = 'legacy_v1', `context_version` = 1
 WHERE `id` > :cursor AND `id` <= :next_cursor
   AND (`lifecycle` <> 'active' OR `api_contract` <> 'legacy_v1' OR `context_version` <> 1);

-- irica-step: s07_tenants_lifecycle_default
ALTER TABLE tenants MODIFY COLUMN lifecycle VARCHAR(16) NOT NULL DEFAULT 'staging'
  CHECK (lifecycle IN ('staging', 'active'));

-- irica-step: s08_tenants_api_contract_default
ALTER TABLE tenants MODIFY COLUMN api_contract VARCHAR(24) NOT NULL DEFAULT 'authoritative_v2'
  CHECK (api_contract IN ('legacy_v1', 'authoritative_v2'));

-- irica-step: s09_tenants_replacement_fk
ALTER TABLE tenants ADD CONSTRAINT fk_tenants_replacement
  FOREIGN KEY (replacement_tenant_id) REFERENCES tenants (id);

-- ---------------------------------------------------------------------------
-- Accounts. `login` is ascii_bin on purpose: it is compared byte-for-byte and
-- is never a display value. There is no username-derived mapping to a legacy
-- user anywhere in this schema, and no credential column of any legacy user is
-- copied.
-- ---------------------------------------------------------------------------
-- irica-step: s10_accounts_create
CREATE TABLE accounts (
  id                   CHAR(36)     NOT NULL,
  login                VARCHAR(64)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  password_hash        VARCHAR(255) NOT NULL,
  display_name         VARCHAR(120) NOT NULL DEFAULT '',
  status               VARCHAR(16)  NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'disabled')),
  must_change_password TINYINT(1)   NOT NULL DEFAULT 1
                       CHECK (must_change_password IN (0, 1)),
  security_version     INT          NOT NULL DEFAULT 1
                       CHECK (security_version > 0),
  platform_role        VARCHAR(16)  NOT NULL DEFAULT 'member'
                       CHECK (platform_role IN ('operator', 'member')),
  created_at           DATETIME     NOT NULL,
  updated_at           DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_accounts_login (login)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- irica-step: s11_account_workspace_memberships_create
CREATE TABLE account_workspace_memberships (
  id         CHAR(36)    NOT NULL,
  account_id CHAR(36)    NOT NULL,
  tenant_id  CHAR(36)    NOT NULL,
  role       VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'user')),
  status     VARCHAR(16) NOT NULL DEFAULT 'active'
             CHECK (status IN ('active', 'removed')),
  created_at DATETIME    NOT NULL,
  updated_at DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_awm_account_tenant (account_id, tenant_id),
  KEY ix_awm_tenant_status_account (tenant_id, status, account_id),
  CONSTRAINT fk_awm_account FOREIGN KEY (account_id) REFERENCES accounts (id),
  CONSTRAINT fk_awm_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- irica-step: s12_account_sessions_create
CREATE TABLE account_sessions (
  id                  CHAR(36)     NOT NULL,
  account_id          CHAR(36)     NOT NULL,
  token_hash          CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  purpose             VARCHAR(16)  NOT NULL CHECK (purpose IN ('desktop', 'admin_web')),
  security_version    INT          NOT NULL CHECK (security_version > 0),
  issued_at           DATETIME     NOT NULL,
  idle_expires_at     DATETIME     NOT NULL,
  absolute_expires_at DATETIME     NOT NULL,
  last_activity_at    DATETIME     NOT NULL,
  revoked_at          DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_account_sessions_token_hash (token_hash),
  KEY ix_account_sessions_account_revocation (account_id, revoked_at, absolute_expires_at),
  CONSTRAINT fk_account_sessions_account FOREIGN KEY (account_id) REFERENCES accounts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- irica-step: s13_identity_state_create
CREATE TABLE identity_state (
  id                TINYINT     NOT NULL CHECK (id = 1),
  first_operator_id CHAR(36)    NULL,
  row_version       INT         NOT NULL DEFAULT 1
                    CHECK (row_version > 0),
  PRIMARY KEY (id),
  CONSTRAINT fk_identity_state_operator
    FOREIGN KEY (first_operator_id) REFERENCES accounts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Reviewed legacy linkage. The composite key points at the EXISTING users
-- table so a mapping can only ever name a real (tenant, legacy user) pair.
-- There is no credential copy and no username-derived mapping: linking is a
-- reviewed operation that records who confirmed it, not an inference.
-- ---------------------------------------------------------------------------
-- irica-step: s14_legacy_account_map_create
CREATE TABLE legacy_account_map (
  tenant_id      CHAR(36)    NOT NULL,
  legacy_user_id CHAR(36)    NOT NULL,
  account_id     CHAR(36)    NOT NULL,
  review_id      CHAR(36)    NULL,
  confirmed_at   DATETIME    NULL,
  PRIMARY KEY (tenant_id, legacy_user_id),
  CONSTRAINT fk_lam_tenant_user FOREIGN KEY (tenant_id, legacy_user_id)
    REFERENCES users (tenant_id, id),
  CONSTRAINT fk_lam_account FOREIGN KEY (account_id) REFERENCES accounts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Security lifecycle events. Metadata only: no request payload, no login, no
-- password, no hash, no token, no IP, no national id. Written by the operation
-- that owns the state change, inside the SAME SQL transaction — never through
-- Log::audit(), whose write is swallowed on failure. Failed login attempts
-- stay in the bounded login_attempts rate limiter, not here.
-- ---------------------------------------------------------------------------
-- irica-step: s15_account_security_events_create
CREATE TABLE account_security_events (
  id                 CHAR(36)     NOT NULL,
  actor_account_id   CHAR(36)     NULL,
  subject_account_id CHAR(36)     NULL,
  tenant_id          CHAR(36)     NULL,
  actor_kind         VARCHAR(16)  NOT NULL
                     CHECK (actor_kind IN ('private_cli', 'account', 'system')),
  event_type         VARCHAR(32)  NOT NULL
                     CHECK (event_type IN ('account_created', 'password_reset',
                                           'account_disabled', 'account_enabled',
                                           'legacy_linked', 'membership_assigned',
                                           'membership_removed', 'workspace_frozen',
                                           'login_success', 'logout',
                                           'password_changed')),
  outcome            VARCHAR(16)  NOT NULL DEFAULT 'success'
                     CHECK (outcome = 'success'),
  created_at         DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY ix_ase_subject_time (subject_account_id, created_at),
  KEY ix_ase_tenant_time (tenant_id, created_at),
  CONSTRAINT fk_ase_actor FOREIGN KEY (actor_account_id) REFERENCES accounts (id),
  CONSTRAINT fk_ase_subject FOREIGN KEY (subject_account_id) REFERENCES accounts (id),
  CONSTRAINT fk_ase_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The singleton bootstrap row. `first_operator_id` stays NULL until an
-- operator is seeded; row_version serialises that first write and the
-- last-active-operator protection built on top of it.
-- irica-step: s16_identity_state_seed
INSERT INTO identity_state (id, first_operator_id, row_version) VALUES (1, NULL, 1);
