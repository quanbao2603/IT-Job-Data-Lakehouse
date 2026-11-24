/**
 * Live UI checks for the five additions: freshness note, trends chart, company structure panel
 * and the job-ad sections. Skipped unless IT_INSIGHT_BASE_URL points at a running release.
 */
import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

const BASE = process.env.IT_INSIGHT_BASE_URL
const live = Boolean(BASE)

function goTo(page: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('page', page)
  window.history.replaceState(null, '', url)
}

describe.skipIf(!live)('ui additions', () => {
  it('renders the weekly trend chart with real bars', async () => {
    goTo('trends')
    render(<App />)
    await screen.findByText('Số tin theo tuần đăng', {}, { timeout: 25000 })
    const bars = document.querySelectorAll('.trend-bar')
    expect(bars.length).toBeGreaterThan(4)
    const heights = Array.from(bars).map((bar) => (bar as HTMLElement).style.height)
    // Every bar must carry a real height, not the 4px minimum for all of them.
    expect(new Set(heights).size).toBeGreaterThan(1)
    expect(screen.getByText(/Lịch sử cập nhật dữ liệu/)).toBeTruthy()
  }, 60000)

  it('shows how many jobs have no platform evidence', async () => {
    goTo('overview')
    render(<App />)
    await screen.findByText('nền tảng', {}, { timeout: 25000 })
    await waitFor(() => {
      const note = Array.from(document.querySelectorAll('.panel-note'))
        .map((node) => node.textContent ?? '')
        .find((text) => text.includes('chưa có bằng chứng nền tảng'))
      expect(note).toBeTruthy()
    }, { timeout: 25000 })
  }, 60000)

  it('opens the company structure panel', async () => {
    goTo('companies')
    const user = userEvent.setup()
    render(<App />)
    const structure = await screen.findAllByText('Cấu trúc', {}, { timeout: 25000 })
    await user.click(structure[0] as HTMLElement)
    await waitFor(() => {
      expect(document.querySelectorAll('.profile-grid .bar-row').length).toBeGreaterThan(2)
    }, { timeout: 25000 })
    expect(document.querySelector('.company-profile')).toBeTruthy()
  }, 60000)

  it('renders the job ad sections in the detail modal', async () => {
    goTo('jobs')
    const user = userEvent.setup()
    render(<App />)
    const buttons = await screen.findAllByText('Xem chi tiết', {}, { timeout: 25000 })
    await user.click(buttons[0] as HTMLElement)
    await waitFor(() => {
      expect(document.querySelectorAll('.ad-section, .detail-grid').length).toBeGreaterThan(0)
    }, { timeout: 25000 })
    const sections = Array.from(document.querySelectorAll('.ad-section summary'))
      .map((node) => node.textContent ?? '')
    // The source may omit the ad text; the modal then says so instead of rendering an empty box.
    const empty = document.body.textContent?.includes('không cung cấp nội dung mô tả')
    expect(sections.length > 0 || empty).toBe(true)
  }, 60000)
})
