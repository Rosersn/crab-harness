import Link from "next/link";

export default function AgentsPage() {
  return (
    <div className="flex size-full items-center justify-center px-6">
      <div className="max-w-md space-y-3 text-center">
        <h1 className="text-2xl font-semibold">Custom agents are unavailable</h1>
        <p className="text-muted-foreground text-sm">
          Filesystem-backed custom agents were removed from cloud mode because they were not tenant-isolated.
        </p>
        <Link className="text-primary text-sm underline underline-offset-4" href="/workspace/chats">
          Back to chats
        </Link>
      </div>
    </div>
  );
}
