<?php
/** Private-account identity policy shared by every customer credential path. */
declare(strict_types=1);

final class Workspace
{
    public const PLATFORM_TENANT = '00000000-0000-4000-8000-000000000001';

    public static function canonical(string $username): string
    {
        $name = strtolower(trim($username));
        if (preg_match('/\A[a-z0-9][a-z0-9._-]{2,63}\z/D', $name) !== 1) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'username must contain 3 to 64 ASCII letters, digits, dots, underscores or hyphens');
        }
        return $name;
    }

    /** @return array<string, mixed>|null */
    public static function resolveLoginName(Database $db, string $username): ?array
    {
        try {
            $name = self::canonical($username);
        } catch (ApiException $error) {
            return null;
        }
        // Reserved names still verify a real password before the workspace gate;
        // a blocked collision never selects one of its former users.
        return $db->queryOne(
            'SELECT u.id, u.tenant_id, u.username, u.password_hash, u.display_name,'
            . ' u.must_change_password, u.status, u.token_version'
            . ' FROM login_names n JOIN users u ON u.id = n.user_id'
            . ' WHERE n.canonical = ? AND n.state IN (?, ?)'
            . ' AND u.deleted_at IS NULL',
            [$name, 'active', 'reserved']
        );
    }

    /** A tenant member is not a second login to its owner's private dataset. */
    public static function requireActive(Database $db, string $tenantId,
                                         string $userId): void
    {
        $row = $db->queryOne(
            'SELECT u.username, n.canonical FROM account_workspaces w'
            . ' JOIN tenants t ON t.id = w.tenant_id'
            . ' JOIN users u ON u.id = w.owner_user_id AND u.tenant_id = w.tenant_id'
            . ' JOIN login_names n ON n.user_id = u.id'
            . ' JOIN memberships m ON m.tenant_id = w.tenant_id AND m.user_id = u.id'
            . ' WHERE w.tenant_id = ? AND w.owner_user_id = ?'
            . ' AND w.state = ? AND t.status = ? AND u.status = ?'
            . ' AND u.deleted_at IS NULL AND n.state = ? AND m.role IN (?, ?)',
            [$tenantId, $userId, 'active', 'active', 'active', 'active',
                Roles::ADMIN, Roles::USER]
        );
        if ($row !== null) {
            try {
                if (self::canonical((string) $row['username']) === $row['canonical']) {
                    return;
                }
            } catch (ApiException $error) {
                // A malformed legacy name is never an identity fallback.
            }
        }
        throw new ApiException(403, Codes::PERMISSION_DENIED,
            'the private account is not available');
    }

    /** Serialize credential issuance with password recovery on the immutable owner. */
    public static function lockCredential(Database $db, string $tenantId, string $userId,
                                          int $tokenVersion): void
    {
        $row = $db->queryOne('SELECT token_version, status, deleted_at FROM users'
            . ' WHERE id = ? AND tenant_id = ? FOR UPDATE', [$userId, $tenantId]);
        if ($row === null || $row['deleted_at'] !== null || $row['status'] !== 'active'
            || (int) $row['token_version'] !== $tokenVersion) {
            throw new ApiException(401, Codes::AUTH_REVOKED,
                'the account credential changed before issuance');
        }
        self::requireActive($db, $tenantId, $userId);
    }

    public static function requireDevice(Database $db, string $tenantId,
                                         array $device): string
    {
        $owner = (string) ($device['owner_user_id'] ?? '');
        if ($owner === '' || $owner !== (string) ($device['enrolled_by'] ?? '')) {
            throw new ApiException(403, Codes::PERMISSION_DENIED,
                'the device does not belong to this private account');
        }
        self::requireActive($db, $tenantId, $owner);
        return $owner;
    }

    /** Create an empty private account; never attach a login to an existing tenant. */
    public static function createAccount(Database $db, string $username, string $hash,
                                         string $displayName, string $role,
                                         ?string $slug = null): array
    {
        $name = self::canonical($username);
        if (!Roles::isKnown($role)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'unknown account role');
        }
        $tenantId = Ids::v4();
        $userId = Ids::v4();
        $slug = $slug ?? ('account-' . $tenantId);
        if (preg_match('/\A[a-z0-9][a-z0-9._-]{2,63}\z/D', $slug) !== 1) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'invalid private account slug');
        }
        $now = gmdate('Y-m-d H:i:s');
        return $db->transaction(function (Database $tx) use (
            $name, $hash, $displayName, $role, $slug, $tenantId, $userId, $now
        ): array {
            $tx->insert('tenants', [
                'id' => $tenantId, 'slug' => $slug, 'name' => 'Private account',
                'status' => 'active', 'created_at' => $now, 'updated_at' => $now,
            ]);
            $tx->insert('users', [
                'id' => $userId, 'tenant_id' => $tenantId, 'username' => $name,
                'password_hash' => $hash, 'display_name' => substr($displayName, 0, 120),
                'must_change_password' => 0, 'status' => 'active', 'token_version' => 1,
                'row_version' => 1, 'created_at' => $now, 'updated_at' => $now,
            ]);
            $tx->insert('memberships', [
                'id' => Ids::v4(), 'tenant_id' => $tenantId, 'user_id' => $userId,
                'role' => $role, 'created_at' => $now, 'updated_at' => $now,
            ]);
            $tx->execute('INSERT INTO login_names'
                . ' (canonical, user_id, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
                [$name, $userId, 'active', $now, $now]);
            $tx->execute('INSERT INTO account_workspaces'
                . ' (tenant_id, owner_user_id, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
                [$tenantId, $userId, 'active', $now, $now]);
            return ['user_id' => $userId, 'tenant_id' => $tenantId,
                'username' => $name, 'slug' => $slug, 'role' => $role];
        });
    }

    /** Password recovery never renames, promotes, activates or reassigns an account. */
    public static function rotatePassword(Database $db, string $username, string $hash,
                                          ?string $expectedSlug = null,
                                          ?string $expectedRole = null): array
    {
        $name = self::canonical($username);
        return $db->transaction(function (Database $tx) use (
            $name, $hash, $expectedSlug, $expectedRole
        ): array {
            $row = $tx->queryOne(
                'SELECT u.id, u.tenant_id, u.username, t.slug, m.role'
                . ' FROM login_names n JOIN users u ON u.id = n.user_id'
                . ' JOIN account_workspaces w ON w.owner_user_id = u.id AND w.tenant_id = u.tenant_id'
                . ' JOIN tenants t ON t.id = w.tenant_id'
                . ' JOIN memberships m ON m.tenant_id = w.tenant_id AND m.user_id = u.id'
                . ' WHERE n.canonical = ? AND n.state IN (?, ?) AND u.deleted_at IS NULL FOR UPDATE',
                [$name, 'active', 'reserved']
            );
            if ($row === null || self::canonical((string) $row['username']) !== $name
                || !Roles::isKnown((string) $row['role'])
                || ($expectedSlug !== null && $expectedSlug !== $row['slug'])
                || ($expectedRole !== null && $expectedRole !== $row['role'])) {
                throw new ApiException(403, Codes::PERMISSION_DENIED,
                    'the exact private account and requested identity constraints must match');
            }
            $now = gmdate('Y-m-d H:i:s');
            $tx->execute('UPDATE users SET password_hash = ?, must_change_password = 0,'
                . ' token_version = token_version + 1, row_version = row_version + 1, updated_at = ?'
                . ' WHERE id = ? AND tenant_id = ?', [$hash, $now, $row['id'], $row['tenant_id']]);
            $tx->execute('UPDATE sync_devices SET status = ?, token_version = token_version + 1,'
                . ' updated_at = ? WHERE tenant_id = ? AND (owner_user_id = ? OR enrolled_by = ?)',
                ['revoked', $now, $row['tenant_id'], $row['id'], $row['id']]);
            $tx->execute('UPDATE auth_sessions SET revoked_at = ?, revoked_reason = ?'
                . ' WHERE tenant_id = ? AND revoked_at IS NULL',
                [$now, 'password_rotated', $row['tenant_id']]);
            return ['user_id' => $row['id'], 'tenant_id' => $row['tenant_id'],
                'username' => $name, 'slug' => $row['slug'], 'role' => $row['role']];
        });
    }

    public static function requireAgreement(Principal $device, Principal $user): void
    {
        if (!$device->isDevice() || !$user->isUser()
            || $device->tenantId !== $user->tenantId
            || $device->userId !== $user->userId) {
            throw new ApiException(403, Codes::PERMISSION_DENIED,
                'device and acting user must belong to the same private account');
        }
    }
}
