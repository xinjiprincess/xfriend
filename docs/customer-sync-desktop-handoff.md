# Desktop customer sync integration (Xsalers 0.7)

Implementation lives in **D:/octop**, not the nested server checkout. No cloud-account authentication implementation was changed. Existing uncommitted changes must remain intact.

## Wire and authentication

- Renderer: `/api/desktop/sync/push`, `/pull`, `/catalog/experts`, `/catalog/skills`. Send `X-Xfriend-Sync-Scope: {cloud_user_id}:{space_id}`. Native proxy pins this context before and after the request, rejects cross-origin requests and uses its own device ID/access token. At most one `cloudAuth.refresh("")` follows a 401. Local JWT and refresh tokens never enter this wire.
- Native forwards exactly the agreed `/api/v2/sync/push` POST and `/api/v2/sync/pull?space_id=&cursor=&limit=100` GET. Owner is not accepted in the push body. The catalog uses the agreed v2 paths.
- Push operations are `{operation_id,entity_type,entity_id,base_revision,deleted,payload}`. The client handles `applied`, `duplicate`, and `conflict` with a full `server_entity`. Push cursor is deliberately ignored. Pull cursor advances only in the same local commit as the page's data.
- Pending operations persist unchanged through retries and restarts. Editing the same pending entity requires first syncing or resolving its conflict. Conflicts never overwrite local data automatically. Resolution either accepts cloud or creates a new operation against the latest server revision.
- Delayed local `/api/customer-sync` requests are also checked against the current native account/space. Python scopes its durable journal by authenticated local user plus space, within the account-isolated Octop home.

## Payload compatibility

Customer, communication, opportunity, followup fields match `D:/xfriend-suite/app/src/main/java/com/singu/xfriend/SalesDomain.kt`. JSON names remain `customerId`, `nextContact`, `analysisJson`, `rawTranscript`, `dueDate`, `dueTime`, etc. Created communication timestamps are epoch milliseconds; displayed in the configured server timezone. Unknown fields survive desktop edits.

AI proposals come from communication `analysisJson.profile`, merge nonempty known profile fields into the existing insight, and require explicit confirmation. Manual CRM editing is independent of AI. Follow-ups and opportunities are linked and displayed per customer.

**Android must preserve unknown payload fields when merging/re-serializing.** Desktop expert/team/model_profile payloads carry `_xfpack` metadata. It contains original portable `resource`, `providers`, base64 `files`, `format_version`, and `client_version`. These preserve engine selection, preferences, avatars, dependencies, member model assignments and workspace files. Original references map to stable desktop IDs; no IDs are regenerated on retries.

The activation server currently limits each payload to 2 MiB, entity IDs to 128 characters, space IDs to 64 characters and each push to 100 operations. The desktop validates these payload limits and batches by both count and bytes. The native proxy allows a 4 MiB push body and 8 MiB responses; the client retries oversized pull pages with `limit=1`. Oversized or rejected exchanges remain pending, without reporting success.

## Configuration and permissions

The configuration tab stages selected real desktop experts/teams/providers using existing portable snapshot ownership and provider checks. Keys are omitted unless explicitly selected by a user with `providers` permission. Desktop `key`/`syncSecret` and Android `apiKey`/`sync_secrets` are supported. Staging sets both consent flags from the explicit edit opt-in, removes both key aliases when disabled, and emits an empty payload for tombstones. Credential aliases are redacted recursively in JSON previews. OAuth/session tokens are never exported by the configuration adapter.

Server ownership and full payload encryption at rest belong to the activation server implementation. No desktop test claims to verify production cloud encryption or a live cloud deployment.

Synced configuration downloads are key-free `.xfpack` files, reviewed/imported through the existing Configuration transfer screen and its permission/skill-trust gates. Download alone never installs code or changes provider permissions. Android one-member teams cannot be represented by the current xfpack schema (minimum two members); package download rejects that case rather than silently changing it.

Catalog entries retain the upstream record as `payload.catalog` including content, version, dependencies and optional files. No license text is rewritten. Catalog retrieval is a real cloud call; unavailable endpoints display failure.

## Constraints for the parent integrator

- `communication.file` / `attachments` are references only. The activation agent added optional `/api/v2/sync/media` upload manifests, 256 KiB chunks, completion and downloads. These paths are intentionally outside the desktop proxy allowlist; desktop media transfer has not been implemented or claimed. Source-device audio/images cannot be fetched merely from a local path. The UI labels this limitation.
- Desktop knowledge bindings are retained in xfpack dependencies, but binary knowledge stores are not exported as fictional text. Cloud `knowledge` records are visible in the synced configuration list. Existing portable importer reports unresolved dependencies.
- Small synchronous JSON transformations use the local journal; database/archive I/O runs in the executor. This is a single-process desktop service journal, not a multi-worker server sync engine.
- Minimal registrations: desktop HTTP dispatch; Python router catalog; `/customers` route/path key; frontend sidebar and matching backend sidebar key. No other existing behavior is refactored.
- Repository-wide formatting/checks and Android builds remain with the parent integration task. The desktop frontend build has been run independently (see below).

## Focused verification

- `uv run pytest tests/unit/test_customer_sync.py -q`: 15 passed. Isolated real SQLite; durable retries, user/space isolation, missing acknowledgements, newer remote edits, explicit AI confirmation, provider permission/secret opt-in, tombstones, xfpack metadata/team/skill round trips, payload limits and OpenAPI schemas. Replayed old conflict receipts cannot replace a newer tombstone, even after repeated delivery.
- From `desktop/src`: `go test . -run CustomerSync -count=1`: 6 passed. Native access token, one refresh, route/origin/scope boundaries, failed-cloud status, account-switch response discard and local journal scope pinning.
- From `dashboard`: `npx vitest run src/api/modules/customerSync.test.ts src/pages/Customers/model.test.ts src/pages/Customers/index.test.tsx src/layouts/sidebarNav.test.ts`: 14 passed. Stable retry operations, no fake success, pull cursors/pages, selected-customer reports, manual confirmation of AI profile proposals, credential alias redaction, and navigation registration. JSDOM emits a non-failing pseudo-element getComputedStyle warning from the modal dependency.
- Focused Ruff, mypy and ESLint checks passed; `npx tsc -b --pretty false` passed. Full `make all`, desktop packaging, production-cloud integration and Android builds were not run.

## Independent cross-client contract review (2026-10-13)

With `PYTHONPATH=D:/xfriend-activation/src`, `uv run pytest tests/unit/test_customer_sync.py tests/unit/test_customer_sync_contract.py -q`: **18 passed**. The three optional sibling-server tests use the actual activation FastAPI application through TestClient and temporary SQLite stores, without external network access. Without the sibling package, those three tests skip explicitly.

The real-server sequence covers: create with authoritative entity ID; lost push receipt; remote tombstone; duplicate receipt after a newer pull; resurrection racing another write; three replays of the original conflict after another tombstone; explicit local resolution with a new operation ID and current base revision; final convergence to one entity at revision 5. Both model key opt-in states round-trip `_xfpack` unchanged. Inspected `sync_changes` and `sync_operations` records contain neither the key marker nor plaintext metadata filenames. This is local server implementation evidence, not production encryption/deployment verification.

This task's Android review was **source inspection only**, of Pascal's `SalesCloudSyncProtocol.kt`, `SalesCloudSyncStorage.kt`, `SalesCloudSyncData.kt`, and `SalesCloudSyncEngine.kt`. The user subsequently reports **22 Android local tests passed against the same protocol**; those tests were not run by this desktop task and do not establish real-device end-to-end acceptance:

- Capture and keep-local conflict resolution now use `salesCloudOutgoingPayload` to overlay typed local fields on the full known payload. `_xfpack` survives projection into Android domain models; secret consent comes from the current local model, not the previous payload.
- Import accepts both secret aliases; opt-out export strips aliases recursively. Snapshot retains existing entity IDs; new conflict proposals get new operation IDs and the observed remote revision. Push cursor is ignored; pull revisions/cursor are checked; push cap is now 4 MiB and large pull responses retry at limit 1.
- **Customer deletion semantics now aligned:** desktop confirmation explicitly states that communications/followups/opportunities survive and lose their customer link. The local transaction queues child updates (`customerId: ""`, original IDs/base revisions, all other payload fields retained) before the customer tombstone. Pending/conflicted children must be synced/resolved first; the UI explains this and the service rejects the whole deletion without rewriting immutable operations. Detached records can resolve conflicts with an empty customerId. Unassigned children remain outside customer-specific panels. Android's separate communication-deletion behavior (`withoutRecord` clears followup recordId) is unchanged by this customer-deletion patch.
- Desktop stops visibly on an invalid cursor after server restoration; Android has an explicit reconciliation/reset path. Desktop does not silently reset or overwrite pending changes.

Production status supplied by the user:

- **43:** host-key fingerprint verified through the console; the new sync server version is deployed. Readiness returns HTTP 200, unauthenticated sync returns HTTP 401, and cold-backup/original-signing-key verification passed. Host-key verification is no longer a blocker for 43.
- **113 trial host:** host-key verification remains pending. Only this host is still waiting for verification.
- **Acceptance still pending:** real Android/desktop login and authenticated two-client synchronization, plus media end-to-end transfer. Deployment checks and the 22 Android local protocol tests do **not** mean these flows have passed acceptance.

This desktop task has made no production calls and has not independently repeated the reported deployment checks. No cloud-account auth source was modified.

`make build-frontend REPO_ROOT=D:/octop 'SHELL=C:/Program Files/Git/bin/bash.exe'` completed successfully, including `npm ci`, TypeScript, Vite and PWA generation. The explicit root works around Windows make's missing `pwd`; no Makefile change was needed. Log: `.artifacts/customer-sync-frontend-build.log`. A final `npm run build` rebuild includes the preview-redaction patch; its log is `.artifacts/customer-sync-frontend-final.log`.

The final rebuild also completed successfully. `uv run python .artifacts/customer-sync-check-build.py` verified all **124** entry/precache references exist, plus the `/customers` route, native proxy, local journal and credential-redaction markers in generated JavaScript. Customer chunk: `src/octop/dashboard/assets/index.BJ4YaAHu.js` (22,459 bytes), SHA-256 `5f9f6471f0daadce10ff798dcfd2c1c93bb8a1745d43f3eb907e746b85d811d1`. Machine-readable evidence: `.artifacts/customer-sync-check-build.json`. Generated files were built, never hand-edited. No lockfile change resulted from `npm ci`.

This acceptance pass changed only owned files: `infra/customer_sync/service.py`, Customers `index.tsx`/`model.ts`/`model.test.ts`, `tests/unit/test_customer_sync.py`, new `tests/unit/test_customer_sync_contract.py`, and this handoff; generated build/audit artifacts are additional outputs. It made no further shared registration changes.

## Customer deletion alignment follow-up

Changed only `infra/customer_sync/service.py`, Customers `index.tsx`/`strings.ts`/`index.test.tsx`, the two customer-sync Python test modules and this handoff. No registration/auth/Android/server source changes.

- With sibling activation src on PYTHONPATH, `uv run pytest tests/unit/test_customer_sync.py tests/unit/test_customer_sync_contract.py -q`: **22 passed**. Added atomic detach/payload preservation, pending-operation rollback, empty-link conflict resolution and actual-server whole-batch retry coverage.
- The focused Vitest command above: **16 passed**, including cancel/confirm deletion and pending-child guidance. Existing JSDOM modal warning remains non-failing.
- Focused Ruff, mypy and ESLint passed. Frontend rebuild log: `.artifacts/customer-sync-frontend-delete.log`; generated-asset audit includes the new confirmation and pending-child guard markers.
- `npm run build` (production, 2 GiB Node heap) completed successfully, including TypeScript/Vite/PWA. `uv run python .artifacts/customer-sync-check-build.py` passed all 124 resource references and both new deletion markers. Latest customer chunk is `index.Ch9HtHul.js` (22,810 bytes), SHA-256 `94aae8c770f73acca4bc1868d36530c41c5a4161a4e19408f254ab570234713a`; this supersedes the earlier acceptance build hash above.
