export type Meta = {
  gold_release_id: string
  silver_release_id: string | null
  snapshot_date: string
  snapshot_cutoff_at: string
  switched_at: string | null
  served_at: string
  mode: 'live' | 'demo'
  age_days: number
  stale_after_days: number
  stale: boolean
  scope: string
  salary_definition: string
}

export type Filters = {
  q?: string
  city?: string
  domain?: string
  level?: string
  skill?: string
  company_key?: string
  page?: number
  page_size?: number
  sort?: 'recent' | 'salary' | 'title'
  expected_release_id?: string
}

export type Option = { value: string; label: string; count: number }
export type Job = {
  unified_job_id: string
  canonical_title: string
  company_key: string
  company_display_name: string
  city_bucket: string
  job_domain: string | null
  job_level: string | null
  work_mode: string | null
  entity_status: string
  canonical_salary_min_vnd: number | null
  canonical_salary_max_vnd: number | null
  salary_midpoint_vnd: number | null
  salary_sample_flag: boolean
  source_count: number
  observation_count?: number
  last_seen_at: string | null
  snapshot_cutoff_at?: string
  skills: { skill_code: string; skill_name: string }[]
  platforms: string[]
  source_url: string | null
  source_urls: { platform: string; url: string; last_observed_at: string | null }[]
}
export type DashboardResponse = {
  meta: Meta
  summary: { jobs: number; companies: number; skills: number; salary_samples: number; salary_median: number | null; missing_city: number; recent_jobs: number; oldest_last_seen: string | null; last_seen_at: string | null }
  domains: Option[]; cities: Option[]; levels: Option[]; companies: Option[]; skills: Option[]; platforms: Option[]
}
export type JobsResponse = { meta: Meta; items: Job[]; total: number; page: number; page_size: number }
export type SkillsResponse = { meta: Meta; items: { skill_code: string; skill_name: string; job_count: number }[]; pairs: { skill_a: string; skill_a_name: string; skill_b: string; skill_b_name: string; job_count: number }[] }
export type CompaniesResponse = { meta: Meta; items: { company_key: string; company_name: string; job_count: number; city_count: number; domain_count: number; salary_samples: number }[]; total: number; page: number; page_size: number }
export type QualityResponse = {
  meta: Meta
  checks: { check_code: string; check_name: string; severity: string; passed: boolean; expected: string; actual: string; checked_at: string }[]
  coverage: { snapshot_date: string; status: string; gold_release_id: string | null; silver_release_id: string | null; sla_breached: boolean }[]
  completeness: {
    jobs: number
    with_salary: number; with_salary_pct: number
    with_city: number; with_city_pct: number
    with_level: number; with_level_pct: number
    with_domain: number; with_domain_pct: number
  }
}
export type AssistantStatus = { enabled: boolean; status: string; message: string; examples: string[] }
export type AssistantAnswer = {
  answer: string
  sql: string | null
  rows: Record<string, unknown>[]
  steps: number
  provider: string
  model: string
}

const INSIGHTS_PREFIX = /^\/insights(\/|$)/.test(window.location.pathname) ? '/insights' : ''
const API_ROOT = `${INSIGHTS_PREFIX}/api`

export class ApiError extends Error {
  status: number
  code: string
  constructor(message: string, status: number, code: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
  signal?: AbortSignal,
  body?: unknown,
): Promise<T> {
  const url = new URL(`${window.location.origin}${API_ROOT}${path}`)
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== '') url.searchParams.set(key, String(value))
  })
  const response = await fetch(url, {
    headers: body === undefined
      ? { Accept: 'application/json' }
      : { Accept: 'application/json', 'Content-Type': 'application/json' },
    method: body === undefined ? 'GET' : 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new ApiError(
      payload?.error?.message ?? 'Không thể tải dữ liệu.',
      response.status,
      payload?.error?.code ?? 'UNKNOWN',
    )
  }
  return payload as T
}

export const api = {
  health: () => request<{ status: string; mode: string; ai_enabled: boolean }>('/health'),
  ready: () => request<{ status: string; meta: Meta }>('/ready'),
  filters: (filters: Filters = {}, signal?: AbortSignal) => request<{ meta: Meta; cities: Option[]; domains: Option[]; levels: Option[]; skills: Option[] }>('/filters', filters, signal),
  dashboard: (filters: Filters, signal?: AbortSignal) => request<DashboardResponse>('/dashboard', filters, signal),
  jobs: (filters: Filters, signal?: AbortSignal) => request<JobsResponse>('/jobs', filters, signal),
  job: (id: string, filters: Filters = {}, signal?: AbortSignal) => request<{ meta: Meta; job: Job }>(`/jobs/${encodeURIComponent(id)}`, filters, signal),
  skills: (filters: Filters, signal?: AbortSignal) => request<SkillsResponse>('/skills', filters, signal),
  companies: (filters: Filters, signal?: AbortSignal) => request<CompaniesResponse>('/companies', filters, signal),
  quality: (filters: Filters = {}, signal?: AbortSignal) => request<QualityResponse>('/quality', filters, signal),
  assistantStatus: () => request<AssistantStatus>('/assistant/status'),
  assistantAsk: (question: string, history: { role: string; content: string }[], signal?: AbortSignal) =>
    request<AssistantAnswer>('/assistant/ask', undefined, signal, { question, history }),
}
