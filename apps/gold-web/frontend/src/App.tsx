import { useEffect, useMemo, useRef, useState } from 'react'
import { prettyCompany, prettyText } from './format'
import {
  AlertCircle, ArrowRight, Bot, Building2, BriefcaseBusiness, CheckCircle2, Coins,
  ChevronDown, Download, ExternalLink, Info, LayoutDashboard, Lock, RefreshCw, Search, ShieldCheck,
  Sparkles, Tags, TrendingUp, X,
} from 'lucide-react'
import {
  api, ApiError, AssistantAnswer, AssistantFilters, AssistantStatus, CompaniesResponse,
  DashboardResponse, Filters, Job, JobsResponse, Meta, Option, QualityResponse, SkillsResponse,
  CompanyProfile, TrendsResponse,
} from './api'
import { Markdown } from './Markdown'

type Page = 'overview' | 'trends' | 'jobs' | 'skills' | 'companies' | 'quality' | 'assistant'

const PAGES: Page[] = ['overview', 'trends', 'jobs', 'skills', 'companies', 'quality', 'assistant']
const PAGE_LABELS: Record<Page, string> = {
  overview: 'Tổng quan',
  trends: 'Xu hướng',
  jobs: 'Tin tuyển dụng',
  skills: 'Kỹ năng',
  companies: 'Doanh nghiệp',
  quality: 'Trạng thái dữ liệu',
  assistant: 'Trợ lý AI',
}
const PAGE_ICONS: Record<Page, React.ReactNode> = {
  overview: <LayoutDashboard size={17} aria-hidden />,
  trends: <TrendingUp size={17} aria-hidden />,
  jobs: <BriefcaseBusiness size={17} aria-hidden />,
  skills: <Tags size={17} aria-hidden />,
  companies: <Building2 size={17} aria-hidden />,
  quality: <ShieldCheck size={17} aria-hidden />,
  assistant: <Bot size={17} aria-hidden />,
}
const PAGE_SUBTITLES: Record<Page, string> = {
  overview: 'Bức tranh tổng quan từ release Gold đang được công bố.',
  trends: 'Nhịp đăng tin theo tuần và lịch sử các lần cập nhật dữ liệu.',
  jobs: 'Tìm kiếm và khám phá những tin mà hệ thống đã ghi nhận.',
  skills: 'Kỹ năng nào xuất hiện trong tin tuyển dụng và thường đi cùng nhau.',
  companies: 'Cơ cấu tin tuyển dụng của từng doanh nghiệp trong dữ liệu đang công bố.',
  quality: 'Các kiểm tra chất lượng và độ phủ của release đang phục vụ.',
  assistant: 'Đặt câu hỏi về lương, kỹ năng, địa điểm hoặc doanh nghiệp.',
}
const initialFilters: Filters = { page: 1, page_size: 12, sort: 'recent' }
function selectedSkills(filters: Filters): string[] {
  const raw = filters.skills ?? filters.skill ?? ''
  return raw.split(',').map((code) => code.trim()).filter(Boolean)
}

const money = new Intl.NumberFormat('vi-VN')
const number = new Intl.NumberFormat('vi-VN')

function readPageFromUrl(): Page {
  const value = new URLSearchParams(window.location.search).get('page') as Page | null
  return value && PAGES.includes(value) ? value : 'overview'
}
function formatMoney(value: number | null) {
  return value == null ? 'Chưa có dữ liệu lương' : `${money.format(value)} đ`
}
/** Compact salary for answer cards: same unit the assistant uses in prose. */
function formatSalary(value: number | null) {
  if (value == null) return 'Chưa có lương'
  if (value >= 1_000_000) {
    const millions = Math.round((value / 1_000_000) * 10) / 10
    return `${millions.toLocaleString('vi-VN')} triệu VND/tháng`
  }
  return `${money.format(value)} đ`
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
const CHECK_LABELS: Record<string, string> = {
  'Grain PK unique on all facts': 'Khóa chính của mọi bảng fact là duy nhất',
  'Facts reference an existing dim_job version': 'Mọi fact đều tham chiếu một phiên bản dim_job tồn tại',
  'Every company_key exists in dim_company': 'Mọi mã công ty đều có trong danh mục công ty',
  'Every skill_key exists in dim_skill': 'Mọi mã kỹ năng đều có trong danh mục kỹ năng',
  'Every platform_key exists in dim_platform': 'Mọi mã nền tảng đều có trong danh mục nền tảng',
  'Every date_key exists in dim_date': 'Mọi khóa ngày đều có trong danh mục ngày',
  'Snapshot rows equal effective entities': 'Số dòng snapshot bằng số thực thể hiệu lực',
  'salary_min <= salary_max': 'Lương tối thiểu không vượt lương tối đa',
  'dim_job version matches the snapshot day': 'Phiên bản dim_job khớp ngày snapshot',
  'No observation newer than cutoff': 'Không có quan sát nào mới hơn thời điểm cắt',
  'No REVIEW_REQUIRED/UNMATCHED link carries a job': 'Link cần duyệt hoặc chưa khớp không mang tin',
  'Exactly one current version per job': 'Mỗi tin chỉ có đúng một phiên bản hiện hành',
  'Market groups sum to snapshot cohort': 'Nhóm thị trường cộng lại bằng tổng cohort',
  'salary_sample_count <= observed_job_count': 'Số mẫu lương không vượt số tin quan sát',
  'Company groups sum to snapshot cohort': 'Nhóm công ty cộng lại bằng tổng cohort',
  'Skill pairs ordered, no self pair': 'Cặp kỹ năng có thứ tự, không tự ghép chính nó',
  'co_mentioned <= jobs having each skill': 'Số đồng xuất hiện không vượt số tin có kỹ năng',
  'Change event PK unique': 'Khóa chính của biến cố thay đổi là duy nhất',
  'Baseline observation is not a change': 'Quan sát gốc không bị tính là biến cố thay đổi',
  'Every row carries gold and silver release ids': 'Mỗi dòng đều mang mã release gold và silver',
  'job_domain null rate within threshold': 'Tỷ lệ thiếu chuyên môn trong ngưỡng cho phép',
  'Unmapped skill codes reported': 'Mã kỹ năng chưa ánh xạ được báo cáo đầy đủ',
  'representative_fallback rate': 'Tỷ lệ dùng dữ liệu đại diện trong ngưỡng cho phép',
  'Job-skill-day grain not inflated by recrawl': 'Dữ liệu không bị nhân bản khi cào lại',
  'SUM(open_job_count) <= SUM(job_count)': 'Số tin đang mở không vượt tổng số tin',
  'Non-open statuses never flagged open': 'Trạng thái không mở không bị đánh dấu đang mở',
  'open_job_count source is entity_status': 'Trạng thái đang mở lấy từ entity_status',
  'open_job_count matches the D-G05 status set': 'Trạng thái đang mở khớp bộ trạng thái D-G05',
  'Parquet entities and SQLite registry agree': 'Thực thể Parquet và registry SQLite khớp nhau',
  'Description drift within 1% of entities': 'Mô tả công việc lệch không quá 1% số thực thể',
  'job_domain UNKNOWN share': 'Tỷ lệ chưa xác định chuyên môn trong ngưỡng cho phép',
  'City axis limited to five explicit groups': 'Trục địa điểm chỉ gồm năm nhóm xác định',
  'No duplicate effective entity': 'Không có thực thể hiệu lực bị trùng',
}

function checkLabel(name: string) {
  // Check names are English identifiers from the DQ registry; the page is Vietnamese.
  const label = CHECK_LABELS[name] ?? name
  return label.replace(/\bUNKNOWN\b/gi, 'chưa xác định').replace(/\bnull\b/gi, 'chưa có dữ liệu')
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
  trends?: TrendsResponse
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
        } else if (page === 'trends') {
          const trends = await api.trends(params, controller.signal)
          setSnapshot({ meta: trends.meta, trends })
          if (!releaseId) setReleaseId(trends.meta.gold_release_id)
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
    setFilters({ ...initialFilters, skills: code })
    setPage('jobs')
  }
  const drillToCompany = (key: string) => {
    setFilters({ ...initialFilters, company_key: key })
    setPage('jobs')
  }

  const activeChips = useMemo(() => {
    const entries: [keyof Filters, string][] = []
    // Show the reader's label ("Python"), not the stored code ("python").
    const labelFor = (list: Option[] | undefined, value: string | undefined) =>
      (value ? list?.find((item) => item.value === value)?.label ?? value : '')
    if (filters.q) entries.push(['q', `“${filters.q}”`])
    if (filters.city) entries.push(['city', labelFor(options?.cities, filters.city)])
    if (filters.domain) entries.push(['domain', labelFor(options?.domains, filters.domain)])
    if (filters.level) entries.push(['level', labelFor(options?.levels, filters.level)])
    for (const code of selectedSkills(filters)) {
      entries.push([`skills:${code}` as keyof Filters, labelFor(options?.skills, code)])
    }
    if (filters.company_key) entries.push(['company_key', 'Công ty đã chọn'])
    return entries
  }, [filters, options])

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
          clearOne={(key) => {
            if (String(key).startsWith('skills:')) {
              const drop = String(key).slice('skills:'.length)
              const left = selectedSkills(filters).filter((code) => code !== drop)
              setFilters((current) => ({ ...current, skills: left.join(',') || undefined, skill: undefined, page: 1 }))
              return
            }
            updateFilter(key, '')
          }}
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
          {page === 'trends' && snapshot.trends && <Trends data={snapshot.trends} />}
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
          {page === 'assistant' && <Assistant
            status={assistant}
            onOpenJob={(jobId) => setSelectedJobId(jobId)}
            onSearchJobs={(next) => {
              // The answer names a skill ("Python"); the jobs API filters by code ("python").
              const mapped: Filters = { ...next }
              const skill = mapped.skill?.trim()
              if (skill) {
                const match = options?.skills.find((item) =>
                  item.value.toLowerCase() === skill.toLowerCase()
                  || item.label.toLowerCase() === skill.toLowerCase())
                mapped.skill = match ? match.value : skill.toLowerCase()
              }
              const domain = mapped.domain?.trim()
              if (domain) {
                const match = options?.domains.find((item) =>
                  item.value.toLowerCase() === domain.toLowerCase()
                  || item.label.toLowerCase() === domain.toLowerCase())
                if (match) mapped.domain = match.value
              }
              setFilters(mapped)
              setPage('jobs')
            }}
          />}
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
  const chosen = selectedSkills(filters)
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
        {filters.city && !(options?.cities ?? []).some((item) => item.value === filters.city) && <option value={filters.city}>{bucketLabel(filters.city)}</option>}
        {(options?.cities ?? []).map((item) => <option key={item.value} value={item.value}>{bucketLabel(item.label)} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.domain ?? ''} onChange={(e) => updateFilter('domain', e.target.value)} aria-label="Lọc theo chuyên môn">
        <option value="">Mọi chuyên môn</option>
        {filters.domain && !(options?.domains ?? []).some((item) => item.value === filters.domain) && <option value={filters.domain}>{bucketLabel(filters.domain)}</option>}
        {(options?.domains ?? []).map((item) => <option key={item.value} value={item.value}>{bucketLabel(item.label)} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.level ?? ''} onChange={(e) => updateFilter('level', e.target.value)} aria-label="Lọc theo cấp bậc">
        <option value="">Mọi cấp bậc</option>
        {filters.level && !(options?.levels ?? []).some((item) => item.value === filters.level) && <option value={filters.level}>{bucketLabel(filters.level)}</option>}
        {(options?.levels ?? []).map((item) => <option key={item.value} value={item.value}>{bucketLabel(item.label)} ({number.format(item.count)})</option>)}
      </select>
      <select value={filters.scope ?? 'all'} onChange={(e) => updateFilter('scope', e.target.value === 'all' ? '' : e.target.value)} aria-label="Phạm vi cập nhật">
        <option value="all">Mọi thời điểm</option>
        <option value="recent">Cập nhật 14 ngày</option>
      </select>
      <details className="multi-select">
        <summary aria-label="Lọc theo kỹ năng">
          <span>{chosen.length === 0 ? 'Mọi kỹ năng' : `${chosen.length} kỹ năng`}{chosen.length === 0 ? '' : ` · ${chosen.map((code) => options?.skills.find((item) => item.value === code)?.label ?? code).join(', ')}`}</span>
          <ChevronDown size={14} aria-hidden />
        </summary>
        <div className="multi-select-panel" role="group" aria-label="Chọn kỹ năng">
          {chosen.length > 0 && (
            <button type="button" className="button small clear-skills" onClick={() => updateFilter('skills', '')}>Bỏ chọn tất cả</button>
          )}
          {(options?.skills ?? []).map((item) => {
            const picked = chosen.includes(item.value)
            return <label key={item.value} className={picked ? 'picked' : ''}>
              <input
                type="checkbox"
                checked={picked}
                onChange={() => {
                  const next = picked
                    ? chosen.filter((code) => code !== item.value)
                    : [...chosen, item.value]
                  updateFilter('skills', next.join(','))
                }}
              />
              <span>{item.label}</span>
              <em>{number.format(item.count)}</em>
            </label>
          })}
        </div>
      </details>
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
      {data.summary.recent_jobs < data.summary.jobs && (
        <p className="panel-note" style={{ gridColumn: '1 / -1' }}>
          {number.format(data.summary.recent_jobs)} tin được ghi nhận trong 14 ngày gần nhất;
          {' '}{number.format(data.summary.jobs - data.summary.recent_jobs)} tin cũ hơn (lần ghi nhận sớm nhất {formatDay(data.summary.oldest_last_seen)}) vẫn nằm trong tập dữ liệu.
        </p>
      )}
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
      <Distribution title="nền tảng" hint="Số tin theo từng nguồn dữ liệu" items={data.platforms}
        note={data.summary.no_platform > 0
          ? `${number.format(data.summary.no_platform)} tin chưa có bằng chứng nền tảng trong 14 ngày nên không nằm trong biểu đồ này.`
          : undefined} />
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

function Distribution({ title, hint, items, note }: { title: string; hint: string; items: { value: string; label?: string; count: number }[]; note?: string }) {
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
    {note && <p className="panel-note">{note}</p>}
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
            <span className="job-domain">{prettyText(bucketLabel(display(job.job_domain)))}</span>
            {job.days_since_last_seen != null && job.days_since_last_seen > 14 && (
              <span className="source-tag" title="Lần ghi nhận cuối đã quá 14 ngày">cũ {job.days_since_last_seen} ngày</span>
            )}
            <h2>{prettyText(job.canonical_title)}</h2>
            <p className="company-name">{prettyCompany(job.company_display_name)}</p>
            <div className="job-meta">
              <span><em>Địa điểm</em>{prettyText(bucketLabel(display(job.city_bucket)))}</span>
              <span><em>Cấp bậc</em>{bucketLabel(display(job.job_level))}</span>
              <span><em>Hình thức</em>{bucketLabel(display(job.work_mode))}</span>
            </div>
            <div className="skill-tags">
              {job.skills.length === 0 && <span className="muted">Chưa ghi nhận kỹ năng</span>}
              {job.skills.slice(0, 6).map((skill) => <span key={skill.skill_code}>{prettyText(skill.skill_name)}</span>)}
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
            <span className="rank-name">{prettyText(item.skill_name)}</span>
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
  const [profile, setProfile] = useState<CompanyProfile | null>(null)
  const [profileError, setProfileError] = useState('')
  const [profileBusy, setProfileBusy] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)

  const showProfile = async (key: string) => {
    if (openKey === key) {
      setOpenKey(null)
      setProfile(null)
      return
    }
    setOpenKey(key)
    setProfile(null)
    setProfileError('')
    setProfileBusy(true)
    try {
      const payload = await api.companyProfile(key, { expected_release_id: data.meta.gold_release_id })
      setProfile(payload.profile)
    } catch (caught) {
      setProfileError(caught instanceof Error ? caught.message : 'Không tải được cấu trúc doanh nghiệp.')
    } finally {
      setProfileBusy(false)
    }
  }

  return <>
    <ReleaseNote meta={data.meta} />
    {openKey && <section className="panel company-profile">
      <div className="panel-heading">
        <div><h2>{profile ? prettyCompany(profile.company_name) : 'Cấu trúc doanh nghiệp'}</h2>
          <p>{profile ? `${number.format(profile.jobs)} tin · ${number.format(profile.salary_samples)} mẫu lương${profile.salary_median ? ` · trung vị ${formatMoney(profile.salary_median)}` : ''}` : 'Đang tải…'}</p></div>
        <button type="button" className="button small" onClick={() => { setOpenKey(null); setProfile(null) }}>Đóng</button>
      </div>
      {profileBusy && <p className="muted" style={{ padding: '0 21px 18px' }}><RefreshCw className="spin" size={14} aria-hidden /> Đang tải cấu trúc…</p>}
      {profileError && <p className="muted" style={{ padding: '0 21px 18px' }}>{profileError}</p>}
      {profile && <div className="profile-grid">
        <div><h3>Chuyên môn</h3>{profile.domains.length
          ? profile.domains.map((item) => <div className="bar-row" key={item.value}>
            <div className="bar-label"><span>{bucketLabel(item.value)}</span><b>{number.format(item.count)}</b></div>
            <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / Math.max(1, ...profile.domains.map((d) => d.count))) * 100)}%` }} /></div>
          </div>)
          : <p className="muted">Chưa có dữ liệu.</p>}</div>
        <div><h3>Địa điểm</h3>{profile.cities.length
          ? profile.cities.map((item) => <div className="bar-row" key={item.value}>
            <div className="bar-label"><span>{bucketLabel(item.value)}</span><b>{number.format(item.count)}</b></div>
            <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / Math.max(1, ...profile.cities.map((d) => d.count))) * 100)}%` }} /></div>
          </div>)
          : <p className="muted">Chưa có dữ liệu.</p>}</div>
        <div><h3>Cấp bậc</h3>{profile.levels.length
          ? profile.levels.map((item) => <div className="bar-row" key={item.value}>
            <div className="bar-label"><span>{bucketLabel(item.value)}</span><b>{number.format(item.count)}</b></div>
            <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / Math.max(1, ...profile.levels.map((d) => d.count))) * 100)}%` }} /></div>
          </div>)
          : <p className="muted">Chưa có dữ liệu.</p>}</div>
        <div><h3>Kỹ năng</h3>{profile.skills.length
          ? profile.skills.map((item) => <div className="bar-row" key={item.value}>
            <div className="bar-label"><span>{item.value}</span><b>{number.format(item.count)}</b></div>
            <div className="bar-track"><span style={{ width: `${Math.max(3, (item.count / Math.max(1, ...profile.skills.map((d) => d.count))) * 100)}%` }} /></div>
          </div>)
          : <p className="muted">Chưa ghi nhận kỹ năng.</p>}</div>
      </div>}
    </section>}
    <p className="section-kicker">{number.format(data.total)} doanh nghiệp theo tên đã chuẩn hóa; mỗi doanh nghiệp có thể có nhiều tin.</p>
    {data.items.length === 0 && <div className="empty-card">Không có doanh nghiệp phù hợp bộ lọc.</div>}
    <div className="company-grid">
      {data.items.map((item) => <button type="button" className="company-card" key={item.company_key} onClick={() => onSelect(item.company_key)}>
        <span className="initials" aria-hidden>{initials(item.company_name)}</span>
        <h2>{prettyCompany(item.company_name)}</h2>
        <div className="company-stat">
          <div><strong>{number.format(item.job_count)}</strong><span>tin tuyển dụng</span></div>
          <div><strong>{number.format(item.city_count)}</strong><span>địa điểm</span></div>
          <div><strong>{number.format(item.domain_count)}</strong><span>chuyên môn</span></div>
          <div><strong>{number.format(item.salary_samples)}</strong><span>mẫu lương</span></div>
        </div>
        <span className="company-actions">
          <span className="button small">Xem tin của công ty <ArrowRight size={13} aria-hidden /></span>
          <span
            role="button"
            tabIndex={0}
            className="button small ghost"
            onClick={(event) => { event.stopPropagation(); void showProfile(item.company_key) }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); void showProfile(item.company_key) }
            }}
          >{openKey === item.company_key ? 'Ẩn cấu trúc' : 'Cấu trúc'}</span>
        </span>
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

function Trends({ data }: { data: TrendsResponse }) {
  const weeks = data.weeks
  // The first crawl week carries every job ever seen, so plotting all 26 weeks flattens the
  // recent bars into the baseline. A quarter keeps the shape readable; KPIs still use everything.
  const chartWeeks = weeks.slice(-13)
  // One bulk-load week (the first crawl) can dwarf every real week and flatten the rest into
  // the baseline. Cap the scale at the second-largest value and mark the clipped bar, so the
  // chart stays readable without hiding the outlier.
  const sorted = [...chartWeeks].map((w) => w.jobs).sort((a, b) => b - a)
  const outlier = sorted.length > 1 && sorted[0] > sorted[1] * 4 ? sorted[0] : null
  const max = Math.max(1, outlier ? sorted[1] : sorted[0])
  const lastFour = weeks.slice(-4)
  const avg = lastFour.length ? Math.round(lastFour.reduce((sum, w) => sum + w.jobs, 0) / lastFour.length) : 0
  const latest = weeks[weeks.length - 1]
  const withSalary = weeks.filter((w) => w.salary_samples > 0)
  return <>
    <ReleaseNote meta={data.meta} />
    <div className="metric-grid">
      <Metric icon={<TrendingUp size={18} aria-hidden />} tone="green" label="Tuần gần nhất"
        value={latest ? number.format(latest.jobs) : '—'} note={latest ? `tuần bắt đầu ${formatDay(latest.week_start)}` : 'chưa có dữ liệu'} />
      <Metric icon={<BriefcaseBusiness size={18} aria-hidden />} tone="blue" label="Trung bình 4 tuần"
        value={number.format(avg)} note={`trên ${lastFour.length} tuần gần nhất`} />
      <Metric icon={<Coins size={18} aria-hidden />} tone="amber" label="Tuần nhiều tin nhất"
        value={number.format(Math.max(...weeks.map((w) => w.jobs), 0))}
        note={weeks.length ? formatDay(weeks.reduce((best, w) => (w.jobs > best.jobs ? w : best), weeks[0]).week_start) : '—'} />
      <Metric icon={<Tags size={18} aria-hidden />} tone="violet" label="Tuần có dữ liệu lương"
        value={`${withSalary.length}/${weeks.length}`} note="tuần có ít nhất một mẫu lương" />
    </div>

    <section className="panel">
      <div className="panel-heading">
        <div><h2>Số tin theo tuần đăng</h2><p>Dựa trên ngày đăng của tin đã hợp nhất; tuần có ít tin có thể là tuần ít nguồn được cào</p></div>
        <span className="muted-badge">{chartWeeks.length} tuần gần nhất</span>
      </div>
      {chartWeeks.length === 0
        ? <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có ngày đăng tin để dựng xu hướng.</p>
        : <div className="trend-chart" role="img"
            aria-label={chartWeeks.map((w) => `${formatDay(w.week_start)}: ${w.jobs} tin`).join(', ')}>
          {chartWeeks.map((week) => {
            const clipped = outlier != null && week.jobs === outlier
            const ratio = Math.min(week.jobs, max) / max
            return <div className="trend-column" key={week.week_start}>
              <b className={clipped ? 'clipped' : undefined}>{number.format(week.jobs)}{clipped ? '▴' : ''}</b>
              <div className="trend-plot">
                <div className={`trend-bar${clipped ? ' clipped' : ''}`}
                  style={{ height: `${Math.max(3, ratio * 100)}%` }}
                  title={`${formatDay(week.week_start)}: ${number.format(week.jobs)} tin`} />
              </div>
              <span>{formatDay(week.week_start).slice(0, 5)}</span>
            </div>
          })}
        </div>}
      {outlier != null && <p className="panel-note">Cột đánh dấu ▴ là tuần nạp dữ liệu đầu tiên ({number.format(outlier)} tin) nên bị cắt bớt chiều cao để các tuần còn lại đọc được; số liệu vẫn là số thật.</p>}
    </section>

    <section className="panel">
      <div className="panel-heading"><div><h2>Lịch sử cập nhật dữ liệu</h2><p>Mỗi mốc là một release Gold đã công bố và số tin trong cohort lúc đó</p></div></div>
      <div className="table-wrap">
        <div className="table-head coverage-columns"><span>Ngày dữ liệu</span><span>Số tin</span><span className="num">Thay đổi</span></div>
        {data.snapshots.length === 0 && <p className="muted" style={{ padding: '0 21px 18px' }}>Chưa có lịch sử release.</p>}
        {data.snapshots.map((row, index) => {
          const previous = index > 0 ? data.snapshots[index - 1].jobs : null
          const delta = previous == null ? null : row.jobs - previous
          return <div className="check-row coverage-columns" key={row.snapshot_date}>
            <span>{formatDay(row.snapshot_date)}</span>
            <span>{number.format(row.jobs)}</span>
            <span className={`num ${delta == null ? '' : delta >= 0 ? 'check-ok' : 'check-fail'}`}>
              {delta == null ? '—' : `${delta >= 0 ? '+' : ''}${number.format(delta)}`}
            </span>
          </div>
        })}
      </div>
      <p className="panel-note">Số tin của một ngày là release công bố sau cùng trong ngày đó. Chênh lệch âm nghĩa là cohort thu hẹp sau khi sửa dữ liệu, không phải mất nguồn.</p>
    </section>
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
          <span><b>{check.check_code}</b><small>{checkLabel(check.check_name)}</small></span>
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

const FILTER_LABELS: Record<string, string> = {
  city: 'Địa điểm',
  title_keyword: 'Từ khoá trong tên tin',
  job_domain: 'Chuyên môn',
  job_levels: 'Cấp bậc',
  skill: 'Kỹ năng',
  company: 'Doanh nghiệp',
  salary_only: 'Tin có lương',
  recent_days: 'Ghi nhận trong',
}
const FILTER_ORDER = ['city', 'title_keyword', 'job_domain', 'job_levels', 'skill', 'company', 'salary_only', 'recent_days']

function filterEntries(filters: AssistantFilters): [string, string][] {
  const entries: [string, string][] = []
  for (const key of FILTER_ORDER) {
    const value = (filters as Record<string, unknown>)[key]
    if (value === undefined || value === null || value === '') continue
    if (key === 'salary_only') {
      if (value === true) entries.push([key, 'Chỉ tin có lương'])
      continue
    }
    if (key === 'recent_days') {
      entries.push([key, `${value} ngày gần nhất`])
      continue
    }
    if (Array.isArray(value)) {
      if (value.length) entries.push([key, value.join(', ')])
      continue
    }
    entries.push([key, String(value)])
  }
  return entries
}

function toJobFilters(filters: AssistantFilters): Filters {
  const mapped: Filters = { page: 1, page_size: 12, sort: 'recent' }
  if (filters.city) mapped.city = filters.city
  if (filters.title_keyword) mapped.q = filters.title_keyword
  if (filters.job_domain) mapped.domain = filters.job_domain
  if (filters.skill) mapped.skill = filters.skill
  // The jobs endpoint filters one level at a time; carrying only the first level of a
  // comparison would silently drop the other group, so the list opens for all levels.
  return mapped
}

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  answer?: AssistantAnswer
  question?: string
  failed?: boolean
}

function Assistant({ status, onOpenJob, onSearchJobs }: {
  status: AssistantStatus | null
  onOpenJob: (jobId: string) => void
  onSearchJobs: (filters: Filters) => void
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const abortReason = useRef<'cancel' | 'timeout' | null>(null)
  const counter = useRef(0)
  const nextId = () => `msg-${counter.current++}`

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
    const history = messages
      .filter((message) => !message.failed)
      .slice(-6)
      .map((message) => ({ role: message.role, content: message.content }))
    setMessages((prev) => [...prev, { id: nextId(), role: 'user', content: trimmed }])
    setInput('')
    setError('')
    setBusy(true)
    const controller = new AbortController()
    controllerRef.current = controller
    abortReason.current = null
    // The server has its own deadline; this guard only stops the browser waiting forever.
    const timer = window.setTimeout(() => {
      abortReason.current = 'timeout'
      controller.abort()
    }, 100_000)
    try {
      const payload = await api.assistantAsk(trimmed, history, controller.signal)
      setMessages((prev) => [...prev, {
        id: nextId(),
        role: 'assistant',
        content: payload.answer_markdown,
        answer: payload,
      }])
    } catch (caught) {
      const cancelled = abortReason.current === 'cancel'
      const timedOut = abortReason.current === 'timeout'
      const message = cancelled
        ? 'Bạn đã hủy câu hỏi này.'
        : timedOut
          ? 'Câu hỏi này mất nhiều thời gian hơn dự kiến. Bạn thử lại hoặc hỏi ngắn hơn nhé.'
          : caught instanceof Error && caught.message
            ? caught.message
            : 'Chưa thể tải kết quả. Bạn thử lại sau nhé.'
      if (!cancelled) setError(message)
      setMessages((prev) => [...prev, {
        id: nextId(),
        role: 'assistant',
        content: message,
        question: trimmed,
        failed: !cancelled,
      }])
    } finally {
      window.clearTimeout(timer)
      controllerRef.current = null
      abortReason.current = null
      setBusy(false)
    }
  }

  const ready = Boolean(status?.enabled)

  return <section className="assistant-panel">
    <div className="assistant-head">
      <div className="ai-symbol"><Bot size={24} aria-hidden /></div>
      <div>
        <span className="eyebrow">Trợ lý dữ liệu</span>
        <h2>Trợ lý AI</h2>
      </div>
      <span className={ready ? 'status-pill ok' : 'status-pill'}>
        {ready ? 'Đang hoạt động' : 'Chưa sẵn sàng'}
      </span>
    </div>
    <p className="assistant-sub">{ready
      ? 'Đặt câu hỏi về lương, kỹ năng, địa điểm hoặc doanh nghiệp.'
      : status?.message ?? 'Đang kiểm tra trạng thái trợ lý…'}</p>

    <div className="chat-log" ref={listRef} role="log" aria-live="polite" aria-label="Hội thoại với trợ lý">
      {messages.length === 0 && <div className="chat-empty">
        <Sparkles size={16} aria-hidden />
        <div>
          <p className="chat-empty-title">Bắt đầu bằng một câu hỏi</p>
          <p>Ví dụ: so sánh lương giữa hai cấp bậc, đếm số tin theo địa điểm, hoặc tìm tin theo kỹ năng.</p>
        </div>
      </div>}
      {messages.map((message) => message.role === 'user'
        ? <div className="chat-bubble user" key={message.id}><p>{message.content}</p></div>
        : <div className={`chat-bubble assistant${message.failed ? ' failed' : ''}`} key={message.id}>
          {message.failed
            ? <p className="chat-failure">{message.content}</p>
            : <Markdown text={message.content} />}
          {message.answer && <AnswerResult answer={message.answer} onOpenJob={onOpenJob} onSearchJobs={onSearchJobs} />}
          {message.failed && message.question && <button
            type="button"
            className="button small chat-retry"
            onClick={() => {
              setMessages((prev) => prev.filter((item) => item.id !== message.id))
              void send(message.question as string)
            }}
          >Thử lại</button>}
        </div>)}
      {busy && <div className="chat-bubble assistant chat-pending">
        <span className="pending-label"><RefreshCw className="spin" size={14} aria-hidden /> đang truy vấn{elapsed > 0 ? ` · ${elapsed}s` : ''}</span>
        <button type="button" className="button small" onClick={() => {
          abortReason.current = 'cancel'
          controllerRef.current?.abort()
        }}>Hủy</button>
      </div>}
    </div>

    {error && !busy && <p className="chat-error" role="alert"><AlertCircle size={14} aria-hidden /> {error}</p>}

    <form className="chat-input" onSubmit={(event) => { event.preventDefault(); void send(input) }}>
      <textarea
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            void send(input)
          }
        }}
        rows={2}
        placeholder={ready ? 'Ví dụ: lương trung vị của Data Engineer là bao nhiêu?' : 'Trợ lý chưa sẵn sàng'}
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
}

function AnswerResult({ answer, onOpenJob, onSearchJobs }: {
  answer: AssistantAnswer
  onOpenJob: (jobId: string) => void
  onSearchJobs: (filters: Filters) => void
}) {
  const filters = filterEntries(answer.applied_filters)
  const jobs = answer.result.kind === 'jobs' ? answer.result.jobs : []
  const table = answer.result.kind === 'table' ? answer.result : null
  const dataDate = answer.data_context?.snapshot_date
  const canOpenList = filters.length > 0

  return <div className="answer-extras">
    {filters.length > 0 && <div className="answer-filters">
      <span className="answer-label">Bộ lọc</span>
      {filters.map(([key, label]) => <span className="answer-chip" key={key}>
        <em>{FILTER_LABELS[key] ?? key}</em>{label}
      </span>)}
    </div>}

    {jobs.length > 0 && <div className="answer-jobs">
      {jobs.map((job) => <article className="answer-job" key={job.job_id}>
        <div className="answer-job-main">
          <h3>{job.title}</h3>
          <p>{job.company ?? 'Chưa xác định doanh nghiệp'}</p>
          <div className="job-meta">
            <span><em>Địa điểm</em>{job.city ?? 'Chưa xác định'}</span>
            <span><em>Cấp bậc</em>{job.level ?? 'Chưa xác định'}</span>
          </div>
        </div>
        <div className="answer-job-side">
          <strong className={job.salary_sample_flag ? 'salary' : 'salary muted-salary'}>
            {job.salary_midpoint_vnd == null ? 'Chưa có lương' : formatSalary(job.salary_midpoint_vnd)}
          </strong>
          <small>Ghi nhận {job.last_seen_at ? formatDate(job.last_seen_at) : 'chưa xác định'}</small>
          <div className="answer-job-actions">
            <button type="button" className="button small primary-action" onClick={() => onOpenJob(job.job_id)}>Xem chi tiết</button>
            {job.source_url && /^https?:\/\//i.test(job.source_url) && <a
              className="button small"
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >Xem tin gốc</a>}
          </div>
        </div>
      </article>)}
    </div>}

    {table && <div className="md-table-wrap answer-table">
      <table className="md-table">
        <thead><tr>{table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {table.rows.map((row, rowIndex) => <tr key={rowIndex}>
            {table.columns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
          </tr>)}
        </tbody>
      </table>
      {table.truncated && <p className="answer-note">Bảng hiển thị một phần kết quả; số liệu trong câu trả lời tính trên toàn bộ phạm vi lọc.</p>}
    </div>}

    <div className="answer-footer">
      {dataDate && <span className="answer-date">Dữ liệu ngày {formatDay(dataDate)}</span>}
      {canOpenList && <button type="button" className="button small" onClick={() => onSearchJobs(toJobFilters(answer.applied_filters))}>
        Xem tất cả tin phù hợp <ArrowRight size={13} aria-hidden />
      </button>}
    </div>
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
        <span className="job-domain">{prettyText(display(job.job_domain))}</span>
        <h2>{prettyText(job.canonical_title)}</h2>
        <p className="company-name">{prettyCompany(job.company_display_name)}</p>
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
          {job.skills.length ? job.skills.map((skill) => <span key={skill.skill_code}>{prettyText(skill.skill_name)}</span>) : <span>Chưa xác định</span>}
        </div>
        {(job.posted_date || job.expired_date) && <div className="detail-grid">
          {job.posted_date && <div><small>Ngày đăng</small><b>{formatDay(job.posted_date)}</b></div>}
          {job.expired_date && <div><small>Hạn nộp</small><b>{formatDay(job.expired_date)}</b></div>}
        </div>}
        {job.description && <details className="ad-section" open>
          <summary>Mô tả công việc</summary>
          <p>{job.description}</p>
        </details>}
        {job.requirements && <details className="ad-section">
          <summary>Yêu cầu ứng viên</summary>
          <p>{job.requirements}</p>
        </details>}
        {job.benefits && <details className="ad-section">
          <summary>Phúc lợi</summary>
          <p>{job.benefits}</p>
        </details>}
        {!job.description && !job.requirements && !job.benefits && (
          <p className="muted">Nguồn không cung cấp nội dung mô tả cho tin này.</p>
        )}
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
