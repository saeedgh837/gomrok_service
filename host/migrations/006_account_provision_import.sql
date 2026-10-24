-- Operator-attributed, transactionally published private-account imports.
SET NAMES utf8mb4;
SET time_zone = '+00:00';

ALTER TABLE legacy_import_jobs
  MODIFY created_by CHAR(36) NULL,
  ADD created_by_operator CHAR(36) NULL,
  ADD CONSTRAINT fk_legacy_jobs_operator FOREIGN KEY (created_by_operator)
    REFERENCES platform_operators (id),
  ADD CONSTRAINT ck_legacy_jobs_actor CHECK (
    (created_by IS NOT NULL AND created_by_operator IS NULL)
    OR (created_by IS NULL AND created_by_operator IS NOT NULL));

ALTER TABLE legacy_import_decisions
  MODIFY decided_by CHAR(36) NULL,
  ADD decided_by_operator CHAR(36) NULL,
  ADD CONSTRAINT fk_legacy_decisions_operator FOREIGN KEY (decided_by_operator)
    REFERENCES platform_operators (id),
  ADD CONSTRAINT ck_legacy_decisions_actor CHECK (
    (decided_by IS NOT NULL AND decided_by_operator IS NULL)
    OR (decided_by IS NULL AND decided_by_operator IS NOT NULL));

ALTER TABLE change_log
  ADD actor_operator_id CHAR(36) NULL,
  ADD CONSTRAINT fk_change_log_operator FOREIGN KEY (actor_operator_id)
    REFERENCES platform_operators (id),
  ADD CONSTRAINT ck_change_log_operator CHECK (actor_operator_id IS NULL
    OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_device_id IS NULL));

ALTER TABLE audit_events
  ADD actor_operator_id CHAR(36) NULL,
  ADD CONSTRAINT fk_audit_events_operator FOREIGN KEY (actor_operator_id)
    REFERENCES platform_operators (id),
  ADD CONSTRAINT ck_audit_events_operator CHECK (actor_operator_id IS NULL
    OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_device_id IS NULL));

ALTER TABLE account_provisions
  ADD request_sha256 CHAR(64) NOT NULL DEFAULT '',
  ADD normalization_version VARCHAR(32) NOT NULL DEFAULT '',
  ADD review_sha256 CHAR(64) NULL,
  ADD completion_json MEDIUMTEXT NULL,
  ADD CONSTRAINT ck_provision_completion CHECK (completion_json IS NULL OR json_valid(completion_json)),
  ADD UNIQUE KEY uq_account_provisions_job (legacy_job_id);

-- Targets and source binding never change, including on retry/cancellation.
CREATE TRIGGER trg_account_provisions_binding_immutable
BEFORE UPDATE ON account_provisions
FOR EACH ROW
SET NEW.owner_user_id = IF(NEW.id = OLD.id AND NEW.operator_id = OLD.operator_id
  AND NEW.tenant_id = OLD.tenant_id AND NEW.owner_user_id = OLD.owner_user_id
  AND NEW.source_sha256 = OLD.source_sha256 AND NEW.request_sha256 = OLD.request_sha256
  AND (OLD.legacy_job_id IS NULL OR NEW.legacy_job_id <=> OLD.legacy_job_id),
  OLD.owner_user_id, NULL);
