import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle, Bot, Building2, BriefcaseBusiness, CheckCircle2, Download,
  Coins, RefreshCw, Search, Sparkles, X,
} from 'lucide-react'
import {
  api, ApiError, AssistantStatus, CompaniesResponse, DashboardResponse,
  Filters, Job, JobsResponse, Meta, Option, QualityResponse, SkillsResponse,
} from './api'

type Page = 'overview' | 'jobs' | 'skills' | 'companies' | 'quality' | 'assistant'

const PAGES: Page[] = ['overview', 'jobs', 'skills', 'companies', 'quality', 'assistant']
const PAGE_LABELS: Record<Page, string> = {
  overview: 'Tổng quan',
  jobs: 'Tin tuyển dụng',
  skills: 'Kỹ năng',
  companies: 'Doanh nghiệp',
  quality: 'Trạng thái dữ liệu',
  assistant: 'Trợ lý AI',
}
const initialFilters: Filters = { page: 1, page_size: 12, sort: 'recent' }
const money = new Intl.NumberFormat('vi-VN')
const number = new Intl.NumberFormat('vi-VN')

function readPageFromUrl(): Page {
  const value = new URLSearchParams(window.location.search).get('page') as Page | null
  return value && PAGES.includes(value) ? value : 'overview'
}
function formatMoney(value: number | null) {
  return value == null ? 'Chưa có dữ liệu lương' : `${money.format(value)} đ`
}
function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('vi-VN') : 'Chưa xác định'
}
function formatDay(value: string | null | undefined) {
  if (!value) return 'Chưa xác định'
  const [year, month, day] = value.slice(0, 10).split('-')
  return year && month && day ? `${day}/${month}/${year}` : value
}
function bucketLabel(value: string) {
  return value === 'UNKNOWN' ? 'Chưa xác định' : value
}
function display(value: string | null | undefined) {
  return value || 'Chưa xác định'
}

type Snapshot = {
  meta: Meta
  dashboard?: DashboardResponse
  jobs?: JobsResponse
  skills?: SkillsResponse
  companies?: CompaniesResponse
  quality?: QualityResponse
}

type FilterOptions = {
  cities: Option[]
  domains: Option[]
  levels: Option[]
  skills: Option[]
}

export default function App() {
  const [page, setPage] = useState<Page>(readPageFromUrl)
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [releaseId, setReleaseId] = useState<string | undefined>(undefined)
  const [options, setOptions] = useState<FilterOptions | null>(null)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [assistant, setAssistant] = useState<AssistantStatus | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<{ message: string; code?: string } | null>(null)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const abortRef = useRef<AbortController | null>(null)

  // Keep the browser URL in sync so /insights/?page=jobs is shareable.
  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('page', page)
    window.history.replaceState(null, '', url)
  }, [page])

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller
    setBusy(true)
    setError(null)
    const params: Filters = { ...filters, expected_release_id: releaseId }
    const run = async () => {
      try {
        if (page === 'overview') {
          const dashboard = await api.dashboard(params, controller.signal)
          setSnapshot({ meta: dashboard.meta, dashboard })
          if (!releaseId) setReleaseId(dashboard.meta.gold_release_id)
        } else if (page === 'jobs') {
          const jobs = await api.jobs(params, controller.signal)
          setSnapshot({ meta: jobs.meta, jobs })
          if (!releaseId) setReleaseId(jobs.meta.gold_release_id)
        } else if (page === 'skills') {
          const skills = await api.skills(params, controller.signal)
          setSnapshot({ meta: skills.meta, skills })
          if (!releaseId) setReleaseId(skills.meta.gold_release_id)
        } else if (page === 'companies') {
          const companies = await api.companies(params, controller.signal)
          setSnapshot({ meta: companies.meta, companies })
          if (!releaseId) setReleaseId(companies.meta.gold_release_id)
        } else if (page === 'quality') {
          const quality = await api.quality(params, controller.signal)
          setSnapshot({ meta: quality.meta, quality })
          if (!releaseId) setReleaseId(quality.meta.gold_release_id)
        } else {
          const [assistantStatus, ready] = await Promise.all([
            api.assistantStatus(), api.ready(),
          ])
          setSnapshot({ meta: ready.meta })
          setAssistant(assistantStatus)
          if (!releaseId) setReleaseId(ready.meta.gold_release_id)
        }
      } catch (caught) {
        if (controller.signal.aborted) return
        if (caught instanceof ApiError && caught.code === 'RELEASE_CHANGED') {
          // The release changed between requests: drop the pinned id and reload.
          setReleaseId(undefined)
          setReloadToken((token) => token + 1)
          setError({ message: 'Dữ liệu vừa được công bố release mới, đang tải lại…', code: caught.code })
          return
        }
        setError({ message: caught instanceof Error ? caught.message : 'Không thể tải dữ liệu.' })
      } finally {
        if (!controller.signal.aborted) setBusy(false)
      }
    }
    void run()
    return () => controller.abort()
  }, [page, filters, releaseId, reloadToken])

  // Filter catalogues follow the release, not the current page result.
  useEffect(() => {
    const controller = new AbortController()
    api.filters({ expected_release_id: releaseId }, controller.signal)
      .then((payload) => setOptions({
        cities: payload.cities,
        domains: payload.domains,
        levels: payload.levels,
        skills: payload.skills,
      }))
      .catch(() => setOptions(null))
    return () => controller.abort()
  }, [releaseId])

  const meta = snapshot?.meta ?? null
  const updateFilter = (key: keyof Filters, value: string) => {
    setFilters((current) => ({ ...current, [key]: value || undefined, page: 1 }))
  }
  const resetFilters = () => setFilters(initialFilters)
  const goToPage = (next: number) => setFilters((current) => ({ ...current, page: next }))

  const drillToSkill = (code: string) => {
    setFilters({ ...initialFilters, skill: code })
    setPage('jobs')
  }
  const drillToCompany = (key: string) => {
    setFilters({ ...initialFilters, company_key: key })
    setPage('jobs')
  }

  const activeChips = useMemo(() => {
    const entries: [keyof Filters, string][] = []
    if (filters.q) entries.push(['q', `“${filters.q}”`])
    if (filters.city) entries.push(['city', filters.city])
    if (filters.domain) entries.push(['domain', filters.domain])
    if (filters.level) entries.push(['level', filters.level])
    if (filters.skill) entries.push(['skill', filters.skill])
    if (filters.company_key) entries.push(['company_key', 'Công ty đã chọn'])
    return entries
  }, [filters])

  const exportCsv = () => {
    const jobs = snapshot?.jobs
    if (!jobs?.items.length) return
    const headers = ['unified_job_id', 'canonical_title', 'company_display_name', 'city_bucket', 'job_domain', 'job_level', 'salary_midpoint_vnd', 'snapshot_date', 'gold_release_id']
    const rows = jobs.items.map((job) => [job.unified_job_id, job.canonical_title, job.company_display_name, job.city_bucket, job.job_domain ?? '', job.job_level ?? '', job.salary_midpoint_vnd ?? '', jobs.meta.snapshot_date, jobs.meta.gold_release_id])
    const safe = (value: unknown) => {
      const text = String(value)
      const formulaSafe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text
      return `"${formulaSafe.replaceAll('"', '""')}"`
    }
    const csv = '\uFEFF' + [headers, ...rows].map((row) => row.map(safe).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `it-insights-trang-${jobs.page}-${jobs.meta.snapshot_date}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark"><Sparkles size={20} /></div>
        <div><strong>IT Insight</strong><span>Nguồn cung tuyển dụng IT</span></div>
      </div>
      <div className="top-meta">
        {meta ? <>
          <span>Snapshot <b>{formatDay(meta.snapshot_date)}</b></span>
          <span>{number.format(meta.age_days)} ngày trước</span>
          <span className={meta.stale ? 'stale' : 'fresh'}>{meta.stale ? 'Dữ liệu cũ' : 'Trong ngưỡng mới'}</span>
        </> : <span>Đang kiểm tra release…</span>}
      </div>
    </header>

    {meta?.mode === 'demo' && <div className="banner demo" role="status">
      <strong>DỮ LIỆU DEMO</strong> — 12 tin giả lập dùng để kiểm tra giao diện, không phải dữ liệu Gold thật.
    </div>}
    {meta?.stale && <div className="banner warn" role="status">
      Snapshot cách đây {meta.age_days} ngày (ngưỡng {meta.stale_after_days} ngày). Số liệu vẫn là release đang công bố, không phải dữ liệu hôm nay.
    </div>}

    <div className="layout">
      <aside className="sidebar">
        <nav aria-label="Điều hướng chính">
          {PAGES.map((key) => <button
            key={key}
            type="button"
            aria-current={page === key ? 'page' : undefined}
            className={page === key ? 'nav-item active' : 'nav-item'}
            onClick={() => setPage(key)}
          >{PAGE_LABELS[key]}</button>)}
        </nav>
        <div className="sidebar-note">
          <span className="eyebrow">Phạm vi</span>
          <p>Chỉ phản ánh nguồn cung tin mà hệ thống đã thu thập và kiểm định.</p>
        </div>
      </aside>

      <main className="content">
        <section className="page-heading">
          <div>
            <span className="eyebrow">/ insights</span>
            <h1>{PAGE_LABELS[page]}</h1>
            <p>{page === 'overview'
              ? 'Bức tranh tổng quan từ release Gold đang được công bố.'
              : 'Dữ liệu được đọc theo cùng release và cutoff.'}</p>
          </div>
          <button type="button" className="icon-button" onClick={() => setReloadToken((t) => t + 1)} title="Tải lại dữ liệu" aria-label="Tải lại dữ liệu">
            <RefreshCw size={18} className={busy ? 'spin' : undefined} />
          </button>
        </section>

        {page !== 'quality' && page !== 'assistant' && <FilterBar
          filters={filters}
          options={options}
          updateFilter={updateFilter}
          clearOne={(key) => updateFilter(key, '')}
          reset={resetFilters}
          chips={activeChips}
        />}

        {error && <div className="state-card error" role="alert">
          <AlertCircle />
          <div>
            <strong>Chưa thể tải dữ liệu</strong>
            <p>{error.message}</p>
            {error.code === 'DB_NOT_CONFIGURED' && <p>Máy chủ chưa được cấu hình kết nối Gold cho ứng dụng web.</p>}
            {error.code === 'GOLD_RELEASE_MISSING' && <p>Chưa có release Gold hợp lệ; đây không phải trạng thái “0 kết quả”.</p>}
            <button type="button" className="primary-button" onClick={() => setReloadToken((t) => t + 1)}>Thử lại</button>
          </div>
        </div>}

        {!error && busy && !snapshot && <div className="state-card" role="status"><RefreshCw className="spin" /> Đang tải dữ liệu…</div>}
        {!error && snapshot && <div className={busy ? 'refreshing' : undefined}>
          {page === 'overview' && snapshot.dashboard && <Overview data={snapshot.dashboard} onCompany={drillToCompany} onSkill={drillToSkill} />}
          {page === 'jobs' && snapshot.jobs && <Jobs
            data={snapshot.jobs}
            sort={filters.sort ?? 'recent'}
            onSort={(sort) => setFilters((current) => ({ ...current, sort, page: 1 }))}
            onSelect={(job) => setSelectedJobId(job.unified_job_id)}
            onExport={exportCsv}
            onPage={goToPage}
          />}
          {page === 'skills' && snapshot.skills && <Skills data={snapshot.skills} onSkill={drillToSkill} />}
          {page === 'companies' && snapshot.companies && <Companies data={snapshot.companies} onSelect={drillToCompany} onPage={goToPage} />}
          {page === 'quality' && snapshot.quality && <Quality data={snapshot.quality} />}
          {page === 'assistant' && <Assistant status={assistant} />}
        </div>}
      </main>
    </div>

    {selectedJobId && <JobModal
      jobId={selectedJobId}
      releaseId={releaseId}
      onClose={() => setSelectedJobId(null)}
    />}
  </div>
}

function FilterBar({ filters, options, updateFilter, clearOne, reset, chips }: {
  filters: Filters
  options: FilterOptions | null
  updateFilter: (key: keyof Filters, value: string) => void
  clearOne: (key: keyof Filters) => void
  reset: () => void
  chips: [keyof Filters, string][]
}) {
  return <section className="filter-panel">
    <div className="filter-bar">
      <div className="search-box">
        <Search size={17} />
        <input
          value={filters.q ?? ''}
          onChange={(event) => updateFilter('q', event.target.value)}
          placeholder="Tìm tên tin hoặc công ty…"
          maxLength={200}
          aria-label="Tìm tên tin hoặc công ty"
        />
      </div>
      <select value={filters.city ?? ''} onChange={(e) => updateFilter('city', e.target.value)} aria-label="Lọc theo địa điểm">
        <option value="">Mọi địa điểm</option>
        {filters.city && !(options?.cities ?? []).some((item) => item.value === filters.city) && <option value={filters.city}>{filters.city}</option>}
        {(options?.cities ?? []).map((item) => <option key={item.value} value={item.value}>{item.label} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.domain ?? ''} onChange={(e) => updateFilter('domain', e.target.value)} aria-label="Lọc theo chuyên môn">
        <option value="">Mọi chuyên môn</option>
        {filters.domain && !(options?.domains ?? []).some((item) => item.value === filters.domain) && <option value={filters.domain}>{filters.domain}</option>}
        {(options?.domains ?? []).map((item) => <option key={item.value} value={item.value}>{item.label} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.level ?? ''} onChange={(e) => updateFilter('level', e.target.value)} aria-label="Lọc theo cấp bậc">
        <option value="">Mọi cấp bậc</option>
        {filters.level && !(options?.levels ?? []).some((item) => item.value === filters.level) && <option value={filters.level}>{filters.level}</option>}
        {(options?.levels ?? []).map((item) => <option key={item.value} value={item.value}>{item.label} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.skill ?? ''} onChange={(e) => updateFilter('skill', e.target.value)} aria-label="Lọc theo kỹ năng">
        <option value="">Mọi kỹ năng</option>
        {filters.skill && !(options?.skills ?? []).some((item) => item.value === filters.skill) && <option value={filters.skill}>{filters.skill}</option>}
        {(options?.skills ?? []).map((item) => <option key={item.value} value={item.value}>{item.label} ({number.format(item.count)})</option>)}
      </select>
      <button type="button" className="ghost-button" onClick={reset} disabled={chips.length === 0}>Xóa lọc</button>
    </div>
    {chips.length > 0 && <div className="chips active-chips">
      {chips.map(([key, label]) => <button type="button" key={key} onClick={() => clearOne(key)}>
        {label} <X size={13} aria-hidden />
      </button>)}
    </div>}
  </section>
}

function ReleaseNote({ meta }: { meta: Meta }) {
  return <div className="release-note">
    <span className={meta.stale ? 'dot stale-dot' : 'dot'} aria-hidden />
    <span>
      Release <b>{meta.gold_release_id}</b> ·
      cutoff {formatDate(meta.snapshot_cutoff_at)} · {meta.stale ? 'dữ liệu đã cũ theo ngưỡng hiển thị' : 'đang trong ngưỡng freshness'}
    </span>
  </div>
}

function Overview({ data, onCompany, onSkill }: { data: DashboardResponse; onCompany: (key: string) => void; onSkill: (code: string) => void }) {
  return <>
    <ReleaseNote meta={data.meta} />
    <div className="metric-grid">
      <Metric icon={<BriefcaseBusiness />} label="Tin hợp nhất được quan sát" value={number.format(data.summary.jobs)} note="Theo snapshot Gold" />
      <Metric icon={<Building2 />} label="Doanh nghiệp" value={number.format(data.summary.companies)} note="Không tính nhóm chưa xác định" />
      <Metric icon={<Sparkles />} label="Kỹ năng xuất hiện" value={number.format(data.summary.skills)} note="Skill hợp lệ trong cohort" />
      <Metric
        icon={<Coins />}
        label="Trung vị lương đăng tuyển"
        value={data.summary.salary_median == null ? '—' : `${money.format(data.summary.salary_median)} đ`}
        note={data.summary.salary_samples === 0 ? 'Chưa có mẫu lương hợp lệ' : `${number.format(data.summary.salary_samples)} mẫu hợp lệ`}
      />
    </div>
    <p className="definition">Lương: trung vị của trung điểm khoảng lương VND trên các tin có đủ min/max hợp lệ. Quan sát gần nhất: {formatDate(data.summary.last_seen_at)}.</p>
    <div className="grid-two">
      <Distribution title="Theo chuyên môn" items={data.domains} />
      <Distribution title="Theo địa điểm" items={data.cities} />
    </div>
    <div className="grid-two">
      <RankPanel title="Công ty nhiều tin nhất" emptyText="Chưa có dữ liệu công ty.">
        {data.companies.slice(0, 8).map((item) => <button type="button" className="rank-row clickable" key={item.value} onClick={() => onCompany(item.value)}>
          <span>{item.label ?? item.value}</span><strong>{number.format(item.count)} tin</strong>
        </button>)}
      </RankPanel>
      <RankPanel title="Kỹ năng phổ biến" emptyText="Chưa có dữ liệu kỹ năng.">
        {data.skills.slice(0, 8).map((item) => <button type="button" className="rank-row clickable" key={item.value} onClick={() => onSkill(item.value)}>
          <span>{item.label ?? item.value}</span><strong>{number.format(item.count)} tin</strong>
        </button>)}
      </RankPanel>
    </div>
    <div className="grid-two">
      <Distribution title="Theo cấp bậc" items={data.levels} />
      <Distribution title="Nền tảng có bằng chứng gần đây" items={data.platforms} />
    </div>
  </>
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) {
  return <div className="metric-card">
    <div className="metric-icon">{icon}</div>
    <span className="metric-label">{label}</span>
    <strong>{value}</strong>
    <small>{note}</small>
  </div>
}

function Distribution({ title, items }: { title: string; items: { value: string; label?: string; count: number }[] }) {
  const max = Math.max(1, ...items.map((item) => item.count))
  return <section className="panel">
    <div className="panel-heading"><h2>{title}</h2><span>{items.length} nhóm</span></div>
    {items.length === 0 && <p className="muted">Chưa có dữ liệu cho nhóm này.</p>}
    {items.slice(0, 8).map((item) => <div className="bar-row" key={item.value}>
      <div className="bar-label"><span>{bucketLabel(item.label ?? item.value)}</span><b>{number.format(item.count)}</b></div>
      <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / max) * 100)}%` }} /></div>
    </div>)}
    {items.length > 8 && <p className="muted">Hiển thị 8 trong {items.length} nhóm lớn nhất.</p>}
  </section>
}

function RankPanel({ title, emptyText, children }: { title: string; emptyText: string; children: React.ReactNode }) {
  const hasChildren = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return <section className="panel">
    <div className="panel-heading"><h2>{title}</h2><span>Bấm để mở danh sách tin</span></div>
    <div className="rank-list">{hasChildren ? children : <p className="muted">{emptyText}</p>}</div>
  </section>
}

function Jobs({ data, sort, onSort, onSelect, onExport, onPage }: {
  data: JobsResponse
  sort: 'recent' | 'salary' | 'title'
  onSort: (sort: 'recent' | 'salary' | 'title') => void
  onSelect: (job: Job) => void
  onExport: () => void
  onPage: (page: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size))
  return <>
    <ReleaseNote meta={data.meta} />
    <div className="toolbar">
      <span>{number.format(data.total)} tin trong cohort</span>
      <div>
        <button type="button" className="ghost-button" onClick={onExport} disabled={!data.items.length}>
          <Download size={16} /> Xuất trang đang xem
        </button>
        <select value={sort} onChange={(event) => onSort(event.target.value as 'recent' | 'salary' | 'title')} aria-label="Sắp xếp danh sách tin">
          <option value="recent">Mới quan sát</option>
          <option value="salary">Lương cao</option>
          <option value="title">Tên tin</option>
        </select>
      </div>
    </div>
    <div className="job-list">
      {data.items.length ? data.items.map((job) => <article
        className="job-card"
        key={job.unified_job_id}
        role="button"
        tabIndex={0}
        onClick={() => onSelect(job)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onSelect(job)
          }
        }}
      >
        <div className="job-main">
          <span className="job-domain">{bucketLabel(display(job.job_domain))}</span>
          <h2>{job.canonical_title}</h2>
          <p className="company-name">{job.company_display_name}</p>
          <div className="chips">
            <span><em>Địa điểm</em>{bucketLabel(display(job.city_bucket))}</span>
            <span><em>Cấp bậc</em>{bucketLabel(display(job.job_level))}</span>
            <span><em>Hình thức</em>{bucketLabel(display(job.work_mode))}</span>
            {job.platforms.slice(0, 3).map((platform) => <span className="source-chip" key={platform}>{platform}</span>)}
          </div>
        </div>
        <div className="job-side">
          <strong className={job.salary_sample_flag ? undefined : 'muted-salary'}>{job.salary_sample_flag ? formatMoney(job.salary_midpoint_vnd) : 'Chưa có lương'}</strong>
          <small>Quan sát gần nhất<br />{formatDate(job.last_seen_at)}</small>
          <small>{job.source_count} nguồn · {job.skills.length} kỹ năng</small>
        </div>
      </article>) : <div className="empty-card">Không có tin phù hợp với bộ lọc hiện tại.</div>}
    </div>
    <div className="pagination">
      <button type="button" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>← Trước</button>
      <span>Trang {data.page} / {totalPages}</span>
      <button type="button" disabled={data.page >= totalPages} onClick={() => onPage(data.page + 1)}>Sau →</button>
    </div>
  </>
}

function Skills({ data, onSkill }: { data: SkillsResponse; onSkill: (code: string) => void }) {
  return <>
    <ReleaseNote meta={data.meta} />
    <div className="grid-two">
      <section className="panel">
        <div className="panel-heading"><h2>Kỹ năng được nhắc đến</h2><span>Job duy nhất</span></div>
        <div className="rank-list">
          {data.items.length === 0 && <p className="muted">Chưa có kỹ năng trong cohort này.</p>}
          {data.items.slice(0, 20).map((item, index) => <button type="button" className="rank-row clickable" key={item.skill_code} onClick={() => onSkill(item.skill_code)}>
            <b>{String(index + 1).padStart(2, '0')}</b>
            <span>{item.skill_name}</span>
            <strong>{number.format(item.job_count)} tin</strong>
          </button>)}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><h2>Cặp kỹ năng đồng xuất hiện</h2><span>Trong cùng job</span></div>
        <div className="rank-list">
          {data.pairs.length === 0 && <p className="muted">Chưa đủ dữ liệu để tính cặp kỹ năng.</p>}
          {data.pairs.slice(0, 20).map((item) => <div className="pair-row" key={`${item.skill_a}-${item.skill_b}`}>
            <span>{item.skill_a_name} <i>+</i> {item.skill_b_name}</span>
            <strong>{number.format(item.job_count)}</strong>
          </div>)}
        </div>
      </section>
    </div>
  </>
}

function Companies({ data, onSelect, onPage }: { data: CompaniesResponse; onSelect: (key: string) => void; onPage: (page: number) => void }) {
  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size))
  return <>
    <ReleaseNote meta={data.meta} />
    <section className="panel">
      <div className="panel-heading"><h2>Doanh nghiệp trong snapshot</h2><span>{number.format(data.total)} doanh nghiệp chuẩn hóa</span></div>
      <div className="rank-list">
        {data.items.length === 0 && <p className="muted">Không có doanh nghiệp phù hợp bộ lọc.</p>}
        {data.items.map((item) => <button type="button" className="company-row" key={item.company_key} onClick={() => onSelect(item.company_key)}>
          <span>{item.company_name}<small>{item.city_count} địa điểm · {item.domain_count} chuyên môn · {item.salary_samples} mẫu lương</small></span>
          <strong>{number.format(item.job_count)} tin →</strong>
        </button>)}
      </div>
    </section>
    <div className="pagination">
      <button type="button" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>← Trước</button>
      <span>Trang {data.page} / {totalPages}</span>
      <button type="button" disabled={data.page >= totalPages} onClick={() => onPage(data.page + 1)}>Sau →</button>
    </div>
  </>
}

function Quality({ data }: { data: QualityResponse }) {
  const passed = data.checks.filter((check) => check.passed).length
  return <>
    <ReleaseNote meta={data.meta} />
    <div className="quality-summary">
      <CheckCircle2 size={28} />
      <div>
        <strong>{passed}/{data.checks.length} kiểm tra đạt</strong>
        <p>DQ và coverage của release đang phục vụ · snapshot {data.meta.snapshot_date}</p>
      </div>
    </div>
    <section className="panel">
      <div className="panel-heading"><h2>Data quality checks</h2><span>Severity và giá trị thực tế</span></div>
      {data.checks.length === 0 && <p className="muted">Chưa có bản ghi kiểm tra chất lượng cho release này.</p>}
      {data.checks.map((check) => <div className="check-row" key={check.check_code}>
        <span className={check.passed ? 'check-ok' : 'check-fail'}>{check.passed ? 'PASS' : 'FAIL'}</span>
        <span><b>{check.check_code}</b> {check.check_name}</span>
        <small>{check.severity} · {check.actual}</small>
      </div>)}
    </section>
    <section className="panel coverage-panel">
      <div className="panel-heading"><h2>Lịch sử coverage</h2><span>Tối đa 30 ngày</span></div>
      {data.coverage.length === 0 && <p className="muted">Chưa có lịch sử coverage.</p>}
      {data.coverage.map((row) => <div className="check-row" key={row.snapshot_date}>
        <span className={row.sla_breached ? 'check-fail' : 'check-ok'}>{row.status}</span>
        <span>{row.snapshot_date}</span>
        <small>{row.gold_release_id ?? '—'}</small>
      </div>)}
    </section>
  </>
}

function Assistant({ status }: { status: AssistantStatus | null }) {
  return <section className="assistant-card">
    <div className="assistant-icon"><Bot size={36} /></div>
    <span className="eyebrow">Giai đoạn web</span>
    <h2>Trợ lý AI chưa cấu hình</h2>
    <p>{status?.message ?? 'AI sẽ được tích hợp sau khi web/API được nghiệm thu và mô hình được lựa chọn.'}</p>
    <div className="suggestions">{(status?.examples ?? []).map((example) => <span key={example}>{example}</span>)}</div>
    <button type="button" className="primary-button" disabled>Chờ cấu hình mô hình</button>
  </section>
}

function JobModal({ jobId, releaseId, onClose }: { jobId: string; releaseId?: string; onClose: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    setJob(null)
    setError('')
    api.job(jobId, { expected_release_id: releaseId }, controller.signal)
      .then((payload) => setJob(payload.job))
      .catch((caught) => {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Không thể tải chi tiết.')
      })
    return () => controller.abort()
  }, [jobId, releaseId])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab' && closeRef.current) {
        // Single-focus dialog: keep keyboard focus inside the modal.
        event.preventDefault()
        closeRef.current.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      previous?.focus()
    }
  }, [onClose])

  return <div className="modal-backdrop" onClick={onClose} role="presentation">
    <div className="modal" role="dialog" aria-modal="true" aria-label="Chi tiết tin tuyển dụng" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="modal-close" onClick={onClose} ref={closeRef} aria-label="Đóng chi tiết">
        <X size={20} />
      </button>
      {error && <p className="muted">{error}</p>}
      {!job && !error && <p className="muted"><RefreshCw className="spin" /> Đang tải chi tiết…</p>}
      {job && <>
        <span className="job-domain">{display(job.job_domain)}</span>
        <h2>{job.canonical_title}</h2>
        <p className="company-name">{job.company_display_name}</p>
        <div className="detail-grid">
          <div><small>Địa điểm</small><b>{display(job.city_bucket)}</b></div>
          <div><small>Cấp bậc</small><b>{display(job.job_level)}</b></div>
          <div><small>Hình thức</small><b>{display(job.work_mode)}</b></div>
          <div><small>Lương midpoint</small><b>{job.salary_sample_flag ? formatMoney(job.salary_midpoint_vnd) : 'Chưa có dữ liệu'}</b></div>
          <div><small>Số nguồn</small><b>{job.source_count}</b></div>
          <div><small>Quan sát gần nhất</small><b>{formatDate(job.last_seen_at)}</b></div>
          <div><small>Snapshot cutoff</small><b>{formatDate(job.snapshot_cutoff_at)}</b></div>
          <div><small>Trạng thái entity</small><b>{job.entity_status}</b></div>
        </div>
        <h3>Kỹ năng</h3>
        <div className="chips">
          {job.skills.length ? job.skills.map((skill) => <span key={skill.skill_code}>{skill.skill_name}</span>) : <span>Chưa xác định</span>}
        </div>
        <h3>Nền tảng có bằng chứng</h3>
        <div className="chips">
          {job.platforms.length ? job.platforms.map((platform) => <span key={platform}>{platform}</span>) : <span>Chưa xác định</span>}
        </div>
        <p className="modal-note">Đây là tin hợp nhất được hệ thống quan sát; không phải khẳng định listing vẫn còn trực tuyến trên nguồn. Gold hiện không cung cấp URL/description đã kiểm chứng cho web.</p>
      </>}
    </div>
  </div>
}
