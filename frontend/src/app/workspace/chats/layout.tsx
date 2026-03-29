"use client";

import { useParams } from "next/navigation";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { ThreadChatProvider } from "@/components/workspace/chats/thread-chat-provider";
import { SubtasksProvider } from "@/core/tasks/context";

export default function ChatsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id?: string }>();

  const content = threadIdFromPath ? (
    <ThreadChatProvider>{children}</ThreadChatProvider>
  ) : (
    children
  );

  return (
    <SubtasksProvider>
      <ArtifactsProvider>
        <PromptInputProvider>{content}</PromptInputProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
