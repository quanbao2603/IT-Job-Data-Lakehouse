import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle, ArrowRight, Bot, Building2, BriefcaseBusiness, CheckCircle2, Coins,
  Download, ExternalLink, Info, LayoutDashboard, Lock, RefreshCw, Search, ShieldCheck,
  Sparkles, Tags, X,
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
const PAGE_ICONS: Record<Page, React.ReactNode> = {
  overview: <LayoutDashboard size={17} aria-hidden />,
  jobs: <BriefcaseBusiness size={17} aria-hidden />,
  skills: <Tags size={17} aria-hidden />,
  companies: <Building2 size={17} aria-hidden />,
  quality: <ShieldCheck size={17} aria-hidden />,
  assistant: <Bot size={17} aria-hidden />,
}
const PAGE_SUBTITLES: Record<Page, string> = {
  overview: 'Bức tranh tổng quan từ release Gold đang được công bố.',
  jobs: 'Tìm kiếm và khám phá những tin mà hệ thống đã ghi nhận.',
  skills: 'Kỹ năng nào xuất hiện trong tin tuyển dụng và thường đi cùng nhau.',
  companies: 'Cơ cấu tin tuyển dụng của từng doanh nghiệp trong dữ liệu đang công bố.',
  quality: 'Các kiểm tra chất lượng và độ phủ của release đang phục vụ.',
  assistant: 'Không gian hỏi đáp với dữ liệu thị trường tuyển dụng.',
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
function shareLabel(count: number, total: number) {
  if (!total) return '—'
  const pct = (count / total) * 100
  if (pct > 0 && pct < 1) return '<1%'
  return `${Math.round(pct)}%`
}
function initials(name: string) {
  // Some Gold rows carry a source template literal; never print it as an avatar.
  const words = name.replace(/\(.*?\)/g, '').match(/[\p{L}\p{N}]+/gu) ?? []
  if (words.length === 0) return '–'
  if (words.length === 1) return (words[0] ?? '').slice(0, 2).toUpperCase()
  const first = words[0] ?? ''
  const last = words[words.length - 1] ?? first
  return `${first.charAt(0)}${last.charAt(0)}`.toUpperCase()
}
function safeHost(url: string) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
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
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><Sparkles size={21} aria-hidden /></div>
        <div>
          <strong>IT Insight</strong>
          <small>Job Data Explorer</small>
        </div>
      </div>

      <p className="workspace-label">Không gian phân tích</p>
      <nav aria-label="Điều hướng chính">
        {PAGES.map((key) => <button
          key={key}
          type="button"
          aria-current={page === key ? 'page' : undefined}
          className={page === key ? 'nav-item active' : 'nav-item'}
          title={PAGE_LABELS[key]}
          onClick={() => setPage(key)}
        >{PAGE_ICONS[key]}<span>{PAGE_LABELS[key]}</span>{key === 'assistant' && <small className="nav-badge" aria-hidden="true">AI</small>}</button>)}
      </nav>

      <div className="sidebar-bottom">
        <button type="button" className="sidebar-status" onClick={() => setPage('quality')}>
          <span className={meta?.stale ? 'dot stale-dot' : 'dot'} aria-hidden />
          <span>
            <b>{meta ? (meta.stale ? 'Dữ liệu cũ' : 'cập nhật mới') : 'Đang kiểm tra'}</b>
            <small>{meta ? `cập nhật ngày ${formatDay(meta.snapshot_date)}` : 'Chưa có dữ liệu'}</small>
          </span>
        </button>
        <div className="sidebar-profile">
          <span>LH</span>
          <span>
            Lakehouse
            <small>Không gian chỉ đọc</small>
          </span>
          <Lock size={14} aria-hidden />
        </div>
      </div>
    </aside>

    <div className="main-shell">
      <header className="topbar">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          Phân tích <span aria-hidden>/</span> <strong>{PAGE_LABELS[page]}</strong>
        </nav>
        <div className="topbar-right">
          <span className="read-only"><Lock size={12} aria-hidden /> Chỉ đọc</span>
          <span className="avatar" title="Không gian Lakehouse, chỉ đọc">LH</span>
        </div>
      </header>

      <main>
        <section className="page-heading">
          <div>
            <span className="eyebrow">Phân tích</span>
            <h1>{PAGE_LABELS[page]}</h1>
            <p>{PAGE_SUBTITLES[page]}</p>
          </div>
          <div className="heading-actions">
            <span className="snapshot-chip">
              <span className={meta?.stale ? 'dot stale-dot' : 'dot'} aria-hidden />
              cập nhật ngày <b>{formatDay(meta?.snapshot_date)}</b>
              {meta && meta.age_days > 0 && <span className="chip-age">{number.format(meta.age_days)} ngày trước</span>}
            </span>
            <button type="button" className="button primary" onClick={() => setReloadToken((t) => t + 1)}>
              <RefreshCw size={15} className={busy ? 'spin' : undefined} aria-hidden /> Làm mới
            </button>
          </div>
        </section>

        {meta?.mode === 'demo' && <div className="banner demo" role="status">
          <Info size={16} aria-hidden />
          <p><strong>Dữ liệu demo</strong> — 12 tin giả lập dùng để kiểm tra giao diện, không phải dữ liệu Gold thật.</p>
        </div>}
        {meta?.stale && <div className="banner warn" role="status">
          <AlertCircle size={16} aria-hidden />
          <p>Dữ liệu cập nhật cách đây {meta.age_days} ngày (ngưỡng {meta.stale_after_days} ngày). Số liệu vẫn là bản đang công bố, không phải dữ liệu hôm nay.</p>
        </div>}

        {page !== 'quality' && page !== 'assistant' && <FilterBar
          filters={filters}
          options={options}
          updateFilter={updateFilter}
          clearOne={(key) => updateFilter(key, '')}
          reset={resetFilters}
          chips={activeChips}
        />}

        {error && <div className="state-card error" role="alert">
          <AlertCircle aria-hidden />
          <div>
            <strong>Chưa thể tải dữ liệu</strong>
            <p>{error.message}</p>
            {error.code === 'DB_NOT_CONFIGURED' && <p>Máy chủ chưa được cấu hình kết nối Gold cho ứng dụng web.</p>}
            {error.code === 'GOLD_RELEASE_MISSING' && <p>Chưa có release Gold hợp lệ; đây không phải trạng thái “0 kết quả”.</p>}
            <button type="button" className="button" onClick={() => setReloadToken((t) => t + 1)}>Thử lại</button>
          </div>
        </div>}

        {!error && busy && !snapshot && <div className="state-card" role="status"><RefreshCw className="spin" aria-hidden /> Đang tải dữ liệu…</div>}
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

        <footer className="footer">
          <span>IT Insight · Dữ liệu để hiểu thị trường</span>
          <span>Phạm vi: tin tuyển dụng IT mà hệ thống ghi nhận</span>
        </footer>
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
      <span className="filter-title"><Search size={14} aria-hidden /> Bộ lọc</span>
      <div className="search-box">
        <Search size={15} aria-hidden />
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
      <button type="button" className="button small" onClick={reset} disabled={chips.length === 0}>Xóa lọc</button>
    </div>
    {chips.length > 0 && <div className="chips active-chips">
      {chips.map(([key, label]) => <button type="button" key={key} onClick={() => clearOne(key)}>
        {label} <X size={12} aria-hidden />
      </button>)}
    </div>}
  </section>
}

function ReleaseNote({ meta }: { meta: Meta }) {
  return <div className="release-note">
    <span className={meta.stale ? 'dot stale-dot' : 'dot'} aria-hidden />
    <span>
      cập nhật ngày <b>{formatDay(meta.snapshot_date)}</b>
    </span>
  </div>
}

function Overview({ data, onCompany, onSkill }: { data: DashboardResponse; onCompany: (key: string) => void; onSkill: (code: string) => void }) {
  return <>
    <div className="metric-grid">
      <Metric icon={<BriefcaseBusiness size={18} aria-hidden />} tone="green" label="Tin hợp nhất được quan sát" value={number.format(data.summary.jobs)} note="Theo dữ liệu Gold" />
      <Metric icon={<Building2 size={18} aria-hidden />} tone="blue" label="Doanh nghiệp" value={number.format(data.summary.companies)} note="Không tính nhóm chưa xác định" />
      <Metric icon={<Sparkles size={18} aria-hidden />} tone="purple" label="Kỹ năng xuất hiện" value={number.format(data.summary.skills)} note="Skill hợp lệ trong cohort" />
      <Metric
        icon={<Coins size={18} aria-hidden />}
        tone="orange"
        label="Trung vị lương đăng tuyển"
        value={data.summary.salary_median == null ? '—' : money.format(data.summary.salary_median)}
        unit={data.summary.salary_median == null ? undefined : 'đ'}
        note={data.summary.salary_samples === 0 ? 'Chưa có mẫu lương hợp lệ' : `${number.format(data.summary.salary_samples)} mẫu hợp lệ`}
      />
    </div>
    <div className="overview-charts">
      <Distribution title="Theo chuyên môn" hint="Số tin trong dữ liệu · tối đa 8 nhóm" items={data.domains} />
      <Donut title="Theo địa điểm" hint="Địa điểm đại diện của mỗi tin" items={data.cities} />
    </div>
    <div className="overview-bottom">
      <RankPanel title="Công ty nhiều tin nhất" hint="Bấm để mở danh sách tin" emptyText="Chưa có dữ liệu công ty.">
        {data.companies.slice(0, 8).map((item) => <button type="button" className="rank-row clickable" key={item.value} onClick={() => onCompany(item.value)}>
          <span className="initials small">{initials(item.label ?? item.value)}</span>
          <span className="rank-name">{item.label ?? item.value}</span>
          <strong>{number.format(item.count)} tin</strong>
        </button>)}
      </RankPanel>
      <RankPanel title="Kỹ năng phổ biến" hint="Một tin có thể ghi nhận nhiều kỹ năng" emptyText="Chưa có dữ liệu kỹ năng.">
        {data.skills.slice(0, 8).map((item, index) => <button type="button" className="rank-row clickable" key={item.value} onClick={() => onSkill(item.value)}>
          <b className="rank-index">{String(index + 1).padStart(2, '0')}</b>
          <span className="rank-name">{item.label ?? item.value}</span>
          <strong>{number.format(item.count)} tin</strong>
        </button>)}
      </RankPanel>
    </div>
    <div className="grid-two">
      <Distribution title="Theo cấp bậc" hint="Cấp bậc chuẩn hóa của tin" items={data.levels} />
      <Distribution title="nền tảng" hint="Số tin theo từng nguồn dữ liệu" items={data.platforms} />
    </div>
  </>
}

function Metric({ icon, tone, label, value, unit, note }: { icon: React.ReactNode; tone: string; label: string; value: string; unit?: string; note: string }) {
  return <div className="metric-card">
    <div className="metric-top">
      <span className="metric-label">{label}</span>
      <span className={`metric-icon ${tone}`}>{icon}</span>
    </div>
    <strong>{value}{unit && <span className="metric-unit">{unit}</span>}</strong>
    <small>{note}</small>
  </div>
}

function Distribution({ title, hint, items }: { title: string; hint: string; items: { value: string; label?: string; count: number }[] }) {
  const max = Math.max(1, ...items.map((item) => item.count))
  return <section className="panel">
    <div className="panel-heading">
      <div><h2>{title}</h2><p>{hint}</p></div>
      <span className="muted-badge">{items.length} nhóm</span>
    </div>
    <div className="bar-list">
      {items.length === 0 && <p className="muted">Chưa có dữ liệu cho nhóm này.</p>}
      {items.slice(0, 8).map((item) => <div className="bar-row" key={item.value}>
        <div className="bar-label"><span>{bucketLabel(item.label ?? item.value)}</span><b>{number.format(item.count)}</b></div>
        <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / max) * 100)}%` }} /></div>
      </div>)}
    </div>
    {items.length > 8 && <p className="panel-note">Hiển thị 8 trong {items.length} nhóm lớn nhất.</p>}
  </section>
}

const DONUT_COLORS = ['#147e73', '#3f9e8e', '#74bfae', '#a7d7c9', '#cde8df', '#9aa8a4']

function Donut({ title, hint, items }: { title: string; hint: string; items: { value: string; label?: string; count: number }[] }) {
  const shown = items.slice(0, 8)
  const total = shown.reduce((sum, item) => sum + item.count, 0)
  const radius = 62
  const circumference = 2 * Math.PI * radius
  let offset = 0
  return <section className="panel">
    <div className="panel-heading">
      <div><h2>{title}</h2><p>{hint}</p></div>
      <span className="muted-badge">{items.length} nhóm</span>
    </div>
    {shown.length === 0
      ? <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có dữ liệu cho nhóm này.</p>
      : <div className="donut-wrap">
        <div className="donut">
          <svg viewBox="0 0 160 160" role="img"
            aria-label={`${title}: ${shown.map((item) => `${bucketLabel(item.label ?? item.value)} ${number.format(item.count)} tin`).join(', ')}`}>
            <circle cx="80" cy="80" r={radius} className="donut-track" />
            {shown.map((item, index) => {
              const share = total ? item.count / total : 0
              const dash = share * circumference
              const element = <circle
                key={item.value}
                cx="80" cy="80" r={radius}
                fill="none"
                stroke={DONUT_COLORS[index % DONUT_COLORS.length]}
                strokeWidth="22"
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={-offset}
                transform="rotate(-90 80 80)"
              />
              offset += dash
              return element
            })}
          </svg>
          <div className="donut-label">
            <strong>{number.format(total)}</strong>
            <span>tin theo địa điểm</span>
          </div>
        </div>
        <ul className="legend">
          {shown.map((item, index) => <li key={item.value}>
            <i style={{ background: DONUT_COLORS[index % DONUT_COLORS.length] }} aria-hidden />
            <span className="legend-name">{bucketLabel(item.label ?? item.value)}</span>
            <strong>{number.format(item.count)}</strong>
            <small>{shareLabel(item.count, total)}</small>
          </li>)}
        </ul>
      </div>}
    <p className="panel-note">Tỷ lệ tính trên {number.format(total)} tin trong {shown.length} nhóm địa điểm hiển thị; nhóm “Chưa xác định” là tin không có địa điểm đại diện.</p>
  </section>
}

function RankPanel({ title, hint, emptyText, children }: { title: string; hint: string; emptyText: string; children: React.ReactNode }) {
  const hasChildren = Array.isArray(children) ? children.length > 0 : Boolean(children)
  return <section className="panel">
    <div className="panel-heading"><div><h2>{title}</h2><p>{hint}</p></div></div>
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
    <div className="toolbar">
      <span>{number.format(data.total)} tin trong cohort</span>
      <div className="inline-actions">
        <select value={sort} onChange={(event) => onSort(event.target.value as 'recent' | 'salary' | 'title')} aria-label="Sắp xếp danh sách tin">
          <option value="recent">Mới quan sát</option>
          <option value="salary">Lương cao</option>
          <option value="title">Tên tin</option>
        </select>
        <button type="button" className="button small" onClick={onExport} disabled={!data.items.length}>
          <Download size={14} aria-hidden /> Xuất trang này
        </button>
      </div>
    </div>
    <section className="panel jobs-panel">
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
          <span className="initials" aria-hidden>{initials(job.company_display_name)}</span>
          <div className="job-main">
            <span className="job-domain">{bucketLabel(display(job.job_domain))}</span>
            <h2>{job.canonical_title}</h2>
            <p className="company-name">{job.company_display_name}</p>
            <div className="job-meta">
              <span><em>Địa điểm</em>{bucketLabel(display(job.city_bucket))}</span>
              <span><em>Cấp bậc</em>{bucketLabel(display(job.job_level))}</span>
              <span><em>Hình thức</em>{bucketLabel(display(job.work_mode))}</span>
            </div>
            <div className="skill-tags">
              {job.skills.length === 0 && <span className="muted">Chưa ghi nhận kỹ năng</span>}
              {job.skills.slice(0, 6).map((skill) => <span key={skill.skill_code}>{skill.skill_name}</span>)}
              {job.skills.length > 6 && <span className="more-tag">+{job.skills.length - 6} kỹ năng</span>}
              {job.platforms.slice(0, 2).map((platform) => <span className="source-tag" key={platform}>{platform}</span>)}
            </div>
          </div>
          <div className="job-side">
            <strong className={job.salary_sample_flag ? 'salary' : 'salary muted-salary'}>{job.salary_sample_flag ? formatMoney(job.salary_midpoint_vnd) : 'Chưa có lương'}</strong>
            <small>cập nhật gần nhất<br />{formatDate(job.last_seen_at)}</small>
            <small>{job.source_count} nguồn · {job.skills.length > 0 ? `${job.skills.length} kỹ năng` : 'Chưa ghi nhận kỹ năng'}</small>
            {job.source_url
              ? <a
                className="button small source-link"
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => event.stopPropagation()}
              >Xem tin gốc <ExternalLink size={13} aria-hidden /></a>
              : <small className="muted">Chưa có liên kết nguồn</small>}
            <span className="button small detail-button">Xem chi tiết <ArrowRight size={13} aria-hidden /></span>
          </div>
        </article>) : <div className="empty-card">Không có tin phù hợp với bộ lọc hiện tại.</div>}
      </div>
      <div className="pagination">
        <span>Trang {data.page} / {totalPages}</span>
        <div>
          <button type="button" className="button small" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>← Trước</button>
          <button type="button" className="button small" disabled={data.page >= totalPages} onClick={() => onPage(data.page + 1)}>Sau →</button>
        </div>
      </div>
    </section>
  </>
}

function Skills({ data, onSkill }: { data: SkillsResponse; onSkill: (code: string) => void }) {
  const max = Math.max(1, ...data.items.map((item) => item.job_count))
  return <>
    <div className="skills-layout">
      <section className="panel">
        <div className="panel-heading">
          <div><h2>Kỹ năng trong tin tuyển dụng</h2></div>
          <span className="muted-badge">{number.format(data.items.length)} kỹ năng</span>
        </div>
        <div className="table-wrap">
          <div className="table-head skill-columns">
            <span>Kỹ năng</span><span className="num">Số tin</span><span>So với kỹ năng dẫn đầu</span>
          </div>
          {data.items.length === 0 && <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có kỹ năng trong cohort này.</p>}
          {data.items.slice(0, 20).map((item) => <button type="button" className="rank-row clickable skill-columns" key={item.skill_code} onClick={() => onSkill(item.skill_code)}>
            <span className="rank-name">{item.skill_name}</span>
            <strong className="num">{number.format(item.job_count)}</strong>
            <span className="skill-share">
              <span className="bar-track"><span style={{ width: `${Math.max(3, (item.job_count / max) * 100)}%` }} /></span>
            </span>
          </button>)}
        </div>
        {data.items.length > 20 && <p className="panel-note">Hiển thị 20 trong {number.format(data.items.length)} kỹ năng.</p>}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div><h2>Thường xuất hiện cùng nhau</h2><p>Cặp kỹ năng trong cùng một tin</p></div>
          <span className="muted-badge">{number.format(data.pairs.length)} cặp</span>
        </div>
        <div className="pair-list">
          {data.pairs.length === 0 && <p className="muted">Chưa đủ dữ liệu để tính cặp kỹ năng.</p>}
          {data.pairs.slice(0, 20).map((item) => <div className="pair-row" key={`${item.skill_a}-${item.skill_b}`}>
            <div>
              <span>{item.skill_a_name}</span><i aria-hidden>+</i><span>{item.skill_b_name}</span>
            </div>
            <p><strong>{number.format(item.job_count)}</strong> <small>tin cùng nhắc đến</small></p>
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
    <p className="section-kicker">{number.format(data.total)} doanh nghiệp theo tên đã chuẩn hóa; mỗi doanh nghiệp có thể có nhiều tin.</p>
    {data.items.length === 0 && <div className="empty-card">Không có doanh nghiệp phù hợp bộ lọc.</div>}
    <div className="company-grid">
      {data.items.map((item) => <button type="button" className="company-card" key={item.company_key} onClick={() => onSelect(item.company_key)}>
        <span className="initials" aria-hidden>{initials(item.company_name)}</span>
        <h2>{item.company_name}</h2>
        <div className="company-stat">
          <div><strong>{number.format(item.job_count)}</strong><span>tin tuyển dụng</span></div>
          <div><strong>{number.format(item.city_count)}</strong><span>địa điểm</span></div>
          <div><strong>{number.format(item.domain_count)}</strong><span>chuyên môn</span></div>
          <div><strong>{number.format(item.salary_samples)}</strong><span>mẫu lương</span></div>
        </div>
        <span className="button small">Xem tin của công ty <ArrowRight size={13} aria-hidden /></span>
      </button>)}
    </div>
    <div className="pagination">
      <span>Trang {data.page} / {totalPages}</span>
      <div>
        <button type="button" className="button small" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>← Trước</button>
        <button type="button" className="button small" disabled={data.page >= totalPages} onClick={() => onPage(data.page + 1)}>Sau →</button>
      </div>
    </div>
  </>
}

function Quality({ data }: { data: QualityResponse }) {
  const passed = data.checks.filter((check) => check.passed).length
  const allPassed = data.checks.length > 0 && passed === data.checks.length
  return <>
    <div className={allPassed ? 'quality-summary' : 'quality-summary warn'}>
      <CheckCircle2 size={26} aria-hidden />
      <div>
        <strong>Đạt quy tắc kiểm tra: {passed}/{data.checks.length}</strong>
        <p>Quy tắc kiểm tra chất lượng dữ liệu · cập nhật ngày {formatDay(data.meta.snapshot_date)}</p>
      </div>
    </div>
    <section className="panel">
      <div className="panel-heading"><div><h2>Mức độ đầy đủ dữ liệu</h2><p>Đạt quy tắc kiểm tra không có nghĩa mọi trường đều đầy đủ</p></div></div>
      <div className="completeness-grid">
        <div className="completeness-item">
          <strong>{number.format(data.completeness.with_salary)}</strong>
          <span>tin có lương hợp lệ</span>
          <small>{data.completeness.with_salary_pct}% tổng số tin</small>
        </div>
        <div className="completeness-item">
          <strong>{number.format(data.completeness.with_city)}</strong>
          <span>tin có địa điểm</span>
          <small>{data.completeness.with_city_pct}% tổng số tin</small>
        </div>
        <div className="completeness-item">
          <strong>{number.format(data.completeness.with_level)}</strong>
          <span>tin có cấp bậc</span>
          <small>{data.completeness.with_level_pct}% tổng số tin</small>
        </div>
        <div className="completeness-item">
          <strong>{number.format(data.completeness.with_domain)}</strong>
          <span>tin có chuyên môn</span>
          <small>{data.completeness.with_domain_pct}% tổng số tin</small>
        </div>
      </div>
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Kết quả kiểm tra chất lượng</h2><p>Quy tắc, mức độ và giá trị thực tế</p></div></div>
      <div className="table-wrap">
        <div className="table-head check-columns"><span>Kết quả</span><span>Quy tắc</span><span>Mức độ</span><span className="num">Giá trị</span></div>
        {data.checks.length === 0 && <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có bản ghi kiểm tra chất lượng cho release này.</p>}
        {data.checks.map((check) => <div className="check-row check-columns" key={check.check_code}>
          <span className={check.passed ? 'check-ok' : 'check-fail'}>{check.passed ? 'Đạt' : 'Chưa đạt'}</span>
          <span><b>{check.check_code}</b><small>{check.check_name}</small></span>
          <span>{check.severity}</span>
          <span className="num">{check.actual}</span>
        </div>)}
      </div>
    </section>
    <section className="panel">
      <div className="panel-heading"><div><h2>Lịch sử độ phủ dữ liệu</h2><p>Các ngày đã ghi nhận trong bảng kiểm soát</p></div><span className="muted-badge">Tối đa 30 ngày</span></div>
      <div className="table-wrap">
        <div className="table-head coverage-columns"><span>Ngày cập nhật</span><span>Trạng thái</span><span className="num">Bản công bố</span></div>
        {data.coverage.length === 0 && <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có lịch sử coverage.</p>}
        {data.coverage.map((row) => <div className="check-row coverage-columns" key={row.snapshot_date}>
          <span>{row.snapshot_date}</span>
          <span className={row.sla_breached ? 'check-fail' : 'check-ok'}>{row.status}</span>
          <span className="num mono">{row.gold_release_id ?? '—'}</span>
        </div>)}
      </div>
    </section>
  </>
}

function Assistant({ status }: { status: AssistantStatus | null }) {
  const [messages, setMessages] = useState<{ role: 'user' | 'assistant'; content: string; sql?: string | null }[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!busy) {
      setElapsed(0)
      return
    }
    const startedAt = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [busy])

  useEffect(() => {
    const list = listRef.current
    // scrollTo is missing in some webviews (and in jsdom), so guard it instead of
    // letting a cosmetic detail break the chat render.
    if (list && typeof list.scrollTo === 'function') {
      list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' })
    }
  }, [messages.length, busy])

  async function send(question: string) {
    const trimmed = question.trim()
    if (!trimmed) {
      setError('Hãy nhập câu hỏi trước khi gửi.')
      return
    }
    if (busy) return
    const history = messages.slice(-6).map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, { role: 'user', content: trimmed }])
    setInput('')
    setError('')
    setBusy(true)
    // A comparison question can take a minute; abort clearly instead of spinning forever.
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 180_000)
    try {
      const payload = await api.assistantAsk(trimmed, history, controller.signal)
      const answer = (payload.answer || '').trim()
      setMessages((prev) => [...prev, {
        role: 'assistant',
        content: answer || 'Mình chưa tạo được câu trả lời cho câu hỏi này. Bạn thử hỏi cụ thể hơn (ví dụ: "lương trung vị của nhóm Dữ liệu & AI").',
        sql: payload.sql,
      }])
    } catch (caught) {
      const aborted = caught instanceof DOMException && caught.name === 'AbortError'
      const message = aborted
        ? 'Trợ lý phản hồi quá lâu (quá 3 phút). Bạn thử hỏi ngắn hơn hoặc hỏi lại.'
        : caught instanceof Error ? caught.message : 'Không gọi được trợ lý.'
      setError(message)
      setMessages((prev) => [...prev, { role: 'assistant', content: message }])
    } finally {
      window.clearTimeout(timeout)
      setBusy(false)
    }
  }

  const ready = Boolean(status?.enabled)

  return <div className="assistant-layout">
    <section className="assistant-main">
      <div className="assistant-head">
        <div className="ai-symbol"><Bot size={26} aria-hidden /></div>
        <div>
          <span className="eyebrow">Trợ lý dữ liệu</span>
          <h2>Hỏi thẳng vào dữ liệu Gold</h2>
        </div>
        <span className={ready ? 'status-pill ok' : 'status-pill'}>
          {ready ? 'Đang hoạt động' : 'Chưa cấu hình mô hình'}
        </span>
      </div>
      <p className="assistant-sub">{status?.message ?? 'Đang kiểm tra trạng thái trợ lý…'}</p>

      <div className="chat-log" ref={listRef} role="log" aria-live="polite">
        {messages.length === 0 && <div className="chat-empty">
          <Sparkles size={16} aria-hidden />
          <p>Đặt câu hỏi về lương, kỹ năng, địa điểm hoặc doanh nghiệp. Trợ lý tự sinh SQL, chạy trên Gold ở chế độ chỉ đọc rồi diễn giải kết quả.</p>
        </div>}
        {messages.map((message, index) => <div className={`chat-bubble ${message.role}`} key={`${message.role}-${index}`}>
          <p>{message.content}</p>
          {message.sql && <details className="chat-sql">
            <summary>SQL đã chạy</summary>
            <pre>{message.sql}</pre>
          </details>}
        </div>)}
        {busy && <div className="chat-bubble assistant"><p className="muted"><RefreshCw className="spin" size={14} aria-hidden /> Đang truy vấn Gold… {elapsed > 0 && `${elapsed}s`}</p></div>}
      </div>

      {error && <p className="chat-error" role="alert"><AlertCircle size={14} aria-hidden /> {error}</p>}

      <form className="chat-input" onSubmit={(event) => { event.preventDefault(); void send(input) }}>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={ready ? 'Ví dụ: lương trung vị của kỹ sư dữ liệu là bao nhiêu?' : 'Cần cấu hình API key cho mô hình trước'}
          aria-label="Câu hỏi cho trợ lý AI"
          disabled={!ready || busy}
          maxLength={500}
        />
        <button type="submit" disabled={!ready || busy || !input.trim()}>Gửi</button>
      </form>

      <div className="prompt-examples">
        {(status?.examples ?? []).map((example) => <button
          type="button"
          key={example}
          className="example-chip"
          disabled={!ready || busy}
          onClick={() => void send(example)}
        ><Sparkles size={13} aria-hidden />{example}</button>)}
      </div>
    </section>
    <aside className="assistant-aside">
      <ShieldCheck size={22} className="panel-icon" aria-hidden />
      <h3>Câu trả lời gắn với dữ liệu</h3>
      <p>Trợ lý chỉ đọc tầng Gold qua một tool duy nhất, có kiểm soát:</p>
      <ul>
        <li><CheckCircle2 size={14} aria-hidden /> Chỉ cho phép một câu SELECT, giới hạn số dòng</li>
        <li><CheckCircle2 size={14} aria-hidden /> Chặn ghi/xoá và mọi schema ngoài Gold</li>
        <li><CheckCircle2 size={14} aria-hidden /> Câu trả lời kèm ngày cập nhật và phạm vi</li>
        <li><CheckCircle2 size={14} aria-hidden /> Hiển thị lại SQL để kiểm chứng</li>
      </ul>
      <p className="aside-note">Số liệu vẫn khám phá được trực tiếp qua Tổng quan, Tin tuyển dụng, Kỹ năng và Doanh nghiệp.</p>
    </aside>
  </div>
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
        <X size={18} aria-hidden />
      </button>
      {error && <p className="muted">{error}</p>}
      {!job && !error && <p className="muted"><RefreshCw className="spin" size={16} aria-hidden /> Đang tải chi tiết…</p>}
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
          <div><small>cập nhật gần nhất</small><b>{formatDate(job.last_seen_at)}</b></div>
          <div><small>Cập nhật lúc</small><b>{formatDate(job.snapshot_cutoff_at)}</b></div>
          <div><small>Trạng thái entity</small><b>{job.entity_status}</b></div>
        </div>
        <h3>Kỹ năng</h3>
        <div className="skill-tags large">
          {job.skills.length ? job.skills.map((skill) => <span key={skill.skill_code}>{skill.skill_name}</span>) : <span>Chưa xác định</span>}
        </div>
        <h3>Nguồn đăng tuyển</h3>
        {job.source_urls.length > 0
          ? <ul className="source-list">
            {job.source_urls.map((link) => <li key={link.url}>
              <a href={link.url} target="_blank" rel="noopener noreferrer">
                <span className="source-tag">{link.platform}</span>
                <span className="source-host">{safeHost(link.url)}</span>
                <ExternalLink size={13} aria-hidden />
              </a>
            </li>)}
          </ul>
          : <p className="muted">Chưa có liên kết nguồn cho tin này.</p>}
        <p className="modal-note">Đây là tin hợp nhất được hệ thống quan sát; không phải khẳng định listing vẫn còn trực tuyến trên nguồn.</p>
      </>}
    </div>
  </div>
}
