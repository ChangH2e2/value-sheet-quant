/* ── Supabase 클라이언트 초기화 ── */
const { createClient } = window.supabase
const db = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

/* ── 상태 ── */
let allStocks = []
let sortMode  = 'avg'

/* ── 초기화 ── */
window.addEventListener('DOMContentLoaded', init)

async function init() {
  setTodayDate()

  // 설정 미완료 안내
  if (SUPABASE_URL.includes('YOUR_PROJECT')) {
    showError('config.js 에서 Supabase URL과 ANON KEY를 설정해주세요.')
    return
  }

  showLoading(true, 'Supabase에서 데이터 불러오는 중...', 5)
  await loadStocks()
}

/* ── 데이터 로드 (페이지네이션) ── */
async function loadStocks() {
  try {
    showLoading(true, '종목 데이터 로드 중...', 20)

    // 전체 종목 수 확인
    const { count } = await db.from('stocks').select('*', { count: 'exact', head: true })

    if (!count) {
      showError('데이터가 없습니다. GitHub Actions를 수동으로 실행하거나 잠시 후 다시 시도하세요.')
      return
    }

    // 메타 정보
    const { data: meta } = await db.from('meta').select('key, value')
    const metaMap = Object.fromEntries((meta || []).map(r => [r.key, r.value]))

    showLoading(true, `${count}개 종목 다운로드 중...`, 40)

    // 전체 데이터 페이지네이션 (1000개씩)
    const PAGE = 1000
    let all = [], from = 0
    while (from < count) {
      const { data, error } = await db
        .from('stocks')
        .select('*')
        .order('avg_rank', { ascending: true })
        .range(from, from + PAGE - 1)
      if (error) throw error
      all = all.concat(data)
      from += PAGE
      const pct = 40 + Math.round((from / count) * 50)
      showLoading(true, `${Math.min(from, count)} / ${count} 종목 로드 중...`, pct)
    }

    allStocks = all.map(mapRow)
    showLoading(false)

    // 업데이트 시각 표시
    const label = document.getElementById('updated-label')
    if (label && metaMap.last_updated) {
      label.textContent = `최종 업데이트: ${metaMap.last_updated}`
    }

    populateSelect()

  } catch (e) {
    console.error(e)
    showError(`데이터 로드 실패: ${e.message}`)
  }
}

/* DB 컬럼(snake_case) → 앱(camelCase) 변환 */
function mapRow(r) {
  return {
    ticker:     r.ticker,
    name:       r.name,
    price:      r.price,
    mktcap:     r.mktcap,
    rrr:        r.rrr,
    score:      r.score,
    avgRank:    r.avg_rank,
    relRank:    r.rel_rank,
    pos52w:     r.pos52w,
    low52w:     r.low52w,
    high52w:    r.high52w,
    roe:        r.roe,
    roeRank:    r.roe_rank,
    pbr:        r.pbr,
    pbrRank:    r.pbr_rank,
    divYield:   r.div_yield,
    bondRatio:  r.bond_ratio,
    bondScore:  r.bond_score,
    bondRank:   r.bond_rank,
    epsGrowth:  r.eps_growth,
    epsDate:    r.eps_date,
    relRankVal: r.rel_rank_val,
    total:      r.total,
  }
}

/* ── 드롭다운 ── */
function setSortMode(mode, btn) {
  sortMode = mode
  document.querySelectorAll('.sort-tab').forEach(b => b.classList.remove('active'))
  btn.classList.add('active')
  populateSelect()
}

function populateSelect() {
  const sel    = document.getElementById('stock-select')
  const prevTk = sel.value

  const sorted = [...allStocks].sort((a, b) =>
    sortMode === 'avg' ? a.avgRank - b.avgRank : a.relRank - b.relRank
  )

  sel.innerHTML = ''
  sorted.forEach(s => {
    const opt = document.createElement('option')
    opt.value = s.ticker
    opt.textContent = `${s.name} (종합 ${s.avgRank}위, 상대 ${s.relRank}위)`
    if (s.ticker === prevTk) opt.selected = true
    sel.appendChild(opt)
  })

  selectStock()
}

function selectStock() {
  const tk = document.getElementById('stock-select').value
  const s  = allStocks.find(x => x.ticker === tk)
  if (s) renderDashboard(s)
}

/* ── 수동 새로고침 ── */
async function refreshData() {
  allStocks = []
  await loadStocks()
}

/* ── 대시보드 렌더링 ── */
function renderDashboard(s) {
  setText('kpi-price',      s.price)
  setText('kpi-mktcap',     s.mktcap)
  setText('kpi-rrr',        s.rrr)
  setText('kpi-score',      s.score.toFixed(2) + '점')
  setText('kpi-score-rank', `(${s.avgRank}위 / ${s.total})`)

  setText('val-52w',  s.pos52w + '%')
  setText('low-52w',  s.low52w)
  setText('high-52w', s.high52w)

  setText('val-roe',  s.roe + '%')
  setText('rank-roe', `전체 ${s.roeRank}위 / ${s.total}`)

  setText('val-pbr',  s.pbr + 'x')
  setText('rank-pbr', `전체 ${s.pbrRank}위 / ${s.total}`)

  setText('detail-stock-name', s.name)
  setText('det-div-yield',  s.divYield)
  setText('det-bond-ratio', s.bondRatio)
  setText('det-bond-score', s.bondScore + '점')
  setText('det-bond-rank',  `순위: ${s.bondRank}위 / ${s.total}`)
  setText('det-eps-growth', s.epsGrowth)
  setText('det-eps-date',   s.epsDate)

  const rel    = s.relRankVal
  const relStr = rel > 0 ? `+${rel}` : rel < 0 ? `${rel}` : '±0'
  setText('det-rel-rank',  relStr)
  setText('det-rel-total', `전체 ${s.avgRank}위 / ${s.total}`)

  const card = document.getElementById('det-rel-card')
  card.className = 'detail-card ' + (rel < 0 ? 'highlight-red' : rel > 0 ? 'highlight-teal' : '')

  drawGauge('gauge-52w', s.pos52w, 0,   100, '#4a9eff', [0, 25, 50, 75, 100])
  drawGauge('gauge-roe', s.roe,   -10,   40, '#f5c842', [-10, 2.5, 15, 27.5, 40])
  drawGauge('gauge-pbr', s.pbr,    0,     3, '#6dd96d', [0, 0.75, 1.5, 2.25, 3])
}

/* ── Canvas 게이지 ── */
function drawGauge(id, value, min, max, color, ticks) {
  const canvas = document.getElementById(id)
  const ctx = canvas.getContext('2d')
  const W = canvas.width, H = canvas.height
  ctx.clearRect(0, 0, W, H)

  const cx = W / 2, cy = H - 20
  const r  = Math.min(W * 0.42, (H - 20) * 0.88)
  const SA = Math.PI, EA = 2 * Math.PI
  const ratio    = Math.max(0, Math.min(1, (value - min) / (max - min)))
  const valAngle = SA + ratio * Math.PI

  ctx.beginPath(); ctx.arc(cx, cy, r, SA, EA)
  ctx.strokeStyle = '#1e2e45'; ctx.lineWidth = 16; ctx.lineCap = 'round'; ctx.stroke()

  ctx.beginPath(); ctx.arc(cx, cy, r, SA, valAngle)
  ctx.strokeStyle = color; ctx.lineWidth = 16; ctx.lineCap = 'round'; ctx.stroke()

  ctx.font = '10px Malgun Gothic, sans-serif'
  ctx.fillStyle = '#5a6f88'; ctx.textAlign = 'center'
  ticks.forEach(t => {
    const tr  = Math.max(0, Math.min(1, (t - min) / (max - min)))
    const ang = SA + tr * Math.PI
    ctx.fillText(t, cx + (r + 20) * Math.cos(ang), cy + (r + 20) * Math.sin(ang) + 4)
  })
}

/* ── 유틸 ── */
function setText(id, val) { const el = document.getElementById(id); if (el) el.textContent = val }

function setTodayDate() {
  const d = new Date()
  setText('today-date', `(${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())})`)
}

function pad(n) { return String(n).padStart(2, '0') }

function showLoading(show, step = '', pct = 0) {
  const ov  = document.getElementById('loading-overlay')
  const sp  = document.getElementById('loading-step')
  const bar = document.getElementById('progress-bar')
  if (show) {
    ov.classList.remove('hidden')
    if (sp)  sp.textContent   = step
    if (bar) bar.style.width  = Math.max(2, pct) + '%'
  } else {
    ov.classList.add('hidden')
  }
}

function showError(msg) {
  const sp  = document.getElementById('loading-step')
  const bar = document.getElementById('progress-bar')
  const ttl = document.querySelector('.loading-title')
  if (ttl) ttl.textContent = '⚠️ 오류'
  if (sp)  sp.textContent  = msg
  if (bar) bar.style.background = '#e05a5a'
  document.getElementById('loading-overlay')?.classList.remove('hidden')
}
