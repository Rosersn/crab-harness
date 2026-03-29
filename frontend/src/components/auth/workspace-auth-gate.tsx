"use client";

import { LoaderIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { loadCurrentUser } from "@/core/auth/api";
import { env } from "@/env";

export function WorkspaceAuthGate({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const router = useRouter();
  const [isReady, setIsReady] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );

  useEffect(() => {
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return;
    }

    let cancelled = false;

    void loadCurrentUser()
      .then(() => {
        if (!cancelled) {
          setIsReady(true);
        }
      })
      .catch(() => {
        if (cancelled) {
          return;
        }
        const next =
          typeof window !== "undefined"
            ? `${window.location.pathname}${window.location.search}`
            : "/workspace";
        router.replace(`/login?next=${encodeURIComponent(next)}`);
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!isReady) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-white">
        <div className="flex items-center gap-3 rounded-full border border-white/15 bg-white/5 px-5 py-3 text-sm">
          <LoaderIcon className="size-4 animate-spin" />
          Checking your workspace session...
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
