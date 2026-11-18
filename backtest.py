"""Usage: python backtest.py XAUUSD_M1.csv [--spread 0.3] [--out trades.csv]
Entry at the NEXT M1 open after the signal. If SL and TP hit in the same candle, SL is assumed first.
Half closes at TP1 (stop moves to breakeven), the rest runs to TP2. Results are in R multiples."""
import sys, argparse, numpy as np, pandas as pd
from data import load_csv, resample, TF
from strategy import evaluate, CFG

def run(m1, cfg=CFG, warm=400):
    fr = resample(m1)
    ends = {k: (fr[k].index + pd.Timedelta(minutes=TF[k])).values for k in TF}
    N = {'h4': 120, 'h1': 150, 'm30': 100, 'm15': 80}
    state, memo, trades = {'by_day': {}, 'used': set()}, {}, []
    idx, O, Hh, Ll = m1.index, m1.open.values, m1.high.values, m1.low.values
    i, n = warm, len(m1)
    while i < n - 2:
        t_end = idx[i] + pd.Timedelta(minutes=1)
        F = {'m1': m1.iloc[i - 120:i + 1]}
        ok = True
        for k in TF:
            c = np.searchsorted(ends[k], np.datetime64(t_end), side='right')
            if c < 60: ok = False; break
            F[k] = fr[k].iloc[max(0, c - N[k]):c]
        if not ok: i += 1; continue
        r = evaluate(F, cfg, state, memo)
        L = r['levels']
        if not L: i += 1; continue
        state['used'].add(L['sweep_key'])
        day = str(t_end.date()); state['by_day'][day] = state['by_day'].get(day, 0) + 1
        buy = L['direction'] == 'BUY'
        entry, sl, tp1, tp2, risk = L['entry'], L['sl'], L['tp1'], L['tp2'], abs(L['entry'] - L['sl'])
        stop, half, R, j = sl, False, 0.0, i + 1
        # fill at next open; spread is already inside 'entry' (conservative)
        while j < n:
            lo, hi = Ll[j], Hh[j]
            hit_sl = lo <= stop if buy else hi >= stop
            hit1 = hi >= tp1 if buy else lo <= tp1
            hit2 = hi >= tp2 if buy else lo <= tp2
            if hit_sl:
                R += (0.5 if half else 1.0) * ((stop - entry) / risk if buy else (entry - stop) / risk); break
            if not half and hit1: half, R, stop = True, R + 0.5 * L['rr1'], entry
            if half and hit2: R += 0.5 * (abs(tp2 - entry) / risk); break
            j += 1
        trades.append(dict(time=str(idx[i]), side=L['direction'], entry=entry, sl=sl, tp1=tp1, tp2=tp2, R=round(R, 3),
                           conf=L['confidence'], bars=j - i))
        i = j + 1
    return pd.DataFrame(trades)

def report(t):
    if t.empty: return "No trades."
    w, l = t[t.R > 0].R.sum(), -t[t.R < 0].R.sum()
    eq = t.R.cumsum()
    return (f"Trades {len(t)} | win rate {(t.R > 0).mean():.0%} | avg R {t.R.mean():.2f} | total R {t.R.sum():.1f} | "
            f"profit factor {w / l if l else float('inf'):.2f} | max drawdown {(eq.cummax() - eq).max():.1f}R")

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('csv'); ap.add_argument('--spread', type=float, default=CFG['spread'])
    ap.add_argument('--out', default='trades.csv'); a = ap.parse_args()
    cfg = dict(CFG, spread=a.spread)
    t = run(load_csv(a.csv), cfg); t.to_csv(a.out, index=False); print(report(t))
