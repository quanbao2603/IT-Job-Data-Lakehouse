/**
 * Assistant rendering tests with a stubbed API (no LLM, no database).
 *
 * These lock the properties that broke in review: bold text renders as bold, tables are
 * real tables, job answers expose working actions, and no SQL/provenance footer reaches
 * the DOM — including answers replayed from older history.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const fixture = vi.hoisted(() => {
  const ANSWER = {
    request_id: 'req_test',
    answer_type: 'salary',
    answer_markdown: [
      '**Theo chức danh Data Engineer**',
      '- Lương trung vị: **31,25 triệu VND/tháng**',
      '- Số tin có dữ liệu lương: **14 tin**',
      '',
      '| Nhóm | Số tin |',
      '| --- | --- |',
      '| Junior | 158 |',
      '| Senior | 49 |',
    ].join('\n'),
    data_context: { snapshot_date: '2026-11-24', timezone: 'Asia/Ho_Chi_Minh' },
    applied_filters: { city: 'Hà Nội', job_levels: ['Junior', 'Senior'], skill: 'Python' },
    result: {
      kind: 'jobs' as const,
      row_count: 2,
      truncated: false,
      jobs: [{
        job_id: 'job_1',
        title: 'Backend Engineer',
        company: 'Aster Labs',
        city: 'Hà Nội',
        level: 'Senior',
        salary_midpoint_vnd: 32000000,
        salary_min_vnd: null,
        salary_max_vnd: null,
        salary_sample_flag: true,
        last_seen_at: '2026-11-24T10:00:00+07:00',
        source_url: 'https://example.com/jobs/1',
      }],
    },
    warnings: [],
  }
  const meta = {
    gold_release_id: 'gold_x', silver_release_id: 'silver_x', snapshot_date: '2026-11-24',
    snapshot_cutoff_at: '2026-11-24T20:00:00+07:00', switched_at: null,
    served_at: '2026-11-24T20:00:00+07:00', mode: 'live', age_days: 0,
    stale_after_days: 1, stale: false, scope: 'x', salary_definition: 'x',
  }
  type AskFn = (question: string) => Promise<typeof ANSWER>
  return { ANSWER, meta, handler: { ask: (async () => ANSWER) as AskFn } }
})

vi.mock('../api', () => {
  class ApiError extends Error {
    status: number
    code: string
    constructor(message: string, status = 500, code = 'UNKNOWN') {
      super(message)
      this.status = status
      this.code = code
    }
  }
  return {
    ApiError,
    api: {
      health: vi.fn(async () => ({ status: 'ok', mode: 'live', ai_enabled: true })),
      ready: vi.fn(async () => ({ status: 'ready', meta: fixture.meta })),
      filters: vi.fn(async () => ({
        meta: fixture.meta,
        cities: [{ value: 'Hà Nội', label: 'Hà Nội', count: 10 }],
        domains: [],
        levels: [],
        skills: [{ value: 'python', label: 'Python', count: 10 }],
      })),
      assistantStatus: vi.fn(async () => ({
        enabled: true,
        status: 'ready',
        message: 'Trợ lý đang hoạt động.',
        examples: ['Lương trung vị của kỹ sư dữ liệu là bao nhiêu?'],
      })),
      assistantAsk: vi.fn((question: string, _history: unknown, signal?: AbortSignal) => new Promise((resolve, reject) => {
        // Mirror fetch(): an aborted request rejects, which is what frees the composer.
        signal?.addEventListener('abort', () => {
          const error = new Error('aborted')
          error.name = 'AbortError'
          reject(error)
        })
        fixture.handler.ask(question).then(resolve, reject)
      })),
      dashboard: vi.fn(async () => { throw new Error('not used') }),
      jobs: vi.fn(async () => ({ meta: fixture.meta, items: [], total: 0, page: 1, page_size: 12 })),
      skills: vi.fn(async () => { throw new Error('not used') }),
      companies: vi.fn(async () => { throw new Error('not used') }),
      quality: vi.fn(async () => { throw new Error('not used') }),
      job: vi.fn(async () => ({ meta: fixture.meta, job: {} })),
      trends: vi.fn(async () => { throw new Error('not used') }),
      companyProfile: vi.fn(async () => { throw new Error('not used') }),
    },
  }
})

import App from '../App'

function openAssistant() {
  const url = new URL(window.location.href)
  url.searchParams.set('page', 'assistant')
  window.history.replaceState(null, '', url)
}

async function askQuestion(question: string) {
  const user = userEvent.setup()
  render(<App />)
  const input = await screen.findByLabelText('Câu hỏi cho trợ lý AI', {}, { timeout: 5000 })
  await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false))
  await user.type(input, question)
  await user.click(screen.getByRole('button', { name: 'Gửi' }))
  return { user, input }
}

describe('assistant rendering (stubbed API)', () => {
  beforeEach(() => {
    fixture.handler.ask = async () => fixture.ANSWER
    openAssistant()
  })

  it('renders bold, a real table and job actions without exposing SQL', async () => {
    await askQuestion('Lương Data Engineer?')
    const strong = await screen.findByText('31,25 triệu VND/tháng', {}, { timeout: 5000 })
    expect(strong.tagName).toBe('STRONG')
    expect(document.querySelector('.chat-bubble.assistant table')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Xem chi tiết/i })).toBeTruthy()
    // Salary uses the same unit as the prose answer, not a raw đồng figure.
    // 32.500.000 đ is shown in the same unit the assistant writes in prose.
    expect(document.querySelector('.answer-job-side strong')?.textContent).toBe('32 triệu VND/tháng')
    const link = screen.getByRole('link', { name: /Xem tin gốc/i })
    expect(link.getAttribute('href')).toBe('https://example.com/jobs/1')
    const text = document.body.textContent ?? ''
    expect(text.toLowerCase()).not.toContain('sql')
    expect(text.toLowerCase()).not.toContain('gold.v_')
    expect(text).toContain('Hà Nội')
  })

  it('offers cancel while waiting and frees the input afterwards', async () => {
    let finish: (value: typeof fixture.ANSWER) => void = () => {}
    fixture.handler.ask = () => new Promise((resolve) => { finish = resolve })
    const { user, input } = await askQuestion('câu hỏi chậm')
    expect(await screen.findByText(/đang truy vấn/i, {}, { timeout: 5000 })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Hủy' }))
    await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false))
    finish(fixture.ANSWER)
  })

  it('opens the job list with the answer filters, resolving a skill name to its code', async () => {
    const { api } = await import('../api')
    const { user } = await askQuestion('Tìm 5 tin Backend ở Hà Nội có Python')
    await screen.findByRole('button', { name: /Xem tất cả tin phù hợp/i }, { timeout: 5000 })
    await user.click(screen.getByRole('button', { name: /Xem tất cả tin phù hợp/i }))
    await waitFor(() => expect(api.jobs).toHaveBeenCalled())
    const calls = (api.jobs as unknown as { mock: { calls: unknown[][] } }).mock.calls
    const params = (calls.at(-1)?.[0] ?? {}) as Record<string, unknown>
    expect(calls.length).toBeGreaterThan(0)
    expect(params.skill).toBe('python')
    expect(params.city).toBe('Hà Nội')
    // Multi-level comparisons are not narrowed to one level on the list page.
    expect(params.level).toBeUndefined()
  })

  it('shows a retry action when the call fails', async () => {
    fixture.handler.ask = async () => { throw new Error('Chưa thể tải kết quả. Bạn thử lại sau nhé.') }
    await askQuestion('lỗi')
    expect(await screen.findByRole('button', { name: 'Thử lại' }, { timeout: 5000 })).toBeTruthy()
  })
})
