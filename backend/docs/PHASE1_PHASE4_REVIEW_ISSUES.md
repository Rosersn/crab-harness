# Phase 1-4 Review Issues

Review scope: latest Phase 1-4 implementation after the current round of fixes.

Validation run:
`PYTHONPATH=. uv run --with pytest --with pytest-asyncio python -m pytest tests/test_platform.py tests/test_phase2_storage.py tests/test_phase3_agent.py tests/test_e2b_sandbox.py tests/test_artifacts_router.py tests/test_custom_agent.py tests/test_memory_updater.py tests/test_threads_router.py tests/test_uploads_router.py -q`

Result:
`194 passed, 11 warnings in 3.24s`

What was fixed in this pass:
- Legacy local thread cleanup route is no longer used by the frontend, and the public route now returns `410 Gone` instead of exposing a parallel delete path.
- Artifact access now validates thread ownership and can fall back to reading directly from E2B when the file only exists in the remote sandbox.
- Memory API is now per-user and backed by PostgreSQL instead of the old global file-backed getters.
- Harness no longer imports `crab_platform` directly for memory storage resolution; multi-tenant memory storage is injected via a resolver hook.
- Thread deletion now cleans external resources before removing the DB row: object storage uploads, checkpointer state, local thread directory, sandbox, and Redis thread lock.
- Run cancellation now has a distributed Redis signal instead of relying only on an in-process event.
- Join-stream behavior was upgraded from one-shot replay to polling-backed state replay while a run is still active.
- Upload flow now rejects duplicate filenames per thread, standardizes extracted markdown naming, and cleans sandbox copies best-effort.
- `config.example.yaml` now points cloud mode at PostgreSQL checkpointer settings instead of local SQLite.
- Generated `__pycache__` / `.pyc` files were removed from the new platform package tree.
- Filesystem-backed custom agents and the global `USER.md` profile are now hard-disabled in cloud mode at both API and runtime entry points, and the frontend agents screens were replaced with explicit unavailable states.
- Memory helpers were updated off deprecated `datetime.utcnow()` usage, reducing test noise and avoiding a future Python removal footgun.

## Remaining Findings

1. `[P1]` Memory updates are still best-effort and process-local.
Files: `backend/packages/harness/deerflow/agents/memory/queue.py:27-206`
Problem: the memory queue is still an in-process singleton with `threading.Timer` debounce and no durable backing store.
Impact: rolling restarts, worker crashes, or multi-instance scheduling can still lose queued memory updates. This is acceptable for non-critical enrichment, but it is not durable infrastructure.
Suggested fix: move memory updates onto a durable queue or job table (for example PG-backed jobs or Redis Streams) before depending on memory freshness operationally.

2. `[P1]` Join-stream semantics are improved but still not full LangGraph event replay.
Files: `backend/app/gateway/routers/langgraph_compat.py:642-695`
Problem: reconnect now polls checkpoint/PG state until the run finishes, which is a big improvement, but it still does not replay the original `messages` event stream or provide exact `joinStream` parity with LangGraph Server.
Impact: reconnecting clients can observe current state, but they still will not receive a faithful replay of every streamed event chunk in order.
Suggested fix: persist streamed run events (or publish them to a replayable stream) so reconnect can resume from the last acknowledged event instead of polling state snapshots.

3. `[P1]` Custom skill runtime wiring is only half complete.
Files: `backend/app/gateway/routers/skills.py:109-188`, `backend/packages/platform/crab_platform/agent/skill_loader.py:68-92`, `backend/packages/platform/crab_platform/sandbox/file_injector.py:98-133`
Problem: sandbox injection now supports existing BOS-backed custom skill directories, but there is still no upload/install API that lets a user create those BOS-backed custom skills through the product.
Impact: the runtime can execute preseeded custom skills, but the end-user custom-skill lifecycle is still incomplete.
Suggested fix: add a proper custom-skill package ingestion path that uploads/unpacks a skill bundle into BOS and records its directory prefix in `user_skill_configs`.

4. `[P2]` E2B cleanup paths still emit coroutine warnings in tests.
Files: `backend/packages/platform/crab_platform/sandbox/e2b_sandbox_provider.py`, `backend/tests/test_e2b_sandbox.py`
Problem: test runs still report `coroutine was never awaited` warnings around mocked `_touch_sandbox_last_seen` / `_clear_pg_sandbox` paths.
Impact: this does not currently fail the suite, but it points to brittle mocked cleanup paths and makes it easier to miss a real async cleanup bug later.
Suggested fix: tighten the provider tests so async cleanup helpers are mocked/awaited consistently and remove the remaining runtime warnings.
