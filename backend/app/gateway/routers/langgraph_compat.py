"""LangGraph SDK-compatible API endpoints.

Implements the subset of the LangGraph Server API that the frontend uses
via @langchain/langgraph-sdk, so we can remove the separate LangGraph Server
process and run agents directly inside the Gateway.

Endpoints:
  POST   /threads                          → create thread
  POST   /threads/search                   → list/search threads
  GET    /threads/{thread_id}/state        → get thread state
  POST   /threads/{thread_id}/state        → update thread state
  POST   /threads/{thread_id}/history      → get state history
  DELETE /threads/{thread_id}              → delete thread
  POST   /threads/{thread_id}/runs/stream  → start streaming run
  POST   /threads/{thread_id}/runs/{run_id}/stream  → join existing run
  POST   /threads/{thread_id}/runs/{run_id}/cancel  → cancel run
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.gateway.deps import get_current_user
from crab_platform.auth.interface import AuthenticatedUser
from crab_platform.context import RequestContext
from crab_platform.db import get_db
from crab_platform.db.models import Thread
from crab_platform.db.repos.message_repo import MessageRepo
from crab_platform.db.repos.thread_repo import RunRepo, ThreadRepo
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/langgraph", tags=["langgraph"])

# In-process cancellation registry: run_id → asyncio.Event
# Set the event to signal a running generator to stop.
_cancel_events: dict[uuid.UUID, asyncio.Event] = {}


@dataclass
class _RunLiveStreamState:
    latest_values_payload: str | None = None
    subscribers: set[asyncio.Queue[dict[str, str] | None]] = field(default_factory=set)


_run_live_streams: dict[uuid.UUID, _RunLiveStreamState] = {}

_LOCK_HEARTBEAT_INTERVAL = 120  # seconds between heartbeat extensions
_LOCK_HEARTBEAT_TTL = 600  # TTL to set on each heartbeat
_VISIBLE_STREAM_NODES = frozenset({"agent", "model", "tools"})
_INTERNAL_STREAM_NODE_SUFFIXES = (
    ".before_agent",
    ".after_agent",
    ".before_model",
    ".after_model",
)


# ── Request / Response schemas ──────────────────────────────────────────


class ThreadCreateRequest(BaseModel):
    thread_id: uuid.UUID | None = None
    metadata: dict[str, Any] | None = None


class ThreadResponse(BaseModel):
    thread_id: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)


class ThreadSearchRequest(BaseModel):
    limit: int = 50
    offset: int = 0
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    select: list[str] | None = None
    metadata: dict[str, Any] | None = None


class ThreadStateUpdateRequest(BaseModel):
    values: dict[str, Any]
    as_node: str | None = None


class ThreadHistoryRequest(BaseModel):
    limit: int = 10


class RunStreamRequest(BaseModel):
    input: dict[str, Any] | None = None
    assistant_id: str = "lead_agent"
    stream_mode: list[str] | None = None
    stream_subgraphs: bool = False
    stream_resumable: bool = False
    on_disconnect: str | None = None
    config: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


# ── Helpers ──────────────────────────────────────────────────────────────


async def _load_state_from_checkpointer(thread_id: uuid.UUID) -> dict[str, Any] | None:
    """Try to load thread state from the LangGraph checkpointer.

    Returns a values dict (messages, title, artifacts, todos, ...) or None
    if no checkpoint is available.
    """
    try:
        from deerflow.agents.checkpointer import make_checkpointer

        from langgraph.checkpoint.base import CheckpointTuple

        config = {"configurable": {"thread_id": str(thread_id)}}
        async with make_checkpointer() as checkpointer:
            checkpoint_tuple: CheckpointTuple | None = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is None or checkpoint_tuple.checkpoint is None:
                return None

            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            if not channel_values:
                return None

            # Serialize LangChain messages from checkpoint
            raw_messages = channel_values.get("messages", [])
            serialized = [_serialize_langchain_message(m) for m in raw_messages]

            return {
                "messages": serialized,
                "title": channel_values.get("title"),
                "artifacts": channel_values.get("artifacts", []),
                "todos": channel_values.get("todos", []),
            }
    except Exception:
        logger.debug("Failed to load state from checkpointer for thread %s", thread_id, exc_info=True)
        return None


def _merge_serialized_messages(
    existing_messages: list[dict[str, Any]] | None,
    new_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge streamed messages into prior state without dropping history."""
    if not existing_messages:
        return list(new_messages or [])
    if not new_messages:
        return list(existing_messages)

    merged: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}

    for message in existing_messages:
        message_id = message.get("id")
        if isinstance(message_id, str):
            index_by_id[message_id] = len(merged)
        merged.append(message)

    for message in new_messages:
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id in index_by_id:
            current = merged[index_by_id[message_id]]
            updated = {**current, **message}

            for key in ("tool_calls", "tool_call_id", "additional_kwargs", "usage_metadata"):
                if message.get(key) in (None, [], {}):
                    if current.get(key) not in (None, [], {}):
                        updated[key] = current[key]

            if message.get("content") in (None, "", []):
                if current.get("content") not in (None, "", []):
                    updated["content"] = current["content"]

            merged[index_by_id[message_id]] = updated
            continue
        if isinstance(message_id, str):
            index_by_id[message_id] = len(merged)
        merged.append(message)

    return merged


def _messages_to_persist(
    messages: list[dict[str, Any]],
    existing_message_ids: set[str],
) -> list[dict[str, Any]]:
    """Return newly generated non-human messages in stable order for PG persistence."""
    persisted: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for message in messages:
        if message.get("type") == "human":
            continue

        message_id = message.get("id")
        if isinstance(message_id, str):
            if message_id in existing_message_ids or message_id in seen_ids:
                continue
            seen_ids.add(message_id)

        persisted.append(message)

    return persisted


def _filter_serialized_messages_by_id(
    messages: list[dict[str, Any]],
    excluded_ids: set[str],
) -> list[dict[str, Any]]:
    """Remove messages whose ids are known to belong to internal middleware calls."""
    if not excluded_ids:
        return list(messages)

    return [
        message
        for message in messages
        if not (
            isinstance(message.get("id"), str)
            and message["id"] in excluded_ids
        )
    ]


def _is_internal_stream_message(metadata: dict[str, Any] | None) -> bool:
    """Whether a streamed message belongs to an internal middleware/model node."""
    if not isinstance(metadata, dict):
        return False

    langgraph_node = metadata.get("langgraph_node")
    if not isinstance(langgraph_node, str) or not langgraph_node:
        return False

    if langgraph_node in _VISIBLE_STREAM_NODES:
        return False

    return any(langgraph_node.endswith(suffix) for suffix in _INTERNAL_STREAM_NODE_SUFFIXES)


def _serialize_stream_message(msg) -> dict[str, Any]:
    """Serialize a streaming message/chunk for the frontend SDK.

    The LangGraph React SDK expects flattened message chunk dictionaries for
    `messages` SSE events, not the nested `{type, data}` structure returned by
    `message_to_dict()`.
    """
    if hasattr(msg, "model_dump"):
        return msg.model_dump(mode="json")
    return _serialize_langchain_message(msg)


def _run_content_location(thread_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Build the run URL surfaced to the LangGraph SDK via Content-Location."""
    return f"/threads/{thread_id}/runs/{run_id}"


def _has_active_run_stream(run_id: uuid.UUID) -> bool:
    """Whether this gateway process is still actively streaming the run."""
    return run_id in _cancel_events


def _get_or_create_run_live_stream(run_id: uuid.UUID) -> _RunLiveStreamState:
    state = _run_live_streams.get(run_id)
    if state is None:
        state = _RunLiveStreamState()
        _run_live_streams[run_id] = state
    return state


async def _publish_run_live_event(run_id: uuid.UUID, event: dict[str, str]) -> None:
    state = _run_live_streams.get(run_id)
    if state is None:
        return
    if event.get("event") == "values":
        state.latest_values_payload = event.get("data")
    for subscriber in list(state.subscribers):
        await subscriber.put(event)


async def _close_run_live_stream(run_id: uuid.UUID) -> None:
    state = _run_live_streams.pop(run_id, None)
    if state is None:
        return
    for subscriber in list(state.subscribers):
        await subscriber.put(None)
    state.subscribers.clear()


def _thread_to_response(thread: Thread, values: dict | None = None) -> ThreadResponse:
    return ThreadResponse(
        thread_id=str(thread.id),
        created_at=thread.created_at.isoformat() if thread.created_at else datetime.now(UTC).isoformat(),
        updated_at=thread.updated_at.isoformat() if thread.updated_at else datetime.now(UTC).isoformat(),
        metadata=thread.metadata_ or {},
        values=values or _build_thread_values(thread),
    )


def _build_thread_values(thread: Thread) -> dict[str, Any]:
    """Build a minimal values dict for thread list display."""
    values: dict[str, Any] = {}
    if thread.title:
        values["title"] = thread.title
    return values


def _serialize_values_payload(
    messages: list[dict[str, Any]],
    title: str | None,
    artifacts: list[Any],
    todos: list[Any],
) -> str:
    return json.dumps(
        {
            "messages": messages,
            "title": title,
            "artifacts": artifacts,
            "todos": todos,
        }
    )


# ── Thread CRUD ──────────────────────────────────────────────────────────


@router.post("/threads", response_model=ThreadResponse, status_code=201)
async def create_thread(
    body: ThreadCreateRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new thread."""
    repo = ThreadRepo(db)
    requested_thread_id = body.thread_id if body else None

    if requested_thread_id is not None:
        existing = await repo.get(requested_thread_id)
        if existing is not None:
            if existing.user_id != user.user_id:
                raise HTTPException(status_code=409, detail="Thread ID already exists")
            return _thread_to_response(existing)

    thread = await repo.create(
        id=requested_thread_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        metadata_=body.metadata if body else None,
    )
    await db.commit()
    return _thread_to_response(thread)


@router.post("/threads/search", response_model=list[ThreadResponse])
async def search_threads(
    body: ThreadSearchRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search/list threads for the current user."""
    if body is None:
        body = ThreadSearchRequest()
    repo = ThreadRepo(db)
    threads = await repo.list_for_user(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        limit=body.limit,
        offset=body.offset,
    )
    return [_thread_to_response(t) for t in threads]


@router.get("/threads/{thread_id}/state")
async def get_thread_state(
    thread_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current state of a thread (messages, title, artifacts, todos)."""
    thread = await _get_owned_thread(thread_id, user, db)

    # Try to load full state from checkpointer (includes artifacts, todos, etc.)
    checkpoint_values = await _load_state_from_checkpointer(thread_id)
    if checkpoint_values is not None:
        # Checkpointer has full state — use it
        return {
            "values": checkpoint_values,
            "next": [],
            "tasks": [],
            "created_at": thread.created_at.isoformat() if thread.created_at else None,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        }

    # Fallback: reconstruct from PG messages
    msg_repo = MessageRepo(db)
    messages = await msg_repo.list_for_thread(thread_id)

    serialized_messages = []
    for msg in messages:
        m: dict[str, Any] = {
            "type": msg.role,
            "content": msg.content,
            "id": str(msg.id),
        }
        if msg.tool_calls:
            m["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        serialized_messages.append(m)

    values: dict[str, Any] = {
        "messages": serialized_messages,
        "title": thread.title,
        "artifacts": [],
        "todos": [],
    }

    return {
        "values": values,
        "next": [],
        "tasks": [],
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
    }


@router.post("/threads/{thread_id}/state")
async def update_thread_state(
    thread_id: uuid.UUID,
    body: ThreadStateUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update thread state (e.g. rename via title)."""
    thread = await _get_owned_thread(thread_id, user, db)

    # Handle title update
    if "title" in body.values:
        thread.title = body.values["title"]
        thread.updated_at = datetime.now(UTC)
        await db.flush()
        await db.commit()

    return {"ok": True}


@router.post("/threads/{thread_id}/history")
async def get_thread_history(
    thread_id: uuid.UUID,
    body: ThreadHistoryRequest | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get state history (returns latest state snapshot for compatibility)."""
    thread = await _get_owned_thread(thread_id, user, db)

    checkpoint_values = await _load_state_from_checkpointer(thread_id)
    if checkpoint_values is not None:
        return [{
            "values": checkpoint_values,
            "next": [],
            "tasks": [],
        }]

    msg_repo = MessageRepo(db)
    messages = await msg_repo.list_for_thread(thread_id)

    serialized = []
    for msg in messages:
        m: dict[str, Any] = {"type": msg.role, "content": msg.content, "id": str(msg.id)}
        if msg.tool_calls:
            m["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        serialized.append(m)

    state = {
        "values": {
            "messages": serialized,
            "title": thread.title,
            "artifacts": [],
            "todos": [],
        },
        "next": [],
        "tasks": [],
    }
    return [state]


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a thread and all associated data."""
    thread = await _get_owned_thread(thread_id, user, db)
    run_repo = RunRepo(db)
    active_run = await run_repo.get_active_run(thread_id)
    if active_run is not None:
        raise HTTPException(status_code=409, detail="Cannot delete a thread with an active run")

    await _cleanup_thread_resources(thread, db)
    repo = ThreadRepo(db)
    await repo.delete(thread_id)
    await db.commit()


# ── Run streaming ────────────────────────────────────────────────────────


@router.post("/threads/{thread_id}/runs/stream")
async def stream_run(
    thread_id: uuid.UUID,
    body: RunStreamRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a new streaming run on a thread.

    This creates a Run record, constructs the Agent, and streams SSE events
    in the LangGraph wire format expected by @langchain/langgraph-sdk.
    """
    thread = await _get_owned_thread(thread_id, user, db)

    # Extract configurable from body
    configurable = {}
    if body.config and "configurable" in body.config:
        configurable = body.config["configurable"]
    if body.context:
        configurable.update(body.context)

    if configurable.get("agent_name"):
        raise HTTPException(
            status_code=410,
            detail="Custom agents are not supported in cloud mode.",
        )

    # Create run record
    run_repo = RunRepo(db)
    run = await run_repo.create(
        thread_id=thread_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    run_id = run.id

    # Acquire thread-level distributed lock (Redis)
    from crab_platform.redis import acquire_thread_lock

    thread_lock = await acquire_thread_lock(thread_id, run_id)
    if not thread_lock.acquired:
        await run_repo.update_status(run_id, "cancelled", error="Thread busy")
        await db.commit()
        raise HTTPException(status_code=409, detail="Another run is active on this thread")

    # Extract user message from input
    input_messages = []
    if body.input and "messages" in body.input:
        input_messages = body.input["messages"]

    # Save user message(s) to PG
    msg_repo = MessageRepo(db)
    for msg_data in input_messages:
        role = msg_data.get("type", "human")
        content = msg_data.get("content", "")
        await msg_repo.create(
            thread_id=thread_id,
            tenant_id=user.tenant_id,
            role=role,
            content=content,
            run_id=run_id,
        )

    # Mark run as running
    from crab_platform.config.platform_config import get_platform_config

    platform_config = get_platform_config()
    await run_repo.update_status(run_id, "running", gateway_instance_id=platform_config.instance_id)
    await db.commit()

    # Update thread.updated_at
    thread.updated_at = datetime.now(UTC)
    await db.flush()
    await db.commit()

    # Build RequestContext for the agent
    ctx = RequestContext(
        request_id=str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        thread_id=thread_id,
        model_name=configurable.get("model_name"),
        thinking_enabled=configurable.get("thinking_enabled", True),
        reasoning_effort=configurable.get("reasoning_effort"),
        is_plan_mode=configurable.get("is_plan_mode", False),
        subagent_enabled=configurable.get("subagent_enabled", False),
        max_concurrent_subagents=configurable.get("max_concurrent_subagents", 3),
        agent_name=configurable.get("agent_name"),
    )

    # Register cancellation event for this run
    cancel_event = asyncio.Event()
    _cancel_events[run_id] = cancel_event

    keep_running_on_disconnect = (
        body.on_disconnect == "continue" or body.stream_resumable
    )
    live_stream = _get_or_create_run_live_stream(run_id)

    async def run_agent_stream() -> None:
        """Run the agent independently from the client connection."""
        from langchain_core.messages import message_chunk_to_message

        messages: list[Any] = []
        title: str | None = None
        artifacts: list[Any] = []
        todos: list[Any] = []
        checkpoint_state = await _load_state_from_checkpointer(thread_id)
        full_state_messages = list((checkpoint_state or {}).get("messages", []))
        title = (checkpoint_state or {}).get("title")
        artifacts = list((checkpoint_state or {}).get("artifacts", []))
        todos = list((checkpoint_state or {}).get("todos", []))
        live_stream.latest_values_payload = _serialize_values_payload(
            full_state_messages,
            title,
            artifacts,
            todos,
        )
        streamed_message_accumulators: dict[str, Any] = {}
        existing_message_ids = {
            str(msg["id"])
            for msg in (checkpoint_state or {}).get("messages", [])
            if isinstance(msg, dict) and msg.get("id")
        }

        # Start lock heartbeat background task
        async def _heartbeat_loop():
            while not cancel_event.is_set():
                try:
                    await asyncio.sleep(_LOCK_HEARTBEAT_INTERVAL)
                    if cancel_event.is_set():
                        break
                    extended = await thread_lock.extend(_LOCK_HEARTBEAT_TTL)
                    if not extended:
                        logger.warning("Lock heartbeat lost for run %s", run_id)
                        cancel_event.set()
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.debug("Heartbeat error for run %s", run_id, exc_info=True)

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        try:
            # Lazily import agent construction to avoid circular imports at module level
            from langchain_core.messages import HumanMessage

            from crab_platform.agent.factory import make_tenant_agent
            from crab_platform.db import get_session_factory
            from deerflow.agents.checkpointer import make_checkpointer

            async with make_checkpointer() as checkpointer:
                # Build tenant-scoped agent with per-user tools, memory, skills
                # Use a fresh DB session since DI session may be stale inside SSE generator
                async with get_session_factory()() as agent_db:
                    agent, runnable_config = await make_tenant_agent(
                        ctx,
                        agent_db,
                        checkpointer=checkpointer,
                        recursion_limit=body.config.get("recursion_limit", 100) if body.config else 100,
                    )

                # Build input state
                human_text = ""
                if input_messages:
                    content = input_messages[0].get("content", "")
                    if isinstance(content, list):
                        # Extract text from content blocks
                        parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                parts.append(block)
                        human_text = "\n".join(parts)
                    else:
                        human_text = str(content)

                input_state = {"messages": [HumanMessage(content=human_text)]}
                stream_context: dict[str, Any] = {
                    "thread_id": str(thread_id),
                    "user_id": str(ctx.user_id),
                    "tenant_id": str(ctx.tenant_id),
                }
                agent_name = configurable.get("agent_name")
                if isinstance(agent_name, str) and agent_name:
                    stream_context["agent_name"] = agent_name

                # Stream the agent.
                # We need both:
                # - messages: token/message chunks for incremental UI rendering
                # - values: full state snapshots for thread state + persistence
                streamed_message_ids: set[str] = set()
                internal_stream_message_ids: set[str] = set()

                async for mode, data in agent.astream(
                    input_state,
                    config=runnable_config,
                    context=stream_context,
                    stream_mode=["messages", "values"],
                ):
                    # Check for cancellation between chunks
                    from crab_platform.redis import is_run_cancellation_requested

                    if cancel_event.is_set() or await is_run_cancellation_requested(run_id):
                        logger.info("Run %s cancelled mid-stream", run_id)
                        from crab_platform.db import get_session_factory as _gsf
                        async with _gsf()() as cancel_db:
                            await RunRepo(cancel_db).update_status(run_id, "cancelled", error="Cancelled by user")
                            await cancel_db.commit()
                        await _publish_run_live_event(
                            run_id,
                            {"event": "error", "data": json.dumps({"error": "Run cancelled"})},
                        )
                        return

                    if mode == "messages":
                        streamed_message, metadata = data
                        message_metadata = dict(metadata) if isinstance(metadata, dict) else {}
                        serialized_stream_message = _serialize_stream_message(streamed_message)
                        stream_message_id = serialized_stream_message.get("id")
                        if _is_internal_stream_message(message_metadata):
                            if isinstance(stream_message_id, str):
                                internal_stream_message_ids.add(stream_message_id)
                                full_state_messages = _filter_serialized_messages_by_id(
                                    full_state_messages,
                                    internal_stream_message_ids,
                                )
                            logger.debug(
                                "Skipping internal stream message from %s for run %s",
                                message_metadata.get("langgraph_node"),
                                run_id,
                            )
                            continue
                        if isinstance(stream_message_id, str) and stream_message_id not in existing_message_ids:
                            streamed_message_ids.add(stream_message_id)
                            accumulated_message = streamed_message_accumulators.get(stream_message_id)
                            if accumulated_message is None:
                                accumulated_message = streamed_message
                            else:
                                try:
                                    accumulated_message = accumulated_message + streamed_message
                                except TypeError:
                                    accumulated_message = streamed_message
                            streamed_message_accumulators[stream_message_id] = accumulated_message
                            full_message = message_chunk_to_message(accumulated_message)
                            full_state_messages = _merge_serialized_messages(
                                full_state_messages,
                                [_serialize_langchain_message(full_message)],
                            )
                            live_stream.latest_values_payload = _serialize_values_payload(
                                full_state_messages,
                                title,
                                artifacts,
                                todos,
                            )
                        message_metadata["run_id"] = str(run_id)
                        await _publish_run_live_event(
                            run_id,
                            {
                                "event": "messages",
                                "data": json.dumps([serialized_stream_message, message_metadata]),
                            },
                        )
                        continue

                    chunk = data
                    messages = chunk.get("messages", [])

                    if "title" in chunk:
                        title = chunk.get("title")
                    if "artifacts" in chunk:
                        artifacts = chunk.get("artifacts", [])
                    if "todos" in chunk:
                        todos = chunk.get("todos", [])

                    serialized = []
                    for msg in messages:
                        s = _serialize_langchain_message(msg)
                        msg_id = s.get("id")
                        if isinstance(msg_id, str) and msg_id in internal_stream_message_ids:
                            continue
                        serialized.append(s)
                        if isinstance(msg_id, str) and msg_id not in existing_message_ids:
                            streamed_message_ids.add(msg_id)

                    full_state_messages = _merge_serialized_messages(
                        full_state_messages,
                        serialized,
                    )
                    full_state_messages = _filter_serialized_messages_by_id(
                        full_state_messages,
                        internal_stream_message_ids,
                    )
                    values_payload = _serialize_values_payload(
                        full_state_messages,
                        title,
                        artifacts,
                        todos,
                    )
                    live_stream.latest_values_payload = values_payload
                    await _publish_run_live_event(
                        run_id,
                        {
                            "event": "values",
                            "data": values_payload,
                        },
                    )

            # Save AI response messages to PG using a fresh session
            from crab_platform.db import get_session_factory

            async with get_session_factory()() as post_db:
                post_msg_repo = MessageRepo(post_db)
                post_run_repo = RunRepo(post_db)

                for msg in _messages_to_persist(full_state_messages, existing_message_ids):
                    role = msg.get("type", "system")
                    await post_msg_repo.create(
                        thread_id=thread_id,
                        tenant_id=user.tenant_id,
                        role=role,
                        content=msg.get("content"),
                        run_id=run_id,
                        tool_calls=msg.get("tool_calls"),
                        tool_call_id=msg.get("tool_call_id"),
                    )

                # Update title if generated
                if title:
                    from crab_platform.db.models import Thread as ThreadModel
                    from sqlalchemy import update as sa_update

                    await post_db.execute(
                        sa_update(ThreadModel)
                        .where(ThreadModel.id == thread_id)
                        .values(title=title, updated_at=datetime.now(UTC))
                    )

                # Mark run succeeded
                await post_run_repo.update_status(run_id, "succeeded")
                await post_db.commit()

            await _publish_run_live_event(
                run_id,
                {"event": "end", "data": json.dumps({})},
            )

        except asyncio.CancelledError:
            # The run itself was cancelled (explicit stop, shutdown, or non-resumable disconnect).
            try:
                from crab_platform.db import get_session_factory as _gsf
                async with _gsf()() as err_db:
                    await RunRepo(err_db).update_status(run_id, "cancelled", error="Run cancelled")
                    await err_db.commit()
            except Exception:
                logger.debug("Failed to mark cancelled run %s", run_id, exc_info=True)
            await thread_lock.release()
        except Exception as e:
            logger.exception("Run %s failed: %s", run_id, e)
            try:
                from crab_platform.db import get_session_factory as _gsf
                async with _gsf()() as err_db:
                    err_run_repo = RunRepo(err_db)
                    err_msg_repo = MessageRepo(err_db)
                    # Save any partial messages generated before failure
                    for msg in _messages_to_persist(full_state_messages, existing_message_ids):
                        try:
                            await err_msg_repo.create(
                                thread_id=thread_id,
                                tenant_id=user.tenant_id,
                                role=msg.get("type", "system"),
                                content=msg.get("content"),
                                run_id=run_id,
                                tool_calls=msg.get("tool_calls"),
                                tool_call_id=msg.get("tool_call_id"),
                            )
                        except Exception:
                            break  # Don't fail on partial save
                    await err_run_repo.update_status(run_id, "failed", error=str(e)[:500])
                    await err_db.commit()
            except Exception:
                logger.exception("Failed to update run status")
            await thread_lock.release()
            await _publish_run_live_event(
                run_id,
                {"event": "error", "data": json.dumps({"error": str(e)})},
            )
        else:
            await thread_lock.release()
        finally:
            # Always clean up heartbeat task and cancel registry
            heartbeat_task.cancel()
            _cancel_events.pop(run_id, None)
            from crab_platform.redis import clear_run_cancellation
            await clear_run_cancellation(run_id)
            await _close_run_live_stream(run_id)

    run_task = asyncio.create_task(run_agent_stream())

    async def event_generator():
        """SSE event generator that relays queued events to the current client."""
        subscriber_queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()
        live_stream.subscribers.add(subscriber_queue)
        try:
            while True:
                event = await subscriber_queue.get()
                if event is None:
                    return
                yield event
        except asyncio.CancelledError:
            if not keep_running_on_disconnect:
                cancel_event.set()
                run_task.cancel()
            raise
        finally:
            live_stream.subscribers.discard(subscriber_queue)

    return EventSourceResponse(
        event_generator(),
        headers={"Content-Location": _run_content_location(thread_id, run_id)},
    )


@router.api_route("/threads/{thread_id}/runs/{run_id}/stream", methods=["GET", "POST"])
async def join_run_stream(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Join / reconnect to an existing run's event stream.

    If the run is still active, returns current state and any future events.
    If finished, returns the final state.
    """
    await _get_owned_thread(thread_id, user, db)

    run_repo = RunRepo(db)
    run = await run_repo.get(run_id)
    if run is None or run.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Run not found")

    async def replay_generator():
        from crab_platform.db import get_session_factory

        last_payload: str | None = None
        while True:
            async with get_session_factory()() as join_db:
                join_run_repo = RunRepo(join_db)
                current_run = await join_run_repo.get(run_id)
                if (
                    current_run is not None
                    and current_run.status == "running"
                    and not _has_active_run_stream(run_id)
                ):
                    logger.warning(
                        "Run %s is marked running but has no active stream; closing it as cancelled",
                        run_id,
                    )
                    await join_run_repo.update_status(
                        run_id,
                        "cancelled",
                        error="Run stream disconnected before completion",
                    )
                    await join_db.commit()
                    current_run = await join_run_repo.get(run_id)

                live_stream = _run_live_streams.get(run_id)
                if current_run is not None and current_run.status == "running" and live_stream is not None:
                    subscriber_queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()
                    live_stream.subscribers.add(subscriber_queue)
                    try:
                        if live_stream.latest_values_payload is not None:
                            last_payload = live_stream.latest_values_payload
                            yield {"event": "values", "data": live_stream.latest_values_payload}
                        while True:
                            event = await subscriber_queue.get()
                            if event is None:
                                return
                            if event.get("event") == "values":
                                payload = event.get("data")
                                if payload == last_payload:
                                    continue
                                last_payload = payload
                            yield event
                    finally:
                        live_stream.subscribers.discard(subscriber_queue)

                checkpoint_values = await _load_state_from_checkpointer(thread_id)
                if checkpoint_values is not None:
                    current_values = checkpoint_values
                else:
                    join_messages = await MessageRepo(join_db).list_for_thread(thread_id)
                    join_thread = await ThreadRepo(join_db).get(thread_id)
                    current_values = {
                        "messages": [
                            {"type": msg.role, "content": msg.content, "id": str(msg.id)}
                            for msg in join_messages
                        ],
                        "title": join_thread.title if join_thread else None,
                        "artifacts": [],
                        "todos": [],
                    }

            payload = json.dumps(current_values)
            if payload != last_payload:
                last_payload = payload
                yield {"event": "values", "data": payload}

            if current_run is None or current_run.status in {"succeeded", "failed", "cancelled"}:
                yield {"event": "end", "data": json.dumps({})}
                return

            await asyncio.sleep(1)

    return EventSourceResponse(
        replay_generator(),
        headers={"Content-Location": _run_content_location(thread_id, run_id)},
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel", status_code=200)
async def cancel_run(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running run."""
    await _get_owned_thread(thread_id, user, db)

    run_repo = RunRepo(db)
    run = await run_repo.get(run_id)
    if run is None or run.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status == "running":
        await run_repo.update_status(run_id, "cancelled", error="Cancelled by user")
        await db.commit()
        from crab_platform.redis import request_run_cancellation
        await request_run_cancellation(run_id)
        # Signal the running generator to stop
        cancel_ev = _cancel_events.get(run_id)
        if cancel_ev is not None:
            cancel_ev.set()

    return {"ok": True}


# ── Internal helpers ─────────────────────────────────────────────────────


async def _get_owned_thread(
    thread_id: uuid.UUID,
    user: AuthenticatedUser,
    db: AsyncSession,
) -> Thread:
    """Fetch a thread and verify ownership."""
    repo = ThreadRepo(db)
    thread = await repo.get(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Thread not owned by current user")
    return thread


async def _cleanup_thread_resources(thread: Thread, db: AsyncSession) -> None:
    """Delete external thread resources before removing the DB row."""
    from crab_platform.redis import release_thread_lock
    from crab_platform.storage import get_object_storage
    from deerflow.agents.checkpointer import make_checkpointer

    storage = get_object_storage()

    # Delete uploaded objects from object storage before the DB cascade removes metadata.
    from crab_platform.db.repos.upload_repo import UploadRepo
    uploads = await UploadRepo(db).list_for_thread(thread.id, thread.user_id)
    for upload in uploads:
        try:
            await storage.delete(upload.bos_key)
            if upload.markdown_bos_key:
                await storage.delete(upload.markdown_bos_key)
        except Exception:
            logger.warning("Failed to delete object storage key %s during thread cleanup", upload.bos_key, exc_info=True)

    # Clear checkpoint state so deleted threads cannot be resumed from old snapshots.
    try:
        async with make_checkpointer() as checkpointer:
            if hasattr(checkpointer, "adelete_thread"):
                await checkpointer.adelete_thread(str(thread.id))
            elif hasattr(checkpointer, "delete_thread"):
                await asyncio.to_thread(checkpointer.delete_thread, str(thread.id))
    except Exception:
        logger.warning("Failed to clear checkpointer state for thread %s", thread.id, exc_info=True)

    # Remove lingering local thread directory.
    try:
        get_paths().delete_thread_dir(str(thread.id))
    except Exception:
        logger.warning("Failed to delete local thread directory for %s", thread.id, exc_info=True)

    # Best-effort release of any leftover thread lock.
    try:
        await release_thread_lock(thread.id, "delete")
    except Exception:
        logger.debug("Failed to clear thread lock for %s", thread.id, exc_info=True)


def _serialize_langchain_message(msg) -> dict[str, Any]:
    """Serialize a LangChain message to the format expected by the frontend SDK."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if isinstance(msg, AIMessage):
        d: dict[str, Any] = {
            "type": "ai",
            "content": msg.content,
            "id": getattr(msg, "id", None),
        }
        if msg.tool_calls:
            d["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"], "id": tc.get("id")}
                for tc in msg.tool_calls
            ]
        reasoning_content = msg.additional_kwargs.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            d["additional_kwargs"] = {
                **d.get("additional_kwargs", {}),
                "reasoning_content": reasoning_content,
            }
        if getattr(msg, "usage_metadata", None):
            d["usage_metadata"] = msg.usage_metadata
        return d
    if isinstance(msg, ToolMessage):
        return {
            "type": "tool",
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
            "name": getattr(msg, "name", None),
            "tool_call_id": getattr(msg, "tool_call_id", None),
            "id": getattr(msg, "id", None),
        }
    if isinstance(msg, HumanMessage):
        return {
            "type": "human",
            "content": msg.content,
            "id": getattr(msg, "id", None),
        }
    if isinstance(msg, SystemMessage):
        return {
            "type": "system",
            "content": msg.content,
            "id": getattr(msg, "id", None),
        }
    return {"type": "unknown", "content": str(msg), "id": getattr(msg, "id", None)}
