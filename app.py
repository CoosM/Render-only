import math
import time
import hmac
import base64
import hashlib
import json
import requests
import os
import threading

from flask import Flask, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo


app = Flask(__name__)


# =========================================================
# ================== ENABLE EXCHANGES =====================
# =========================================================

USE_BITGET = os.getenv("USE_BITGET", "true").lower() == "true"
USE_OKX = os.getenv("USE_OKX", "false").lower() == "true"


# =========================================================
# ================== STRATEGY SETTINGS ====================
# =========================================================

TIMEFRAME = os.getenv("TIMEFRAME", "1m")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1"))

MAX_STEPS = 10

# RSI - НЕ ENV
RSI_LENGTH = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30


# =========================================================
# ================== GITHUB STATE =========================
# =========================================================

STATE_FILE_NAME = "state.json"
RUNTIME_FILE_NAME = "strategy_runtime.json"

cached_state = None
LAST_KNOWN_STATE = {
    "bitget": 0,
    "okx": 0
}

cached_runtime = None
LAST_PROCESSED_TS = None

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")

HEADERS_GIST = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


# =========================================================
# ======================== LOG =============================
# =========================================================

def log(msg):
    now = datetime.now(
        ZoneInfo("Europe/Kyiv")
    ).strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{now}] {msg}", flush=True)


# =========================================================
# ====================== STATE =============================
# =========================================================

def load_state():
    global cached_state, LAST_KNOWN_STATE

    if cached_state is not None:
        return cached_state

    try:
        url = f"https://api.github.com/gists/{GIST_ID}"

        r = requests.get(
            url,
            headers=HEADERS_GIST,
            timeout=10
        )

        r.raise_for_status()

        files = r.json()["files"]

        if STATE_FILE_NAME not in files:
            state = {
                "bitget": 0,
                "okx": 0
            }

        else:
            state = json.loads(
                files[STATE_FILE_NAME]["content"]
            )

            if "bitget" not in state:
                state["bitget"] = 0

            if "okx" not in state:
                state["okx"] = 0

        cached_state = state

        LAST_KNOWN_STATE = {
            "bitget": int(state.get("bitget", 0)),
            "okx": int(state.get("okx", 0))
        }

        return cached_state

    except Exception as e:

        log(f"⚠️ load_state error: {e}")

        return LAST_KNOWN_STATE


def save_state(state):
    global cached_state, LAST_KNOWN_STATE

    try:
        url = f"https://api.github.com/gists/{GIST_ID}"

        payload = {
            "files": {
                STATE_FILE_NAME: {
                    "content": json.dumps(
                        state,
                        separators=(",", ":")
                    )
                }
            }
        }

        r = requests.patch(
            url,
            headers=HEADERS_GIST,
            json=payload,
            timeout=10
        )

        r.raise_for_status()

        cached_state = state

        LAST_KNOWN_STATE = {
            "bitget": int(state.get("bitget", 0)),
            "okx": int(state.get("okx", 0))
        }

        log(
            f"✅ State saved: "
            f"bitget={state.get('bitget')} "
            f"okx={state.get('okx')}"
        )

        return True

    except Exception as e:

        log(f"⚠️ save_state error: {e}")

        return False


# =========================================================
# ====================== RUNTIME ===========================
# =========================================================

def load_runtime_state():

    global cached_runtime
    global LAST_PROCESSED_TS

    if cached_runtime is not None:
        return cached_runtime

    try:

        url = f"https://api.github.com/gists/{GIST_ID}"

        r = requests.get(
            url,
            headers=HEADERS_GIST,
            timeout=10
        )

        r.raise_for_status()

        files = r.json()["files"]

        if RUNTIME_FILE_NAME in files:

            runtime = json.loads(
                files[RUNTIME_FILE_NAME]["content"]
            )

            last_ts = runtime.get(
                "last_processed_ts"
            )

            if last_ts is not None:
                last_ts = int(last_ts)

        else:

            runtime = {
                "last_processed_ts": None
            }

            last_ts = None

        cached_runtime = runtime
        LAST_PROCESSED_TS = last_ts

        log(
            f"ℹ️ Runtime loaded | "
            f"last_processed_ts={LAST_PROCESSED_TS}"
        )

        return cached_runtime

    except Exception as e:

        log(
            f"⚠️ load_runtime_state error: {e}"
        )

        return {
            "last_processed_ts": LAST_PROCESSED_TS
        }


def save_runtime_state(last_processed_ts):

    global cached_runtime
    global LAST_PROCESSED_TS

    try:

        runtime = {
            "last_processed_ts": int(
                last_processed_ts
            )
        }

        url = f"https://api.github.com/gists/{GIST_ID}"

        payload = {
            "files": {
                RUNTIME_FILE_NAME: {
                    "content": json.dumps(
                        runtime,
                        separators=(",", ":")
                    )
                }
            }
        }

        r = requests.patch(
            url,
            headers=HEADERS_GIST,
            json=payload,
            timeout=10
        )

        r.raise_for_status()

        cached_runtime = runtime
        LAST_PROCESSED_TS = int(
            last_processed_ts
        )

        log(
            f"💾 Runtime saved | "
            f"last_processed_ts={last_processed_ts}"
        )

        return True

    except Exception as e:

        log(
            f"⚠️ save_runtime_state error: {e}"
        )

        return False


# =========================================================
# ======================== BITGET ===========================
# =========================================================

BITGET_API_KEY = os.getenv("BITGET_API_KEY")
BITGET_SECRET = os.getenv("BITGET_API_SECRET")
BITGET_PASS = os.getenv("BITGET_PASSPHRASE")

BITGET_SYMBOL = os.getenv(
    "BITGET_SYMBOL",
    "AXSUSDT"
)

BITGET_BUY_USDT = os.getenv(
    "BITGET_BUY_USDT",
    "14"
)

BITGET_BASE = "https://api.bitget.com"


# =========================================================
# ================= BITGET TIMEFRAME =======================
# =========================================================

def bitget_interval():

    mapping = {
        "1m": "1min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "6h": "6h",
        "12h": "12h",
        "1d": "1day"
    }

    return mapping.get(
        TIMEFRAME.lower(),
        "1min"
    )


def timeframe_seconds():

    mapping = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400
    }

    return mapping.get(
        TIMEFRAME.lower(),
        60
    )


# =========================================================
# ================= BITGET HEADERS =========================
# =========================================================

def bitget_headers(method, path, body=""):

    ts = str(
        int(time.time() * 1000)
    )

    msg = (
        ts
        + method.upper()
        + path
        + body
    )

    sign = base64.b64encode(
        hmac.new(
            BITGET_SECRET.encode(),
            msg.encode(),
            hashlib.sha256
        ).digest()
    ).decode()

    return {
        "ACCESS-KEY": BITGET_API_KEY,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": BITGET_PASS,
        "Content-Type": "application/json"
    }


# =========================================================
# ================= BITGET QUANTITY SCALE =================
# =========================================================

def bitget_get_quantity_scale():

    path = (
        "/api/v2/spot/public/symbols"
        f"?symbol={BITGET_SYMBOL}"
    )

    try:

        res = requests.get(
            BITGET_BASE + path,
            timeout=10
        ).json()

        if res.get("code") != "00000":

            log(
                f"⚠️ scale fetch error: {res}"
            )

            return 6

        data = res.get("data", [])

        if not data:
            return 6

        item = data[0]

        if "quantityPrecision" in item:
            return int(
                item["quantityPrecision"]
            )

        if "quantityScale" in item:
            return int(
                item["quantityScale"]
            )

        return 6

    except Exception as e:

        log(
            f"⚠️ quantity scale error: {e}"
        )

        return 6


def adjust_size_to_scale(
    size,
    scale
):

    factor = 10 ** scale

    return (
        math.floor(size * factor)
        / factor
    )


# =========================================================
# ================= BITGET BALANCE =========================
# =========================================================

def bitget_get_balance():

    path = (
        "/api/v2/spot/account/assets"
    )

    try:

        headers = bitget_headers(
            "GET",
            path
        )

        res = requests.get(
            BITGET_BASE + path,
            headers=headers,
            timeout=10
        ).json()

        if res.get("code") != "00000":

            log(
                f"⚠️ BITGET BALANCE ERROR | "
                f"{res}"
            )

            return 0.0

        base_ccy = (
            BITGET_SYMBOL
            .replace("USDT", "")
        )

        for item in res.get("data", []):

            if item.get("coin") == base_ccy:

                return float(
                    item.get(
                        "available",
                        0
                    )
                )

        return 0.0

    except Exception as e:

        log(
            f"⚠️ BITGET BALANCE EXCEPTION | "
            f"{e}"
        )

        return 0.0


# =========================================================
# ================= BITGET BUY =============================
# =========================================================

def bitget_buy():

    state = load_state()

    step = int(
        state.get("bitget", 0)
    )

    if step >= MAX_STEPS:

        log(
            f"⛔ BITGET BUY SKIP | "
            f"max steps | step={step}"
        )

        return {
            "status": "max steps reached",
            "step": step
        }

    log(
        f"🟢 BITGET BUY TRY | "
        f"step_before={step} | "
        f"usdt={BITGET_BUY_USDT} | "
        f"symbol={BITGET_SYMBOL}"
    )

    path = (
        "/api/v2/spot/trade/place-order"
    )

    body = {
        "symbol": BITGET_SYMBOL,
        "side": "buy",
        "orderType": "market",
        "force": "gtc",
        "size": str(BITGET_BUY_USDT),
        "quoteCoin": "USDT"
    }

    body_json = json.dumps(
        body,
        separators=(",", ":")
    )

    try:

        headers = bitget_headers(
            "POST",
            path,
            body_json
        )

        res = requests.post(
            BITGET_BASE + path,
            headers=headers,
            data=body_json,
            timeout=15
        ).json()

    except Exception as e:

        log(
            f"⚠️ BITGET BUY REQUEST ERROR | "
            f"{e}"
        )

        return {
            "status": "error",
            "error": str(e)
        }

    if res.get("code") != "00000":

        log(
            f"⚠️ BITGET BUY ERROR | "
            f"step_before={step} | "
            f"resp={res}"
        )

        return {
            "status": "error",
            "error": res
        }

    state["bitget"] = step + 1

    if not save_state(state):

        log(
            "⚠️ BUY executed but state "
            "was not saved!"
        )

    log(
        f"🟢 BITGET BUY OK | "
        f"step_now={state['bitget']}"
    )

    return {
        "status": "buy ok",
        "step": state["bitget"]
    }


# =========================================================
# ================= BITGET SELL ============================
# =========================================================

def bitget_sell():

    state = load_state()

    step = int(
        state.get("bitget", 0)
    )

    if step <= 0:

        log(
            f"⛔ BITGET SELL BLOCKED | "
            f"step={step}"
        )

        return {
            "status": "skip",
            "reason": "step <= 0",
            "step": step
        }

    balance = bitget_get_balance()

    if balance <= 0:

        log(
            f"⚠️ BITGET SELL SKIP | "
            f"balance=0 | step={step}"
        )

        return {
            "status": "skip",
            "reason": "balance 0",
            "step": step
        }

    sell_percent = 1 / step

    raw_qty = (
        balance * sell_percent
    )

    quantity_scale = (
        bitget_get_quantity_scale()
    )

    sell_qty = adjust_size_to_scale(
        raw_qty,
        quantity_scale
    )

    if sell_qty <= 0:

        log(
            f"⚠️ BITGET SELL SKIP | "
            f"qty too small | "
            f"balance={balance:.8f} | "
            f"raw={raw_qty:.8f} | "
            f"step={step}"
        )

        return {
            "status": "skip",
            "reason": "qty too small",
            "step": step
        }

    log(
        f"🔴 BITGET SELL TRY | "
        f"balance={balance:.8f} | "
        f"{sell_percent * 100:.2f}% | "
        f"qty={sell_qty} | "
        f"step={step}"
    )

    path = (
        "/api/v2/spot/trade/place-order"
    )

    body = {
        "symbol": BITGET_SYMBOL,
        "side": "sell",
        "orderType": "market",
        "force": "gtc",
        "size": str(sell_qty)
    }

    body_json = json.dumps(
        body,
        separators=(",", ":")
    )

    try:

        headers = bitget_headers(
            "POST",
            path,
            body_json
        )

        res = requests.post(
            BITGET_BASE + path,
            headers=headers,
            data=body_json,
            timeout=15
        ).json()

    except Exception as e:

        log(
            f"⚠️ BITGET SELL REQUEST ERROR | "
            f"{e}"
        )

        return {
            "status": "error",
            "error": str(e)
        }

    if res.get("code") != "00000":

        log(
            f"⚠️ BITGET SELL ERROR | "
            f"step_before={step} | "
            f"qty={sell_qty} | "
            f"resp={res}"
        )

        return {
            "status": "error",
            "error": res
        }

    state["bitget"] = step - 1

    if not save_state(state):

        log(
            "⚠️ SELL executed but state "
            "was not saved!"
        )

    log(
        f"✅ BITGET SELL OK | "
        f"sold={sell_qty} | "
        f"step_now={state['bitget']}"
    )

    return {
        "status": "sell ok",
        "sold_qty": sell_qty,
        "percent": round(
            sell_percent * 100,
            2
        ),
        "step_after": state["bitget"]
    }


# =========================================================
# ================= BITGET CANDLES =========================
# =========================================================

def bitget_get_candles():

    interval = bitget_interval()

    path = (
        "/api/v2/spot/market/candles"
    )

    params = {
        "symbol": BITGET_SYMBOL,
        "granularity": interval,
        "limit": "100"
    }

    try:

        r = requests.get(
            BITGET_BASE + path,
            params=params,
            timeout=10
        )

        r.raise_for_status()

        res = r.json()

        if res.get("code") != "00000":

            log(
                f"⚠️ CANDLES ERROR | "
                f"{res}"
            )

            return []

        candles = []

        for item in res.get(
            "data",
            []
        ):

            if len(item) < 5:
                continue

            candles.append({
                "ts": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4])
            })

        candles.sort(
            key=lambda x: x["ts"]
        )

        return candles

    except Exception as e:

        log(
            f"⚠️ CANDLES REQUEST ERROR | "
            f"{e}"
        )

        return []


# =========================================================
# ===================== RSI WILDER =========================
# =========================================================

def calculate_rsi(
    closes,
    length=14
):

    if len(closes) < length + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = (
            closes[i]
            - closes[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0.0)

        else:

            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = (
        sum(gains[:length])
        / length
    )

    avg_loss = (
        sum(losses[:length])
        / length
    )

    rsi_values = [None] * length

    if avg_loss == 0:

        rsi_values.append(100.0)

    else:

        rs = (
            avg_gain
            / avg_loss
        )

        rsi_values.append(
            100
            - (
                100
                / (1 + rs)
            )
        )

    for i in range(
        length,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (length - 1)
            )
            + gains[i]
        ) / length

        avg_loss = (
            (
                avg_loss
                * (length - 1)
            )
            + losses[i]
        ) / length

        if avg_loss == 0:

            rsi = 100.0

        else:

            rs = (
                avg_gain
                / avg_loss
            )

            rsi = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

        rsi_values.append(rsi)

    return rsi_values


# =========================================================
# ==================== STRATEGY SIGNAL =====================
# =========================================================

def calculate_signal(candles):

    if len(candles) < RSI_LENGTH + 3:

        return None, None

    closes = [
        candle["close"]
        for candle in candles
    ]

    rsi_values = calculate_rsi(
        closes,
        RSI_LENGTH
    )

    if not rsi_values:
        return None, None

    i = len(candles) - 1

    current = candles[i]
    previous = candles[i - 1]

    rsi_current = rsi_values[i]
    rsi_previous = rsi_values[i - 1]
    rsi_two_back = rsi_values[i - 2]

    if (
        rsi_current is None
        or rsi_previous is None
        or rsi_two_back is None
    ):
        return None, None

    # =====================================================
    # EXACT PINE LOGIC
    # =====================================================

    bullish_candle = (
        current["close"]
        >= previous["open"]
        and
        previous["close"]
        < previous["open"]
    )

    bearish_candle = (
        current["close"]
        <= previous["open"]
        and
        previous["close"]
        > previous["open"]
    )

    is_rsi_oversold = (
        rsi_current <= RSI_OVERSOLD
        or
        rsi_previous <= RSI_OVERSOLD
        or
        rsi_two_back <= RSI_OVERSOLD
    )

    is_rsi_overbought = (
        rsi_current >= RSI_OVERBOUGHT
        or
        rsi_previous >= RSI_OVERBOUGHT
        or
        rsi_two_back >= RSI_OVERBOUGHT
    )

    trade_buy = (
        is_rsi_oversold
        and bullish_candle
    )

    trade_sell = (
        is_rsi_overbought
        and bearish_candle
    )

    info = {
        "timestamp": current["ts"],
        "close": current["close"],
        "rsi": round(rsi_current, 4),
        "rsi_previous": round(
            rsi_previous,
            4
        ),
        "rsi_two_back": round(
            rsi_two_back,
            4
        ),
        "bullish": bullish_candle,
        "bearish": bearish_candle,
        "buy": trade_buy,
        "sell": trade_sell
    }

    if trade_buy:

        return "buy", info

    if trade_sell:

        return "sell", info

    return None, info


# =========================================================
# ================= CLOSED CANDLE ==========================
# =========================================================

def get_closed_candles(candles):

    now_ms = int(
        time.time() * 1000
    )

    interval_ms = (
        timeframe_seconds()
        * 1000
    )

    current_candle_ts = (
        now_ms
        // interval_ms
    ) * interval_ms

    closed = [
        candle
        for candle in candles
        if candle["ts"]
        < current_candle_ts
    ]

    return closed


# =========================================================
# ================= PROCESS CANDLE =========================
# =========================================================

def process_candle(
    candle,
    all_candles
):

    global LAST_PROCESSED_TS

    ts = candle["ts"]

    # Берём только свечи до текущей
    candles_until_here = [
        c
        for c in all_candles
        if c["ts"] <= ts
    ]

    signal, info = calculate_signal(
        candles_until_here
    )

    candle_time = datetime.fromtimestamp(
        ts / 1000,
        tz=ZoneInfo("Europe/Kyiv")
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    if signal is None:

        log(
            f"⚪ CANDLE | "
            f"{candle_time} | "
            f"close={candle['close']} | "
            f"RSI={info['rsi'] if info else 'N/A'} | "
            f"signal=NONE"
        )

        # Даже свечу без сигнала
        # считаем обработанной
        if not save_runtime_state(ts):

            log(
                "⚠️ Runtime save failed"
            )

        return

    log(
        f"🚨 SIGNAL | "
        f"{signal.upper()} | "
        f"time={candle_time} | "
        f"close={candle['close']} | "
        f"RSI={info['rsi']}"
    )

    # =====================================================
    # BUY
    # =====================================================

    if signal == "buy":

        if USE_BITGET:

            result = bitget_buy()

            log(
                f"📌 BITGET BUY RESULT | "
                f"{result}"
            )

    # =====================================================
    # SELL
    # =====================================================

    elif signal == "sell":

        if USE_BITGET:

            result = bitget_sell()

            log(
                f"📌 BITGET SELL RESULT | "
                f"{result}"
            )

    # =====================================================
    # OKX НЕ ПОДКЛЮЧАЕМ К СИГНАЛУ,
    # ПОКА USE_OKX НЕ ИМЕЕТ РЕАЛЬНЫХ ФУНКЦИЙ
    # =====================================================

    if USE_OKX:

        log(
            "⚠️ USE_OKX=true, "
            "but Render-only strategy currently "
            "executes Bitget only."
        )

    # После попытки сигнала
    # свеча считается обработанной.
    if not save_runtime_state(ts):

        log(
            "⚠️ Runtime save failed after signal"
        )


# =========================================================
# ================= STRATEGY ENGINE ========================
# =========================================================

def strategy_engine():

    global LAST_PROCESSED_TS

    log(
        "🚀 RENDER-ONLY STRATEGY ENGINE STARTED"
    )

    log(
        f"⚙️ TIMEFRAME={TIMEFRAME}"
    )

    log(
        f"⚙️ CHECK_INTERVAL={CHECK_INTERVAL}"
    )

    log(
        f"⚙️ SYMBOL={BITGET_SYMBOL}"
    )

    log(
        f"⚙️ RSI={RSI_LENGTH} / "
        f"{RSI_OVERSOLD} / "
        f"{RSI_OVERBOUGHT}"
    )

    log(
        "⚙️ TradingView = OFF"
    )

    load_state()
    load_runtime_state()

    last_cycle_candle_ts = None

    while True:

        try:

            candles = bitget_get_candles()

            if not candles:

                time.sleep(
                    CHECK_INTERVAL
                )

                continue

            closed_candles = (
                get_closed_candles(
                    candles
                )
            )

            if not closed_candles:

                time.sleep(
                    CHECK_INTERVAL
                )

                continue

            # -------------------------------------------------
            # На первом запуске:
            # если runtime пустой, начинаем с последней
            # закрытой свечи, чтобы не открыть сразу
            # множество старых сделок.
            # -------------------------------------------------

            if LAST_PROCESSED_TS is None:

                first_candle = (
                    closed_candles[-1]
                )

                LAST_PROCESSED_TS = (
                    first_candle["ts"] - 1
                )

                log(
                    f"ℹ️ First start | "
                    f"starting from candle "
                    f"{first_candle['ts']}"
                )

            # -------------------------------------------------
            # Берём все закрытые свечи после последней
            # обработанной.
            # -------------------------------------------------

            new_candles = [
                candle
                for candle in closed_candles
                if candle["ts"]
                > LAST_PROCESSED_TS
            ]

            # -------------------------------------------------
            # Обрабатываем строго по порядку.
            # -------------------------------------------------

            for candle in new_candles:

                process_candle(
                    candle,
                    candles
                )

            # -------------------------------------------------
            # Если новых свечей нет,
            # просто ждём.
            # -------------------------------------------------

            latest_closed_ts = (
                closed_candles[-1]["ts"]
            )

            if (
                last_cycle_candle_ts
                != latest_closed_ts
            ):

                last_cycle_candle_ts = (
                    latest_closed_ts
                )

            time.sleep(
                CHECK_INTERVAL
            )

        except Exception as e:

            log(
                f"🔥 ENGINE ERROR | {e}"
            )

            time.sleep(3)


# =========================================================
# ======================== HEALTH ===========================
# =========================================================

@app.route("/health")
def health():

    global cached_state

    if cached_state is None:

        cached_state = (
            LAST_KNOWN_STATE
        )

    return jsonify({
        "status": "ok",
        "mode": "render_only",
        "tradingview": False,
        "strategy": "RSI + Engulfing",
        "timeframe": TIMEFRAME,
        "symbol": BITGET_SYMBOL,
        "state": cached_state,
        "last_processed_ts": LAST_PROCESSED_TS,
        "bitget_enabled": USE_BITGET,
        "okx_enabled": USE_OKX
    })


# =========================================================
# ====================== START ENGINE ======================
# =========================================================

def start_engine():

    thread = threading.Thread(
        target=strategy_engine,
        daemon=True
    )

    thread.start()


# Запускаем движок на уровне модуля, чтобы Gunicorn автоматически 
# стартовал фоновый поток при закуске Web Service
start_engine()


# =========================================================
# ======================== START ============================
# =========================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
