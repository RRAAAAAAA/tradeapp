# Gold desk (XAUUSD multi-timeframe signal tool)

Rules-based: H4 bias, H1 zone, M15 liquidity sweep, M1 break-of-structure entry, ATR-based SL, 1:1.5 TP1 and 1:2+ TP2.
Every signal comes with a checklist, a written explanation, and a "why not" note when nothing fires.

## Setup
    pip install -r requirements.txt

## 1. Backtest first
In MT5 export M1 history for XAUUSD (Tools > History Center > Export, or any CSV with time/open/high/low/close). Then:

    python backtest.py XAUUSD_M1.csv --spread 0.30

Writes trades.csv and prints win rate, avg R, profit factor and max drawdown. Use at least 6-12 months.

## 2. Dashboard
    python app.py --csv XAUUSD_M1.csv     # evaluates the file's latest candle (testing)
    python app.py                         # live MT5 data (Windows, terminal open and logged in)
Open http://127.0.0.1:5000. The auto-execute toggle is off by default, only works in live MT5 mode,
and refuses non-demo accounts unless ALLOW_LIVE=1 is set.

## Settings to review in strategy.py (CFG)
- `server_utc_offset`: your broker's server time minus UTC (the session filter depends on it).
- `news_blackouts`: add (start, end) timestamps around CPI/NFP/FOMC.
- `spread`, `max_spread`: in dollars per ounce.

## Known simplifications
- Spread is applied to entry for both sides; no slippage or commission.
- Same-candle SL and TP hits count as SL.
- Backtest ignores news filtering unless you add blackouts.
- Signals are decision support, not financial advice.
