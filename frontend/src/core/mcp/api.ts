import { getBackendBaseURL } from "@/core/config";
import { fetchJson } from "@/core/http/fetch";

import type { MCPServerListResponse, UserMCPServer } from "./types";

export async function loadMCPConfig() {
  return fetchJson<MCPServerListResponse>(
    `${getBackendBaseURL()}/api/mcp/servers`,
    undefined,
    {
      fallbackMessage: "Failed to load MCP servers",
    },
  );
}

export async function updateUserMCPServer(
  serverName: string,
  server: Pick<UserMCPServer, "enabled" | "transport_type" | "config">,
) {
  return fetchJson<UserMCPServer>(
    `${getBackendBaseURL()}/api/mcp/servers/${serverName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled: server.enabled,
        transport_type: server.transport_type,
        config: server.config,
      }),
    },
    {
      fallbackMessage: `Failed to update MCP server '${serverName}'`,
    },
  );
}
