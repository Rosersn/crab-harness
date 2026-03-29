import { getBackendBaseURL } from "../config";
import { fetchJson } from "../http/fetch";

import type { Model } from "./types";

export async function loadModels() {
  const { models } = await fetchJson<{ models: Model[] }>(
    `${getBackendBaseURL()}/api/models`,
    undefined,
    {
      fallbackMessage: "Failed to load models",
    },
  );
  return models;
}
