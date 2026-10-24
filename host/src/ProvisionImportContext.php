<?php
/** Transaction-scoped import capability; never a general customer credential. */
declare(strict_types=1);

final class ProvisionImportContext
{
    private bool $valid = true;

    private function __construct(
        private readonly Database $db,
        private readonly OperatorPrincipal $operator,
        public readonly string $provisionId,
        public readonly string $tenantId,
        public readonly string $ownerUserId,
        public readonly ?string $jobId
    ) {
    }

    /** Lock in the same operator -> session order used by recovery/refresh. */
    public static function lockOperator(Database $db, OperatorPrincipal $operator): void
    {
        if (!$db->pdo()->inTransaction()) {
            throw new LogicException('operator import authority requires a transaction');
        }
        $row = $db->queryOne('SELECT enabled,token_version FROM platform_operators WHERE id=? FOR UPDATE',
            [$operator->operatorId]);
        $session = $db->queryOne('SELECT operator_id,revoked_at,absolute_expires_at FROM operator_sessions'
            . ' WHERE id=? FOR UPDATE', [$operator->sessionId]);
        if ($row === null || (int) $row['enabled'] !== 1
            || (int) $row['token_version'] !== $operator->tokenVersion
            || $session === null || $session['operator_id'] !== $operator->operatorId
            || $session['revoked_at'] !== null || strtotime($session['absolute_expires_at']) <= time()) {
            throw new ApiException(401, Codes::AUTH_REVOKED, 'operator credential is no longer active');
        }
    }

    /** The capability is invalidated even if a caller retains it after the callback. */
    public static function withLocked(Database $db, OperatorPrincipal $operator,
                                      string $id, callable $operation): mixed
    {
        self::lockOperator($db, $operator);
        $row = $db->queryOne('SELECT * FROM account_provisions WHERE id=? AND operator_id=? FOR UPDATE',
            [$id, $operator->operatorId]);
        if ($row === null) {
            throw new ApiException(404, Codes::NOT_FOUND, 'account provision was not found');
        }
        $context = new self($db, $operator, $id, $row['tenant_id'], $row['owner_user_id'], $row['legacy_job_id']);
        try {
            $context->requireBound($db);
            return $operation($context, $row);
        } finally {
            $context->valid = false;
        }
    }

    public function requireBound(Database $db, ?string $jobId = null, bool $write = false): void
    {
        if (!$this->valid || $db !== $this->db || !$db->pdo()->inTransaction()) {
            throw new LogicException('import capability is outside its transaction');
        }
        self::lockOperator($db, $this->operator);
        $p = $db->queryOne('SELECT * FROM account_provisions WHERE id=? AND operator_id=? FOR UPDATE',
            [$this->provisionId, $this->operator->operatorId]);
        if ($p === null || $p['tenant_id'] !== $this->tenantId || $p['owner_user_id'] !== $this->ownerUserId
            || $p['legacy_job_id'] !== $this->jobId || ($jobId !== null && $jobId !== $this->jobId)) {
            throw new ApiException(403, Codes::PERMISSION_DENIED, 'import capability binding changed');
        }
        $owner = $db->queryOne('SELECT tenant_id,status,deleted_at FROM users WHERE id=? FOR UPDATE', [$this->ownerUserId]);
        $workspace = $db->queryOne('SELECT owner_user_id,state FROM account_workspaces WHERE tenant_id=? FOR UPDATE', [$this->tenantId]);
        $name = $db->queryOne('SELECT canonical,state FROM login_names WHERE user_id=? FOR UPDATE', [$this->ownerUserId]);
        if ($owner === null || $owner['tenant_id'] !== $this->tenantId || $owner['deleted_at'] !== null
            || $workspace === null || $workspace['owner_user_id'] !== $this->ownerUserId || $name === null) {
            throw new ApiException(403, Codes::PERMISSION_DENIED, 'pending private account binding changed');
        }
        if ($write && (!in_array($p['state'], ['reserved','analyzed','reviewed','applying'], true)
            || $owner['status'] !== 'active' || $workspace['state'] !== 'pending' || $name['state'] !== 'reserved')) {
            throw new ApiException(409, Codes::VERSION_CONFLICT, 'this provision cannot change pending data');
        }
        if ($this->jobId !== null) {
            $job = $db->queryOne('SELECT tenant_id,source_sha256,created_by_operator FROM legacy_import_jobs WHERE id=? FOR UPDATE', [$this->jobId]);
            if ($job === null || $job['tenant_id'] !== $this->tenantId
                || $job['source_sha256'] !== $p['source_sha256'] || $job['created_by_operator'] !== $this->operator->operatorId) {
                throw new ApiException(403, Codes::PERMISSION_DENIED, 'legacy job binding changed');
            }
        }
    }

    public function operatorId(): string
    {
        return $this->operator->operatorId;
    }
}
