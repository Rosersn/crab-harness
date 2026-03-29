"use client";

import Link from "next/link";

export default function AgentChatPage() {
  return (
    <div className="flex size-full items-center justify-center px-6">
      <div className="max-w-md space-y-3 text-center">
        <h1 className="text-2xl font-semibold">Custom agents are unavailable</h1>
        <p className="text-muted-foreground text-sm">
          This cloud deployment only supports the default tenant-scoped assistant.
        </p>
        <Link className="text-primary text-sm underline underline-offset-4" href="/workspace/chats">
          Back to chats
        </Link>
      </div>
    </div>
  );
}
