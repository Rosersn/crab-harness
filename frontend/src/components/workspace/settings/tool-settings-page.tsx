"use client";

import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import { useMCPConfig, useEnableMCPServer } from "@/core/mcp/hooks";
import type {
  MCPServerConfig,
  MCPServerListResponse,
  UserMCPServer,
} from "@/core/mcp/types";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

export function ToolSettingsPage() {
  const { t } = useI18n();
  const { config, isLoading, error } = useMCPConfig();
  return (
    <SettingsSection
      title={t.settings.tools.title}
      description={t.settings.tools.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div>Error: {error.message}</div>
      ) : (
        config && <MCPServerList config={config} />
      )}
    </SettingsSection>
  );
}

function MCPServerList({ config }: { config: MCPServerListResponse }) {
  const { mutate: enableMCPServer } = useEnableMCPServer();
  return (
    <div className="flex w-full flex-col gap-4">
      <section className="space-y-3">
        <div className="space-y-1">
          <h3 className="text-sm font-medium">Platform servers</h3>
          <p className="text-muted-foreground text-sm">
            Shared MCP tools managed by the deployment.
          </p>
        </div>
        {Object.entries(config.platform_servers).map(([name, server]) => (
          <PlatformServerItem key={name} name={name} server={server} />
        ))}
      </section>
      <section className="space-y-3">
        <div className="space-y-1">
          <h3 className="text-sm font-medium">User servers</h3>
          <p className="text-muted-foreground text-sm">
            Tenant-scoped MCP connections stored in PostgreSQL.
          </p>
        </div>
        {config.user_servers.length === 0 ? (
          <div className="text-muted-foreground rounded-xl border border-dashed p-4 text-sm">
            No user-managed MCP servers have been configured yet.
          </div>
        ) : (
          config.user_servers.map((server) => (
            <UserServerItem
              key={server.server_name}
              server={server}
              onToggle={(enabled) =>
                enableMCPServer({
                  serverName: server.server_name,
                  enabled,
                })
              }
            />
          ))
        )}
      </section>
    </div>
  );
}

function PlatformServerItem({
  name,
  server,
}: {
  name: string;
  server: MCPServerConfig;
}) {
  return (
    <Item className="w-full" variant="outline">
      <ItemContent>
        <ItemTitle>{name}</ItemTitle>
        <ItemDescription className="line-clamp-4">
          {server.description || "Platform-managed MCP server"}
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Switch checked={server.enabled} disabled />
      </ItemActions>
    </Item>
  );
}

function UserServerItem({
  server,
  onToggle,
}: {
  server: UserMCPServer;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <Item className="w-full" variant="outline">
      <ItemContent>
        <ItemTitle>{server.server_name}</ItemTitle>
        <ItemDescription className="line-clamp-4">
          {server.transport_type.toUpperCase()}
          {typeof server.config.url === "string" ? ` • ${server.config.url}` : ""}
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Switch
          checked={server.enabled}
          disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
          onCheckedChange={onToggle}
        />
      </ItemActions>
    </Item>
  );
}
