"""
Value Sheet 퀀트 대시보드 - 백엔드 서버
pykrx + FinanceDataReader 기반 전체 KOSPI/KOSDAQ 종목 데이터
"""

import os, json, threading, traceback
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import pandas as pd
import numpy as np
from pykrx import stock as krx

app = Flask(__name__)
CORS(app)

CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data_cache.json')
BOND_YIELD_PCT = 3.5   # 국채 3년 기준금리 (%)  ← 필요시 수정

_build_lock = threading.Lock()
_is_building = False
_build_progress = {"step": "", "pct": 0}


# ────────────────────────────────────────────
#  유틸
# ────────────────────────────────────────────

def last_biz_day(dt=None):
    """주어진 날짜(또는 오늘)의 가장 최근 평일 반환 (공휴일 미처리)"""
    d = dt if dt else datetime.now()
    for _ in range(10):
        if d.weekday() < 5:
            return d.strftime('%Y%m%d')
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')


def fmt_price(val):
    return f"{int(val):,}원"


def fmt_mktcap(val):
    val = int(val)
    if val >= 10 ** 12:
        jo  = val // 10 ** 12
        eok = (val % 10 ** 12) // 10 ** 8
        return f"{jo}조{eok:,}억" if eok else f"{jo}조"
    if val >= 10 ** 8:
        return f"{val // 10 ** 8:,}억"
    return f"{val // 10 ** 4:,}만"


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_cache(data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


# ────────────────────────────────────────────
#  데이터 수집
# ────────────────────────────────────────────

def fetch_names(today: str) -> dict:
    """종목코드 → 종목명 딕셔너리. FinanceDataReader 우선, 실패 시 pykrx."""
    _build_progress.update(step="종목명 수집 중...", pct=5)
    names = {}
    try:
        import FinanceDataReader as fdr
        for mkt in ('KOSPI', 'KOSDAQ'):
            lst = fdr.StockListing(mkt)
            col = 'Symbol' if 'Symbol' in lst.columns else 'Code'
            nm  = 'Name'   if 'Name'   in lst.columns else 'ISU_NM'
            for _, row in lst.iterrows():
                names[str(row[col]).zfill(6)] = row[nm]
    except Exception:
        # pykrx fallback
        for mkt in ('KOSPI', 'KOSDAQ'):
            try:
                tickers = krx.get_market_ticker_list(today, market=mkt)
                for t in tickers:
                    try:
                        names[t] = krx.get_market_ticker_name(t)
                    except Exception:
                        names[t] = t
            except Exception:
                pass
    return names


def fetch_52w(today: str) -> tuple[dict, dict]:
    """
    주간 샘플링(52회)으로 52주 고가/저가 수집.
    get_market_ohlcv_by_ticker(date, market) 는 해당 하루의 모든 종목 OHLCV 반환.
    """
    _build_progress.update(step="52주 고저 수집 중 (가장 오래 걸립니다)...", pct=25)
    today_dt   = datetime.strptime(today, '%Y%m%d')
    year_ago   = today_dt - timedelta(days=365)
    highs: dict = {}
    lows:  dict = {}

    total_weeks = 52
    done = 0
    d = year_ago
    while d <= today_dt:
        ds = last_biz_day(d)
        for mkt in ('KOSPI', 'KOSDAQ'):
            try:
                ohlcv = krx.get_market_ohlcv_by_ticker(ds, market=mkt)
                if ohlcv.empty:
                    continue
                for tk in ohlcv.index:
                    h = ohlcv.at[tk, '고가']
                    l = ohlcv.at[tk, '저가']
                    if h > 0:
                        highs[tk] = max(highs.get(tk, 0), h)
                        lows[tk]  = min(lows.get(tk, float('inf')), l)
            except Exception:
                pass
        d += timedelta(weeks=1)
        done += 1
        _build_progress.update(
            step=f"52주 고저 수집 중... ({done}/{total_weeks}주)",
            pct=25 + int(done / total_weeks * 40)
        )

    return highs, lows


def build_dataset():
    global _is_building
    with _build_lock:
        if _is_building:
            return None
        _is_building = True

    try:
        today    = last_biz_day()
        year_ago = last_biz_day(datetime.strptime(today, '%Y%m%d') - timedelta(days=365))

        print(f"\n[{datetime.now():%H:%M:%S}] ▶ 데이터 수집 시작 (기준일: {today})")

        # ── 1. 종목명 ──────────────────────────────────
        all_names = fetch_names(today)

        # ── 2. 시가총액 & 현재가 ────────────────────────
        _build_progress.update(step="시가총액 & 현재가 수집 중...", pct=10)
        print(f"  시가총액 수집...")
        cap_kospi  = krx.get_market_cap_by_ticker(today, market='KOSPI')
        cap_kosdaq = krx.get_market_cap_by_ticker(today, market='KOSDAQ')
        cap_df = pd.concat([cap_kospi, cap_kosdaq])

        # ── 3. 펀더멘털 ─────────────────────────────────
        _build_progress.update(step="펀더멘털 (PBR/EPS/BPS/DIV) 수집 중...", pct=15)
        print(f"  펀더멘털 수집...")
        fund_today = krx.get_market_fundamental_by_ticker(today, market='ALL')

        # ── 4. 전년 EPS (YoY) ───────────────────────────
        _build_progress.update(step="전년 EPS 수집 중...", pct=20)
        print(f"  전년 EPS 수집...")
        try:
            fund_prev = krx.get_market_fundamental_by_ticker(year_ago, market='ALL')
        except Exception:
            fund_prev = pd.DataFrame()

        # ── 5. 52주 고저 ────────────────────────────────
        print(f"  52주 고저 수집 (약 2-5분 소요)...")
        highs, lows = fetch_52w(today)

        # ── 6. 데이터 병합 ──────────────────────────────
        _build_progress.update(step="지표 계산 및 랭킹 산정 중...", pct=70)
        print(f"  지표 계산...")

        df = cap_df[['종가', '시가총액']].copy()
        df = df.join(fund_today[['PBR', 'PER', 'EPS', 'BPS', 'DIV']], how='inner')

        # 유효 데이터만
        df = df[(df['종가'] > 100) & (df['PBR'] > 0) & (df['BPS'] > 0)].copy()

        df['name'] = df.index.map(lambda t: all_names.get(t, t))

        cur = df['종가']
        df['high52w'] = df.index.map(lambda t: highs.get(t, cur[t]))
        df['low52w']  = df.index.map(lambda t: lows.get(t,  cur[t] * 0.7))

        rng = (df['high52w'] - df['low52w']).clip(1)
        df['pos52w'] = ((cur - df['low52w']) / rng * 100).clip(0, 100)

        df['ROE']      = (df['EPS'] / df['BPS'] * 100).clip(-200, 300)
        df['divYield'] = df['DIV'].clip(0, 50)
        df['bondRatio']= (df['divYield'] / BOND_YIELD_PCT).clip(0, 20)

        if not fund_prev.empty and 'EPS' in fund_prev.columns:
            prev = fund_prev['EPS'].reindex(df.index).fillna(0)
            mask = prev != 0
            df['epsGrowth'] = 0.0
            df.loc[mask, 'epsGrowth'] = (
                (df.loc[mask, 'EPS'] - prev[mask]) / prev[mask].abs() * 100
            ).clip(-999, 999)
        else:
            df['epsGrowth'] = 0.0

        # RRR: 52주 저가 대비 수익 / 52주 변동폭
        ret  = ((cur - df['low52w']) / df['low52w'].clip(1) * 100).clip(0, 1000)
        risk = ((df['high52w'] - df['low52w']) / df['low52w'].clip(1) * 100).clip(1, 1000)
        df['RRR'] = (ret / risk).clip(0, 1).round(3)

        max_br = df['bondRatio'].quantile(0.99).clip(0.01)
        df['bondScore'] = (df['bondRatio'] / max_br * 100).clip(0, 100).round(0)

        total = len(df)
        print(f"  유효 종목: {total}개")

        # ── 7. 퀀트 점수 ────────────────────────────────
        def pct_rank(s, asc=False):
            return s.rank(ascending=asc, pct=True, method='average') * 100

        df['s_52w']  = pct_rank(df['pos52w'],   asc=True)   # 저가 근처일수록 ↑
        df['s_roe']  = pct_rank(df['ROE'],       asc=False)
        df['s_pbr']  = pct_rank(df['PBR'],       asc=True)   # 낮을수록 ↑
        df['s_div']  = pct_rank(df['divYield'],  asc=False)
        df['s_eps']  = pct_rank(df['epsGrowth'], asc=False)
        df['s_rrr']  = pct_rank(df['RRR'],       asc=False)
        df['s_bond'] = pct_rank(df['bondRatio'], asc=False)

        W = np.array([1.5, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0])
        cols = ['s_52w','s_roe','s_pbr','s_div','s_eps','s_rrr','s_bond']
        df['composite'] = (df[cols] * W).sum(axis=1) / W.sum()
        df['composite'] = df['composite'].round(2)

        df['avgRank']  = df['composite'].rank(ascending=False, method='min').astype(int)
        df['roeRank']  = df['ROE'].rank(ascending=False, method='min').astype(int)
        df['pbrRank']  = df['PBR'].rank(ascending=True,  method='min').astype(int)
        df['bondRank'] = df['bondRatio'].rank(ascending=False, method='min').astype(int)

        # 상대순위 변동 (이전 캐시 비교)
        old_ranks = {}
        cache = load_cache()
        if cache and 'stocks' in cache:
            for s in cache['stocks']:
                old_ranks[s['ticker']] = s.get('avgRank')

        # ── 8. 레코드 생성 ──────────────────────────────
        _build_progress.update(step="결과 저장 중...", pct=90)
        eps_date = f"분기보고서 ({datetime.strptime(today,'%Y%m%d').strftime('%Y.%m')})"

        records = []
        for ticker, row in df.iterrows():
            old_r      = old_ranks.get(ticker)
            rel_change = (old_r - int(row['avgRank'])) if old_r else 0
            rrr_v      = float(row['RRR'])
            rrr_str    = 'MAX' if rrr_v >= 0.85 else str(round(rrr_v * 10, 1))

            records.append({
                'ticker':    ticker,
                'name':      row['name'],
                'price':     fmt_price(row['종가']),
                'mktcap':    fmt_mktcap(row['시가총액']),
                'rrr':       rrr_str,
                'score':     float(row['composite']),
                'avgRank':   int(row['avgRank']),
                'relRank':   abs(rel_change),
                'pos52w':    round(float(row['pos52w']), 1),
                'low52w':    f"{int(row['low52w']):,}",
                'high52w':   f"{int(row['high52w']):,}",
                'roe':       round(float(row['ROE']), 1),
                'roeRank':   int(row['roeRank']),
                'pbr':       round(float(row['PBR']), 2),
                'pbrRank':   int(row['pbrRank']),
                'divYield':  f"{round(float(row['divYield']), 1)}%",
                'bondRatio': f"{round(float(row['bondRatio']), 1)}배",
                'bondScore': int(row['bondScore']),
                'bondRank':  int(row['bondRank']),
                'epsGrowth': f"{round(float(row['epsGrowth']), 1)}%",
                'epsDate':   eps_date,
                'relRankVal':rel_change,
                'total':     total,
            })

        records.sort(key=lambda x: x['avgRank'])

        result = {
            'updated':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'date':      today,
            'total':     total,
            'bondYield': BOND_YIELD_PCT,
            'stocks':    records,
        }
        save_cache(result)
        _build_progress.update(step="완료", pct=100)
        print(f"[{datetime.now():%H:%M:%S}] ✔ 완료! {total}개 종목")
        return result

    except Exception as e:
        _build_progress.update(step=f"오류: {e}", pct=-1)
        traceback.print_exc()
        raise
    finally:
        with _build_lock:
            _is_building = False


# ────────────────────────────────────────────
#  Flask 라우트
# ────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(os.path.dirname(__file__), path)

@app.route('/api/stocks')
def api_stocks():
    cache = load_cache()
    today = last_biz_day()

    # 오늘 캐시가 있으면 즉시 반환
    if cache and cache.get('date') == today:
        return jsonify(cache)

    # 캐시는 있지만 오래됐을 때: 오래된 데이터 반환하면서 백그라운드 갱신
    if cache:
        if not _is_building:
            t = threading.Thread(target=build_dataset, daemon=True)
            t.start()
        return jsonify({**cache, 'stale': True})

    # 캐시 없음: 동기 빌드 (첫 실행)
    data = build_dataset()
    return jsonify(data)

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    if _is_building:
        return jsonify({'status': 'building', 'message': '이미 데이터 수집 중입니다'})
    t = threading.Thread(target=build_dataset, daemon=True)
    t.start()
    return jsonify({'status': 'started'})

@app.route('/api/status')
def api_status():
    cache = load_cache()
    return jsonify({
        'building': _is_building,
        'progress': _build_progress,
        'cached':   cache is not None,
        'updated':  cache.get('updated') if cache else None,
        'total':    cache.get('total',  0) if cache else 0,
        'date':     cache.get('date',  '') if cache else '',
        'today':    last_biz_day(),
    })


if __name__ == '__main__':
    print("=" * 50)
    print("  Value Sheet 퀀트 대시보드 서버")
    print("  http://localhost:5000")
    print("=" * 50)
    # 서버 시작 시 캐시가 없으면 바로 빌드 시작
    if not os.path.exists(CACHE_FILE):
        print("  캐시 없음 → 첫 데이터 수집을 백그라운드에서 시작합니다...")
        t = threading.Thread(target=build_dataset, daemon=True)
        t.start()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)
