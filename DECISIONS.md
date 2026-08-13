# Engineering decisions

## Invariants identified

- A boot is identified by `(deviceId, bootId)`. Registering the same boot is idempotent, while each newly registered boot for a device receives a greater server-assigned generation.
- A telemetry event is identified by `(deviceId, bootId, sequence)`. Repeated delivery may return success, but it must create at most one audit row and must not change or republish current state.
- Current state exists per `(deviceId, metric)` and advances only when the incoming `(generation, sequence)` is greater than the stored value. `deviceTime` is diagnostic metadata and is never used for ordering.
- Raw telemetry is an audit log. Delayed or stale events remain in history even when they do not become current state.
- Realtime publication happens only after a successful database commit and only when authoritative current state changed.
- WebSocket messages are bounded, best-effort notifications. The database snapshot is the source of truth after startup or reconnection.

## Incidents fixed

- Events from two boots with the same sequence were incorrectly treated as duplicates because the original schema omitted `boot_id` from its unique constraint.
- A future or incorrect device clock could prevent a valid later sequence from becoming current, while delayed events from an older boot could move state backward.
- The service published a preview before ingestion, allowing duplicates, stale events, or failed transactions to produce false dashboard updates.
- Realtime publication awaited every client serially, so one slow client could delay healthy clients and allow unbounded pending work.
- A reconnecting dashboard could permanently miss state changes that occurred while its WebSocket was disconnected.
- The declared runtime dependencies did not include a WebSocket implementation, so a real Uvicorn process rejected `/ws` upgrades even though in-process API tests passed.

## Design choices and trade-offs

- Migration `002` rebuilds `telemetry_events` with the correct unique key because SQLite cannot alter a table-level unique constraint in place. It copies existing rows with their original IDs, then recreates the audit index.
- The current-state upsert compares server-assigned generation first and sequence second. This implements the protocol directly in the transaction and deliberately ignores device clocks for ordering.
- Ingestion returns the committed state only when current state changed. The service publishes that result after `ingest` completes, removing the unsafe preview path.
- Each WebSocket client owns a sender task and a bounded queue of 100 messages by default. Publication uses `put_nowait`; overflow removes the slow client and closes it with code `1013`, allowing healthy clients to continue.
- The dashboard fetches `/api/devices` after every successful WebSocket connection. Messages arriving during that fetch are queued and then applied using the same generation-and-sequence ordering rule, preventing stale data from replacing a newer snapshot.
- `websockets` is declared explicitly instead of installing every optional Uvicorn extra, keeping the runtime dependency change focused.
- The fixes were separated into three implementation pull requests so event identity, transaction boundaries, and realtime behavior could be reviewed independently.

## Schema or API compatibility concerns

- Existing databases are upgraded through the versioned migration; the local database is not deleted or reset. Existing telemetry event IDs and audit rows are preserved.
- The corrected unique constraint is intentionally less restrictive across different boots while remaining idempotent within one boot.
- Existing HTTP endpoints and response shapes remain compatible. The optional `websocket_buffer_limit` argument on `create_app` defaults to `100`, so existing callers retain the same construction behavior.
- Installation now includes `websockets==17.0.1` so the documented WebSocket endpoint works under the real server.

## Remaining risks or incomplete work

- Browser reconnect behavior was manually verified, but the repository has no automated browser test harness. The backend recovery contract is covered by an API/WebSocket integration test.
- A process failure after the database commit but before notification can still lose a WebSocket message. This is acceptable for a best-effort channel because the dashboard reloads the authoritative snapshot after reconnecting.
- Queue capacity is bounded per client, not globally. Total realtime memory can still grow with the number of connected clients, which is acceptable for the assignment’s single-machine scope.
- The hub is process-local and is not designed for multiple worker processes. SQLite access is also synchronous, so a higher-scale deployment would need a different concurrency and persistence design.
- Migration `002` copies the audit table inside one transaction. A very large production database would require capacity planning and a staged migration.
- No CI workflow was included in the starter repository. Compilation, tests, whitespace checks, API checks, and manual runtime scenarios were run locally.
