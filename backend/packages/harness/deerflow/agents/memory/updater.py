"""Memory updater for reading, writing, and updating memory data."""

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from deerflow.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from deerflow.agents.memory.storage import get_memory_storage
from deerflow.config.memory_config import get_memory_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)

_memory_storage_resolver: Callable[[str, str], Any] | None = None


def set_memory_storage_resolver(resolver: Callable[[str, str], Any] | None) -> None:
    """Register an optional user-scoped memory storage resolver.

    The harness stays platform-agnostic by treating this as an injectable hook.
    Multi-tenant runtimes can register a resolver that returns a user-scoped
    storage backend, while local single-user mode simply leaves it unset.
    """
    global _memory_storage_resolver
    _memory_storage_resolver = resolver

def get_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """Get the current memory data via storage provider."""
    return get_memory_storage().load(agent_name)

def reload_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """Reload memory data via storage provider."""
    return get_memory_storage().reload(agent_name)


def _extract_text(content: Any) -> str:
    """Extract plain text from LLM response content (str or list of content blocks).

    Modern LLMs may return structured content as a list of blocks instead of a
    plain string, e.g. [{"type": "text", "text": "..."}]. Using str() on such
    content produces Python repr instead of the actual text, breaking JSON
    parsing downstream.

    String chunks are concatenated without separators to avoid corrupting
    chunked JSON/text payloads. Dict-based text blocks are treated as full text
    blocks and joined with newlines for readability.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)


# Matches sentences that describe a file-upload *event* rather than general
# file-related work.  Deliberately narrow to avoid removing legitimate facts
# such as "User works with CSV files" or "prefers PDF export".
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """Remove sentences about file uploads from all memory summaries and facts.

    Uploaded files are session-scoped; persisting upload events in long-term
    memory causes the agent to search for non-existent files in future sessions.
    """
    # Scrub summaries in user/history sections
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    # Also remove any facts that describe upload events
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]

    return memory_data


def _fact_content_key(content: Any) -> str | None:
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped


class MemoryUpdater:
    """Updates memory using LLM based on conversation context."""

    def __init__(self, model_name: str | None = None):
        """Initialize the memory updater.

        Args:
            model_name: Optional model name to use. If None, uses config or default.
        """
        self._model_name = model_name

    def _get_model(self):
        """Get the model for memory updates."""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)

    @staticmethod
    def _resolve_storage(user_id: str | None = None, tenant_id: str | None = None) -> Any:
        """Resolve the appropriate memory storage backend.

        When user_id and tenant_id are provided and a resolver has been
        registered, creates a user-scoped storage backend. Otherwise falls back
        to the global file-based singleton used by local mode.
        """
        if user_id and tenant_id and _memory_storage_resolver is not None:
            try:
                return _memory_storage_resolver(user_id, tenant_id)
            except Exception:
                logger.warning(
                    "Failed to resolve user-scoped memory storage for user %s, falling back to file storage",
                    user_id,
                    exc_info=True,
                )
        return get_memory_storage()

    def update_memory(self, messages: list[Any], thread_id: str | None = None, agent_name: str | None = None, user_id: str | None = None, tenant_id: str | None = None) -> bool:
        """Update memory based on conversation messages.

        Args:
            messages: List of conversation messages.
            thread_id: Optional thread ID for tracking source.
            agent_name: If provided, updates per-agent memory. If None, updates global memory.
            user_id: If provided with tenant_id, routes to per-user PG storage.
            tenant_id: Tenant ID for per-user PG storage.

        Returns:
            True if update was successful, False otherwise.
        """
        config = get_memory_config()
        if not config.enabled:
            return False

        if not messages:
            return False

        try:
            # Resolve storage: per-user PG storage when user context is present,
            # otherwise global file-based singleton.
            storage = self._resolve_storage(user_id=user_id, tenant_id=tenant_id)

            # Get current memory
            current_memory = storage.load(agent_name)

            # Format conversation for prompt
            conversation_text = format_conversation_for_update(messages)

            if not conversation_text.strip():
                return False

            # Build prompt
            prompt = MEMORY_UPDATE_PROMPT.format(
                current_memory=json.dumps(current_memory, indent=2),
                conversation=conversation_text,
            )

            # Call LLM
            model = self._get_model()
            response = model.invoke(prompt)
            response_text = _extract_text(response.content).strip()

            # Parse response
            # Remove markdown code blocks if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            update_data = json.loads(response_text)

            # Apply updates
            updated_memory = self._apply_updates(current_memory, update_data, thread_id)

            # Strip file-upload mentions from all summaries before saving.
            # Uploaded files are session-scoped and won't exist in future sessions,
            # so recording upload events in long-term memory causes the agent to
            # try (and fail) to locate those files in subsequent conversations.
            updated_memory = _strip_upload_mentions_from_memory(updated_memory)

            # Save
            return storage.save(updated_memory, agent_name)

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response for memory update: %s", e)
            return False
        except Exception as e:
            logger.exception("Memory update failed: %s", e)
            return False

    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply LLM-generated updates to memory.

        Args:
            current_memory: Current memory data.
            update_data: Updates from LLM.
            thread_id: Optional thread ID for tracking.

        Returns:
            Updated memory data.
        """
        config = get_memory_config()
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        # Update user sections
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Update history sections
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Remove facts
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # Add new facts
        existing_fact_keys = {fact_key for fact_key in (_fact_content_key(fact.get("content")) for fact in current_memory.get("facts", [])) if fact_key is not None}
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:
                raw_content = fact.get("content", "")
                normalized_content = raw_content.strip()
                fact_key = _fact_content_key(normalized_content)
                if fact_key is not None and fact_key in existing_fact_keys:
                    continue

                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized_content,
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                current_memory["facts"].append(fact_entry)
                if fact_key is not None:
                    existing_fact_keys.add(fact_key)

        # Enforce max facts limit
        if len(current_memory["facts"]) > config.max_facts:
            # Sort by confidence and keep top ones
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory


def update_memory_from_conversation(messages: list[Any], thread_id: str | None = None, agent_name: str | None = None) -> bool:
    """Convenience function to update memory from a conversation.

    Args:
        messages: List of conversation messages.
        thread_id: Optional thread ID.
        agent_name: If provided, updates per-agent memory. If None, updates global memory.

    Returns:
        True if successful, False otherwise.
    """
    updater = MemoryUpdater()
    return updater.update_memory(messages, thread_id, agent_name)
