"""
Value Sheet 퀀트 대시보드 - GitHub Actions 데이터 수집기
yfinance 기반 (pykrx는 GitHub Actions IP 차단됨)

종목 리스트: 리포지토리 내 data/krx_stocks.csv (KRX KIND에서 로컬 생성, 커밋)
가격/지표:   yfinance (Yahoo Finance, 전세계 접근 가능)
"""
import os, sys, traceback, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import yfinance as yf
from supabase import create_client

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BOND_YIELD   = float(os.environ.get('BOND_YIELD', '3.5'))
BATCH_SIZE   = 300
MAX_WORKERS  = 8   # 낮게 유지해서 crumb 세션 안정화

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── 종목 리스트 ──────────────────────────────────────────────

def fetch_krx_list() -> pd.DataFrame:
    # data/krx_stocks.csv는 로컬에서 생성 후 리포에 커밋된 파일
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path   = os.path.join(script_dir, '..', 'data', 'krx_stocks.csv')
    csv_path   = os.path.normpath(csv_path)
    print(f"  종목 리스트 로드: {csv_path}")
    df = pd.read_csv(csv_path, dtype={'ticker': str})
    df['ticker'] = df['ticker'].str.zfill(6)
    print(f"  총 {len(df)}개 (KOSPI: {len(df[df['market']=='KOSPI'])}, KOSDAQ: {len(df[df['market']=='KOSDAQ'])})")
    return df


# ── yfinance 단일 종목 ───────────────────────────────────────

def get_one(row: dict) -> dict | None:
    try:
        t  = yf.Ticker(row['yf_ticker'])
        fi = t.fast_info

        price = getattr(fi, 'last_price', None)
        if not price or price <= 0:
            return None

        mktcap = getattr(fi, 'market_cap', 0) or 0
        high52 = getattr(fi, 'fifty_two_week_high', price) or price
        low52  = getattr(fi, 'fifty_two_week_low',  price * 0.7) or price * 0.7

        # t.info 실패 시(401 Crumb 만료 등) 가격 데이터만이라도 저장
        pbr = roe = div_yield = eps = eps_fwd = 0.0
        try:
            info      = t.info
            pbr       = float(info.get('priceToBook')    or 0)
            roe       = float(info.get('returnOnEquity') or 0) * 100
            div_yield = float(info.get('dividendYield')  or 0) * 100
            eps       = float(info.get('trailingEps')    or 0)
            eps_fwd   = float(info.get('forwardEps')     or eps)
        except Exception:
            pass

        return {
            'ticker':    row['ticker'],
            'name':      row['name'],
            'market':    row['market'],
            'price':     price,
            'mktcap':    mktcap,
            'high52w':   high52,
            'low52w':    low52,
            'pbr':       pbr,
            'roe':       roe,
            'div_yield': div_yield,
            'eps':       eps,
            'eps_fwd':   eps_fwd,
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

    # 1. 종목 리스트
    stocks_df = fetch_krx_list()
    rows = stocks_df.to_dict('records')
    print(f"  총 {len(rows)}개 종목 처리 예정")

    # 2. yfinance 병렬 수집
    print(f"  yfinance 수집 중 (병렬 {MAX_WORKERS} workers)...")
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
    # 가격 데이터만 있는 종목도 포함 (PBR/ROE 등은 없을 수 있음)
    total = len(df)
    has_fundamentals = int((df['pbr'] > 0).sum())
    print(f"  유효 종목: {total}개 (기본지표 보유: {has_fundamentals}개)")

    # 3. 파생 지표
    rng            = (df['high52w'] - df['low52w']).clip(1)
    df['pos52w']   = ((df['price'] - df['low52w']) / rng * 100).clip(0, 100)
    df['bond_r']   = (df['div_yield'] / BOND_YIELD).clip(0, 20)
    df['eps_g']    = np.where(
        df['eps_fwd'] != 0,
        ((df['eps'] - df['eps_fwd']) / df['eps_fwd'].abs().clip(0.01) * 100).clip(-500, 500),
        0.0
    )
    ret          = ((df['price'] - df['low52w']) / df['low52w'].clip(1) * 100).clip(0, 1000)
    risk         = (rng / df['low52w'].clip(1) * 100).clip(1, 1000)
    df['rrr']    = (ret / risk).clip(0, 1)
    df['bscr']   = (df['bond_r'] / df['bond_r'].quantile(0.99).clip(0.01) * 100).clip(0, 100)

    # 4. 퀀트 점수
    def pr(s, asc=False): return s.rank(ascending=asc, pct=True, method='average') * 100

    df['s1'] = pr(df['pos52w'],   True)
    df['s2'] = pr(df['roe'])
    df['s3'] = pr(df['pbr'],      True)
    df['s4'] = pr(df['div_yield'])
    df['s5'] = pr(df['eps_g'])
    df['s6'] = pr(df['rrr'])
    df['s7'] = pr(df['bond_r'])

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
        })

    # 5. Supabase 업로드
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
