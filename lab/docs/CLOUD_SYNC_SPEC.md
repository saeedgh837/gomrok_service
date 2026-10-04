# Cloud Sync Specification (IRICA ↔ cPanel host)

Status: **settled contract**. Approved by Saeed (2026-10-05, all 7 grilling
recommendations) and validated by two advisor consultations.
Every workstream builds against THIS file. If an implementation needs something
this file does not state, it is a decision the implementation may NOT make on
its own: report it as `decision needed` (report item 7).

Repo: `saeedgh837/gomrok_service`, branch `feat/cloud-sync`.
Baseline: `main` @ `5d07e14`, suite `Ran 1495 tests … OK`.

---

## 1. Goal and non-goals

**Goal:** users and CRM cases live in a MySQL database on Saeed's cPanel host.
Any machine, after logging in with the same username, sees the full synced
dataset and can edit cases with those edits persisting and propagating.

**Non-goals (explicit, out of scope):**
- `gomrok.py` is never edited (standing rule).
- Robots (`gomrok2.py`, `estelam_*.py`, `exitban.py`, `profile_inquiry.py`)
  are never edited and never learn about the network; they keep reading the
  local JSON stores exactly as today.
- The panel is never exposed to the internet; it stays on `127.0.0.1:8750`.
- The client never speaks MySQL. No `pymysql`/driver in `requirements.txt`.
- No redesign of the existing UI; additions follow the current pattern.
- `profiles.json`, `estelam_store.json`, `inputs.json`, `logs*`, `*.log`,
  `telegram_config.json`, `upstream_proxy.json`, `relay_config.json`,
  `system_debug.json`, screenshots and `tx_artifacts/` stay **local only**.

## 2. Topology

```
[ panel.py 127.0.0.1:8750 ] ──HTTPS──> [ api.<domain> (PHP 8 + .htaccess) ]
        │                                    │  PDO, 127.0.0.1 only
        └── local JSON stores (unchanged)    └── MySQL/MariaDB (cPanel)
```

- TLS required: subdomain with a Let's Encrypt certificate. HTTP is redirected
  or refused. The API refuses requests whose `X-Forwarded-Proto` is not `https`
  **and** whose direct connection is not loopback (the health probe).
- MySQL accepts connections from `127.0.0.1` only. The PHP DB user has
  `SELECT, INSERT, UPDATE, DELETE` on its own schema and `CREATE` only for the
  migration runner (run manually, not from the web entry point).
- Server time is authoritative for every timestamp it stores.

## 3. Scope of the sync

**Synced (6 collections + 2 auxiliaries):**

| JSON (today) | SQL table | Notes |
|---|---|---|
| `customers[]` | `customers` | `transactions[]` moves to its own table |
| `customers[].transactions[]` | `customer_transactions` | ordered by `seq` inside the customer |
| `owners[]` | `owners` | 1:1 with `crm_devices` when attached |
| `identities[]` | `identities` | inquiry pool |
| `devices[]` | `crm_devices` | **not** `devices` — that name is taken by machines |
| `periods[]` | `periods` | |
| `settings{}` | `settings` | **allowlist only**, see §4 |
| (new) | `users` | login identities, see §7 |
| (new) | `sync_devices` | machines; **never** conflated with `crm_devices` |

**Never synced:** anything in the non-goals list above, plus `settings` keys
that are device-local or secret.

**`settings` allowlist (synced):** `business_name`, `business_phone`,
`watch_interval_hours`, `scheduler_enabled`, `auto_advance_default`,
`telegram_report_full_data`, `registry_fee_amount`, `registration_chat_id`,
`report_chat_id`, `sound_enabled`, `current_period_id`, `links[]`.

**Never in `settings` sync:** anything holding a token, a path, a host, a
credential, or a device name. An unknown key must be **dropped on write** with a
warning, never forwarded.

## 4. Identifiers, time, versions

- **Existing ids** (`uuid4().hex[:8]`, 32 bits) are NOT globally unique enough
  for several machines. New server-side ids are full UUID v4.
- **Migration mapping is deterministic**, so a retry produces the same id:
  `uuid5(NAMESPACE_URL, "<source_installation_id>/<entity>/<legacy_id>")`.
  `source_installation_id` is a UUID minted once on first bootstrap and stored
  in `cloud_config.json`.
- Every synced table carries: `id CHAR(36)`, `tenant_id`, `row_version INT NOT
  NULL DEFAULT 1`, `created_at`, `updated_at` (UTC ISO-8601 with offset),
  `deleted_at NULL`, `deleted_by NULL` (tombstone instead of hard delete).
- **Conflict winner is the server commit order**, never a client clock. Client
  `updated_at` is preserved for display only.
- The global cursor is `change_log.seq BIGINT AUTO_INCREMENT`.

## 5. Auth protocol

Two credential families, never mixed:

| | Device (sync) | User (login + admin ops) |
|---|---|---|
| access token | JWT, `aud=sync`, **15 min** | JWT, `aud=user`, **15 min** |
| refresh | opaque 256-bit, **90 days idle / 1 year absolute** | opaque, **8 h idle / 24 h absolute** |
| stored | hashed server-side; client keeps it DPAPI-protected | RAM-only on the client |
| revocation | `sync_devices.status='revoked'`, `token_version` bump | session row delete |

- Refresh tokens **rotate on every use**; reuse of a previously used token
  invalidates the whole token family (one-time-use family).
- **Enrolment:** `POST /v1/sync-devices/enroll` with a live *user* access token
  having the `device:enroll` permission (admins by default). Returns
  `sync_device_id` + device refresh token. The password is sent only to
  `/v1/auth/login`, never stored, never re-sent.
- **No static bootstrap/shared secret in the client config.**
- Client storage:
  - `cloud_config.json` in the data dir: `api_base`, `tenant_id`,
    `sync_device_id`, `source_installation_id` — non-secret.
  - `%LOCALAPPDATA%\irica\auth.dat`: device refresh token, **DPAPI
    `CurrentUser`**, ACL limited to the current user SID + `SYSTEM`, no
    inheritance.
  - user access/refresh tokens and the panel `irica_session`: **RAM only**.
- **Nothing in `Authorization`, `Cookie`, token, password, request body,
  outbox payload, or PII may reach a log.** Logs carry `request_id`, route,
  status, duration, internal UUIDs only.
- **Offline lease:** a machine that has not completed a successful auth in
  **7 days** becomes read-only: local edits are refused with a clear Persian
  message; reads still work.

## 6. HTTP API (the host package contract)

Base: `https://api.<domain>/v1`. All bodies `application/json`, UTF-8.
Success: `{"ok":true, ...payload}`.
Failure: `{"ok":false,"error":{"code":"<STABLE_CODE>","message":"<server text>","request_id":"<uuid>"}}`.
The **client** maps `code` to Persian; the UI never shows raw server text.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health/live` | none | liveness only, returns `{ok, time}` and nothing else |
| POST | `/auth/login` | none | `{username,password}` → user tokens + `must_change_password` |
| POST | `/auth/refresh` | none (body token) | `{refresh_token,kind}` → rotated pair |
| POST | `/auth/logout` | user access | invalidates that token family |
| POST | `/sync-devices/enroll` | user access (`device:enroll`) | → `sync_device_id`, device refresh |
| GET | `/sync/bootstrap` | device access | full snapshot + `cursor` + `dataset_epoch` |
| GET | `/sync/changes?cursor=N&limit=500` | device access | incremental pull |
| POST | `/sync/mutations` | device access (+user context if acting for a user) | batched push |
| POST | `/bootstrap/import` | admin user access **and** enrolled device | one-shot import; see §8 |

### 6.1 `POST /sync/mutations`

```json
{"operations":[
  {"operation_id":"<uuid4>","kind":"upsert","entity":"crm_devices",
   "id":"<uuid>","base_version":4,"record":{...},"actor_user_id":"<uuid|null>"}
]}
```
Response:
```json
{"ok":true,"applied":["<operation_id>"],"conflicts":[
  {"operation_id":"<uuid>","entity":"crm_devices","id":"<uuid>",
   "server_version":6,"server_record":{...}}],"cursor":12345}
```

Rules:
- `operation_id` is idempotent: the same id returns its first result, never
  applies twice (table `idempotency_keys`, retained ≥ 30 days).
- Apply with compare-and-swap on `row_version = base_version`. On mismatch →
  `conflicts[]` with the server copy; **no implicit overwrite, no 4xx abort of
  the batch** — the untouched operations still apply.
- The whole batch runs in one transaction; `change_log` rows are appended with
  the same cursor.
- Deletes are tombstones (`deleted_at`), propagated as `record: null`.

### 6.2 `GET /sync/changes`

```json
{"ok":true,"changes":[
  {"entity":"customers","id":"<uuid>","seq":88,"row_version":3,
   "record":{...}},
  {"entity":"owners","id":"<uuid>","seq":89,"row_version":1,"record":null}
],"cursor":90,"has_more":false}
```
- Never filter by `updated_at > last_sync`: equal timestamps and clock skew
  silently drop rows. Filter by `seq > cursor` only.
- `bootstrap` must return its snapshot and its `cursor` from **one**
  transaction, so nothing committed between them can be missed.

## 7. Roles and authorization

One shared tenant (Saeed's approved answer to grilling Q2). Every request is
authorized per tenant on every call — an id from another tenant must 404, never
403-with-hint (no IDOR oracle).

| Capability | admin | user |
|---|---|---|
| read all synced data | ✓ | ✓ |
| create/edit cases, transactions, owners, identities, stages | ✓ | ✓ |
| open/close periods, delete records | ✓ | ✗ |
| `settings` read | ✓ | ✓ |
| `settings` write | ✓ | ✗ |
| create/modify users, roles, devices; enrol devices; run import | ✓ | ✗ |

No other roles in v1. There is no "owner of one row" concept.

## 8. Bootstrap / import (one-shot)

1. Installer runs `bin/create-admin.php` — creates the first admin with a
   **fresh password chosen at install time**; no seeded user, no seeded hash,
   no default password anywhere in SQL or source.
2. Admin logs in on the source machine → enrol that machine → runs import
   (panel-side routine, one-shot, only after an explicit confirmation).
3. Import: takes a local backup, generates `import_id` +
   `source_installation_id`, sends the whole validated document in one request.
4. Server: `SELECT ... FOR UPDATE` on `tenant_state`:
   - same `import_id` + same payload hash → replay the same result;
   - same `import_id` + different hash → `409`;
   - `bootstrap_status='done'` or any business row present → `409
     DATASET_NOT_EMPTY`;
   - concurrent imports: only the first transaction wins.
5. All rows are inserted in one transaction with `row_version = 1`, ids mapped
   by §4, and `dataset_epoch` incremented. Times converted to UTC explicitly
   (for correctness, not for conflict resolution).
6. After that, **the host is the source of truth**; every other machine does a
   full pull before its first push. Local data of an unknown or foreign epoch is
   quarantined and never auto-pushed.

`panel_users.json` is **never** imported (its hashes are burned — see §10).
Only usernames/display names may be carried over as *proposed* users requiring
a first-time password set.

## 9. Client behaviour (outbox, retries, conflicts)

- A local edit is applied to the local JSON store first (unchanged path), then
  appended to `cloud_sync/outbox.jsonl` **in the same critical section** as the
  store write, before the caller sees success.
- Push is a background worker: batch, exponential backoff with jitter, cap 5 min,
  honour `Retry-After`, bounded attempts (no infinite tight loop).
- Retry **only** on timeout, connection error, `429`, `500–599`. `401` → one
  refresh attempt, then stop and surface. `403`, `400`, `409` are never retried
  blindly.
- `409 VERSION_CONFLICT` → the server record is fetched, shown to the operator
  as a conflict (§11), and **is not applied until they choose**. A pull must not
  overwrite a row that is locally dirty.
- Outbox survives restart; it is bounded (cap with an explicit "queue full"
  state, never silent truncation).
- Sync state is observable: last success time, pending count, conflict count,
  last error code.

## 10. Security requirements (all mandatory, all in-mission)

1. **Plaintext default passwords are removed from `panel_auth.py` source** and
   the three existing hashes are retired; on cPanel the users are created by
   `create-admin.php` with install-time passwords and `must_change_password`.
2. **CSRF + `Origin`/`Host` checks** on every state-changing request to the
   local panel (in addition to the `SameSite=Lax` cookie).
3. **The two unlocked read-modify-write sites in `panel.py`
   (`10076-10081`, `2016→2113`) are closed** under the same lock every other
   mutator uses.
4. Passwords hashed **Argon2id** on the host (fallback `PASSWORD_DEFAULT`);
   PBKDF2 params never migrate.
5. Rate limiting: DB-backed `login_attempts`, keyed on account + IP, delay +
   temporary lock; no permanent lockout (DoS vector).
6. PDO prepared statements everywhere; identifier allowlisting; body size cap;
   strict `Content-Type` check.
7. CORS closed (no `Access-Control-Allow-Origin`); the desktop client needs none.
8. Config, keys and migrations live **outside** `public_html`; `.htaccess`
   denies `.`-files and any `config/`, `src/`, `migrations/`, `bin/`.
9. No passwords, tokens, cookies, national ids, phone numbers or names in any
   server log or audit row. `audit_events` stores metadata or encrypted
   payload, never a plaintext diff of PII.
10. Encrypted, tested backups; forward-only migrations plus a restore procedure
    (no destructive `down.sql`).

## 11. UI (existing style, `ui-ux-pro-max`)

A compact status surface in the panel (settings area), in the current visual
system and Persian conventions (ZWNJ, Persian digits, «گیومه», no em dashes):

- connection state: connected / offline / read-only-after-7-days
- last successful sync time, pending changes, conflicts
- a conflict view: local vs server values, choose one, never auto-resolve
- login: username + password against the host; on success the panel shows the
  acting user exactly as today (`__ACTOR__`)
- every error message mapped from a stable `error.code`, Persian, no raw server
  text, no PII

New routes extend the existing flat `elif` chain in `_do_get_body` /
`_do_post_body`; responses use the existing `{"ok":false,"errors":[...]}` panel
envelope (the client maps host codes into it).

## 12. Test strategy

- Baseline first: `python lab/scripts/run_panel_tests.py` → `Ran 1495 … OK`.
- Everything offline. No test may reach the network, the live data dir
  (`%LOCALAPPDATA%\irica\`), or the real host. Use a loopback fake API —
  the existing pattern is `lab/tests/_telegram_fake.py`.
- `lab/tests/_cloud_api_fake.py` (owned by C-client) is the executable copy of
  §6. Every sync test honours `IRICA_TEST_API_BASE`: unset → the loopback fake,
  set → a real base URL. Workstream G reuses it to point the same suite at the
  PHP API when it exists, so client and host cannot drift unnoticed.
- Cover: idempotent replay, CAS conflict both ways, batch partial-apply,
  tombstone propagation, cursor resume, `has_more`, outbox durability across
  restart, backoff bounds, 401-refresh-then-stop, read-only lease, missing/late/
  malformed payloads, settings allowlist dropping a secret key, CSRF rejection,
  rate-limit lockout, import replay and `DATASET_NOT_EMPTY`, role matrix.
- Mock/sample data only. No real customer PII, no real credentials.

## 13. File ownership (conflict control — hard rule)

Two agents must never write the same file.

| Owner | Files |
|---|---|
| **F** (schema) | `host/migrations/*.sql`, `host/src/Database.php`, `host/src/Schema.php`, `lab/tests/test_host_schema.py` |
| **B** (mapping) | `cloud_sync/mapper.py`, `cloud_sync/ids.py`, `lab/tests/test_cloud_mapper.py` |
| **A** (state machine) | `cloud_sync/state.py`, `cloud_sync/lease.py`, `lab/tests/test_cloud_state.py` |
| **S** (security) | `panel_auth.py`, `panel.py` (gate/CSRF/lock fixes only), `lab/tests/test_auth_hardening.py` |
| **C-host** (API) | `host/src/*.php` (except the three F owns), `host/public/index.php`, `host/public/.htaccess`, `host/bin/*`, `host/config/*`, `host/INSTALL.md`, `host/SECURITY.md`, `lab/tests/test_host_api.py` |
| **C-client** (transport) | `cloud_sync/client.py`, `cloud_sync/outbox.py`, `cloud_sync/auth.py`, `cloud_sync/errors.py`, `cloud_sync/config.py`, `lab/tests/_cloud_api_fake.py`, `lab/tests/test_cloud_transport.py` |
| **D** (UI) | `panel.py` (routes + page strings for the cloud section), `lab/tests/test_cloud_ui.py` |
| **E** (conflict UI) | `panel.py` **after D**, `lab/tests/test_cloud_conflicts.py` |
| **G** (suite) | `lab/scripts/run_panel_tests.py`, `lab/tests/test_cloud_integration.py` |
| **I** (Persian) | review only; edits go back through the owning agent |

`panel.py` is single-threaded across workstreams: **S → D → E**, in that order.
`host/` is single-threaded: **F → C-host**.

### 13.1 Local verification environment (already provisioned)

The host package is executed here, not only reviewed. Ready on this machine:

- **PHP 8.1.34** (winget): `C:\Users\Saeed\AppData\Local\Microsoft\WinGet\Packages\PHP.PHP.8.1_Microsoft.Winget.Source_8wekyb3d8bbwe\php.exe`
  with a local `php.ini` enabling `pdo_mysql`, `mbstring`, `openssl`, `sodium`,
  `fileinfo`. `password_algos()` includes `argon2id`.
- **MariaDB 11.4.4** (portable, no service): running on `127.0.0.1:3307`,
  database `irica_test` (`utf8mb4`), server files under `.tools/mysql/`
  (gitignored). Started/inspected with:
  `…\bin\mariadb.exe -h127.0.0.1 -P3307 -uroot -p<root> --protocol=TCP --ssl=FALSE`
  — **`--ssl=FALSE` is mandatory** with this client; `mysql.exe` from the same
  package fails to authenticate.
- Credentials live in `%TEMP%\irica_test_db.json` (`root_password`,
  `api_password`). **They are never committed, never logged, never printed.**
- Two accounts exist: `root` (migrations, admin) and `irica_api`@`127.0.0.1`
  with **only** `SELECT, INSERT, UPDATE, DELETE` on `irica_test.*` — `CREATE`
  is denied. Production must be created the same way (spec §2), so the grants
  themselves are under test.

## 14. Batches

1. **Batch 1 (parallel):** F, B, A, S — disjoint files.
2. **Batch 2 (parallel):** C-host (needs F), C-client (needs A+B), D (needs S).
3. **Batch 3:** E (after D), G (integration + full suite), I (Persian review),
   then 2–3 independent reviewers (H) over the whole diff.

Report contract (every workstream, exactly these 7): files reviewed · changes ·
bugs found · tests added · tests run + results · remaining risks · decisions
needed or «هیچ».

## 15. Manager rulings (batch 1 — binding, not questions)

Raised by workstream B; ruled on while batch 1 was running. They refine §3/§4 and
do not change the endpoint contract.

1. **`legacy_id` is a real column.** Every business table carries both
   `id CHAR(36)` (UUID, §4) and `legacy_id VARCHAR(64) NULL` (the original 8-hex
   id from `crm_store.json`). The client's mapper keeps the legacy id inside
   `record.id` and emits the UUID only through `envelope()`, so the two must
   never share one column. Nullable: `settings` and `customer_transactions` have
   no legacy id of their own.
2. **Legacy-id formulas for the two entities that have none** — the import and
   the client must compute them identically:
   - `customer_transactions` → `"<customer_legacy_id>/<seq>"`
   - `settings` → `"settings/<key>"`
3. **Boolean columns** are `TINYINT(1)`; the Python side accepts SQL `0/1`.
4. **Two fields sync as opaque strings and are never foreign keys**, because
   their referents stay local (§1): `crm_devices.profile_id` (points into
   `profiles.json`) and `customer_transactions.image` (a bare filename under
   `tx-artifacts/`). No FK, no content validation, no dereferencing during sync;
   a reader on another machine may find them dangling and must treat that as
   normal, not an error.
5. **Source timezone** for store timestamps is `Asia/Tehran`, injectable
   (`tz=`), never read from the OS default. Wrong zone silently shifts history.
6. **`cloud_sync/__init__.py` belongs to workstream B** and stays import-free
   (docstring only) — other `cloud_sync` modules must not depend on anything
   being imported there.

Raised by workstream A (state machine), ruled while batch 1 was running:

7. **A device-token refresh counts as a successful authentication** for the
   7-day lease: the machine proved possession of a valid credential, so it calls
   `lease.grant()` and fires `AUTH_OK`.
8. **Error codes are an allowlist-shaped grammar:** `^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$`,
   ≤ 40 characters (`NET_TIMEOUT`, `HTTP_500`, `VERSION_CONFLICT`,
   `QUEUE_FULL`). This is what makes PII structurally impossible in a code —
   the host API and the client must both emit codes that pass it, and
   `ECONNREFUSED`-style tokens are not valid.
9. **Persistence files** live in the data dir as `cloud_sync_state.json` and
   `cloud_sync_lease.json`; `cloud_config.json` is the transport's (C-client's).
   Restoring a persisted `SYNCING` downgrades to idle with
   `restore_reason=INTERRUPTED_SYNC` — a state with no worker must not refuse to
   start again.
10. **`QUEUE_FULL` is a stable error code, not a ninth state** — a ninth state
    would need masking rules in every fault state for no added safety.
11. **An expired lease stops network sync entirely** (pulls included): a device
    that cannot authenticate cannot pull either. Local reads keep working.
12. **Every route that drives the state machine must catch
    `IllegalTransitionError` / `LeaseExpiredError` explicitly.** `panel.py`
    already has 39 `except Exception` sites (13 of them `pass`), and a machine
    event swallowed there is precisely the silent drift this module exists to
    prevent. Whoever fires an event fires **exactly one** outcome or fault per
    batch attempt.
