// The assistant conversation has to survive a tab switch and a page reload: the panel unmounts on
// every navigation, and a reader who asked three questions expects them back. This module owns the
// storage, so the hook stays about behaviour and the tests can target the edge cases directly.
import type { AssistantAnswer } from './api'

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  answer?: AssistantAnswer
  question?: string
  failed?: boolean
}

export type ChatSnapshot = { messages: ChatMessage[]; input: string }

const STORAGE_KEY = 'it-insight.assistant.v1'
/** Older exchanges are dropped first: a long session must not wedge the tab in a quota loop. */
const MAX_MESSAGES = 40
const MAX_INPUT = 500

export const EMPTY_CHAT: ChatSnapshot = { messages: [], input: '' }

function isMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return (
    typeof item.id === 'string'
    && (item.role === 'user' || item.role === 'assistant')
    && typeof item.content === 'string'
  )
}

export function loadChat(storage: Storage | undefined = globalThis.localStorage): ChatSnapshot {
  if (!storage) return EMPTY_CHAT
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY_CHAT
    const parsed = JSON.parse(raw) as Partial<ChatSnapshot>
    const messages = Array.isArray(parsed.messages) ? parsed.messages.filter(isMessage) : []
    const input = typeof parsed.input === 'string' ? parsed.input.slice(0, MAX_INPUT) : ''
    return { messages: messages.slice(-MAX_MESSAGES), input }
  } catch {
    // A half-written or hand-edited entry must never break the page.
    return EMPTY_CHAT
  }
}

export function saveChat(
  snapshot: ChatSnapshot,
  storage: Storage | undefined = globalThis.localStorage,
): void {
  if (!storage) return
  let messages = snapshot.messages.slice(-MAX_MESSAGES)
  const input = (snapshot.input ?? '').slice(0, MAX_INPUT)
  // Answers carry result tables, so a long session can approach the quota. Halve the history
  // instead of throwing away the whole conversation.
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify({ messages, input }))
      return
    } catch {
      if (messages.length === 0) return
      messages = messages.slice(Math.ceil(messages.length / 2))
    }
  }
}

export function clearChat(storage: Storage | undefined = globalThis.localStorage): void {
  try {
    storage?.removeItem(STORAGE_KEY)
  } catch {
    // ignore: nothing to recover from a failed remove
  }
}
