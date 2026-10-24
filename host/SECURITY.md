# IRICA cloud-sync host — security notes

Scope: everything in this directory (`host/`), the HTTP surface it exposes,
and the MySQL account it uses. The binding requirements are
`lab/docs/CLOUD_SYNC_SPEC.md` §2 (topology), §5 (auth), §6 (API),
§7 (roles), §8 (import) and §10 (ten mandatory controls).

This document states the threat model first, then maps **every §10 control
to the code that implements it**, then covers the parts that are design
decisions rather than checkboxes: token construction and why, the authorization
model, exactly what may never reach a log, and backup/restore/rotation.

Nothing here is a secret. Credentials live in `config/config.php`, which is
outside the document root, mode `600`, never committed, never printed.

---

## 1. Threat model

### 1.1 What we are protecting

| Asset | Why it matters |
|---|---|
| Customer records (names, national ids, phone numbers, addresses, images) | the entire dataset of the business; leak = irreversible harm |
| `users.password_hash`, `auth_sessions`, `sync_devices` | compromise = impersonation of the operator and of every enrolled machine |
| `config.php`: DB credentials, `jwt_secret` | compromise = full read/write of the dataset and the ability to mint tokens |
| The tenant's `change_log` / dataset continuity | tampering = silent divergence between offices |
| Availability of the API | a lockout that never releases is a self-inflicted outage |

### 1.2 Adversaries considered

1. **Unauthenticated internet caller** hitting `https://api.<domain>`:
   probing, credential stuffing, resource exhaustion, trying to read
   `config.php` or `001_initial.sql` over HTTP.
2. **A stolen or leaked user credential**: password reuse, a phished panel
   login. Must be bounded by rate limiting, short-lived access tokens,
   refresh rotation, and an admin-only ceiling on enrolment and import.
3. **A stolen device refresh token** (a laptop left unlocked, a copied
   token): must be revocable per device and per session family, and must die
   when the device is revoked or its `token_version` is bumped.
4. **A replayed request** (an attacker resubmitting a captured mutation or
   import): must be idempotent, never a second write.
5. **A cross-tenant caller**: this install runs one shared tenant (spec §7),
   but the code does not assume it always will — a row another tenant owns
   must never be readable or writable.
6. **A malicious or curious operator reading our logs**: logs are a common
   leak; they must not contain a password, token, cookie, national id, phone
   number or name, in this package or any log shipper pointed at it.
7. **Prompt-injection / hostile page content**: not a server concern for
   this API — it never renders HTML, never returns a script, and never
   interprets page text as instruction. Client-side consumers must treat
   every response field as data.

### 1.3 Explicitly out of scope

* Compromise of the cPanel account itself, or of the MySQL server as a whole —
  anyone with the host's root credentials owns the data by definition.
* A nation-state with disk access; we assume backups and the filesystem are
  the host owner's responsibility (see §6).
* Client-side storage on the desktop panel (DPAPI-protected, outside this
  package).
* Denial of service by network-level flooding (upstream to Apache).
* Multi-tenant isolation beyond the single `tenant_slug` of spec §7 — the
  *code* is tenant-scoped everywhere, but there is no tenant-administration UI.

---

## 2. Spec §10 control map

Every control, where it lives, and how to verify it.

### 10.1 No published credential; fresh installs seed a random password

* `bin/create-admin.php` — generates 24 URL-safe characters from
  `random_bytes` when `IRICA_ADMIN_PASSWORD` is absent, prints it **once**,
  refuses to run a second time while an admin membership exists, and tells
  the operator to use `--reset-password`. It never prints the hash.
* `bin/create-admin.php --reset-password` bumps `users.token_version` and
  revokes every `auth_sessions` row for that user, so a reset also ends
  every live session.
* There is no default password anywhere in this package, in a fixture, or in
  a test: `lab/tests/test_host_api.py` generates one per run and never
  commits or prints it.
* Pre-existing panel hashes from before this branch are **not migrated**;
  `cloud_sync/` and the host share no credential store.

*Verify:* `grep -R "password" host/bin host/src` — no literal appears except
variable names and the string `'password_hash'`.

### 10.2 CSRF + `Origin`/`Host` checks

The host is a **bearer-token JSON API**, not a cookie session: there is no
ambient authority for a browser to abuse, so classic CSRF does not apply.

What is enforced instead:

* `public/.htaccess` — `Header always unset Set-Cookie`. This API never
  issues a cookie; nothing can be attached to a cross-site request.
* `public/index.php` — `POST` without `Content-Type: application/json` is
  answered `415 UNSUPPORTED_MEDIA_TYPE`; a form-encoded or `text/plain`
  body (the two that a browser can submit cross-site without a preflight)
  can never reach a handler. `Request::jsonBody()` re-checks after the
  empty-body case.
* The `Origin` header is deliberately **not** trusted: a browser can set it,
  and a legitimate desktop client does not send one. Trusting it would break
  the client while protecting nothing.
* The local panel (out of this package) keeps its `SameSite=Lax` cookie and
  `Origin`/`Host` checks; that is `panel.py`'s control, not this one.

### 10.3 Unlocked read-modify-write sites

Not in this package — the two sites are in `panel.py` and are workstream
A's. The host has the equivalent property structurally: every mutation runs
inside one `Database::transaction()` with `SELECT … FOR UPDATE` on the
row it is about to change and a compare-and-swap `WHERE row_version = ?`
(`Sync::casWhere`, `Sync::applyOne`). There is no read-modify-write path in
PHP that is not inside a transaction.

### 10.4 Passwords hashed Argon2id (fallback `PASSWORD_DEFAULT`)

* `Auth::hashPassword()` — uses `PASSWORD_ARGON2ID` when
  `password_algos()` reports it, otherwise `PASSWORD_DEFAULT`.
* `Auth::login()` — `password_verify()` against the stored hash, and for an
  unknown or inactive account it verifies against a **decoy Argon2id hash**
  so the two failure paths cost the same wall-clock time. An attacker
  cannot enumerate users by timing.
* `PBKDF2` parameters never migrate: `create-admin.php` writes a hash from
  the current PHP build's own algorithm, not from a client-supplied value.

*Verify:* `bin/smoke-test.php` prints `argon2id present` as PASS or WARN.

### 10.5 Rate limiting: DB-backed, keyed on account + IP, temporary lock

`RateLimit.php`:

* `check()` counts **consecutive failures since the last success** inside
  `rate_limit.window_seconds`, keyed on `(account, ip)`.
* After `lock_after` failures the login answers `429 RATE_LIMITED` with a
  `Retry-After` header; after `lock_seconds` the window clears.
* Below the lock, delay grows as `base_delay * 2^(n - delay_after)` capped
  at `max_delay_seconds`.
* **There is no permanent lockout.** A permanent ban keyed on an attacker
  chosen string is a denial-of-service weapon against the operator.
* `record()` writes one `login_attempts` row per attempt (append-only,
  `(account, ip, attempted_at, outcome)`) and prunes rows older than
  `4 × window` at most once a minute, so the table cannot grow without bound.

*Test:* `test_rate_limit_locks_then_releases` in
`lab/tests/test_host_api.py` — three failures, a 429 with `Retry-After`,
then a real login succeeding after the window.

### 10.6 Prepared statements, identifier allowlist, body cap, strict `Content-Type`

* `Database.php` — `ATTR_EMULATE_PREPARES => false` (the *server* does the
  type handling), every value bound as a parameter, session
  `SET time_zone = '+00:00'`.
* `Database::table()`, `::column()`, `::columns()` — reject any identifier
  not present in `Schema.php`'s allowlist. Quoting is not the safety
  mechanism; the allowlist is.
* `Http::Request::capture()` — `Content-Length` is checked **before**
  `php://input` is read, and the read is capped again, so a lying client
  cannot force a large allocation. Default cap `limits.max_body_bytes`
  (1 MiB), `limits.max_import_body_bytes` (16 MiB) for `/bootstrap/import`.
* `Http::Request::jsonBody()` — empty body first (`{}` or 400), then an
  exact `application/json` match, then a JSON **object** (a list or a scalar
  is rejected).
* Booleans are converted to `0`/`1` before binding (`Sync::recordToRow`),
  because native prepares bind `false` as `''` and strict SQL mode rejects
  it.

*Tests:* `test_oversized_body_is_refused` (413),
`test_non_json_body_is_refused` (415/400),
`test_mutations_reject_a_malformed_batch`, `test_app_account_cannot_create`.

### 10.7 CORS closed

* No handler in `public/index.php` ever sets `Access-Control-Allow-Origin`.
* `public/.htaccess` — `Header always unset Access-Control-Allow-Origin`
  and `…Allow-Credentials`, so another module cannot add it either.
* `Response::emit()` sets `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `Pragma: no-cache`
  and never a CORS header.

*Test:* `test_no_access_control_allow_origin_anywhere` — asserts the absence
on 200, 401, 404 and on `index.php`'s source.

### 10.8 Config, keys and migrations outside `public_html`

* Layout: only `public/` is the document root; `config/`, `src/`,
  `migrations/`, `bin/` are one level up.
* `public/.htaccess` —
  * `RewriteRule ^(src|config|migrations|bin)(/|$) - [F,L]`
  * `RewriteRule (^|/)\. - [F,L]` (dot-files, editor backups, `.htaccess`)
  * `<FilesMatch "^\.">` and `^\.ht` → `Require all denied`
  * `Options -Indexes -MultiViews`, `ServerSignature Off`
* `host/config/.gitignore` contains `config.php`; only the placeholder
  template is committable.
* `Config::validate()` rejects a `REPLACE_…` placeholder and an all-zero or
  short `jwt_secret`, and every error names a **key**, never a value.
* `bin/migrate.php` refuses a checksum drift and refuses a non-pristine
  database, so a web-driven or half-run migration cannot half-apply.

*Test:* `test_migrate_applies_once_then_skips_and_refuses_drift`,
`test_smoke_test_fails_without_a_configuration`.

### 10.9 No password, token, cookie, national id, phone number or name in any log or audit row

See §4 below — this one gets its own section because it is the easiest to
break silently.

### 10.10 Encrypted, tested backups; forward-only migrations; a restore procedure

See §6 below.

---

## 3. Token design, and why

Two credential families, deliberately separate (spec §5). A device must be
revocable without logging the office out, and a user session must not be
able to enrol new hardware by accident.

### 3.1 Access tokens — compact JWS, HS256

| | User | Device |
|---|---|---|
| `aud` | `user` | `sync` |
| Lifetime | 900 s (15 min) | 900 s (15 min) |
| Claims | `iss=irica-host`, `sub`, `aud`, `kind`, `tid` (tenant), `sid` (session), `tv` (`token_version`), `iat`, `exp` | same |

**Why HS256 (`hash_hmac('sha256')`) and not RS256/ES256.** There is exactly
one issuer and exactly one verifier: this host. An asymmetric key pair buys
nothing — you would still have to protect the private key exactly as you
protect the HMAC secret — while adding an OpenSSL dependency to a package
whose contract is "plain PHP 8.1, no Composer, no framework". `hash_hmac` is
in the always-available core. The algorithm is **pinned**: `Tokens::verify()`
rejects anything whose header `alg` is not `HS256`, so the classic
`alg: none` and RS256/HS256 key-confusion attacks cannot be attempted. There
is no `jku`/`x5u` header to chase, so there is no SSRF surface in token
verification.

**Why the access token is checked against the database on every request**
(`Auth::principalFor()`), rather than trusted purely on signature: a JWT that
is only signature-checked cannot be revoked. Every request re-reads the live
`auth_sessions` row and the subject's row, and rejects a revoked session, an
absolutely expired session, a device whose `status` is not `active`, or a
`token_version` that has moved on. The JWT's job is then only to be a fast,
unsigned-by-the-client carrier of `sid`/`sub`/`kind`; the *authority* stays in
the database. The cost is one indexed lookup per request; the benefit is that
bumping `token_version` or revoking a device stops in-flight tokens
immediately. Both are tested
(`test_bumping_token_version_stops_the_token`,
`test_revoking_a_device_stops_its_in_flight_token`).

### 3.2 Refresh tokens — opaque, hashed, one-time

* 32 bytes from `random_bytes`, transmitted as hex; the database stores only
  `hash('sha256', token)` in `auth_sessions.token_hash` (`CHAR(64) UNIQUE`).
  A database leak therefore yields no usable refresh token.
* **Rotation on every use.** `Auth::refresh()` runs in one transaction with
  `SELECT … FOR UPDATE`, claims the row with an `UPDATE … WHERE used_at IS NULL`
  compare-and-swap, and issues a successor that **inherits the parent's
  `absolute_expires_at`** — rotation extends the idle window, never the
  absolute ceiling.
* **Reuse kills the family.** Presenting an already-claimed token returns
  `401 AUTH_REVOKED` and revokes every session sharing `family_id`
  (detection of a stolen token, not just of a bug). The revocation and its
  `audit_events` row are executed **after** the transaction commits, because
  doing it inside would be rolled back by the very `throw` that follows —
  a mistake this package made and fixed, and
  `test_refresh_rotates_and_reuse_kills_the_family` now pins it.

Lifetimes (spec §5, `config.example.php → refresh`):

| Family | Idle | Absolute |
|---|---|---|
| user | 8 h | 24 h |
| device | 90 d | 1 y |

**Why refresh tokens are opaque rather than JWTs:** they are single-use and
must be revocable individually and by family, so they need server-side state
regardless; making them signed tokens would add a second verification path
and a second place a leak could matter, for no gain.

### 3.3 Failure semantics

| Situation | Response |
|---|---|
| No credential | `401 AUTH_REQUIRED` |
| Expired / malformed access token | `401 AUTH_EXPIRED` (or `AUTH_REQUIRED`) |
| Revoked session, revoked device, bumped `token_version` | `401 AUTH_REVOKED` |
| Valid user token, insufficient role | `403 ROLE_FORBIDDEN` |
| Valid credential, id this tenant does not own | `404 NOT_FOUND` — never `403` |
| Reused refresh token | `401 AUTH_REVOKED` + family revoked |
| Too many failures | `429 RATE_LIMITED` + `Retry-After` |

`404` rather than `403` for a foreign id is deliberate: `403` would confirm
that the id exists in some other tenant. `Sync::preflight()` looks the row up
**without** a tenant filter precisely so the answer can be "not found".

All error `code`s satisfy `^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$` and are ≤ 40
characters (`Http\Codes::PATTERN`, `Codes::MAX_LENGTH`; ruling §15.8), and
`Codes::assertValid()` runs against the full registry at runtime —
`test_every_emitted_code_satisfies_the_grammar` checks the same registry from
outside.

---

## 4. What must never be logged

This is §10.9, and it is enforced structurally rather than by discipline.

### 4.1 Never in a log line, an audit row, an error message or a response

* a password, a password hash, `IRICA_ADMIN_PASSWORD`
* an access token, a refresh token, a `token_hash`
* a cookie or any `Authorization` header value
* a national id, a phone number, an address, a customer or user **name**
* a customer `record` in full, a `server_record`, or a request body
* a database DSN password, a `jwt_secret`

### 4.2 How it is enforced

1. **`Log::ALLOWED_FIELDS` is a closed list.** `Log::line()` drops any key
   not on it: `ts, request_id, method, route, status, duration_ms, code,
   tenant_id, user_id, device_id, action, outcome, entity, operation, count,
   conflicts, cursor, errno, sqlstate, error_class, error_file, error_line,
   level, ip, detail`. You cannot add a field by accident — it is dropped.
2. **Strings are truncated to 200 characters and stripped of control
   characters**, so a value cannot smuggle a newline and forge a second log
   line.
3. **`PDOException::getMessage()` is never logged.** A duplicate-key message
   embeds the value that collided. `Log::describe()` reports only the
   exception class plus SQLSTATE/errno for a PDO error, and class + location
   for anything else. Our own `RuntimeException`s are exempt because the
   messages this package throws name keys, never values.
4. **`detail` is built from codes and column names only**
   (`Sync::describeFailures()` → `INVALID_TIMESTAMP:customers.created_at`),
   never from the offending value.
5. **Routes are logged from the route table, not from the URL.** An
   unmatched path is logged as `<unknown>`, so a user-controlled string can
   never be written to the log by path.
6. **`audit_events.metadata` is a small JSON object of ids and outcomes**
   (`action`, `tenant_id`, `outcome`, `operation`, …). It never stores a
   plaintext diff of PII. `Log::audit()` catches its own failure so a full
   audit table can never take the API down.
7. **`public/index.php` sets `display_errors = 0`** before anything else and
   routes every throwable to a generic envelope; `public/.htaccess` sets
   `display_errors off` as a second layer.
8. **Response messages are operator text only** and are written to contain
   no credential and no PII; the client maps `code` to Persian and never
   shows `message` to an end user.

*Test:* `test_no_pii_or_token_reaches_an_observable_log` performs a failed
login with a canary password, a mutation carrying a canary name / national id
/ phone number, a bootstrap, a refresh and an enrolment — then reads the
application log file and the web-server log file and asserts that none of the
canaries, none of the four live credentials, and none of the DB passwords
appear in either file, and that `audit_events.metadata` carries none of them.

### 4.3 What *is* logged, and why it is safe

One structured line per request: timestamp, `request_id`, method, route
label, status, duration, error `code`, and the ids that join it to a row
(`tenant_id`, `user_id`, `device_id`) — plus, for a specific action, an
entity name, an operation id, counts and a cursor. That is enough to answer
"which request failed, where, and for whom" from `request_id` alone.

The log contains no PII by construction, but it is still operational data:
keep retention short (see §6).

---

## 5. Authorization model

* Roles live on `memberships` (`admin` | `user`) for the shared tenant of
  spec §7 — there is no third role and no superuser in the API.
* `Roles::canWrite()` — `delete` is admin-only; `settings` and `periods` are
  admin-only; ordinary `upsert` is allowed to both. `Roles::canEnroll()` and
  `Roles::canImport()` are admin-only. Reads are open to both.
* **Effective role** (`Auth::effectiveRole()`): a `/sync/*` request carries
  a *device* credential, optionally with a *user* credential in
  `X-Acting-User`. The acting user's membership role is used when present;
  otherwise the role of the user who enrolled the device. A device token
  alone can never be promoted to admin by a request header, and
  `X-Acting-User` must itself be a valid, live user token.
* `POST /bootstrap/import` requires **both**: an enrolled device and an
  admin user in `X-Acting-User` (spec §6/§8). Missing either is 401/403
  before any dataset check runs.
* A role denial aborts the batch with `403` during `Sync::preflight()`,
  **before** the transaction opens, so nothing is applied. (The "no 4xx
  abort" rule of §6.1 is about CAS *conflicts*, which are data — those
  never abort.)
* Validation failures are also pre-transaction: an operation whose record
  fails `Schema::validateRow` answers `400 VALIDATION_FAILED` for the
  request rather than half-applying.

*Tests:* `test_role_matrix_gates_settings_and_periods`,
`test_import_needs_an_admin_user_and_a_device`,
`test_enrolment_needs_the_device_enroll_permission`.

---

## 6. Backups, restore, rotation

### 6.1 Backups

* Nightly logical dump of the schema, **encrypted at rest** before it leaves
  the host, with retention you have actually verified you can restore from.
* `config/config.php` is included in the encrypted backup and in no other.
* The application log is *not* a backup asset — it contains no PII and no
  secret, so it needs no special handling beyond ordinary retention.
* Take a backup before every upgrade (see `INSTALL.md` §11).

### 6.2 Restore (the only rollback path)

Migrations are **forward-only**: there is deliberately no `down.sql`
(spec §10.10), so the recovery path is restore-then-forward:

1. Load the dump into a clean database.
2. `php bin/migrate.php --status` — shows the recorded checksum.
3. If the package is newer than the dump, `php bin/migrate.php` applies only
   what is missing. A checksum mismatch stops the run (exit 2) instead of
   guessing.
4. `php bin/smoke-test.php` must end in `Result: PASS`.
5. `POST /v1/health/live` must answer `{"ok":true,…}`.

Restoring `config.php` restores the DB credentials and `jwt_secret`. If the
secret changed, access tokens issued before the restore are invalid;
refresh tokens still work, because they are opaque and stored as hashes that
do not depend on the key.

**Tested restore:** the procedure above is exercised in
`lab/tests/test_host_api.py::test_migrate_applies_once_then_skips_and_refuses_drift`
against a scratch database — apply, re-run (skip), checksum drift (refusal),
and a populated database without a tracker row (refusal) — and the whole
suite provisions its database from `001_initial.sql` on every run, which is
the restore path from an empty server.

### 6.3 Rotation procedures

| What | How | Effect |
|---|---|---|
| `jwt_secret` | generate a new 32-byte hex, edit `config.php`, reload PHP | every access token invalid immediately; clients re-authenticate on their next refresh |
| Admin password | `IRICA_ADMIN_PASSWORD=… php bin/create-admin.php --reset-password --username=…` | new hash, `token_version` bumped, every session for that user revoked |
| App DB password | change in MySQL, update `config.php`, re-run `smoke-test.php` | existing connections drop on next reconnect |
| Device | revoke in `sync_devices` (`status = 'revoked'`) or bump its `token_version` | that device's in-flight access token dies on its next request |
| A user's sessions | revoke `auth_sessions` by `family_id`, or bump `users.token_version` | all of that user's tokens die immediately |

Never rotate by editing a migration, and never by copying a token between
machines — enrol a device instead.

---

## 7. Residual risks and known gaps

1. **`jwt_secret` compromise is total for the access-token path.** The
   signature check is bypassable if the key leaks, but
   `Auth::principalFor()` still requires a live, unrevoked `auth_sessions`
   row, so a forged token is worthless without a matching session row —
   which is the reason for §3.1's database re-read. Rotation is still the
   right response, and the refresh path is unaffected.
2. **There is no user-management endpoint** (spec §6 has none): creating a
   second non-admin account is a CLI/SQL operation. That is a product gap,
   not a hole — it means nobody can grant themselves a role over HTTP.
3. **One shared tenant.** The SQL is tenant-scoped, and the cross-tenant
   test proves a foreign id answers 404, but there is no UI to onboard a
   second tenant.
4. **Proxy trust is explicit.** Only exact validated IPv4/IPv6 peers in
   `trusted_proxies` may assert a single `X-Forwarded-Proto: https`. Configure
   proxies to overwrite incoming scheme headers. Native `HTTPS=on` is accepted.
   `allow_loopback_http` defaults false and is for direct local development
   only; a configured loopback proxy reporting HTTP or no scheme never bypasses
   TLS. Apply these settings to the API, migration page and setup page.
   Setup additionally requires an explicit default-off maintenance flag,
   POST-only secret, isolated CSRF session and durable one-shot creation gate.
5. **Rate limiting is per-install.** It defeats credential stuffing against
   this host; it does not defeat a distributed attack from many IPs. Add an
   upstream (Apache `mod_evasive`, a WAF, or fail2ban) if that becomes real.
6. **Backups are not verified automatically.** The restore procedure is
   written and the migration half of it is tested; actually loading a dump
   on a spare database is an operator duty (see `INSTALL.md` §10).

---

## 8. Incident checklist

If you suspect this host was reached:

1. `IRICA_ADMIN_PASSWORD=… php bin/create-admin.php --reset-password --username=admin`
   — rotates the password and kills every session that user holds.
2. Revoke devices: `UPDATE sync_devices SET status='revoked' WHERE …;` —
   each one's access token dies on its next request.
3. `UPDATE auth_sessions SET revoked_at = UTC_TIMESTAMP() WHERE revoked_at IS NULL;`
   — kills every refresh token family at once.
4. Generate a new `jwt_secret` in `config.php` if you believe it leaked.
5. Rotate the app DB password in MySQL and `config.php`.
6. `php bin/smoke-test.php` → `Result: PASS`.
7. Read `var/log/api.log` filtered by the `request_id`s you care about —
   `code`, `route`, `tenant_id`, `user_id`, `device_id` and `ip` are all
   present and all safe to export.
8. Restore from the most recent backup only if the dataset itself is
   suspect; follow §6.2.
