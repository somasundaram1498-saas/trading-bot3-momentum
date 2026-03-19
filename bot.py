"""
Bot 3 — Momentum Stocks Bot
Scans: Top 30 momentum stocks
Equity signals only — no options
Every day, score 5/6 minimum
"""
import os, asyncio, logging
import pandas as pd
import numpy as np
from datetime import datetime, time, date, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():  return datetime.now(IST)
def time_ist(): return now_ist().time()
def date_ist(): return now_ist().date()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANGEL_API_KEY    = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID  = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PASSWORD   = os.getenv("ANGEL_PASSWORD", "")
ANGEL_TOTP       = os.getenv("ANGEL_TOTP_SECRET", "")
PAPER_MODE       = True

smart_api   = None
angel_ready = False
angel_error = "Not attempted"

def connect_angel():
    global smart_api, angel_ready, angel_error
    try:
        if not all([ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP]):
            angel_error = f"Missing creds API={bool(ANGEL_API_KEY)} CID={bool(ANGEL_CLIENT_ID)}"
            return False
        import pyotp
        from SmartApi import SmartConnect
        totp_code = pyotp.TOTP(ANGEL_TOTP).now()
        obj  = SmartConnect(api_key=ANGEL_API_KEY)
        data = obj.generateSession(ANGEL_CLIENT_ID, ANGEL_PASSWORD, totp_code)
        if data and data.get("status"):
            smart_api   = obj
            angel_ready = True
            angel_error = "OK"
            logger.info("Angel One connected!")
            return True
        angel_error = f"Login failed: {data}"
    except Exception as e:
        angel_error = f"Error: {e}"
    logger.error(f"Angel connect failed: {angel_error}")
    return False

# ── Top 30 Momentum Stocks ────────────────────────────────────────────────────
MOMENTUM_STOCKS = {
    "RELIANCE": {"token":"2885",  "exchange":"NSE"},
    "TCS":      {"token":"11536", "exchange":"NSE"},
    "HDFCBANK": {"token":"1333",  "exchange":"NSE"},
    "INFY":     {"token":"1594",  "exchange":"NSE"},
    "ICICIBANK":{"token":"4963",  "exchange":"NSE"},
    "SBIN":     {"token":"3045",  "exchange":"NSE"},
    "LT":       {"token":"11483", "exchange":"NSE"},
    "BAJFINANCE":{"token":"317",  "exchange":"NSE"},
    "AXISBANK": {"token":"5900",  "exchange":"NSE"},
    "KOTAKBANK":{"token":"1922",  "exchange":"NSE"},
    "TATAMOTORS":{"token":"3456", "exchange":"NSE"},
    "ADANIENT": {"token":"25",    "exchange":"NSE"},
    "ZOMATO":   {"token":"5097",  "exchange":"NSE"},
    "IRCTC":    {"token":"13611", "exchange":"NSE"},
    "TATAPOWER":{"token":"3426",  "exchange":"NSE"},
    "BHARTIARTL":{"token":"10604","exchange":"NSE"},
    "TITAN":    {"token":"3506",  "exchange":"NSE"},
    "MARUTI":   {"token":"10999", "exchange":"NSE"},
    "EICHERMOT":{"token":"910",   "exchange":"NSE"},
    "INDIGO":   {"token":"11195", "exchange":"NSE"},
    "NAUKRI":   {"token":"13751", "exchange":"NSE"},
    "PERSISTENT":{"token":"18365","exchange":"NSE"},
    "COFORGE":  {"token":"23650", "exchange":"NSE"},
    "KPITTECH": {"token":"4453",  "exchange":"NSE"},
    "TATAELXSI":{"token":"3453",  "exchange":"NSE"},
    "VOLTAS":   {"token":"3597",  "exchange":"NSE"},
    "POLYCAB":  {"token":"23650", "exchange":"NSE"},
    "HAVELLS":  {"token":"430",   "exchange":"NSE"},
    "BERGEPAINT":{"token":"404",  "exchange":"NSE"},
    "TRENT":    {"token":"3513",  "exchange":"NSE"},
}

# ── Settings ──────────────────────────────────────────────────────────────────
TRADE_START    = time(10, 0)
TRADE_END      = time(15, 15)
SQUAREOFF_TIME = time(15, 15)
TARGET_MULT    = 2.0
VIX_HIGH       = 20.0
VIX_TOKEN      = "99919000"
VIX_SYMBOL     = "India VIX"

pending_signals  = {}
paper_positions  = {}
active_positions = {}
paper_trades     = []
daily_trades     = []
squaredoff_today = False

def is_trading_time():
    n = now_ist()
    return n.weekday() < 5 and TRADE_START <= n.time() <= TRADE_END

def get_stock_qty(ltp):
    if ltp<=100: return 50
    elif ltp<=250: return 25
    elif ltp<=500: return 20
    elif ltp<=1000: return 10
    elif ltp<=2000: return 5
    elif ltp<=5000: return 2
    return 1

# ── Data ──────────────────────────────────────────────────────────────────────
def get_ltp(token, exchange, symbol):
    if not angel_ready or smart_api is None: return None
    try:
        r = smart_api.ltpData(exchange, symbol, token)
        if r and r.get("status") and r.get("data"):
            return float(r["data"]["ltp"])
    except Exception as e:
        logger.error(f"LTP [{symbol}]: {e}")
    return None

def fetch_candles(token, exchange, symbol):
    if not angel_ready or smart_api is None: return None
    try:
        today     = date_ist()
        from_date = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        resp = smart_api.getCandleData({
            "exchange": exchange, "symboltoken": str(token),
            "interval": "FIVE_MINUTE",
            "fromdate": f"{from_date} 09:15",
            "todate":   now_ist().strftime("%Y-%m-%d %H:%M"),
        })
        if not resp or not resp.get("status") or not resp.get("data"): return None
        df = pd.DataFrame(resp["data"], columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").astype(float)
        return df if len(df) >= 20 else None
    except Exception as e:
        logger.error(f"Candle [{symbol}]: {e}")
        return None

def resample_tf(df, tf):
    return df.resample(tf).agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()

def place_order(token, exchange, symbol, txn_type, quantity):
    if PAPER_MODE:
        return {"status":True,"data":{"orderid":f"PAPER-{int(now_ist().timestamp())}"}}
    if not angel_ready or smart_api is None:
        return {"status":False,"message":"Not connected"}
    try:
        return smart_api.placeOrder({
            "variety":"NORMAL","tradingsymbol":symbol,"symboltoken":token,
            "transactiontype":txn_type,"exchange":exchange,"ordertype":"MARKET",
            "producttype":"INTRADAY","duration":"DAY","quantity":str(quantity),"price":"0",
        })
    except Exception as e:
        return {"status":False,"message":str(e)}

# ── Indicators ────────────────────────────────────────────────────────────────
def calc_supertrend(df, period=10, mult=3.0):
    h,l,c = df["high"],df["low"],df["close"]
    hl2   = (h+l)/2
    tr    = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr   = tr.ewm(span=period,adjust=False).mean()
    upper = hl2+mult*atr; lower = hl2-mult*atr
    d = pd.Series(0,index=df.index,dtype=int)
    for i in range(1,len(df)):
        if c.iloc[i]>upper.iloc[i-1]:   d.iloc[i]=1
        elif c.iloc[i]<lower.iloc[i-1]: d.iloc[i]=-1
        else:                            d.iloc[i]=d.iloc[i-1]
    return d

def calc_vwap(df):
    tp = (df["high"]+df["low"]+df["close"])/3
    return (tp*df["volume"]).cumsum()/df["volume"].cumsum()

def calc_adx(df, period=14):
    h,l,c = df["high"],df["low"],df["close"]
    up,dn = h.diff(),-l.diff()
    pdm = pd.Series(np.where((up>dn)&(up>0),up,0.0),index=df.index)
    ndm = pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=df.index)
    tr  = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.ewm(span=period,adjust=False).mean()
    pdi = 100*pdm.ewm(span=period,adjust=False).mean()/atr
    ndi = 100*ndm.ewm(span=period,adjust=False).mean()/atr
    dx  = 100*(pdi-ndi).abs()/(pdi+ndi).replace(0,np.nan)
    return dx.ewm(span=period,adjust=False).mean(),pdi,ndi

def calc_rsi(df,period=14):
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(span=period,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(span=period,adjust=False).mean()
    return 100-(100/(1+g/l.replace(0,np.nan)))

def calc_macd(df):
    fast=df["close"].ewm(span=12,adjust=False).mean()
    slow=df["close"].ewm(span=26,adjust=False).mean()
    m=fast-slow; s=m.ewm(span=9,adjust=False).mean()
    return m,s

def get_signal(token, exchange, symbol):
    raw = fetch_candles(token, exchange, symbol)
    if raw is None or len(raw)<20: return None
    df5=raw; df15=resample_tf(raw,"15min")
    if len(df15)<5 or len(df5)<10: return None
    try:
        t15=int(calc_supertrend(df15).iloc[-1]); d5=int(calc_supertrend(df5).iloc[-1])
        close5=float(df5["close"].iloc[-1]); vwap_v=float(calc_vwap(df5).iloc[-1])
        adx_s,_,_=calc_adx(df15); adx_v=float(adx_s.iloc[-1])
        rsi_v=float(calc_rsi(df5).iloc[-1]); mc,ms=calc_macd(df5)
        macd_ab=float(mc.iloc[-1])>float(ms.iloc[-1]); macd_bl=float(mc.iloc[-1])<float(ms.iloc[-1])
        buy_s=sum([t15==1,d5==1,close5>vwap_v,macd_ab,rsi_v<70,adx_v>20])
        sel_s=sum([t15==-1,d5==-1,close5<vwap_v,macd_bl,rsi_v>30,adx_v>20])
        signal="BUY" if buy_s>=5 else ("SELL" if sel_s>=5 else None)
        return {"signal":signal,"close":close5,"adx":adx_v,"rsi":rsi_v,
                "buy_score":buy_s,"sell_score":sel_s}
    except Exception as e:
        logger.error(f"Signal [{symbol}]: {e}")
        return None

# ── Scanner ───────────────────────────────────────────────────────────────────
async def scan_and_alert(app):
    if not is_trading_time(): return
    if not angel_ready:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,
            text=f"BOT3 MOMENTUM: Not connected\n{angel_error}\nSend /reconnect3"); return

    vix_val = get_ltp(VIX_TOKEN,"NSE",VIX_SYMBOL) or 0.0
    hv      = vix_val > VIX_HIGH
    done    = set()

    for sym, info in MOMENTUM_STOCKS.items():
        if sym in done: continue
        result = get_signal(info["token"], info["exchange"], sym)
        if not result or not result["signal"]: continue

        ltp    = result["close"]
        signal = result["signal"]
        qty    = get_stock_qty(ltp)
        sl_pct = 0.015 if hv else 0.01
        sl_pts = round(ltp*sl_pct, 2)
        sl_p   = round(ltp-sl_pts if signal=="BUY" else ltp+sl_pts, 2)
        tgt_p  = round(ltp+sl_pts*TARGET_MULT if signal=="BUY" else ltp-sl_pts*TARGET_MULT, 2)
        score  = result["buy_score"] if signal=="BUY" else result["sell_score"]
        done.add(sym)

        key = f"MOM_{sym}_{signal}_{int(now_ist().timestamp())}"
        pending_signals[key] = {
            "symbol":sym,
            "legs":[{"token":info["token"],"symbol":sym,"exchange":info["exchange"],
                     "action":signal,"ltp":ltp,"sl":sl_p,"target":tgt_p,
                     "quantity":qty,"trailing":False}],
            "signal":signal
        }
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve",callback_data=f"approve_{key}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{key}")
        ]])
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,parse_mode="Markdown",reply_markup=kb,
            text=f"*BOT3 MOMENTUM | {signal} - {sym}*\nLTP:Rs{ltp:.2f} Qty:{qty}\nSL:Rs{sl_p:.2f} ({int(sl_pct*100)}%) T:Rs{tgt_p:.2f}\nADX:{result['adx']:.1f} RSI:{result['rsi']:.1f} Score:{score}/6\nVIX:{vix_val:.1f}{'  HIGH VIX' if hv else ''}\n\nTap Approve to paper trade")
        await asyncio.sleep(0.3)

# ── Monitor ───────────────────────────────────────────────────────────────────
async def monitor_positions(app, positions, is_paper=True):
    if not positions or not angel_ready: return
    to_close=[]
    for key,trade in list(positions.items()):
        for leg in trade.get("legs",[]):
            ltp=get_ltp(leg["token"],leg["exchange"],leg["symbol"])
            if ltp is None: continue
            entry=leg["ltp"]; sl=leg["sl"]; tgt=leg["target"]; action=leg["action"]; qty=leg["quantity"]
            pnl=(ltp-entry)*qty if action=="BUY" else (entry-ltp)*qty
            sl_hit=(action=="BUY" and ltp<=sl) or (action=="SELL" and ltp>=sl)
            tgt_hit=(action=="BUY" and ltp>=tgt) or (action=="SELL" and ltp<=tgt)
            if sl_hit: to_close.append((key,"SL_HIT",ltp,pnl,leg))
            elif tgt_hit and not leg.get("trailing"):
                leg["trailing"]=True; leg["sl"]=entry
                new_tgt=round(ltp+(tgt-entry) if action=="BUY" else ltp-(entry-tgt),2); leg["target"]=new_tgt
                tag="PAPER: " if is_paper else ""
                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,parse_mode="Markdown",
                    text=f"*BOT3 {tag}Target Hit - Trailing!*\n{leg['symbol']}\nLTP:Rs{ltp:.2f} SL->Rs{entry:.2f} T:Rs{new_tgt:.2f} P&L:Rs{pnl:.2f}")
            elif tgt_hit and leg.get("trailing"): to_close.append((key,"TARGET_HIT",ltp,pnl,leg))
    for key,reason,exit_price,pnl,leg in to_close:
        positions.pop(key,None)
        if not is_paper:
            exit_txn="SELL" if leg["action"]=="BUY" else "BUY"
            place_order(leg["token"],leg["exchange"],leg["symbol"],exit_txn,leg["quantity"])
        tag="PAPER " if is_paper else ""
        outcome="Target Hit" if reason=="TARGET_HIT" else "SL Hit"
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,parse_mode="Markdown",
            text=f"*BOT3 {tag}Closed - {outcome}*\n{leg['symbol']}\nEntry:Rs{leg['ltp']:.2f} Exit:Rs{exit_price:.2f} P&L:Rs{pnl:.2f}")
        record={"symbol":leg["symbol"],"action":leg["action"],"entry":leg["ltp"],"exit":exit_price,"pnl":pnl,"reason":reason}
        (paper_trades if is_paper else daily_trades).append(record)

async def square_off_all(app):
    global squaredoff_today
    if squaredoff_today: return
    squaredoff_today=True
    for positions,is_paper,label in [(paper_positions,True,"PAPER"),(active_positions,False,"REAL")]:
        if not positions: continue
        total=0.0
        for key,trade in list(positions.items()):
            for leg in trade.get("legs",[]):
                ltp=get_ltp(leg["token"],leg["exchange"],leg["symbol"]) or leg["ltp"]
                if not is_paper:
                    exit_txn="SELL" if leg["action"]=="BUY" else "BUY"
                    place_order(leg["token"],leg["exchange"],leg["symbol"],exit_txn,leg["quantity"])
                pnl=(ltp-leg["ltp"])*leg["quantity"] if leg["action"]=="BUY" else (leg["ltp"]-ltp)*leg["quantity"]
                total+=pnl
                (paper_trades if is_paper else daily_trades).append({"symbol":leg["symbol"],"action":leg["action"],"entry":leg["ltp"],"exit":ltp,"pnl":pnl,"reason":"SQUAREOFF"})
            positions.pop(key,None)
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID,parse_mode="Markdown",
            text=f"*BOT3 {label} Square-Off*\nP&L: Rs{total:.2f}")

async def send_pnl(target, is_update=True):
    lines=[f"*BOT3 MOMENTUM P&L - {now_ist().strftime('%I:%M %p')}*\n"]
    for positions,label in [(paper_positions,"Paper"),(active_positions,"Real")]:
        if not positions: continue
        total=0.0; lines.append(f"*{label}:*")
        for key,trade in positions.items():
            for leg in trade.get("legs",[]):
                ltp=get_ltp(leg["token"],leg["exchange"],leg["symbol"]) or leg["ltp"]
                pnl=(ltp-leg["ltp"])*leg["quantity"] if leg["action"]=="BUY" else (leg["ltp"]-ltp)*leg["quantity"]
                total+=pnl; lines.append(f"{leg['symbol']} Rs{ltp:.2f} P&L:Rs{pnl:.2f}")
        lines.append(f"Total: Rs{total:.2f}\n")
    if not paper_positions and not active_positions: lines.append("No open positions.")
    msg="\n".join(lines)
    if is_update: await target.message.reply_text(msg,parse_mode="Markdown")
    else: await target.bot.send_message(chat_id=TELEGRAM_CHAT_ID,text=msg,parse_mode="Markdown")

# ── Commands ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    a_status="Connected" if angel_ready else f"Not Connected - {angel_error}"
    await update.message.reply_text(
        f"*BOT 3 - Momentum Stocks Bot*\nMode: PAPER\n\n"
        f"Stocks: {len(MOMENTUM_STOCKS)} momentum stocks\n"
        f"Signals: Equity only, every day\n"
        f"No options — pure directional equity\n"
        f"Angel One: {a_status}\n\n"
        f"/scan3 /pnl3 /status3 /reconnect3",
        parse_mode="Markdown")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vix=get_ltp(VIX_TOKEN,"NSE",VIX_SYMBOL) if angel_ready else None
    a_status="Connected" if angel_ready else f"Not Connected\n{angel_error}"
    await update.message.reply_text(
        f"*BOT3 Status*\n"
        f"Time: {now_ist().strftime('%I:%M %p')} | {'Open' if is_trading_time() else 'Closed'}\n"
        f"VIX: {f'{vix:.2f}' if vix else 'N/A'}\n"
        f"Angel: {a_status}\n"
        f"Momentum Stocks: {len(MOMENTUM_STOCKS)}\n"
        f"Paper:{len(paper_positions)} Pending:{len(pending_signals)}",
        parse_mode="Markdown")

async def cmd_reconnect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global angel_ready, angel_error
    angel_ready=False; angel_error="Reconnecting..."
    await update.message.reply_text("BOT3: Reconnecting Angel One...")
    success = await asyncio.get_event_loop().run_in_executor(None, connect_angel)
    if success: await update.message.reply_text("BOT3: Angel One connected!")
    else: await update.message.reply_text(f"BOT3: Failed!\n{angel_error}")

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not angel_ready:
        await update.message.reply_text(f"BOT3: Not connected\n{angel_error}"); return
    if not is_trading_time():
        await update.message.reply_text("BOT3: Market closed."); return
    await update.message.reply_text(f"BOT3: Scanning {len(MOMENTUM_STOCKS)} momentum stocks...")
    await scan_and_alert(ctx.application)
    await update.message.reply_text("BOT3: Scan complete!")

async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_pnl(update,is_update=True)

async def handle_approval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; await query.answer(); data=query.data
    if data.startswith("approve_"):
        key=data.replace("approve_","")
        if key not in pending_signals:
            await query.edit_message_text("Signal expired."); return
        trade=pending_signals.pop(key); legs=trade.get("legs",[]); success=True; order_ids=[]
        for leg in legs:
            resp=place_order(leg["token"],leg["exchange"],leg["symbol"],leg["action"],leg["quantity"])
            if resp and resp.get("status"): order_ids.append(resp.get("data",{}).get("orderid","N/A"))
            else: success=False; break
        if success:
            is_paper=any(str(oid).startswith("PAPER-") for oid in order_ids)
            (paper_positions if is_paper else active_positions)[key]=trade
            legs_text="\n".join([f"{l['action']} {l['symbol']} Entry:Rs{l['ltp']:.2f} SL:Rs{l['sl']:.2f} T:Rs{l['target']:.2f}" for l in legs])
            await query.edit_message_text(f"*BOT3 {'Paper' if is_paper else 'Real'} Trade Active!*\n\n{legs_text}\n\nMonitoring SL and Target.",parse_mode="Markdown")
        else: await query.edit_message_text("BOT3: Order failed.")
    elif data.startswith("reject_"):
        pending_signals.pop(data.replace("reject_",""),None)
        await query.edit_message_text("Signal rejected.")

# ── Jobs ──────────────────────────────────────────────────────────────────────
async def job_scan(ctx: ContextTypes.DEFAULT_TYPE):
    await scan_and_alert(ctx.application)

async def job_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    await monitor_positions(ctx.application,paper_positions,is_paper=True)
    await monitor_positions(ctx.application,active_positions,is_paper=False)

async def job_pnl(ctx: ContextTypes.DEFAULT_TYPE):
    if is_trading_time(): await send_pnl(ctx.application,is_update=False)

async def job_squareoff(ctx: ContextTypes.DEFAULT_TYPE):
    if time_ist()>=SQUAREOFF_TIME and (active_positions or paper_positions):
        await square_off_all(ctx.application)

async def job_reconnect(ctx: ContextTypes.DEFAULT_TYPE):
    if not angel_ready:
        await asyncio.get_event_loop().run_in_executor(None, connect_angel)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logger.info("BOT3 MOMENTUM starting...")
    connect_angel()
    logger.info(f"Angel status: {angel_ready} | {angel_error}")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    for cmd,fn in [
        ("start3",     cmd_start),
        ("status3",    cmd_status),
        ("reconnect3", cmd_reconnect),
        ("scan3",      cmd_scan),
        ("pnl3",       cmd_pnl),
    ]:
        app.add_handler(CommandHandler(cmd,fn))
    app.add_handler(CallbackQueryHandler(handle_approval))

    jq=app.job_queue
    jq.run_repeating(job_scan,      interval=300,first=60)
    jq.run_repeating(job_monitor,   interval=30, first=90)
    jq.run_repeating(job_pnl,       interval=1800,first=120)
    jq.run_repeating(job_squareoff, interval=60, first=60)
    jq.run_repeating(job_reconnect, interval=300,first=300)

    logger.info("BOT3 polling started!")
    app.run_polling(allowed_updates=["message","callback_query"])

if __name__ == "__main__":
    main()
