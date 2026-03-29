import { getBackendBaseURL } from "../config";
import { fetchJson } from "../http/fetch";

import type { UserMemory } from "./types";

export async function loadMemory() {
  return fetchJson<UserMemory>(`${getBackendBaseURL()}/api/memory`, undefined, {
    fallbackMessage: "Failed to load memory",
  });
}
