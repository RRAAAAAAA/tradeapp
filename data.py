import pandas as pd
TF = {'m15': 15, 'm30': 30, 'h1': 60, 'h4': 240}

def load_csv(path):
    """MT5 export (<DATE>,<TIME>,<OPEN>...) or any CSV with a time column + open/high/low/close."""
    df = pd.read_csv(path, sep=None, engine='python')
    df.columns = [c.strip('<> ').lower() for c in df.columns]
    if 'date' in df and 'time' in df: ts = pd.to_datetime(df['date'].astype(str) + ' ' + df['time'].astype(str))
    else: ts = pd.to_datetime(df[next(c for c in ('datetime', 'timestamp', 'time') if c in df)])
    out = df.assign(t=ts).set_index('t')[['open', 'high', 'low', 'close']].astype(float).sort_index()
    out.index.name = 'time'
    return out[~out.index.duplicated()]

def resample(m1):
    out = {'m1': m1}
    for k, m in TF.items():
        out[k] = m1.resample(f'{m}min', label='left', closed='left').agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    return out

def mt5_frames(symbol='XAUUSD', n=400):
    """Closed candles only (the forming bar is dropped). Requires Windows + MetaTrader5 package."""
    import MetaTrader5 as mt5
    if not mt5.initialize(): raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    tfs = {'m1': mt5.TIMEFRAME_M1, 'm15': mt5.TIMEFRAME_M15, 'm30': mt5.TIMEFRAME_M30, 'h1': mt5.TIMEFRAME_H1, 'h4': mt5.TIMEFRAME_H4}
    out = {}
    for k, tf in tfs.items():
        r = mt5.copy_rates_from_pos(symbol, tf, 0, n + 1)
        if r is None: raise RuntimeError(f"No {k} data for {symbol}")
        df = pd.DataFrame(r); df['time'] = pd.to_datetime(df['time'], unit='s')
        out[k] = df.set_index('time')[['open', 'high', 'low', 'close']].iloc[:-1]
    tick = mt5.symbol_info_tick(symbol)
    return out, float(tick.ask - tick.bid)
