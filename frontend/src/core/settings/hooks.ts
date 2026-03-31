import { useCallback, useSyncExternalStore } from "react";

import {
  DEFAULT_LOCAL_SETTINGS,
  getLocalSettings,
  saveLocalSettings,
  type LocalSettings,
} from "./local";

// ---------------------------------------------------------------------------
// Shared in-memory store backed by localStorage.
// All `useLocalSettings()` consumers share the same snapshot so that a model
// change in one component is immediately visible to every other component.
// ---------------------------------------------------------------------------

let _snapshot: LocalSettings = DEFAULT_LOCAL_SETTINGS;

function _readFromStorage(): LocalSettings {
  if (typeof window === "undefined") return DEFAULT_LOCAL_SETTINGS;
  return getLocalSettings();
}

// Hydrate once on module load (client-side).
if (typeof window !== "undefined") {
  _snapshot = _readFromStorage();
}

const _listeners = new Set<() => void>();

function _emit() {
  for (const l of _listeners) l();
}

function subscribe(listener: () => void) {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

function getSnapshot(): LocalSettings {
  return _snapshot;
}

function getServerSnapshot(): LocalSettings {
  return DEFAULT_LOCAL_SETTINGS;
}

function _update(next: LocalSettings) {
  _snapshot = next;
  saveLocalSettings(next);
  _emit();
}

// Sync when another tab changes localStorage.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === "crab.local-settings") {
      _snapshot = _readFromStorage();
      _emit();
    }
  });
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useLocalSettings(): [
  LocalSettings,
  (
    key: keyof LocalSettings,
    value: Partial<LocalSettings[keyof LocalSettings]>,
  ) => void,
] {
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setter = useCallback(
    (
      key: keyof LocalSettings,
      value: Partial<LocalSettings[keyof LocalSettings]>,
    ) => {
      const prev = _snapshot;
      _update({
        ...prev,
        [key]: {
          ...prev[key],
          ...value,
        },
      });
    },
    [],
  );

  return [state, setter];
}
