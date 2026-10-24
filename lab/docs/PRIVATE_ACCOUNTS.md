# Private CRM accounts — implementation design

Status: implementation contract for `feat/private-accounts`.
Extends `CLOUD_SYNC_SPEC.md` §7 (roles) and `SERVER_CRM_V2_PLAN.md` §1 (product
outcome). Where this file and the older shared-tenant text disagree, this file
wins for the account boundary.

## 1. Product outcome

Each CRM account owns exactly one private dataset. A login can read and write
only its own tenant. Several devices may sign in to the same account and
synchronize that one dataset. The operator provisions accounts — username,
password and a legacy `crm_store.json` — through one protected web panel, and
the same username/password then signs the customer in on the desktop app.

Two things stay true from the earlier contract:

* MySQL/MariaDB on the host remains the source of truth; desktops never connect
  to the database directly.
* `crm_store.json` on a desktop is a cache plus the robot compatibility surface,
  not a cross-machine authority.

## 2. What changed and why

The released host models one shared tenant (`config.php: tenant_slug`), so two
logins see one dataset. That is now a defect, not a product decision. The
replacement keeps the existing tenant-scoped query shape — every business
table, `auth_sessions`, `sync_devices`, `legacy_import_*` and `change_log`
already carry `tenant_id`, and every read path already filters by the
authenticated principal's tenant. Isolation is therefore a matter of making
tenant resolution authoritative and explicit, not of rewriting queries.

Rejected alternative: row-level `owner_user_id` on every business table. It
would require changing every query, every foreign key, every dedupe rule and
every change-log reader, and it makes an accidental cross-account read a
one-line mistake. A per-account tenant keeps the existing graph.

## 3. Data model (migration 005, additive)

### 3.1 `account_workspaces` — one row per private account

| column | notes |
| --- | --- |
| `tenant_id` CHAR(36) PK | FK → `tenants.id`; the dataset boundary |
| `owner_user_id` CHAR(36) NOT NULL UNIQUE | FK → `users.id`; immutable while the row exists |
| `state` VARCHAR(16) | `pending` \| `active` \| `suspended` |
| `created_at`, `updated_at` DATETIME UTC | |

A personal owner is always `users.tenant_id = account_workspaces.tenant_id`, so
the existing per-tenant queries remain correct. The immutability rule is
enforced in code (a workspace owner is never updated in place) and asserted by
a trigger-free test; a database `CHECK` cannot express "belongs to that
tenant" portably enough on MySQL 5.7.

A tenant with **no** `account_workspaces` row is a legacy shared tenant and is
unusable by customer logins. That is the fail-closed default: absence denies.

### 3.2 `login_names` — global canonical registry

| column | notes |
| --- | --- |
| `canonical` VARCHAR(64) PK | lowercase ASCII, `[a-z0-9][a-z0-9._-]{2,63}` |
| `user_id` CHAR(36) NULL UNIQUE | NULL = reserved/blocked |
| `state` VARCHAR(16) | `reserved` \| `active` \| `blocked` |
| `created_at`, `updated_at` | |

`users.username` stays unique only per tenant; the registry is what makes it
globally unique. Login resolves `canonical(username)` here and then to
`user_id`; it never scans `users` by name and never trusts `tenant_slug`.

Canonicalization is `strtolower(trim(username))` plus a whitelist check. Two
distinct legacy names that collide after lowercasing are a collision, not a
merge: the second one is refused and reported, never silently matched.

### 3.3 `platform_operators` — the account-management authority

| column | notes |
| --- | --- |
| `id` CHAR(36) PK | |
| `login_name` VARCHAR(64) NOT NULL UNIQUE | canonical, separate namespace from `login_names` |
| `password_hash` VARCHAR(255) NOT NULL | Argon2id, own credential — no tenant-admin reset path |
| `user_id` CHAR(36) NULL | audit attribution only; grants nothing |
| `enabled` TINYINT(1) | |
| `token_version` INT | revocation |
| `created_at`, `updated_at`, `last_login_at` | |

Operator sessions live in `operator_sessions` (`id`, `token_hash`, `family_id`,
`issued_at`, `absolute_expires_at`, `revoked_at`). Operator access tokens carry
`kind=operator` and `aud=operator`; `Auth::principalFor()` accepts only
`user`/`device`, so an operator token can never authenticate a CRM route, and a
customer token can never reach an operator route. There is no impersonation and
no tenant-admin path to an operator credential.

### 3.4 `account_provisions` — durable onboarding jobs

| column | notes |
| --- | --- |
| `id` CHAR(36) PK | also the idempotency key |
| `operator_id` CHAR(36) | who ran it |
| `tenant_id` CHAR(36), `owner_user_id` CHAR(36) | resolved server-side, never from the request body |
| `source_sha256` CHAR(64) | digest of the exact uploaded bytes |
| `source_version` VARCHAR(32) | |
| `original_filename` VARCHAR(255) | sanitized basename only |
| `legacy_job_id` CHAR(36) NULL | the `legacy_import_jobs` row this provision owns |
| `state` VARCHAR(24) | `reserved` → `analyzed` → `reviewed` → `applying` → `active`, plus `cancelled`, `failed` |
| `error_code` VARCHAR(40) | stable code only |
| `created_at`, `updated_at`, `applied_at` | |

The raw upload is never stored: analysis stages normalized rows into the
existing `legacy_import_*` tables, keyed to the provision's target tenant, and
only the digest survives in the provision row.

### 3.5 Why these tables are not in `Schema.php`

`Schema.php` is the allowlist for `Database::table()` / `Database::column()` and
describes the **migration 001** table set, which is what the CRM sync path
actually writes through. The tables added by 002–004 (`record_presence`,
`legacy_import_*`, `sync_devices.owner_user_id`) are already outside it: those
services issue their own parameterized SQL against a fixed statement text, with
no identifier built from input. The five tables in 005 follow the same rule, so
`lab/tests/test_host_schema.py` keeps its exact-match contract for the
allowlist surface and `bin/migrate.php` still applies and checksums 005.

## 4. Authorization policy

One class, `Workspace`, is the single place the boundary is decided:

* `resolveLoginName(canonical)` → `user_id` or refuse.
* `requireActive(Database, array $principal)` — called from `Auth::login`,
  `Auth::refresh`, `Auth::principalFor` (both kinds) and `Devices::selfEnroll`.
  It requires: a `account_workspaces` row for the tenant, `state='active'`,
  `owner_user_id` equal to the user (user tokens) or to the device's owner
  (device tokens), an active user, and — for mixed requests — agreement
  between the device owner and the acting user.
* Missing workspace, `pending`, `suspended`, owner mismatch, or an unassigned
  legacy tenant all produce `403 PERMISSION_DENIED` (or `401 AUTH_REQUIRED`
  where the credential itself is unusable). No route opts out.

Financial append/void, presence, import review/apply, device management and
`/admin/backup` are audited as separate paths in the test suite: each must
refuse a foreign tenant and cause no writes.

`/admin/backup` additionally requires a platform operator. A tenant admin —
including a personal account owner — can never trigger a whole-database dump.

## 5. Onboarding flow

1. `POST /operator/auth/login` (operator credentials) → operator token.
2. `POST /operator/provisions` with `username`, `password`, `source_text`,
   `filename`, `source_version` and a client-generated `provision_id`.
   Server-side, in one transaction: reserve the canonical name, create the
   `tenants` row, the `users` row (hashed immediately, `must_change_password=0`),
   the `memberships` row (`admin`), the `account_workspaces` row in `pending`,
   and the `legacy_import_jobs` row. Analysis then stages rows.
3. `GET /operator/provisions/{id}` → counts, issues, review state.
4. `POST /operator/provisions/{id}/decisions` → explicit resolutions.
5. `POST /operator/provisions/{id}/apply` → one transaction that applies the
   reviewed rows, reconciles them, marks the workspace `active`, completes the
   job and the provision. Until it commits, the account cannot sign in.

Repeating a request with the same `provision_id` returns the stored outcome.
The same id with a different payload is `IDEMPOTENCY_MISMATCH`. The password is
hashed at step 2 and is never echoed, logged, or written anywhere else.

## 6. Desktop client

* The main login screen posts the customer's username/password to
  `POST /v1/auth/login`. Success creates the local UI session directly; the
  separate cloud-credentials block is removed from the settings surface.
* Account state lives under a directory derived from the validated API base and
  the server-issued `user_id`/`tenant_id`, never from a typed username. Robot
  paths, `crm_store.json`, outbox, cursor, `auth.dat` and logs all resolve
  inside it.
* Switching accounts is **sign out, quit, relaunch, authenticate**. The process
  that authenticates then owns one immutable account profile; a second login in
  the same process is refused.
* Cloud mode is online-only: no offline password check and no lease extension.
  An unreachable host leaves the cloud dataset closed.
* Existing global state from v1.15.x is moved aside into a quarantine folder on
  first run of the new build, never imported and never uploaded.

## 7. Non-goals

* Replacing an active account's dataset, merging two accounts, or bulk
  management.
* Automatically attributing or repartitioning the pre-existing shared data.
  It is backed up, verified, archived and then deleted by an explicit operator
  action.
* Protecting local cache files from another process running as the same Windows
  user; that needs separate OS accounts.

## 8. Rollout

0. Verified backup + isolated restore check + archive handoff, then delete the
   shared dataset and its accounts.
1. Security floor: schema, registry, fail-closed gating, session invalidation.
2. Operator auth, then `/admin/backup` behind it.
3. Provisioning and reviewed import; activation stays disabled until the client
   ships.
4. Isolated desktop client.
5. Activate one account, verify, then allow more.

Rollback never restores shared access: at worst the affected APIs stay
unavailable.
