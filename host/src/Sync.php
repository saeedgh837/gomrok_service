<?php
/**
 * Sync.php — GET /sync/bootstrap, GET /sync/changes, POST /sync/mutations.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §6.1 (idempotent, CAS, one
 * transaction, conflicts are data), §6.2 (cursor is `seq` only, snapshot and
 * cursor from one transaction), §3/§4/§15.1 (the wire record versus the
 * sync envelope), §7 (role matrix), §10.6 (prepared statements, identifiers
 * from the Schema allowlist).
 *
 * Two shapes never mix, which is spec §15.1's hard rule:
 *
 *   envelope  {id: UUID, tenant_id, row_version, created_at, updated_at,
 *              deleted_at, deleted_by}      -> SQL columns, server-minted
 *   record    business columns only, where `id` is the legacy id for the five
 *              entities that have one, and a minted UUID for settings and
 *              customer_transactions (spec §15.2)
 *
 * `record` is what travels inside `collections`, `changes[].record` and
 * `conflicts[].server_record`; the envelope sits beside it.
 */

declare(strict_types=1);

final class Sync
{
    private const DEFAULT_PAGE = 500;
    private const MAX_PAGE = 5000;
    private const DEFAULT_OPERATIONS = 500;

    /**
     * @param array<string, mixed> $cfg
     */
    public function __construct(
        private readonly \Database $db,
        private readonly Log $log,
        private readonly array $cfg
    ) {
    }

    private function intOf(string $key, int $default): int
    {
        $value = $this->cfg[$key] ?? $default;
        return is_int($value) && $value > 0 ? $value : $default;
    }

    // ==================================================================
    // Row <-> record
    // ==================================================================

    /**
     * A database row as the wire `record` (business columns + `id`).
     *
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    public function rowToRecord(string $table, array $row): array
    {
        $entity = (string) Schema::entityForTable($table);
        $record = [];
        foreach (Schema::businessColumns($table) as $column) {
            $spec = Schema::column($table, $column);
            if ($spec === null) {
                continue;
            }
            $value = $row[$column] ?? null;
            if ($value === null) {
                // A list column reads back as [] rather than null: that is
                // what the mapper's _encode produces for an absent value.
                $record[$column] = $spec['format'] === 'json' ? [] : null;
                continue;
            }
            if ($spec['type'] === 'datetime') {
                $record[$column] = Schema::toWireTimestamp((string) $value);
                continue;
            }
            if ($spec['type'] === 'tinyint') {
                $record[$column] = (int) $value === 1;
                continue;
            }
            if ($spec['format'] === 'json') {
                $decoded = json_decode((string) $value, true);
                $record[$column] = is_array($decoded) ? $decoded : [];
                continue;
            }
            if ($spec['type'] === 'int' || $spec['type'] === 'bigint') {
                $record[$column] = (int) $value;
                continue;
            }
            $record[$column] = (string) $value;
        }

        if (Ids::isSynthetic($entity)) {
            $record['id'] = (string) $row['id'];
        } elseif (isset($row['legacy_id']) && $row['legacy_id'] !== null
            && $row['legacy_id'] !== '') {
            // A row born on the host with no legacy id would break the
            // client's reverse mapping, so the key is omitted rather than
            // sent empty; writes guarantee this cannot happen (see below).
            $record['id'] = (string) $row['legacy_id'];
        }
        return $record;
    }

    /**
     * The wire `record` of one operation as the columns to write.
     *
     * Drops keys the client must not set (the server mints the rest of the
     * envelope), drops unknown columns with a warning rather than rejecting
     * the batch (spec §3: an unknown key is dropped on write), and derives
     * `legacy_id` per spec §15.1/§15.2.
     *
     * @param array<string, mixed> $record
     * @return array<string, mixed> a complete row ready for validateRow()
     * @throws ApiException 400 VALIDATION_FAILED
     */
    public function recordToRow(string $entity, string $table, string $uuid,
                                string $tenantId, array $record,
                                string $requestId, Log $log): array
    {
        $envelope = Schema::envelopeFor($table);
        $business = [];
        foreach ($record as $key => $value) {
            if (!is_string($key)) {
                continue;
            }
            if (in_array($key, $envelope, true)) {
                continue;               // the server owns the envelope
            }
            if (!Schema::hasColumn($table, $key)) {
                $log->line([
                    'request_id' => $requestId, 'action' => 'sync.unknown_column',
                    'outcome' => 'dropped', 'entity' => $entity,
                    'detail' => $entity . '.' . substr($key, 0, 40),
                ]);
                continue;
            }
            $business[$key] = $value;
        }
        unset($business['id']);

        if (Ids::isSynthetic($entity)) {
            $legacyId = $this->syntheticLegacyId($entity, $business, $tenantId);
        } else {
            $raw = $record['id'] ?? null;
            if (!is_string($raw) || $raw === '' || strlen($raw) > 64) {
                throw new ApiException(400, Codes::VALIDATION_FAILED,
                    $entity . ' records must carry their legacy id');
            }
            $legacyId = $raw;
        }

        // JSON columns travel as real JSON values and are stored as text.
        foreach ($business as $column => $value) {
            $spec = Schema::column($table, (string) $column);
            if ($spec === null || $spec['format'] !== 'json') {
                continue;
            }
            if (is_array($value)) {
                $encoded = json_encode($value, JSON_UNESCAPED_SLASHES
                    | JSON_UNESCAPED_UNICODE);
                if ($encoded === false) {
                    throw new ApiException(400, Codes::VALIDATION_FAILED,
                        $entity . '.' . $column . ' is not encodable');
                }
                $business[$column] = $encoded;
            } elseif ($value === null) {
                $business[$column] = null;
            }
        }

        // Spec §4 timestamps carry an explicit UTC offset, and validateRow
        // enforces that rule on the WHOLE row — the server's own envelope
        // included. Database::insert folds the offset back to UTC at bind
        // time, so the wire form is what has to be minted here.
        $now = gmdate('Y-m-d\TH:i:s+00:00');
        $row = [
            'id' => $uuid,
            'tenant_id' => $tenantId,
            'legacy_id' => $legacyId,
            'row_version' => 1,
            'created_at' => $now,
            'updated_at' => $now,
            'deleted_at' => null,
            'deleted_by' => null,
        ] + $business;

        $failures = Schema::validateRow($table, $row);
        if ($failures !== []) {
            $log->line([
                'request_id' => $requestId, 'action' => 'sync.validation',
                'outcome' => 'rejected', 'entity' => $entity,
                'detail' => $this->describeFailures($table, $failures),
            ]);
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                $entity . ' record failed schema validation');
        }
        // PDO must never receive a boolean: with native prepares `false`
        // would bind as an empty string and be rejected by strict SQL mode.
        foreach ($row as $column => $value) {
            if (is_bool($value)) {
                $row[$column] = $value ? 1 : 0;
            }
        }
        return $row;
    }

    /**
     * Codes and column names only — never the value that failed (§10.9).
     *
     * @param list<array{code: string, table: string, column: string}> $failures
     */
    private function describeFailures(string $table, array $failures): string
    {
        $parts = [];
        foreach (array_slice($failures, 0, 8) as $failure) {
            $parts[] = $failure['code'] . ':' . $failure['table']
                . '.' . $failure['column'];
        }
        return implode(',', $parts);
    }

    /**
     * Spec §15.2: the two entities that have no 8-hex id of their own.
     *
     * @param array<string, mixed> $business
     * @throws ApiException 400 VALIDATION_FAILED
     */
    private function syntheticLegacyId(string $entity, array $business,
                                       string $tenantId): string
    {
        if ($entity === 'settings') {
            return Ids::SETTINGS_LEGACY_ID;
        }
        $seq = $business['seq'] ?? null;
        if (!is_int($seq) && !(is_string($seq) && ctype_digit($seq))) {
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                'customer_transactions records need a sequence number');
        }
        $customerId = $business['customer_id'] ?? null;
        if (!is_string($customerId) || !Ids::isUuid($customerId)) {
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                'customer_transactions records need a customer reference');
        }
        $legacy = $this->db->queryValue(
            'SELECT legacy_id FROM customers WHERE id = ? AND tenant_id = ?',
            [$customerId, $tenantId]
        );
        if (!is_string($legacy) || $legacy === '') {
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                'customer_transactions references a customer this tenant '
                . 'does not have');
        }
        return Ids::transactionLegacyId($legacy, (int) $seq);
    }

    // ==================================================================
    // GET /v1/sync/bootstrap
    // ==================================================================

    /**
     * Snapshot and cursor from ONE transaction (spec §6.2), so nothing
     * committed between them can be lost.
     *
     * @return array<string, mixed>
     */
    public function bootstrap(string $tenantId): array
    {
        return $this->db->transaction(function (\Database $tx) use ($tenantId): array {
            $collections = [];
            foreach (Schema::entities() as $entity => $table) {
                $rows = $tx->query(
                    'SELECT * FROM ' . $tx->table($table)
                    . ' WHERE tenant_id = ? AND deleted_at IS NULL',
                    [$tenantId]
                );
                $entries = [];
                foreach ($rows as $row) {
                    $entries[] = [
                        'id' => (string) $row['id'],
                        'tenant_id' => (string) $row['tenant_id'],
                        'row_version' => (int) $row['row_version'],
                        'record' => $this->rowToRecord($table, $row),
                    ];
                }
                $collections[$entity] = $entries;
            }
            $cursor = $tx->queryValue(
                'SELECT MAX(seq) FROM change_log WHERE tenant_id = ?',
                [$tenantId]
            );
            $state = $tx->queryOne(
                'SELECT dataset_epoch, bootstrap_status,'
                . ' source_installation_id FROM tenant_state WHERE tenant_id = ?',
                [$tenantId]
            );
            return [
                'collections' => $collections,
                'cursor' => $cursor === null ? 0 : (int) $cursor,
                'dataset_epoch' => $state === null
                    ? 0 : (int) $state['dataset_epoch'],
                'tenant_id' => $tenantId,
                'bootstrap_status' => $state === null
                    ? 'pending' : (string) $state['bootstrap_status'],
                'source_installation_id' => $state !== null
                    && $state['source_installation_id'] !== null
                    ? (string) $state['source_installation_id'] : null,
            ];
        });
    }

    // ==================================================================
    // GET /v1/sync/changes
    // ==================================================================

    /**
     * One page of the change log, filtered by `seq > cursor` ONLY.
     *
     * Filtering by `updated_at` would silently drop rows that share a
     * timestamp or arrive from a skewed clock (spec §6.2), so it is never
     * used here — not even as a secondary key.
     *
     * @return array<string, mixed>
     * @throws ApiException 400 BAD_PAYLOAD
     */
    public function changes(string $tenantId, ?int $cursor, ?int $limit): array
    {
        $page = $this->intOf('limits.default_changes_limit', self::DEFAULT_PAGE);
        $max = $this->intOf('limits.max_changes_limit', self::MAX_PAGE);
        if ($cursor === null || $cursor < 0) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'cursor must be a non-negative integer');
        }
        if ($limit === null) {
            $limit = $page;
        }
        if ($limit < 1 || $limit > $max) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'limit is outside the accepted range');
        }

        $rows = $this->db->query(
            'SELECT seq, entity, id, row_version FROM change_log'
            . ' WHERE tenant_id = ? AND seq > ? ORDER BY seq LIMIT ' . ($limit + 1),
            [$tenantId, $cursor]
        );
        $hasMore = count($rows) > $limit;
        if ($hasMore) {
            $rows = array_slice($rows, 0, $limit);
        }

        $live = $this->loadLiveRows($tenantId, $rows);
        $changes = [];
        foreach ($rows as $entry) {
            $entity = (string) $entry['entity'];
            $id = (string) $entry['id'];
            $table = Schema::tableForEntity($entity);
            if ($table === null) {
                continue;
            }
            $row = $live[$entity][$id] ?? null;
            $record = null;
            $version = (int) ($row['row_version'] ?? 0);
            if ($row !== null && $row['deleted_at'] === null) {
                $record = $this->rowToRecord($table, $row);
            } elseif ($row === null) {
                $version = (int) $entry['row_version'];
            }
            $changes[] = [
                'entity' => $entity,
                'id' => $id,
                'seq' => (int) $entry['seq'],
                'row_version' => $version,
                'record' => $record,
            ];
        }
        $next = $cursor;
        if ($changes !== []) {
            $next = (int) $changes[count($changes) - 1]['seq'];
        }
        return [
            'changes' => $changes,
            'cursor' => $next,
            'has_more' => $hasMore,
        ];
    }

    /**
     * Fetch the current rows a page of change_log entries refers to, in one
     * query per entity.
     *
     * The record returned is the row as it stands NOW, not as it stood when
     * the change was appended: `change_log` stores no payload, and sending a
     * stale `row_version` next to a fresh record would make the client's next
     * push conflict against a version the server no longer has.
     *
     * @param list<array<string, mixed>> $entries
     * @return array<string, array<string, array<string, mixed>>>
     */
    private function loadLiveRows(string $tenantId, array $entries): array
    {
        $byEntity = [];
        foreach ($entries as $entry) {
            $entity = (string) ($entry['entity'] ?? '');
            if (!Schema::tableForEntity($entity)) {
                continue;
            }
            $byEntity[$entity][(string) $entry['id']] = true;
        }
        $out = [];
        foreach ($byEntity as $entity => $ids) {
            $table = Schema::tableForEntity($entity);
            if ($table === null || $ids === []) {
                continue;
            }
            $names = array_keys($ids);
            $placeholders = implode(', ', array_fill(0, count($names), '?'));
            $sql = 'SELECT * FROM ' . $this->db->table($table)
                . ' WHERE tenant_id = ? AND id IN (' . $placeholders . ')';
            $rows = $this->db->query($sql, array_merge([$tenantId], $names));
            $map = [];
            foreach ($rows as $row) {
                $map[(string) $row['id']] = $row;
            }
            $out[$entity] = $map;
        }
        return $out;
    }

    // ==================================================================
    // POST /v1/sync/mutations
    // ==================================================================

    /**
     * Refuse a write whose caller was materialized from another dataset
     * generation (spec §8: "local data of an unknown or foreign epoch is
     * quarantined and never auto-pushed").
     *
     * The check has to take the same row the import path locks exclusively,
     * or an import committing between this read and the write would still land
     * the batch in the new dataset. It takes a SHARED lock rather than FOR
     * UPDATE: several desktops write concurrently by design (v1.14.0), and an
     * exclusive lock on this single row would serialise every mutation in a
     * tenant against every other. A shared lock still blocks against the
     * import's `FOR UPDATE` and cannot be upgraded to write, which is exactly
     * the mutual exclusion the epoch contract needs.
     *
     * A caller that sends no epoch is an older desktop that has not been
     * updated yet; it keeps its previous behaviour, because inventing a
     * refusal for a field it never knew about would strand it. The field is
     * therefore opt-in: absence is allowed, a *different* value never is.
     *
     * @param mixed $claimed the caller's `dataset_epoch`, or null when absent
     * @throws ApiException 400 BAD_PAYLOAD, 409 DATASET_EPOCH_MISMATCH
     */
    public static function requireEpoch(\Database $tx, string $tenantId,
                                        mixed $claimed): void
    {
        if ($claimed === null) {
            return;
        }
        if (is_bool($claimed)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'dataset_epoch must be an integer or a numeric string');
        }
        if (is_string($claimed)) {
            if (!ctype_digit($claimed)) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'dataset_epoch must be an integer or a numeric string');
            }
        } elseif (!is_int($claimed)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'dataset_epoch must be an integer or a numeric string');
        }
        $current = $tx->queryValue(
            'SELECT dataset_epoch FROM tenant_state WHERE tenant_id = ? '
            . 'LOCK IN SHARE MODE',
            [$tenantId]
        );
        // No state row yet means no dataset was ever imported: the host is
        // empty, not a different generation, so the caller is not foreign.
        if ($current === null) {
            return;
        }
        if ((int) $current !== (int) $claimed) {
            throw new ApiException(409, Codes::DATASET_EPOCH_MISMATCH,
                'this desktop was materialized from another dataset '
                . 'generation; re-bootstrap before pushing');
        }
    }

    /**
     * Apply one batch: one transaction, idempotent per operation_id,
     * compare-and-swap per row, conflicts reported as data (spec §6.1).
     *
     * @param list<array<string, mixed>> $operations
     * @param array{device: ?Principal, user: ?Principal, role: string} $context
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function mutations(array $operations, array $context,
                              string $requestId,
                              mixed $datasetEpoch = null): array
    {
        $device = $context['device'] ?? null;
        $user = $context['user'] ?? null;
        if ($device instanceof Principal) {
            $tenantId = $device->tenantId;
        } elseif ($user instanceof Principal) {
            $tenantId = $user->tenantId;
        } else {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'an authenticated credential is required');
        }
        $max = $this->intOf('limits.max_operations', self::DEFAULT_OPERATIONS);
        if ($operations === []) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'operations must be a non-empty array');
        }
        if (count($operations) > $max) {
            throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE,
                'the batch holds more operations than one request may carry');
        }

        $prepared = [];
        $ids = [];
        foreach ($operations as $index => $operation) {
            $prepared[] = $this->preflight($operation, $index, $tenantId,
                $context['role'], $ids);
        }

        $result = $this->db->transaction(function (\Database $tx) use (
            $prepared, $tenantId, $context, $requestId, $datasetEpoch
        ): array {
            // Before anything is written: a caller from a replaced dataset
            // must not land a row here (spec §8).
            self::requireEpoch($tx, $tenantId, $datasetEpoch);
            $replayed = $this->loadReplays($tx, $tenantId, $prepared);
            // Apply in FK-safe entity order (crm_devices needs its customer,
            // a ledger row needs its device, settings needs its period), but
            // report in the order the client sent: `applied[]` and
            // `conflicts[]` are positional evidence of this batch.
            $outcomes = [];
            foreach ($this->inForeignKeyOrder($prepared) as $index => $op) {
                if (isset($replayed[$op['operation_id']])) {
                    $outcomes[$index] = $replayed[$op['operation_id']];
                    continue;
                }
                $outcome = $this->applyOne($tx, $op, $tenantId, $context,
                    $requestId);
                $this->storeReplay($tx, $op, $outcome, $tenantId);
                $outcomes[$index] = $outcome;
            }
            $applied = [];
            $conflicts = [];
            foreach ($prepared as $index => $op) {
                $outcome = $outcomes[$index] ?? null;
                if (!is_array($outcome)) {
                    continue;
                }
                if (($outcome['status'] ?? '') === 'applied') {
                    $applied[] = $op['operation_id'];
                } elseif (isset($outcome['conflict'])) {
                    $conflicts[] = $outcome['conflict'];
                }
            }
            $cursor = $tx->queryValue(
                'SELECT MAX(seq) FROM change_log WHERE tenant_id = ?',
                [$tenantId]
            );
            $this->pruneIdempotency($tx);
            return [
                'applied' => $applied,
                'conflicts' => $conflicts,
                'cursor' => $cursor === null ? 0 : (int) $cursor,
            ];
        });

        $this->log->line([
            'request_id' => $requestId, 'action' => 'sync.mutations',
            'outcome' => 'applied', 'tenant_id' => $tenantId,
            'count' => count($result['applied']),
            'conflicts' => count($result['conflicts']),
            'cursor' => $result['cursor'],
            'device_id' => $context['device'] !== null
                ? $context['device']->deviceId : null,
            'user_id' => $context['user'] !== null
                ? $context['user']->userId : null,
        ]);
        $this->log->audit($this->db, [
            'tenant_id' => $tenantId,
            'action' => 'sync.mutations',
            'actor_type' => $context['device'] !== null ? 'device' : 'user',
            'actor_user_id' => $context['user'] !== null
                && $context['user']->userId !== ''
                ? $context['user']->userId : null,
            'actor_device_id' => $context['device'] !== null
                ? $context['device']->deviceId : null,
            'request_id' => $requestId,
            'outcome' => 'ok',
            // Counts only: never which record changed, never its contents.
            'metadata' => [
                'applied' => count($result['applied']),
                'conflicts' => count($result['conflicts']),
            ],
        ]);
        return $result;
    }

    /**
     * Structural, size and authorization checks for the WHOLE batch, before
     * a single row is written (spec §6.1 validates first, and a role denial
     * must not leave a half-applied batch behind).
     *
     * @param mixed $operation
     * @param array<string, string> $seen
     * @return array<string, mixed>
     * @throws ApiException
     */
    private function preflight(mixed $operation, int $index, string $tenantId,
                               string $role, array &$seen): array
    {
        if (!is_array($operation) || array_is_list($operation)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'each operation must be a JSON object');
        }
        $operationId = $operation['operation_id'] ?? null;
        $kind = $operation['kind'] ?? null;
        $entity = $operation['entity'] ?? null;
        $id = $operation['id'] ?? null;
        $base = $operation['base_version'] ?? null;
        $record = $operation['record'] ?? null;

        if (!is_string($operationId) || !Ids::isUuid($operationId)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'operation_id must be a UUID');
        }
        if (!is_string($kind) || !in_array($kind, ['upsert', 'delete'], true)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'operation kind must be upsert or delete');
        }
        if (!is_string($entity) || !Ids::isEntity($entity)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'operation entity is not part of the sync scope');
        }
        if (!is_string($id) || !Ids::isUuid($id)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'operation id must be a UUID');
        }
        if (is_bool($base) || !is_int($base) || $base < 0) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'base_version must be a non-negative integer');
        }
        if ($kind === 'upsert' && (!is_array($record) || array_is_list($record))) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'an upsert operation needs a record object');
        }
        if ($kind === 'delete' && $record !== null && !is_array($record)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'a delete operation record must be an object or null');
        }

        $table = (string) Schema::tableForEntity($entity);
        Roles::requireWrite($role, $entity, $kind);

        // No IDOR oracle (spec §7): an id this tenant does not own answers
        // 404 exactly like an id that does not exist anywhere.
        $foreign = $this->db->queryOne(
            'SELECT id, tenant_id FROM ' . $this->db->table($table)
            . ' WHERE id = ?',
            [$id]
        );
        if ($foreign !== null && $foreign['tenant_id'] !== $tenantId) {
            throw new ApiException(404, Codes::NOT_FOUND,
                'no such record in this tenant');
        }

        if (isset($seen[$operationId])) {
            // The same id twice in one batch: the second is a replay of the
            // first, which is exactly what idempotency_keys will return.
            unset($seen[$operationId]);
        }
        $seen[$operationId] = $index;

        return [
            'operation_id' => $operationId,
            'kind' => $kind,
            'entity' => $entity,
            'table' => $table,
            'id' => $id,
            'base_version' => $base,
            'record' => is_array($record) ? $record : [],
        ];
    }

    /**
     * @param list<array<string, mixed>> $prepared
     * @return list<array<string, mixed>> same entries, original indexes kept,
     *         stable-sorted so operations inside one entity keep their order
     */
    private function inForeignKeyOrder(array $prepared): array
    {
        $rank = [
            'periods' => 0, 'customers' => 1, 'owners' => 2,
            'identities' => 3, 'crm_devices' => 4,
            'customer_transactions' => 5, 'settings' => 6,
        ];
        $indexed = [];
        foreach ($prepared as $index => $op) {
            $indexed[$index] = $op;
        }
        uasort($indexed, static function (array $a, array $b) use ($rank): int {
            return ($rank[$a['entity']] ?? 99) <=> ($rank[$b['entity']] ?? 99);
        });
        return $indexed;
    }

    /**
     * @param list<array<string, mixed>> $prepared
     * @return array<string, array<string, mixed>> operation_id => stored result
     */
    private function loadReplays(\Database $tx, string $tenantId, array $prepared): array
    {
        $ids = array_column($prepared, 'operation_id');
        if ($ids === []) {
            return [];
        }
        $placeholders = implode(', ', array_fill(0, count($ids), '?'));
        $rows = $tx->query(
            'SELECT operation_id, status, response FROM idempotency_keys'
            . ' WHERE tenant_id = ? AND operation_id IN (' . $placeholders . ')',
            array_merge([$tenantId], $ids)
        );
        $out = [];
        foreach ($rows as $row) {
            $decoded = json_decode((string) $row['response'], true);
            if (is_array($decoded)) {
                $out[(string) $row['operation_id']] = $decoded;
            }
        }
        return $out;
    }

    /**
     * @param array<string, mixed> $outcome
     */
    private function storeReplay(\Database $tx, array $op, array $outcome,
                                 string $tenantId): void
    {
        $days = (int) ($this->cfg['idempotency_retention_days'] ?? 30);
        if ($days < 1) {
            $days = 30;
        }
        $now = time();
        $tx->insert('idempotency_keys', [
            'operation_id' => $op['operation_id'],
            'tenant_id' => $tenantId,
            'request_hash' => hash('sha256', json_encode([
                $op['kind'], $op['entity'], $op['id'], $op['base_version'],
            ], JSON_UNESCAPED_SLASHES) ?: ''),
            'status' => $outcome['status'] === 'applied' ? 'applied' : 'conflict',
            'response' => json_encode($outcome, JSON_UNESCAPED_SLASHES),
            'created_at' => gmdate('Y-m-d H:i:s', $now),
            'expires_at' => gmdate('Y-m-d H:i:s', $now + $days * 86400),
        ]);
    }

    private function pruneIdempotency(\Database $tx): void
    {
        static $last = 0;
        if (time() - $last < 600) {
            return;
        }
        $last = time();
        try {
            $tx->delete('idempotency_keys', 'expires_at < ?',
                [gmdate('Y-m-d H:i:s')]);
        } catch (\Throwable $error) {
            unset($error);
        }
    }

    /**
     * Apply one operation inside the batch transaction.
     *
     * @param array<string, mixed> $op
     * @param array<string, mixed> $context
     * @return array{status: string, conflict?: array<string, mixed>}
     * @throws ApiException
     */
    private function applyOne(\Database $tx, array $op, string $tenantId,
                              array $context, string $requestId): array
    {
        $table = $op['table'];
        $entity = $op['entity'];
        $uuid = $op['id'];
        $base = $op['base_version'];
        $kind = $op['kind'];

        $target = $this->findTarget($tx, $table, $entity, $uuid, $tenantId,
            $kind === 'upsert' ? $op['record'] : null);

        if ($kind === 'delete') {
            if ($target === null) {
                if ($base !== 0) {
                    return $this->conflict($op, 0, null);
                }
                // Deleting a row the server never had is a no-op: there is
                // nothing to tombstone and nothing for another machine to
                // learn, and a bare tombstone insert would have to invent
                // every NOT NULL column of the table.
                return ['status' => 'applied'];
            }
            if ((int) $target['row_version'] !== $base) {
                return $this->conflictFromRow($op, $table, $target);
            }
            if ($target['deleted_at'] !== null) {
                return ['status' => 'applied'];   // already a tombstone
            }
            $now = gmdate('Y-m-d H:i:s');
            $next = (int) $target['row_version'] + 1;
            $changed = $tx->update($table, [
                'deleted_at' => $now,
                'deleted_by' => $this->actorUserId($context),
                'row_version' => $next,
                'updated_at' => $now,
            ], $this->casWhere($table, $uuid), [$uuid, $tenantId, $base]);
            if ($changed === 0) {
                $reloaded = $tx->queryOne(
                    'SELECT * FROM ' . $tx->table($table) . ' WHERE id = ?',
                    [$uuid]
                );
                return $reloaded === null
                    ? $this->conflict($op, 0, null)
                    : $this->conflictFromRow($op, $table, $reloaded);
            }
            $this->appendChange($tx, $entity, $uuid, $next, 'delete',
                $op['operation_id'], $context, $tenantId);
            return ['status' => 'applied'];
        }

        // ---- upsert ------------------------------------------------
        $row = $this->recordToRow($entity, $table, $uuid, $tenantId,
            $op['record'], $requestId, $this->log);

        if ($target !== null) {
            if ((int) $target['row_version'] !== $base) {
                return $this->conflictFromRow($op, $table, $target);
            }
            $now = gmdate('Y-m-d H:i:s');
            $next = (int) $target['row_version'] + 1;
            unset($row['id'], $row['tenant_id'], $row['created_at'],
                $row['legacy_id']);
            $row['row_version'] = $next;
            $row['updated_at'] = $now;
            $row['deleted_at'] = null;
            $row['deleted_by'] = null;
            $changed = $tx->update($table, $row,
                $this->casWhere($table, (string) $target['id']),
                [(string) $target['id'], $tenantId, $base]);
            if ($changed === 0) {
                $reloaded = $tx->queryOne(
                    'SELECT * FROM ' . $tx->table($table) . ' WHERE id = ?',
                    [(string) $target['id']]
                );
                return $reloaded === null
                    ? $this->conflict($op, 0, null)
                    : $this->conflictFromRow($op, $table, $reloaded);
            }
            $this->appendChange($tx, $entity, (string) $target['id'], $next,
                'upsert', $op['operation_id'], $context, $tenantId);
            return ['status' => 'applied'];
        }

        if ($base !== 0) {
            // CAS against a row this tenant has never seen: nothing to
            // compare with, so it is a conflict, not a free create.
            return $this->conflict($op, 0, null);
        }
        $tx->insert($table, $row);
        $this->appendChange($tx, $entity, $uuid, 1, 'upsert',
            $op['operation_id'], $context, $tenantId);
        return ['status' => 'applied'];
    }

    /**
     * Locate the row an operation addresses, tolerating a legacy-id match
     * under a different uuid (two installations that minted different ids for
     * the same logical record). Adopting the existing row keeps the unique
     * index intact and lets the data converge instead of deadlocking.
     *
     * @param array<string, mixed>|null $record
     * @return array<string, mixed>|null
     */
    private function findTarget(\Database $tx, string $table, string $entity,
                                string $uuid, string $tenantId,
                                ?array $record): ?array
    {
        $row = $tx->queryOne(
            'SELECT * FROM ' . $tx->table($table) . ' WHERE id = ?',
            [$uuid]
        );
        if ($row !== null) {
            return $row['tenant_id'] === $tenantId ? $row : null;
        }

        $legacyId = null;
        if (Ids::isSynthetic($entity)) {
            $legacyId = $entity === 'settings'
                ? Ids::SETTINGS_LEGACY_ID : null;
            if ($entity === 'customer_transactions' && $record !== null) {
                $legacyId = $this->transactionLegacyIfResolvable($record,
                    $tenantId);
            }
        } elseif ($record !== null && isset($record['id'])
            && is_string($record['id']) && $record['id'] !== '') {
            $legacyId = substr($record['id'], 0, 64);
        }

        if ($legacyId !== null) {
            $alt = $tx->queryOne(
                'SELECT * FROM ' . $tx->table($table)
                . ' WHERE tenant_id = ? AND legacy_id = ?',
                [$tenantId, $legacyId]
            );
            if ($alt !== null) {
                return $alt;
            }
        }
        if ($table === 'settings') {
            // One settings row per tenant is a schema guarantee
            // (UNIQUE uq_settings_tenant); address it by tenant when its id
            // differs from the one this machine computed.
            $alt = $tx->queryOne(
                'SELECT * FROM settings WHERE tenant_id = ?',
                [$tenantId]
            );
            if ($alt !== null) {
                return $alt;
            }
        }
        return null;
    }

    /**
     * Best-effort legacy id for a ledger row that may not exist yet (it is
     * created earlier in the same batch). null means "no legacy hint".
     *
     * @param array<string, mixed> $record
     */
    private function transactionLegacyIfResolvable(array $record,
                                                   string $tenantId): ?string
    {
        $seq = $record['seq'] ?? null;
        $customerId = $record['customer_id'] ?? null;
        if ((!is_int($seq) && !(is_string($seq) && ctype_digit((string) $seq)))
            || !is_string($customerId) || !Ids::isUuid($customerId)) {
            return null;
        }
        $legacy = $this->db->queryValue(
            'SELECT legacy_id FROM customers WHERE id = ? AND tenant_id = ?',
            [$customerId, $tenantId]
        );
        if (!is_string($legacy) || $legacy === '') {
            return null;
        }
        return Ids::transactionLegacyId($legacy, (int) $seq);
    }

    /** The WHERE clause of a compare-and-swap, from allowlisted names only. */
    private function casWhere(string $table, string $id): string
    {
        return $this->db->column($table, 'id') . ' = ? AND '
            . $this->db->column($table, 'tenant_id') . ' = ? AND '
            . $this->db->column($table, 'row_version') . ' = ?';
    }

    /**
     * @param array<string, mixed> $op
     * @return array{status: string, conflict: array<string, mixed>}
     */
    private function conflict(array $op, int $serverVersion,
                              ?array $serverRecord): array
    {
        return [
            'status' => 'conflict',
            'conflict' => [
                'operation_id' => $op['operation_id'],
                'entity' => $op['entity'],
                'id' => $op['id'],
                'server_version' => $serverVersion,
                'server_record' => $serverRecord,
            ],
        ];
    }

    /**
     * @param array<string, mixed> $op
     * @param array<string, mixed> $row
     * @return array{status: string, conflict: array<string, mixed>}
     */
    private function conflictFromRow(array $op, string $table,
                                     array $row): array
    {
        $record = $row['deleted_at'] !== null
            ? null : $this->rowToRecord($table, $row);
        return $this->conflict($op, (int) $row['row_version'], $record);
    }

    /**
     * Append one change_log entry and return nothing: the cursor is read as
     * MAX(seq) once the whole batch has committed.
     *
     * @param array<string, mixed> $context
     */
    private function appendChange(\Database $tx, string $entity, string $id,
                                  int $rowVersion, string $action,
                                  string $operationId, array $context,
                                  string $tenantId): void
    {
        $user = $context['user'] ?? null;
        $device = $context['device'] ?? null;
        $tx->insert('change_log', [
            'tenant_id' => $tenantId,
            'entity' => $entity,
            'id' => $id,
            'row_version' => $rowVersion,
            'action' => $action,
            'actor_type' => $device !== null ? 'device'
                : ($user !== null ? 'user' : 'system'),
            'actor_user_id' => $this->actorUserId($context),
            'actor_device_id' => $device !== null ? $device->deviceId : null,
            'operation_id' => $operationId,
            'changed_at' => gmdate('Y-m-d H:i:s'),
        ]);
    }

    /** The user to attribute a write to, or null when there is none. */
    private function actorUserId(array $context): ?string
    {
        if (isset($context['user']) && $context['user'] !== null
            && $context['user']->userId !== '') {
            return $context['user']->userId;
        }
        if (isset($context['device']) && $context['device'] !== null
            && $context['device']->userId !== '') {
            return $context['device']->userId;
        }
        return null;
    }
}
