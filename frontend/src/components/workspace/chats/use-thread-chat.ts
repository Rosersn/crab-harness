"use client";

import { useParams, usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { uuid } from "@/core/utils/uuid";

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const draftThreadIdRef = useRef(
    threadIdFromPath === "new" ? uuid() : threadIdFromPath,
  );
  const [threadId, setThreadIdState] = useState(() => draftThreadIdRef.current);

  const [isNewThread, setIsNewThread] = useState(
    () => threadIdFromPath === "new",
  );

  const setThreadId = useCallback((nextThreadId: string) => {
    draftThreadIdRef.current = nextThreadId;
    setThreadIdState(nextThreadId);
  }, []);

  useEffect(() => {
    if (pathname.endsWith("/new")) {
      setIsNewThread(true);
      const draftThreadId = uuid();
      draftThreadIdRef.current = draftThreadId;
      setThreadIdState(draftThreadId);
      return;
    }

    if (threadIdFromPath) {
      setIsNewThread(false);
      draftThreadIdRef.current = threadIdFromPath;
      setThreadIdState(threadIdFromPath);
    }
  }, [pathname, threadIdFromPath]);
  const isMock = searchParams.get("mock") === "true";
  return { threadId, setThreadId, isNewThread, setIsNewThread, isMock };
}
