-- ============================================================
--  Value Sheet 퀀트 대시보드 - Supabase 스키마
--  Supabase 대시보드 > SQL Editor 에 붙여넣고 Run 하세요
-- ============================================================

-- 주식 데이터 테이블
CREATE TABLE IF NOT EXISTS public.stocks (
  ticker        TEXT PRIMARY KEY,
  name          TEXT,
  price         TEXT,
  mktcap        TEXT,
  rrr           TEXT,
  score         REAL,
  avg_rank      INT,
  rel_rank      INT,
  pos52w        REAL,
  low52w        TEXT,
  high52w       TEXT,
  roe           REAL,
  roe_rank      INT,
  pbr           REAL,
  pbr_rank      INT,
  div_yield     TEXT,
  bond_ratio    TEXT,
  bond_score    INT,
  bond_rank     INT,
  eps_growth    TEXT,
  eps_date      TEXT,
  rel_rank_val  INT,
  total         INT,
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 메타 정보 테이블 (최종 업데이트 시각 등)
CREATE TABLE IF NOT EXISTS public.meta (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Row Level Security ──────────────────────────────────────
-- 누구나 읽기 가능, 쓰기는 service_role(GitHub Actions)만 가능

ALTER TABLE public.stocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.meta   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "stocks_anon_select" ON public.stocks;
DROP POLICY IF EXISTS "meta_anon_select"   ON public.meta;

CREATE POLICY "stocks_anon_select"
  ON public.stocks FOR SELECT TO anon USING (true);

CREATE POLICY "meta_anon_select"
  ON public.meta FOR SELECT TO anon USING (true);

-- ── 인덱스 ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_stocks_avg_rank ON public.stocks (avg_rank);
CREATE INDEX IF NOT EXISTS idx_stocks_score    ON public.stocks (score DESC);
