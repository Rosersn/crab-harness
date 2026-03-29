import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { loadMCPConfig, updateUserMCPServer } from "./api";
import type { MCPServerListResponse } from "./types";

export function useMCPConfig() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["mcpConfig"],
    queryFn: () => loadMCPConfig(),
  });
  return { config: data, isLoading, error };
}

export function useEnableMCPServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      serverName,
      enabled,
    }: {
      serverName: string;
      enabled: boolean;
    }) => {
      const config = queryClient.getQueryData<MCPServerListResponse>([
        "mcpConfig",
      ]);
      if (!config) {
        throw new Error("MCP config not found");
      }
      const server = config.user_servers.find(
        (candidate) => candidate.server_name === serverName,
      );
      if (!server) {
        throw new Error(`MCP server ${serverName} not found`);
      }
      await updateUserMCPServer(serverName, {
        enabled,
        transport_type: server.transport_type,
        config: server.config,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}
