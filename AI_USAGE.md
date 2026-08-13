# AI usage record

## Tools used

- OpenAI Codex in ChatGPT was used as a coding and review assistant.
- I ran all Git, GitHub CLI, test, server, simulator, and API commands myself and reviewed the resulting diffs before merging each pull request.

## Important prompts or prompt summaries

- Inspect the protocol, runtime contract, starter implementation, and tests; identify the invariants and design focused failure cases before changing code.
- Correct telemetry identity and ordering without deleting the existing SQLite database or losing audit history.
- Move realtime publication to the correct side of the database transaction and prove that duplicates, stale events, and failed ingestion do not publish.
- Design bounded per-client WebSocket delivery so a slow or broken connection cannot block healthy clients.
- Recover browser state after a disconnect while preventing a snapshot/realtime race from moving displayed state backward.
- Review each pull-request diff for scope, formatting, missing edge cases, and compatibility before merge.

## Generated output rejected or corrected

- The first database edit had poor indentation and extra blank lines. I corrected the formatting, reran the checks, and added a separate cleanup commit before merging.
- The initial database tests did not explicitly cover a lower sequence from the same boot arriving late. I added that missing stale-event test after reviewing the first test set.
- In-process `TestClient` tests passed without a real WebSocket runtime implementation. Manual Uvicorn testing exposed repeated unsupported-upgrade warnings and `/ws` failures, so I added the explicit `websockets` dependency and repeated the real-server test.
- The first reconnect design reapplied every queued notification after loading the snapshot. I recognized that an older queued notification could regress a newer snapshot and added generation-and-sequence comparison in the browser.
- I did not accept passing tests as sufficient evidence. I also inspected SQL constraints, migration behavior, operation order, task cleanup, dependency metadata, and the remote pull-request diffs.

## Verification performed

- Ran `python -m compileall -q telemetry_gateway simulator.py tests`.
- Ran the complete test suite after each change; the final suite has 19 passing tests covering migration preservation, duplicates, restarts, clock skew, reordering, transaction failure, publication order, slow/broken clients, and reconnect recovery.
- Ran `git diff --check` and reviewed staged and remote diffs before each merge.
- Verified boot-registration idempotency and telemetry duplicate handling using real HTTP requests.
- Verified that a higher sequence with an earlier `deviceTime` becomes current, while `/api/events` retains the raw audit history.
- Verified that telemetry from an unregistered boot returns `409 unknown_boot`.
- Started the application with Uvicorn and confirmed liveness, readiness, HTTP, and real WebSocket connectivity.
- Ran the chaos simulator with four devices and observed duplicates, delays, restarts, clock skew, and live dashboard updates.
- Restarted the server and confirmed that the dashboard reconnected and fetched `/api/devices` to recover authoritative state.
