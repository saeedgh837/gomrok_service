<?php
/** Durable new-account staging and atomic verified publication. */
declare(strict_types=1);

final class AccountProvision
{
    private readonly LegacyImport $import;
    private readonly LegacyApply $apply;
    private readonly LegacyVerify $verify;

    public function __construct(private readonly Database $db, private readonly Log $log,
                                 private readonly array $cfg)
    {
        $sync = new Sync($db, $log, $cfg);
        $this->import = new LegacyImport($db, $sync, $log, $cfg);
        $this->apply = new LegacyApply($db, $sync, $log, $cfg);
        $this->verify = new LegacyVerify($db, $log, $cfg);
    }

    public function analyze(OperatorPrincipal $operator, array $body, string $requestId,
                             string $ip): array
    {
        $this->requireWindow();
        $this->onlyKeys($body, ['provision_id','username','password','password_confirmation',
            'source_text','source_version','filename']);
        $id = $this->uuid($body['provision_id'] ?? null);
        $name = Workspace::canonical($this->text($body, 'username', 256));
        $password = $this->text($body, 'password', 4096);
        if (!is_string($body['password_confirmation'] ?? null)
            || !hash_equals($password, $body['password_confirmation'])) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'password confirmation must match');
        }
        $source = $this->text($body, 'source_text', $this->sourceLimit());
        $version = isset($body['source_version']) ? $this->text($body, 'source_version', 32) : '';
        $filename = isset($body['filename']) ? $this->text($body, 'filename', 255) : '';
        $sha = hash('sha256', $source);
        // No password-derived fingerprint or plaintext is persisted in a job.
        $fingerprint = hash('sha256', $this->json([$name, $sha, $version, $filename]));
        $hash = Auth::hashPassword($password);
        try {
            return $this->db->transaction(function (Database $tx) use (
                $operator, $body, $requestId, $ip, $id, $name, $password, $fingerprint, $sha, $hash
            ): array {
                ProvisionImportContext::lockOperator($tx, $operator);
                $existing = $tx->queryOne('SELECT operator_id FROM account_provisions WHERE id=? FOR UPDATE', [$id]);
                if ($existing !== null) {
                    return ProvisionImportContext::withLocked($tx, $operator, $id,
                        function (ProvisionImportContext $context, array $row) use ($password, $fingerprint): array {
                            $owner = $this->db->queryOne('SELECT password_hash FROM users WHERE id=? FOR UPDATE', [$context->ownerUserId]);
                            if (!hash_equals($row['request_sha256'], $fingerprint)
                                || !Auth::verifyPassword($password, (string) ($owner['password_hash'] ?? ''))) {
                                throw new ApiException(409, Codes::VERSION_CONFLICT, 'provision retry input changed');
                            }
                            return $this->response($context, $row);
                        });
                }
                $account = $this->reserve($tx, $name, $hash);
                $now = gmdate('Y-m-d H:i:s');
                $tx->execute('INSERT INTO account_provisions (id,operator_id,tenant_id,owner_user_id,'
                    . 'source_sha256,request_sha256,normalization_version,state,created_at,updated_at)'
                    . ' VALUES (?,?,?,?,?,?,?,?,?,?)', [$id, $operator->operatorId, $account['tenant_id'],
                        $account['user_id'], $sha, $fingerprint, LegacyImport::NORMALIZATION_VERSION, 'reserved', $now, $now]);
                ProvisionImportContext::withLocked($tx, $operator, $id,
                    function (ProvisionImportContext $context) use ($body, $requestId, $ip, $tx, $id, $now): void {
                        $staged = $this->import->analyzeInTransaction($context, $body, $requestId, $ip);
                        $job = $staged['job'];
                        $tx->execute('UPDATE account_provisions SET legacy_job_id=?,source_version=?,original_filename=?,'
                            . 'state=?,updated_at=? WHERE id=? AND state=?', [$job['id'], $job['source_version'],
                                $job['original_filename'], 'analyzed', $now, $id, 'reserved']);
                    });
                return ProvisionImportContext::withLocked($tx, $operator, $id,
                    function (ProvisionImportContext $context, array $row) use ($requestId, $ip): array {
                        $row = $this->saveReview($context, $row, false);
                        $this->audit($context, 'analyzed', $requestId, $ip);
                        return $this->response($context, $row);
                    });
            });
        } catch (PDOException $error) {
            if ((int) ($error->errorInfo[1] ?? 0) === 1062) {
                throw new ApiException(409, Codes::VERSION_CONFLICT, 'username or provision identity is already reserved');
            }
            throw $error;
        }
    }

    public function review(OperatorPrincipal $operator, string $id): array
    {
        $this->requireWindow();
        return $this->with($operator, $id, function (ProvisionImportContext $context, array $row): array {
            if (in_array($row['state'], ['analyzed','reviewed'], true)) {
                $row = $this->saveReview($context, $row, true);
            }
            return $this->response($context, $row);
        });
    }

    public function decide(OperatorPrincipal $operator, string $id, array $body,
                            string $requestId, string $ip): array
    {
        $this->requireWindow();
        $this->onlyKeys($body, ['record_id','resolution','target_id']);
        return $this->with($operator, $id,
            function (ProvisionImportContext $context, array $row) use ($body, $requestId, $ip): array {
                $context->requireBound($this->db, $context->jobId, true);
                $this->requireNormalization($row);
                $this->requireEmptyTarget($context);
                $this->apply->decideInTransaction($context,
                    ['job_id' => $context->jobId] + $body, $requestId, $ip);
                $row = $this->saveReview($context, $row, true);
                $this->audit($context, 'decision', $requestId, $ip);
                return $this->response($context, $row);
            });
    }

    public function apply(OperatorPrincipal $operator, string $id, string $reviewSha,
                           string $requestId, string $ip): array
    {
        $this->requireWindow();
        if (($this->cfg['private_account_activation_enabled'] ?? false) !== true) {
            throw new ApiException(503, Codes::CONFIG_INVALID, 'private account activation is closed');
        }
        if (preg_match('/\A[a-f0-9]{64}\z/D', $reviewSha) !== 1) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'review_sha256 is required');
        }
        return $this->with($operator, $id,
            function (ProvisionImportContext $context, array $row) use ($reviewSha, $requestId, $ip): array {
                if ($row['state'] === 'active') {
                    if (!hash_equals((string) $row['review_sha256'], $reviewSha)) {
                        throw new ApiException(409, Codes::VERSION_CONFLICT, 'completed review does not match');
                    }
                    return $this->completion($row);
                }
                $context->requireBound($this->db, $context->jobId, true);
                $this->requireNormalization($row);
                $this->requireEmptyTarget($context);
                $current = $this->digest($context, $row);
                if (!hash_equals((string) $row['review_sha256'], $reviewSha)
                    || !hash_equals($current, $reviewSha)) {
                    throw new ApiException(409, Codes::VERSION_CONFLICT, 'review changed; review this provision again');
                }
                $changed = $this->db->execute('UPDATE account_provisions SET state=?,updated_at=?'
                    . ' WHERE id=? AND state IN (?,?)', ['applying', gmdate('Y-m-d H:i:s'),
                        $context->provisionId, 'analyzed', 'reviewed']);
                if ($changed !== 1) {
                    throw new ApiException(409, Codes::VERSION_CONFLICT, 'provision is not ready to publish');
                }
                $result = $this->apply->applyInTransaction($context, (string) $context->jobId, $requestId, $ip);
                $verification = $this->verify->report($context, (string) $context->jobId);
                if (($verification['passed'] ?? false) !== true) {
                    throw new ApiException(409, Codes::VALIDATION_FAILED, 'import reconciliation failed; publication rolled back');
                }
                $now = gmdate('Y-m-d H:i:s');
                $this->db->execute('UPDATE account_workspaces SET state=?,updated_at=? WHERE tenant_id=? AND state=?',
                    ['active', $now, $context->tenantId, 'pending']);
                $this->db->execute('UPDATE login_names SET state=?,updated_at=? WHERE user_id=? AND state=?',
                    ['active', $now, $context->ownerUserId, 'reserved']);
                $this->db->execute('UPDATE account_provisions SET state=?,applied_at=?,updated_at=? WHERE id=? AND state=?',
                    ['active', $now, $now, $context->provisionId, 'applying']);
                $row['state'] = 'active';
                $row['applied_at'] = $now;
                $payload = ['provision' => $this->publicRow($row), 'verification' => $verification, 'result' => $result];
                $this->db->execute('UPDATE account_provisions SET completion_json=? WHERE id=?',
                    [$this->json($payload), $context->provisionId]);
                $this->audit($context, 'active', $requestId, $ip);
                return $payload;
            });
    }

    public function cancel(OperatorPrincipal $operator, string $id, string $requestId,
                            string $ip): array
    {
        $this->requireWindow();
        return $this->with($operator, $id,
            function (ProvisionImportContext $context, array $row) use ($requestId, $ip): array {
                if ($row['state'] === 'active') {
                    throw new ApiException(409, Codes::VERSION_CONFLICT, 'a published account cannot be cancelled');
                }
                if ($row['state'] === 'cancelled') {
                    return ['provision' => $this->publicRow($row)];
                }
                $context->requireBound($this->db, $context->jobId, true);
                $now = gmdate('Y-m-d H:i:s');
                $this->db->execute('UPDATE users SET status=?,token_version=token_version+1,updated_at=? WHERE id=?',
                    ['disabled', $now, $context->ownerUserId]);
                $this->db->execute('UPDATE account_workspaces SET state=?,updated_at=? WHERE tenant_id=?',
                    ['suspended', $now, $context->tenantId]);
                $this->db->execute('UPDATE legacy_import_jobs SET status=?,updated_at=? WHERE id=? AND tenant_id=?',
                    ['cancelled', $now, $context->jobId, $context->tenantId]);
                $this->db->execute('UPDATE account_provisions SET state=?,updated_at=? WHERE id=?',
                    ['cancelled', $now, $context->provisionId]);
                $row['state'] = 'cancelled';
                $this->audit($context, 'cancelled', $requestId, $ip);
                return ['provision' => $this->publicRow($row)];
            });
    }

    private function with(OperatorPrincipal $operator, string $id, callable $operation): array
    {
        $this->uuid($id);
        return $this->db->transaction(fn(Database $tx): array =>
            ProvisionImportContext::withLocked($tx, $operator, $id, $operation));
    }

    private function reserve(Database $tx, string $name, string $hash): array
    {
        $tenant = Ids::v4();
        $user = Ids::v4();
        $now = gmdate('Y-m-d H:i:s');
        $tx->insert('tenants', ['id' => $tenant, 'slug' => 'account-' . $tenant,
            'name' => 'Private account', 'status' => 'active', 'created_at' => $now, 'updated_at' => $now]);
        $tx->insert('users', ['id' => $user, 'tenant_id' => $tenant, 'username' => $name,
            'password_hash' => $hash, 'display_name' => $name, 'must_change_password' => 0,
            'status' => 'active', 'token_version' => 1, 'row_version' => 1, 'created_at' => $now, 'updated_at' => $now]);
        $tx->insert('memberships', ['id' => Ids::v4(), 'tenant_id' => $tenant, 'user_id' => $user,
            'role' => Roles::ADMIN, 'created_at' => $now, 'updated_at' => $now]);
        $tx->execute('INSERT INTO login_names (canonical,user_id,state,created_at,updated_at) VALUES (?,?,?,?,?)',
            [$name, $user, 'reserved', $now, $now]);
        $tx->execute('INSERT INTO account_workspaces (tenant_id,owner_user_id,state,created_at,updated_at) VALUES (?,?,?,?,?)',
            [$tenant, $user, 'pending', $now, $now]);
        $tx->execute('INSERT INTO tenant_state (tenant_id,bootstrap_status,dataset_epoch,created_at,updated_at) VALUES (?,?,?,?,?)',
            [$tenant, 'pending', 0, $now, $now]);
        return ['tenant_id' => $tenant, 'user_id' => $user];
    }

    private function saveReview(ProvisionImportContext $context, array $row, bool $reviewed): array
    {
        $context->requireBound($this->db, $context->jobId, true);
        $this->requireNormalization($row);
        $this->requireEmptyTarget($context);
        $row['review_sha256'] = $this->digest($context, $row);
        if ($reviewed) {
            $row['state'] = 'reviewed';
        }
        $this->db->execute('UPDATE account_provisions SET review_sha256=?,state=?,updated_at=? WHERE id=?',
            [$row['review_sha256'], $row['state'], gmdate('Y-m-d H:i:s'), $context->provisionId]);
        return $row;
    }

    private function digest(ProvisionImportContext $context, array $row): string
    {
        $state = $this->db->queryOne('SELECT dataset_epoch,source_installation_id,bootstrap_status'
            . ' FROM tenant_state WHERE tenant_id=? FOR UPDATE', [$context->tenantId]);
        $records = $this->db->query('SELECT r.id,r.entity,r.source_key,r.source_hash,r.record_json,r.issues_json,'
            . 'r.action,r.target_id,d.resolution,d.target_id AS decision_target_id,d.decided_by,d.decided_by_operator'
            . ' FROM legacy_import_records r LEFT JOIN legacy_import_decisions d'
            . ' ON d.job_id=r.job_id AND d.record_id=r.id AND d.tenant_id=r.tenant_id'
            . ' WHERE r.job_id=? AND r.tenant_id=? ORDER BY r.entity,r.source_key,r.id',
            [$context->jobId, $context->tenantId]);
        return hash('sha256', $this->json([$context->provisionId, $context->tenantId, $context->ownerUserId,
            $context->jobId, $row['source_sha256'], $row['normalization_version'], $state, $records]));
    }

    private function requireEmptyTarget(ProvisionImportContext $context): void
    {
        foreach (array_merge(array_values(Schema::entities()), ['change_log','sync_devices']) as $table) {
            if ((int) $this->db->queryValue('SELECT COUNT(*) FROM ' . $this->db->table($table) . ' WHERE tenant_id=?', [$context->tenantId]) !== 0) {
                throw new ApiException(409, Codes::VERSION_CONFLICT, 'pending account target is no longer empty');
            }
        }
    }

    private function response(ProvisionImportContext $context, array $row): array
    {
        if ($row['state'] === 'active') {
            return $this->completion($row);
        }
        return ['provision' => $this->publicRow($row), 'review_sha256' => $row['review_sha256'],
            'review' => $this->apply->review($context, (string) $context->jobId)];
    }

    private function completion(array $row): array
    {
        $result = json_decode((string) $row['completion_json'], true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($result)) {
            throw new ApiException(500, Codes::SERVER_ERROR, 'provision completion is unavailable');
        }
        return $result;
    }

    private function publicRow(array $row): array
    {
        $result = [];
        foreach (['id','tenant_id','owner_user_id','source_sha256','source_version','original_filename',
            'legacy_job_id','normalization_version','state','created_at','applied_at'] as $key) {
            $result[$key] = $row[$key] ?? null;
        }
        $result['username'] = (string) $this->db->queryValue('SELECT canonical FROM login_names WHERE user_id=?', [$row['owner_user_id']]);
        return $result;
    }

    private function audit(ProvisionImportContext $context, string $action, string $requestId, string $ip): void
    {
        $this->log->audit($this->db, ['tenant_id' => $context->tenantId, 'action' => 'account_provision.' . $action,
            'actor_type' => 'operator', 'actor_operator_id' => $context->operatorId(), 'entity' => 'account_provision',
            'entity_id' => $context->provisionId, 'request_id' => $requestId, 'ip' => $ip,
            'metadata' => ['provision_id' => $context->provisionId]], true);
    }

    private function requireNormalization(array $row): void
    {
        if ($row['normalization_version'] !== LegacyImport::NORMALIZATION_VERSION) {
            throw new ApiException(409, Codes::VERSION_CONFLICT, 'normalization version changed; create a new provision');
        }
    }

    private function requireWindow(): void
    {
        if (($this->cfg['legacy_import_enabled'] ?? false) !== true) {
            throw new ApiException(503, Codes::CONFIG_INVALID, 'legacy account onboarding is closed');
        }
    }

    private function onlyKeys(array $body, array $allowed): void
    {
        if (array_diff(array_keys($body), $allowed) !== []) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'unsupported provision fields');
        }
    }

    private function uuid(mixed $value): string
    {
        if (!is_string($value) || !Ids::isUuid($value)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'provision_id must be a UUID');
        }
        return $value;
    }

    private function text(array $body, string $key, int $limit): string
    {
        $value = $body[$key] ?? null;
        if (!is_string($value) || $value === '' || strlen($value) > $limit) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'invalid ' . $key);
        }
        return $value;
    }

    private function sourceLimit(): int
    {
        return max(1024, (int) ($this->cfg['limits']['max_import_body_bytes'] ?? 16777216));
    }

    private function json(array $value): string
    {
        return json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    }
}
