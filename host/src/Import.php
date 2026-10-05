<?php
/**
 * Import.php — POST /v1/bootstrap/import, the one-shot migration gate.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §8 and §4.
 *
 *   SELECT ... FOR UPDATE on tenant_state serialises concurrent imports so
 *   only the first transaction wins:
 *     same import_id + same payload hash -> the identical stored result
 *     same import_id + different hash   -> 409 IMPORT_CONFLICT
 *     bootstrap_status='done', or any business row present
 *                                        -> 409 DATASET_NOT_EMPTY
 *
 * Everything is inserted in one transaction with row_version = 1, ids mapped
 * by §4 (uuid5 of installation/entity/legacy id), dataset_epoch incremented
 * and source_installation_id recorded once — never rewritten afterwards.
 *
 * Rows arrive in FK-safe order rather than document order: crm_devices points
 * at customers/owners/periods, customer_transactions at customers and
 * crm_devices, settings at periods. A document that lists them in any other
 * order still imports correctly.
 */

declare(strict_types=1);

final class Import
{
    /** Insertion order that satisfies every foreign key in the schema. */
    private const ORDER = [
        'periods', 'customers', 'owners', 'identities', 'crm_devices',
        'customer_transactions', 'settings',
    ];

    /**
     * @param array<string, mixed> $cfg
     */
    public function __construct(
        private readonly \Database $db,
        private readonly Sync $sync,
        private readonly Log $log,
        private readonly array $cfg
    ) {
    }

    /**
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function run(Principal $device, Principal $user, array $body,
                        string $requestId, string $ip): array
    {
        Roles::requireImport($user->role);

        $importId = isset($body['import_id']) && is_string($body['import_id'])
            ? $body['import_id'] : '';
        $installation = isset($body['source_installation_id'])
            && is_string($body['source_installation_id'])
            ? $body['source_installation_id'] : '';
        if (!Ids::isUuid($importId) || !Ids::isUuid($installation)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'import_id and source_installation_id must be UUIDs');
        }
        $collections = $body['collections'] ?? null;
        if (!is_array($collections) || array_is_list($collections)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'collections must be a JSON object');
        }

        $tenantId = $user->tenantId;
        $documents = $this->normalize($collections);
        $this->ensureState($tenantId);
        $payloadHash = hash('sha256', $this->canonical($documents));

        $result = $this->db->transaction(function (\Database $tx) use (
            $tenantId, $importId, $installation, $documents, $payloadHash,
            $user, $device, $requestId
        ): array {
            // The gate: one row, locked for the whole import (spec §8.4).
            $state = $tx->queryOne(
                'SELECT * FROM tenant_state WHERE tenant_id = ? FOR UPDATE',
                [$tenantId]
            );
            if ($state === null) {
                throw new ApiException(500, Codes::CONFIG_INVALID,
                    'the tenant state row is missing');
            }

            if ($state['last_import_id'] === $importId) {
                if ($state['last_import_hash'] === $payloadHash) {
                    return $this->replay($tx, $importId, $tenantId);
                }
                throw new ApiException(409, Codes::IMPORT_CONFLICT,
                    'this import_id was already used with a different payload');
            }
            if (($state['bootstrap_status'] ?? '') === 'done'
                || $this->businessRows($tx, $tenantId) > 0) {
                throw new ApiException(409, Codes::DATASET_NOT_EMPTY,
                    'the tenant already holds data; a second import is refused');
            }

            $counts = [];
            $now = gmdate('Y-m-d H:i:s');
            foreach (self::ORDER as $entity) {
                $table = Schema::tableForEntity($entity);
                if ($table === null) {
                    continue;
                }
                $entries = $documents[$entity] ?? [];
                $counts[$entity] = 0;
                foreach ($entries as $index => $record) {
                    $uuid = $this->identifier($entity, $record, $installation);
                    $row = $this->sync->recordToRow($entity, $table, $uuid,
                        $tenantId, $record, $requestId, $this->log);
                    $row['row_version'] = 1;
                    $row['created_at'] = $now;
                    $row['updated_at'] = $now;
                    $tx->insert($table, $row);
                    $tx->insert('change_log', [
                        'tenant_id' => $tenantId,
                        'entity' => $entity,
                        'id' => $uuid,
                        'row_version' => 1,
                        'action' => 'upsert',
                        'actor_type' => 'user',
                        'actor_user_id' => $user->userId,
                        'actor_device_id' => $device->deviceId,
                        'operation_id' => $importId,
                        'changed_at' => $now,
                    ]);
                    $counts[$entity]++;
                    unset($index);
                }
            }

            $epoch = (int) $state['dataset_epoch'] + 1;
            $tx->update('tenant_state', [
                'bootstrap_status' => 'done',
                'dataset_epoch' => $epoch,
                'last_import_id' => $importId,
                'last_import_hash' => $payloadHash,
                'source_installation_id' => $installation,
                'updated_at' => $now,
            ], 'tenant_id = ?', [$tenantId]);

            $cursorValue = $tx->queryValue(
                'SELECT MAX(seq) FROM change_log WHERE tenant_id = ?',
                [$tenantId]
            );
            $response = [
                'import_id' => $importId,
                'tenant_id' => $tenantId,
                'dataset_epoch' => $epoch,
                'source_installation_id' => $installation,
                'counts' => $counts,
                'rows' => array_sum($counts),
                'cursor' => $cursorValue === null ? 0 : (int) $cursorValue,
            ];
            // Stored forever enough that a retried import after a lost
            // response still replays byte for byte.
            $tx->insert('idempotency_keys', [
                'operation_id' => $importId,
                'tenant_id' => $tenantId,
                'request_hash' => $payloadHash,
                'status' => 'applied',
                'response' => json_encode($response, JSON_UNESCAPED_SLASHES),
                'created_at' => $now,
                'expires_at' => gmdate('Y-m-d H:i:s', time() + 86400 * 3650),
            ]);
            return $response;
        });

        $this->log->line([
            'request_id' => $requestId, 'action' => 'bootstrap.import',
            'outcome' => 'success', 'tenant_id' => $tenantId,
            'count' => (int) ($result['rows'] ?? 0),
            'cursor' => (int) ($result['cursor'] ?? 0),
            'device_id' => $device->deviceId, 'user_id' => $user->userId,
        ]);
        $this->log->audit($this->db, [
            'tenant_id' => $tenantId,
            'action' => 'bootstrap.import',
            'actor_type' => 'user',
            'actor_user_id' => $user->userId,
            'actor_device_id' => $device->deviceId,
            'entity' => 'tenant_state',
            'entity_id' => $tenantId,
            'request_id' => $requestId,
            'ip' => $ip,
            'outcome' => 'ok',
            'metadata' => [
                'import_id' => $importId,
                'rows' => (int) ($result['rows'] ?? 0),
                'epoch' => (int) ($result['dataset_epoch'] ?? 0),
            ],
        ]);
        return $result;
    }

    /**
     * Re-read a previous result so a replay answers byte for byte.
     *
     * @return array<string, mixed>
     * @throws ApiException
     */
    private function replay(\Database $tx, string $importId,
                            string $tenantId): array
    {
        $stored = $tx->queryOne(
            'SELECT response FROM idempotency_keys WHERE operation_id = ?',
            [$importId]
        );
        if ($stored === null) {
            throw new ApiException(409, Codes::IMPORT_CONFLICT,
                'this import was recorded but its result is no longer stored');
        }
        $decoded = json_decode((string) $stored['response'], true);
        if (!is_array($decoded) || !isset($decoded['tenant_id'])
            || $decoded['tenant_id'] !== $tenantId) {
            throw new ApiException(409, Codes::IMPORT_CONFLICT,
                'the stored import result is not usable');
        }
        return $decoded;
    }

    /**
     * Normalise `collections` into `{entity: [record, …]}`.
     *
     * `settings` is a single object in the client's document (spec §3: one
     * settings object, one row per tenant); every other entity is a list.
     *
     * @param array<string, mixed> $collections
     * @return array<string, list<array<string, mixed>>>
     * @throws ApiException 400
     */
    private function normalize(array $collections): array
    {
        $out = [];
        $maxRows = (int) ($this->cfg['limits']['max_import_rows'] ?? 100000);
        if ($maxRows < 1) {
            $maxRows = 100000;
        }
        $total = 0;
        foreach ($collections as $entity => $value) {
            if (!is_string($entity) || !Ids::isEntity($entity)) {
                throw new ApiException(400, Codes::VALIDATION_FAILED,
                    'collections holds a key outside the sync scope');
            }
            if ($entity === 'settings' && is_array($value)
                && !array_is_list($value)) {
                $value = [$value];
            }
            if (!is_array($value) || !array_is_list($value)) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'collections.' . $entity . ' must be an array');
            }
            $entries = [];
            foreach ($value as $record) {
                if (!is_array($record) || array_is_list($record)) {
                    throw new ApiException(400, Codes::BAD_PAYLOAD,
                        'every collection entry must be a JSON object');
                }
                /** @var array<string, mixed> $record */
                $entries[] = $record;
            }
            if ($entity === 'settings' && count($entries) > 1) {
                throw new ApiException(400, Codes::VALIDATION_FAILED,
                    'settings holds exactly one row per tenant');
            }
            $total += count($entries);
            if ($total > $maxRows) {
                throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE,
                    'the import holds more rows than one request may carry');
            }
            $out[$entity] = $entries;
        }
        return $out;
    }

    /**
     * The §4 identifier of an imported record.
     *
     * The five entities with a legacy id derive their UUID from it; the two
     * synthetic ones (spec §15.2) either already carry the minted UUID in
     * `id` or are recomputed from their own formula.
     *
     * @param array<string, mixed> $record
     * @throws ApiException 400
     */
    private function identifier(string $entity, array $record,
                                string $installation): string
    {
        if (Ids::isSynthetic($entity)) {
            if ($entity === 'settings') {
                return Ids::uuid5($installation, 'settings',
                    Ids::SETTINGS_LEGACY_ID);
            }
            $given = $record['id'] ?? null;
            if (is_string($given) && Ids::isUuid($given)) {
                return strtolower($given);
            }
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                'customer_transactions entries must carry their minted id');
        }
        $legacy = $record['id'] ?? null;
        if (!is_string($legacy) || $legacy === '' || strlen($legacy) > 64) {
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                $entity . ' entries must carry their legacy id');
        }
        return Ids::uuid5($installation, $entity, $legacy);
    }

    /**
     * Total business rows of this tenant — "any business row present" of
     * spec §8.4.
     */
    private function businessRows(\Database $tx, string $tenantId): int
    {
        $parts = [];
        $params = [];
        foreach (Schema::entities() as $table) {
            $parts[] = '(SELECT COUNT(*) FROM ' . $tx->table($table)
                . ' WHERE tenant_id = ?)';
            $params[] = $tenantId;
        }
        $value = $tx->queryValue(
            'SELECT ' . implode(' + ', $parts), $params
        );
        return $value === null ? 0 : (int) $value;
    }

    /**
     * Create the tenant_state row if it is not there yet.
     *
     * It must exist BEFORE the import transaction takes its FOR UPDATE lock,
     * otherwise two concurrent first imports would both try to create it and
     * one would fail on the primary key instead of waiting for the other.
     */
    private function ensureState(string $tenantId): void
    {
        $exists = $this->db->queryValue(
            'SELECT tenant_id FROM tenant_state WHERE tenant_id = ?',
            [$tenantId]
        );
        if ($exists !== null) {
            return;
        }
        $now = gmdate('Y-m-d H:i:s');
        try {
            $this->db->insert('tenant_state', [
                'tenant_id' => $tenantId,
                'bootstrap_status' => 'pending',
                'dataset_epoch' => 0,
                'last_import_id' => null,
                'last_import_hash' => null,
                'source_installation_id' => null,
                'created_at' => $now,
                'updated_at' => $now,
            ]);
        } catch (\PDOException $error) {
            // 1062 = another request created it between the check and here.
            $info = $error->errorInfo ?? null;
            if (!is_array($info) || (int) ($info[1] ?? 0) !== 1062) {
                throw $error;
            }
        }
    }

    /**
     * A stable textual form of the document, so two byte-different but
     * equivalent JSON bodies still hash the same.
     *
     * @param array<string, mixed> $value
     */
    private function canonical(array $value): string
    {
        $walk = static function (mixed $node) use (&$walk): mixed {
            if (!is_array($node)) {
                return $node;
            }
            if (array_is_list($node)) {
                return array_map($walk, $node);
            }
            ksort($node);
            $out = [];
            foreach ($node as $key => $item) {
                $out[$key] = $walk($item);
            }
            return $out;
        };
        return (string) json_encode($walk($value), JSON_UNESCAPED_SLASHES
            | JSON_UNESCAPED_UNICODE);
    }
}
