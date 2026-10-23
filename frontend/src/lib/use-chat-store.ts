"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";

import type { ChatMessage, ChatStore, Conversation } from "./types";

const STORAGE_KEY = "filing-room:chat:v1";
const MAX_CONVERSATIONS = 40;

const EMPTY_STORE: ChatStore = { conversations: [], activeId: null };

let memoryStore: ChatStore | null = null;
const listeners = new Set<() => void>();

export function makeTitle(prompt: string): string {
  const cleaned = prompt.replace(/\s+/g, " ").trim();
  if (cleaned.length <= 54) return cleaned;
  return `${cleaned.slice(0, 51).trimEnd()}...`;
}

function parseStore(raw: string | null): ChatStore | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as ChatStore;
    if (!Array.isArray(parsed.conversations)) return null;
    const conversations = parsed.conversations
      .filter(
        (conversation): conversation is Conversation =>
          Boolean(
            conversation &&
              typeof conversation.id === "string" &&
              typeof conversation.title === "string" &&
              Array.isArray(conversation.messages),
          ),
      )
      .map((conversation) => ({
        ...conversation,
        messages: conversation.messages.map((message) =>
          message.status === "pending"
            ? {
                ...message,
                status: "error" as const,
                error: "This request was interrupted before it finished.",
              }
            : message,
        ),
      }));

    const activeId =
      parsed.activeId && conversations.some((c) => c.id === parsed.activeId)
        ? parsed.activeId
        : (conversations[0]?.id ?? null);

    return { conversations, activeId };
  } catch {
    return null;
  }
}

function loadStore(): ChatStore {
  if (typeof window === "undefined") return EMPTY_STORE;
  if (memoryStore) return memoryStore;
  try {
    memoryStore =
      parseStore(window.localStorage.getItem(STORAGE_KEY)) ?? EMPTY_STORE;
  } catch {
    memoryStore = EMPTY_STORE;
  }
  return memoryStore;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    memoryStore =
      event.newValue === null
        ? EMPTY_STORE
        : (parseStore(event.newValue) ?? EMPTY_STORE);
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function commit(next: ChatStore) {
  memoryStore = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage may be full or disabled; the session still works in memory.
  }
  for (const listener of listeners) listener();
}

function update(updater: (previous: ChatStore) => ChatStore) {
  commit(updater(loadStore()));
}

function getServerSnapshot(): ChatStore {
  return EMPTY_STORE;
}

export function useChatStore() {
  const store = useSyncExternalStore(
    subscribe,
    loadStore,
    getServerSnapshot,
  );

  const active = useMemo(
    () =>
      store.conversations.find((c) => c.id === store.activeId) ?? null,
    [store.activeId, store.conversations],
  );

  const createConversation = useCallback((title: string): string => {
    const id = crypto.randomUUID();
    const now = Date.now();
    const conversation: Conversation = {
      id,
      title,
      createdAt: now,
      updatedAt: now,
      messages: [],
    };
    update((previous) => ({
      conversations: [conversation, ...previous.conversations].slice(
        0,
        MAX_CONVERSATIONS,
      ),
      activeId: id,
    }));
    return id;
  }, []);

  const appendMessage = useCallback(
    (conversationId: string, message: ChatMessage) => {
      update((previous) => ({
        ...previous,
        conversations: previous.conversations.map((conversation) => {
          if (conversation.id !== conversationId) return conversation;
          const messages = [...conversation.messages, message];
          const firstUser = messages.find((m) => m.role === "user");
          return {
            ...conversation,
            messages,
            updatedAt: message.createdAt,
            title: firstUser ? makeTitle(firstUser.content) : conversation.title,
          };
        }),
      }));
    },
    [],
  );

  const patchMessage = useCallback(
    (
      conversationId: string,
      messageId: string,
      patch: Partial<ChatMessage>,
    ) => {
      update((previous) => ({
        ...previous,
        conversations: previous.conversations.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                updatedAt: Date.now(),
                messages: conversation.messages.map((message) =>
                  message.id === messageId
                    ? { ...message, ...patch }
                    : message,
                ),
              }
            : conversation,
        ),
      }));
    },
    [],
  );

  const deleteConversation = useCallback((conversationId: string) => {
    update((previous) => {
      const conversations = previous.conversations.filter(
        (conversation) => conversation.id !== conversationId,
      );
      return {
        conversations,
        activeId:
          previous.activeId === conversationId
            ? (conversations[0]?.id ?? null)
            : previous.activeId,
      };
    });
  }, []);

  const setActiveId = useCallback((conversationId: string | null) => {
    update((previous) => ({ ...previous, activeId: conversationId }));
  }, []);

  const clearAll = useCallback(() => {
    update(() => EMPTY_STORE);
  }, []);

  return {
    conversations: store.conversations,
    activeId: store.activeId,
    active,
    createConversation,
    appendMessage,
    patchMessage,
    deleteConversation,
    setActiveId,
    clearAll,
  };
}
