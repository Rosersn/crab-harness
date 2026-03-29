import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { WorkspaceShell } from "@/components/workspace/workspace-shell";
import { env } from "@/env";

export default async function WorkspaceLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true") {
    const cookieStore = await cookies();
    const hasSession =
      cookieStore.has("crab_access_token") ||
      cookieStore.has("crab_refresh_token");

    if (!hasSession) {
      redirect("/login");
    }
  }

  return <WorkspaceShell>{children}</WorkspaceShell>;
}
