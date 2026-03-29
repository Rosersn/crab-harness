"""RequestContext - immutable per-request context flowing through the entire chain."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    """Immutable context for a single API request, built from auth + request params."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    thread_id: uuid.UUID | None = None
    model_name: str | None = None
    thinking_enabled: bool = True
    reasoning_effort: str | None = None
    is_plan_mode: bool = False
    subagent_enabled: bool = False
    max_concurrent_subagents: int = 3
    agent_name: str | None = None

    def to_runnable_configurable(self) -> dict:
        """Convert to LangGraph RunnableConfig['configurable'] dict."""
        cfg: dict = {
            "thread_id": str(self.thread_id) if self.thread_id else None,
            "user_id": str(self.user_id),
            "tenant_id": str(self.tenant_id),
            "thinking_enabled": self.thinking_enabled,
            "is_plan_mode": self.is_plan_mode,
            "subagent_enabled": self.subagent_enabled,
            "max_concurrent_subagents": self.max_concurrent_subagents,
        }
        if self.model_name:
            cfg["model_name"] = self.model_name
        if self.reasoning_effort:
            cfg["reasoning_effort"] = self.reasoning_effort
        if self.agent_name:
            cfg["agent_name"] = self.agent_name
        return cfg
