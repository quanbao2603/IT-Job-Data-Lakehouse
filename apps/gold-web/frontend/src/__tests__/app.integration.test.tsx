/**
 * DOM integration test against a running IT Insight instance.
 *
 * It is skipped unless IT_INSIGHT_BASE_URL is set, because the suite must not
 * silently pass with a mocked API: the point is to exercise the real FastAPI +
 * Gold reader path. Example:
 *
 *   IT_INSIGHT_BASE_URL=http://womtech.io.vn:8010/ npm test
 */
import { describe, expect, it, beforeAll, beforeEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

const BASE = process.env.IT_INSIGHT_BASE_URL
const live = Boolean(BASE)

async function meta() {
  const response = await fetch(new URL('api/dashboard', BASE).toString())
  if (!response.ok) throw new Error(`dashboard API returned ${response.status}`)
  return (await response.json()).meta as { age_days: number; snapshot_date: string }
}

describe.skipIf(!live)('IT Insight web (live API)', () => {
  beforeAll(() => {
    // jsdom's URL comes from IT_INSIGHT_BASE_URL (see vitest.config.ts); only
    // normalise it when the two disagree so history.replaceState stays same-origin.
    if (BASE && new URL(BASE).origin !== window.location.origin) {
      window.history.replaceState(null, '', new URL(BASE).pathname)
    }
  })

  // App writes ?page= into the URL, so each test must start from the overview page.
  beforeEach(() => {
    const url = new URL(window.location.href)
    url.searchParams.delete('page')
    window.history.replaceState(null, '', url)
  })

  it('renders the update date and summary metrics without developer wording', async () => {
    const live_meta = await meta()
    render(<App />)
    const cards = await screen.findAllByText(/Tin hợp nhất được quan sát/i, {}, { timeout: 20000 })
    expect(cards.length).toBeGreaterThan(0)
    const metric = document.querySelector('.metric-card strong')
    expect(metric?.textContent).toMatch(/[0-9]/)

    // The user-facing label is "cập nhật ngày", never "Snapshot"/"Release ... cutoff".
    const chip = document.querySelector('.snapshot-chip')
    expect(chip?.textContent?.toLowerCase()).toContain('cập nhật ngày')
    const body = document.body.textContent ?? ''
    expect(body).not.toContain('Snapshot')
    expect(body).not.toContain('cutoff')
    expect(body).not.toContain('BRONZE')
    const [year, month, day] = live_meta.snapshot_date.slice(0, 10).split('-')
    expect(chip?.textContent).toContain(`${day}/${month}/${year}`)
  })

  it('filters jobs and opens/closes the detail dialog with the keyboard', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Tin tuyển dụng' }))
    const jobCards = await waitFor(() => {
      const found = document.querySelectorAll('.job-card')
      expect(found.length).toBeGreaterThan(0)
      return found
    }, { timeout: 20000 })

    const first = jobCards[0] as HTMLElement
    await user.click(first)
    const dialog = await screen.findByRole('dialog', {}, { timeout: 20000 })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const search = screen.getByLabelText('Tìm tên tin hoặc công ty')
    await user.type(search, 'python')
    await waitFor(() => {
      const total = document.querySelector('.toolbar span')
      expect(total?.textContent).toMatch(/tin trong cohort/)
    })
    cleanup()
  })

  it('links every job card to its original listing', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Tin tuyển dụng' }))
    await waitFor(() => {
      expect(document.querySelectorAll('.job-card').length).toBeGreaterThan(0)
    }, { timeout: 20000 })

    const cards = Array.from(document.querySelectorAll('.job-card')) as HTMLElement[]
    const withLink = cards.filter((card) => card.querySelector('a.source-link'))
    // Gold now carries a source URL for every observed job; a card without one must
    // say so rather than render an empty anchor.
    for (const card of cards) {
      const anchor = card.querySelector('a.source-link') as HTMLAnchorElement | null
      if (anchor) {
        expect(anchor.href).toMatch(/^https?:\/\//)
        expect(anchor.target).toBe('_blank')
        expect(anchor.rel).toContain('noopener')
      } else {
        expect(card.textContent).toContain('Chưa có liên kết nguồn')
      }
    }
    expect(withLink.length).toBeGreaterThan(0)
    cleanup()
  })

  it('drills from a skill into the job list', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Kỹ năng' }))
    const rows = await waitFor(() => {
      const found = document.querySelectorAll('.rank-row.clickable')
      expect(found.length).toBeGreaterThan(0)
      return found
    }, { timeout: 20000 })
    await user.click(rows[0] as HTMLElement)
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('Tin tuyển dụng')
      const chips = document.querySelector('.active-chips')
      expect(chips?.textContent ?? '').not.toBe('')
    }, { timeout: 20000 })
    cleanup()
  })

  it('warns only when the served release really is past the freshness window', async () => {
    const live_meta = await meta()
    render(<App />)
    await screen.findAllByText(/Tin hợp nhất được quan sát/i, {}, { timeout: 20000 })
    const banner = document.querySelector('.banner.warn')
    if (live_meta.age_days > 0) {
      expect(banner?.textContent).toMatch(/cập nhật cách đây/)
    } else {
      expect(banner).toBeNull()
      expect(document.querySelector('.sidebar-status')?.textContent).toContain('cập nhật mới')
    }
    cleanup()
  })
})
