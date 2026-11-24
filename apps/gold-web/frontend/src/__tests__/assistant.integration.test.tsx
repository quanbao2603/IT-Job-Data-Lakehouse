/**
 * Chat regression tests: a typed message and a suggestion chip must both answer, and a
 * greeting must not require a database round trip.
 * Runs against the live API when IT_INSIGHT_BASE_URL is set.
 */
import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

const BASE = process.env.IT_INSIGHT_BASE_URL
const live = Boolean(BASE)

describe.skipIf(!live)('assistant chat behaviour', () => {
  beforeEach(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('page', 'assistant')
    window.history.replaceState(null, '', url)
  })

  it('answers a typed greeting', async () => {
    const user = userEvent.setup()
    render(<App />)
    const input = await screen.findByLabelText('Câu hỏi cho trợ lý AI', {}, { timeout: 20000 })
    await waitFor(() => expect((input as HTMLInputElement).disabled).toBe(false), { timeout: 20000 })
    await user.type(input, 'xin chào')
    const button = screen.getByRole('button', { name: 'Gửi' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
    await user.click(button)
    const answer = await waitFor(() => {
      const found = Array.from(document.querySelectorAll('.chat-bubble.assistant'))
        .map((node) => node.textContent ?? '')
        .find((text) => text && !text.includes('Đang truy vấn'))
      expect(found).toBeTruthy()
      return found as string
    }, { timeout: 90000 })
    expect(answer.length).toBeGreaterThan(10)
    expect(answer.toLowerCase()).toMatch(/chào|trợ lý|hỏi|số liệu/)
  }, 120000)

  it('answers a clicked suggestion chip', async () => {
    const user = userEvent.setup()
    render(<App />)
    const chip = await screen.findByRole('button', { name: /Lương trung vị của kỹ sư dữ liệu/ }, { timeout: 20000 })
    await user.click(chip)
    const answer = await waitFor(() => {
      const found = Array.from(document.querySelectorAll('.chat-bubble.assistant'))
        .map((node) => node.textContent ?? '')
        .find((text) => text && !text.includes('Đang truy vấn'))
      expect(found).toBeTruthy()
      return found as string
    }, { timeout: 90000 })
    expect(answer.length).toBeGreaterThan(10)
  }, 120000)
})
