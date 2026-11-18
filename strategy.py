"""Rules-based XAUUSD multi-timeframe engine (H4/H1/M30 context, M15 setup, M1 trigger).
Input: dict of CLOSED-candle DataFrames {'m1','m15','m30','h1','h4'} (cols open/high/low/close).
Shorts are handled by mirroring prices (flip) so one 'bull' pipeline serves both sides."""
import numpy as np, pandas as pd

CFG = dict(
    swing_k=2, ema_len=50, atr_len=14, zone_atr=1.0, sweep_lookback=8, swing_lookback=20,
    sl_buffer_atr=0.25, max_sl_atr=1.5, tp1_rr=1.5, tp2_min_rr=2.0,
    spread=0.30, max_spread=0.60,            # in price units (USD per oz)
    session_utc=(7, 21), server_utc_offset=0,  # broker time = UTC + offset hours
    max_trades_day=2, news_blackouts=[],       # list of (start, end) pd.Timestamps in data time
    risk_pct=0.5)

CHECKS = ["Session", "No high-impact news", "Spread", "Daily trade cap", "H4 bias", "H1 agrees",
          "M30 not opposing", "H1 zone", "M15 sweep", "M1 break of structure", "SL size", "Risk-reward"]

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def flip(df):
    return pd.DataFrame({'open': -df.open, 'high': -df.low, 'low': -df.high, 'close': -df.close}, index=df.index)

def swings(df, k=2):
    h, l = df.high.values, df.low.values
    H, L = [], []
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max(): H.append((i, h[i]))
        if l[i] == l[i - k:i + k + 1].min(): L.append((i, l[i]))
    return H, L

def structure(df, k=2):
    H, L = swings(df, k)
    if len(H) < 2 or len(L) < 2: return 'neutral', H, L
    if H[-1][1] > H[-2][1] and L[-1][1] > L[-2][1]: return 'bull', H, L
    if H[-1][1] < H[-2][1] and L[-1][1] < L[-2][1]: return 'bear', H, L
    return 'neutral', H, L

def evaluate(F, cfg=CFG, state=None, memo=None, spread=None):
    state = state if state is not None else {'by_day': {}, 'used': set()}
    memo = memo if memo is not None else {}
    now = F['m1'].index[-1] + pd.Timedelta(minutes=1)
    sp = cfg['spread'] if spread is None else spread
    k = cfg['swing_k']
    checks = []

    def add(name, ok, detail):
        checks.append(dict(name=name, ok=bool(ok), detail=detail)); return bool(ok)

    def finish(levels=None):
        done = {c['name'] for c in checks}
        for n in CHECKS:
            if n not in done: checks.append(dict(name=n, ok=None, detail='Not evaluated because an earlier check failed.'))
        checks.sort(key=lambda c: CHECKS.index(c['name']))
        res = dict(time=str(now), signal=levels['direction'] if levels else None, checks=checks, levels=levels)
        res['why_not'] = None if levels else _why(checks)
        res['explanation'] = _explain(checks, levels) if levels else None
        return res

    # ---- filters
    hr = (now - pd.Timedelta(hours=cfg['server_utc_offset'])).hour
    s0, s1 = cfg['session_utc']
    if not add("Session", s0 <= hr < s1, f"{hr:02d}:00 UTC; trading window is {s0:02d}:00-{s1:02d}:00 UTC (London and New York)."): return finish()
    if not add("No high-impact news", not any(a <= now <= b for a, b in cfg['news_blackouts']), "No news blackout window is active." if not any(a <= now <= b for a, b in cfg['news_blackouts']) else "Inside a news blackout window."): return finish()
    if not add("Spread", sp <= cfg['max_spread'], f"Spread {sp:.2f} vs limit {cfg['max_spread']:.2f}."): return finish()
    tt = state['by_day'].get(str(now.date()), 0)
    if not add("Daily trade cap", tt < cfg['max_trades_day'], f"{tt} of {cfg['max_trades_day']} trades taken today."): return finish()

    # ---- higher-timeframe context (cached per H4/H1/M30 bar)
    key = ('htf', F['h4'].index[-1], F['h1'].index[-1], F['m30'].index[-1])
    if key not in memo: memo[key] = _htf(F, cfg)
    htf = memo[key]
    checks.extend(htf['checks'])
    if htf['bias'] == 0 or not all(c['ok'] for c in htf['checks']): return finish()
    d = htf['bias']
    G = F if d == 1 else {n: flip(v) for n, v in F.items()}   # G is always a 'buy' world
    h1, m15, m1 = G['h1'], G['m15'], G['m1']
    c = m1.close.iloc[-1]

    # ---- H1 zone: discount of the latest H1 range, zone around its swing low
    _, H, L = structure(h1, k)
    Hw, Lw = H[-1][1], L[-1][1]
    if Hw <= Lw: add("H1 zone", False, "Latest H1 swing range is invalid (low above high)."); return finish()
    eq = (Hw + Lw) / 2
    a1 = atr(h1, cfg['atr_len']).iloc[-1]
    zt = min(eq, Lw + cfg['zone_atr'] * a1)
    touched = m15.low.iloc[-cfg['sweep_lookback']:].min() <= zt
    real = lambda x: d * x
    zr = sorted([real(Lw), real(zt)])
    if not add("H1 zone", touched and c <= eq,
               f"H1 range {sorted([real(Lw), real(Hw)])[0]:.2f}-{sorted([real(Lw), real(Hw)])[1]:.2f}, midpoint {real(eq):.2f}. "
               f"{'Discount' if d == 1 else 'Premium'} zone {zr[0]:.2f}-{zr[1]:.2f}. "
               f"Zone {'touched' if touched else 'not touched'} in the last {cfg['sweep_lookback']} M15 bars; price {'on the right side of' if c <= eq else 'beyond'} the midpoint."): return finish()

    # ---- M15 liquidity sweep
    skey = ('sw', m15.index[-1], d, round(zt, 3))
    if skey not in memo:
        found = None
        n = len(m15)
        for j in range(n - 1, n - 1 - cfg['sweep_lookback'], -1):
            _, Ls = swings(m15.iloc[max(0, j - cfg['swing_lookback']):j], k)
            if not Ls: continue
            ref, lo = Ls[-1][1], m15.low.iloc[j]
            if lo < ref and m15.close.iloc[j] > ref and lo <= zt: found = (j, ref); break
        memo[skey] = found
    sw = memo[skey]
    if sw is None: add("M15 sweep", False, "No M15 candle swept a prior swing and closed back inside the zone in the last %d bars." % cfg['sweep_lookback']); return finish()
    j, ref = sw
    t0, sbar = m15.index[j], m15.iloc[j]
    sweep_key = f"{t0}|{d}"
    if not add("M15 sweep", sweep_key not in state['used'],
               f"M15 bar {t0:%H:%M} wicked to {real(sbar.low):.2f} through the swing at {real(ref):.2f} and closed back at {real(sbar.close):.2f}." +
               ("" if sweep_key not in state['used'] else " This sweep was already traded.")): return finish()

    # ---- M1 break of structure
    seg = m1[m1.index >= t0]
    p = int(np.argmin(seg.low.values)) if len(seg) else 0
    if len(seg) < 3 or p >= len(seg) - 1:
        add("M1 break of structure", False, "Price is still making new lows on M1 after the sweep; no bounce to break yet."); return finish()
    sh = seg.high.values[p:-1].max()
    if not add("M1 break of structure", c > sh, f"M1 close {real(c):.2f} vs minor {'high' if d == 1 else 'low'} {real(sh):.2f} formed after the sweep low."): return finish()

    # ---- levels
    a15 = atr(m15, cfg['atr_len']).iloc[-1]
    entry = c + sp
    ext = min(sbar.low, seg.low.min())
    sl = ext - (cfg['sl_buffer_atr'] * a15 + sp)
    risk = entry - sl
    if not add("SL size", 0 < risk <= cfg['max_sl_atr'] * a15, f"Risk {risk:.2f} vs max {cfg['max_sl_atr'] * a15:.2f} (1.5 x M15 ATR {a15:.2f})."): return finish()
    cands = sorted([p_ for fr in (G['h1'], G['h4']) for _, p_ in swings(fr, k)[0] if p_ > entry])
    tp2 = next((t for t in cands if t - entry >= cfg['tp2_min_rr'] * risk), None)
    src = "next H1/H4 swing high" if d == 1 else "next H1/H4 swing low"
    if tp2 is None: tp2, src = entry + cfg['tp2_min_rr'] * risk, f"fixed {cfg['tp2_min_rr']:.1f}R (no structural target far enough)"
    tp1 = entry + cfg['tp1_rr'] * risk
    rr2 = (tp2 - entry) / risk
    add("Risk-reward", rr2 >= cfg['tp2_min_rr'], f"TP1 at 1:{cfg['tp1_rr']:.1f} (half off, stop to breakeven); TP2 at 1:{rr2:.1f} using {src}.")

    soft = dict(m30_aligned=htf['m30_aligned'], structural_target=not src.startswith('fixed'),
                rejection_close=bool(sbar.close >= (sbar.high + sbar.low) / 2))
    levels = dict(direction='BUY' if d == 1 else 'SELL', entry=real(entry), sl=real(sl), tp1=real(tp1), tp2=real(tp2),
                  risk=risk, rr1=cfg['tp1_rr'], rr2=rr2, atr_m15=a15, sweep_key=sweep_key, soft=soft,
                  confidence={3: 'A', 2: 'B'}.get(sum(soft.values()), 'C'), source=src,
                  invalidation=(f"An M15 close {'below' if d == 1 else 'above'} {real(ext):.2f} (the sweep extreme), "
                                f"or the H4 bias flipping before entry."))
    return finish(levels)

def _htf(F, cfg):
    k, out = cfg['swing_k'], []
    def add(name, ok, detail): out.append(dict(name=name, ok=bool(ok), detail=detail)); return bool(ok)
    h4, h1, m30 = F['h4'], F['h1'], F['m30']
    s4, _, _ = structure(h4, k)
    e, c = ema(h4.close, cfg['ema_len']).iloc[-1], h4.close.iloc[-1]
    bias = 1 if (s4 == 'bull' and c > e) else -1 if (s4 == 'bear' and c < e) else 0
    add("H4 bias", bias != 0, f"H4 structure is {s4}; close {c:.2f} is {'above' if c > e else 'below'} the 50 EMA ({e:.2f}). " +
        ({1: "Bias: bullish (higher highs and higher lows).", -1: "Bias: bearish (lower highs and lower lows).", 0: "Bias: neutral, so no trades."}[bias]))
    res = dict(checks=out, bias=bias, m30_aligned=False)
    if bias == 0: return res
    s1, _, _ = structure(h1, k)
    want = 'bull' if bias == 1 else 'bear'
    if not add("H1 agrees", s1 == want, f"H1 structure is {s1}; needs {want}."): return res
    s30, _, _ = structure(m30, k)
    opp = 'bear' if bias == 1 else 'bull'
    res['m30_aligned'] = s30 == want
    add("M30 not opposing", s30 != opp, f"M30 structure is {s30}.")
    return res

def _why(checks):
    bad = next(c for c in checks if c['ok'] is False)
    passed = [c['name'] for c in checks if c['ok']]
    return f"Stopped at '{bad['name']}': {bad['detail']} Passed so far: {', '.join(passed) if passed else 'nothing'}."

def _explain(checks, L):
    det = {c['name']: c['detail'] for c in checks}
    side = L['direction']
    return "\n\n".join([
        f"{side} signal, confidence {L['confidence']} ({sum(L['soft'].values())}/3 optional confirmations).",
        "1. Bias. " + det["H4 bias"] + " " + det["H1 agrees"] + " " + det["M30 not opposing"],
        "2. Setup. " + det["H1 zone"] + " " + det["M15 sweep"],
        "3. Entry trigger. " + det["M1 break of structure"] + f" Entry about {L['entry']:.2f} (spread included).",
        f"4. Stop and targets. Stop {L['sl']:.2f} sits beyond the sweep extreme plus 0.25 x M15 ATR and spread ({det['SL size']}) " + det["Risk-reward"],
        "5. Invalidation. " + L['invalidation'],
        "Decision support only. Backtest and demo-test before risking money."])
