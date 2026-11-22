/**
 * DOM integration test against a running IT Insight instance.
 *
 * It is skipped unless IT_INSIGHT_BASE_URL is set, because the suite must not
 * silently pass with a mocked API: the point is to exercise the real FastAPI +
 * Gold reader path. Example:
 *
 *   IT_INSIGHT_BASE_URL=http://127.0.0.1:8011/insights/ npm test
 */
import { describe, expect, it, beforeAll } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

const BASE = process.env.IT_INSIGHT_BASE_URL
const live = Boolean(BASE)

describe.skipIf(!live)('IT Insight web (live API)', () => {
  beforeAll(() => {
    // jsdom's URL comes from IT_INSIGHT_BASE_URL (see vitest.config.ts); only
    // normalise it when the two disagree so history.replaceState stays same-origin.
    if (BASE && new URL(BASE).origin !== window.location.origin) {
      window.history.replaceState(null, '', new URL(BASE).pathname)
    }
  })

  it('renders live release metadata and summary metrics', async () => {
    render(<App />)
    const cards = await screen.findAllByText(/Tin hợp nhất được quan sát/i, {}, { timeout: 20000 })
    expect(cards.length).toBeGreaterThan(0)
    const metric = document.querySelector('.metric-card strong')
    expect(metric?.textContent).toMatch(/[0-9]/)
    expect(document.querySelector('.release-note')?.textContent).toContain('Release')
    expect(document.body.textContent).toContain('Snapshot')
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

  it('marks the dataset as stale instead of claiming fresh data', async () => {
    render(<App />)
    const banner = await waitFor(() => {
      const found = document.querySelector('.banner.warn')
      expect(found).toBeTruthy()
      return found as HTMLElement
    }, { timeout: 20000 })
    expect(banner.textContent).toMatch(/Snapshot cách đây/)
    const meta = document.querySelector('.release-note')
    expect(meta?.textContent).toContain('cutoff')
    cleanup()
  })
})
