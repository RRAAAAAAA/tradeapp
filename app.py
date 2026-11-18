"""Run: python app.py            (live MT5, Windows)   |   python app.py --csv XAUUSD_M1.csv   (evaluates the file's last candle)
Dashboard: http://127.0.0.1:5000   Auto-execution is OFF by default and only sends orders when toggled on."""
import json, os, threading, time, pandas as pd
from flask import Flask, jsonify, request, send_from_directory
from data import load_csv, resample, mt5_frames
from strategy import evaluate, CFG

# Config via environment variables so it also runs under gunicorn on Railway:
#   DATA_CSV=path to an M1 CSV (optional)   SYMBOL=XAUUSD
class args: csv = os.environ.get('DATA_CSV'); symbol = os.environ.get('SYMBOL', 'XAUUSD')
app = Flask(__name__)
S = dict(auto=False, risk=CFG['risk_pct'], last=None, log=[], error=None, mode='csv' if args.csv else 'mt5')
state, memo, lock = {'by_day': {}, 'used': set()}, {}, threading.Lock()

def frames():
    if args.csv:
        f = resample(load_csv(args.csv)); n = {'m1': 150, 'm15': 80, 'm30': 100, 'h1': 150, 'h4': 120}
        return {k: v.iloc[-n[k]:] for k, v in f.items()}, CFG['spread']
    return mt5_frames(args.symbol)

def tick():
    F, sp = frames()
    res = {}
    for _ in (0,):
        r = evaluate(F, dict(CFG, risk_pct=S['risk']), state, memo, sp)
    L = r['levels']
    if L:
        state['used'].add(L['sweep_key']); day = r['time'][:10]; state['by_day'][day] = state['by_day'].get(day, 0) + 1
        entry = dict(time=r['time'], **{k: L[k] for k in ('direction', 'entry', 'sl', 'tp1', 'tp2', 'confidence')}, executed=None)
        if S['auto'] and S['mode'] == 'mt5':
            from executor import execute
            entry['executed'] = execute(L, args.symbol, S['risk'])
        S['log'].insert(0, entry); json.dump(S['log'][:200], open('signals.json', 'w'))
    S['last'] = r
    if S['mode'] == 'mt5' and S['auto']:
        from executor import manage_breakeven; manage_breakeven(args.symbol)

def loop():
    while True:
        try:
            with lock: tick(); S['error'] = None
        except Exception as e: S['error'] = str(e)
        time.sleep(10)

@app.get('/')
def index(): return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.get('/api/state')
def api_state():
    last = dict(S['last'] or {}); sig = last.get('levels')
    return jsonify(dict(auto=S['auto'], risk=S['risk'], mode=S['mode'], error=S['error'], last=last, log=S['log'][:30]))

@app.post('/api/settings')
def api_settings():
    j = request.get_json(force=True)
    if 'auto' in j: S['auto'] = bool(j['auto']) and S['mode'] == 'mt5'
    if 'risk' in j: S['risk'] = max(0.1, min(1.0, float(j['risk'])))
    return api_state()

threading.Thread(target=loop, daemon=True).start()   # one background worker (run gunicorn with 1 worker)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
