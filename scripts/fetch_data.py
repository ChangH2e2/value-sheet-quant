"""
Value Sheet 퀀트 대시보드 - GitHub Actions 데이터 수집기
데이터 소스: Naver Finance HTML (한국 주식 완전 지원)

종목 리스트: data/krx_stocks.csv (로컬에서 KRX KIND 다운로드 후 커밋)
가격/지표:   finance.naver.com (가격, 52주, PBR, PER, ROE, 배당률, 시가총액)
"""
import os, sys, traceback, re, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import requests
from supabase import create_client

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BOND_YIELD   = float(os.environ.get('BOND_YIELD', '3.5'))
BATCH_SIZE   = 300
MAX_WORKERS  = 12

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── 종목 리스트 ──────────────────────────────────────────────

def fetch_krx_list() -> pd.DataFrame:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path   = os.path.normpath(os.path.join(script_dir, '..', 'data', 'krx_stocks.csv'))
    print(f"  종목 리스트 로드: {csv_path}")
    df = pd.read_csv(csv_path, dtype={'ticker': str})
    df['ticker'] = df['ticker'].str.zfill(6)
    # 6자리 숫자 티커만
    df = df[df['ticker'].str.match(r'^\d{6}$')].copy()
    print(f"  총 {len(df)}개 (KOSPI: {len(df[df['market']=='KOSPI'])}, KOSDAQ: {len(df[df['market']=='KOSDAQ'])})")
    return df


# ── Naver Finance HTML 파서 ───────────────────────────────────

def _num(s: str, default=0.0) -> float:
    if not s:
        return float(default)
    s = s.strip().replace(',', '').replace('%', '')
    try:
        return float(s)
    except ValueError:
        return float(default)


def parse_naver_page(html: str) -> dict:
    """Naver Finance main 페이지 HTML에서 전 지표 파싱"""

    # 현재가
    m = re.search(r'현재가\s+([\d,]+)', html)
    price = int(_num(m.group(1))) if m else 0

    # 52주 최고 / 최저 (같은 <td> 안에 두 <em> 태그)
    m52 = re.search(r'52주최고.*?<td>.*?<em[^>]*>([\d,]+)</em>.*?<em[^>]*>([\d,]+)</em>', html, re.DOTALL)
    if m52:
        h52 = int(_num(m52.group(1)))
        l52 = int(_num(m52.group(2)))
    else:
        h52 = price
        l52 = int(price * 0.7)

    # 시가총액 (억원) - 첫 번째 <td> 값
    m_mkt = re.search(r'시가총액\(억\).*?<td>([\d,]+)</td>', html, re.DOTALL)
    mktcap_eok = int(_num(m_mkt.group(1))) if m_mkt else 0
    mktcap = mktcap_eok * 1e8  # 억원 → 원

    # 투자지표: PER, EPS, PBR, ROE (각 항목 뒤 <em> 또는 <td> 숫자)
    def get_metric(label: str) -> float:
        idx = html.find(label)
        if idx < 0:
            return 0.0
        chunk = html[idx: idx + 600]
        # em 태그 안 숫자 (부호 포함)
        m_em = re.search(r'<em[^>]*>\s*([+-]?[\d,. ]+)\s*</em>', chunk)
        if m_em:
            return _num(m_em.group(1))
        # td 태그 숫자
        m_td = re.search(r'<td[^>]*>\s*([+-]?[\d,. ]+)\s*</td>', chunk)
        if m_td:
            return _num(m_td.group(1))
        return 0.0

    per       = get_metric('PER(배)')
    eps_raw   = get_metric('EPS(원)')       # 원 단위 EPS
    pbr       = get_metric('PBR(배)')
    roe       = get_metric('ROE(지배주주)')
    bps       = get_metric('BPS(원)')

    # 배당수익률 - '%' 포함 형태
    m_div = re.search(r'배당수익률.*?<em[^>]*>\s*([+-]?[\d,. ]+)\s*</em>', html, re.DOTALL)
    if not m_div:
        m_div = re.search(r'배당수익률.*?<td[^>]*>\s*([+-]?[\d,. ]+)\s*%?', html, re.DOTALL)
    div_yield = _num(m_div.group(1)) if m_div else 0.0

    # EPS (fwd) - 다음 분기 EPS 예상치 (있으면)
    # Naver에는 별도 제공 안함 → trailing EPS 그대로 사용
    eps_fwd = eps_raw

    return {
        'price':     price,
        'mktcap':    mktcap,
        'high52w':   h52,
        'low52w':    l52,
        'per':       per,
        'pbr':       pbr,
        'roe':       roe,
        'div_yield': div_yield,
        'eps':       eps_raw,
        'eps_fwd':   eps_fwd,
        'bps':       bps,
    }


# ── 단일 종목 수집 ────────────────────────────────────────────

def get_one(row: dict) -> dict | None:
    code = row['ticker']
    url  = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        html = r.content.decode('utf-8', errors='replace')
        d    = parse_naver_page(html)

        price = d['price']
        if price <= 0:
            return None

        return {
            'ticker':    code,
            'name':      row['name'],
            'market':    row['market'],
            'price':     price,
            'mktcap':    d['mktcap'],
            'high52w':   d['high52w'],
            'low52w':    d['low52w'],
            'pbr':       d['pbr'],
            'roe':       d['roe'],
            'div_yield': d['div_yield'],
            'eps':       d['eps'],
            'eps_fwd':   d['eps_fwd'],
        }
    except Exception:
        return None


# ── 포맷 헬퍼 ────────────────────────────────────────────────

def fmt_price(v):
    return f"{int(v):,}원"

def fmt_mktcap(v):
    v = int(v)
    if v >= 10**12:
        jo = v // 10**12; eok = (v % 10**12) // 10**8
        return f"{jo}조{eok:,}억" if eok else f"{jo}조"
    if v >= 10**8:
        return f"{v // 10**8:,}억"
    return f"{v // 10**4:,}만"


# ── 메인 ─────────────────────────────────────────────────────

def main():
    print(f"\n[{datetime.now():%H:%M:%S}] ▶ 데이터 수집 시작")

    stocks_df = fetch_krx_list()
    rows = stocks_df.to_dict('records')
    print(f"  총 {len(rows)}개 종목 처리 예정")

    print(f"  Naver Finance 수집 중 (병렬 {MAX_WORKERS} workers)...")
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(get_one, r): r for r in rows}
        for f in as_completed(futures):
            data = f.result()
            if data:
                results.append(data)
            done += 1
            if done % 200 == 0:
                print(f"    {done}/{len(rows)} 처리 (유효: {len(results)})")

    if not results:
        raise RuntimeError("유효한 종목 데이터 없음")

    df = pd.DataFrame(results)
    # 가격이 있는 종목만 (이미 get_one에서 필터됨)
    total = len(df)
    has_pbr = int((df['pbr'] > 0).sum())
    has_div = int((df['div_yield'] > 0).sum())
    print(f"  유효 종목: {total}개 | PBR보유: {has_pbr}개 | 배당정보: {has_div}개")

    # 파생 지표
    rng          = (df['high52w'] - df['low52w']).clip(1)
    df['pos52w'] = ((df['price'] - df['low52w']) / rng * 100).clip(0, 100)
    df['bond_r'] = (df['div_yield'] / BOND_YIELD).clip(0, 20)
    df['eps_g']  = np.where(
        df['eps_fwd'].abs() > 0.01,
        ((df['eps'] - df['eps_fwd']) / df['eps_fwd'].abs() * 100).clip(-500, 500),
        0.0
    )
    ret         = ((df['price'] - df['low52w']) / df['low52w'].clip(1) * 100).clip(0, 1000)
    risk        = (rng / df['low52w'].clip(1) * 100).clip(1, 1000)
    df['rrr']   = (ret / risk).clip(0, 1)
    df['bscr']  = (df['bond_r'] / df['bond_r'].quantile(0.99).clip(0.01) * 100).clip(0, 100)

    # 퀀트 점수 (7개 서브스코어, 백분위 0-100)
    def pr(s, asc=False):
        return s.rank(ascending=asc, pct=True, method='average') * 100

    df['s1'] = pr(df['pos52w'],   True)   # 저가 근접 ↑
    df['s2'] = pr(df['roe'])               # ROE ↑
    df['s3'] = pr(df['pbr'],      True)   # 저PBR ↑
    df['s4'] = pr(df['div_yield'])         # 배당률 ↑
    df['s5'] = pr(df['eps_g'])             # EPS 성장 ↑
    df['s6'] = pr(df['rrr'])               # RRR ↑
    df['s7'] = pr(df['bond_r'])            # 국채 대비 ↑

    W = np.array([1.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0])
    df['composite'] = (df[['s1','s2','s3','s4','s5','s6','s7']] * W).sum(axis=1) / W.sum()
    df['composite'] = df['composite'].round(2)
    df['avg_rank']  = df['composite'].rank(ascending=False, method='min').astype(int)
    df['roe_rank']  = df['roe'].rank(ascending=False, method='min').astype(int)
    df['pbr_rank']  = df['pbr'].rank(ascending=True,  method='min').astype(int)
    df['bond_rank'] = df['bond_r'].rank(ascending=False, method='min').astype(int)

    # 이전 순위 비교
    old_ranks = {}
    try:
        res = sb.table('stocks').select('ticker, avg_rank').execute()
        for r in res.data:
            old_ranks[r['ticker']] = r['avg_rank']
    except Exception:
        pass

    eps_date = f"분기보고서 ({datetime.now().strftime('%Y.%m')})"
    records  = []
    for _, row in df.iterrows():
        rel   = old_ranks.get(row['ticker'], int(row['avg_rank'])) - int(row['avg_rank'])
        rrr_v = float(row['rrr'])
        rrr_s = 'MAX' if rrr_v >= 0.85 else str(round(rrr_v * 10, 1))

        records.append({
            'ticker':       row['ticker'],
            'name':         row['name'],
            'market':       row['market'],
            'price':        fmt_price(row['price']),
            'mktcap':       fmt_mktcap(row['mktcap']),
            'rrr':          rrr_s,
            'score':        float(row['composite']),
            'avg_rank':     int(row['avg_rank']),
            'rel_rank':     abs(rel),
            'pos52w':       round(float(row['pos52w']), 1),
            'low52w':       f"{int(row['low52w']):,}",
            'high52w':      f"{int(row['high52w']):,}",
            'roe':          round(float(row['roe']), 1),
            'roe_rank':     int(row['roe_rank']),
            'pbr':          round(float(row['pbr']), 2),
            'pbr_rank':     int(row['pbr_rank']),
            'div_yield':    f"{round(float(row['div_yield']), 1)}%",
            'bond_ratio':   f"{round(float(row['bond_r']), 1)}배",
            'bond_score':   int(row['bscr']),
            'bond_rank':    int(row['bond_rank']),
            'eps_growth':   f"{round(float(row['eps_g']), 1)}%",
            'eps_date':     eps_date,
            'rel_rank_val': rel,
            'total':        total,
            'updated_at':   datetime.utcnow().isoformat(),
            # 서브스코어 저장
            's1': round(float(row['s1']), 1),
            's2': round(float(row['s2']), 1),
            's3': round(float(row['s3']), 1),
            's4': round(float(row['s4']), 1),
            's5': round(float(row['s5']), 1),
            's6': round(float(row['s6']), 1),
            's7': round(float(row['s7']), 1),
        })

    # Supabase 업로드
    print(f"\n  Supabase 업로드 중 ({total}개)...")
    for i in range(0, len(records), BATCH_SIZE):
        sb.table('stocks').upsert(records[i:i + BATCH_SIZE], on_conflict='ticker').execute()
        print(f"    {min(i + BATCH_SIZE, total)}/{total} 완료")

    sb.table('meta').upsert([
        {'key': 'last_updated', 'value': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        {'key': 'total_stocks', 'value': str(total)},
        {'key': 'bond_yield',   'value': str(BOND_YIELD)},
    ], on_conflict='key').execute()

    print(f"\n[{datetime.now():%H:%M:%S}] ✔ 완료! {total}개 종목 업로드")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
