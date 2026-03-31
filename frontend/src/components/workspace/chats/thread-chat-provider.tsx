"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings } from "@/core/settings";
import {
  type ThreadStreamOptions,
  useThreadStream,
} from "@/core/threads/hooks";
import { textOfMessage } from "@/core/threads/utils";
import { uuid } from "@/core/utils/uuid";

type ThreadChatRuntimeValue = {
  threadId: string;
  isNewThread: boolean;
  isMock: boolean;
  thread: ReturnType<typeof useThreadStream>[0];
  sendMessage: (
    message: PromptInputMessage,
    extraContext?: Record<string, unknown>,
  ) => Promise<void>;
  isUploading: boolean;
};

const ThreadChatRuntimeContext = createContext<ThreadChatRuntimeValue | null>(
  null,
);

export function ThreadChatProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { thread_id: threadIdFromPath } = useParams<{ thread_id?: string }>();
  const [settings] = useLocalSettings();
  const { showNotification } = useNotification();

  const initialThreadId =
    threadIdFromPath && threadIdFromPath !== "new"
      ? threadIdFromPath
      : uuid();

  const pendingRouteThreadIdRef = useRef<string | null>(null);
  const [threadId, setThreadId] = useState(initialThreadId);
  const [isNewThread, setIsNewThread] = useState(threadIdFromPath === "new");
  const isMock = searchParams.get("mock") === "true";

  useLayoutEffect(() => {
    if (!threadIdFromPath) {
      return;
    }

    if (threadIdFromPath === "new") {
      pendingRouteThreadIdRef.current = null;
      setThreadId(uuid());
      setIsNewThread(true);
      return;
    }

    if (pendingRouteThreadIdRef.current === threadIdFromPath) {
      setThreadId(threadIdFromPath);
      return;
    }

    pendingRouteThreadIdRef.current = null;
    setThreadId(threadIdFromPath);
    setIsNewThread(false);
  }, [threadIdFromPath]);

  const streamOptions: ThreadStreamOptions = {
    context: settings.context,
    isMock,
    onStart: (actualThreadId) => {
      pendingRouteThreadIdRef.current = actualThreadId;
      setThreadId(actualThreadId);
      if (threadIdFromPath !== actualThreadId) {
        startTransition(() => {
          router.replace(`/workspace/chats/${actualThreadId}`);
        });
      }
    },
    onFinish: (state) => {
      if (pendingRouteThreadIdRef.current) {
        setIsNewThread(false);
        pendingRouteThreadIdRef.current = null;
      }

      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
    onError: () => {
      if (pendingRouteThreadIdRef.current) {
        setIsNewThread(false);
        pendingRouteThreadIdRef.current = null;
      }
    },
  };

  if (!isNewThread) {
    streamOptions.threadId = threadId;
  }

  const [thread, sendMessageForThread, isUploading] =
    useThreadStream(streamOptions);

  const sendMessage = useCallback(
    (
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
    ) => {
      // Navigate immediately for new threads so the user sees the chat page
      // without waiting for the (potentially slow) file upload to finish.
      if (isNewThread && threadIdFromPath === "new") {
        pendingRouteThreadIdRef.current = threadId;
        startTransition(() => {
          router.replace(`/workspace/chats/${threadId}`);
        });
      }
      return sendMessageForThread(threadId, message, extraContext);
    },
    [sendMessageForThread, threadId, isNewThread, threadIdFromPath, router],
  );

  const value = useMemo<ThreadChatRuntimeValue>(
    () => ({
      threadId,
      isNewThread,
      isMock,
      thread,
      sendMessage,
      isUploading,
    }),
    [threadId, isNewThread, isMock, thread, sendMessage, isUploading],
  );

  return (
    <ThreadChatRuntimeContext.Provider value={value}>
      {children}
    </ThreadChatRuntimeContext.Provider>
  );
}

export function useThreadChatRuntime() {
  const context = useContext(ThreadChatRuntimeContext);
  if (!context) {
    throw new Error("useThreadChatRuntime must be used within ThreadChatProvider");
  }
  return context;
}
