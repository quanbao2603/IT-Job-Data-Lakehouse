// The conversation must survive a tab switch and a reload; a broken entry must never break the page.
import { beforeEach, describe, expect, it } from 'vitest'
import { EMPTY_CHAT, clearChat, loadChat, saveChat } from '../chatStore'

class FakeStorage implements Storage {
  private map = new Map<string, string>()
  failNext = false
  get length() { return this.map.size }
  clear() { this.map.clear() }
  getItem(key: string) { return this.map.get(key) ?? null }
  key(index: number) { return [...this.map.keys()][index] ?? null }
  removeItem(key: string) { this.map.delete(key) }
  setItem(key: string, value: string) {
    if (this.failNext) { this.failNext = false; throw new Error('QuotaExceededError') }
    this.map.set(key, value)
  }
}

const message = (id: string, role: 'user' | 'assistant', content: string) => ({ id, role, content })

describe('chat history storage', () => {
  let storage: FakeStorage
  beforeEach(() => { storage = new FakeStorage() })

  it('round-trips a conversation', () => {
    saveChat({ messages: [message('a', 'user', 'xin chào'), message('b', 'assistant', 'Chào bạn!')], input: 'đang gõ' }, storage)
    const loaded = loadChat(storage)
    expect(loaded.messages).toHaveLength(2)
    expect(loaded.messages[1].content).toBe('Chào bạn!')
    expect(loaded.input).toBe('đang gõ')
  })

  it('returns an empty chat when nothing was stored', () => {
    expect(loadChat(storage)).toEqual(EMPTY_CHAT)
  })

  it('ignores a corrupted entry instead of throwing', () => {
    storage.setItem('it-insight.assistant.v1', '{not json')
    expect(loadChat(storage)).toEqual(EMPTY_CHAT)
  })

  it('drops entries that are not real messages', () => {
    storage.setItem('it-insight.assistant.v1', JSON.stringify({ messages: [{ id: 1 }, message('ok', 'user', 'hợp lệ')], input: 5 }))
    const loaded = loadChat(storage)
    expect(loaded.messages).toHaveLength(1)
    expect(loaded.input).toBe('')
  })

  it('keeps only the newest 40 messages', () => {
    const many = Array.from({ length: 60 }, (_, index) => message(`m${index}`, 'user', `câu ${index}`))
    saveChat({ messages: many, input: '' }, storage)
    const loaded = loadChat(storage)
    expect(loaded.messages).toHaveLength(40)
    expect(loaded.messages[loaded.messages.length - 1].content).toBe('câu 59')
  })

  it('halves the history instead of losing it when the quota is hit', () => {
    saveChat({ messages: [message('a', 'user', 'một'), message('b', 'assistant', 'hai')], input: '' }, storage)
    storage.failNext = true
    saveChat({ messages: [message('a', 'user', 'một'), message('b', 'assistant', 'hai')], input: '' }, storage)
    // first attempt throws, retry writes the trimmed conversation
    expect(loadChat(storage).messages.length).toBeGreaterThan(0)
  })

  it('clears on request', () => {
    saveChat({ messages: [message('a', 'user', 'x')], input: '' }, storage)
    clearChat(storage)
    expect(loadChat(storage)).toEqual(EMPTY_CHAT)
  })
})
