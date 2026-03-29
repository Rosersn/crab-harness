import { getBackendBaseURL } from "@/core/config";
import { fetchJson, fetchWithAuth, readErrorDetail } from "@/core/http/fetch";

import type { Agent, CreateAgentRequest, UpdateAgentRequest } from "./types";

export async function listAgents(): Promise<Agent[]> {
  const data = await fetchJson<{ agents: Agent[] }>(
    `${getBackendBaseURL()}/api/agents`,
    undefined,
    {
      fallbackMessage: "Failed to load agents",
    },
  );
  return data.agents;
}

export async function getAgent(name: string): Promise<Agent> {
  return fetchJson<Agent>(`${getBackendBaseURL()}/api/agents/${name}`, undefined, {
    fallbackMessage: `Agent '${name}' not found`,
  });
}

export async function createAgent(request: CreateAgentRequest): Promise<Agent> {
  return fetchJson<Agent>(
    `${getBackendBaseURL()}/api/agents`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
    {
      fallbackMessage: "Failed to create agent",
    },
  );
}

export async function updateAgent(
  name: string,
  request: UpdateAgentRequest,
): Promise<Agent> {
  return fetchJson<Agent>(
    `${getBackendBaseURL()}/api/agents/${name}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
    {
      fallbackMessage: `Failed to update agent '${name}'`,
    },
  );
}

export async function deleteAgent(name: string): Promise<void> {
  const res = await fetchWithAuth(`${getBackendBaseURL()}/api/agents/${name}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(
      await readErrorDetail(res, `Failed to delete agent '${name}'`),
    );
  }
}

export async function checkAgentName(
  name: string,
): Promise<{ available: boolean; name: string }> {
  return fetchJson<{ available: boolean; name: string }>(
    `${getBackendBaseURL()}/api/agents/check?name=${encodeURIComponent(name)}`,
    undefined,
    {
      fallbackMessage: "Failed to check agent name",
    },
  );
}
