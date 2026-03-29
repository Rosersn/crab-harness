export interface MCPServerConfig extends Record<string, unknown> {
  enabled: boolean;
  description: string;
  type?: string;
  url?: string | null;
  oauth?: Record<string, unknown> | null;
}

export interface UserMCPServer {
  server_name: string;
  enabled: boolean;
  transport_type: string;
  config: Record<string, unknown>;
}

export interface MCPServerListResponse {
  platform_servers: Record<string, MCPServerConfig>;
  user_servers: UserMCPServer[];
}
