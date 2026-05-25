/* ── Supabase 클라이언트 ── */
const { createClient } = window.supabase
const db = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

/* ── 상태 ── */
let allStocks    = []
let sortMode     = 'avg'
let marketFilter = 'all'
let searchQuery  = ''
let selectedTicker = null

/* ── 초기화 ── */
window.addEventListener('DOMContentLoaded', init)

async function init() {
  setTodayDate()
  if (SUPABASE_URL.includes('YOUR_PROJECT')) {
    showError('config.js 에서 Supabase URL과 ANON KEY를 설정해주세요.')
    return
  }
  showLoading(true, 'Supabase에서 데이터 불러오는 중...', 5)
  await loadStocks()
}

/* ── 데이터 로드 ── */
async function loadStocks() {
  try {
    showLoading(true, '종목 수 확인 중...', 15)
    const { count } = await db.from('stocks').select('*', { count: 'exact', head: true })
    if (!count) {
      showError('데이터가 없습니다. GitHub Actions를 수동으로 실행하거나 잠시 후 다시 시도하세요.')
      return
    }

    const { data: meta } = await db.from('meta').select('key, value')
    const metaMap = Object.fromEntries((meta || []).map(r => [r.key, r.value]))

    showLoading(true, `${count}개 종목 다운로드 중...`, 35)
    const PAGE = 1000
    let all = [], from = 0
    while (from < count) {
      const { data, error } = await db
        .from('stocks').select('*')
        .order('avg_rank', { ascending: true })
        .range(from, from + PAGE - 1)
      if (error) throw error
      all = all.concat(data)
      from += PAGE
      showLoading(true, `${Math.min(from, count)} / ${count} 종목 로드 중...`,
        35 + Math.round((from / count) * 55))
    }

    allStocks = all.map(mapRow)
    showLoading(false)

    const label = document.getElementById('updated-label')
    if (label && metaMap.last_updated)
      label.textContent = `최종 업데이트: ${metaMap.last_updated}  |  총 ${count}개 종목`

    refreshView()

  } catch (e) {
    console.error(e)
    showError(`데이터 로드 실패: ${e.message}`)
  }
}

/* ── DB row → app object ── */
function mapRow(r) {
  return {
    ticker:     r.ticker,
    name:       r.name,
    market:     r.market,
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
    // sub-scores (if stored; gracefully fall back to 0)
    s1: r.s1 || 0, s2: r.s2 || 0, s3: r.s3 || 0,
    s4: r.s4 || 0, s5: r.s5 || 0, s6: r.s6 || 0, s7: r.s7 || 0,
    mktcapRaw: parseMktcap(r.mktcap),
  }
}

/* ── 시가총액 파싱 (한국식) ── */
function parseMktcap(s) {
  if (!s) return 0
  let v = 0
  const jo  = s.match(/(\d[\d,]*)조/)
  const eok = s.match(/조.*?(\d[\d,]*)억/) || s.match(/^(\d[\d,]*)억/)
  if (jo)  v += parseInt(jo[1].replace(/,/g,''))  * 1e12
  if (eok) v += parseInt(eok[1].replace(/,/g,'')) * 1e8
  return v
}

/* ── 시가총액 등급 ── */
function mktcapTier(raw) {
  if (raw >= 10e12) return '대형'
  if (raw >= 1e12)  return '중형'
  if (raw >= 100e9) return '소형'
  return '소액'
}

/* ── 필터링된 종목 목록 ── */
function getFiltered() {
  return allStocks.filter(s => {
    if (marketFilter !== 'all' && s.market !== marketFilter) return false
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      if (!s.name.toLowerCase().includes(q) && !s.ticker.includes(q)) return false
    }
    return true
  })
}

/* ── 정렬 ── */
function getSorted(list) {
  return [...list].sort((a, b) =>
    sortMode === 'avg' ? a.avgRank - b.avgRank : b.relRank - a.relRank
  )
}

/* ── 전체 뷰 갱신 ── */
function refreshView() {
  const filtered = getFiltered()
  const sorted   = getSorted(filtered)
  populateSelect(sorted)
  renderRankingTable(sorted)
  // 선택 종목이 필터 결과에 없으면 첫 번째로
  if (selectedTicker && sorted.find(s => s.ticker === selectedTicker)) {
    const stock = allStocks.find(s => s.ticker === selectedTicker)
    if (stock) renderDashboard(stock)
  } else if (sorted.length > 0) {
    selectedTicker = sorted[0].ticker
    document.getElementById('stock-select').value = selectedTicker
    renderDashboard(sorted[0])
  }
}

/* ── 탭/필터 이벤트 ── */
function setSortMode(mode, btn) {
  sortMode = mode
  document.querySelectorAll('#sort-tabs .tab-btn').forEach(b => b.classList.remove('active'))
  btn.classList.add('active')
  refreshView()
}

function setMarket(mkt, btn) {
  marketFilter = mkt
  document.querySelectorAll('#market-tabs .tab-btn').forEach(b => b.classList.remove('active'))
  btn.classList.add('active')
  refreshView()
}

function onSearch() {
  searchQuery = document.getElementById('search-input').value.trim()
  refreshView()
}

/* ── 드롭다운 ── */
function populateSelect(sorted) {
  const sel = document.getElementById('stock-select')
  const cur = sel.value
  sel.innerHTML = ''
  sorted.forEach(s => {
    const opt = document.createElement('option')
    opt.value = s.ticker
    const tier = mktcapTier(s.mktcapRaw)
    opt.textContent = `${s.name} [${s.market}] — 종합 ${s.avgRank}위`
    if (s.ticker === cur || s.ticker === selectedTicker) opt.selected = true
    sel.appendChild(opt)
  })
}

function selectStock() {
  const tk = document.getElementById('stock-select').value
  selectedTicker = tk
  const s = allStocks.find(x => x.ticker === tk)
  if (s) renderDashboard(s)
  // 테이블 선택 강조
  document.querySelectorAll('#ranking-tbody tr').forEach(tr => {
    tr.classList.toggle('selected', tr.dataset.ticker === tk)
  })
}

async function refreshData() {
  allStocks = []
  showLoading(true, '새로고침 중...', 5)
  await loadStocks()
}

/* ── 대시보드 렌더링 ── */
function renderDashboard(s) {
  const tier    = mktcapTier(s.mktcapRaw)
  const tierBadge = `<span class="tier-badge tier-${tier}">${tier}주</span>`

  document.getElementById('kpi-price').innerHTML  = s.price + tierBadge
  document.getElementById('kpi-mktcap').textContent = '시가총액 ' + (s.mktcap || '-')
  document.getElementById('kpi-pos52w').textContent = s.pos52w + '%'
  document.getElementById('kpi-52w-range').textContent = `저가 ${s.low52w} / 고가 ${s.high52w}`
  setText('kpi-rrr',        s.rrr)
  setText('kpi-score',      s.score.toFixed(2) + '점')
  setText('kpi-score-rank', `전체 ${s.avgRank}위 / ${s.total}`)

  setText('val-52w',  s.pos52w + '%')
  setText('low-52w',  s.low52w)
  setText('high-52w', s.high52w)
  setText('val-roe',  s.roe + '%')
  setText('rank-roe', `전체 ${s.roeRank}위 / ${s.total}`)
  setText('val-pbr',  s.pbr > 0 ? s.pbr + 'x' : 'N/A')
  setText('rank-pbr', s.pbr > 0 ? `전체 ${s.pbrRank}위 / ${s.total}` : '데이터 없음')

  setText('bars-stock-name',   `— ${s.name} (${s.market})`)
  setText('detail-stock-name', `— ${s.name}`)
  setText('det-div-yield',  s.divYield)
  setText('det-bond-ratio', s.bondRatio)
  setText('det-bond-score', s.bondScore + '점')
  setText('det-bond-rank',  `순위: ${s.bondRank}위 / ${s.total}`)
  setText('det-eps-growth', s.epsGrowth)
  setText('det-eps-date',   s.epsDate)

  const rel    = s.relRankVal
  const relStr = rel > 0 ? `▲ +${rel}` : rel < 0 ? `▼ ${rel}` : '± 0'
  setText('det-rel-rank',  relStr)
  setText('det-rel-total', `현재 ${s.avgRank}위 / ${s.total}`)
  const card = document.getElementById('det-rel-card')
  card.className = 'detail-card ' + (rel > 0 ? 'highlight-green' : rel < 0 ? 'highlight-red' : '')

  drawGauge('gauge-52w', s.pos52w, 0,   100, '#3b82f6', [0, 25, 50, 75, 100])
  drawGauge('gauge-roe', s.roe,   -10,   40, '#f59e0b', [-10, 2.5, 15, 27.5, 40])
  drawGauge('gauge-pbr', s.pbr,    0,     3, '#059669', [0, 0.75, 1.5, 2.25, 3])

  renderScoreBars(s)
}

/* ── 점수 구성 바 ── */
const SCORE_META = [
  { key:'s1', label:'52주위치', desc:'저가근접↑', color:'#3b82f6' },
  { key:'s2', label:'ROE',      desc:'수익성↑',   color:'#f59e0b' },
  { key:'s3', label:'PBR',      desc:'저PBR↑',    color:'#059669' },
  { key:'s4', label:'배당률',   desc:'배당↑',     color:'#0891b2' },
  { key:'s5', label:'EPS성장',  desc:'이익증가↑', color:'#7c3aed' },
  { key:'s6', label:'RRR',      desc:'수익/위험↑', color:'#dc2626' },
  { key:'s7', label:'국채대비', desc:'배당비율↑',  color:'#d97706' },
]

function renderScoreBars(s) {
  const container = document.getElementById('score-bars')
  container.innerHTML = ''
  SCORE_META.forEach(m => {
    const val = s[m.key] || 0
    const pct = Math.max(0, Math.min(100, val))
    const el  = document.createElement('div')
    el.className = 'score-bar-item'
    el.innerHTML = `
      <div class="score-bar-label">${m.label}<br><span style="color:#8fa3b1;font-size:9px">${m.desc}</span></div>
      <div class="score-bar-track">
        <div class="score-bar-fill" style="width:${pct}%;background:${m.color}"></div>
      </div>
      <div class="score-bar-val" style="color:${m.color}">${Math.round(val)}점</div>
    `
    container.appendChild(el)
  })
}

/* ── 랭킹 테이블 ── */
function renderRankingTable(sorted) {
  const tbody = document.getElementById('ranking-tbody')
  tbody.innerHTML = ''
  const top = sorted.slice(0, 20)
  top.forEach((s, i) => {
    const rank   = i + 1
    const isTop3 = rank <= 3
    const roeClass = s.roe > 0 ? 'roe-pos' : s.roe < 0 ? 'roe-neg' : ''
    const rrrClass = s.rrr === 'MAX' ? 'rrr-max' : 'rrr-val'
    const pos52pct = Math.max(0, Math.min(100, s.pos52w))
    const tierRaw  = mktcapTier(s.mktcapRaw)
    const tr = document.createElement('tr')
    tr.dataset.ticker = s.ticker
    if (s.ticker === selectedTicker) tr.classList.add('selected')
    tr.onclick = () => {
      selectedTicker = s.ticker
      document.getElementById('stock-select').value = s.ticker
      renderDashboard(s)
      document.querySelectorAll('#ranking-tbody tr').forEach(r =>
        r.classList.toggle('selected', r.dataset.ticker === s.ticker))
    }
    tr.innerHTML = `
      <td class="center"><span class="rank-num${isTop3?' top3':''}">${rank}</span></td>
      <td><b>${s.name}</b></td>
      <td class="center"><span class="mkt-badge mkt-${s.market}">${s.market}</span></td>
      <td class="right"><span class="score-chip">${s.score.toFixed(1)}</span></td>
      <td class="center">
        <div class="mini-bar-wrap">
          <div class="mini-bar-fill" style="width:${pos52pct}%;background:#3b82f6"></div>
        </div>
        <span style="font-size:11px;color:#526070;margin-left:4px">${s.pos52w}%</span>
      </td>
      <td class="right"><span class="${roeClass} roe-val">${s.roe > 0 ? '+' : ''}${s.roe}%</span></td>
      <td class="right">${s.pbr > 0 ? s.pbr + 'x' : '<span style="color:#8fa3b1">N/A</span>'}</td>
      <td class="center"><span class="${rrrClass}">${s.rrr}</span></td>
      <td class="right">${s.divYield}</td>
      <td class="right" style="font-size:12px;color:#526070">${s.mktcap || '-'}</td>
    `
    tbody.appendChild(tr)
  })

  // 섹션 제목에 필터 상태 반영
  const mktLabel = marketFilter === 'all' ? '전체' : marketFilter
  document.getElementById('ranking-title').textContent =
    `🏆 ${mktLabel} 상위 랭킹 Top 20 (총 ${sorted.length}개 종목)`
}

/* ── Canvas 게이지 ── */
function drawGauge(id, value, min, max, color, ticks) {
  const canvas = document.getElementById(id)
  const ctx    = canvas.getContext('2d')
  const W = canvas.width, H = canvas.height
  ctx.clearRect(0, 0, W, H)

  const cx = W / 2, cy = H - 20
  const r  = Math.min(W * 0.42, (H - 20) * 0.88)
  const SA = Math.PI, EA = 2 * Math.PI
  const ratio    = Math.max(0, Math.min(1, (value - min) / (max - min)))
  const valAngle = SA + ratio * Math.PI

  // 배경 호
  ctx.beginPath(); ctx.arc(cx, cy, r, SA, EA)
  ctx.strokeStyle = '#dde3ee'; ctx.lineWidth = 16; ctx.lineCap = 'round'; ctx.stroke()

  // 값 호
  if (value > min) {
    ctx.beginPath(); ctx.arc(cx, cy, r, SA, valAngle)
    ctx.strokeStyle = color; ctx.lineWidth = 16; ctx.lineCap = 'round'; ctx.stroke()
  }

  // 눈금 라벨
  ctx.font = '10px Malgun Gothic, sans-serif'
  ctx.fillStyle = '#8fa3b1'; ctx.textAlign = 'center'
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
    if (sp)  sp.textContent  = step
    if (bar) bar.style.width = Math.max(2, pct) + '%'
  } else {
    ov.classList.add('hidden')
  }
}

function showError(msg) {
  document.querySelector('.loading-title').textContent = '⚠️ 오류'
  document.getElementById('loading-step').textContent  = msg
  document.getElementById('progress-bar').style.background = '#dc2626'
  document.getElementById('loading-overlay').classList.remove('hidden')
}
