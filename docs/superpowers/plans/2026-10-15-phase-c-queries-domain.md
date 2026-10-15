# Phase C — Queries and Transactional Domain Commands

> **DRAFT — REVISION ROUND 3 — NOT approved for implementation dispatch.**
> This document requires (a) controller sign-off on its decision gates — **DONE**, rulings 79–81 in
> `execution-rulings.md` — (b) independent review by a reviewer **other than the author and other than the
> round-1 reviewer** of this revised draft, and (c) controller approval, before any worker may execute a
> task below.
> Status: `DRAFT · REV-3` · Author role: plan-authoring subagent · Baseline commit: `f183134`
>
> Round 2 passed the required second independent review as **APPROVE WITH REQUIRED REVISIONS —
> 1 Critical / 8 Important / 12 Minor** (`.superpowers/sdd/2026-10-11-host-identity-integrity/phase-c-plan-review-2.md`).
> The controller settled the Critical and the two decisive Importants in `execution-rulings.md` **rulings 90–94**;
> ruling 94 makes this round a required revision and requires an existing reviewer to recheck the revised
> dependency chain, transaction boundary, executable FK migration and observable M1 test — it does not
> require a third full review. Implementation remains blocked until that recheck and controller approval.

### 0.1 Revision round 2 — what changed and on whose authority

Round-1 independent review **REJECT**ed the previous draft with **3 Critical / 7 Important / 10 Minor**
(`.superpowers/sdd/2026-10-11-host-identity-integrity/phase-c-plan-review.md`). The controller confirmed the
two sharpest Criticals against the repository and settled every Critical and the G7/idempotency rulings in
`execution-rulings.md` **rulings 82–88**; ruling **89** requires a **second** independent review of this
revision, by a reviewer who is neither the author nor the round-1 reviewer, before any task is dispatched.
Controller adjudication does not substitute for that check.

| Finding | Ruling | Where this revision fixes it |
| --- | --- | --- |
| **C1** suite-name ownership deadlock | 83 | §6.1 ownership rows, §6.5 dependency graph, Task 1 Steps 6–7, Task 1 acceptance, per-task "Verification" contract in §6.5 and §7 |
| **C2** `profiles` / inquiry-result storage missing | 84 | §6.2 content row (the domain migration — `005_*` since the ruling-91 split), new §6.2.1 schema contract, Task 9a Produces/Steps, Task 3 Consumes and graph edge, Q8/C5 identity reference |
| **C3** graph vs. parallel sets vs. `CrmCommands.php` | 85 | §6.5 dependency graph and lanes (the unconditional parallel-safe set is deleted) |
| **I1** G7 premise was false | 86 | §1.3 gate table, §3.3 G7 row, §4.0 strict-body bullet, Task 1 Step 5, §9.1 G7, §10 item 2 |
| **I2** invented `withWorkspace` signature | 87 | §4.0 Authorization bullet, §6.3 transaction-ownership paragraph |
| **R1** `idempotency_keys` is actively used | 88 | §3.1 C25, §5.5, Task 9b, §10 item 5 |
| **I3** M1/G1 test mechanism unachievable | 80/83 | §3.3 M1 row, §6.1 front row, §6.2 harness row, Task 1 Steps 1/3, Task 10 Step 6, §10 item 14 |
| **I4** `Migrations.php` / `MigrationPreflight.php` unowned | — | §6.1 ownership rows, §5.3 detection location, §10 items 3 and 8 |
| **I5** replay race unspecified | — | §5.4, §5.5, §6.4 step 5 (write-back step 8), Task 9b Step 1 |
| **I6** §10 item 1 already answerable | — | §6.3, §10 item 1 (CLOSED) |
| **I7** cross-tenant *reference* ids | — | §4.0 scoped-lookup bullet, §4.2 preamble, C6/C9/C17/C22 rows |
| **M1–M10** Minors | 82 (register) | **M1** §10 item 5 + §5.5 + §3.1 C25; **M2** §10 item 2 + §3.3 G1 row; **M3** §9.2 BC-7 + §10 item 12; **M4** §3.1 C11 and §4.2 C11 (twelve actions, ten owned here); **M5** §3.1 C24 (who writes `dispatched`/`expired`); **M6** §4.0 capability mapping and §3.1/§4.2 C23; **M7** §6.2.1.1 (`tenant_id` in both tables, 003 composite FKs) and §9.2 BC-5; **M8** §4.0 id grammar (hyphenated UUID only); **M9** §6.1 `test_workspaces.php` row + T9a Step 1; **M10** §6.1 `Http.php` row (T1 adds / T10 reconciles) |

### 0.1.1 Revision round 3 — what changed and on whose authority

Round-2 independent review returned **APPROVE WITH REQUIRED REVISIONS — 1 Critical / 8 Important / 12 Minor**
(`.superpowers/sdd/2026-10-11-host-identity-integrity/phase-c-plan-review-2.md`); its own finding register
renumbers those as **C-1**, **I-1..I-8** and **m-1..m-12**, and that numbering is used below. The controller
settled the Critical and the two decisive Importants in **rulings 90–94**; ruling 94 requires an **existing**
reviewer (not the author, not either previous reviewer) to recheck the revised dependency chain, transaction
boundary, executable FK migration and observable M1 test — it does **not** require a third full review.

This revision was executed in two parts (ruling 95 records the split): the structural half landed first
(**C-1**, **I-1**), and the remainder (**I-2..I-8**, **m-1..m-12** plus the four imprecise-citation
editorials the round-2 reviewer listed as "also noted") is applied here.

| Finding | Ruling | Where this revision fixes it |
| --- | --- | --- |
| **C-1** operations row is the first write of every command but its table and `Operations.php` land last | 91 | §6.1 ownership rows (foundation migration, `Schema.php`, `Operations.php`, `test_foundation.php`), §6.2 rows, §6.5 backbone and lanes; T1 owns migration `004_*` (operations + intent only, brand new and empty, therefore no pre-migration detection and no `Migrations.php` edit) plus `lab/fixtures/host_v2/test_foundation.php` as the tested foundation gate; T9a owns the separate domain migration `005_*` |
| **I-1** 003 does not supply the composite parent keys the plan claimed | 92 | §6.2 domain-migration row, §6.2.1.1: "reuse existing candidate keys and add only required keys that are absent"; 003 adds `UNIQUE (tenant_id, id)` for exactly `customers`/`owners`/`periods`/`crm_devices` (`003:57-67`), nothing for `identities`, and never touches `tenants` |
| **I-2** generated fixture config omits `log_path`, so `Log::line()` no-ops | 93 | §6.2.1.2 harness contract: the generated config carries an isolated, writable temporary `log_path`, consumed by `index.php:64-65` |
| **I-3** M1 pinned as "exactly `route`, `status`, `code`" | 93 | §3.3 M1 row, §6.2.1.2 M1 bullet, T1 Step 1(d)/Step 3/S-acceptance: the record must **contain** `route`, `status`, `code` with the expected values, be drawn from `Log::ALLOWED_FIELDS`, and keep `ts` and the Phase B metadata; deleting a production observability field to satisfy a test is forbidden |
| **I-4** T1 Step 5(vi) requires a `/v1` request through a `/v2`-only front | 94 (disposition) | T1 Step 5(vi) re-specified as a pure `Http::jsonBody()` suite case with an explicit waiver path and owner; §6.2.1.2 records that the fixture front 404s every non-`/v2` path |
| **I-5** C6's `serial1`/`serial2` `≤15` contradicts the oracle | 94 | §3.1 C6, §4.2 C6/C7: exactly 15 ASCII digits after `normalize_digits()` — `crm.py:393-395` — with storage `VARCHAR(15)` (`001:483-484`) |
| **I-6** T2 Step 4's read-only snapshot is invalidated by the same step's fixture writes | 94 | T2 Steps 1 and 4: seed and commit → snapshot A → sweep → snapshot B → assert equality |
| **I-7** T1 `Files` omits `lab/tests/test_host_v2_api.py` | 94 | Task 1 `Files` list |
| **I-8** the I5 replay-race evidence cannot distinguish the operations lock from the tenant lock | 94 | §5.4 note, §6.4 step 5, T9b Step 1: the discriminating assertion isolates the operations lock |
| **m-1..m-12** Minors | 94 | **m-1** §6.1 stub row + T1 Step 6 (the **runner** prints `FIXTURE_*_PASS`, `run_host_v2_tests.py:389-391`); **m-2** §7 timeout budget; **m-3** §6.2.1.1 re-record semantics; **m-4** §6.5 graph (`T8 ← T3`); **m-5** T7 Step 1 (Q9/Q10 are T2's/T10's); **m-6** §4.2 cond.1 and T7 Step 3 cross-refs; **m-7** T1 M1 scoped to the five Phase B routes; **m-8** T10 `Files` evidence-pack path; **m-9** §6.1 `Migrations.php`/`MigrationPreflight.php` rationale (already in place, verified); **m-10** C1 `telegram_id`/`instagram_id` labelled a Phase C addition; **m-11** C4/C5 oracle gaps; **m-12** C20 rejection labelled a documented divergence |
| **Imprecise citations** ("also noted", not counted) | 94 | `crm.py:381-386` → `crm.py:396-397` (§5.1 I1, T5 Step 1); `003:85-86` → `003:86-88` (§5.1 I2); `test_host_v2_api.py:944` → `:935` for the `def` (§3.3 G5, T1 Step 4, §7) |
| **T4 account-actor exception** (Advisor ruling, not a round-2 finding id) | **96** | Task 4's previous temporary condition — which had no owner and no retrofit step — is **replaced**: T4 may omit the Phase C actor columns only while implementing against the pre-005 schema, the exemption is **owned by T9b**, **expires at T9b completion**, and Phase C cannot be accepted or released with any T4 mutation path still exempt. §4.2 condition 3 is clarified to govern the **completed** implementation without weakening. T9b (which depends on T9a) gains the retrofit responsibility for every T4 mutation surface, the historical-data rule (deterministic backfill only; unattributable rows are legacy data) and a six-part acceptance gate. Migration 004 (T1) stays operations + intent only; migration 005 (T9a) stays the sole schema owner of the actor columns |
| **Round 4 — focused recheck of the ruling-91 migration split** (five corrections, not a round-2 finding id) | — | A focused recheck found the 004/005 split only partially propagated, plus one registration gap and three editorial defects, fixed as: **F1** T1 Step 6 now registers an **eighth** name, `foundation`, bound to T1's own `test_foundation.php` (created in Step 0, never a stub), with the `expected` map, the `_INTEGRATION` count (14 suites), the "seven → eight" wording in §6.1/§7/T10 and §7's T1 row updated; **F2** every stale pre-split `004` reference to domain objects is now `005` / "the domain migration" (§5.3 detector and `profiles` row, §6.2.1.1 `inquiry_results` parent-key note, §6.5 backbone and lane rule, T3 acceptance, T9b Step 3, §10 items 3/4/11/13, §11 items 1/2/5); **F3** `OperationsContract` gains an explicit `claimWithin()` signature and the operations table's `NOT NULL` outcome columns are resolved by a pending-default claim insert plus a same-transaction `recordWithin()` write-back, with the classified pre-commit failure recorded post-rollback by `recordFailure()` (§5.4, §6.3); **F4** §5.4's business-row cross-reference is §6.4 **step 6**, not step 5; **F5** the M1 log-line selector skips non-record lines and is pinned to the append window (§6.2.1.2, T1 Step 1(d)), and §7's T1 row now lists the suites T1 runs |

---

## 0. Authorization and evidence boundary

The controller selected **drafting this DRAFT plan only**. Neither this document nor its code recipes
authorize implementation, DB provisioning, migration, commit, push, release, deployment, cutover, production
data access or external-network testing. Per `execution-rulings.md` ruling 78, the order before ANY worker is
dispatched is: (a) controller settles the UNKNOWN / decision-gate list in §9 and §9.2 — **DONE**: the Advisor
settled gates B-2..B-10 in ruling 79, corrected the blocker classification in ruling 80, and rulings 82–88
closed every Critical and the G7/`idempotency_keys` rulings; (b) independent review of **this revision** by a
reviewer who is neither the author nor the round-1 reviewer — **still required** (ruling 89); (c) controller
approval — **still required**. Ruling 81 is explicit that completing §9/§10 alone does not make this plan
review-ready, and ruling 89 is explicit that controller adjudication does not replace the round-2 check.

Phase B's completion does **not** authorize Phase C. The checkpoint amendment that produced commits
`c2bd654` and `f183134` explicitly does not authorize committing a Phase C plan, so this file must remain
uncommitted unless separately authorized.

This plan is written against a **fixture-only** evidence base:

- MariaDB 10.3.39 on Windows, disposable fixture datadir, is the only database evidence available.
- MySQL 5.7 remains **BLOCKED and UNRUN** (Phase B handoff §5). Nothing here infers 5.7 compatibility.
- `lab/tests/test_host_api.py`, `test_host_schema.py`, `test_create_admin.py`,
  `lab/scripts/run_panel_tests.py`, `run_online_feasibility.py` and `lab/tests/test_online_*` are
  **unrun** gates (handoff §6). They stay unrun; their absence is never counted as green.
- `/v1` remains the served API. `/v2` is a separation mechanism, **not** a deployment or cutover approval.
- No whole-product PASS may be claimed from any Phase C task.

Authorization isolation, tenant isolation and finance behavior are proven only against the local fixture DB
and loopback HTTP front. No production host, customer data, credential file, `host/config/config.php`,
panel local data or external service is touched by any task in this plan.

## 1. Baseline and evidence

**Baseline commit:** `f183134` — `feat(host-identity): Phase B foundation — accounts, workspaces,
purpose-bound sessions and /v2` on branch `lab/host-identity-phase-b`. Working tree was clean at drafting
time.

**Earlier Phase A baseline:** `c2bd654` — `chore(phase-a): checkpoint the Phase A online-feasibility
baseline` (21 paths, 3655 insertions). It is an ancestor of `f183134`, not the Phase C starting point.

Stated exactly: Phase C starts from the committed Phase B tree. Phase A evidence (88 harness tests, offline
Chrome/panel continuation) is bounded laboratory behavior and never a production acceptance claim.

### 1.1 Evidence carried into Phase C

| Evidence | Location | How Phase C uses it |
| --- | --- | --- |
| Phase B plan (structure, interface contract, lock order) | `docs/superpowers/plans/2026-10-11-host-identity-integrity.md` | Template and binding constraint source; §Lock order extended in §6.4 below |
| Phase B handoff (G1–G12, unrun gates, NOT-done, service contracts to C/D/E) | `.superpowers/sdd/2026-10-11-host-identity-integrity/recovery-snapshot-2026-10-15/lab/docs/HOST_IDENTITY_PHASE_B.md` | Gap disposal table in §3.3; §9 "Service contracts handed to C/D/E" is the Phase C reuse contract |
| Final whole-branch review A (security lens) | `.superpowers/sdd/2026-10-11-host-identity-integrity/final-review-a.md` | SHIP WITH NOTED RESIDUALS, 0 Critical / 0 Important / 3 Minor (M1–M3) |
| Final whole-branch review B (correctness lens) | `.superpowers/sdd/2026-10-11-host-identity-integrity/final-review-b.md` | SHIP WITH NOTED RESIDUALS, 0 Critical / 0 Important / 1 Minor (stale-version message operands) |
| Execution rulings 1–78 | `.superpowers/sdd/2026-10-11-host-identity-integrity/execution-rulings.md` | Rulings 77–78 govern this drafting task; rulings 1–76 remain in force for shared files |
| Execution rulings 79–81 (decision gates), 82–89 (round-1 review + its resolutions), 90–95 (round-2 review + its required revisions) and **96** (the T4 account-actor exception) | same file, `## Phase C decision gates settled (2026-10-15, Advisor)`, `## Phase C plan review round 1 (2026-10-15)` and `## Phase C plan review round 2 (2026-10-15)` | B-2..B-10 as decided facts (§9.1); C1–C3, I1, I2, R1 applied per §0.1; ruling 89 required the round-2 review before dispatch; rulings 90–94 settle round 2's Critical and decisive Importants and require the round-3 revisions mapped in §0.1.1; ruling 95 records that round 3 was executed in two parts; **ruling 96 names T9b as the owner of the T4 account-actor retrofit and makes that exemption expire at T9b completion** (§0.1.1, Task 4, §4.2 cond. 3, Task 9b) |
| Phase C plan review round 1 — **REJECT**, 3 Critical / 7 Important / 10 Minor | `.superpowers/sdd/2026-10-11-host-identity-integrity/phase-c-plan-review.md` | The finding register this revision maps in §0.1; every Critical and Important finding must be verifiably closed in this text |
| Post-commit verification | `execution-rulings.md` rulings 74, 76 | `fixture-guard` exit 0 (43/43 OK); `--suite all` exit 0 with exactly one `FIXTURE_READY` (`driver=mysql server=10.3.39-MariaDB tables=25`), port 8766 closed afterwards |
| Fixture restart recipe | `execution-rulings.md` ruling 75 | Fixture datadir `%TEMP%/irica-b-fixture-00d0fe30d2a20095/data` + its `server.ini`; the pristine vendor datadir yields `Access denied` |

### 1.2 Binding Phase B contract this plan inherits

The single ruling that most directly shapes Phase C, from
`docs/superpowers/plans/2026-10-11-host-identity-integrity.md` Task 8 Step 5, and repeated in the handoff
§9: **"New account IDs cannot be written into legacy `actor_user_id` FKs. C adds separate account actor
columns when its mutation audit paths are introduced. Existing actor history is retained, not remapped
silently."**

Consequence for this plan: every Phase C mutation audit path writes new **account** actor columns of its own;
it never writes `audit_events.actor_user_id` or `change_log.actor_user_id` (both FK to legacy `users(id)`,
`host/migrations/001_initial.sql:241,284`). See Task 9b (invariants I12/I13) and gate B-3 in §9.1.

Also inherited verbatim from Phase B §9 (foundation only; each consumer still supplies its own
authentication, authorization and review):

- `Accounts`: transactional `createWithin`, `seedFirstOperator`, password policy (12 codepoints / 72 UTF-8
  bytes, no trim/normalise), legacy mapping unique on `account_id`.
- `AccountSessions`: purpose-bound admission; passive reads never renew idle; forced-change admits only
  me/logout/change-password; account-then-session lock order on every path.
- `Workspaces`: `lockAuthorizedWithin` performs lock-held lookup with expected-version compare after the row
  lock. `assign` / `remove` / `freeze` remain internal/CLI services in B; D owns their exposure.
- `V2Api`: exactly five routes (`/v2/auth/login`, `/v2/auth/me`, `/v2/auth/logout`,
  `/v2/auth/change-password`, `/v2/workspaces` — `host/src/V2Api.php:67-73`) with exact 404/405/415/400
  behaviour, 16 KiB body cap from `Content-Length`, unknown-field refusal, `no-store` closed-CORS.
- `Migrations` / `MigrationPreflight`: step journal, advisory lock, crash reconciliation, fail-closed
  adoption.

### 1.3 Decision gates settled before this revision

The draft this revision replaces referenced gates B-2..B-10 in its body while §9/§10 were empty. The Advisor
settled every one of them in `execution-rulings.md` ruling 79, and corrected the blocker classification in
ruling 80. They are recorded here as **decided facts**, and §9.1 repeats them in final form; nothing in this
document may reopen them:

| Gate | Ruling | Where it lands in this plan |
| --- | --- | --- |
| B-2 | **New dedicated intent table, mandatory in the foundation migration `004_*` (T1, ruling 91)** (an expiring transport-id dedup row cannot reconstruct, resume or reconcile a finance/job action) | §5.5, §6.2 and §6.2.1.1 rows, Task 1 (table) and Task 9b (service) |
| B-3 | **Q13-only resolution posture accepted**: caller-known `operation_id` before execution, atomic operation outcome with the business mutation, timeout = UNKNOWN not failure, `GET /v2/operations/{operation_id}` resolves | §5.6, Q13, C25, Task 9 Step 5 |
| B-4 | **Phase C mutations stay off the legacy `change_log` cursor** served to `/v1` by `host/src/Sync.php` | global constraint 5, §3.3, Task 10 Step 4 |
| B-5 | **`AccountPrincipal` frozen**; the acting session id is read inside the mutation transaction's locked callback from that exact authenticated session row | §4.0 Actor, §6.3 `$context`, G10 |
| B-6 | **Credentials fully redacted from `/v2`** by explicit safe-field allowlists; credential-field updates rejected; stored credentials preserved | §4.0, Q10, C20 |
| B-8 | **Path-ID model adopted**: parameterised `/v2/crm/.../{id}` routes are added as new routes; no second query-parameter identity form | §4.0 route table, Q2/Q4/Q9/Q12/Q13, Tasks 1/2/10 |
| B-9 | **Closed PHP vocabularies mirroring `crm.py`**; unknown value → 400, no silent fallback; parity-pinned | C21, Task 3 |
| B-10 | **Database constraints in the domain migration `005_*`** (tenant-scoped serial uniqueness, unique device ownership with tenant-matching composite FKs) **plus** retained domain checks; pre-migration violation detection; duplicate-key races → 409 | §5.1 I1/I2, §5.3, Task 9 |
| G1, M1 | Block **Phase C production acceptance and release, not implementation** | §3.3, Task 1, Task 10 |
| G7 | **New `/v2` behaviour (ruling 86).** Strict request parsing — duplicate-key and surrogate-key refusal — is a **new guarantee implemented at the `V2Api` request-parsing boundary** and is required before any Phase C JSON-mutating endpoint (C1–C25) is accepted. It is **not** an existing shared guarantee: `Http::jsonBody()` (`host/src/Http.php:260-294`) is last-duplicate-key-wins and stays unchanged, as does `/v1`. | §3.3 G7 row, §4.0 body-handling bullet, §9.1 G7, Task 1 Step 5 |

## 2. Global constraints (binding, carried from Phase B)

Every constraint below is inherited verbatim from
`docs/superpowers/plans/2026-10-11-host-identity-integrity.md` §Global Constraints unless marked
**C-specific**. A worker that violates any of them stops and escalates (§9.3).

1. Do not edit applied `host/migrations/001_initial.sql`. Preserve its bytes, checksum and historical
   identity/actor rows. This checkout has CRLF while the Git blob has LF; that difference is not permission
   to normalize an applied migration or reinterpret a deployed checksum. Actual deployed bytes/checksum are
   unknown and require an authorized host preflight — outside this plan.
2. No drop, truncate or recreate of any schema object, as repair or otherwise.
3. No direct MySQL exposure, new Python host requirement, WebSocket server, Redis or background service
   daemon is introduced.
4. A new `/v2` route family separates the new contract from `/v1` during migration; old synchronization
   writes are disabled at cutover. **`/v1` stays the served API; `/v2` is not a deployment or cutover
   approval.**
5. Never run old synchronization and new authoritative writes against the same active workspace
   simultaneously. **C-specific:** Phase C mutations must not feed the legacy `change_log` cursor that
   `host/src/Sync.php` serves to `/v1` clients (see gate B-4).
6. A client-provided workspace ID selects a workspace but grants no authority.
7. The platform-admin permission is separate from a workspace admin.
8. All new source code, tests, UI strings, logs and checked-in documentation are English.
9. Existing duplicate/orphan rows stop constraint activation and produce a sanitized repair report, never
   auto-deletion.
10. Do not rely only on `CHECK` constraints across supported MySQL/MariaDB versions; domain validation and
    verified unique/FK behavior remain mandatory.
11. Keep `gomrok.py` byte-identical: SHA-256
    `5d00ac1516728986d2fcc314d7f6c862a7f9d14142b4e9307f812807b9986d5e`.
12. Do not read `host/config/config.php`, existing DB credential files, customer data or local panel data
    while planning or testing.
13. No production fallback, unbounded retry, external effect inside a retried transaction, raw
    password/token logging, token in URL/process arguments/environment, automatic legacy identity merge or
    destructive repair.
14. No commit/push is authorized by this plan. At task boundaries review the scoped diff and record
    verification; commit only after separate explicit authorization.
15. Existing v1 regression fixtures remain compatible; their destructive harness
    (`lab/tests/test_host_api.py`, `test_host_schema.py`) cannot be run against an arbitrary localhost DB
    and is **not** part of Phase C verification.
16. MySQL 5.7 stays **BLOCKED and UNRUN**. No 5.7 inference from MariaDB 10.3.39 results or syntax checks.
17. Never claim a whole-product PASS. `lab/tests/test_host_api.py` migrate-CLI assertions remain scoped out
    (handoff §7, ruling 19/66).
18. No secrets, credentials, tokens, session IDs, password hashes, usernames or IPs in source, tests, docs,
    fixtures or this plan.
19. Fixture-only DB access. The runtime fixture DB account cannot perform DDL; the separate synthetic DDL
    account is fixture-scoped only.
20. **C-specific:** no new account id is ever written into a legacy `actor_user_id` foreign key
    (`audit_events.actor_user_id`, `change_log.actor_user_id`, both FK to legacy `users(id)`). Phase C adds
    its **own** account actor columns when its mutation audit paths are introduced; existing actor history is
    retained, never remapped.
21. Do not reopen the denied Phase A Advisor final evidence gate, and do not replace
    `.claude/TASK_STATE.md`. The planning ledger stays under
    `.superpowers/sdd/2026-10-11-host-identity-integrity/`.

## 3. Scope and acceptance

Roadmap §C names the Phase C output as "Paginated `/v2` reads and transactional CRM, period, owner, stage,
ledger, profile and inquiry commands; durable intents and operation recovery", with the stopping condition
"**B fixture migrations pass. Existing domain fixtures are the behavioral oracle; stale edits and uncertain
outcomes cannot become success.**" The behavioral oracles are `crm.py`, `panel.py::crm_state_payload`, and
`lab/tests/test_crm_stage_transitions.py`, `test_crm_case_integrity.py`, `test_crm_ledger_integrity.py`,
`test_v1_6_accounting.py`, `test_v1_7.py` — all verified present in this checkout.

`V2Api::ROUTES` (`host/src/V2Api.php:67-73`) currently declares exactly five routes. **Every route name
below is planned, not existing**; it becomes real only when the owning task adds it and its tests pass.

### 3.1 In-scope operations (enumerated)

Each row is a separate deliverable with its own acceptance criteria. IDs are stable references for §4, §6
and §7.

**Queries — read-only, no mutation, no intent**

| ID | Operation | Acceptance (observable) |
| --- | --- | --- |
| Q1 | `GET /v2/crm/devices` — paginated device list | Filter by `stage`, `customer_id`, `owner_id`, `period_id`, `q` (name/serial substring), `include_deleted`; ordered by a declared keyset; `limit` bounded (default 50, max 200) with `after` cursor; total never unbounded; same-account caller with different workspace sees only its tenant's rows |
| Q2 | `GET /v2/crm/devices/{id}` — single device | 200 with full device row + `row_version`; 404 `NOT_FOUND` for unknown id **and** for an id in another tenant (indistinguishable from the caller's view) |
| Q3 | `GET /v2/crm/customers` — paginated customer list | Filter by `q` (name/mobile), `has_balance`; keyset ordering; bound `limit`; tenant-scoped |
| Q4 | `GET /v2/crm/customers/{id}` — customer detail | 200 includes `row_version` and computed `balance`; 404 for unknown/other-tenant |
| Q5 | `GET /v2/crm/customers/{id}/ledger` — paginated statement | Filter `from`/`to` on `at`, `kind`, `include_voided`; keyset on `(at, seq)`; bound `limit`; rows carry `seq`, `amount`, `kind`, `voided_at`, `void_reason`, `device_id`, `at`; balance uses the `_tx_countable` predicate exactly (`crm.py:673-697`) |
| Q6 | `GET /v2/crm/customers/{id}/balance` — balance only | Same predicate as Q5 and `customer_balance()` (`crm.py:693`); integer Toman; documented response shape |
| Q7 | `GET /v2/crm/owners` — paginated owner list | Filter by `q` (name/national), `bound` (bound vs free); keyset ordering; bound `limit`; **never returns another tenant's owner** |
| Q8 | `GET /v2/crm/identities` — paginated identity pool | Same shape as Q7; `available` filter using the pool rules of `pick_pool_identity()` / `identity_for_stage()` |
| Q9 | `GET /v2/crm/periods` and `GET /v2/crm/periods/{id}` | Ordered by `number` desc; includes `number`, `label`, `opened_at/closed_at`, device counts; 404 for unknown |
| Q10 | `GET /v2/crm/settings` | Returns the **safe-field allowlist** of the settings object including `links`; credential-class fields are never returned (gate B-6, §4.1 Q10) |
| Q11 | `GET /v2/crm/state` — composite state read | Parity with `panel.py::crm_state_payload` (`panel.py:2887`) for the host-owned portion: devices joined with inquiry results and profile status; documented as **no** local path/`runner_pid`/`browser_visible` leakage; bounded row counts |
| Q12 | `GET /v2/crm/devices/{id}/invoice` | Parity with `crm.py::device_invoice` (`crm.py:2521`); integer Toman totals; 404 for unknown device |
| Q13 | `GET /v2/operations/{operation_id}` | Returns the stored operation result for a known id owned by the caller's tenant; 404 otherwise; this is the only supported way to resolve an outcome that is UNKNOWN after a timeout (§5.6, gate B-3) |
| Q14 | `GET /v2/intents` (pending intents) | Paginated, tenant-scoped, bounded; exposes state only, never credential material |

**Commands — transactional, each with expected row version + operation id**

| ID | Operation | Acceptance (observable) |
| --- | --- | --- |
| C1 | Create customer | Validates per `crm.validate_customer` (`crm.py:259-272`), with `telegram_id`/`instagram_id` ≤64 recorded as a **Phase C addition** (§4.2 C1, round-3, m-10); generates stable id; sets `row_version=1`; 409 `VERSION_CONFLICT` impossible on create; `VALIDATION_FAILED` with a structured field list on bad input |
| C2 | Update customer | Requires `expected_row_version`; row locked then re-read; mismatch → 409 `VERSION_CONFLICT` with both versions; success increments `row_version` |
| C3 | Delete customer (soft, `force`) | Mirrors `crm.delete_customer` (`crm.py:622`): without force and with active transactions → refusal; **books no refund** when delete succeeds (`test_crm_ledger_integrity.test_delete_without_customer_books_no_refund`); sets `deleted_at`/`deleted_by` |
| C4 | Create / update / delete owner | Mirrors `crm.validate_owner` + card internal-space normalization (`crm.py:275-316`, tests `test_card_internal_spaces_normalized_on_store`, `test_update_owner_survives_row_without_card_no`) **including the required owner mobile (`crm.py:301-303`) and the optional `[A-Za-z0-9]{1,20}` passport (`crm.py:313-315`)**; delete refuses a bound owner |
| C5 | Create / update / delete identity (pool) | Mirrors `crm.validate_identity` (`crm.py:319-341`) for `national`/`birth`/`mobile` only; the `label ≤80` bound is the host column, not an oracle rule (§4.2 C5, round-3, m-11); delete refuses an identity still referenced by a live inquiry result |
| C6 | Create device | Mirrors `crm.validate_device` + `_serial_conflict_errors` (`crm.py:343-415`): `serial1` required and, with `serial2` when set, **exactly 15 ASCII digits** (`crm.py:389-395`); `serial1` unique within tenant across both slots; requires an open period when `require_period`; Persian-free English error codes |
| C7 | Update device | Requires `expected_row_version`; **serial immutability** and the fee-reversal guard of `test_immutable_serial_error_mentions_fee_reversal` |
| C8 | Delete device | Mirrors `crm.delete_device` (`crm.py:1388`): disables/detaches linked profile, removes the inquiry row, and books the registry-fee refund only when the fee row exists and the refund feature is enabled (`test_delete_with_fee_disabled_books_no_refund`, `test_double_delete_cannot_double_refund`) |
| C9 | Attach owner to device | 1:1 binding (`crm.attach_owner`, `crm.py:1441`); refusal when the owner is already bound to another device; sets `stage=ready_owner` when called through the stage path |
| C10 | Detach owner from device | `crm.advance(action='detach_owner')`; stage unchanged except the `problem`/`ready_owner`/`closed` cases in `ALLOWED` |
| C11 | Stage command `advance` | The twelve actions of the `ALLOWED` map (`crm.py:108-150`): `queue_inquiry`, `mark_problem`, `mark_ready`, `close_case`, `requeue_inquiry`, `detach_owner`, `attach_owner`, `handover`, `to_payment`, `start_payment`, `confirm_payment`, `deliver_code`. `attach_owner` and `detach_owner` are C9/C10's surface and are excluded from C11's payload by name; the other ten are C11's. Refuses any action not permitted from the current stage with a structured 409 stage refusal (code name is
  BC-8; T1 Step 4 already declares `STAGE_NOT_ALLOWED`) |
| C12 | Stage command `stage_back` | Uses `_STAGE_BACK` (`crm.py:153`); clears `_STAGE_TIMESTAMP_KEY` stamps for every stage left, including `done_at`; `closed` is terminal → refusal |
| C13 | Stage command `stage_skip` | Uses `_STAGE_SKIP_NEXT` (`crm.py:172`); appends a `skipped_stages` provenance row; **no skip path may reach `done`**; `problem` re-enters the mainline (`test_skip_branch_has_no_done_stamp`) |
| C14 | Stage gates | `handover` refuses without a bound, resolvable owner; `mark_ready` refuses unless both inquiries passed for this device (`_inquiry_gate_errors`, `crm.py:1496`); duplicate tick (`handover`/`confirm_payment`/`deliver_code` already stamped) is a documented refusal, not a silent no-op |
| C15 | `close_case` | Requires a non-empty reason; sets `closed_at`/`closed_reason`; `closed` is terminal except `detach_owner` |
| C16 | Add ledger transaction | Mirrors `crm.add_transaction` (`crm.py:698`): integer Toman, digit normalization (`normalize_digits`), appends with monotonically increasing per-customer `seq`, unique `(tenant_id, customer_id, seq)` |
| C17 | Void ledger transaction | Mirrors `crm.void_transaction` (`crm.py:719`): stamps `voided_at`/`void_reason` and appends a `void` reversal; refuses re-void and refuses voiding a `void` row; half-pair guard for `base_fee`/`base_fee_refund` on the same `device_id`; second concurrent void → refusal, never double-reverse |
| C18 | Open period | Mirrors `crm.open_period` (`crm.py:934`): `_next_period_number` monotone per tenant, unique `(tenant_id, number)`; at most one open period |
| C19 | Close period | Mirrors `crm.close_period` (`crm.py:991`): `closed_at/closed_by/closed_reason`; device period links preserved; refuses close when already closed |
| C20 | Update settings | Validates each bounded field (`_normalise_settings`, `crm.py:424`); `current_period_id` must be a same-tenant period; links array shape-checked |
| C21 | Record inquiry result | `InquiryResults`: writes the per-device inquiry outcome that `mark_ready` gates on; bounded status vocabulary; requires an open/known device; **no robot invocation, no network call** |
| C22 | Create / link profile | `Profiles`: linked profile creation referenced by `crm_devices.profile_id`; bounded create/update/link/unlink; unlink disables the detached profile (`_disable_detached_profile`, `crm.py:1425`) |
| C23 | Create durable intent | `CommandIntents`: for a finance or job-affecting command, write a recoverable intent **before** any external effect; state machine `pending → dispatched → settled/rejected/expired`; unique per `(tenant_id, transport_id)`; **capability is the underlying command's capability, never a hard-coded `edit`** — a `delete`/`settings`/`period_admin` command wrapped in a C23 intent is creatable only with that same capability |
| C24 | Resolve intent | Reconcile a locally observed outcome to `settled` or `rejected`; **never automatic repeat** of an uncertain external action. Phase C's C24 API accepts exactly `settled` and `rejected` and answers 400 `INTENT_STATE_INVALID` for anything else. The two remaining vocabulary values are **not reachable through C24** and are named with their writer so they are not dead entries: `dispatched` is written only by a Phase E job adapter (which does not exist in this checkout, §5.6 item 7), and `expired` is written only by T9b's bounded retention sweep on a `pending` row older than the retention window. Q14 still filters on the full five-value vocabulary (BC-5) |
| C25 | Operation commit + recovery | `Operations`: atomic operation digest + committed result. The operation table is a **Phase C table in the foundation migration `004_*`** (gate B-2, §5.5, ruling 91); the legacy `idempotency_keys` table is in **active use** by `/v1` Import/Sync (`host/src/Import.php:175,224`, `host/src/Sync.php:700,725,746`) and is never written to, repurposed, truncated or re-retentioned by Phase C. Replay with the same transport id returns the stored body; replay with a different body and same id → `IDEMPOTENCY_MISMATCH`; a caller may query Q13 after a transport timeout |

### 3.2 Non-goals

- **No** invoice or installment models absent from the current domain (roadmap §C required contract). `Q12`
  reports the existing `device_invoice` computation only; it creates no new receivable model.
- **No** deployment, cutover, `/v1` retirement, release, installation, or production migration.
- **No** operator administration UI, cookies/CSRF, account/workspace mutation exposure, package import,
  sealed staging, image storage, or backup — Phase D.
- **No** desktop broker, RAM-only session context, worker/robot adapters, job leases, Telegram sends, or
  panel/UI preservation — Phase E, and `gomrok.py` stays byte-identical.
- **No** MySQL 5.7 run, no legacy-harness run (`test_host_api.py`, `test_host_schema.py`,
  `test_create_admin.py`, `run_panel_tests.py`, `run_online_feasibility.py`, `test_online_*`).
- **No** edit of `001_initial.sql`, **no** drop/truncate/recreate, **no** schema recreation for domain
  tables.
- **No** change to legacy `Auth`/`Sync`/`Import` JWT semantics or legacy `Principal`.
- **No** network egress of any kind from a test; no robot or external-service interaction inside a
  transaction.
- **No** commit or push.

### 3.3 Disposal of Phase B gaps G1–G12 and security findings M1–M3

Legend: **release blocker** = must be closed before Phase C production acceptance and release, but does
**not** stop independent implementation of the Phase C tasks (ruling 80); **non-blocker** = accepted for
Phase C with the stated rationale; **resolved-at-baseline** = verified already correct in `f183134`.

| ID | Phase B residual | Disposal for Phase C | Rationale |
| --- | --- | --- | --- |
| G1 | No test binds `host/public/index.php`'s v2 branch; flipping its fixture flag or deleting the early return ships green | **release blocker**, owned by Task 1 (the binding test lands with the dispatch foundation) | Verified: `lab/fixtures/host_v2_front.php:71-84` **delegates** classification (`V2Api::logLabel`, `V2Api::isVersion2Request`) and then calls `V2Api::dispatch` **directly** — it never enters `index.php`, and its transport gate is `assertTransport(..., fixture: true)` (`host_v2_front.php:82`) where the production branch passes `false` (`index.php:102-106`). Every `--suite api` green is therefore produced on a parallel path. Per ruling 80 this blocks **production acceptance and release, not implementation**: independent implementation proceeds, but no Phase C claim of "reachable in production" is valid until the binding test exists. The mechanism is fixed, not left as a choice — see Task 1 Step 1(d) and §6.2.1.2; if it cannot be built, the worker stops per §9.3(5) and the controller records a waiver in §10 item 14. |
| G2 | The front's SAPI and exact-loopback guards (preconditions ruling 43 rests on) are exercised by no test | non-blocker, with a task-level obligation | Phase C's evidence still depends on the front, but the front is unchanged by Phase C and the guards fail closed. Task 2 must not edit `host_v2_front.php` guards; any edit escalates. |
| G3 | `issuedForAnotherPurpose()` has no liveness predicate; revoked/expired other-purpose session labelled purpose-mismatch (401 either way) | non-blocker | Possession prerequisite plus identical 401 outcome; Phase C inherits it unchanged. Do not "fix" it inside a Phase C task — it belongs to a later auth pass. |
| G4 | api `test_c14` hardcodes the log path and ignores `IRICA_V2_LOG` | non-blocker | Test-polish on a Phase B fixture; Phase C must not add further hardcoded log paths. |
| G5 | The `Codes::ALL` agreement guard cannot run without a listening front | **Load-bearing, satisfied by the plan's verification** | Verified: the assertion's `def` is `test_d03_codes_all_equals_the_declared_constant_set` at `lab/tests/test_host_v2_api.py:935` (the body's `$all = Codes::ALL;` is at `:944`), inside `--suite api`. `Codes::ALL` currently lists exactly 34 entries (`host/src/Http.php:88-102`). Phase C adds codes, so `--suite api` is **mandatory, not optional**, in every task that touches `host/src/Http.php`. |
| G6 | The accounts race test is opportunistic, not forced | non-blocker | Account admission is Phase B surface; Phase C does not modify it. |
| G7 | The PHP duplicate-key tokenizer and surrogate rejection have no dedicated test | **acceptance blocker for Phase C JSON-mutating endpoints**, not a DDL blocker — and the guarantee is **new `/v2` work**, not an existing one | Ruling 86 corrects the premise: `Http::jsonBody()` (`host/src/Http.php:260-294`) decodes the body twice and is **last-duplicate-key-wins**; the only strict tokenizer in the tree is `BFixture::decode()` (`lab/fixtures/host_v2/bootstrap.php:106-137`), whose only callers are fixture-target/config paths and which throws `FIXTURE_TARGET_REJECTED`, never a 400. Therefore duplicate-key and surrogate-key refusal is **implemented at the `V2Api` request-parsing boundary, before decoding can lose a duplicate**, and is a **new `/v2` behaviour**. `Http::jsonBody()`, its six call sites in `host/public/index.php`, and `/v1` stay **unchanged** (global constraint 15); T1 Step 1 asserts that explicitly. Required **before** any Phase C JSON-mutating endpoint (C1–C25) is accepted; DDL-only work (either Phase C migration) need not wait on it. |
| G8 | Latent uncaught `JsonException` in the PHP JSON reader (Task 1 deferred minor) | non-blocker for commands; **verified partially resolved** | `Http`'s request reader catches `JsonException` and returns 400 `BAD_PAYLOAD` (`host/src/Http.php:280-285`), and `lab/fixtures/host_v2/bootstrap.php:112-113` catches it. The remaining uncaught site is the fixture tokenizer at `bootstrap.php:124` (JSON-pointer path decoding), fixture-only. Command bodies therefore already fail closed; Task 1 must still assert one malformed-body case per command family. |
| G9 | `outcome <> 'success'` assertion in `test_migrations.php` is globally unscoped | non-blocker | Fixture-test polish; Phase C must not widen it, and must not count it as evidence of mutation-audit cleanliness. |
| G10 | `AccountPrincipal` carries no `sessionId` (extra `displayName` instead); a principal-only caller cannot name its session row | **settled by gate B-5**; no further gate | `AccountPrincipal` stays frozen — no Phase C task edits that Phase B file. The acting session id is read **inside the mutation transaction's locked callback**, from that exact authenticated session row (`account_sessions.id`), never selected by an arbitrary account/tenant match, and never left silently null. If a mutation path cannot identify the exact session, it is an explicit contract gap and the worker stops and escalates (§9.3). |
| G11 | `Workspaces::assign()`, `remove()` and `freeze()` do not check `accounts.status`; only `lockAuthorizedWithin()` does | non-blocker for C | Verified in `final-review-a.md` M2: `guardMembershipChange` **does** check the target account; the missing check is the actor's status, and `freeze()` checks none. Phase C's business commands authorize via `lockAuthorizedWithin()`, which is unaffected. Phase C must not expose assign/remove/freeze (Phase D owns it, and handoff §9's "should" is corrected to "must"). |
| G12 | `lab/tests/test_host_api.py::test_migrate_applies_once_then_skips_and_refuses_drift` is scoped out | non-blocker | Ruling 19/66 scope-out stands; never counted green. |
| M1 | The production `/v2` branch's edge access-log line is **asserted by no test**, and its route label cannot name any Phase C route | **release blocker**, owned by Task 1 (`host/public/index.php` is Task 1's file) | Verified at `f183134`: `index.php` sets `$routeLabel = V2Api::logLabel(...)` for a v2 path (`index.php:86-91`), emits and `return`s from the v2 branch (`index.php:96-118`), and the trailing `finally` writes the access line with **six** fields — `request_id`, `method`, `route`, `status`, `duration_ms`, `code` (`index.php:328-335`) — to which `Log::line()` prepends `ts` (`host/src/Log.php:68`), seven keys in total, every one of them inside `Log::ALLOWED_FIELDS` (`Log.php:24-30`). A 5xx additionally emits one diagnostic `$log->line` in its catch arm (`index.php:316-318` for `PDOException`, `:324-326` for `Throwable`), so a request can produce two lines and the assertion selects the access line — the last line of the request — instead of requiring exactly one line per request. Three facts make this a release blocker, and they are the three this plan acts on: **(i)** no Phase C or Phase B test asserts that line, so its presence, field values and emission on denial/failure are unproven; **(ii)** the generated fixture config of §6.2.1.2 carries no `log_path`, and `index.php:64-65` therefore constructs `Log` with a `null` path, which `Log::line()` honours by writing nothing (`Log.php:65-67`) — the M1 line is unobservable through that harness until §6.2.1.2 names an isolated writable temporary path; **(iii)** `logLabel()` returns the literal `<unknown>` for any path not in the static `ROUTES` table (`V2Api.php:135-140`), so once Phase C registers `/v2/crm/...` routes the line cannot distinguish them. Ruling 80: sanitized edge logging — success, denial **and** failure — is required before releasing Phase C, while implementation proceeds. The assertion contract (round-3, I-3, ruling 93) is that the emitted record **contains** `route`, `status` and `code` with the expected values — never that those are its exclusive fields; `ts`, `request_id`, `method` and `duration_ms` are preserved and no production observability field may be deleted to satisfy the test. Task 1 adds the explicit assertion (scoped to the five Phase B routes, round-3, m-7) and Task 10 extends `logLabel()`/`knownRoute()` (as `V2Api.php`'s T10 co-owner) so every registered Phase C route is labelable; both are release gates, neither blocks implementation. |
| M2 | G11 misdescribed; handoff §9 says D "should" add the `accounts.status` check | non-blocker for C | Documentation correction owned by D's plan; Phase C must not restate it as "should". |
| M3 | "live row" claim for the purpose-mismatch relabel is inaccurate | non-blocker for C | Doc-only, 401 either way; same class as G3. |
| RB-1 | Review B Minor-1: `WORKSPACE_CONTEXT_STALE` message states versions backwards | **resolved-at-baseline** | Verified directly in `f183134`: `host/src/Workspaces.php:151-156` already reads `'moved on since version ' . $actual . '; this caller still holds ' . $expected`. Reviewer B read the pre-correction `package-a.md` snapshot. Phase C still owns the **analogous** business-row conflict message for `VERSION_CONFLICT`; Task 4 must assert operand order there. |

**Settled blocker classification (ruling 80, applied throughout this revision; G7's premise corrected by
ruling 86):** G1 and M1 block Phase C
**production acceptance and release only** — production-entrypoint integration coverage and sanitized edge
logging (success, denial and failure) are required before releasing Phase C, while independent implementation
proceeds; G7 is a JSON request-boundary acceptance gate on the mutating endpoints, not a DDL gate, and the
guarantee it gates is **new `/v2` parsing work**, not a shared one; G10 is
closed by gate B-5; RB-1's business-row analogue stays a Task 4 assertion.

**B-4 consequence (settled, carried into §5.5 and Task 10).** Phase C mutation audit is written
transactionally but stays **off** the legacy `change_log` cursor that `host/src/Sync.php` serves to `/v1`
clients, because `/v1`'s contract cannot represent Phase C rows or their semantics. Omitting log entries
alone does **not** prove isolation: Task 10 must test `/v1` **incremental** and **snapshot/bootstrap**
visibility and show that Phase C mutations produce no `/v1` cursor advance and no bootstrap row.

**Phase C actor columns vs. legacy `deleted_by` (verified at `f183134`).** Every business row's `deleted_by`
is a foreign key to legacy `users(id)` — `001_initial.sql:118,157,343,382,410,440,464,523,559`. A new account
id can therefore never be written there, exactly as global constraint 20 requires for `actor_user_id`.
The domain migration (Task 9a) adds Phase C **account actor columns of its own**; Phase C soft deletes set
`deleted_at` plus the new account column and leave the legacy `deleted_by` NULL. `periods.opened_by` /
`closed_by` are free-text `VARCHAR(64)` with no foreign key (`001_initial.sql:335-337`) and may carry an
account id, but they are not the audit channel.

## 4. Operation contracts

### 4.0 Conventions that apply to every Phase C operation

Verified against `host/src/V2Api.php` and `host/src/Workspaces.php`:

- **Response envelope.** `V2Api::dispatch()` returns `array{status: int, body: array, headers: array}`
  (`V2Api.php:213-239`, `route()` docblock at `241-245`). Success bodies are plain JSON objects; failures
  are produced by `V2Api::failure($status, $code, $message, $requestId, $retryAfter)`.
- **Body handling.** `$request->jsonBody()` refuses empty bodies, non-`application/json` (415
  `UNSUPPORTED_MEDIA_TYPE`), invalid JSON and non-object JSON with 400 `BAD_PAYLOAD`
  (`host/src/Http.php:260-294`). `V2Api::onlyFields($body, [...])` refuses unknown fields rather than
  dropping them. Every Phase C command must call both.
- **Strict request parsing is new `/v2` work (ruling 86).** `Http::jsonBody()` decodes the body twice —
  object, then array — and is **last-duplicate-key-wins**; it implements **no** duplicate-key refusal, and
  `Http.php` is **not** changed by Phase C. Consequently a duplicate key is already lost by the time
  `jsonBody()` returns, so Phase C's duplicate-key and surrogate-key refusal is implemented **at the
  `V2Api` request-parsing boundary, before decoding**: `V2Api` (T1) tokenises the raw body itself and
  answers 400 `BAD_PAYLOAD` on a duplicated object key at any depth or on a surrogate/lone-\uD800-\uDBFF
  code point, and only then hands the body to `jsonBody()`/`onlyFields()`. `/v1` keeps the six
  `jsonBody()` call sites in `host/public/index.php` (`:197,205,213,226,254,282`) and their last-wins
  behaviour **unchanged** (global constraint 15); T1 Step 1 asserts that compatibility fact as a named
  test so a later attempt to "share" the strict parser with `/v1` fails loudly.
- **Route table and parameterised dispatch (settled by gate B-8).** `V2Api::ROUTES` is an exact-string
  allowlist (`V2Api.php:67-73`) and `route()` resolves with a `match` on `$path` (`V2Api.php:261-269`).
  Unknown path → 404 `HTTP_404`; wrong method → 405 `HTTP_405`. `isServable()` (`V2Api.php:277-286`)
  additionally refuses `%`, `..`, `//` and `\` anywhere in the raw path. **Parameterised
  `/v2/crm/.../{id}` routes are new routes this plan adds**; they are the single identity form — no second
  query-parameter identity form is introduced. Because the router today does static `match` on `$path`
  only, T1 must verify and extend it; nothing may assume the router already supports parameterised
  dispatch, and nothing may weaken `isServable()` — parameterised matching runs **after** `isServable()` and
  under the same rules. Binding routing rules:
  - **Id grammar (pinned, one shape only).** A path parameter must be exactly the output shape of
    `Ids::v4()`: 36 characters, lowercase hex, hyphens at positions 9, 14, 19 and 24
    (`host/src/Ids.php:53-66` — it emits only that hyphenated form, never a bare 32-hex string). A segment
    that does not match answers 404 `HTTP_404`, never 400, so the path surface is not a validation oracle.
    Path parameters are **not** synthetic-id entities (`Ids::isSyntheticEntity()` is irrelevant here): the
    grammar test is a shape test, and no v5/uuid5 id is ever minted on a `/v2/crm` path.
  - **Precedence.** Exact static route strings win over any parameterised pattern for the same method; a
    static path that exists under another verb still answers 405 before parameterised matching is consulted.
  - **Existing error contract preserved.** 404 / 405 / 415 / 400 behaviour for the five Phase B routes is
    unchanged and re-asserted by T1 Step 6.
  - **Scoped lookup.** A matched id is always resolved with a `tenant_id = ?` predicate; unknown, malformed
    and foreign-workspace ids are tested separately and must be indistinguishable to the caller.
  - **Reference ids answer 404-foreign (round-2, I7).** The scoped-lookup rule is not limited to the *target*
    row of a mutation. **Every id a request references** — `customer_id`, `owner_id`, `device_id`,
    `period_id`, `transaction_id`, `operation_id`, `intent_id`, `identity_id`, `profile_id`
    — is resolved with the same `tenant_id = ?` predicate, and an id belonging to
    another tenant answers **404 `NOT_FOUND` with a body identical to the unknown-id body**. Never 400
    "unknown", never 403, never 500, and never a message that distinguishes "exists elsewhere" from "does
    not exist": either answer is an IDOR oracle. This binding applies to C6 (`customer_id`), C9 (`owner_id`,
    `device_id`), C16/C17 (`customer_id`, `transaction_id`, `device_id`), C19 (`period_id`), C20
    (`current_period_id`), C21/C22 (`device_id`, `identity_id`, `profile_id`), C23/C24 (`intent_id`,
    `operation_id`) and Q4/Q5/Q6/Q8/Q9/Q12/Q13 in exactly the same way as to the primary path id of Q2.
  - **`/v1` unchanged.** `/v1` stays served and untouched; routing work is confined to the `/v2` namespace.
- **Authorization and transaction ownership (corrected by ruling 87).** Every Phase C call runs inside
  `AccountSessions::withWorkspace()` with its **real** signature —
  `withWorkspace(string $token, string $purpose, int $now, string $tenantId, int $expectedContextVersion,
  string $capability, callable $fn): mixed` (`host/src/AccountSessions.php:532-536`). Argument order is
  exactly that; `$now` is the **third** parameter, not the last. There is **no `$sqlOnly` parameter** and
  none is invented here: no such flag exists anywhere in the repository. `withWorkspace()` performs session
  admission and then `Workspaces::lockAuthorizedWithin()` and opens **exactly one**
  `Database::transaction()` (`AccountSessions.php:536-554`); **that transaction is owned by
  `withWorkspace()` and is never opened, re-opened, committed or rolled back by a Phase C callback.** A
  callback's entire obligation is therefore: perform only transactional DB work, and return the value the
  caller wants. "SQL-only" is an **implementation constraint on the callback body**, not a parameter — no
  password hashing, no HTTP, no file I/O, no Telegram, no network, no robot call inside it (§5.2), and no
  nested `Database::transaction()`. The capability vocabulary is closed:
  `read`, `edit` (both roles) and `delete`, `settings`, `period_admin` (admin only) —
  `Workspaces::MEMBER_CAPABILITIES` / `ADMIN_CAPABILITIES` (`Workspaces.php:62-70`), checked by
  `Workspaces::allows()` (`Workspaces.php:106-115`). **Phase C reuses that vocabulary and does not invent
  new capabilities.** `Roles.php` stays the `/v1` matrix and is not modified.
- **Capability mapping used throughout §4:** `read` for Q1–Q14; `edit` for create/update and for every
  stage/ledger/owner/identity command; `delete` for C3, C4-delete, C5-delete, C8; `settings` for C20;
  `period_admin` for C18 and C19. C21–C25 use the capability of the underlying domain action — C23 in
  particular **never** hard-codes `edit`, because an intent wrapping a `delete`/`settings`/`period_admin`
  command must be creatable only with that command's own capability (round-2, M6).
- **Tenant scope.** `$tenantId` is chosen server-side from the admitted workspace context, never from a
  client body field. A client-supplied workspace id grants no authority (global constraint 6).
- **Row identity and versions.** Every business row carries `id`, `tenant_id`, `row_version`, `created_at`,
  `updated_at`, `deleted_at`, `deleted_by` (verified in `001_initial.sql` for `periods`, `settings`,
  `customers`, `owners`, `identities`, `crm_devices`, `customer_transactions`). Mutations take
  `expected_row_version` and compare **after** the row lock, mirroring
  `Workspaces::lockAuthorizedWithin()`'s expected-version pattern.
- **Actor (settled by gate B-5).** Mutations record the acting **account** id and the **exact authenticated
  session id** in Phase C's own account actor columns, and never write `audit_events.actor_user_id` or
  `change_log.actor_user_id`. `AccountPrincipal` (`host/src/AccountSessions.php:101`) stays frozen: it carries
  `displayName` and no `sessionId`, and no Phase C task edits it. The session id is obtained **inside the
  mutation transaction's locked callback**, read from that exact authenticated session row of the admitted
  session; a worker may not select an arbitrary session by account or tenant, and may not leave the session
  id silently null on an authenticated mutation. A path that cannot identify the exact session is an explicit
  contract gap: stop and escalate (§9.3).
- **Credential redaction (settled by gate B-6).** No `/v2` response, error body or log line may carry a
  credential. Responses are produced from **explicit safe-field allowlists**, never by copying a stored row:
  Q10 returns only the allowlisted settings fields (§4.1 Q10), and C20 accepts only the same allowlist —
  any credential-class field is **rejected**, never partially applied, and the stored value is preserved
  unchanged. Secrets never appear in error messages, `Log` lines or test output.
- **Currency and digits.** All amounts are signed 64-bit **integer Toman**. Digit normalization uses the
  `crm.normalize_digits()` rules (`crm.py:206`); amounts are never floats or strings in storage.
- **Error surface.** Codes already declared in `host/src/Http.php` (34 in `Codes::ALL`, lines 88-102) that
  Phase C reuses: `VALIDATION_FAILED`, `NOT_FOUND`, `VERSION_CONFLICT`, `IDEMPOTENCY_MISMATCH`,
  `ROLE_FORBIDDEN`, `PERMISSION_DENIED`, `AUTH_*`, `BAD_PAYLOAD`, `UNSUPPORTED_MEDIA_TYPE`,
  `PAYLOAD_TOO_LARGE`, `RATE_LIMITED`, `SERVER_ERROR`, `DB_UNAVAILABLE`. New Phase C codes must be added
  as named constants **and** to `Codes::ALL`, and are only claimable green when `--suite api` passes
  (G5).

### 4.1 Query contracts (Q1–Q14)

All queries: `GET`, capability `read`, tenant-scoped by the admitted workspace, no mutation, no intent, no
`FOR UPDATE`. `AccountSessions` admission treats these as **passive reads that never renew idle**
(handoff §9); that behavior is inherited, not re-decided here.

**Shared pagination contract (Q1, Q3, Q5, Q7, Q8, Q9, Q14).** Query parameters `limit` (integer, default
50, minimum 1, maximum 200; out-of-range → 400 `VALIDATION_FAILED`) and `after` (opaque keyset cursor).
Responses return `{items: [...], next_after: string|null, limit: int}`. The implementation uses
`LIMIT limit+1` and derives `next_after` from the last real item — the same pattern as
`Workspaces::listWithin()` ("it never materialises a whole membership set to slice it in PHP"). Cursors are
**windows, never authority**: a cursor cannot select another tenant, and a cursor that no longer resolves
is treated as an empty page, not an error.

| ID | Inputs & validation | Outputs | Errors | Authorization & scope | Affected records |
| --- | --- | --- | --- | --- | --- |
| Q1 | `stage` ∈ `new,inquiry,problem,ready_owner,handed,to_payment,payment,payment_done,done,closed` (else 400); `customer_id`, `owner_id`, `period_id` valid UUIDs; `q` ≤ 64 chars matched against `name`/`serial1`/`serial2` with **escaped** `LIKE` (no raw `%`/`_` injection); `include_deleted` ∈ `true,false` (default `false`); `limit`, `after` | `{items:[{id,name,serial1,serial2,customer_id,owner_id,stage,period_id,row_version,updated_at,deleted_at,…}], next_after, limit}` ordered by `id` ascending (keyset) | 400 `VALIDATION_FAILED` (bad enum/uuid/limit); 401/403 as admission decides | `read`; tenant = admitted workspace | `crm_devices` (read-only) |
| Q2 | Path `id` on the parameterised route `GET /v2/crm/devices/{id}` (gate B-8); the segment must match the `Ids::v4()` shape (`host/src/Ids.php:53-66`), otherwise the router answers 404 `HTTP_404`, never a validation oracle; lookup then applies `tenant_id = ?` | Full device row + `row_version` | 404 `NOT_FOUND` for unknown **and** for another tenant's id (same body, no distinguishing signal) | `read` | `crm_devices` |
| Q3 | `q` ≤ 64 chars over `full_name`/`mobile`; `has_balance` ∈ `true,false`; `limit`, `after` | `{items:[{id,full_name,mobile,row_version,updated_at,…}], next_after, limit}` ordered by `id` asc | as Q1 | `read` | `customers` |
| Q4 | Path `id` on `GET /v2/crm/customers/{id}`, same grammar and same 404-not-400 rule as Q2 | Customer row + `row_version` + `balance` (integer Toman, `_tx_countable` predicate) | as Q2 | `read` | `customers`, `customer_transactions` (read) |
| Q5 | `from`, `to` as `YYYY-MM-DD` (else 400), inclusive, applied to `at`; `kind` ≤ 32 chars matched against stored kinds (free-form, not an enum — legacy rows carry arbitrary kinds); `include_voided` ∈ `true,false` (default `true`, matching the statement surface); `limit`, `after` keyset on `(at, seq)` | `{items:[{id,seq,at,amount,note,kind,image,device_id,voided_at,void_reason,row_version}], next_after, limit}` ordered `at` asc then `seq` asc | as Q1; 404 when the customer is unknown/foreign | `read` | `customer_transactions` |
| Q6 | Path `id` (customer) | `{customer_id, balance}` — `balance` = Σ `amount` over rows satisfying `_tx_countable` (`crm.py:673-697`: not `voided_at`, `kind != 'void'`, and `at[:10]` matches the fixed-width date shape) | 404 as Q2 | `read` | `customer_transactions` (read) |
| Q7 | `q` ≤ 64 chars over `first_name`/`last_name`/`national`; `bound` ∈ `true,false`; `limit`, `after` | `{items:[{id,first_name,last_name,national,birth,mobile,card_no,passport,device_id,row_version}], next_after, limit}` where `device_id` is the 1:1 binding (empty when free) | as Q1 | `read` | `owners`, `crm_devices` (read) |
| Q8 | `q` ≤ 64 chars over `label`/`national`; `available` ∈ `true,false`; `limit`, `after` | `{items:[{id,label,national,birth,mobile,row_version}], next_after, limit}`; `available=true` excludes identities currently referenced by a live inquiry result. **The reference column is `inquiry_results.identity_id`** (domain migration `005_*`, §6.2.1.1): the host-owned inquiry row records which pool identity produced it, and the filter is `NOT EXISTS (SELECT 1 FROM inquiry_results r WHERE r.tenant_id = ? AND r.identity_id = identities.id AND r.status IN ('pending','running') AND r.deleted_at IS NULL)` — a **non-final, non-deleted** row reserves the identity, mirroring `crm.identity_for_stage()` (`crm.py:2696`) / `pick_pool_identity()` which draw only from `identities` rows that are not in use | as Q1 | `read` | `identities`, `inquiry_results` (read) |
| Q9 | List form `GET /v2/crm/periods` with `include_closed` ∈ `true,false` (default `true`) plus `limit`, `after` keyset-ordered `number` desc; detail form `GET /v2/crm/periods/{id}` under gate B-8 with the Q2 grammar | `{id,number,label,opened_at,opened_by,closed_at,closed_by,closed_reason,device_count,row_version}` | as Q1 for the list form; 404 for unknown/foreign on the detail form | `read` | `periods`, `crm_devices` (count) |
| Q10 | None | The **safe-field allowlist** of the stored settings object: `business_name`, `business_phone`, `watch_interval_hours`, `scheduler_enabled`, `auto_advance_default`, `telegram_report_full_data`, `registry_fee_amount`, `sound_enabled`, `current_period_id`, `links`. Credential-class fields of the `settings` row (`registration_chat_id`, `report_chat_id`, `001_initial.sql:371-372`) are **never returned** (gate B-6); the allowlist is closed, so any future secret column is excluded by default | 404 when no settings row exists for the tenant | `read` | `settings` |
| Q11 | Optional `limit` (default 200, max 500) bounding the joined device set; optional `stage` filter | Composite: `{devices:[…], inquiry_results:[…], profiles:[…], settings:{…}, periods:{…}}` — parity with the **host-owned** portion of `panel.py::crm_state_payload` (`panel.py:2887-2926`) | 400 on bad limit/stage | `read` | `crm_devices`, `inquiry_results` (domain migration `005_*`, §6.2.1.1), `profiles` (domain migration `005_*`, §6.2.1.1), `settings`, `periods` |
| Q12 | Path `id` on `GET /v2/crm/devices/{id}/invoice`, same grammar and 404-not-400 rule as Q2; tenant-scoped device lookup | `{device, owner, customer, lines:[…], totals:{…}}` mirroring `crm.device_invoice` (`crm.py:2521`) | 404 unknown/foreign device | `read` | `crm_devices`, `owners`, `customers`, `customer_transactions` |
| Q13 | Path `operation_id` valid UUID; served by the parameterised `/v2/operations/{operation_id}` route added under gate B-8 | `{operation_id, status, code, body, committed_at, digest}` from the stored operation record. This is the **only** supported resolution of a command whose outcome is UNKNOWN (§5.6) | 404 for unknown **or** foreign-tenant operation | `read` | Phase C operations table (foundation migration `004_*`, gate B-2) |
| Q14 | `state` ∈ `pending,dispatched,settled,rejected,expired`; `limit`, `after` | `{items:[{intent_id,operation_id,kind,state,created_at,dispatched_at,resolved_at,transport_id}], next_after, limit}` — **never** credential or payload material | as Q1 | `read` | Phase C intent table (foundation migration `004_*`, gate B-2) |

### 4.2 Command contracts (C1–C25)

Shared for every command unless stated: `POST`/`PATCH`/`DELETE` with `Content-Type: application/json`; body
must be a JSON object with no unknown fields; `workspace_context_version` is required and is compared under
the tenant lock; every mutation command carries `transport_id` (client-minted UUID) for C23/C25 idempotency;
`$now` is server-generated and never read from the body. Affected-record lists name every table the
transaction may write.

**Every referenced id is tenant-resolved and answers 404-foreign (round-2, I7).** The §4.0 scoped-lookup
bullet binds each row below as well as the target row: a `customer_id`, `owner_id`, `device_id`,
`transaction_id`, `period_id`, `identity_id` or `profile_id` that belongs to another tenant is
**404 `NOT_FOUND`, body-identical to unknown**, never 403, 400 or 500. The rows that previously said only
"unknown" — C6 (`customer_id`), C9 (`owner_id`), C17 (`transaction_id`, `device_id`) and C22 (`device_id`,
`profile_id`) — mean "unknown **or foreign**", and their tests assert the two bodies are byte-identical.

**Table columns for C21–C25 are not restated here.** `inquiry_results`, `profiles`, the Phase C operations
table and the Phase C intent table have their columns, keys, `tenant_id` ownership and deletion semantics in
**§6.2.1.1**; both new Phase C tables carry `tenant_id NOT NULL` and tenant-matching composite FKs under the
003 convention (round-2, M7), and Q13/Q14/C23/C24 read that contract rather than restating it.

**Caller-known `operation_id` (settled by gate B-3).** Every mutation command must also carry an
`operation_id` that the caller knows **before** execution, so a lost response never blocks lookup: the
operation record is keyed by that id, and `GET /v2/operations/{operation_id}` (Q13) resolves the durable
outcome. The exact binding between `transport_id` and `operation_id` is a bounded choice (§9.2 BC-4), but
the id must be caller-known a priori in either form — the server may not mint an id that only appears in a
response the caller may never receive.

| ID | Operation | Inputs & validation | Outputs | Errors | Auth | Affected records |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | Create customer | `full_name` (1–80), `address` (≤300), `mobile` (`09…` 11 digits, **optional** — `crm.py:266-268` only validates it when present), `notes` (≤500) — all of those are `crm.validate_customer` (`crm.py:259-272`). **`telegram_id` and `instagram_id` (≤64) are a Phase C addition (round-3, m-10), not oracle rules**: `validate_customer` contains no check on either field at all, so the ≤64 bound is this plan's own rule for two columns the host keeps; its parity tests assert the oracle's four checks and separately assert the two Phase C bounds, and never claim the oracle enforces them | `201` `{customer:{…,row_version:1}}` | 400 `VALIDATION_FAILED` (field list); 403 `ROLE_FORBIDDEN`; 409 `WORKSPACE_CONTEXT_STALE` | `edit` | `customers` (+ Phase C audit) |
| C2 | Update customer | `id`, `expected_row_version` ≥ 1, plus changed fields | `200` `{customer:{…,row_version:n+1}}` | 409 `VERSION_CONFLICT` carrying `{expected_row_version, actual_row_version}` in **that operand order**; 404 unknown/foreign; 400 validation | `edit` | `customers`, audit |
| C3 | Delete customer | `id`, `expected_row_version`, `force` (bool, default `false`) | `200` `{deleted:true}` | 400 `VALIDATION_FAILED` when active transactions exist and `force=false`; 409 stale; 404 | `delete` | `customers` (`deleted_at` + Phase C account actor column; legacy `deleted_by` stays NULL — see §3.3), audit. **No ledger row is written** — `test_delete_without_customer_books_no_refund` |
| C4 | Create/update/delete owner | `first_name`, `last_name`, `national` (exactly 10 digits **plus** the check digit — `crm.py:283-293`), `birth` (`YYYY/M/D` and age 15–99, `crm.py:295-300`), `mobile` (**required**, 11 digits starting `09` — `crm.py:301-303` applies `MOBILE_RE` with no "when present" guard, unlike C1's customer mobile), `card_no` (**optional**; when present exactly 16 digits, internal spaces stripped — `crm.py:304-309`), `passport` (**optional**; when present `[A-Za-z0-9]{1,20}`, latin letters and digits only, mixed case kept — `crm.py:313-315`) — all of `crm.validate_owner` (`crm.py:275-316`) | `201`/`200`/`200` with `row_version` | 400 validation; 404; 409 stale; 409-refusal when deleting a bound owner | create/update `edit`, delete `delete` | `owners`, audit |
| C5 | Create/update/delete identity | `national`, `birth`, `mobile` — **exactly** what `crm.validate_identity` (`crm.py:319-341`) validates, and nothing else. **`label` (≤80) is not an oracle rule (round-3, m-11)**: `validate_identity` never reads a `label` field; the ≤80 bound is the host storage column `identities.label VARCHAR(80)` (`001_initial.sql:454`), so the host enforces it as a column bound and its parity test asserts the oracle's three checks, never a fourth | `201`/`200`/`200` | 400; 404 unknown **or foreign**; 409 stale; delete refused while a non-deleted `inquiry_results.identity_id` row references it (§6.2.1) | create/update `edit`, delete `delete` | `identities`, `inquiry_results` (read for the refusal), audit |
| C6 | Create device | `name` (**required**, ≤40 — `crm.py:385-388`), `serial1` (**required**, and **exactly 15 ASCII digits** after `normalize_digits()` — `crm.py:389-395`), `serial2` (**optional; when non-empty also exactly 15 ASCII digits** — same loop, `crm.py:393-395`), plus the same-device `serial1 != serial2` rule (`crm.py:396-397`); `customer_id?`, `notes`, `auto_advance`; `require_period` (bool, default `true`). **Round-3, I-5: the bound is "exactly 15", not "≤15"** — `re.fullmatch(r"\d{15}", …)` rejects a 1–14 digit serial and a 16-digit one alike; `VARCHAR(15)` (`001_initial.sql:483-484`) is the storage width, not the rule, and a host that accepted a 9-digit serial would accept rows `crm.py` refuses, which the roadmap makes the behavioural oracle | `201` `{device:{…,stage:'new',row_version:1}}` | 400 serial-conflict / validation; 409 `NO_OPEN_PERIOD`-class refusal (code name is BC-8; declared by T1 Step 4) when no open period | `edit` | `crm_devices`, audit |
| C7 | Update device | `id`, `expected_row_version`, mutable fields only (`name`, `customer_id`, `notes`, `auto_advance`); **`serial1`/`serial2` are immutable** once created, and any attempted change is rejected with the **same** 15-digit rule stated for C6 (round-3, I-5: the immutability message never restates the length bound as `≤15`) | `200` with new `row_version` | 400 `VALIDATION_FAILED` naming the serial-immutability rule **and** the fee-reversal consequence (`test_immutable_serial_error_mentions_fee_reversal`); 409 stale; 404 | `edit` | `crm_devices`, audit |
| C8 | Delete device | `id`, `expected_row_version` | `200` with a summary of side effects | 409 stale; 404; any internal failure rolls the whole set back | `delete` | `crm_devices` (soft delete + Phase C account actor column; legacy `deleted_by` stays NULL), linked profile disable, inquiry-row removal, and `customer_transactions` **only** when a `base_fee` row exists and the refund feature is enabled (`crm.delete_device`/`_refund_registry_fee`, `crm.py:821`, `test_double_delete_cannot_double_refund`). **Parity divergence to decide before dispatch (§9.2 BC-6):** legacy `crm.delete_device` (`crm.py:1388-1411`) *hard-removes* the device row — which frees its owner, because binding lives on `crm_devices.owner_id` — while Phase C tombstones it. The owner-binding consequence of that divergence is not covered by rulings 79–81 |
| C9 | Attach owner | `device_id`, `owner_id`, `expected_row_version` | `200` `{device:{…,owner_id,stage}}` | 409 when the owner is already bound to another device (message names the binding, not the other device id — no IDOR oracle); 404 unknown owner; 409 stale | `edit` | `crm_devices.owner_id`, audit |
| C10 | Detach owner | `device_id`, `expected_row_version` | `200` `{device:{…,owner_id:null}}` | 409 stale; 404; refusal when the action is not in `ALLOWED[stage]` | `edit` | `crm_devices.owner_id`, audit |
| C11 | Stage `advance` | `device_id`, `action` ∈ the `ALLOWED` map (`crm.py:108-150`), whose distinct action vocabulary is **twelve** names: `queue_inquiry`, `mark_problem`, `mark_ready`, `close_case`, `requeue_inquiry`, `detach_owner`, `attach_owner`, `handover`, `to_payment`, `start_payment`, `confirm_payment`, `deliver_code`. C11 owns the **ten** that are not `attach_owner`/`detach_owner` — those two are C9/C10 and are rejected on this route by name — plus `expected_row_version` and action-specific payload (`reason` for `close_case`) | `200` `{device:{…,stage,timestamps}}` | 409 refusal "this action is not permitted from the current stage" (structured, English, carries `{stage, action}`); 409 stale; 400 missing required payload; 404 | `edit` | `crm_devices`, audit |
| C12 | Stage `stage_back` | `device_id`, `expected_row_version` | `200` `{device:{…,stage,cleared_timestamps:[…]}}` | 409 when `_STAGE_BACK` has no entry (`done`→ok, `closed`→refusal, `new`→refusal); 409 stale | `edit` | `crm_devices` (`stage`, cleared stamps, `done_at`), audit |
| C13 | Stage `stage_skip` | `device_id`, `expected_row_version` | `200` `{device:{…,stage,skipped_stages:[…]}}` | 409 when no `_STAGE_SKIP_NEXT` entry; **any attempt whose target is `done` is refused**; 409 stale | `edit` | `crm_devices` (`stage`, `skipped_stages`, stamp), audit |
| C14 | Stage gates (cross-cutting, enforced inside C11) | — | — | `handover` without a bound resolvable owner → 400-class refusal; `mark_ready` when either inquiry has not passed → refusal naming the failing service (English equivalent of `_inquiry_gate_errors`); repeated `handover`/`confirm_payment`/`deliver_code` when the target stamp is already set → refusal "already confirmed", **not** a silent success | `edit` | `crm_devices`, audit |
| C15 | `close_case` | `device_id`, `reason` (non-empty, ≤1000), `expected_row_version` | `200` `{device:{…,stage:'closed',closed_at,closed_reason}}` | 400 when `reason` empty; 409 when already `closed`/`done`; 409 stale | `edit` | `crm_devices`, audit |
| C16 | Add ledger transaction | `customer_id`, `amount` (integer Toman, signed, digit-normalized), `note` (≤1000), `kind` (≤32), `image` (≤64), `device_id?` (same tenant) | `201` `{transaction:{…,seq}}` where `seq` is the next per-customer sequence | 400 non-integer/overflow; 404 unknown customer/device; unique-key collision on `(tenant_id, customer_id, seq)` is retried **only** inside the same bounded transaction, never at HTTP level | `edit` | `customer_transactions`, audit |
| C17 | Void ledger transaction | `customer_id`, `transaction_id` (**id, not array index** — Phase C addresses rows by stable id), `reason` (≤1000), `expected_row_version` | `200` `{original:{…voided}, reversal:{…}}` | 409 refusal when already voided; refusal when the row is itself a `void` reversal; **half-pair guard**: refuse voiding one side of an active `base_fee`/`base_fee_refund` pair on the same `device_id` (`crm.py:748-780`); 409 stale | `edit` | `customer_transactions` (2 rows), audit |
| C18 | Open period | `label` (≤120, optional) | `201` `{period:{…,number}}` with `number = max(number)+1` per tenant | 409 refusal when a period is already open; unique-key collision on `(tenant_id, number)` resolved by re-reading under the tenant lock, never by silent increment loops | `period_admin` | `periods`, audit |
| C19 | Close period | `period_id`, `reason` (≤1000), `expected_row_version` | `200` `{period:{…,closed_at,closed_reason}}` | 409 when already closed; 404; 409 stale | `period_admin` | `periods`, audit |
| C20 | Update settings | Any bounded subset of the **same safe-field allowlist Q10 returns** (gate B-6): `business_name`, `business_phone`, `watch_interval_hours`, `scheduler_enabled`, `auto_advance_default`, `telegram_report_full_data`, `registry_fee_amount`, `sound_enabled`, `current_period_id`, `links`. A credential-class field (`registration_chat_id`, `report_chat_id`, or any unknown field) is **rejected outright** — never partially applied — and the stored value is preserved. `current_period_id` must be a same-tenant, non-deleted period; `links` array **shape-checked by a Phase C rule**, not by the oracle; `watch_interval_hours` ∈ 1–72; numeric flags ∈ {0,1}. **Round-3, m-12 — the `links` rejection is a documented divergence, not oracle parity.** `_normalise_settings` (`crm.py:424-447`) **normalises and falls back**: it keeps a present list (even an empty one) after dropping non-dict entries and entries whose `url` is not a string, and substitutes `_default_links()` for a missing or non-list key — it **never raises, never rejects and never returns an error**. C20's 400 on a malformed `links` entry is therefore this plan's own strictness on a host-owned settings column; its test asserts the Phase C rule and additionally asserts that the oracle's lenient normalisation is not silently reimplemented (a dropped entry must not be silently rewritten into a stored default) | `200` `{settings:{…,row_version}}` carrying only the allowlisted fields | 400 validation **and** 400 on any credential-class field; 409 stale; 404 | `settings` | `settings`, audit |
| C21 | Record inquiry result | `device_id`, `service` ∈ the **closed** PHP set `{'gomrok','hamta'}` — the two channels `_inquiry_gate_errors` reads (`crm.py:1496-1521`); `status` ∈ the **closed** PHP set mirroring `panel.py:_ESTELAM_FINAL` (`panel.py:3672-3673`) plus the in-flight values `pending` and `running`; `result` (≤5000); optional timestamps. Gate B-9: the accepted sets are pinned by a parity test that fails when either Python source set changes, and **any** value outside the closed sets is 400 `VALIDATION_FAILED` with no silent fallback, no defaulting and no case-folding | `201` `{inquiry_result:{…}}` | 400 unknown vocabulary value (field-named); 404 unknown device; **no** network call, **no** robot invocation, ever | `edit` | `inquiry_results` (domain migration `005_*`, §6.2.1.1), audit |
| C22 | Create / link / unlink profile | `profile_id` (bounded 64-char), `device_id`, bounded profile fields | `200`/`201` with `row_version` | 404 unknown device; 409 stale; unlink disables the detached profile (`crm._disable_detached_profile`, `crm.py:1425`) and does not delete it | `edit` | `crm_devices.profile_id`, `profiles` (domain migration `005_*`, §6.2.1.1), audit |
| C23 | Create durable intent | `transport_id` (client UUID), `operation_id` (client-known before execution, gate B-3), `kind` (bounded: `finance` / `job`), `payload_digest` (SHA-256 of the canonical request), `command_name` and `command_version` | `201` `{intent_id, state:'pending', operation_id}` — written **inside the same transaction as the domain write**, before any external effect | 409 `IDEMPOTENCY_MISMATCH` when the same `transport_id` arrives with a different digest; duplicate same-digest → the existing intent | `edit` | Phase C intent table (foundation migration `004_*`, gate B-2), operations table, audit |
| C24 | Resolve intent | `intent_id`, `outcome` ∈ `settled`,`rejected`, `evidence` (bounded, sanitized) | `200` `{intent_id, state}` | 409 when the intent is already terminal; 409 when the transition is not in the state machine (`pending` may go to `dispatched` or `rejected`; `dispatched` may go to `settled`/`rejected`/`expired`; `settled` is terminal) | `edit` | intent table, audit |
| C25 | Operation commit + recovery | `operation_id` (client-known before execution), `transport_id`, canonical request digest, committed response body, authorization scope, classified pre-commit failure when the command did not commit | `200` replay of the stored body for a known matching digest; the original command's status for the first execution. The outcome row is written **atomically with the business mutation** | 409 `IDEMPOTENCY_MISMATCH` on same id + different digest; unknown id → treated as a new command, never as a replay | capability of the underlying command | Phase C operations table (foundation migration `004_*`, gate B-2), audit |

Five conditions bind **every** command row above, and a reviewer can check each one against the table without
reading the implementation:

1. **One transaction, one operation record** (§5.2, §6.4 **steps 5 and 8** — step 5 takes the operations
   row lock and makes its insert the first write; step 8 writes the committed response body back onto that
   row before the transaction ends; round-3, m-6). The domain write, the audit write and the
   operation record commit together; the external effect, if any, runs only after commit.
2. **Caller-known `operation_id`** on every mutation (gate B-3), so a lost response never blocks Q13.
3. **Acting account and exact session id** in Phase C's own actor columns (gate B-5); never a legacy
   `actor_user_id` or `deleted_by`. This condition governs the **completed** Phase C implementation — every
   command, with no exemption left standing — not the intermediate pre-005 schema state. It is **not**
   weakened for T4: T4 may omit these columns only while implementing against the pre-005 schema, that
   exemption is temporary, T9b owns the retrofit and the exemption **expires at T9b completion** (ruling 96).
   Phase C is not accepted or released while any command still omits them.
4. **No credential in, none out** (gate B-6): commands accept the closed safe-field allowlist only and their
   responses are built from it.
5. **Nothing Phase C writes reaches `/v1`** (gate B-4, global constraint 5), proven in T10 by both the
   incremental cursor and the snapshot/bootstrap read.

## 5. Integrity, transactions and recovery

### 5.1 Business invariants (enforced in domain code, not by `CHECK` alone)

Global constraint 10: MySQL/MariaDB `CHECK` support is not uniform, so every invariant below is enforced by
validated domain code **and** (where the engine already provides it) a unique/FK/`CHECK` backstop. The
backstop is never the only proof.

| # | Invariant | Enforcement point |
| --- | --- | --- |
| I1 | A device's `serial1` is unique per tenant and, when non-empty, `serial2` is unique per tenant **across both slots** — `crm.py::_find_serial_conflict` (`crm.py:343-356`) scans `serial1` *and* `serial2` of every other device, so a `serial1` colliding with another device's `serial2` is a conflict, and `validate_device` additionally rejects `serial1 == serial2` on the same device (`crm.py:396-397`) | Gate B-10: **domain check under the tenant lock** (actionable, names the conflicting device and customer) **plus** a DB constraint. Because I1 is cross-slot, two independent column indexes are **not sufficient** — the domain migration `005_*` adds a single-unique-index slot ledger: `device_serial_slots (tenant_id, serial, device_id, slot)` with `UNIQUE (tenant_id, serial)`, written and tombstoned **in the same transaction** as the device row, which makes every cross-slot collision a DB error. Tombstone interaction is handled: `001_initial.sql:470-471` documents that a serial correction is delete-and-recreate, so the slot ledger holds only **non-deleted** devices and never a tombstoned one. The pre-existing `KEY ix_crm_devices_serial1 (tenant_id, serial1)` (`001_initial.sql:516`) is non-unique and is left untouched |
| I2 | An owner is bound to at most one device (1:1) | Domain check under the device-row lock, **plus** a DB unique constraint added by the domain migration `005_*` (gate B-10). Verified gap at `f183134`: `owners` has no `device_id` column (`001_initial.sql:418-443`) and `crm_devices.owner_id` is only a non-unique `KEY` (`001_initial.sql:517`); 003 adds the scoped FK `fk_crm_devices_owner_scoped` (`003_scoped_business_keys.sql:86-88`) but **no** unique. The domain migration therefore adds a unique key covering active bindings only, and pre-migration detection reports any pre-existing double binding before the constraint is installed |
| I3 | `stage` ∈ the closed vocabulary; `problem` is off-mainline; `closed` and `done` are terminal for stage advance | Domain check on every stage command; the vocabulary is read from one table in `CrmCommands`, never re-typed |
| I4 | Only `deliver_code` may produce `done`; no `stage_skip` path reaches `done` | `_STAGE_SKIP_NEXT` shape assertion plus a dedicated refusal test |
| I5 | `handover` requires an attached, resolvable owner; `mark_ready` requires both inquiries passed for that device | Domain gate inside the same transaction as the stage write |
| I6 | Ledger rows are append-only; a void writes exactly one reversal row and stamps the original; a void is never applied twice; a `void` row is never voided | Row lock on the target transaction + re-check inside the lock (`crm.void_transaction` semantics) |
| I7 | Active-ledger predicate (`_tx_countable`) is the single balance rule on every surface: API balance, statement, invoice, finance report | One shared helper in `LedgerCommands`; tests assert the API balance equals `crm.customer_balance` on the same fixture |
| I8 | `base_fee` and `base_fee_refund` for the same `device_id` void as a pair or not at all | Half-pair guard under the customer's ledger lock |
| I9 | Per-customer `seq` is monotone and unique within the tenant | Unique `(tenant_id, customer_id, seq)`; allocation under the customer row lock, never `MAX()+1` without the lock |
| I10 | Period `number` is monotone per tenant; at most one open period | Lock the tenant row, then read `MAX(number)` and the open period inside that lock |
| I11 | A deleted parent cannot be re-pointed at; a soft-deleted row is never returned by a non-`include_deleted` query | `deleted_at IS NULL` predicate in every query helper |
| I12 | No new account id is ever written into a legacy `actor_user_id` FK | Dedicated negative test per mutation family (see Task 6) |
| I13 | No Phase C write populates a legacy `deleted_by` foreign key with an account id, and no credential-class field ever crosses the `/v2` surface | `deleted_by` on every business table references legacy `users(id)` (`001_initial.sql:118,157,343,382,410,440,464,523,559`), so Phase C soft deletes set `deleted_at` plus the new account actor column and leave `deleted_by` NULL. Credential redaction (gate B-6) is proven by a negative test asserting `registration_chat_id` / `report_chat_id` never appear in a Q10 body, a C20 body, an error body or a `Log` line |

### 5.2 Transaction boundaries

- Every command runs inside **exactly one** `Database::transaction()` callback (`Database.php:376`), opened
  by `AccountSessions::withWorkspace()`. Nested `transaction()` calls are forbidden (Phase B interface
  contract).
- Callbacks are **SQL-only**: no password hashing, no HTTP, no file I/O, no Telegram, no network, no robot
  call. `Database.php:376-385` retries the whole callback, so anything non-idempotent inside it is a defect.
- One command = one transaction = one operation record. A command that would need two transactions must be
  decomposed into a command plus a durable intent (C23) with an explicit recovery path (C24).
- Read queries (Q1–Q14) use the same admission transaction but take no row locks; they must not write.
- Rollback is all-or-nothing: C8 (device delete with profile disable, inquiry removal and conditional fee
  refund) either applies every listed effect or none. A test asserts the ledger is byte-identical after a
  forced mid-transaction failure.

### 5.3 Uniqueness and ownership enforcement

- Same-tenant composite parent keys and scoped FKs added by `003_scoped_business_keys.sql` are the
  ownership backstop. Phase C must **not** weaken them, and must not rely on the ID-only parent FKs.
- Ownership is proven twice for every mutation: once by `Workspaces::lockAuthorizedWithin()` (account →
  tenant → membership) and once by an explicit `tenant_id = ?` predicate on the business row read under
  `FOR UPDATE`. A row that passes one and fails the other is a `NOT_FOUND`, never a 500.
- Cross-tenant probing is tested with **direct fixture SQL** as well as API requests, the Phase B rule.
- **Gate B-10 — pre-migration detection lives in `Migrations.php` under T9a (round-2, I4).** The repository
  has **no** generic counts-only blocking hook: `Migrations::apply()` consults
  `assertScopedReferencesSafe()` only when `$version === self::SCOPED_VERSION` (`host/src/Migrations.php:198-199`;
  the method itself is at `:267` and is hard-wired to 003's reference set), and
  `MigrationPreflight::scopedIntegrity()` (`host/src/MigrationPreflight.php:684`) only walks
  `Schema::scopedRelationships()`. Both files are Phase B files, so the domain migration `005_*` cannot
  silently rely on either.
  Resolution: **T9a is the sole Phase C owner of `host/src/Migrations.php` and
  `host/src/MigrationPreflight.php`**, and may make **one additive change only** — a per-version
  preflight dispatch mirroring the existing 003 hook shape (a `$version === <005 version>` branch in
  `apply()` plus a private `assert<005>Safe()`), with 005's detector reporting **counts only** for (a)
  active devices sharing a serial across slots within a tenant, (b) devices whose own `serial1` equals their
  `serial2`, (c) owners bound to more than one active device, and (d) non-empty `crm_devices.profile_id`
  values that have no matching `profiles` row (advisory, see below). A non-zero blocking count **stops that
  step before any DDL of it runs** — no repair, no deletion, no truncation, and an actionable counts-only
  report. No existing method signature, journal rule, checksum rule, advisory-lock rule, crash-reconciliation
  rule or 003 behaviour may change; T9a Step 4 proves that by re-running `--suite migrations` and
  `--suite integrity` unchanged. Any other proposed edit to either file is a §9.3(1) stop-and-escalate.
- **`crm_devices.profile_id` stays dangling by design — no FK over it (round-2, C2).** Migration 001
  documents `crm_devices.profile_id` as "points at profiles.json -> local only" (`001_initial.sql:50`) and
  "dangling by design" (`:469`); the column is `VARCHAR(64) NOT NULL DEFAULT ''` (`:494`). The domain migration
  therefore **adds no foreign key over that column**. Instead the domain migration `005_*` adds the
  host-owned `profiles` table with
  `UNIQUE (tenant_id, id)`, and a profile id becomes *host-owned* only when a matching row exists; a
  `profile_id` with no matching row is a legitimate local-only reference, reported by the advisory count (d)
  above and never blocking. Deletion of a `profiles` row is **soft** (`deleted_at` + Phase C account actor
  column) and is refused with 409 while any non-deleted `crm_devices.profile_id` in the same tenant points at
  it.
- **Gate B-10 — duplicate-key races.** When the slot ledger or the ownership unique key rejects a concurrent
  write, the command surfaces a 409 (owner-already-bound / serial-already-registered class, §4.0 error
  surface) rather than a 500. The race path is tested with two connections. Domain checks stay in front so
  the actionable message names the conflict; the constraint is the backstop, never the only proof
  (global constraint 10).

### 5.4 Concurrency and stale writes

- Linearization point for a mutation: the row lock on the target business row, taken after the
  account/tenant/membership gates.
- `expected_row_version` is compared **after** the lock. Mismatch → 409 `VERSION_CONFLICT` with both
  operands in the order `{expected, actual}` (the corrected `Workspaces::stale()` order verified at
  `Workspaces.php:151-156`).
- Two-connection fixtures are mandatory for: concurrent void of the same ledger row (exactly one wins),
  concurrent stage tick to the same target (exactly one wins), concurrent period open (exactly one wins),
  concurrent device delete vs. fee refund, concurrent create-vs-delete of the same owner, and — round-2, I5 —
  **two concurrent requests carrying the same `transport_id` (and the same request hash) for the same
  command**, which must produce exactly one domain mutation, one operations row and one intent row, with the
  second request returning the first's stored body and never re-executing the domain write.
- **Replay is detected by a locking read, before any domain write (round-2, I5).** Under InnoDB
  REPEATABLE READ a plain `SELECT` in the second concurrent request can miss the first request's committed
  row, so replay detection must not be a snapshot read. **Round-3, I-8 — the honest statement of that
  rationale.** In the two-request scenario §6.4 actually builds, the tenant-row lock at step 3
  (`Workspaces.php:289`) already serialises the two same-tenant requests, so the second request's first
  consistent read happens *after* the first has committed and a plain `SELECT` would in practice see the row;
  the miss described above is therefore **defence in depth, not an observable race in that scenario**. §5.4
  keeps the mandated `FOR UPDATE` — it is correct and cheap, and it is what makes the guarantee hold when the
  tenant gate is absent or the ids differ — but no test may cite "the second request blocked" as proof of
  *this* lock, and T9b's evidence isolates the operations lock as its Step 1 states. The mandated sequence inside the one command
  transaction is: (1) after the account/session/tenant/membership gates and **before** touching any business
  row, `SELECT ... FOR UPDATE` on the operations row keyed by `(tenant_id, operation_id)` — a missing row
  means first execution and the insert of that row (with its unique key) is the **first** write of the
  command, so a duplicate-key loss rolls the whole command back rather than double-applying it; (2) the
  request hash is compared **after** that lock, and a mismatch answers 409 `IDEMPOTENCY_MISMATCH` from
  inside the lock; (3) only then are the domain rows locked in §6.4 **step-6** order and the mutation applied
  (round-4, F4: the business-row lock order is step 6, not step 5 — step 5 *is* the operations row);
  (4) the intent row (C23) is written under the same operations-row lock so `UNIQUE (tenant_id,
  transport_id)` is also a first-write backstop. This makes the operations row the **linearization point**
  for replay, ahead of the business row lock.
- **The claim insert and the operations table's `NOT NULL` outcome columns (round-4, F3 — DDL and API
  must agree).** §6.2.1.1 declares `status VARCHAR(16) NOT NULL`, `http_status INT NOT NULL`,
  `response MEDIUMTEXT NOT NULL` and `committed_at DATETIME NOT NULL`, but the claim row is inserted
  **before** the domain mutation exists, so none of the four is known yet. The resolution this plan fixes is
  a **partial-row claim with declared pending defaults, completed by a same-transaction write-back** — not
  nullable columns, because Q13 (§5.6(5)) must be able to rely on all four being present for every row a
  caller can ever observe: (a) `Operations::claimWithin()` inserts with the declared defaults
  `status = 'pending'`, `http_status = 0` (deliberately outside the HTTP status range, so an accidental
  leak is detectable rather than plausible), `response = ''`, `committed_at = <claim timestamp>`, and
  `intent_id` / `failure_class` `NULL` — those two are genuinely nullable in the DDL; (b)
  `Operations::recordWithin()` overwrites the outcome columns with the committed values **inside the same
  transaction**, as §6.4 step 8 already requires, so the row is complete before `COMMIT`. A pending default
  is therefore never observable: another connection either blocks on the step-5 locking read / unique key or
  reads only the committed state, and Q13 never sees a sentinel; (c) `COMMIT` is the only point at which the
  row becomes visible at all, so a rollback discards it with every other write of the command transaction
  (§5.2). Consequently the **classified pre-commit failure** of §5.5/§5.6 is **not** a surviving claim row —
  it is written by `Operations::recordFailure()` in a **separate, short transaction opened after the command
  transaction has rolled back** and outside every lock, with the real `status`, `http_status`, error body and
  `committed_at`. That is the single documented exception to "one command = one transaction" (§5.2), it
  writes no domain row, it is never inside a retried callback, and `test_foundation.php` asserts both halves
  of the sequence — the pending defaults present only inside the open transaction, and the completed row
  after commit.
- Lock order for business rows follows the Phase B order (§6.4). Domain rows are locked by primary key in
  ascending order; any operation locking more than one business row does so in that order.

### 5.5 Idempotency, retries and transport ambiguity

- Every mutation command carries a client-minted `transport_id` **and** a caller-known `operation_id`
  (§4.2). First execution records `{operation_id, transport_id, request_hash, status, response}` plus the
  authorization scope, intent/idempotency binding, command fingerprint (`command_name`, `command_version`)
  and durable execution state (gate B-3). A replay with the same `transport_id` **and** the same request
  hash returns the stored response without re-executing. A replay with the same id and a **different** hash
  → 409 `IDEMPOTENCY_MISMATCH`.
- **Gate B-2 (settled): new tables in the foundation migration `004_*`, mandatory (round-3, C-1).** The legacy `idempotency_keys` table
  (`001_initial.sql:248-264`) is an expiring transport-id dedup row keyed by `operation_id`
  (`PRIMARY KEY (operation_id)`) with `KEY ix_idempotency_keys_expiry (expires_at)`, a `status` CHECK of
  `('applied','conflict','rejected')` and a `response MEDIUMTEXT`. It cannot reconstruct, resume or
  reconcile a finance/job action. The foundation migration therefore adds a **dedicated intent table** carrying scoped
  intent identity, command payload + `command_version`, lifecycle state and operation linkage, and a
  **dedicated operations table** carrying the durable execution state and the committed domain result or
  classified pre-commit failure.
- **`idempotency_keys` is in ACTIVE USE and is out of Phase C's reach (round-2, R1).** The previous draft's
  claim that no writer was found was **factually wrong**. Writers exist and are live:
  `host/src/Import.php:175` inserts a row and `:224` reads `response` back by `operation_id`;
  `host/src/Sync.php:700` reads by operation/status, `:725` inserts, and `:746` runs the expiry sweep
  (`DELETE ... WHERE expires_at < ?`). Phase C therefore must not, in any task: write to, truncate, migrate,
  repurpose, re-key, re-index, change the retention or uniqueness semantics of, or add a row to
  `idempotency_keys`; nor may it route any `/v2` mutation through the Import/Sync idempotency path. Where a
  Phase C test seeds or inspects that table it does so **read-only**, and any Phase C code path that calls
  an existing Import/Sync writer (none is planned) must retain that writer's own idempotency guarantee and
  assert that the two tables' transactions compose without nesting (§5.2).
- **Key scopes of the two new tables (round-2).** Operations: `PRIMARY KEY (operation_id)` with
  `UNIQUE (tenant_id, operation_id)` and `UNIQUE (tenant_id, transport_id)` — the first is Q13's lookup key
  and the second is the replay backstop that makes §5.4's first-write insert decisive. Intents:
  `PRIMARY KEY (intent_id)` with `UNIQUE (tenant_id, transport_id)` and a nullable
  `UNIQUE (tenant_id, operation_id)` link. Both carry `tenant_id` FK to `tenants(id)` through the 003
  composite-key convention, and neither shares a name, a key or a row with `idempotency_keys`.
- **Lifecycle and retention of the two new tables.** Operations rows are **append-only and never swept**:
  they are the durable outcome of record (gate B-3), so Phase C adds no expiry to them and no Phase C code
  deletes one. Intent rows keep the full `pending → dispatched → settled/rejected/expired` vocabulary; in
  Phase C only `settled`/`rejected` are settable through C24 and `expired` only by T9b's retention sweep
  (§3.1 C24). Retention, if any, is a **marked, separately-owned sweep** with a stated window and a
  never-sweep rule for any intent whose operation row is unresolved — it is never an opportunistic delete on
  an unrelated path.
- **Atomicity boundary (round-2).** The operations row, the intent row, the audit write and the domain rows
  are all written inside the **one** command transaction (§5.2), so a forced mid-transaction failure leaves
  none of them. The external effect, if any, is the only thing that happens after commit, and it is never
  inside a retried callback.
- A committed intent cannot execute twice under a new transport id (roadmap §C required contract). An
  **explicitly new** legitimate repeat ledger entry remains possible and is a distinct command with a
  distinct `transport_id` — this is a documented behavior, not a bug to "fix".
- Retries: at most **3** attempts, exponential backoff with jitter, **only** for classified transient
  failures (`DB_UNAVAILABLE`, connection-level errors). Never retry `VALIDATION_FAILED`, `NOT_FOUND`,
  `VERSION_CONFLICT`, `ROLE_FORBIDDEN`, `IDEMPOTENCY_MISMATCH`, 401/403, rate-limit refusals, CAPTCHA or
  lockout-shaped responses. Hard attempt cap is enforced in code and asserted by a test.

### 5.6 External side effects, intents and timeout-after-commit (settled: gate B-3)

Roadmap §C states: *"Finance and job actions first have durable recoverable intents"* and *"Timeout-after-
commit is unknown until operation/intent recovery resolves it."* Gate B-3 (ruling 79) settles the posture;
this plan carries it as binding design, not as an open item.

1. Any command whose completion implies an external effect writes a durable intent **inside the same
   transaction as the domain write** (C23), then performs the external effect **after** commit, outside the
   transaction.
2. The external effect is **never** performed inside a retried transaction (§5.2).
3. The operation record is written **atomically with the business mutation** and carries: the caller-known
   `operation_id`, the authorization scope under which the command ran, the intent/idempotency binding, the
   command fingerprint (`command_name` + `command_version`), the durable execution state, and either the
   committed domain result / effect references or a **classified pre-commit failure**. Nothing about the
   outcome exists only in the HTTP response.
4. **A timeout is UNKNOWN, not failure (gate B-3).** When the connection is lost after commit, the system
   records the committed outcome and says nothing about delivery: the command layer never claims whether a
   response was delivered, and **invents no HTTP response representation** for that condition. No status
   code, error code or body shape for "delivered-vs-not" is defined by this plan, and no worker may invent
   one.
5. **`GET /v2/operations/{operation_id}` (Q13) is the single supported resolution.** It returns the durable
   domain outcome for an id owned by the caller's tenant: committed result/effect references, or the
   classified pre-commit failure, plus the stored status and digest. Unknown or foreign id → 404. A caller
   that cannot reach Q13 simply keeps the outcome UNKNOWN; it never re-issues a new command to find out.
6. **Async execution must durably link its recoverable intent.** When a command hands work to a later
   continuation, the intent row and the operation row cross-reference each other in the same transaction as
   the domain write, so a resume or reject decision can be made from local state alone — never from
   transport memory.
7. No Phase E robot/job adapter exists in this checkout. Phase C therefore implements the intent **state
   machine and storage only**; it never dispatches a real external effect. C24 accepts a locally observed
   outcome and refuses automatic repeat of an uncertain action.

**Consequence for task sequencing (ruling 80).** Because the HTTP-facing layer no longer has to invent a
timeout representation, every command in §3.1 is dispatchable in Phase C; there is no deferred
"HTTP-facing layer" left hanging. What remains release-gated is production-entrypoint coverage (G1) and the
edge access log (M1), not command implementation.

## 6. Task decomposition

### 6.1 Scope allocation and shared-file ownership

**Phase C includes:** paginated `/v2` CRM reads, transactional CRM/stage/owner/period/ledger/profile/inquiry
commands, durable intents and operation recovery, the account actor columns for Phase C mutation audit, and
the API-contract gates that prove all of it against the fixture DB.

**Phase C excludes (owned elsewhere):** operator administration UI, cookies/CSRF, package imports, sealed
staging, canonical images, backups (D); RAM-only broker, desktop context, worker IPC, robot adapters, job
schedules, Telegram, panel preservation (E); host rehearsal, cutover, `/v1` retirement, release (F).

**Hard serialization rule.** Two workers never edit the same file concurrently. Shared-file owners:

| File | Owner task(s) | Rule |
| --- | --- | --- |
| `host/src/V2Api.php` | T1 (dispatch mechanism, strict request boundary, `logLabel` for the five Phase B routes), T10 (Phase C route registration **and** `logLabel`/`knownRoute` extension for parameterised Phase C routes) | T2–T9 are **forbidden** to edit it. They publish handler entry points; only T10 wires them. T1 and T10 never edit it in parallel — T1 lands first, T10 after. |
| `host/src/Http.php` (error codes + `Codes::ALL`) | **T1 adds**, **T10 reconciles** | Mirrors the `V2Api.php` rule: T1 owns the declared Phase C code set; T10 may only reconcile a code discovered in T2–T9 by adding the constant **and** its `Codes::ALL` entry together, and must re-run `--suite api` (G5). No other task adds codes, and a task needing an undeclared code stops (§9.3(3)). |
| `host/public/index.php` | T1 | G1 binding test and the M1 access-log assertion land here; nobody else touches it. |
| `lab/scripts/run_host_v2_tests.py` (`SUITES`, `_INTEGRATION`, `suite_path`) | **T1 registers every Phase C name once**, together with T1's own stub `test_<suite>.php` files | Round-2, C1. Registration and implementation are sequenced so the tree is runnable at **every** step: T1 adds the names **and** the stub files in the same change, and each owning task then replaces its stub's content. No task ever adds a name whose file is absent, and no task removes a name. |
| `lab/tests/test_host_v2_fixture.py` (`expected` suite map, `test_suite_bindings_are_exact_…`) | **T1** | Same change as the `SUITES` edit above: T1 adds all eight Phase C paths to `expected` — the seven stub paths plus `foundation` → `test_foundation.php` (round-4, F1) — in the same commit as the `SUITES` entries and the stub files, because `suite_path()` derives the path from the suite name and the file must exist for the map to be truthful. T2–T9 **never** edit this file (round-2, C1). |
| `lab/fixtures/host_v2/test_foundation.php` | **T1** | Round-3, C-1 (ruling 91). T1's own foundation-gate suite: it is **not** a stub and is never handed off. Its content asserts that the foundation migration applies, that the operations/intent keys of §6.2.1.1 exist, and that the §5.4 claim sequence holds — the tested foundation gate. |
| `lab/fixtures/host_v2/test_<suite>.php` (one file per Phase C suite) | **created empty-but-green by T1**, then owned by its task | Round-2, C1; round-3, m-1. T1 creates each file with a single `t(true, …)`-style assertion that makes the suite **exit 0** and nothing else, so `--suite all` is exit 0 from T1 onward. The `FIXTURE_*_PASS` label is **printed by `run_host_v2_tests.py:389-391` from that exit status**, not by the file — a stub that prints such a line itself produces dead output (integration-suite stdout is discarded, `:281-282`) and is not evidence. Ownership of the file's **content** transfers to its owning task at dispatch of that task, which replaces the placeholder with the real suite. An empty *passing* placeholder is permitted **only** in this one place and only before the owning task runs; it is never counted as evidence for any §4 contract. |
| `lab/fixtures/host_v2/test_workspaces.php` (`TENANT_CHILDREN` at `:105-113`, used by the tenant cleanup at `:369-372`) | T9a | Round-2, M9. The **domain** migration adds tenant-scoped tables, and the fixture delete order is hard-coded, not derived. T9a extends `TENANT_CHILDREN` with those tables in correct FK order (children before parents) in the same change that adds them, and asserts the list still deletes a seeded tenant cleanly. The foundation migration's two tables are added to this list by **T1**, in the foundation-gate step, because they exist from T1 onward — T1 owns that edit, T9a owns the rest, and the two edits are sequential on the backbone. |
| `lab/tests/test_host_v2_api.py` | T1 (foundation cases), T10 (HTTP contract cases) | Never in parallel. |
| `lab/fixtures/host_v2/bootstrap.php` (`BFixture`) | T1 (shared seed helpers), T10 (acceptance helpers) | T2–T9 add suite-local seeding inside their own `test_*.php`; they do not edit `bootstrap.php`. |
| `lab/fixtures/host_v2_front.php` | **nobody in Phase C** | G2 guard surface. It delegates classification (`V2Api::logLabel`, `V2Api::isVersion2Request`) to `V2Api.php` (`:71-75`) and calls `V2Api::dispatch` directly (`:84`) with `assertTransport(..., fixture: true)` (`:82`). Any edit escalates (§9.3(1)); the G1 production-entrypoint harness of §6.2.1.2 works **around** it, never by modifying it. |
| `lab/fixtures/host_v2/test_migrations.php`, `test_integrity.php` | **T9a**, with **one narrow T1 exception** | T9a is sole owner for every Phase C migration assertion; other tasks must not add migration cases. **Round-3, C-1 exception:** T1's foundation migration would otherwise break two hard-coded three-file expectations — `$verifiedSteps === 27` and the per-file step inventory `{'001_initial' => 1, '002_identity_foundation' => 16, '003_scoped_business_keys' => 10}` at `test_migrations.php:551-568`, and `Schema::migrationSteps($version)` must declare the new version's steps (`host/src/Schema.php:1546`) — so **T1 owns exactly that inventory extension for `004_*`** (the version's step count plus the matching new total) and nothing else in the file; **T9a owns the same extension for `005_*`** and every other case. Neither edit removes an existing entry, and both are verified by re-running `--suite migrations`. |
| `host/src/CrmCommands.php` | T4 (create) → T5 → T7 → T8 (extend) | Strict chain; these four never overlap. |
| `host/src/LedgerCommands.php` | **T6** | Sole owner. T2's Q5/Q6 **call into** it for `_tx_countable` and the sum; T2 never re-implements the predicate and never edits this file (round-2, C3). |
| `host/src/Schema.php` | **T1** (the operations/intent tables) then **T9a** (the domain tables) | Round-3, C-1 (ruling 91): two additive edits, strictly sequenced on the backbone — T1 registers the foundation tables **in the same change** as the foundation migration, T9a adds the domain tables afterwards. Neither may remove or retype the other's entry, neither edits it while the other task is live, and every other task is forbidden to touch it. |
| `host/migrations/<F>_*.sql` — the **foundation migration** (first new file after `003_scoped_business_keys.sql`, expected `004_*`) | **T1** | Round-3, C-1 (ruling 91). Contains **only** the Phase C operations table, the Phase C intent table and the parent keys those two need. Every object it creates is brand new and empty, so it needs **no** pre-migration detection and no edit to `Migrations.php`. T1 applies and tests it in the foundation gate before any command task is dispatched. |
| `host/migrations/<D>_*.sql` — the **domain migration** (next free number after the foundation migration, expected `005_*`) | **T9a** | Sole owner of every other Phase C DDL object: the Phase C account actor columns, the I1 serial slot ledger, the I2 active-ownership unique key, the `profiles` and `inquiry_results` tables, and the counts-only pre-migration detector for the objects that constrain **pre-existing** rows. The column-level contract is §6.2.1.1. |
| `host/src/Operations.php` | **T1** | Round-3, C-1 (ruling 91). Sole owner. T1 creates it with the claim / write-back / replay / lookup / failure-recording machinery of §6.3 and the one-transaction contract of §5.4 (including the `NOT NULL` pending-default resolution, round-4, F3), so T4–T8 inherit a working operations row and never have to create one. T9b **calls** it and never edits it; only `CommandIntents.php` is T9b's. |
| `host/src/CommandIntents.php` | **T9b** | Sole owner. The intent state machine and storage, C23/C24 and Q14 — the integration and reconciliation half that ruling 91 keeps in T9b. |
| `host/src/Migrations.php`, `host/src/MigrationPreflight.php` | **T9a** | Round-2, I4; round-3, m-9. These are **Phase B deliverables** (`docs/superpowers/plans/2026-10-11-host-identity-integrity.md:98-99, 236, 299`) that §6.1 previously neither owned nor forbade; T9a now owns them with **one additive change** — the **domain** migration's counts-only pre-migration detector (§5.3) — and nothing else. The acceptance test is behavioural, not textual: every existing `migrations`/`integrity` case still passes and **no existing method signature changes**. PHP behaviour is not byte-comparable, so the previous "byte-for-byte equivalent" wording is struck. Any other proposed edit is a §9.3(1) stop-and-escalate. **T1 must not edit either file**: the foundation migration needs no detector at all. |
| `host/src/Sync.php`, `Import.php`, `Auth.php`, `Roles.php`, `AccountSessions.php`, `Accounts.php`, `Workspaces.php`, `Tokens.php`, `Ids.php`, `Database.php`, `Config.php`, `Log.php` | **nobody in Phase C** | Phase B surfaces. A task that believes it must edit one stops and escalates. `Tokens::hashOpaque()` and `Ids::v4()` are **called**, never modified. |
| `host/migrations/001_initial.sql`, `002_identity_foundation.sql`, `003_scoped_business_keys.sql`, `gomrok.py` | **nobody, ever** | Byte-identity pins (global constraints 1, 11). |

### 6.2 File structure (planned symbols)

Roadmap §C names the Phase C files; the last column records ownership.

| File | Responsibility | Owner |
| --- | --- | --- |
| `host/src/CrmQueries.php` | Q1–Q12 read services: keyset pagination helper, filter validation, tenant-scoped SELECTs, composite state read. Q5/Q6 **delegate** to `LedgerCommands` for the predicate and the sum — one implementation of `_tx_countable`, never two | T2 |
| `host/src/CrmCommands.php` | Customers/owners/identities/devices/owner-binding/stage/period/settings command services and the shared row-version check | T4 (created), T5, T7, T8 |
| `host/src/LedgerCommands.php` | C16/C17, the **single** `_tx_countable` parity helper, statement/balance reads used by Q5/Q6 | T6 |
| `host/src/Profiles.php` | C22 profile create/link/unlink/disable | T3 |
| `host/src/InquiryResults.php` | C21 inquiry result recording and the `mark_ready` gate lookup | T3 |
| `host/src/CommandIntents.php` | C23/C24 intent state machine and storage, Q14 list service | T9b |
| `host/src/Operations.php` | C25 operation claim, digest, stored result, replay and the Q13 lookup. Exposes the one-transaction claim API every command task uses from T4 onward | **T1** (round-3, C-1) |
| `host/migrations/<F>_*.sql` — the **foundation migration** (expected `004_*`) | **Gate B-2's storage half, relocated by ruling 91:** (1) the dedicated **operations** table and (2) the dedicated **intent** table, with the columns, keys and lifecycle of §6.2.1.1. Nothing else. Both are new and empty, so there is no pre-existing row that can violate them and no `Migrations.php` change is required. | **T1** |
| `host/migrations/<D>_*.sql` — the **domain migration** (expected `005_*`) | **Unconditional content (gate B-10, expanded by ruling 84):** (1) Phase C **account actor columns** for mutation audit and soft deletes on every business table Phase C mutates (legacy `deleted_by` stays untouched); (2) the I1 cross-slot serial slot ledger `device_serial_slots (tenant_id, serial, device_id, slot)` with `UNIQUE (tenant_id, serial)`; (3) the I2 active-ownership unique key; (4) the **host-owned `profiles` table** and (5) the **host-owned `inquiry_results` table** — §6.2.1.1, added because C21/C22 and the Q8/Q11/C5 surfaces have no storage in 001/002/003 and Task 3 is prohibited from adding DDL; and (6) pre-migration violation detection for the I1/I2 constraints plus the advisory profile-coverage count (§5.3). **Parent-key rule (round-3, I-1): reuse existing candidate keys and add only required keys that are absent.** `003_scoped_business_keys.sql` adds `UNIQUE (tenant_id, id)` for **exactly four** tables — `customers`, `owners`, `periods`, `crm_devices` (`003:57-67`) — and supplies the composite same-tenant FKs that build on them; it adds **nothing** for `identities` and never touches `tenants`. §6.2.1.1 states precisely which parent keys the domain migration must reuse and which it must add | **T9a** |
| `host/src/Schema.php` | New columns/tables, grouped index and FK metadata, identifier allowlists for the above | T1 (foundation tables), then T9a (domain tables) |
| `lab/fixtures/host_v2/test_foundation.php` | The tested foundation gate: foundation migration, operations/intent keys, §5.4 claim sequence | T1 |
| `lab/fixtures/host_v2/test_crm_queries.php` | Q1–Q12 suite | T2 |
| `lab/fixtures/host_v2/test_crm_core.php` | C1–C10 suite | T4 then T5 |
| `lab/fixtures/host_v2/test_periods.php` | C18–C20 (round-3, m-5: **not** Q9/Q10 — those services live in `CrmQueries.php`, owned by T2, and their HTTP contract is T10's) | T7 |
| `lab/fixtures/host_v2/test_ledger.php` | C16–C17, Q5/Q6, I6–I9 | T6 |
| `lab/fixtures/host_v2/test_stages.php` | C11–C15, I3–I5 | T8 |
| `lab/fixtures/host_v2/test_inquiry_profiles.php` | C21–C22 | T3 |
| `lab/fixtures/host_v2/test_intents.php` | C23–C25, Q13/Q14, I12 | T9b |
| `lab/tests/test_host_v2_api.py` (extended) | HTTP contracts, `Codes::ALL` agreement, G1 index.php binding, M1 log line | T1, T10 |

Every new class is `final`, `declare(strict_types=1)`, PHP 8.1 compatible (no readonly-class syntax), takes
`Database` in the constructor, loads no config or credentials itself, and exposes methods whose callbacks
run inside a caller-owned `Database::transaction()`.

#### 6.2.1 Schema contract added by revision round 2

##### 6.2.1.1 The four Phase C tables, at column level — foundation migration `004_*` (T1) and domain migration `005_*` (T9a)

Ruling 84 requires the storage contract to land **before** its consumers. Ruling 91 then split it (§6.1,
§6.2): the **operations** and **intent** tables are the foundation migration `004_*`, owned by **T1**,
created brand new and empty so they need no pre-migration detector and no `Migrations.php` edit; the
**`inquiry_results`** and **`profiles`** tables are the domain migration `005_*`, owned by **T9a**. All four
rows below are **unconditional** content — none is optional — and the whole table is what T3 (C21/C22,
Q8/C5/Q11) and T9b (C23–C25, Q13/Q14) compile against. Exact identifiers are BC-2; the columns, keys,
ownership and deletion semantics are not.

**Parent-key rule (round-3, I-1, ruling 92), applied per row below.** `003_scoped_business_keys.sql` adds
`UNIQUE (tenant_id, id)` for exactly `customers` (`003:57-58`), `owners` (`:60-61`), `periods` (`:63-64`) and
`crm_devices` (`:66-67`) — and nothing else. It adds **nothing** for `identities` and **never touches
`tenants`**, which has no `tenant_id` column at all (`001_initial.sql:88-89` gives it `PRIMARY KEY (id)` and
`UNIQUE (slug)`). Every 001/003 foreign key to a tenant is therefore the **plain** form
`FOREIGN KEY (tenant_id) REFERENCES tenants (id)` (`001_initial.sql:117,131,155,191,215,240,260,283,313,342,380,409,439,463,519,556`),
never a composite one. Phase C **reuses existing candidate keys and adds only required keys that are absent**: the
domain migration adds `UNIQUE (tenant_id, id)` on `identities` — the one genuinely missing parent key —
before it may install any composite FK that references it, and adding it is not a duplicate.

| Table | Columns (beyond the shared `created_at`, `updated_at`, `row_version`) | Keys / indexes | Relationships and deletion |
| --- | --- | --- | --- |
| `inquiry_results` **(domain migration `005_*`, T9a)** | `id` CHAR(36) PK, `tenant_id` CHAR(36) NOT NULL, `device_id` CHAR(36) NOT NULL, `service` VARCHAR(16) NOT NULL (`'gomrok'\|'hamta'`), `status` VARCHAR(32) NOT NULL (closed vocabulary, C21), `result` TEXT, `messages` JSON, `identity_id` CHAR(36) NULL, `completed_at` DATETIME NULL, `deleted_at` DATETIME NULL, `deleted_account_id` CHAR(36) NULL (Phase C actor column) | `PRIMARY KEY (id)`; `UNIQUE (tenant_id, device_id, service)` — one result row per device per channel, so a re-record is an update under the row lock, never a second row; `KEY ix_inquiry_results_tenant_service (tenant_id, service, status)` for Q8's reservation filter; `KEY ix_inquiry_results_identity (tenant_id, identity_id)` | `tenant_id`: the **plain** `FOREIGN KEY (tenant_id) REFERENCES tenants (id)` that 001 already uses (`001_initial.sql:463`); 003 has no composite key on `tenants` to reuse and `005_*` — which creates this table — must not invent one. `device_id`: **reuse** `uq_crm_devices_tenant_id` (`003:66-67`) for the tenant-matching composite FK. `identity_id`: 003 adds **no** parent key for `identities`, so the domain migration adds `UNIQUE (tenant_id, id)` there first (parent-key rule above) and only then the composite FK, **nullable and ON DELETE RESTRICT** — a C5 delete is refused (409) while a non-deleted row references it, never cascaded. **Deletion is soft** (`deleted_at` + actor column); `crm.delete_device`'s inquiry-row removal (`crm.py:1388-1411` parity) is expressed as that soft delete in the **same transaction** as the device tombstone. **Re-record over a soft-deleted row (round-3, m-3): the tombstone still occupies the unique key**, so a second C21 record for the same device+channel must **resurrect** the row — clear `deleted_at` and the actor column and write the new outcome under the row lock, in one transaction — rather than answer 409 or insert a second row. That is the behaviour the "one result row per device per channel" key already implies, it keeps parity with the oracle's delete-then-recreate (`crm.py:1388-1411`, a hard delete whose host equivalent is the hard-delete-vs-tombstone divergence already recorded at §3.1 C8 / §9.2 BC-6), and it leaves no third option for a task to invent |
| `profiles` **(domain migration `005_*`, T9a)** | `id` VARCHAR(64) NOT NULL — **the same type and grammar as the existing `crm_devices.profile_id`**, so a local `profiles.json` id can be adopted without re-keying; `tenant_id` CHAR(36) NOT NULL, `label` VARCHAR(120) NOT NULL DEFAULT '', `enabled` TINYINT(1) NOT NULL DEFAULT 1, `disabled_at` DATETIME NULL, `disabled_account_id` CHAR(36) NULL (Phase C actor column), `deleted_at` DATETIME NULL, `deleted_account_id` CHAR(36) NULL | `PRIMARY KEY (tenant_id, id)` — the tenant is part of the identity, so this table **is its own parent key** and reuses nothing from 003; `KEY ix_profiles_enabled (tenant_id, enabled, deleted_at)` | `tenant_id`: the **plain** `FOREIGN KEY (tenant_id) REFERENCES tenants (id)` (the 001 convention). **No FK from `crm_devices.profile_id` to this table** — that column stays dangling by design (§5.3). `enabled = 0` is how `_disable_detached_profile` (`crm.py:1425`) parity is expressed; unlink never deletes the row. Deletion is soft and is refused (409) while a non-deleted same-tenant `crm_devices.profile_id` points at it |
| Phase C operations **(foundation migration `004_*`, T1)** | `operation_id` CHAR(36) NOT NULL, `tenant_id` CHAR(36) NOT NULL, `transport_id` CHAR(36) NOT NULL, `request_hash` CHAR(64) NOT NULL, `status` VARCHAR(16) NOT NULL, `http_status` INT NOT NULL, `response` MEDIUMTEXT NOT NULL, `command_name` VARCHAR(64) NOT NULL, `command_version` VARCHAR(32) NOT NULL, `capability` VARCHAR(32) NOT NULL, `account_id` CHAR(36) NOT NULL, `session_id` CHAR(36) NOT NULL, `intent_id` CHAR(36) NULL, `failure_class` VARCHAR(32) NULL, `committed_at` DATETIME NOT NULL | `PRIMARY KEY (operation_id)`; `UNIQUE (tenant_id, operation_id)`; `UNIQUE (tenant_id, transport_id)`; `KEY ix_operations_tenant_committed (tenant_id, committed_at)` | `tenant_id`: the **plain** `FOREIGN KEY (tenant_id) REFERENCES tenants (id)` (the 001 convention) — there is no 003 composite key on `tenants` and none may be invented (parent-key rule above). Append-only: **never swept, never deleted** by Phase C (§5.5). Written inside the command transaction as the **first** write after the gates (§5.4). The four `NOT NULL`
  outcome columns (`status`, `http_status`, `response`, `committed_at`) are filled at claim time with the
  declared pending defaults and completed by the same-transaction write-back (§5.4, round-4, F3), so the DDL
  and `OperationsContract` agree. Created **empty**, so it needs no pre-migration detector and no `Migrations.php` edit |
| Phase C intents **(foundation migration `004_*`, T1)** | `intent_id` CHAR(36) NOT NULL, `tenant_id` CHAR(36) NOT NULL, `transport_id` CHAR(36) NOT NULL, `operation_id` CHAR(36) NULL, `kind` VARCHAR(16) NOT NULL (`'finance'\|'job'`), `state` VARCHAR(16) NOT NULL (`pending\|dispatched\|settled\|rejected\|expired`), `command_name` VARCHAR(64) NOT NULL, `command_version` VARCHAR(32) NOT NULL, `payload_digest` CHAR(64) NOT NULL, `dispatched_at` DATETIME NULL, `resolved_at` DATETIME NULL, `evidence` VARCHAR(500) NULL, `created_account_id` CHAR(36) NULL | `PRIMARY KEY (intent_id)`; `UNIQUE (tenant_id, transport_id)`; `UNIQUE (tenant_id, operation_id)`; `KEY ix_intents_tenant_state (tenant_id, state, created_at)` for Q14 | `tenant_id`: the **plain** `FOREIGN KEY (tenant_id) REFERENCES tenants (id)`. `operation_id` FK to the Phase C operations table — **same migration, so the composite parent key is created by that migration itself**, nullable until the operation commits. Q14 returns state only, never payload or credential material. Created empty with the operations table, so it needs no pre-migration detector either |

**Local profile ids and compatibility (explicit, per ruling 84).** Migration 001 documents
`crm_devices.profile_id` as pointing at the local `profiles.json` and being "dangling by design"
(`001_initial.sql:50,469`); the column is `VARCHAR(64) NOT NULL DEFAULT ''` (`:494`). The domain migration
therefore (a) types `profiles.id` as `VARCHAR(64)` so an existing local id is adoptable **without re-keying**,
(b) puts the tenant in the primary key so two tenants cannot adopt the same local id into different rows, and
(c) adds **no foreign key** over `crm_devices.profile_id`. A profile id is host-owned only once a
`profiles` row exists; the advisory pre-migration count (§5.3 (d)) reports the coverage gap and never blocks.
This is a deliberate, documented divergence from 001's "local only" comment: the host table is an
**authoritative mirror of the non-credential subset**, not a replacement for the local store.

**Credential boundary on `profiles`.** Browser-session material, cookies, proxy settings and any other
credential-class field of the local store (`panel.py::load_store`) are **never** columnised. The host table
carries only `label`, `enabled` and the lifecycle/actor columns, and Q11's `profiles` array is built from
that closed column set (gate B-6, I13).

**`row_version` on the four Phase C tables (constraint found while closing §10 item 4).** `test_migrations.php:918-941`
iterates `Schema::tables()` and requires every table carrying a `row_version` column — except the four
`$versionDomains` entries at `:892-897` — to keep an **unrestricted** domain (`min`/`max` both null) and to
validate `0` and `-5` without `OUT_OF_RANGE`. The four new Phase C tables (two in `004_*`, two in `005_*`)
therefore declare `row_version` as an
**unrestricted int with no min**, exactly like the legacy business tables, or T9a must extend that assertion
in the same change. Silently declaring `min=1` on a new table would fail `--suite migrations`.

##### 6.2.1.2 The G1 / M1 production-entrypoint harness (I3)

The previous draft's BC-1 offered an unbounded choice and Task 10 asserted an M1 line "over the full Phase C
route surface", which could not be produced as written. The mechanism is now fixed:

- **Why the fixture front cannot prove G1 — and cannot serve `/v1` either.** `lab/fixtures/host_v2_front.php`
  delegates classification and calls `V2Api::dispatch` directly with `assertTransport(..., fixture: true)`
  (`:82,84`); the production branch in `host/public/index.php:96-118` passes `fixture=false`, which
  **requires the server's own TLS indication** (`V2Api.php:161-171`), and resolves its configuration through
  `Config::locate()`, which honours `IRICA_HOST_CONFIG` (`host/src/Config.php:18,74-81`) and otherwise reads
  `host/config/config.php` (global constraint 12 forbids reading that file). The front is additionally
  **`/v2`-only**: `host_v2_front.php:75-78` throws `404 HTTP_404` for any path where
  `!V2Api::isVersion2Request($rawUri)`, so it cannot carry a `/v1` request at all, and §6.1 makes the file
  untouchable. Every `/v1` compatibility assertion of this plan is therefore built **inside** a
  `lab/fixtures/host_v2/*.php` suite against `Http::jsonBody()` directly, never through the front.
- **The harness (T1's deliverable).** A CLI harness `lab/fixtures/host_v2/index_binding.php` (new file,
  T1-owned) that sets, in this order: `$_SERVER['HTTPS'] = 'on'`, `$_SERVER['REQUEST_METHOD']`,
  `$_SERVER['REQUEST_URI']`, `$_SERVER['REMOTE_ADDR'] = '127.0.0.1'`, a `Content-Type: application/json`
  header, and `putenv('IRICA_HOST_CONFIG=' . <temp path>)`, then `require`s `host/public/index.php`. The
  temp config file is **generated at run time** from the already-validated fixture target (the same
  `fixture/run_id/migrate/runtime` structure the runner validates at
  `lab/scripts/run_host_v2_tests.py:36-58`), converted to `Config.php`'s shape and written under the
  suite's own temp directory with owner-only permissions, then **deleted in a `finally`**. It is
  **never checked in and never printed** (global constraints 13, 18); its `jwt_secret` is generated fresh
  per run. **The generated shape (round-3, I-2, ruling 93) is exactly:**

  ```php
  ['db' => ['host' => …, 'port' => …, 'dbname' => …, 'user' => …, 'password' => …],
   'jwt_secret' => <fresh 32-byte hex>,
   'log_path'   => <suite temp dir>/access.log]
  ```

  `log_path` is **required, not decorative**: `host/public/index.php:64-65` builds
  `new Log(is_string($config['log_path'] ?? null) ? $config['log_path'] : null)`, and `Log::line()` writes
  **nothing** when the path is `null` (`host/src/Log.php:65-67`) — which is why the M1 line was unobservable
  through this harness as previously specified. The path names a file inside the suite's own temporary
  directory (writable, isolated, removed with the rest of the temp state); `Log::__construct` creates a
  missing directory at mode `0750` (`Log.php:37-41`), so the harness need only ensure the parent directory
  is writable. `Log::line()` drops any key outside `ALLOWED_FIELDS` (`Log.php:70-72`), so a record can only
  ever carry keys from that allowlist. `Config::validate()` requires only `db` and `jwt_secret`
  (`host/src/Config.php:83-104`) and `validateDb()` requires `db.{host,dbname,user,password}` (`:115-121`),
  so the extra `log_path` key and the optional `port` are accepted as-is; the harness **never** sets
  `limits`, so `index.php:67-74` keeps its `??` defaults. The consumer of `log_path` is `index.php:64-65`
  and nothing else in the plan may invent a second one.
- **What G1 asserts.** The harness request must produce the same status and error code as the same request
  through the fixture front, and the binding case must **fail** when the early `return` in `index.php`'s v2
  branch is deleted or when the fixture flag is flipped — mutation-tested by T1 in the same step.
- **What M1 asserts (round-3, I-3, ruling 93).** The access-log record emitted for a request — the
  `$log->line` in `index.php`'s trailing `finally` (`:328-335`), which is the last line of every request —
  **contains** `route`, `status` and `code` with the expected values. It is **not** required to contain only
  those three: `Log::line()` prepends `ts` (`Log.php:68`) and `index.php` also writes `request_id`,
  `method` and `duration_ms`, so the record carries seven keys, all of them inside `Log::ALLOWED_FIELDS`
  (`Log.php:24-30`). The assertion therefore also checks that every key in the record is in
  `ALLOWED_FIELDS`, that no credential or PII value appears, and that the line is emitted on success, on a
  401/403 denial and on a 5xx. A 5xx additionally writes one diagnostic line in its catch arm
  (`index.php:316-318`, `:324-326`), so the assertion selects the access line rather than asserting exactly
  one line per request. The line is read from the `log_path` the harness config names — never a hardcoded
  path (G4) — and **the selector is specified, not assumed (round-4, F5)**: `Log::line()` **appends** one
  JSON object per line (`Log.php:91-92`), so `log_path` is a growing file that can also hold the 5xx
  diagnostic record (`index.php:316-318`, `:324-326`), lines from an earlier request and lines from an
  earlier suite of the same `--suite all` run. The case therefore (i) records the file size immediately
  before issuing the request, (ii) reads only what was appended after that offset, (iii) **skips every line
  that is not a JSON object and every record that lacks `duration_ms`** — only the trailing-`finally` record
  carries it (`index.php:328-335`), which is exactly what distinguishes it from the diagnostic arm — and (iv)
  asserts that exactly one surviving record carries the expected `route`/`status`/`code`, matching
  `request_id` when the response exposes it (`Http.php:362`). It never reads the first line of the file and
  never assumes the last line of the file is the access record. And **T1 must not delete `request_id`,
  `method`, `duration_ms` or `ts` to make the assertion
  pass**: a production observability field is never removed to satisfy a test, and an assertion that would
  require that is wrong, not the line.
- **`logLabel` / `knownRoute` extension (T10, as `V2Api.php`'s co-owner).** `logLabel()` returns the literal
  `<unknown>` for any path absent from the static `ROUTES` table (`V2Api.php:135-140`), so no Phase C route
  can be named in a log line until T10 adds the registered parameterised patterns to that lookup — **by
  pattern, from the same table `route()` matches**, never by echoing the request path. `knownRoute()` is
  extended the same way so the label stays user-input-free. T10 Step 6 then asserts one M1 line per
  registered Phase C route.
- **Escape hatch, decided, not open.** If the harness cannot be made to run — for example because
  `Config::load()`'s validation rejects a generated fixture config — the worker stops per §9.3(5), records
  the exact failure, and the controller records a **waiver** against §10 item 14 before T10's acceptance is
  signed. The worker never waives it locally, and never substitutes the fixture front as evidence for G1.

### 6.3 Interface contract (defined here for all tasks)

All `$now` values come from the server clock, never an HTTP body. All callbacks are SQL-only and run within
one `Database::transaction()` opened by `AccountSessions::withWorkspace()`. Nested transactions are
forbidden. The block below defines signatures for review; it is not a request to create interface files.

```php
<?php
declare(strict_types=1);

interface CrmQueriesContract {
    /** Q1/Q3/Q7/Q8/Q9/Q14 shared keyset page. */
    public function pageWithin(Database $tx, string $tenantId, string $entity,
                               array $filters, ?string $after, int $limit): array;
    public function devicesWithin(Database $tx, string $tenantId, array $filters,
                                  ?string $after, int $limit): array;   // Q1
    public function deviceWithin(Database $tx, string $tenantId, string $id): ?array; // Q2
    public function customersWithin(Database $tx, string $tenantId, array $filters,
                                    ?string $after, int $limit): array; // Q3
    public function customerWithin(Database $tx, string $tenantId, string $id): ?array; // Q4
    public function ledgerWithin(Database $tx, string $tenantId, string $customerId,
                                 array $filters, ?string $after, int $limit): array; // Q5
    public function balanceWithin(Database $tx, string $tenantId, string $customerId): int; // Q6
    public function ownersWithin(Database $tx, string $tenantId, array $filters,
                                 ?string $after, int $limit): array;   // Q7
    public function identitiesWithin(Database $tx, string $tenantId, array $filters,
                                     ?string $after, int $limit): array; // Q8
    public function periodsWithin(Database $tx, string $tenantId, string $id): array; // Q9
    public function settingsWithin(Database $tx, string $tenantId): array; // Q10
    public function stateWithin(Database $tx, string $tenantId, array $filters): array; // Q11
    public function invoiceWithin(Database $tx, string $tenantId, string $deviceId): array; // Q12
}

interface CrmCommandsContract {
    public static function assertRowVersion(array $row, int $expected): void;
    public function createCustomer(Database $tx, array $context, array $input, int $now): array;
    public function updateCustomer(Database $tx, array $context, array $input, int $now): array;
    public function deleteCustomer(Database $tx, array $context, array $input, int $now): array;
    public function createOwner(Database $tx, array $context, array $input, int $now): array;
    public function updateOwner(Database $tx, array $context, array $input, int $now): array;
    public function deleteOwner(Database $tx, array $context, array $input, int $now): array;
    public function createIdentity(Database $tx, array $context, array $input, int $now): array;
    public function updateIdentity(Database $tx, array $context, array $input, int $now): array;
    public function deleteIdentity(Database $tx, array $context, array $input, int $now): array;
    public function createDevice(Database $tx, array $context, array $input, int $now): array;
    public function updateDevice(Database $tx, array $context, array $input, int $now): array;
    public function deleteDevice(Database $tx, array $context, array $input, int $now): array;
    public function attachOwner(Database $tx, array $context, array $input, int $now): array;
    public function detachOwner(Database $tx, array $context, array $input, int $now): array;
    public function advanceStage(Database $tx, array $context, array $input, int $now): array;
    public function stageBack(Database $tx, array $context, array $input, int $now): array;
    public function stageSkip(Database $tx, array $context, array $input, int $now): array;
    public function openPeriod(Database $tx, array $context, array $input, int $now): array;
    public function closePeriod(Database $tx, array $context, array $input, int $now): array;
    public function updateSettings(Database $tx, array $context, array $input, int $now): array;
}

interface LedgerCommandsContract {
    public static function countable(array $row): bool;          // _tx_countable parity
    public function addTransaction(Database $tx, array $context, array $input, int $now): array;
    public function voidTransaction(Database $tx, array $context, array $input, int $now): array;
    public function balanceWithin(Database $tx, string $tenantId, string $customerId): int;
    public function statementWithin(Database $tx, string $tenantId, string $customerId,
                                    array $filters, ?string $after, int $limit): array;
}

interface ProfilesContract {
    public function createWithin(Database $tx, array $context, array $input, int $now): array;
    public function linkWithin(Database $tx, array $context, array $input, int $now): array;
    public function unlinkWithin(Database $tx, array $context, array $input, int $now): array;
}

interface InquiryResultsContract {
    public function recordWithin(Database $tx, array $context, array $input, int $now): array;
    /** Returns [] when both services passed; otherwise the blocking reasons. */
    public function gateWithin(Database $tx, string $tenantId, string $deviceId): array;
}

interface CommandIntentsContract {
    public function createWithin(Database $tx, array $context, string $transportId,
                                 string $kind, string $payloadDigest, int $now): array;
    public function transitionWithin(Database $tx, array $context, string $intentId,
                                     string $state, string $evidence, int $now): array;
    public function listWithin(Database $tx, string $tenantId, array $filters,
                               ?string $after, int $limit): array;
}

interface OperationsContract {
    /**
     * §5.4 step 1 / §6.4 step 5 — the command's FIRST write, after the account/session/
     * tenant/membership gates. Takes the `SELECT ... FOR UPDATE` on
     * `(tenant_id, operation_id)`; a missing row means first execution and this method
     * performs the insert. Returns a replay payload when the locked row's `transport_id`
     * and `request_hash` both match, an `IDEMPOTENCY_MISMATCH` classification when the ids
     * match and the hash differs (the caller answers 409 from inside the lock, §5.4), and
     * `{outcome: 'claimed', operation_id, …}` on first execution.
     */
    public function claimWithin(Database $tx, array $context, string $operationId,
                                string $transportId, string $requestHash,
                                string $commandName, string $commandVersion,
                                string $capability, int $now): array;
    /**
     * §6.4 step 8 — the committed write-back onto the row `claimWithin()` created, in the
     * SAME transaction. Fills `status`, `http_status`, `response`, `failure_class`,
     * `intent_id` and `committed_at`.
     */
    public function recordWithin(Database $tx, array $context, string $operationId,
                                 string $status, int $httpStatus, string $responseBody,
                                 ?string $failureClass, ?string $intentId, int $now): array;
    /**
     * Post-rollback ONLY (round-4, F3): the classified pre-commit failure of §5.5/§5.6.
     * Opens its own short transaction on `$db` — §5.2 forbids nesting and the rolled-back
     * claim row is already gone — writes no domain row, and never runs inside a lock or
     * inside a callback §5.2 may retry.
     */
    public function recordFailure(Database $db, array $context, string $operationId,
                                  string $status, int $httpStatus, string $responseBody,
                                  string $failureClass, int $now): array;
    public function replayWithin(Database $tx, string $tenantId, string $transportId,
                                 string $requestHash): ?array;
    public function lookupWithin(Database $tx, string $tenantId, string $operationId): ?array;
}
```

How the claim row satisfies the operations table's `NOT NULL` outcome columns is specified in §5.4
(round-4, F3); the DDL of §6.2.1.1 and this API agree on it, and `test_foundation.php` asserts the sequence.

`$context` is the workspace context array produced by `AccountSessions::withWorkspace()` plus the acting
account id; it never carries a raw token, password or client-asserted role. `$context` shape:
`{account_id, tenant_id, workspace_role, context_version, api_contract, session_id}`. **`session_id` is
mandatory, not optional (gate B-5):** `AccountPrincipal` (`host/src/AccountSessions.php:101`) is frozen and
carries no session id, so the calling task resolves the exact authenticated session row inside the locked
callback of the mutation transaction and rejects the mutation if it cannot identify that one row. It never
substitutes an arbitrary session of the same account or tenant, and never passes `null`. Reading a session id
is read-only work on `account_sessions`, not an edit of the Phase B file. Verified mechanism, now **settled**
(round-2, I6; §10 item 1 is CLOSED): the callback is invoked as `$fn($tx, $principal, $snapshot)`
(`host/src/AccountSessions.php:550`), and `$snapshot` is exactly
`Workspaces::describe()`'s array — `tenant_id, slug, name, status, lifecycle, api_contract, context_version,
role` (`host/src/Workspaces.php:348-362`) — which **does not carry a session id**. The fallback is therefore
the only path, and its steps are fixed: (1) the callback is authored by `V2Api`, which still holds the
admitted token in the request; (2) the callback computes the stored form with the public
`Tokens::hashOpaque(string $token): string` (`host/src/Tokens.php:127-130`, plain SHA-256, 64 hex chars) —
**not** `AccountSessions::hash()`, which is `private` (`AccountSessions.php:217`) and is literally the same
operation; (3) it then re-reads **that one** row with `SELECT id FROM account_sessions WHERE token_hash = ?`
under `uq_account_sessions_token_hash` (`002_identity_foundation.sql:136`) — the unique key makes the read a
single-row lookup, never an account/tenant scan — and takes `id` as `$context.session_id`. If the token is
not in scope at the callback authoring site, that is a contract gap: stop and escalate (§9.3(2)).

### 6.4 Lock order

Phase B's order (`docs/superpowers/plans/2026-10-11-host-identity-integrity.md` §Lock order) is preserved
and extended; Phase C inserts domain steps after step 4 and never reorders the earlier steps.

0. Operations changing platform-operator lifecycle acquire the singleton `identity_state` lock first.
   **No Phase C task performs such an operation.**
1. Multi-account operations declare all account IDs up front and lock them in UUID order.
2. Account then session rows, locked/revalidated by stable primary key (`AccountSessions`).
3. All affected **tenant** rows in UUID order, then membership rows
   (`Workspaces::lockAuthorizedWithin`, capability check).
4. Existing `tenant_state` locks follow the tenant row, as today.
5. **Phase C insert — the operations row is the FIRST Phase C write (round-2, I5).** After step 4 and
   **before** any business row is touched, the command takes a **locking** read —
   `SELECT ... FOR UPDATE` on the Phase C operations row by `(tenant_id, operation_id)`. A missing row means
   first execution, and the operations-row `INSERT` (with `UNIQUE (tenant_id, operation_id)` and
   `UNIQUE (tenant_id, transport_id)`) is the command's first write, so a concurrent duplicate loses on the
   unique key and the whole command rolls back rather than double-applying. The request hash is compared
   **under that lock**; a mismatch answers 409 `IDEMPOTENCY_MISMATCH` from inside the lock. A plain
   `SELECT` is not permitted here — under InnoDB REPEATABLE READ it can miss a concurrently committed row
   and two identical `transport_id` requests would both execute (§5.4). **Round-3, I-8:** step 3's tenant
   lock already serialises same-tenant requests, so this lock is defence in depth for the §6.4 scenario and
   is load-bearing wherever that gate is absent; tests must assert *this* lock specifically (T9b Step 1),
   never infer it from a request that merely waited for the first to commit.
6. **Phase C insert:** lock the affected business rows **by primary key in ascending UUID order**, after the
   step-5 operations lock.
   - Single-row commands lock exactly one business row.
   - Commands that touch more than one business row (C8 device delete, C17 void, C9/C10 owner binding)
     lock them in ascending `id` order, never interleaved with account/tenant locks.
   - Customer-ledger commands (C16/C17) lock the `customers` row first, then the specific
     `customer_transactions` row(s) by `id`.
7. **Phase C insert:** allocation reads (`MAX(seq)`, `MAX(period.number)`, "is a period already open")
   happen **under** the step-4 tenant lock or the step-6 customer lock, never from an unlocked snapshot.
8. **Phase C insert:** the intent row (C23) and the Phase C audit write are written **inside** the same
   transaction, under the step-5 operations lock, and the committed response body is written back onto the
   operations row before the transaction ends. The external effect, if any, is performed only after commit
   and outside every lock.

Full Phase C lock order, stated once: **account → session → tenant → membership → tenant_state →
operations row → business rows (ascending `id`) → allocation reads → intent/audit.**

No Phase C code may lock a target business row before the account/tenant/membership gates. No Phase C code
may acquire a second tenant after locking a first, except through the UUID-ordering rule.

### 6.5 Tasks

Each task states files, consumes/produces, dependencies, steps with the exact commands, and RED/GREEN
expectations. **RED means a genuine failing assertion that the implementation must then satisfy**; a suite
that cannot run, or that is skipped, is never GREEN. Every task ends with a scoped-diff review; commit is
out of scope for this plan.

Global verification commands (discovered from the repository, not invented):

- Fixture gate: `"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite fixture-guard`
- One suite: `…" -I -B lab/scripts/run_host_v2_tests.py --suite <name>`
- Everything: `…" -I -B lab/scripts/run_host_v2_tests.py --suite all`
- Lint: `php -l host/src/<File>.php`
- The runner's `--suite` choices are the `SUITES` tuple at `lab/scripts/run_host_v2_tests.py:22`, currently
  `("fixture-guard", "migrations", "integrity", "workspaces", "accounts", "sessions", "api", "all")`;
  `--suite` enforces `choices=SUITES` (`:338`), so an unregistered name is rejected by argparse before any
  file is consulted, and a registered name whose file is absent raises `FIXTURE_SUITE_MISSING`
  (`:355`, `:385`, exit 2). Registering a Phase C suite therefore requires **one** coordinated change to that
  tuple, `_INTEGRATION` (`:23`, a slice of `SUITES`, so it follows automatically) and the `expected` map in
  `lab/tests/test_host_v2_fixture.py:234-241`. **T1 performs that change once, and in the same change
  creates every Phase C `test_<suite>.php` file** (round-2, C1) — see §6.1.

**Dependency graph and lanes (rewritten for revision round 2, ruling 85).** The previous draft's
unconditional "parallel-safe set" `{T2, T3, T6, T7}` is **deleted**: it contradicted the graph (`T9a → T6`,
`T5 → {T7,T8}`), contradicted `CrmCommands.php`'s strict T4→T5→T7→T8 chain, and omitted T2's real dependency
on T6's `LedgerCommands`. Only the edges below are authorised (round-3, m-4: the diagram now carries **every**
edge the prose states, including the cross-lane `T3 → T8`):

```
T1 ──► T4 ──► T9a ──► ( lane A: T5 ──► T7 ──► T8 )
                 ├──► ( lane B: T6 ──► T2 )          T2's completion gate is AFTER T6
                 ├──► ( lane C: T3 ) ────────────────► T8   CROSS-LANE: T8's mark_ready gate
                 └──► T9b                             (after T4, for a mutation to audit)
All of T2, T3, T5, T6, T7, T8, T9b ──► T10 (integration + full-suite acceptance)
The diagram is now edge-complete: T8 has TWO predecessors, T7 (lane A) and T3 (lane C).
```

- **T1 → T4 → T9a** is the backbone: dispatch foundation, then the command base, then the domain schema
  005 (T1's foundation schema 004 lands with T1 itself).
- **Lane A** (`T5 → T7 → T8`) is a strict serialization on `host/src/CrmCommands.php`; no two of those three
  ever run concurrently.
- **Lane B** (`T6 → T2`) exists because `CrmQueries` Q5/Q6 **delegate** to `LedgerCommands` (§6.2), which T6
  owns and creates. Independent T2 query work (Q1–Q4, Q7–Q12) may be **developed** after T1 in parallel with
  lanes A/B, but **T2's completion gate — its named suite green and its `all` inclusion verified — stays
  after T6**, because a `crm_queries` suite that asserts Q5/Q6 parity cannot be green before
  `LedgerCommands` exists.
- **Lane C** (`T3`) runs after **T9a**, not after T1: C21/C22, and the Q8 `available` filter, the Q11
  composite and the C5 delete refusal, all need the `inquiry_results` and `profiles` tables (§6.2.1.1).
- **T9b** runs after T9a (tables) and after T4 (a real mutation to audit); it does not block lanes A/B/C.
- Lanes A, B and C are mutually independent in **files**: `CrmCommands.php` (A), `LedgerCommands.php` +
  `CrmQueries.php` (B), `Profiles.php` + `InquiryResults.php` (C). They may run concurrently **with each
  other only**, never within themselves.
- `Schema.php` (once T1's foundation edit has landed, §6.1) and `host/migrations/005_*.sql` stay T9a's
  exclusive files; lanes A/B/C consume them and
  never edit them.

**Per-task verification contract (round-2, C1 — no task may be green by omission).** Every task's acceptance
must do **all four**, in this order, and record the real output of each:

1. `--suite fixture-guard` exits 0.
2. The task's **own named suite** (`--suite <name>`) exits 0 with that suite's `FIXTURE_*_PASS` line.
3. `--suite all` exits 0 **and** prints exactly one `FIXTURE_READY` plus a `FIXTURE_*_PASS` line **including
   the task's own suite** — this is the proof that the name is genuinely registered and wired, not merely
   that the file runs.
4. `php -l` on every PHP file the task created or extended.

Prohibited, in every task: an empty passing placeholder left behind after the owning task has run; skipping a
suite; falling back to a different suite because the named one is missing; claiming a suite green when it
exited 2 with `FIXTURE_SUITE_MISSING`. A suite that cannot run is a §9.3(9) stop, never a pass.

---

#### Task 1: Dispatch foundation, production-branch binding, access log, codes and suite plumbing

**Files:** `host/src/V2Api.php`, `host/src/Http.php`, `host/src/Schema.php` (the two foundation tables only,
round-3, C-1), `host/src/Operations.php` (new), `host/public/index.php`,
`host/migrations/004_*.sql` (new, the foundation migration), `lab/scripts/run_host_v2_tests.py`,
`lab/tests/test_host_v2_fixture.py`, **`lab/tests/test_host_v2_api.py` (foundation cases only — round-3,
I-7; Steps 1, 2, 4, 5 and 7 edit it, and §6.1 already assigns it "T1 (foundation cases), T10 (HTTP contract
cases)" so it must appear here or the end-of-task scoped-diff review trips §9.3(10) on the first task)**,
`lab/fixtures/host_v2/bootstrap.php`, `lab/fixtures/host_v2/test_workspaces.php` (`TENANT_CHILDREN` entries
for the two foundation tables, round-3, C-1), `lab/fixtures/host_v2/index_binding.php` (new, the G1
production-entrypoint harness of §6.2.1.2), `lab/fixtures/host_v2/test_foundation.php` (new, the tested
foundation gate of §6.1/§6.2), and the seven new `lab/fixtures/host_v2/test_<suite>.php` stubs.
**Not T1's, despite the foundation work:** `host/src/Migrations.php`, `host/src/MigrationPreflight.php`
(the foundation migration creates brand-new empty tables, so it needs no detector — §6.1), and
`lab/fixtures/host_v2/test_migrations.php` / `test_integrity.php` (T9a's; T1's migration-step inventory
change is the single exception named in the §6.1 `test_migrations.php` row).

**Consumes:** Phase B contract (5 exact routes, `isServable`, `onlyFields`, `withSession`).
**Size note (round-2).** Round-1 review flagged T1 as the densest task and warned that it would have to be
split if G7 forced new `/v1`-shared parsing behaviour. Ruling 86 rejected exactly that: the strict parser is
`/v2`-scoped and `Http.php` is untouched, so the G7 work is bounded to `V2Api` and **T1 is not split**.
**Produces:** parameterised-path routing that cannot be abused; the **strict `/v2` request-parsing boundary**
(duplicate-key and surrogate-key refusal, ruling 86) with `/v1` proven unchanged; the G1
production-entrypoint binding and its harness; the asserted M1 access-log line (M1 release gate); the Phase C
error-code constants; the eight Phase C suite names in `SUITES` **plus the seven stub files** (the eighth
name, `foundation`, binds to the foundation gate below, round-4, F1); shared fixture
seed helpers; and **the foundation gate** (round-3, C-1, ruling 91) — migration `004_*`, its `Schema.php`
rows, its `TENANT_CHILDREN` entries and `host/src/Operations.php` — which every command task inherits.

- [ ] **Step 0: Build the foundation gate (round-3, C-1, ruling 91) before any command task exists.**
  (1) Create `host/migrations/004_*.sql` with **only** the Phase C operations table, the Phase C intent table
  and the parent keys those two need (§6.2.1.1): plain `FOREIGN KEY (tenant_id) REFERENCES tenants (id)` on
  each, and for the intents → operations link the composite parent key the operations table itself declares in
  the same file — 003 never touched `tenants` and has no key on `identities`, so nothing from 003 may be
  assumed (round-3, I-1, ruling 92). Both tables are brand new and empty, so the file needs **no**
  pre-migration detection and **no** edit to `Migrations.php`/`MigrationPreflight.php`; (2) register the two
  tables and their `Schema::migrationSteps('004_*')` entries in `host/src/Schema.php` **in the same change**
  (§6.1); (3) add them to `TENANT_CHILDREN` in `test_workspaces.php` (children before parents) and prove a
  seeded tenant still deletes cleanly; (4) create `lab/fixtures/host_v2/test_foundation.php` — the tested
  foundation gate: the migration applies, the operations/intent keys of §6.2.1.1 exist, and the §5.4 claim
  sequence holds; (5) extend **only** the step inventory at `test_migrations.php:551-568` for `004_*` (the
  version's step count and the matching new `$verifiedSteps` total) — §6.1's narrow T1 exception, no other
  line in that file; (6) create `host/src/Operations.php` with the §6.3 `OperationsContract` machinery —
  `claimWithin()` (first write, pending defaults), `recordWithin()` (same-transaction write-back),
  `recordFailure()` (post-rollback classification), `replayWithin()` and `lookupWithin()` — and the
  one-transaction contract of §5.4 (round-4, F3), so T4–T8 inherit a working operations row. Verify with
  `--suite fixture-guard`, `--suite migrations`, `--suite workspaces` and `php -l`; a foundation gate that
  cannot run is a §9.3(9) stop, never a pass.

- [ ] **Step 1: Write the RED tests first.** Add to `lab/tests/test_host_v2_api.py`: (a) an assertion that
  a request whose path contains a percent-escape, a dot segment, a doubled separator or a backslash is
  answered 404 and never reaches a handler — including the same assertions **repeated on a parameterised
  path shape**, proving `isServable()` still refuses them before matching (gate B-8); (b) an assertion that a
  parameterised path parses into `{route, params}` with the parameter restricted to the `Ids::v4()` shape
  (`host/src/Ids.php:53-66`), that a non-conforming segment yields 404 not 400, and that an exact static
  route always wins over a parameterised pattern for the same method (precedence, gate B-8); (c) the **G1
  binding** case, driven through the production entrypoint by `lab/fixtures/host_v2/index_binding.php`
  (§6.2.1.2) — **not** through `host_v2_front.php`, which never enters `index.php` and therefore cannot prove
  anything about it. The case must fail when the early `return` in `index.php`'s v2 branch is deleted or when
  the fixture flag is flipped to `true`, and T1 performs that mutation check in the same step; (d) the
  **M1** case, asserting that a `/v2` request through that same harness emits an access-log record that
  **contains** `route`, `status` and `code` **with the expected values** — not that those are its exclusive
  fields (round-3, I-3, ruling 93): `Log::line()` prepends `ts` (`Log.php:68`) and `index.php:328-335` also
  writes `request_id`, `method` and `duration_ms`, so the record carries seven keys, all inside
  `Log::ALLOWED_FIELDS` (`Log.php:24-30`). The case asserts every key of the record is in `ALLOWED_FIELDS`,
  that no credential or PII value appears in it, and that it is emitted on a denied and on a failing request
  as well as on a successful one (ruling 80). A 5xx also writes one diagnostic line (`index.php:316-318`,
  `:324-326`), so the case selects the access line — the `finally` record — **by the §6.2.1.2 selector**:
  read only the bytes appended after the pre-request file size, skip any line that is not a JSON object and
  any record without `duration_ms`, then match `request_id` (round-4, F5: never the file's first line and
  never "the last line in the file"), and does not require exactly one line per request. **T1 must not delete `ts`, `request_id`, `method` or
  `duration_ms` to satisfy this assertion**; a production observability field is never removed to make a test
  pass. **Scope (round-3, m-7): T1's M1 assertion covers the five Phase B routes only** — `logLabel()` still
  returns `<unknown>` for anything outside `V2Api::ROUTES` (`V2Api.php:135-140`), and T1 registers no Phase C
  route, so a Phase C route label is T10's assertion after its Step 3 `logLabel`/`knownRoute` extension.
  The log path is taken from the harness's generated config (`log_path`, §6.2.1.2) rather than a hardcoded
  path (G4).
- [ ] **Step 2: Run `--suite api` and require genuine failures for (a)–(d).**
  `"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite api`
- [ ] **Step 3: Implement the router extension (gate B-8), verifying before extending.** First confirm what
  `V2Api::route()` actually does today (static `match` on `$path`, `V2Api.php:261-269`); then extend it with
  a parameterised matcher that is evaluated **after** `isServable()` and never relaxes it, that prefers an
  exact static route over any parameterised pattern, and that constrains a parameter to the `Ids::v4()`
  shape. Keep the exact-string table for the five Phase B routes so their 404/405 semantics are unchanged.
  T1 registers **no** `/v2/crm` route — route registration is Task 10's. For M1, **verify first**: the
  trailing `finally` in `index.php` already writes the access record with `request_id`, `method`, `route`,
  `status`, `duration_ms` and `code` (`index.php:328-335`) and the v2 branch already sets `$routeLabel`
  (`index.php:86-91`), so T1 adds **no duplicate** log call and **removes no field** — it asserts that the
  existing record **contains** `route`, `status` and `code` with the expected values, over the harness of
  §6.2.1.2, and changes `index.php` only if that assertion proves the line is genuinely absent or missing one
  of those three fields, in which case the change is the smallest one that restores it and must never delete
  `ts`, `request_id`, `method` or `duration_ms`. Then add the G1 binding harness and its mutation check.
- [ ] **Step 4: Declare the Phase C error codes.** Add named constants **and** their `Codes::ALL` entries
  for every non-reused code named in §4: `NO_OPEN_PERIOD`, `STAGE_NOT_ALLOWED`, `OWNER_ALREADY_BOUND`,
  `SERIAL_IMMUTABLE`, `INQUIRY_NOT_PASSED`, `INTENT_STATE_INVALID`, `OPERATION_UNKNOWN`,
  `LEDGER_HALF_PAIR`, `PERIOD_ALREADY_OPEN`. Re-run `--suite api` so the `Codes::ALL` agreement case at
  `lab/tests/test_host_v2_api.py:935` (`test_d03_codes_all_equals_the_declared_constant_set`) proves 1:1.
- [ ] **Step 5: Implement strict request parsing at the `/v2` boundary (G7, ruling 86).** The premise that a
  duplicate-key refusal already exists is **false**: `Http::jsonBody()` (`host/src/Http.php:260-294`) decodes
  the body twice and is last-duplicate-key-wins, and `Http.php` is **not** changed. Implement the refusal in
  `V2Api` by tokenising the raw body **before** it is handed to `jsonBody()`/`onlyFields()`, and write the
  RED cases first, all driven through the HTTP boundary (`--suite api`):
  (i) a duplicated **top-level** object key → 400 `BAD_PAYLOAD`;
  (ii) a duplicated key **nested** at depth 2, 3 and inside an array element — the case a top-level-only
  check misses;
  (iii) two key names that are **byte-different but canonically equivalent after JSON string unescaping**
  (e.g. `"ab"` vs `"ab"`, and `"a\tb"` vs the literal tab form) — the check must compare **decoded**
  names, not raw source text, or this class slips through;
  (iv) surrogate / lone high-and-low code points (`\uD800`, `\uDC00`, an unpaired pair) → 400;
  (v) unknown fields, oversized bodies (16 KiB `Content-Length` cap), non-object roots, invalid UTF-8 and a
  wrong content type keep the **existing** 400 `BAD_PAYLOAD` / 415 / `PAYLOAD_TOO_LARGE` contract;
  (vi) **`/v1` compatibility assertion (round-3, I-4)**: the same duplicate-key bodies must be **still
  accepted with last-wins semantics**, proving the strict parser is `/v2`-scoped and global constraint 15
  holds. **The mechanism is fixed and it is not the fixture front.** `lab/fixtures/host_v2_front.php:75-78`
  throws `404 HTTP_404` for any path where `!V2Api::isVersion2Request($rawUri)` — it serves `/v2` and nothing
  else — and §6.1 makes it untouchable, so a `/v1` request "through the fixture front" is unbuildable.
  Instead the case is a **pure `Http::jsonBody()` assertion inside a T1-owned
  `lab/fixtures/host_v2/test_<suite>.php` suite**: build a `Request` with the public constructor
  (`host/src/Http.php:136-141`, which carries `rawBody` and the headers) from a raw body string containing a
  duplicated key plus a JSON `Content-Type` header, call `jsonBody()` (`Http.php:260-298`) on it, and assert
  the decoded value is the **last** duplicate; assert in the same suite that the same body handed to the
  `/v2` path is refused. The two assertions side by side are the `/v1`-unchanged proof, because
  `Http::jsonBody()` plus its six `index.php` call sites are the entire `/v1` body reader (global constraint
  15, ruling 86). No superglobal, no HTTP front, no `/v1` route and no `index.php` change is required or
  permitted. **Waiver path, with an owner:** if that construction cannot be driven (for example because
  `Request`'s constructor requires something the fixture bootstrap cannot supply), T1 stops per §9.3(5),
  records the exact failure, and the **controller** records a waiver against §10 item 14 naming the
  substitute evidence; T1 never substitutes the fixture front and never waives it locally. This test is mandatory, not
  optional — it is the guard against a later attempt to "share" the parser. DDL-only work does not wait on
  this step, but **no Phase C JSON-mutating endpoint (C1–C25) is accepted until it is green**.
- [ ] **Step 6: Register the Phase C suites **together with** their files — one runnable state (C1, ruling
  83).** Because `--suite` enforces `choices=SUITES` (`run_host_v2_tests.py:338`) and a registered name
  without a file exits 2 with `FIXTURE_SUITE_MISSING` (`:355`, `:385`), the registration and the files are
  **one change, never two**: (1) create all seven stub files — `test_crm_queries.php`, `test_crm_core.php`,
  `test_periods.php`, `test_ledger.php`, `test_stages.php`, `test_inquiry_profiles.php`,
  `test_intents.php` — each loading `bootstrap.php` and containing exactly one `t(true, …)`-style passing
  assertion and nothing else. **Round-3, m-1: a stub prints no `FIXTURE_*_PASS` line of its own** — the
  **runner** prints `FIXTURE_<SUITE>_PASS` from the suite's exit status (`run_host_v2_tests.py:389-391`), and
  integration-suite stdout is discarded anyway (`:281-282`); a stub that prints such a line itself produces
  dead output and is not evidence (§6.1's stub row states the same rule); (2) insert **eight** names into
  `SUITES` before `api` (which also updates `_INTEGRATION` through its `:23` slice) — `foundation`,
  `crm_queries`, `crm_core`, `periods`, `ledger`, `stages`, `inquiry_profiles`, `intents` **(round-4, F1:
  `foundation` was missing from this list, so `--suite foundation` was rejected by `choices=SUITES`
  (`:338`) before any file was consulted and the §5.4 claim-sequence gate could never run)**. `foundation`
  is **not** a stub: it binds to `lab/fixtures/host_v2/test_foundation.php`, the file **Step 0(4) already
  created** as T1's own foundation gate — §6.1 states it "is **not** a stub and is never handed off", so no
  other task replaces its content and it never becomes an empty placeholder; (3) add the eight `expected`
  entries at `test_host_v2_fixture.py:234-241` — `foundation` →
  `ROOT / "lab/fixtures/host_v2/test_foundation.php"` plus the seven stub paths — because `suite_path()`
  derives a `_INTEGRATION` suite's file from its name (`run_host_v2_tests.py:118-123`) and the map must
  match it; (4) add matching `BFixture` seed helpers
  for "a tenant with an active account/membership/workspace context" and "an open period". There is **no**
  fallback branch in this step: the names are not deferred "until the owning task lands", and no suite is
  left out of `SUITES`. The seven stubs are the only permitted empty placeholders and only exist until their
  owning task replaces their content; `test_foundation.php` is T1's real suite from the moment it is
  registered.
- [ ] **Step 7: Verify, including inclusion in `all`.** `php -l` on `V2Api.php`, `Http.php`, `Schema.php`,
  `Operations.php`, `index.php`, `test_foundation.php` and the seven stubs; `--suite fixture-guard`;
  `--suite foundation`, `--suite migrations` and `--suite workspaces` (the foundation gate, the foundation
  migration and its `TENANT_CHILDREN` entries, Step 0); `--suite api`; `--suite all`. **Record the observed
  wall time of `--suite all`** (round-3, m-2 — see §7). `--suite all` must exit 0, print
  exactly one `FIXTURE_READY`, and print a `FIXTURE_*_PASS` line for **each of the eight new suites** — the
  presence of those eight lines, `foundation` included, is T1's proof that the registration is real. Record
  that Phase B's five-route
  404/405/415/400 behaviour is unchanged.

**Acceptance:** (a)–(d) RED→GREEN through the production-entrypoint harness, with (d) scoped to the five
Phase B routes and asserting the M1 record **contains** `route`/`status`/`code` (§6.2.1.2); the foundation
gate of Step 0 is green and its migration is applied; `Codes::ALL` is exactly the declared constant set (G5,
`--suite api`); the strict `/v2` parser refuses nested duplicates and equivalent escaped keys while a
`/v1`-shaped duplicate-key body is still accepted last-wins through `Http::jsonBody()`; `--suite all` exits 0
and shows all eight Phase C `FIXTURE_*_PASS` lines (`foundation` among them); `/v1` still serves
unaffected; no `001_initial.sql` or
`gomrok.py` byte change.

---

#### Task 2: CRM query layer (Q1–Q12)

**Files:** Create `host/src/CrmQueries.php`, `lab/fixtures/host_v2/test_crm_queries.php`.

**Consumes:** T1 suite plumbing and the parameterised router (gate B-8); Phase B
`Database`/`Workspaces`/`AccountSessions` contracts; **T6** for Q5/Q6, which delegate to `LedgerCommands`'
single `_tx_countable` helper and sum. Independent T2 query work may be developed after T1 in parallel with
lanes A/B, but **T2's completion gate is after T6** (§6.5).
**Produces:** keyset-paginated, tenant-scoped, filter-validated read services for every Q1–Q12 contract in
§4.1. Routes are **not** registered here — T2 publishes handler entry points and T10 wires them. T2
**replaces** T1's `test_crm_queries.php` stub content; it does not edit `SUITES` or the `expected` map.

- [ ] **Step 1: Write the RED suite.** Cover: happy-path page and second page via `next_after`; `limit`
  bounds (0, 1, 200, 201, "abc", negative); every invalid filter enum; a `q` containing `%`, `_`, `\` and a
  Persian string proving escaping; **cross-tenant isolation for every Q id**, asserted both by direct
  fixture SQL seeding a second tenant and by requesting the foreign id; **missing id, malformed id (wrong
  shape, non-hex, non-hyphenated-UUID, over-long) and foreign-workspace id each return the same 404 body**
  (gate B-8 and §4.0 id grammar); unknown vs. foreign id returning the identical 404 body;
  `include_deleted` behaviour; the `_tx_countable` parity between Q6 and `crm.customer_balance` on the same
  rows; Q8's `available` filter against an `inquiry_results` row in `pending`, in `running` and in a final
  status, plus a soft-deleted row that must **not** reserve; Q11's `inquiry_results` and `profiles` arrays
  built from the closed column sets of §6.2.1.1 and free of credential-class fields; and a fixture with 250
  rows proving the cursor never materialises the whole set.
- [ ] **Step 2: Run `--suite crm_queries`; require genuine missing-behaviour failures.**
- [ ] **Step 3: Implement.** One shared keyset helper using `LIMIT limit+1`; every query carries
  `tenant_id = ?`; no query takes a lock; every referenced id is tenant-resolved and answers 404-foreign
  (§4.0); Q5/Q6 **call** `LedgerCommands`, never re-implement `_tx_countable`; no query returns
  `runner_pid`, browser-visible flags or absolute local paths (Q11 parity restriction).
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion.** `--suite crm_queries` must exit 0
  and `--suite all` must exit 0 with a `crm_queries` `FIXTURE_*_PASS` line (per-task verification contract,
  §6.5). **Assert no query mutates (round-3, I-6), with the snapshot ordered so the fixture's own writes
  cannot invalidate it.** The assertion is partitioned, and the order is the point: (1) seed **everything** the
  suite needs — the second tenant for the cross-tenant probes, the 250-row cursor fixture, and the
  `inquiry_results` rows in `pending` / `running` / final / soft-deleted states — and **commit**; (2) take
  snapshot A: `MAX(updated_at)` (and `MAX(row_version)` where the table has one) of every table Q1–Q12 can
  read — `crm_devices`, `customers`, `owners`, `identities`, `customer_transactions`, `periods`, `settings`,
  `inquiry_results`, `profiles`; (3) run the **full** Q1–Q12 sweep and nothing else; (4) take snapshot B over
  the same table set; (5) assert `A == B` element by element. No seeding, no fixture write and no helper
  call is permitted between (2) and (4) — a helper that seeds mid-sweep is a test defect, not an
  implementation defect. The check therefore fails honestly (a query that wrote shows up as a moved
  timestamp) instead of failing for an unrelated reason, and it is not vacuous, because the seeded tables are
  exactly the ones under assertion.

**Acceptance:** all §4.1 rows satisfied; authorization isolation proven per query; pagination bounds proven;
no writes.

---

#### Task 3: Profiles and inquiry results (C21–C22)

**Files:** Create `host/src/Profiles.php`, `host/src/InquiryResults.php`,
`lab/fixtures/host_v2/test_inquiry_profiles.php`.

**Consumes:** **T9a** — the `inquiry_results` and `profiles` tables of the domain migration `005_*` (§6.2.1.1). T3 is
**prohibited from adding DDL** and does not edit `Schema.php` or any migration (§6.1). If T9a's tables are
not on the fixture DB, T3 does not start; that is the schema-prerequisite edge of lane C (§6.5).
**Produces:** bounded inquiry-result recording (no network, no robot) and profile create/link/unlink, plus
the `mark_ready` gate lookup T8 will call. T3 **replaces** T1's `test_inquiry_profiles.php` stub content.

- [ ] **Step 1: RED suite.** Vocabulary rejection (gate B-9): every value in the closed PHP sets is accepted,
  every value outside them is 400 with a field-named message and **no** silent fallback or defaulting, and a
  **parity test** pins the PHP sets to their Python sources — `service ∈ {'gomrok','hamta'}` as read by
  `_inquiry_gate_errors` (`crm.py:1496-1521`), `status` against `panel.py:_ESTELAM_FINAL`
  (`panel.py:3672-3673`) plus `pending`/`running`, and the pass predicate against `_GOMROK_PASS`
  (`{"healthy"}`) / `_HAMTA_PASS` (`{"good","registered","success"}`) at `crm.py:1492-1493`, so a Python
  vocabulary change fails the PHP suite. Also: unknown device 404 **and** foreign-tenant device 404 with a
  byte-identical body (§4.0); `UNIQUE (tenant_id, device_id, service)` — a second record for the same
  device+channel is an update under the row lock, never a second row; `identity_id` recorded on write and
  honoured by C5's delete refusal (409 while a non-deleted row references it, and no cascade); unlink
  disables rather than deletes (`crm._disable_detached_profile`, `crm.py:1425` parity) and the `profiles`
  row survives with `enabled = 0`; deleting a profile that a non-deleted `crm_devices.profile_id` still
  points at is refused (§5.3); a local-only `profile_id` with no `profiles` row is still a legal device
  state (the dangling-by-design contract); **no credential-class column is ever written or returned**; and
  **assert the process opens no socket** (the suite must fail if a network call is attempted).
- [ ] **Step 2: Run `--suite inquiry_profiles`; require failures.**
- [ ] **Step 3: Implement** the two services and the gate lookup returning blocking reasons in English,
  inside the §6.4 lock order and the §4.0 `withWorkspace` signature.
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5): `--suite inquiry_profiles` exits 0 and `--suite all` prints an `inquiry_profiles`
  `FIXTURE_*_PASS` line.

**Acceptance:** C21/C22 contracts hold against the `005_*` domain tables of §6.2.1.1; the gate lookup is the single
source T8 uses; the identity reference is written and enforced; no egress; no DDL, no `Schema.php` edit.

---

#### Task 4: Customers, owners and identities (C1–C5)

**Files:** Create `host/src/CrmCommands.php`, `lab/fixtures/host_v2/test_crm_core.php`.

**Consumes:** T1. T4 creates `CrmCommands.php` and therefore precedes T9a on the backbone (§6.5).
**Produces:** the command base — `$context` handling, the shared `assertRowVersion`, the Phase C audit
write helper — plus C1–C5. T4 **replaces** T1's `test_crm_core.php` stub content; T5 then extends it.
The domain migration's account actor columns do not exist yet at T4 (T9a runs after T4 on the backbone), so
T4 may omit the Phase C actor columns **ONLY while implementing against the pre-005 schema** — this is
temporary implementation sequencing, not a Phase C acceptance exception, and it has a **named owner: T9b**
(ruling 96). The exemption **EXPIRES at T9b completion**: T9b retrofits every T4 mutation surface (customers,
owners, identities) to write the authenticated acting account id into the applicable Phase C actor columns,
and **Phase C cannot be accepted or released with any T4 mutation path still exempt**. T4's actor-column
assertions are therefore deferred to T5 (after T9a) and T9b, and §4.2's five conditions continue to govern the
**completed** implementation, not this intermediate state. T4 does consume T1's foundation migration and
`Operations.php` — that is the point of round-3, C-1.

- [ ] **Step 1: RED suite.** Validation matrices ported from `crm.validate_customer/owner/identity`
  (`crm.py:259,275,319`) including `national_checksum_ok`, card internal-space normalization, and
  `test_update_owner_survives_row_without_card_no` parity; **round-3, m-11: an owner with no `mobile` is
  refused (`crm.py:301-303`), an owner whose `passport` contains a non-latin or non-alphanumeric character is
  refused (`crm.py:313-315`), and a customer with no `mobile` is accepted (`crm.py:266-268`)** — the two
  surfaces differ and the matrix proves it; and **round-3, m-10: `telegram_id`/`instagram_id` are asserted
  against this plan's ≤64 rule only, never against `crm.validate_customer`**; **stale-write**: two
  connections, second update
  with a stale `expected_row_version` → 409 whose message names `{expected, actual}` in that order (RB-1
  analogue); owner delete while bound → refusal; customer delete with active transactions and
  `force=false` → refusal; customer delete **writes no ledger row**; C5's identity delete refusal against a
  seeded `inquiry_results.identity_id` reference (409), and the C5 foreign-tenant `device_id`/reference case
  answering 404-foreign (§4.0).
- [ ] **Step 2: Run `--suite crm_core`; require failures.**
- [ ] **Step 3: Implement**, entering every command through the real
  `AccountSessions::withWorkspace($token, $purpose, $now, $tenantId, $expectedContextVersion, $capability,
  $fn)` signature of §4.0 and the §6.4 lock order, with the callback doing SQL work only.
- [ ] **Step 4: Run the suite plus `--suite all` and prove inclusion** (per-task verification contract,
  §6.5); `php -l` the new file.

**Acceptance:** C1–C5 contracts hold; version conflict proven under two connections; no legacy actor FK
write.

---

#### Task 5: Devices and owner binding (C6–C10)

**Files:** Extend `host/src/CrmCommands.php`, `lab/fixtures/host_v2/test_crm_core.php`.

**Consumes:** T4 and **T9a** (the domain migration `005_*`'s serial slot ledger, ownership unique key and
account actor columns — T5 must not run against a schema that lacks them).
**Produces:** device CRUD with serial rules, 1:1 owner attach/detach, and the conditional fee-refund
deletion path.

- [ ] **Step 1: RED suite.** Serial uniqueness within tenant (and **non**-uniqueness across tenants);
  **cross-slot collision** — a device whose `serial1` equals another device's `serial2` is refused
  (gate B-10, I1); **round-3, I-5: a 14-digit and a 16-digit `serial1` are each refused, and a 15-digit one
  accepted** (`crm.py:393-395`); `serial2` unique only when non-empty and, when set, also exactly 15 digits; a device whose `serial1` equals its own `serial2`
  is refused (`crm.py:396-397`); the slot ledger row appears and disappears with the device row in the same
  transaction; serial immutability with the fee-reversal message; require-period refusal when no period is
  open; owner bound twice → refusal naming no other device's id, including the two-connection race where the
  second writer hits the unique key and must surface 409 rather than 500; device delete with an active
  `base_fee` row books exactly one refund; delete with the refund feature disabled books none; **double
  delete cannot double-refund** (`test_double_delete_cannot_double_refund` parity); delete rolls back
  profile-disable and inquiry-removal when the ledger write fails; **soft delete writes `deleted_at` and the
  Phase C account actor column and leaves legacy `deleted_by` NULL**; and the §9.2 BC-6 owner-binding
  decision is asserted exactly as recorded (legacy `crm.delete_device` hard-removes and therefore frees the
  owner, `crm.py:1388-1411`).
- [ ] **Step 2: Run `--suite crm_core`; require failures.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5: the named suite exits 0 **and** `--suite all` prints its `FIXTURE_*_PASS` line).

**Acceptance:** C6–C10 contracts hold; I1, I2, I11 proven; rollback proven.

---

#### Task 6: Ledger commands and balance parity (C16–C17, Q5/Q6 services)

**Files:** Create `host/src/LedgerCommands.php`, `lab/fixtures/host_v2/test_ledger.php`.

**Consumes:** T4 (customers exist in fixtures) and T9a (Phase C account actor columns for the ledger audit).
**Produces:** append-only ledger writes, void-with-reversal, half-pair guard, and the single balance
predicate used by Q5/Q6 — exported as `LedgerCommandsContract::countable()` so **T2's Q5/Q6 call it and
never re-implement it** (§6.2, §6.1). T6 owns `LedgerCommands.php` alone; T6 **replaces** T1's
`test_ledger.php` stub content, and T2's `crm_queries` completion gate stays after T6 (§6.5 lane B).

- [ ] **Step 1: RED suite.** Port `test_crm_ledger_integrity.py` and `test_v1_6_accounting.py` cases that
  apply to the host path: typo-correction is fee-neutral; refund targets the customer holding the fee row;
  done-rollback and re-completion leave the ledger untouched; `base_fee`/`base_fee_refund` half-pair
  refusal; re-void refusal; voiding a `void` row refusal; **two connections voiding the same row → exactly
  one success and one refusal, never two reversals**; `seq` monotonicity under two concurrent inserts;
  amount digit normalization including Persian digits; non-integer and overflow rejection; balance equals
  `crm.customer_balance` for a fixture containing voided rows, `void` rows and a row with a malformed `at`.
- [ ] **Step 2: Run `--suite ledger`; require failures.**
- [ ] **Step 3: Implement**, locking the `customers` row before any `customer_transactions` row.
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5: the named suite exits 0 **and** `--suite all` prints its `FIXTURE_*_PASS` line).

**Acceptance:** C16/C17 contracts hold; I6–I9 proven; concurrency proven.

---

#### Task 7: Periods and settings (C18–C20)

**Files:** Extend `host/src/CrmCommands.php`, `lab/fixtures/host_v2/test_periods.php`.

**Consumes:** T5 (lane A predecessor; `CrmCommands.php` ownership chain §6.1). T7 **replaces** T1's
`test_periods.php` stub content.
**Produces:** period open/close with monotone numbering, and settings updates.

- [ ] **Step 1: RED suite.** Period `number` monotone across 25 opens in two interleaved connections with
  exactly one winner per number; only one open period; close of an already-closed period refused; settings
  bounds (`watch_interval_hours` 0/1/72/73, flag values, `current_period_id` cross-tenant 400/404); links
  array shape rejection; **C18/C19** period behaviour asserted against `crm.open_period`/`close_period`
  parity (`crm.py:934,991`) using `_fa_ordinal`-independent numeric assertions. **Round-3, m-5: this task
  does not assert Q9 or Q10.** Those services are `CrmQueries.php` (T2's file) and their HTTP contract is
  T10's — T7 would have to edit `CrmQueries.php` or register routes to assert them, both forbidden. Period
  and settings **query** assertions belong to T2's `crm_queries` suite (Q9/Q10 rows of §4.1) and to T10's
  HTTP suite, and no `T7 → T2` or `T7 → T10` edge is introduced.
- [ ] **Step 2: Run `--suite periods`; require failures.**
- [ ] **Step 3: Implement** with the allocation-under-lock rule (§6.4 **step 7** — allocation reads happen
  under the step-4 tenant lock or the step-6 customer lock; round-3, m-6).
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5: the named suite exits 0 **and** `--suite all` prints its `FIXTURE_*_PASS` line).

**Acceptance:** C18–C20 contracts hold; I10 proven under concurrency.

---

#### Task 8: Stage machine (C11–C15)

**Files:** Extend `host/src/CrmCommands.php`, `lab/fixtures/host_v2/test_stages.php`.

**Consumes:** T5 and T7 (the `CrmCommands.php` ownership chain, lane A) **and T3** (the inquiry gate,
lane C). T8 is the only task with a **cross-lane** edge: it does not start until **both** are complete.
T8 **replaces** T1's `test_stages.php` stub content.
**Produces:** the complete stage command surface with gates, rollback and skip provenance.

- [ ] **Step 1: RED suite.** Port `test_crm_stage_transitions.py` and the stage cases of
  `test_crm_case_integrity.py` / `test_crm_ledger_integrity.py`: every row of `ALLOWED` succeeds from its
  stage and every disallowed pair refuses; `stage_back` clears exactly the stamps of the stages left,
  including `done_at`; `closed` is terminal; `stage_skip` records provenance and **no skip path reaches
  `done`**; `problem` re-enters the mainline; `handover` without an owner refuses; `mark_ready` with an
  incomplete inquiry refuses naming the service; a repeated `handover`/`confirm_payment`/`deliver_code`
  refuses as already confirmed; `close_case` requires a reason; **two connections advancing the same device
  to the same target → exactly one wins**.
- [ ] **Step 2: Run `--suite stages`; require failures.**
- [ ] **Step 3: Implement** using the single vocabulary table from `CrmCommands`, never re-typed literals.
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5: the named suite exits 0 **and** `--suite all` prints its `FIXTURE_*_PASS` line).

**Acceptance:** C11–C15 contracts hold; I3–I5 proven; concurrency proven.

---

#### Task 9: Domain migration (T9a) then intents and operation recovery (T9b) — C23–C25, I12, I13

**Split, because the gates made the schema half load-bearing for other tasks.** T9a must finish before T3,
T5 and T6 are dispatched (§6.5 lanes A/B/C); T9b runs after T9a and after T4.

**T9a — schema half.**
**Files:** create the next free `host/migrations/00N_*.sql` (**expected `005_*`** — `004_*` is T1's foundation
migration, round-3, C-1); extend `host/src/Schema.php`; extend
`lab/fixtures/host_v2/test_migrations.php` and `test_integrity.php`; extend
`lab/fixtures/host_v2/test_workspaces.php`'s `TENANT_CHILDREN` (`:105-113`, M9); and the **one additive**
change to `host/src/Migrations.php` (and, if the dispatch shape requires it, `MigrationPreflight.php`) that
installs the **domain** migration's counts-only pre-migration detector (§5.3, I4).
**Consumes:** T1 (suite plumbing **and the foundation migration `004_*` already applied on the fixture DB**)
and T4 (T4 precedes T9a on the backbone; T9a's detector must tolerate a
`crm_devices` table that T5 has not yet extended).
**Produces:** the domain migration `005_*` containing, unconditionally (gate B-10, expanded by ruling 84), in
the exact content of §6.2 and the column-level contract of §6.2.1.1: the Phase C **account actor columns** for
mutation audit and soft deletes; the I1 `device_serial_slots` unique ledger; the I2 active-ownership unique
key; the `UNIQUE (tenant_id, id)` enabling key on `identities` that no existing migration supplies (parent-key
rule, round-3, I-1); the **host-owned `profiles`** and **`inquiry_results`** tables; `Schema.php` metadata and
`Schema::migrationSteps()` entries for all of them; the extended fixture delete order; and the pre-migration
violation detection for I1/I2 plus the advisory profile-coverage count. The dedicated **operations** and
**intent** tables are **not** T9a's — they are T1's foundation migration `004_*` (round-3, C-1, ruling 91),
which creates them, registers them in `Schema.php` and covers them in `TENANT_CHILDREN`; T9a never creates,
drops or retypes them, and its `Schema.php` edit is additive only.

- [ ] **Step 1: RED.** Extend `test_migrations.php`: a case asserting `005_*` refuses to install the I1/I2
  constraints when the fixture seed contains a cross-slot serial collision, a self-colliding device or a
  doubly-bound owner — counts-only report, no repair, no deletion, no truncation; a case asserting the
  advisory `profile_id`-coverage count is reported and does **not** block; a case asserting all four Phase C
  tables exist with the keys of §6.2.1.1 (`inquiry_results`'s `UNIQUE (tenant_id, device_id, service)`,
  `profiles`'s `PRIMARY KEY (tenant_id, id)`, the operations/intents uniques — the last two created by T1's
  foundation migration, present because `004_*` has already been applied); a case asserting the new tables
  are in `Schema::tables()` so the `tableCount($ddl) === count(Schema::tables())` assertion at
  `test_migrations.php:567` still holds, **and** that the fixed step inventory at `:551-568`
  (`$verifiedSteps === 27` over `001_initial => 1`, `002_identity_foundation => 16`,
  `003_scoped_business_keys => 10`) now declares `005_*`'s step count **and** the matching new total — that
  inventory is a hard-coded three-file expectation and both new migrations must extend it; a case extending
  `TENANT_CHILDREN` and proving a seeded tenant still deletes cleanly; and a case
  asserting the migration is additive (journal entry, checksum skip, drift refusal, crash reconciliation)
  with `005_*` present.
- [ ] **Step 2: Run `--suite migrations` and `--suite fixture-guard`; require failures.**
- [ ] **Step 3: Implement** the migration, the `Schema.php` metadata, the `TENANT_CHILDREN` extension and
  the additive pre-migration detector, following the conventions of `003_scoped_business_keys.sql` (stable
  `-- irica-step:` markers, one statement per step, parent unique keys before composite FKs). **No FK over
  `crm_devices.profile_id`** (§5.3, §6.2.1.1). No service code in T9a, and no change to any existing
  `Migrations`/`MigrationPreflight` behaviour.
- [ ] **Step 4: Run `--suite migrations`, `--suite integrity`, `--suite all`, and prove inclusion** (the
  `migrations` and `integrity` lines appear in `--suite all`). Assert the pre-existing journal, checksum,
  drift and crash-reconciliation behaviours still hold **unchanged**, and that `001_initial.sql`,
  `002_identity_foundation.sql` and `003_scoped_business_keys.sql` are byte-unchanged.

**T9a acceptance:** `005_*` applies on the fixture DB with the domain tables and the I1/I2 constraints,
while T1's `004_*` tables are already present and untouched; the
pre-migration detector blocks a seeded I1/I2 violation and reports counts only, and reports (without
blocking) the profile-coverage gap; `Schema.php` and the DDL move together; the fixture delete order covers
the new tables; `Migrations`/`MigrationPreflight`'s 003 behaviour is demonstrably unchanged; duplicate-key
races on the new unique keys are demonstrably possible at the DB layer (ready for T5's 409 translation
tests); nothing existing is rewritten; `idempotency_keys` is untouched.

**T9b — service half.**
**Files:** create `host/src/CommandIntents.php` and `lab/fixtures/host_v2/test_intents.php`.
`host/src/Operations.php` is **T1's file** (round-3, C-1): T9b **calls** its claim / write-back / replay /
lookup API and
never edits it.
**Consumes:** T1 (codes **and** `Operations.php` with the foundation migration applied), T4 (the mutation
surfaces to retrofit) and **T9a** — T9b **depends on T9a**: the Phase C account actor columns exist only from
the domain migration `005_*`, so T9b cannot begin before T9a lands.
**Produces:** the intent state machine, the operation record and replay, Q13/Q14 services, the
no-legacy-actor-FK proof, the credential-redaction negative test, and — **ruling 96** — the **T4
account-actor retrofit**: every T4 mutation surface (customers, owners, identities — C1–C5) writes the
**authenticated acting account id** into the applicable Phase C actor columns **atomically with the domain
mutation**, in the same transaction or not at all. **Ownership identity must never substitute for acting-account
identity**: an owner's or customer's own `id`, `national`, or any other business field is never used as, or
inferred to be, the account that acted. **Historical data:** pre-retrofit rows are backfilled **only** where
existing authoritative records establish the correct acting account **deterministically**; attribution is
**never guessed from current ownership and never fabricated**. Rows that cannot be attributed deterministically
are classified as **legacy data**, left unattributed and reported by count as such; every **subsequent**
mutation on those rows must satisfy the actor contract in full.

- [ ] **Step 1: RED suite.** Intent state machine: every legal transition succeeds and every illegal one
  refuses; terminal states refuse further transitions; C24 accepts only `settled`/`rejected` and answers
  400 `INTENT_STATE_INVALID` otherwise (§3.1 C24); same `transport_id` + same digest is idempotent and
  returns the stored body; same id + different digest → `IDEMPOTENCY_MISMATCH`; unknown id is a new command,
  not a replay; **a committed intent is never executed twice under a new transport id** while an explicitly
  new repeat with a fresh id is accepted; Q13/Q14 are tenant-scoped (foreign operation → 404 **and** foreign
  intent → 404, byte-identical to unknown, §4.0); the operation row is written **atomically with the
  domain write** (a forced mid-transaction failure leaves neither); every operation row carries the
  authorization scope, the command fingerprint **and** the `session_id` resolved per gate B-5; retries
  classified transient reach at most 3 attempts and non-transient classes are never retried.
  **Round-2, I5 — the replay race:** two connections issue the **same** `transport_id` and the same request
  hash concurrently; the test requires exactly one domain mutation, one operations row and one intent row,
  the second request returning the first's stored body. **The lock assertion must isolate the operations
  lock (round-3, I-8): "the second transaction blocks until the first commits" is NOT sufficient evidence**,
  because §6.4 step 3 already takes the tenant row `FOR UPDATE` (`Workspaces::lockTenantRow(..., true)`,
  `host/src/Workspaces.php:189-198`, called at `:289`), so two same-tenant requests serialise *before* step 5
  even exists — that observation is equally consistent with the tenant lock and would still pass if §5.4's
  `FOR UPDATE` were deleted. Instead assert the operations lock directly, with one of: (a) **two raw
  connections driving `Operations.php`'s claim path with no tenant gate at all** — connection A holds the
  claim transaction open, connection B's claim blocks, and B unblocks and reports the stored body only after
  A commits (this is the discriminating case: there is no step-3 lock to explain the block); or (b) a lock-wait
  assertion from `performance_schema.data_locks` naming the operations table's row lock while A is open and
  showing it gone after A commits. Whichever is used, the test also records that the tenant lock was already
  released or was never taken in that path, so the two locks cannot be confused. A variant with the **same** id and a **different** hash must yield exactly one
  `IDEMPOTENCY_MISMATCH` and zero domain mutations.
  **Round-2, R1 — `idempotency_keys` is read-only for Phase C:** a direct-SQL assertion that after the whole
  suite the table's row count and content for the run's tenant are unchanged by every Phase C path, and a
  scan assertion that no `host/src/Crm*.php`, `LedgerCommands.php`, `Operations.php` or `CommandIntents.php`
  statement names `idempotency_keys`. **I12:** a direct-SQL assertion that no Phase C write path populated
  `audit_events.actor_user_id` or `change_log.actor_user_id` with an `accounts.id`. **I13:** a direct-SQL
  assertion that no Phase C write populated a business row's `deleted_by` with an account id, and that
  `registration_chat_id` / `report_chat_id` never appear in a Q13/Q14 body, an error body or a `Log` line.
- [ ] **Step 2: Run `--suite intents`; require failures.**
- [ ] **Step 3: Implement** both services against the Phase C tables of §6.2.1.1 — the operations and intent
  tables of T1's foundation migration `004_*`, **and** the domain tables of T9a's migration `005_*`, which the
  ruling-96 actor retrofit writes (round-4, F2: the domain half of this step is `005_*`, not `004_*`) —
  entering commands through the real
  `withWorkspace` signature of §4.0 and writing the operations row first under its lock (§5.4, §6.4 step 5).
  T9b **replaces** T1's `test_intents.php` stub content; it never edits `SUITES`, the `expected` map or
  `idempotency_keys`.
- [ ] **Step 4: Run the suite plus `--suite all`, and prove inclusion** (per-task verification contract,
  §6.5).
- [ ] **Step 5: Assert the settled timeout-after-commit posture (gate B-3).** Prove by test that (a) a
  command whose response was never delivered is still resolvable through Q13 by its caller-known
  `operation_id`, (b) the stored outcome is either the committed result/effect references or a classified
  pre-commit failure, (c) the command layer asserts nothing about delivery, and (d) no new status code,
  error code or body shape was invented for the timeout case.

**T9b acceptance:** C23–C25 contracts hold; I12 and I13 proven; the operation record is atomic with the
business mutation; Q13 resolves an undelivered-response command; the migration is additive and recoverable.

**T9b acceptance gate — T4 account-actor retrofit (ruling 96).** All of the following must hold with
behavioural and direct-SQL evidence, or T9b is not accepted and Phase C cannot be accepted or released:

- **All T4 mutation paths.** Every T4 mutation surface — C1, C2, C3, C4 (create/update/delete owner) and C5
  (create/update/delete identity) — writes the applicable Phase C actor columns on an authenticated mutation.
  A scan assertion over `host/src/CrmCommands.php` proves no T4 mutation path still leaves them unset, i.e.
  that the pre-005 exemption is gone.
- **Actor provenance.** The value written is the authenticated **acting account id** resolved from the
  admitted session inside the mutation transaction, with the exact session id alongside it. A direct-SQL
  assertion proves no Phase C actor column was populated from a business-row identity field: ownership
  identity never substitutes for acting-account identity.
- **Transactional rollback.** A forced failure inside the mutation rolls the domain write **and** the actor
  write back together: no row survives with a domain change and a missing or stale actor, and none with an
  actor value and no domain change.
- **Replay preserves the ORIGINAL attribution.** Re-driving a settled command through the C25/Q13 replay path
  returns the stored body and does **not** overwrite or re-stamp the actor columns with the replaying
  session's account id; the originally acting account remains the recorded actor.
- **Upgrade through migration 005.** On a fixture DB with `004_*` applied and `005_*` not yet applied,
  applying `005_*` then running every T4 path yields populated actor columns; the retrofit is exercised
  against the post-005 schema end to end.
- **Historical data.** Backfilled attribution exists only where an authoritative record establishes the acting
  account deterministically; every unattributable row is classified as legacy data and reported by count,
  never guessed from current ownership and never fabricated, and every subsequent mutation on such a row
  satisfies the actor contract.

---

#### Task 10: Route registration, HTTP contracts and Phase C acceptance

**Files:** `host/src/V2Api.php` (route registration **and** the `logLabel()`/`knownRoute()` extension of
§6.2.1.2), `host/src/Http.php` (code reconciliation only), `lab/tests/test_host_v2_api.py`,
`lab/fixtures/host_v2/bootstrap.php`, a handoff document in `lab/docs/` mirroring Phase B's
`HOST_IDENTITY_PHASE_B.md`, and — **round-3, m-8** — the Step 6 evidence pack written to
`.superpowers/sdd/2026-10-11-host-identity-integrity/phase-c-evidence-pack.md`. That path is inside
`.superpowers/sdd/`, which `.superpowers/sdd/.gitignore` ignores (`*`), so the pack is a **local artifact,
do-not-commit** and never part of the task's scoped diff; T10 cites it by path and does not stage it.

**Consumes:** T1–T9 (all seven lanes complete).
**Produces:** every §3.1 route registered and reachable over the fixture HTTP front; the full HTTP contract
suite; the labelled M1 line for every registered Phase C route; the Phase C handoff listing gaps, unrun
gates and NOT-done work.

- [ ] **Step 1: RED HTTP cases.** For every route: exact 404 for unknown path, 405 for wrong method, 415
  for a wrong content type, 400 for an empty/non-object/oversized body, unknown-field refusal, 401 without
  a token, `PASSWORD_CHANGE_REQUIRED` for a forced-change session, `SESSION_PURPOSE_MISMATCH` for a
  wrong-purpose token, 403 for a member calling a `delete`/`settings`/`period_admin` capability, 409 for a
  stale `workspace_context_version`, `no-store` and no `Set-Cookie`/CORS on every response, and the
  `Codes::ALL` agreement, and the T1 strict-boundary cases (duplicate top-level key, nested duplicate,
  equivalent escaped key) re-run **per Phase C route** so no registered route bypasses the `/v2` parser. For
  every **parameterised** route additionally: missing id, malformed id (non-UUID-shape) and
  foreign-workspace id each return the identical 404 body; an exact static route still wins; and a path with
  `%`, `..`, `//` or `\` in it is 404 before any handler runs (gate B-8). For C23 additionally: a member
  calling the C23 form of a `delete`/`settings`/`period_admin` command is refused with the **underlying
  command's** capability check (round-2, M6), not an `edit` shortcut.
- [ ] **Step 2: Run `--suite api`; require failures.**
- [ ] **Step 3: Register the routes** in `V2Api::ROUTES` and `route()` — the static entries plus the
  parameterised `/v2/crm/.../{id}`, `/v2/crm/periods/{id}`, `/v2/crm/devices/{id}/invoice` and
  `/v2/operations/{operation_id}` forms — mapping each to the owning service; reconcile any code constant
  discovered during T2–T9 (adding the constant **and** its `Codes::ALL` entry together, then re-running
  `--suite api`). In the same change, **extend `logLabel()` and `knownRoute()`** to recognise the registered
  parameterised patterns by pattern-match against the same table `route()` uses — never by echoing the
  request path — so every registered Phase C route produces a label and nothing else becomes labelable
  (§6.2.1.2). `/v1` is not touched.
- [ ] **Step 4: Prove `/v1` isolation (gate B-4) — both directions.** With a Phase C mutation committed, run
  (a) an incremental `/v1` sync request and assert the `change_log` cursor does not advance and no Phase C
  row appears, **and** (b) a snapshot/bootstrap `/v1` read and assert no Phase C row is present. Omitting
  log entries alone is not evidence; both the incremental cursor and the bootstrap payload are asserted.
- [ ] **Step 5: Run `--suite all`, `--suite fixture-guard`, and prove every suite's inclusion.** `--suite all`
  must exit 0 with one `FIXTURE_READY` and a `FIXTURE_*_PASS` line for each of the eight Phase C suites plus
  the Phase B suites — this is the closing proof that every owning task's suite is registered and wired
  (per-task verification contract, §6.5). Verify unchanged `001_initial.sql` and `gomrok.py` hashes, no
  secrets/debug/generated/live data, no `/v1` behavioural change, no drop/truncate, and no new account id in
  a legacy `actor_user_id` FK or legacy `deleted_by` column.
- [ ] **Step 6: Assemble the release-gate evidence pack (not a release).** Record, with the exact commands
  and outputs: (a) the G1 production-entrypoint binding from `lab/fixtures/host_v2/index_binding.php`,
  including its mutation check (§6.2.1.2); and (b) **one M1 access-log line per registered Phase C route**,
  covering success, denial and failure, read from the harness's own log path and labelled by the
  `logLabel`/`knownRoute` extension of Step 3 — not `<unknown>`. If the harness could not be built, record
  the exact failure and the controller's waiver against §10 item 14 instead of substituting the fixture
  front. These are required before Phase C production acceptance and release; their presence here does not
  authorize either.
- [ ] **Step 7: Write the handoff** listing: Phase C coverage gaps, unrun gates by name, NOT-done work for
  D/E/F, the documented divergences (BC-6 owner binding, the host-owned `profiles`/`inquiry_results`
  mirror, the dangling `crm_devices.profile_id`), and the open items from §10 that remain unresolved.

**Acceptance:** every §3.1 route answers its §4 contract over HTTP; parameterised and static routing obey
the precedence and id rules of gate B-8 with `isServable()` unchanged; `/v1` incremental **and**
snapshot/bootstrap visibility prove isolation from Phase C mutations; `--suite all` is green with every
Phase C suite line present; the G1/M1 evidence pack exists and M1 lines are labelled, never `<unknown>`;
the handoff is honest about what is not covered.

**End-of-task rule for every task above.** The worker reviews only its own scoped diff, records the exact
commands it ran with their real exit results, confirms the byte pins (`001_initial.sql`, `gomrok.py`), and
stops under §9.3 rather than widening scope. Commit, push and release are never part of a task's Definition
of Done.

## 7. Verification

**Commands (discovered from the repository, never invented).** From `lab/scripts/run_host_v2_tests.py`,
whose `SUITES` tuple at line 22 is `("fixture-guard", "migrations", "integrity", "workspaces", "accounts",
"sessions", "api", "all")`:

```
"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite fixture-guard
"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite <name>
"C:/Program Files/Python310/python.exe" -I -B lab/scripts/run_host_v2_tests.py --suite all
php -l host/src/<File>.php
```

`<name>` is one of the **eight** Phase C suite names T1 adds (`foundation`, `crm_queries`, `crm_core`,
`periods`, `ledger`,
`stages`, `inquiry_profiles`, `intents`) or a Phase B suite (`migrations`, `integrity`, `workspaces`,
`accounts`, `sessions`, `api`). Diff and integrity checks use ordinary `git status` / `git diff --name-only`
plus the pinned `gomrok.py` SHA-256 of global constraint 11.

**Prerequisites.**

- **Fixture database.** `--suite fixture-guard` runs first in every task and must exit 0. It validates the
  fixture target with no file, process or socket access first (`validate_fixture`,
  `run_host_v2_tests.py:36`), and a gate is never green without exactly one `FIXTURE_READY
  driver=… server=… tables=…` record (`run_host_v2_tests.py:127,268`). The engine is MariaDB 10.3.39 on a
  disposable fixture datadir. If the fixture server is dead, restart it the Phase B way (ruling 75): the
  provisioned datadir `%TEMP%/irica-b-fixture-00d0fe30d2a20095/data` plus **its own** `server.ini` — the
  pristine vendor datadir answers `Access denied`. The runtime fixture account cannot perform DDL; the
  separate synthetic DDL account is fixture-scoped only (global constraint 19).
- **Loopback front.** `--suite api` needs a listening loopback front: the suite drives
  `lab/fixtures/host_v2_front.php` at `http://127.0.0.1:8766` (`lab/tests/test_host_v2_api.py:41,45`) and
  **rejects any other origin outright** (`test_host_v2_api.py:9`). The G5 `Codes::ALL` agreement assertion
  lives inside that suite at `test_host_v2_api.py:935` (`test_d03_codes_all_equals_the_declared_constant_set`),
  so it runs only when the front is listening. Port
  8766 is closed after a completed run (ruling 76).
- **Unrun gates stay unrun.** `lab/tests/test_host_api.py`, `test_host_schema.py`, `test_create_admin.py`,
  `lab/scripts/run_panel_tests.py`, `run_online_feasibility.py` and `lab/tests/test_online_*` are never
  part of Phase C verification, and MySQL 5.7 is never run.

**Which suite each task runs.** Every row's `all` entry carries the round-2 C1 obligation: the task's own
`FIXTURE_*_PASS` line must appear in `--suite all`'s output, which is what proves the name is registered and
the file exists — running the named suite alone proves neither.

| Task | Suites beyond `fixture-guard` | Plus |
| --- | --- | --- |
| T1 | `foundation`, `migrations`, `workspaces`, `api`, `all` (Step 7's order; `all` must show all **eight** new suite lines, round-4, F5) | `php -l` on `V2Api.php`, `Http.php`, `index.php`, `test_foundation.php`, the seven stubs and `index_binding.php` |
| T2 | `crm_queries`, `all` | completion gate after **T6** (§6.5 lane B) |
| T3 | `inquiry_profiles`, `all` | requires **T9a**'s `migrations` green first (§6.5 lane C) |
| T4 | `crm_core`, `all` | `php -l host/src/CrmCommands.php` |
| T5 | `crm_core`, `all` | requires T9a's `migrations` green first |
| T6 | `ledger`, `all` | requires T9a's `migrations` green first |
| T7 | `periods`, `all` | requires T5 complete (ownership chain) |
| T8 | `stages`, `all` | requires T7 **and** T3 complete (cross-lane edge) |
| T9a | `migrations`, `integrity`, `all` | byte-checks on `001_initial.sql`, `002_identity_foundation.sql`, `003_scoped_business_keys.sql`, `gomrok.py` |
| T9b | `intents`, `all` | direct-SQL I12/I13 assertions |
| T10 | `api`, `all`, `fixture-guard` | hash pins, `/v1` incremental **and** snapshot/bootstrap checks |

**Per-suite timeout budget (round-3, m-2).** The runner gives every suite **120 s**
(`run_host_v2_tests.py:273,281`) and aborts the whole run on the first non-zero status. After T1's
registration `--suite all` selects `_INTEGRATION = SUITES[1:-1]` (`:23`), which becomes **14 suites** once
T1's eight names are added (round-4, F1 — the seven stub suites plus `foundation`), so the
worst-case wall time is 14 × 120 s ≈ 28 minutes plus startup, and a suite that times out is reported as
`FIXTURE_<SUITE>_FAIL` — **the same label as a behavioural failure**, with no separate timeout signal. Two
consequences bind every task: (i) T1 records the **observed** wall time of `--suite all` in its task report,
so later tasks have a real baseline rather than an estimate; (ii) a `*_FAIL` line is never assumed to be a
behaviour failure — the worker reads the suite's own output and the exit code before classifying it, and a
timeout is a §9.3 stop with the exact duration recorded, not a pass and not a silent reclassification.

**RED/GREEN expectations.** Every task writes its failing tests before implementation (Step 1) and runs them
(Step 2) to obtain **genuine** failures that name the missing behaviour (§6.5). RED means a real assertion
that fails on the current tree; a suite that cannot start, that is skipped, or that is reported without its
`FIXTURE_READY` record is **never** GREEN. GREEN means the same suite exits 0 after implementation, with
`--suite all` additionally printing one `FIXTURE_READY` and one `FIXTURE_*_PASS` line per integration suite
**including the task's own suite** (round-2, C1). A suite that exits 2 with `FIXTURE_SUITE_MISSING`, a
suite whose file is still T1's stub, or a task that substitutes another suite for its named one is a
§9.3(9) stop — never a pass and never a partial pass. No task claims a whole-product PASS, and no unrun gate
is counted green.

## 8. Safety and recovery

- **Local and fixture-only by default.** Every command in §7 runs against the disposable fixture database
  and the loopback front. There is no deployment, no production or external testing, no customer data, no
  panel local data, no `host/config/config.php`, and no network egress of any kind (§0, global constraints
  3, 12, 13).
- **`/v1` stays served and unchanged.** Phase C adds a `/v2` surface only; global constraint 4 and gate B-4
  keep `/v1` serving its existing contract, and T10 asserts that Phase C mutations are invisible to both the
  incremental `change_log` cursor and the snapshot/bootstrap read.
- **MySQL 5.7 is BLOCKED and UNRUN.** Nothing in Phase C infers 5.7 compatibility from MariaDB 10.3.39
  results or from `php -l` (global constraint 16). If a task discovers a statement that is valid on 10.3 but
  questionable on 5.7, it records the fact in §10 and stops — it does not "fix" it speculatively.
- **Migration failure mid-run (Task 9a).** `Migrations`/`MigrationPreflight` are the only path: each step
  carries a stable `-- irica-step:` marker and its own journal entry, DDL is non-transactional, and the
  runner reconciles from engine metadata after a crash. Recovery is: stop, inspect the journal against
  `information_schema`, never edit an applied file, never drop/truncate/recreate, never re-run a half-applied
  step by hand. A pre-migration detection block is not a failure to work around — it is a report for the
  operator (global constraint 9, §5.3). T9a is the sole Phase C owner of both files and may make only the
  additive detector change of §5.3 (round-2, I4).
- **Task failure mid-suite.** A worker that hits a failing suite after implementation stops at the failing
  assertion, records the exact command and output in the task report, and escalates (§9.3). It does not
  weaken a test, skip a suite, or reclassify a failure as expected.
- **No commit.** Every task ends with a scoped-diff review and a recorded verification result; commit and
  push are out of scope for this plan (global constraint 14). Files that must never change:
  `host/migrations/001_initial.sql`, `host/migrations/002_identity_foundation.sql`,
  `host/migrations/003_scoped_business_keys.sql` and `gomrok.py`.
- **Secrets and personal data.** Nothing from a fixture DB — passwords, hashes, tokens, session ids, chat
  ids, names, phone numbers, IPs — is copied into a report, a log line, this plan, or a test fixture (global
  constraint 18, gate B-6).

## 9. Decision gates

### 9.1 Settled gates (decided facts) and what is still open

Every gate the draft left open was settled by the Advisor in `execution-rulings.md` ruling 79, with the
blocker classification corrected in ruling 80 and the G7 premise corrected by ruling 86. They are recorded
below as **decided**; reopening one inside a task is a stop-and-escalate event (§9.3), not a local judgement
call.

**These gates are unaffected by revision round 2.** Round 2 changed task sequencing (C3), suite registration
(C1), the migration content list (C2), the G7 implementation site (I1), the `withWorkspace` signature (I2) and the
idempotency posture (R1) — none of which reopens B-2..B-10, G1/M1's classification, or ruling 80's blocker
split. The round-2 revisions **themselves** are a separate object of review: ruling 89 requires an
independent reviewer who is neither this document's author nor the round-1 reviewer to examine those
Critical/Important changes and their dependencies, and that review **had not been performed when this draft
was written**. Controller adjudication (rulings 82–88) settles the decisions; it does not substitute for
that check. Until the round-2 review passes and the controller approves, no task below may be dispatched.

- **B-2 — new dedicated intent table, mandatory (round-3, C-1: the foundation migration `004_*`, T1).**
  An expiring transport-id dedup row
  cannot reconstruct, resume or reconcile a finance/job action. The foundation migration therefore persists scoped
  intent identity, the command payload and `command_version`, the lifecycle state, and the operation
  linkage. `idempotency_keys` is left untouched **and is actively used by `/v1` Import/Sync, so it is
  additionally protected from repurposing by ruling 88** (§5.5). Consequence: both new tables are
  unconditional (§6.2, §6.2.1.1), they exist from T1's foundation gate onward so T4–T8 can write an
  operations row immediately, T9a's domain migration is still a prerequisite for T3, T5 and T6, and Q14 is
  served by T9b after T9a.
- **B-3 — Q13-only resolution posture accepted.** The command persists a caller-known `operation_id` before
  execution, the authorization scope, the intent/idempotency binding, the command fingerprint, the durable
  execution state, and either the committed domain result/effect references or a classified pre-commit
  failure — written **atomically with the business mutation**; async execution durably links its recoverable
  intent. A timeout means outcome **UNKNOWN, not failure**; `GET /v2/operations/{operation_id}` resolves the
  durable domain outcome; the command invents no HTTP response representation and never claims whether a
  response was delivered. Consequence: §5.6, Q13, C23/C25 and Task 9 Step 5 are written to that posture, and
  no HTTP-facing layer is deferred waiting for a wire format.
- **B-4 — Phase C mutations stay off the legacy `change_log` cursor** that `host/src/Sync.php` serves to
  `/v1`. Consequence: Phase C audit stays transactional but separate from the legacy cursor, and Task 10
  proves isolation in **both** directions — incremental cursor **and** snapshot/bootstrap visibility.
  Omitting log entries alone is explicitly not evidence.
- **B-5 — `AccountPrincipal` stays frozen.** No `sessionId` member is added to that Phase B file. The acting
  session id is obtained inside the mutation transaction's locked callback, from that exact authenticated
  session row; never an arbitrary session by account/tenant, never a silently null value on an authenticated
  mutation. Consequence: `$context.session_id` is mandatory (§6.3), G10 is closed, and a callback that cannot
  identify the exact session is an explicit contract gap to escalate. **Round-2, I6:** the mechanism is
  settled, not open — `$snapshot` does **not** carry a session id (`Workspaces.php:348-362`), so the callback
  re-reads by `Tokens::hashOpaque()` + `uq_account_sessions_token_hash` exactly as §6.3 states.
- **B-6 — credentials are fully redacted from `/v2`.** Explicit safe-field allowlists govern Q10 and C20;
  credential-field updates are rejected outright; existing stored credentials are preserved; secrets stay
  out of error bodies and logs. Consequence: Q10/C20 rows carry a closed allowlist, and I13 tests the
  negative.
- **B-8 — the path-ID model is adopted and those `/v2` routes are added.** Parameterised
  `/v2/crm/.../{id}` routes are **new routes**; there is no second query-parameter identity form. Because
  `V2Api::route()` currently does a static `match` on `$path` (`V2Api.php:261-269`), verifying and extending
  that router is scoped work in T1, not an assumption. Id validation, static-vs-parameterised precedence and
  the existing 404/405 contract are defined in §4.0; workspace-scoped lookup and missing/malformed/foreign-id
  tests are required in T2 and T10; `/v1` stays served and unchanged; `isServable()` (`V2Api.php:277-286`)
  is not weakened and parameterised matching runs under its `%`/`..`/`//`/`\` rules.
- **B-9 — closed PHP vocabularies mirroring `crm.py`.** Unknown values are 400 with no silent fallback; the
  accepted service/status sets are pinned by parity tests. Consequence: C21 carries the explicit sets and T3
  pins them to `crm.py:1492-1493` and `panel.py:3672-3673`.
- **B-10 — database constraints in the domain migration `005_*`** for tenant-scoped serial uniqueness and unique device
  ownership, backed by tenant-matching composite keys, **plus** retained domain checks for actionable
  errors; existing violations are detected **before** the migration runs; duplicate-key races translate to
  409. Because I1 is cross-slot (§5.1), two independent serial-column indexes are insufficient — **005**
  (the domain migration, sole owner of the I1 ledger) adds a single-unique slot ledger instead, and the plan
  says so explicitly. **Round-2, I4:** the detection lives
  in `Migrations.php` under T9a's single additive change (§5.3), because no generic counts-only hook exists.
- **G1 / M1** block Phase C **production acceptance and release**, not implementation (ruling 80). The
  mechanism is fixed in §6.2.1.2, and `logLabel()`'s parameterised-route support is owned by T10.
- **G7** is a **new `/v2`** JSON request-boundary guarantee: duplicate-key and surrogate-key refusal is
  implemented at the `V2Api` request-parsing boundary **before** decoding can lose a duplicate (ruling 86),
  and dedicated PHP parser tests — including nested duplicates, canonically-equivalent escaped keys and a
  `/v1` compatibility assertion — are required **before** Phase C JSON-mutating endpoints are accepted.
  `Http::jsonBody()` and `/v1` stay unchanged; DDL-only work does not wait (rulings 80, 86).

**Still genuinely open** (none blocks dispatch; each is either a §9.2 bounded choice or a §10 open item):
**gate B-7 was not among the gates the Advisor settled** (ruling 79 covers B-2..B-6 and B-8..B-10), so the
exact refusal-code names for stage refusals and no-open-period refusals are carried as bounded choice
**BC-8** — T1 Step 4 declares them once in `Codes::ALL`; also open are the `transport_id`/`operation_id`
binding (BC-4), the owner-binding consequence of Phase C soft-deleting a device where legacy
`crm.delete_device` hard-removes it (BC-6), the Phase C page-limit constant against `MAX_PAGE_SIZE`
(BC-7), and the five §10 items that remain genuinely OPEN — 7, 9, 11, 13 and 14. The other nine §10 items are
CLOSED with their evidence (§10), including the previously open session-id mechanism (item 1), the front's
acceptance of parameterised paths (item 2), `MigrationPreflight.php`'s editability (item 3) and the
`idempotency_keys` writer question (item 5).

### 9.2 Bounded implementation choices (worker may decide, record rationale)

A worker may decide these and must record the chosen value plus a one-line rationale in its task report. Any
choice that turns out to conflict with §4's contracts is escalated instead of applied.

- **BC-1 — RESOLVED in revision round 2, no longer a worker choice.** The G1/M1 mechanism is fixed in
  §6.2.1.2: the production-entrypoint harness `lab/fixtures/host_v2/index_binding.php` (T1-owned), driven by
  mutation testing (delete the early `return` / flip the fixture flag and require the suite to fail). A
  static assertion on `index.php` is explicitly **not** sufficient — it cannot prove emission. Kept in this
  list under its old label so earlier cross-references still resolve.
- **BC-2 — the domain migration file name, step ids and column names** (the foundation migration is T1's
  `004_*`, round-3, C-1; the domain migration is the next free number, expected `005_*`), within the
  unconditional content list of §6.2 and the column/key/deletion contract of §6.2.1.1, and the naming
  conventions of `003_scoped_business_keys.sql`.
- **BC-3 — keyset cursor encoding** (`after`): opaque base64 of the last key tuple versus a plain trailing
  id, provided it is treated as a window and never as authority.
- **BC-4 — `operation_id` ↔ `transport_id` binding**: a separate client-minted UUID sent in the same
  request, or the same value used for both. Either is acceptable only if the caller knows the id **before**
  execution (§4.2, gate B-3) and only if §6.2.1.1's keys still hold — if the two ids are the same value, the
  operations table may implement `UNIQUE (tenant_id, operation_id)` and `UNIQUE (tenant_id, transport_id)`
  as one key, but the replay read of §5.4 must still be a `FOR UPDATE` on that key.
- **BC-5 — operations/intent table field names and status enum values**, provided the columns, keys and
  deletion semantics of §6.2.1.1 all exist and the state names used by Q14
  (`pending, dispatched, settled, rejected, expired`) are exactly those.
- **BC-6 — owner binding after a soft device delete.** Legacy `crm.delete_device` (`crm.py:1388-1411`)
  hard-removes the row, which frees `crm_devices.owner_id`; Phase C tombstones it. The parity-preserving
  default is to detach the owner on delete so the 1:1 pool semantics match the behavioural oracle; keeping
  the binding is also defensible if the 1:1 check ignores tombstoned devices. Whatever is chosen must be
  asserted by a named test in T5 and stated in the handoff as a documented divergence. This is **not** a
  settled gate — rulings 79–81 do not cover it.
- **BC-7 — Phase C page-limit constant.** Q1–Q9 and Q11 allow `limit` up to 200 (and 500 for Q11) while
  `V2Api::MAX_PAGE_SIZE = 100` (`V2Api.php:59`). **Verified fact (round-2, M3):** the constant is at
  `:59`, not `:60`, and it bounds **only** the `/v2/workspaces` list — it is enforced inside `intQuery`
  (`V2Api.php:505-524`, the message and comparison at `:516-521`), whose **only** call site is the
  workspaces list at `V2Api.php:396`; nothing enforces it globally today. Either keep a distinct Phase C constant and say
  so, or adopt 100; do not change the Phase B constant silently, and do not assume it is a global `/v2`
  bound.
- **BC-8 — the final Phase C error-code list** beyond the nine named in T1 Step 4: T1 enumerates and owns
  `Codes::ALL`; any task needing an undeclared code stops (§9.3).
- **BC-9 — Q11 composite read strategy**: one join query versus several tenant-scoped selects inside the
  same admission transaction, provided row counts stay bounded and no lock is taken.
- **BC-10 — Phase C account actor column names** (`created_account_id`-style versus `*_by_account_id`),
  provided every mutation and soft delete writes them and legacy `actor_user_id` / `deleted_by` are never
  touched.

### 9.3 Worker stop / escalation conditions

A worker stops and escalates rather than deciding when any of these is true:

1. It needs to edit a file the §6.1 ownership table denies it, or any Phase B shared file
   (`Sync.php`, `Import.php`, `Auth.php`, `Roles.php`, `AccountSessions.php`, `Accounts.php`,
   `Workspaces.php`, `Tokens.php`, `Ids.php`, `Database.php`, `Config.php`, `Log.php`, `AccountPrincipal`,
   `host_v2_front.php`, or `Migrations.php`/`MigrationPreflight.php` outside T9a's single additive detector
   change), or `001_initial.sql`, `002_identity_foundation.sql`, `003_scoped_business_keys.sql`,
   `gomrok.py`, or an already-applied migration.
2. A mutation cannot identify **the exact authenticated session row** inside the locked callback — for
   example the token is not in scope at the callback authoring site so the §6.3 `Tokens::hashOpaque` re-read
   cannot be performed — or would have to leave `session_id` null on an authenticated mutation (gate B-5).
3. It needs a Phase C error code that is not in `Codes::ALL`, or a response shape that contradicts §4.
4. A settled gate (§9.1) would have to be reopened or reinterpreted to make a test pass.
5. A required test cannot be built at all — for example `lab/fixtures/host_v2/index_binding.php` cannot be
   made to run because `Config::load()` rejects a generated fixture config, or a "response never delivered"
   scenario cannot be produced on the loopback front — rather than marking it waived locally. A waiver is
   recorded in §10 by the controller, never by the worker.
6. Migration preflight reports a non-zero pre-existing violation count, a journal/checksum mismatch, or drift
   it cannot reconcile.
7. Any test would require the network, a production host, `host/config/config.php`, credential files,
   customer or panel local data, MySQL 5.7, or an unrun legacy harness.
8. A concurrency test produces two winners, a double refund, a second reversal, two identical-`transport_id`
   mutations, or any invariant I1–I13 failure that the implementation cannot explain with a specific,
   evidenced defect.
9. The task's RED phase produces no genuine failure (the test passes before implementation), or its named
   suite cannot run — argparse rejects the name, or `FIXTURE_SUITE_MISSING` is raised — and is about to be
   counted as green (round-2, C1).
10. Scope would grow beyond the task's stated files — a new dependency, a speculative abstraction, or
    work that belongs to Phase D/E/F.

## 10. Resolution list — every former UNKNOWN, CLOSED with evidence or still OPEN

Round-2 status of every item the previous draft left open. **CLOSED** means the repository settled it and the
closing evidence is named; the plan now treats it as a fact. **OPEN** means it genuinely cannot be settled
from this checkout, and the controller must resolve or waive it before the dependent task is dispatched. No
item is left open merely because it was hard to check, and no item is closed without named evidence.

### CLOSED

1. **CLOSED (round-2, I6) — `$snapshot` does not carry the session id; the re-read is mandatory.**
   `Workspaces::describe()` returns exactly `tenant_id, slug, name, status, lifecycle, api_contract,
   context_version, role` (`host/src/Workspaces.php:348-362`) — no session id — and it is the array
   `lockAuthorizedWithin()` hands back to `withWorkspace()`'s callback (`AccountSessions.php:550`). The
   fallback is therefore not merely viable, it is the only path, and §6.3 now spells out its steps: compute
   `Tokens::hashOpaque($token)` (`host/src/Tokens.php:127-130`; the equivalent `AccountSessions::hash()` is
   `private` at `:217`), then `SELECT id FROM account_sessions WHERE token_hash = ?` under
   `uq_account_sessions_token_hash` (`002_identity_foundation.sql:136`). The old draft's cross-reference to
   "§10 item 12" for this was wrong — this is item 1, and item 12 was `MAX_PAGE_SIZE`.
2. **CLOSED (round-2, M2) — the fixture front does not "reimplement classification"; the real residual is
   G1.** `lab/fixtures/host_v2_front.php:71-84` calls `V2Api::logLabel()`, `V2Api::isVersion2Request()`,
   `Request::capture()`, `V2Api::assertTransport(..., fixture: true)` and `V2Api::dispatch()` — it delegates
   every classification decision. Its only local rule is the loopback-only transport gate. A parameterised
   `/v2/crm/.../{id}` path therefore passes the front unchanged. The draft's "its own path rules may reject"
   framing is struck; what remains is the two facts of I3 — the front never enters `index.php`, and
   `logLabel()` returns `<unknown>` off the static `ROUTES` table (`V2Api.php:135-140`) — both now handled in
   §6.2.1.2 with a named owner. G2's no-edit rule stands (§6.1).
3. **CLOSED (round-2, I4) — `MigrationPreflight.php` and `Migrations.php` are editable by exactly one task.**
   They are Phase B files, so they were never free-for-all; the defect was that neither §6.1 nor §9.3 said
   so. §6.1 now names **T9a** as sole Phase C owner of both, with a single permitted additive change (the
   domain migration `005_*`'s counts-only pre-migration detector), and §5.3 records why an additive hook is
   needed at all:
   `Migrations::apply()` consults `assertScopedReferencesSafe()` only for
   `$version === self::SCOPED_VERSION` (`Migrations.php:198-199`, method at `:267`) and
   `MigrationPreflight::scopedIntegrity()` (`MigrationPreflight.php:684`) walks only
   `Schema::scopedRelationships()` — there is no generic counts-only hook. **Item 8 below is the same
   question and is closed by the same evidence.**
4. **CLOSED (round-2) — `test_migrations.php` can be extended, and each new migration has one concrete
   trap (round-4, F2: this trap was written as "`004`" when one migration carried the whole domain; it now
   binds both `004_*` and `005_*`).** Verified
   directly: `test_migrations.php:567` asserts `tableCount($ddl) === count(Schema::tables())`, so `Schema.php`
   and the DDL must move together (T9a already required to do so); `$versionDomains` at `:892-897` is a fixed
   four-entry list, so adding tables does not perturb it; and `:918-941` requires every **other** table with a
   `row_version` column to keep an unrestricted domain and to accept `0`/`-5`. That last rule is now a stated
   constraint on the four Phase C tables in §6.2.1.1, so T9a cannot trip it silently.
5. **CLOSED (round-2, R1 / ruling 88) — `idempotency_keys` is in ACTIVE use; the old draft's claim was
   factually wrong.** Writers: `host/src/Import.php:175` (insert) and `:224` (read-back of `response` by
   `operation_id`); `host/src/Sync.php:700` (read of `operation_id, status, response`), `:725` (insert) and
   `:746` (expiry sweep `DELETE ... WHERE expires_at < ?`). Phase C therefore treats the table as protected:
   §3.1 C25, §5.5 and T9b's direct-SQL and statement-scan assertions all forbid writing, truncating,
   migrating, repurposing or re-retentioning it, and require Phase C's own operations/intent tables to be
   separate (§6.2.1.1).
6. **CLOSED (round-2) — `V2Api::route()`'s internal structure is now fully read.** `route()`
   (`V2Api.php:244-269`) is: `isVersion2Request($request->path)` **or** `!isServable($path)` **or**
   `!isset(self::ROUTES[$path])` → 404; then a method `in_array` → 405; then a `match` on `$path` with a
   `default =>` 404. There is **no** intermediate guard between the 405 check and the `match`, so T1's
   parameterised matcher has exactly one insertion point and cannot bypass `isServable()` (verified at
   `:277-286`, which refuses `%`, `..`, `//` and `\`).
7. **OPEN (item 7) — whether the loopback harness can produce a genuinely undelivered response after
   commit.** Not testable from this checkout (no run was permitted). Consequence, already binding:
   Task 9b Step 5 writes the B-3 assertions at the **command/service layer** (a committed operation row plus a
   deliberately withheld response), never by simulating a dropped socket, and records the gap in the handoff.
8. **CLOSED (round-2, I4) — `Migrations.php` offers no reusable counts-only blocking hook**, so T9a does
   add one, additively, under its exclusive ownership. See item 3 for the evidence.
9. **OPEN (item 9) — the complete production `status` vocabulary for inquiry results.** Only the sets visible
   in this checkout were pinned: `_GOMROK_PASS = {"healthy"}` and `_HAMTA_PASS = {"good","registered",
   "success"}` (`crm.py:1492-1493`), `_ESTELAM_FINAL = {"healthy","problem","advanced","error","good",
   "registered","blocked","success","unknown"}` (`panel.py:3672-3673`), and the in-flight values `pending` /
   `running` (`panel.py:3695-3707, 3721`). Robot-side writers were not exhaustively enumerated. Gate B-9's
   parity test makes this safe rather than open-ended: any later Python vocabulary change fails the PHP suite,
   and C21 rejects every value outside the pinned set with a field-named 400. The controller may widen the set
   before dispatch; no task may widen it silently.
10. **CLOSED (round-2) — Phase C does not need a fourth audit structure.** No repository artifact names a
    Phase C audit target; the mutation-audit contract is the Phase C account actor columns (**`005_*`**, the
    domain migration that solely owns them), the
    operations row (which carries `account_id`, `session_id`, `capability`, the command fingerprint and the
    committed result) and the intent row. I12/I13 assert in direct SQL that no legacy `actor_user_id` or
    `deleted_by` is ever written. If a later phase wants a separate audit table, that is D/E/F scope.
11. **OPEN (item 11) — MySQL 5.7 acceptance of the two new migrations' DDL (`004_*` and `005_*`).**
    Unknowable here: MySQL 5.7 is **BLOCKED and
    UNRUN** (global constraint 16). MariaDB 10.3.39 results and `php -l` prove nothing about 5.7. The owning
    task records
    any 5.7-questionable statement in the handoff and stops — **T1** for `004_*`, **T9a** for `005_*` — and
    it never "fixes" it speculatively. This blocks
    any future production migration of either new migration, not Phase C implementation.
12. **CLOSED (round-2, M3) — the factual half of the `MAX_PAGE_SIZE` question.** The constant is
    `V2Api::MAX_PAGE_SIZE = 100` at **`V2Api.php:59`**, not 60. It is enforced inside `intQuery()`
    (`V2Api.php:505-524`, bounds message and comparison at `:516-521`) whose **only** call site is the
    `/v2/workspaces` list at `:396`. Nothing enforces it globally, so "was it *intended* as a global `/v2`
    bound" remains a design question, not a repository fact; BC-7 carries it as a bounded choice with the
    verified scope stated.
13. **OPEN (item 13) — deployed production bytes/checksums of the applied migrations.** Requires an
    authorized host preflight (global constraint 1), outside this plan. Blocks any production release of
    either new migration (`004_*`, `005_*`), not Phase C implementation.
14. **OPEN (added in round 2, I3) — whether the production-entrypoint harness of §6.2.1.2 is actually
    buildable.** The mechanism is specified down to its environment variables and config shape, but whether a
    runtime-generated `Config.php`-shaped file passes `Config::validate()` / `validateDb()`
    (`host/src/Config.php:83-107, 109-134`, which require `db.{host,dbname,user,password}`, reject any
    `REPLACE_`-prefixed placeholder and require a 32-byte-hex `jwt_secret`) without touching
    `host/config/config.php` was **not** executed here, and neither was the third key the harness now
    generates — `log_path` (round-3, I-2), which `index.php:64-65` consumes and without which `Log::line()`
    writes nothing (`Log.php:65-67`). `Config::validate()` ignores unknown keys, so `log_path` adds no
    rejection risk of its own; the risk being reported is whether the CLI harness reaches `index.php`'s
    `finally` at all. T1 builds it first and reports; if it cannot be built,
    T1 stops per §9.3(5) and the **controller** records a waiver against this item. G1 and M1 then remain
    open release blockers and Phase C production acceptance stays blocked — implementation is unaffected.

**Summary:** items **1, 2, 3, 4, 5, 6, 8, 10, 12** are CLOSED with named evidence; items **7, 9, 11, 13, 14**
are genuinely OPEN, none of which blocks dispatch of T1–T9b — 7 and 9 shape test scope, 11 and 13 are
release-side, and 14 is a T1 build report with a controller waiver path.

## 11 Review focus

**Round-3 recheck (ruling 94) — do this first.** An **existing** reviewer — not the author, not the round-1
reviewer, not a new full review — rechecks exactly four things against this text and the repository: (a) the
**revised dependency chain** — T1's foundation migration and `Operations.php` ahead of T4, T9a's domain
migration `005_*`, the backbone `T1 → T4 → T9a`, lanes A/B/C and the `T8 ← T3` cross-lane edge (§6.5), with
every task's `Files` list covering every file its steps touch (§6.1, Task 1); (b) the **transaction boundary**
— one `Database::transaction()` per command, the operations row as first write and linearization point, the
step-5/step-8 ordering of §6.4 and the §5.4 replay rule; (c) the **executable FK migration** — that §6.2.1.1
reuses only keys that exist (`003:57-67`, four tables, never `tenants`), adds the one missing
`UNIQUE (tenant_id, id)` on `identities`, and uses the plain `REFERENCES tenants (id)` of `001` (round-3,
I-1, ruling 92); and (d) the **observable M1 test** — that the generated config names an isolated writable
`log_path`, that `Log::line()` therefore writes, and that the assertion is "contains `route`/`status`/`code`"
over the five Phase B routes rather than "exactly those three fields" (round-3, I-2/I-3, rulings 93).
Escalate to another full review only per ruling 94's own conditions (material redesign, unresolved
disagreement, or an explicit governing review requirement).

An independent reviewer — **not the author and not the round-1 reviewer** (ruling 89) — should then examine,
in this order:

1. **Internal consistency with rulings 79–81 and 82–88.** Every B-N ruling is applied in the body, not only
   listed in §9.1: B-2's migration content (`004_*` operations/intents, T1; `005_*` domain tables, T9a — the
   §6.2.1.1 split), the Q13 posture through
   §4.2/§5.6/Task 9 (B-3), `/v1` isolation in both directions (B-4), the frozen `AccountPrincipal` path and
   the `Tokens::hashOpaque` re-read (B-5, I6), credential allowlists in Q10/C20 (B-6), the parameterised-route
   model and the deleted contradiction (B-8), the C21 vocabulary sets (B-9), and the cross-slot serial
   strategy (B-10).
2. **The three round-2 Criticals, first and hardest.** (C1) that T1's `SUITES` change and its seven stub files
   are one change, that no owning task can add or remove a suite name, and that every task's acceptance runs
   its named suite **and** verifies its line in `--suite all`; (C2) that `profiles` and `inquiry_results` are
   unconditional `005_*` content with a stated column/key/deletion contract, that **no FK is added over the
   deliberately dangling `crm_devices.profile_id`**, that local profile ids stay adoptable without re-keying,
   and that T3 cannot start before T9a; (C3) that the `{T2,T3,T6,T7}` parallel set is gone, that
   `CrmCommands.php` remains a strict T4→T5→T7→T8 chain, that T2's completion gate is after T6, and that T3
   sits after T9a.
3. **The round-2 Important fixes.** That G7 is implemented at the `V2Api` boundary with `/v1` proven
   unchanged (I1); that no `$sqlOnly` appears anywhere and the `withWorkspace` argument order is exactly
   `($token, $purpose, $now, $tenantId, $expectedContextVersion, $capability, $fn)` (I2); that the G1/M1
   harness is specified and owned, and that M1 lines are labelable rather than `<unknown>` (I3); that
   `Migrations.php`/`MigrationPreflight.php` are owned by T9a with one additive change and no other (I4);
   that replay detection is a `FOR UPDATE` taken **before** the domain write and that a duplicate-`transport_id`
   two-connection case is mandatory (I5); that §10 item 1 is closed with the hash re-read spelled out and the
   "§10 item 12" mis-citation is gone (I6); and that every referenced id — not only the target row — is
   404-foreign for C6/C9/C17/C22 (I7).
4. **Routing safety.** That the B-8 extension cannot weaken `isServable()`, that static precedence is
   correct, that a malformed id cannot become a 400 oracle, and that every lookup is tenant-scoped.
5. **Schema.** That both new migrations are additive, that the slot ledger actually covers cross-slot collisions,
   that pre-migration detection blocks rather than repairs, that the I2 unique key covers active bindings
   only, that the Phase C `row_version` columns (two in `004_*`, two in `005_*`) keep the unrestricted domain
   `test_migrations.php:918-941`
   requires, that the fixture `TENANT_CHILDREN` delete order covers the new tables, and that no legacy
   `actor_user_id` or `deleted_by` is ever written.
6. **Idempotency.** That `idempotency_keys` is treated as in active use and is never written, migrated,
   truncated or re-retentioned by Phase C, and that the new operations/intents tables have the key scopes and
   atomicity boundary of §5.5/§6.2.1.1.
7. **Task dependency graph and file ownership.** That T9a genuinely precedes T3/T5/T6, that no two tasks
   share a file, that `Migrations.php`/`MigrationPreflight.php`/`host_v2_front.php` are owned or forbidden
   explicitly, and that every suite a task claims has a declared `SUITES` entry and an existing PHP file.
8. **Acceptance evidence.** That RED phases are genuine, that "suite could not run" is never green, that
   G1/M1/G7 are classified as release/acceptance gates rather than implementation blockers, and that no
   unrun legacy harness is counted.
9. **Scope.** No Phase D/E/F work, no commit authorization, no `/v1` change, no production or external
   target, no secrets.
10. **§10.** Re-check every CLOSED item against its named evidence, and confirm that the five OPEN items
    (7, 9, 11, 13, 14) are genuinely open; any load-bearing one must be closed by the controller before the
    dependent task is dispatched.

## 12 Checked-in facts

- **Baseline commits.** `c2bd654` — `chore(phase-a): checkpoint the Phase A online-feasibility baseline`
  (Phase A evidence baseline, 21 paths / 3655 insertions) and `f183134` —
  `feat(host-identity): Phase B foundation — accounts, workspaces, purpose-bound sessions and /v2`
  (the Phase C starting point), both on branch `lab/host-identity-phase-b`. Ruling 73 records the commit
  order; ruling 71 preserves the zero-unauthorized-commit posture.
- **Verification on the committed tree (rulings 74, 76).** `fixture-guard` exit 0 (43/43 OK) and
  `--suite all` exit 0 with exactly one `FIXTURE_READY` (`driver=mysql server=10.3.39-MariaDB tables=25`);
  15/15 protected pins matched; port 8766 closed after the run.
- **Database.** MariaDB 10.3.39, fixture-only, on a disposable datadir. MySQL 5.7 is **BLOCKED and UNRUN**
  (Phase B handoff §5); no 5.7 inference is permitted anywhere in this plan.
- **Served API.** `/v1` remains the served API. `/v2` exists as a separation mechanism with exactly five
  routes (`V2Api.php:67-73`); it is neither a deployment nor a cutover approval.
- **Unrun gates (handoff §6).** `lab/tests/test_host_api.py`, `test_host_schema.py`, `test_create_admin.py`,
  `lab/scripts/run_panel_tests.py`, `run_online_feasibility.py` and `lab/tests/test_online_*` stay unrun;
  their absence is never counted as green (ruling 19/66 scope-out stands).
- **Rulings constraining this plan.** From
  `.superpowers/sdd/2026-10-11-host-identity-integrity/execution-rulings.md`: **71** (preserve-first,
  zero-unauthorized-commit), **72** (content review of all shared/modified files before staging), **73**
  (the two checkpoint commits), **74** (post-commit integrity, 15/15 protected pins), **75** (fixture restart
  recipe and the `Access denied` failure of the pristine vendor datadir), **76** (post-commit verification on
  the committed tree), **77** (incremental drafting rule — no artifact-less long-running authoring agent),
  **78** (DRAFT ONLY; gate sign-off → independent review → controller approval), **79** (gates B-2..B-10
  settled), **80** (G1/M1 are release gates; G7 is a JSON request-boundary gate), **81** (filling §9/§10
  alone is not review-ready; internal consistency is required), **82** (round-1 review REJECT, 3 Critical /
  7 Important / 10 Minor, with the controller's independent confirmation of C1/C2), **83** (C1 — suite
  registration and implementation sequenced into a runnable state at every step; each owning task executes
  its named suite **and** verifies inclusion in `all`), **84** (C2 — authoritative `profiles` and
  inquiry-result storage in the domain migration **before** their consumers; T3 stays DDL-prohibited; no FK over
  deliberately dangling profile ids without a migration strategy), **85** (C3 — the unconditional parallel-safe
  set is dropped in favour of dependency-gated lanes), **86** (I1 — duplicate-key refusal is new `/v2`
  boundary work; `Http::jsonBody()` and `/v1` stay unchanged), **87** (I2 — the real `withWorkspace`
  signature; no `$sqlOnly`; transaction ownership documented as a callback constraint), **88** (R1 —
  `idempotency_keys` is actively used and protected; the new tables' key scopes, lifecycle, replay and
  atomicity are specified), **89** (R2 — a **second** independent review of this revision is required, by a
  reviewer who is neither the author nor the round-1 reviewer, before any task is dispatched),
  **90** (round-2 review — APPROVE WITH REQUIRED REVISIONS, 1 Critical / 8 Important / 12 Minor, with three
  §10 closures verified independently), **91** (C-1 — the operations/intent DDL and `Operations.php` move
  ahead of T4 into a tested foundation gate owned by T1; one transaction; no B-3 cost), **92** (I-1 — plain
  `REFERENCES tenants(id)`, add the missing `UNIQUE (tenant_id, id)` on `identities` before any composite FK
  that needs it, and correct the claim about what 003 supplies: "reuse existing candidate keys and add only
  required keys that are absent"), **93** (I-2/I-3 — an isolated writable temporary `log_path` in the
  generated fixture config, and an M1 assertion that the record **contains** `route`/`status`/`code` rather
  than that those are its exclusive fields; `ts` and the Phase B metadata are preserved and no production
  observability field may be deleted to satisfy a test), **94** (review disposition — controller adjudication
  suffices for these decisions, no third full review is automatic, and an **existing** reviewer rechecks the
  dependency chain, transaction boundary, executable FK migration and observable M1 test), **95** (round 3 was
  executed in two parts: C-1 and I-1 landed first, I-2..I-8 and m-1..m-12 followed in a tightly-scoped
  second pass), **96** (the T4 account-actor exception: T4 may omit the Phase C actor columns only while
  implementing against the pre-005 schema, **T9b owns the retrofit** of every T4 mutation surface, the
  exemption **expires at T9b completion**, Phase C cannot be accepted or released with any T4 path still
  exempt, historical attribution is backfilled only where deterministic and otherwise classified as legacy
  data; migration 004 (T1) stays operations + intent only and migration 005 (T9a) stays the sole schema
  owner of the account actor columns).
