"""
Value Sheet 퀀트 대시보드 - GitHub Actions 데이터 수집기
pykrx로 KOSPI/KOSDAQ 전종목 데이터 수집 → Supabase 업로드

환경변수 필요:
  SUPABASE_URL              : Supabase 프로젝트 URL
  SUPABASE_SERVICE_ROLE_KEY : Supabase service_role 키 (쓰기 권한)
  BOND_YIELD                : 국채 3년 기준금리 % (기본값 3.5)
"""

import os, sys, traceback
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from pykrx import stock as krx
from supabase import create_client

# ── 설정 ─────────────────────────────────────────────────────
SUPABASE_URL  = os.environ['SUPABASE_URL']
SUPABASE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BOND_YIELD    = float(os.environ.get('BOND_YIELD', '3.5'))
BATCH_SIZE    = 300   # Supabase upsert 1회 최대 행 수

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── 유틸 ─────────────────────────────────────────────────────

def last_biz_day(dt=None):
    d = dt if dt else datetime.now()
    for _ in range(10):
        if d.weekday() < 5:
            return d.strftime('%Y%m%d')
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')


def fmt_price(v):  return f"{int(v):,}원"
def fmt_mktcap(v):
    v = int(v)
    if v >= 10**12:
        jo = v // 10**12; eok = (v % 10**12) // 10**8
        return f"{jo}조{eok:,}억" if eok else f"{jo}조"
    return f"{v // 10**8:,}억" if v >= 10**8 else f"{v // 10**4:,}만"


# ── 종목명 수집 ───────────────────────────────────────────────

def fetch_names(today):
    print("  종목명 수집 중...")
    names = {}
    try:
        import FinanceDataReader as fdr
        for mkt in ('KOSPI', 'KOSDAQ'):
            df = fdr.StockListing(mkt)
            code_col = next(c for c in df.columns if c in ('Symbol','Code'))
            name_col = next(c for c in df.columns if c in ('Name','ISU_NM','ShortName'))
            for _, row in df.iterrows():
                names[str(row[code_col]).zfill(6)] = row[name_col]
    except Exception as e:
        print(f"    FDR 실패({e}), pykrx fallback 사용")
        for mkt in ('KOSPI', 'KOSDAQ'):
            for t in krx.get_market_ticker_list(today, market=mkt):
                try: names[t] = krx.get_market_ticker_name(t)
                except: names[t] = t
    print(f"    종목명 {len(names)}개 수집 완료")
    return names


# ── 52주 고저 (주간 샘플링) ───────────────────────────────────

def fetch_52w(today):
    today_dt = datetime.strptime(today, '%Y%m%d')
    year_ago = today_dt - timedelta(days=365)
    highs, lows = {}, {}
    d, total, done = year_ago, 52, 0

    print("  52주 고저 수집 중 (가장 오래 걸립니다)...")
    while d <= today_dt:
        ds = last_biz_day(d)
        for mkt in ('KOSPI', 'KOSDAQ'):
            try:
                ohlcv = krx.get_market_ohlcv_by_ticker(ds, market=mkt)
                if ohlcv.empty: continue
                for tk in ohlcv.index:
                    h, l = ohlcv.at[tk, '고가'], ohlcv.at[tk, '저가']
                    if h > 0:
                        highs[tk] = max(highs.get(tk, 0), h)
                        lows[tk]  = min(lows.get(tk, float('inf')), l)
            except: pass
        d += timedelta(weeks=1)
        done += 1
        if done % 10 == 0:
            print(f"    {done}/{total}주 완료")

    return highs, lows


# ── 메인 ─────────────────────────────────────────────────────

def main():
    today    = last_biz_day()
    year_ago = last_biz_day(datetime.strptime(today, '%Y%m%d') - timedelta(days=365))
    print(f"\n[{datetime.now():%H:%M:%S}] ▶ 데이터 수집 시작 (기준일: {today})")

    # 1. 시가총액 & 현재가
    print("  시가총액 수집...")
    cap_df = pd.concat([
        krx.get_market_cap_by_ticker(today, market='KOSPI'),
        krx.get_market_cap_by_ticker(today, market='KOSDAQ'),
    ])

    # 2. 펀더멘털
    print("  펀더멘털 수집...")
    fund_today = krx.get_market_fundamental_by_ticker(today, market='ALL')

    # 3. 전년 EPS
    print("  전년 EPS 수집...")
    try:   fund_prev = krx.get_market_fundamental_by_ticker(year_ago, market='ALL')
    except: fund_prev = pd.DataFrame()

    # 4. 종목명
    names = fetch_names(today)

    # 5. 52주 고저
    highs, lows = fetch_52w(today)

    # ── 데이터 병합 ──────────────────────────────────────────
    print("  지표 계산 중...")
    df = cap_df[['종가', '시가총액']].copy()
    df = df.join(fund_today[['PBR', 'PER', 'EPS', 'BPS', 'DIV']], how='inner')
    df = df[(df['종가'] > 100) & (df['PBR'] > 0) & (df['BPS'] > 0)].copy()

    df['name']    = df.index.map(lambda t: names.get(t, t))
    cur           = df['종가']
    df['high52w'] = df.index.map(lambda t: highs.get(t, cur[t]))
    df['low52w']  = df.index.map(lambda t: lows.get(t,  cur[t] * 0.7))

    rng          = (df['high52w'] - df['low52w']).clip(1)
    df['pos52w'] = ((cur - df['low52w']) / rng * 100).clip(0, 100)
    df['ROE']    = (df['EPS'] / df['BPS'] * 100).clip(-200, 300)
    df['div_y']  = df['DIV'].clip(0, 50)
    df['bond_r'] = (df['div_y'] / BOND_YIELD).clip(0, 20)

    if not fund_prev.empty and 'EPS' in fund_prev.columns:
        prev = fund_prev['EPS'].reindex(df.index).fillna(0)
        mask = prev != 0
        df['epsG'] = 0.0
        df.loc[mask, 'epsG'] = (
            (df.loc[mask,'EPS'] - prev[mask]) / prev[mask].abs() * 100
        ).clip(-999, 999)
    else:
        df['epsG'] = 0.0

    ret  = ((cur - df['low52w']) / df['low52w'].clip(1) * 100).clip(0, 1000)
    risk = ((df['high52w'] - df['low52w']) / df['low52w'].clip(1) * 100).clip(1, 1000)
    df['RRR'] = (ret / risk).clip(0, 1).round(3)
    df['bScore'] = (df['bond_r'] / df['bond_r'].quantile(0.99).clip(0.01) * 100).clip(0, 100)

    total = len(df)
    print(f"  유효 종목: {total}개")

    # ── 퀀트 점수 ────────────────────────────────────────────
    def pr(s, asc=False): return s.rank(ascending=asc, pct=True, method='average') * 100

    df['s1'] = pr(df['pos52w'], True)
    df['s2'] = pr(df['ROE'])
    df['s3'] = pr(df['PBR'],    True)
    df['s4'] = pr(df['div_y'])
    df['s5'] = pr(df['epsG'])
    df['s6'] = pr(df['RRR'])
    df['s7'] = pr(df['bond_r'])

    W = np.array([1.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0])
    df['composite'] = (df[['s1','s2','s3','s4','s5','s6','s7']] * W).sum(axis=1) / W.sum()
    df['composite'] = df['composite'].round(2)
    df['avg_rank']  = df['composite'].rank(ascending=False, method='min').astype(int)
    df['roe_rank']  = df['ROE'].rank(ascending=False, method='min').astype(int)
    df['pbr_rank']  = df['PBR'].rank(ascending=True,  method='min').astype(int)
    df['bond_rank'] = df['bond_r'].rank(ascending=False, method='min').astype(int)

    # 이전 순위 비교 (Supabase에서 읽기)
    old_ranks = {}
    try:
        res = sb.table('stocks').select('ticker, avg_rank').execute()
        for row in res.data:
            old_ranks[row['ticker']] = row['avg_rank']
    except: pass

    eps_date = f"분기보고서 ({datetime.strptime(today,'%Y%m%d').strftime('%Y.%m')})"

    records = []
    for ticker, row in df.iterrows():
        old_r = old_ranks.get(ticker)
        rel   = (old_r - int(row['avg_rank'])) if old_r else 0
        rrr_v = float(row['RRR'])
        rrr_s = 'MAX' if rrr_v >= 0.85 else str(round(rrr_v * 10, 1))

        records.append({
            'ticker':       ticker,
            'name':         row['name'],
            'price':        fmt_price(row['종가']),
            'mktcap':       fmt_mktcap(row['시가총액']),
            'rrr':          rrr_s,
            'score':        float(row['composite']),
            'avg_rank':     int(row['avg_rank']),
            'rel_rank':     abs(rel),
            'pos52w':       round(float(row['pos52w']), 1),
            'low52w':       f"{int(row['low52w']):,}",
            'high52w':      f"{int(row['high52w']):,}",
            'roe':          round(float(row['ROE']), 1),
            'roe_rank':     int(row['roe_rank']),
            'pbr':          round(float(row['PBR']), 2),
            'pbr_rank':     int(row['pbr_rank']),
            'div_yield':    f"{round(float(row['div_y']), 1)}%",
            'bond_ratio':   f"{round(float(row['bond_r']), 1)}배",
            'bond_score':   int(row['bScore']),
            'bond_rank':    int(row['bond_rank']),
            'eps_growth':   f"{round(float(row['epsG']), 1)}%",
            'eps_date':     eps_date,
            'rel_rank_val': rel,
            'total':        total,
            'updated_at':   datetime.utcnow().isoformat(),
        })

    # ── Supabase 업로드 ──────────────────────────────────────
    print(f"\n  Supabase 업로드 중 ({total}개, 배치 {BATCH_SIZE}개씩)...")
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        sb.table('stocks').upsert(batch, on_conflict='ticker').execute()
        print(f"    {min(i + BATCH_SIZE, total)}/{total} 완료")

    sb.table('meta').upsert([
        {'key': 'last_updated',  'value': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        {'key': 'total_stocks',  'value': str(total)},
        {'key': 'base_date',     'value': today},
        {'key': 'bond_yield',    'value': str(BOND_YIELD)},
    ], on_conflict='key').execute()

    print(f"\n[{datetime.now():%H:%M:%S}] ✔ 완료! {total}개 종목 업로드")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
