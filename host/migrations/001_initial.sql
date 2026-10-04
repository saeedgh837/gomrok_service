-- ===========================================================================
-- 001_initial.sql — IRICA cloud-sync host schema (workstream F)
--
-- Contract: lab/docs/CLOUD_SYNC_SPEC.md (§3 scope, §4 envelope, §7 roles,
-- §8 bootstrap, §10 security). This file is the schema both the PHP API and
-- the Python mapper compile against; host/src/Schema.php declares the same
-- structure and lab/tests/test_host_schema.py proves the two never drift.
--
-- Rules for this file
--   * Forward-only: no DROP, no ALTER of an applied object, no down.sql.
--     Rollback is a restore from backup (spec §10.10).
--   * utf8mb4 + InnoDB everywhere: InnoDB is required for row locks
--     (SELECT ... FOR UPDATE on tenant_state, spec §8.4) and for foreign keys.
--   * Every timestamp is UTC. The session time zone is pinned to '+00:00' so
--     that any expression the runner adds later cannot silently become local
--     time. The application (spec §2) is what mints server timestamps.
--   * Deletes are tombstones: deleted_at / deleted_by on every business row,
--     never a physical DELETE (spec §4).
--   * Only schema_migrations uses CREATE TABLE IF NOT EXISTS. Everything else
--     assumes an EMPTY database, so a second application of this file fails
--     cleanly at `tenants` with ER_TABLE_EXISTS_ERROR instead of half-creating
--     a schema. The runner (host/bin, workstream C-host) must therefore read
--     schema_migrations FIRST:
--         version present + checksum == sha256(file)  -> skip   (no-op)
--         version present + checksum != sha256(file)  -> refuse (hard error)
--         version absent                              -> apply, then record
--     This file never writes its own tracker row: the checksum it would have
--     to store is the checksum of this file, which cannot be known from
--     inside it. The runner computes it after a successful apply.
--
-- Column vocabulary
--   Envelope (spec §4), identical on every synced table:
--       id CHAR(36)          server UUID (uuid4, or uuid5 for migrated rows)
--       legacy_id VARCHAR(64) the record's original id from crm_store.json
--       tenant_id            always FK -> tenants.id
--       row_version          INT NOT NULL DEFAULT 1 (CAS target, spec §6.1)
--       created_at/updated_at  UTC, server-minted (spec §2)
--       deleted_at/deleted_by  tombstone (spec §4)
--   `legacy_id` is NULL for rows that were born on the host. Two entities have
--   no 8-hex id of their own; their legacy ids are synthetic and MUST be built
--   identically on both sides so the uuid5 mapping agrees:
--       customer_transactions -> "<customer legacy_id>/<seq>"
--       settings              -> "settings/<key>"
--   Both are produced client-side as
--       uuid5(NAMESPACE_URL, "<source_installation_id>/<entity>/<legacy_id>")
--   and this column stores the legacy_id *input* (8-64 chars), never the
--   derived UUID.
--
-- Columns that are deliberately DANGLING (spec §1 non-goals)
--   crm_devices.profile_id       points at profiles.json          -> local only
--   customer_transactions.image  bare file name under tx_artifacts -> local only
--   Both are synced as opaque strings on purpose: no foreign key, no content
--   validation, no expectation that the referenced file exists on any other
--   machine. Never add an FK to either.
-- ===========================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';

-- ---------------------------------------------------------------------------
-- Migration tracker
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     VARCHAR(64)  NOT NULL,
  description VARCHAR(255) NOT NULL DEFAULT '',
  checksum    CHAR(64)     NOT NULL,
  applied_at  DATETIME     NOT NULL,
  applied_by  VARCHAR(64)  NOT NULL DEFAULT '',
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Tenancy and identity (spec §7)
--
-- One shared tenant is the product decision (spec §7); the table exists so
-- every tenant_id FK is real and IDOR is impossible to introduce by accident.
-- An id from another tenant must 404 (spec §7), which the FK graph makes
-- structurally true rather than a code review hope.
-- ---------------------------------------------------------------------------
CREATE TABLE tenants (
  id         CHAR(36)     NOT NULL,
  slug       VARCHAR(64)  NOT NULL,
  name       VARCHAR(120) NOT NULL DEFAULT '',
  status     VARCHAR(16)  NOT NULL DEFAULT 'active'
             CHECK (status IN ('active', 'suspended')),
  created_at DATETIME     NOT NULL,
  updated_at DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tenants_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Login identities. `password_hash` holds Argon2id (spec §10.4); no seeded
-- user, no seeded hash, no default password exists anywhere in this file
-- (spec §8.1 — bin/create-admin.php picks an install-time password).
-- The role lives on memberships, not here: role is a property of the
-- user-tenant pair, and a second column would be a second source of truth.
CREATE TABLE users (
  id                   CHAR(36)     NOT NULL,
  tenant_id            CHAR(36)     NOT NULL,
  username             VARCHAR(64)  NOT NULL,
  password_hash        VARCHAR(255) NOT NULL,
  display_name         VARCHAR(120) NOT NULL DEFAULT '',
  must_change_password TINYINT(1)   NOT NULL DEFAULT 1
                       CHECK (must_change_password IN (0, 1)),
  status               VARCHAR(16)  NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'disabled')),
  token_version        INT          NOT NULL DEFAULT 1,
  last_login_at        DATETIME     NULL,
  row_version          INT          NOT NULL DEFAULT 1,
  created_at           DATETIME     NOT NULL,
  updated_at           DATETIME     NOT NULL,
  deleted_at           DATETIME     NULL,
  deleted_by           CHAR(36)     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_tenant_username (tenant_id, username),
  KEY ix_users_tenant (tenant_id),
  CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_users_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- user <-> tenant with the role (spec §7 role matrix: admin | user, no others).
CREATE TABLE memberships (
  id         CHAR(36)    NOT NULL,
  tenant_id  CHAR(36)    NOT NULL,
  user_id    CHAR(36)    NOT NULL,
  role       VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'user')),
  created_at DATETIME    NOT NULL,
  updated_at DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_memberships_tenant_user (tenant_id, user_id),
  CONSTRAINT fk_memberships_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_memberships_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Machines (spec §5). NEVER conflated with crm_devices: that table holds CRM
-- cases, this one holds enrolled laptops. Revocation is status + token_version.
CREATE TABLE sync_devices (
  id            CHAR(36)    NOT NULL,
  tenant_id     CHAR(36)    NOT NULL,
  name          VARCHAR(120) NOT NULL DEFAULT '',
  status        VARCHAR(16) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'revoked')),
  token_version INT         NOT NULL DEFAULT 1,
  last_auth_at  DATETIME    NULL,
  last_seen_at  DATETIME    NULL,
  enrolled_by   CHAR(36)    NULL,
  row_version   INT         NOT NULL DEFAULT 1,
  created_at    DATETIME    NOT NULL,
  updated_at    DATETIME    NOT NULL,
  deleted_at    DATETIME    NULL,
  deleted_by    CHAR(36)    NULL,
  PRIMARY KEY (id),
  KEY ix_sync_devices_tenant_status (tenant_id, status),
  KEY ix_sync_devices_last_auth (last_auth_at),
  CONSTRAINT fk_sync_devices_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_sync_devices_enrolled_by FOREIGN KEY (enrolled_by) REFERENCES users (id),
  CONSTRAINT fk_sync_devices_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Sessions, rate limiting, audit (spec §5, §10)
-- ---------------------------------------------------------------------------
-- One-time-use refresh families for BOTH token kinds (spec §5). A refresh
-- rotates on every use: the new row's parent_id points at the consumed one,
-- which keeps `used_at` set. Re-presenting a token whose row already has
-- used_at is reuse -> revoke the whole family_id.
CREATE TABLE auth_sessions (
  id                 CHAR(36)     NOT NULL,
  tenant_id          CHAR(36)     NOT NULL,
  family_id          CHAR(36)     NOT NULL,
  kind               VARCHAR(8)   NOT NULL CHECK (kind IN ('user', 'device')),
  token_hash         CHAR(64)     NOT NULL,
  user_id            CHAR(36)     NULL,
  sync_device_id     CHAR(36)     NULL,
  parent_id          CHAR(36)     NULL,
  issued_at          DATETIME     NOT NULL,
  idle_expires_at    DATETIME     NOT NULL,
  absolute_expires_at DATETIME    NOT NULL,
  used_at            DATETIME     NULL,
  revoked_at         DATETIME     NULL,
  revoked_reason     VARCHAR(64)  NOT NULL DEFAULT '',
  rotated_to         CHAR(36)     NULL,
  created_at         DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_auth_sessions_token_hash (token_hash),
  KEY ix_auth_sessions_family (family_id),
  KEY ix_auth_sessions_tenant_kind (tenant_id, kind),
  KEY ix_auth_sessions_absolute_expiry (absolute_expires_at),
  KEY ix_auth_sessions_user (user_id),
  KEY ix_auth_sessions_device (sync_device_id),
  CONSTRAINT fk_auth_sessions_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_auth_sessions_device FOREIGN KEY (sync_device_id) REFERENCES sync_devices (id),
  CONSTRAINT fk_auth_sessions_parent FOREIGN KEY (parent_id) REFERENCES auth_sessions (id),
  CONSTRAINT fk_auth_sessions_rotated FOREIGN KEY (rotated_to) REFERENCES auth_sessions (id),
  CHECK ((kind = 'user' AND user_id IS NOT NULL)
      OR (kind = 'device' AND sync_device_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- DB-backed rate limiting keyed on account + IP (spec §10.5). Deliberately
-- append-only with a query-time window: there is no permanent lockout column,
-- because a permanent lockout is a DoS vector.
CREATE TABLE login_attempts (
  id           BIGINT      NOT NULL AUTO_INCREMENT,
  tenant_id    CHAR(36)    NULL,
  account      VARCHAR(120) NOT NULL,
  ip           VARCHAR(45) NOT NULL,
  attempted_at DATETIME    NOT NULL,
  outcome      VARCHAR(16) NOT NULL
               CHECK (outcome IN ('success', 'failure', 'locked')),
  PRIMARY KEY (id),
  KEY ix_login_attempts_account_time (account, attempted_at),
  KEY ix_login_attempts_ip_time (ip, attempted_at),
  KEY ix_login_attempts_tenant (tenant_id),
  CONSTRAINT fk_login_attempts_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Metadata only (spec §10.9): no passwords, tokens, cookies, national ids,
-- phone numbers or names, and never a plaintext diff of PII. `metadata` is a
-- JSON document of non-identifying keys; the API is responsible for keeping it
-- that way. The value is stored as MEDIUMTEXT (not the JSON alias) so the
-- column type is identical on MySQL and MariaDB; JSON_VALID is enforced below.
CREATE TABLE audit_events (
  id              CHAR(36)     NOT NULL,
  tenant_id       CHAR(36)     NOT NULL,
  occurred_at     DATETIME     NOT NULL,
  action          VARCHAR(64)  NOT NULL,
  actor_type      VARCHAR(16)  NOT NULL DEFAULT 'system',
  actor_user_id   CHAR(36)     NULL,
  actor_device_id CHAR(36)     NULL,
  entity          VARCHAR(64)  NOT NULL DEFAULT '',
  entity_id       CHAR(36)     NULL,
  request_id      CHAR(36)     NULL,
  ip              VARCHAR(45)  NULL,
  outcome         VARCHAR(16)  NOT NULL DEFAULT 'ok',
  metadata        MEDIUMTEXT   NULL,
  PRIMARY KEY (id),
  KEY ix_audit_events_tenant_time (tenant_id, occurred_at),
  KEY ix_audit_events_action (action),
  CONSTRAINT fk_audit_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_audit_events_user FOREIGN KEY (actor_user_id) REFERENCES users (id),
  CONSTRAINT fk_audit_events_device FOREIGN KEY (actor_device_id) REFERENCES sync_devices (id),
  CHECK (metadata IS NULL OR json_valid(metadata))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Idempotent mutations (spec §6.1): the same operation_id returns its first
-- stored response and never applies twice; retained >= 30 days.
CREATE TABLE idempotency_keys (
  operation_id CHAR(36)     NOT NULL,
  tenant_id    CHAR(36)     NOT NULL,
  request_hash CHAR(64)     NULL,
  status       VARCHAR(16)  NOT NULL DEFAULT 'applied'
               CHECK (status IN ('applied', 'conflict', 'rejected')),
  response     MEDIUMTEXT   NOT NULL,
  created_at   DATETIME     NOT NULL,
  expires_at   DATETIME     NOT NULL,
  PRIMARY KEY (operation_id),
  KEY ix_idempotency_keys_expiry (expires_at),
  KEY ix_idempotency_keys_tenant (tenant_id),
  CONSTRAINT fk_idempotency_keys_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The global sync cursor (spec §4, §6.2). Clients page with `seq > cursor`
-- ONLY — never with a timestamp (spec §6.2). One transaction appends the whole
-- batch, so every operation of a push shares a contiguous run of seq values.
CREATE TABLE change_log (
  seq             BIGINT      NOT NULL AUTO_INCREMENT,
  tenant_id       CHAR(36)    NOT NULL,
  entity          VARCHAR(64) NOT NULL,
  id              CHAR(36)    NOT NULL,
  row_version     INT         NOT NULL,
  action          VARCHAR(16) NOT NULL DEFAULT 'upsert'
                  CHECK (action IN ('upsert', 'delete')),
  actor_type      VARCHAR(16) NOT NULL DEFAULT 'system',
  actor_user_id   CHAR(36)    NULL,
  actor_device_id CHAR(36)    NULL,
  operation_id    CHAR(36)    NULL,
  changed_at      DATETIME    NOT NULL,
  PRIMARY KEY (seq),
  KEY ix_change_log_tenant_seq (tenant_id, seq),
  KEY ix_change_log_entity_id (tenant_id, entity, id),
  KEY ix_change_log_operation (operation_id),
  CONSTRAINT fk_change_log_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_change_log_user FOREIGN KEY (actor_user_id) REFERENCES users (id),
  CONSTRAINT fk_change_log_device FOREIGN KEY (actor_device_id) REFERENCES sync_devices (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Bootstrap / import gate (spec §8). Read with SELECT ... FOR UPDATE so that
-- concurrent imports serialise on this single row and only the first
-- transaction wins. The import response snapshot lives in idempotency_keys
-- keyed by the same import_id — that is what makes a replay return the same
-- body instead of re-running the import.
--
-- `source_installation_id` is provenance (spec §8.3): the UUID the client
-- minted once on its first bootstrap and sent with the import, so the host
-- knows which installation produced this dataset — the record behind the
-- "an unknown or foreign epoch is quarantined and never auto-pushed" rule and
-- behind any audit question of where a row came from. NULL until the first
-- import, written once at import, and never updated afterwards: changing it
-- later would silently rewrite the dataset's origin.
CREATE TABLE tenant_state (
  tenant_id              CHAR(36)     NOT NULL,
  bootstrap_status       VARCHAR(16)  NOT NULL DEFAULT 'pending'
                         CHECK (bootstrap_status IN ('pending', 'running', 'done')),
  dataset_epoch          INT          NOT NULL DEFAULT 0,
  last_import_id         CHAR(36)     NULL,
  last_import_hash       CHAR(64)     NULL,
  source_installation_id CHAR(36)     NULL,
  created_at             DATETIME     NOT NULL,
  updated_at             DATETIME     NOT NULL,
  PRIMARY KEY (tenant_id),
  KEY ix_tenant_state_source (source_installation_id),
  CONSTRAINT fk_tenant_state_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Business tables (spec §3) — every row carries the §4 envelope
-- ---------------------------------------------------------------------------

-- periods[] — reporting batches closed by an operator (crm.open_period /
-- close_period). created/updated are not part of the store record, so the
-- envelope's own timestamps are the only ones.
CREATE TABLE periods (
  id            CHAR(36)     NOT NULL,
  tenant_id     CHAR(36)     NOT NULL,
  legacy_id     VARCHAR(64)  NULL,
  row_version   INT          NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL,
  updated_at    DATETIME     NOT NULL,
  deleted_at    DATETIME     NULL,
  deleted_by    CHAR(36)     NULL,
  number        INT          NOT NULL,
  label         VARCHAR(120) NOT NULL DEFAULT '',
  opened_at     DATETIME     NOT NULL,
  opened_by     VARCHAR(64)  NOT NULL DEFAULT 'system',
  closed_at     DATETIME     NULL,
  closed_by     VARCHAR(64)  NOT NULL DEFAULT '',
  closed_reason VARCHAR(1000) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uq_periods_tenant_legacy (tenant_id, legacy_id),
  KEY ix_periods_tenant_closed (tenant_id, closed_at),
  CONSTRAINT fk_periods_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_periods_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- settings{} — ONE row per tenant, allowlist only (spec §3). Any key outside
-- the allowlist is dropped on write with a warning and never reaches the host.
-- `links` is the one structured value (list of {name,url}); stored as text with
-- a JSON validity check so the column type is the same on MySQL and MariaDB.
CREATE TABLE settings (
  id                       CHAR(36)     NOT NULL,
  tenant_id                CHAR(36)     NOT NULL,
  legacy_id                VARCHAR(64)  NULL,
  row_version              INT          NOT NULL DEFAULT 1,
  created_at               DATETIME     NOT NULL,
  updated_at               DATETIME     NOT NULL,
  deleted_at               DATETIME     NULL,
  deleted_by               CHAR(36)     NULL,
  business_name            VARCHAR(80)  NOT NULL DEFAULT '',
  business_phone           VARCHAR(80)  NOT NULL DEFAULT '',
  watch_interval_hours     INT          NOT NULL DEFAULT 2
                           CHECK (watch_interval_hours BETWEEN 1 AND 72),
  scheduler_enabled        TINYINT(1)   NOT NULL DEFAULT 1
                           CHECK (scheduler_enabled IN (0, 1)),
  auto_advance_default     TINYINT(1)   NOT NULL DEFAULT 0
                           CHECK (auto_advance_default IN (0, 1)),
  telegram_report_full_data TINYINT(1)  NOT NULL DEFAULT 1
                           CHECK (telegram_report_full_data IN (0, 1)),
  registry_fee_amount      BIGINT       NOT NULL DEFAULT 24000000,
  registration_chat_id     VARCHAR(32)  NOT NULL DEFAULT '',
  report_chat_id           VARCHAR(32)  NOT NULL DEFAULT '',
  sound_enabled            TINYINT(1)   NOT NULL DEFAULT 1
                           CHECK (sound_enabled IN (0, 1)),
  current_period_id        CHAR(36)     NULL,
  links                    MEDIUMTEXT   NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_settings_tenant (tenant_id),
  UNIQUE KEY uq_settings_tenant_legacy (tenant_id, legacy_id),
  CONSTRAINT fk_settings_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_settings_period FOREIGN KEY (current_period_id) REFERENCES periods (id),
  CONSTRAINT fk_settings_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> ''),
  CHECK (links IS NULL OR json_valid(links))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- customers[] — intake records. The nested transactions[] array lives in its
-- own table and is ordered by `seq` (spec §3).
CREATE TABLE customers (
  id           CHAR(36)     NOT NULL,
  tenant_id    CHAR(36)     NOT NULL,
  legacy_id    VARCHAR(64)  NULL,
  row_version  INT          NOT NULL DEFAULT 1,
  created_at   DATETIME     NOT NULL,
  updated_at   DATETIME     NOT NULL,
  deleted_at   DATETIME     NULL,
  deleted_by   CHAR(36)     NULL,
  full_name    VARCHAR(80)  NOT NULL,
  address      VARCHAR(300) NOT NULL DEFAULT '',
  mobile       VARCHAR(11)  NOT NULL DEFAULT '',
  telegram_id  VARCHAR(64)  NOT NULL DEFAULT '',
  instagram_id VARCHAR(64)  NOT NULL DEFAULT '',
  notes        VARCHAR(500) NOT NULL DEFAULT '',
  created      DATETIME     NULL,
  updated      DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_customers_tenant_legacy (tenant_id, legacy_id),
  KEY ix_customers_tenant_mobile (tenant_id, mobile),
  CONSTRAINT fk_customers_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_customers_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- owners[] — passenger identities, 1:1 with a crm_devices row when attached.
-- No UNIQUE on the (device) binding: the row becomes a tombstone rather than
-- disappearing, so a unique index would keep a freed passenger hostage. The
-- 1:1 rule is enforced by crm.attach_owner / advance().
CREATE TABLE owners (
  id          CHAR(36)    NOT NULL,
  tenant_id   CHAR(36)    NOT NULL,
  legacy_id   VARCHAR(64) NULL,
  row_version INT         NOT NULL DEFAULT 1,
  created_at  DATETIME    NOT NULL,
  updated_at  DATETIME    NOT NULL,
  deleted_at  DATETIME    NULL,
  deleted_by  CHAR(36)    NULL,
  first_name  VARCHAR(120) NOT NULL,
  last_name   VARCHAR(120) NOT NULL,
  national    VARCHAR(10) NOT NULL,
  birth       VARCHAR(10) NOT NULL,
  mobile      VARCHAR(11) NOT NULL,
  card_no     VARCHAR(16) NOT NULL DEFAULT '',
  passport    VARCHAR(20) NOT NULL DEFAULT '',
  created     DATETIME    NULL,
  updated     DATETIME    NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_owners_tenant_legacy (tenant_id, legacy_id),
  KEY ix_owners_tenant_national (tenant_id, national),
  CONSTRAINT fk_owners_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_owners_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- identities[] — the inquiry pool.
CREATE TABLE identities (
  id          CHAR(36)    NOT NULL,
  tenant_id   CHAR(36)    NOT NULL,
  legacy_id   VARCHAR(64) NULL,
  row_version INT         NOT NULL DEFAULT 1,
  created_at  DATETIME    NOT NULL,
  updated_at  DATETIME    NOT NULL,
  deleted_at  DATETIME    NULL,
  deleted_by  CHAR(36)    NULL,
  label       VARCHAR(80) NOT NULL DEFAULT '',
  national    VARCHAR(10) NOT NULL,
  birth       VARCHAR(10) NOT NULL,
  mobile      VARCHAR(11) NOT NULL,
  created     DATETIME    NULL,
  updated     DATETIME    NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_identities_tenant_legacy (tenant_id, legacy_id),
  KEY ix_identities_tenant_national (tenant_id, national),
  CONSTRAINT fk_identities_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_identities_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- devices[] -> crm_devices. The CRM case, NOT the machine (sync_devices) and
-- NOT the browser profile (profile_id, dangling by design).
-- No UNIQUE on serial1/serial2: correcting a serial is delete-and-recreate of
-- the same serial (crm.update_device says so), which a unique index would
-- reject while the old row still exists as a tombstone.
CREATE TABLE crm_devices (
  id                   CHAR(36)      NOT NULL,
  tenant_id            CHAR(36)      NOT NULL,
  legacy_id            VARCHAR(64)   NULL,
  row_version          INT           NOT NULL DEFAULT 1,
  created_at           DATETIME      NOT NULL,
  updated_at           DATETIME      NOT NULL,
  deleted_at           DATETIME      NULL,
  deleted_by           CHAR(36)      NULL,
  name                 VARCHAR(40)   NOT NULL,
  serial1              VARCHAR(15)   NOT NULL,
  serial2              VARCHAR(15)   NOT NULL DEFAULT '',
  customer_id          CHAR(36)      NULL,
  owner_id             CHAR(36)      NULL,
  stage                VARCHAR(16)   NOT NULL DEFAULT 'new'
                       CHECK (stage IN ('new', 'inquiry', 'problem',
                                        'ready_owner', 'handed', 'to_payment',
                                        'payment', 'payment_done', 'done',
                                        'closed')),
  auto_advance         TINYINT(1)    NOT NULL DEFAULT 0
                       CHECK (auto_advance IN (0, 1)),
  profile_id           VARCHAR(64)   NOT NULL DEFAULT '',
  handed_at            DATETIME      NULL,
  registered_at        DATETIME      NULL,
  payment_confirmed_at DATETIME      NULL,
  code_delivered_at    DATETIME      NULL,
  done_at              DATETIME      NULL,
  last_inquiry_at      DATETIME      NULL,
  notes                VARCHAR(500)  NOT NULL DEFAULT '',
  advance_attempts     INT           NOT NULL DEFAULT 0,
  period_id            CHAR(36)      NULL,
  created              DATETIME      NULL,
  updated              DATETIME      NULL,
  skipped_stages       MEDIUMTEXT    NULL,
  closed_at            DATETIME      NULL,
  closed_reason        VARCHAR(1000) NOT NULL DEFAULT '',
  periodic_opt_out     TINYINT(1)    NOT NULL DEFAULT 0
                       CHECK (periodic_opt_out IN (0, 1)),
  payment_started_at   DATETIME      NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_crm_devices_tenant_legacy (tenant_id, legacy_id),
  KEY ix_crm_devices_tenant_stage (tenant_id, stage),
  KEY ix_crm_devices_tenant_period (tenant_id, period_id),
  KEY ix_crm_devices_serial1 (tenant_id, serial1),
  KEY ix_crm_devices_customer (customer_id),
  KEY ix_crm_devices_owner (owner_id),
  CONSTRAINT fk_crm_devices_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_crm_devices_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
  CONSTRAINT fk_crm_devices_owner FOREIGN KEY (owner_id) REFERENCES owners (id),
  CONSTRAINT fk_crm_devices_period FOREIGN KEY (period_id) REFERENCES periods (id),
  CONSTRAINT fk_crm_devices_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> ''),
  CHECK (skipped_stages IS NULL OR json_valid(skipped_stages))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- customers[].transactions[] — the integer-Toman ledger (crm.add_transaction;
-- a negative amount is debt). `seq` is the array index and restores the order
-- (spec §3). `image` is a bare file name under tx_artifacts/ (local only) and
-- must never become a foreign key.
CREATE TABLE customer_transactions (
  id           CHAR(36)      NOT NULL,
  tenant_id    CHAR(36)      NOT NULL,
  legacy_id    VARCHAR(64)   NULL,
  row_version  INT           NOT NULL DEFAULT 1,
  created_at   DATETIME      NOT NULL,
  updated_at   DATETIME      NOT NULL,
  deleted_at   DATETIME      NULL,
  deleted_by   CHAR(36)      NULL,
  seq          INT           NOT NULL,
  customer_id  CHAR(36)      NOT NULL,
  at           DATETIME      NULL,
  amount       BIGINT        NOT NULL,
  note         VARCHAR(1000) NOT NULL DEFAULT '',
  kind         VARCHAR(32)   NOT NULL DEFAULT '',
  image        VARCHAR(64)   NOT NULL DEFAULT '',
  device_id    CHAR(36)      NULL,
  voided_at    DATETIME      NULL,
  void_reason  VARCHAR(1000) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uq_customer_tx_tenant_customer_seq (tenant_id, customer_id, seq),
  UNIQUE KEY uq_customer_tx_tenant_legacy (tenant_id, legacy_id),
  KEY ix_customer_tx_tenant_at (tenant_id, at),
  KEY ix_customer_tx_device (device_id),
  CONSTRAINT fk_customer_tx_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_customer_tx_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
  CONSTRAINT fk_customer_tx_device FOREIGN KEY (device_id) REFERENCES crm_devices (id),
  CONSTRAINT fk_customer_tx_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
