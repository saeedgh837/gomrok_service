-- ===========================================================================
-- 007_clerks.sql
--
-- Support registration clerks (متصدی ثبت) and clerk accounting in cloud sync.
--
-- Tables added:
--   * clerks: clerk profiles (name, mobile, notes, active status, timestamps)
--   * clerk_transactions: financial ledger for clerks (amount, kind, device, period, voiding)
--
-- Tables modified:
--   * crm_devices: add clerk_id column with foreign key to clerks(id)
-- ===========================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS clerks (
  id          CHAR(36)     NOT NULL,
  tenant_id   CHAR(36)     NOT NULL,
  legacy_id   VARCHAR(64)  NULL,
  row_version INT          NOT NULL DEFAULT 1,
  created_at  DATETIME     NOT NULL,
  updated_at  DATETIME     NOT NULL,
  deleted_at  DATETIME     NULL,
  deleted_by  CHAR(36)     NULL,
  name        VARCHAR(80)  NOT NULL,
  mobile      VARCHAR(11)  NOT NULL DEFAULT '',
  notes       VARCHAR(500) NOT NULL DEFAULT '',
  active      TINYINT(1)   NOT NULL DEFAULT 1,
  created     DATETIME     NULL,
  updated     DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_clerks_tenant_legacy (tenant_id, legacy_id),
  KEY ix_clerks_tenant_active (tenant_id, active),
  CONSTRAINT fk_clerks_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_clerks_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CONSTRAINT ck_clerks_legacy CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE crm_devices
  ADD COLUMN clerk_id CHAR(36) NULL AFTER period_id,
  ADD KEY ix_crm_devices_tenant_clerk (tenant_id, clerk_id),
  ADD KEY ix_crm_devices_clerk (clerk_id),
  ADD CONSTRAINT fk_crm_devices_clerk FOREIGN KEY (clerk_id) REFERENCES clerks (id);

CREATE TABLE IF NOT EXISTS clerk_transactions (
  id             CHAR(36)      NOT NULL,
  tenant_id      CHAR(36)      NOT NULL,
  legacy_id      VARCHAR(64)   NULL,
  row_version    INT           NOT NULL DEFAULT 1,
  created_at     DATETIME      NOT NULL,
  updated_at     DATETIME      NOT NULL,
  deleted_at     DATETIME      NULL,
  deleted_by     CHAR(36)      NULL,
  clerk_id       CHAR(36)      NOT NULL,
  at             DATETIME      NULL,
  amount         BIGINT        NOT NULL,
  kind           VARCHAR(32)   NOT NULL DEFAULT 'credit',
  recipient_name VARCHAR(120)  NOT NULL DEFAULT '',
  device_id      CHAR(36)      NULL,
  period_id      CHAR(36)      NULL,
  note           VARCHAR(1000) NOT NULL DEFAULT '',
  voided_at      DATETIME      NULL,
  void_reason    VARCHAR(1000) NOT NULL DEFAULT '',
  reversal_of    VARCHAR(64)   NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uq_clerk_tx_tenant_legacy (tenant_id, legacy_id),
  KEY ix_clerk_tx_clerk (clerk_id),
  KEY ix_clerk_tx_period (period_id),
  KEY ix_clerk_tx_device (device_id),
  CONSTRAINT fk_clerk_tx_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
  CONSTRAINT fk_clerk_tx_clerk FOREIGN KEY (clerk_id) REFERENCES clerks (id),
  CONSTRAINT fk_clerk_tx_device FOREIGN KEY (device_id) REFERENCES crm_devices (id),
  CONSTRAINT fk_clerk_tx_period FOREIGN KEY (period_id) REFERENCES periods (id),
  CONSTRAINT fk_clerk_tx_deleted_by FOREIGN KEY (deleted_by) REFERENCES users (id),
  CONSTRAINT ck_clerk_tx_legacy CHECK (legacy_id IS NULL OR legacy_id <> '')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
