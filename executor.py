"""MT5 order placement. Refuses non-demo accounts unless ALLOW_LIVE is set in the environment."""
import os, math
MAGIC = 260518

def _lots(mt5, info, balance, risk_pct, sl_dist):
    loss_per_lot = info.trade_tick_value / info.trade_tick_size * sl_dist
    lots = balance * risk_pct / 100 / loss_per_lot
    step = info.volume_step
    return max(info.volume_min, min(info.volume_max, math.floor(lots / step) * step))

def execute(sig, symbol='XAUUSD', risk_pct=0.5):
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and not os.environ.get('ALLOW_LIVE'):
        return "Refused: this is not a demo account. Set ALLOW_LIVE=1 only after your demo results hold up."
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    vol = _lots(mt5, info, acc.balance, risk_pct, abs(sig['entry'] - sig['sl']))
    buy = sig['direction'] == 'BUY'
    legs = [(round(vol / 2 / info.volume_step) * info.volume_step, sig['tp1']), (vol - round(vol / 2 / info.volume_step) * info.volume_step, sig['tp2'])]
    if legs[0][0] < info.volume_min or legs[1][0] < info.volume_min: legs = [(vol, sig['tp2'])]
    msgs = []
    for v, tp in legs:
        r = mt5.order_send(dict(action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=round(v, 2),
            type=mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, price=tick.ask if buy else tick.bid,
            sl=round(sig['sl'], 2), tp=round(tp, 2), deviation=20, magic=MAGIC, comment=('goldbot-s' if len(legs)==1 else 'goldbot-1' if tp==sig['tp1'] else 'goldbot-2'),
            type_time=mt5.ORDER_TIME_GTC, type_filling=mt5.ORDER_FILLING_IOC))
        msgs.append(f"{v:.2f} lots -> retcode {r.retcode}")
    return "; ".join(msgs)

def manage_breakeven(symbol='XAUUSD'):
    """If only the TP2 leg of a pair remains open, move its stop to entry."""
    import MetaTrader5 as mt5
    pos = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]
    if any(p.comment == 'goldbot-1' for p in pos): return
    for p in pos:
        be = p.price_open
        if p.comment == 'goldbot-2' and ((p.type == 0 and p.sl < be) or (p.type == 1 and p.sl > be)):
            mt5.order_send(dict(action=mt5.TRADE_ACTION_SLTP, position=p.ticket, symbol=symbol, sl=be, tp=p.tp))
