
import os
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pocket_option import get_otc_candles

# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVE_DATA_KEY = os.environ["TWELVE_DATA_API_KEY"]

SYMBOLS = [
    s.strip()
    for s in os.getenv(
        "SYMBOLS",
        "EUR/USD,GBP/USD,USD/JPY"
    ).split(",")
    if s.strip()
]

MIN_SCORE = int(os.getenv("MIN_SCORE", "4"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "1"))

STATE_FILE = "signal_state.json"


# =========================
# HTTP / JSON
# =========================

def get_json(url, params):
    query = urllib.parse.urlencode(params)
    full_url = url + "?" + query

    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": "M5-Signal-Bot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "M5-Signal-Bot/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


# =========================
# TWELVE DATA
# =========================

def get_candles(symbol, interval="5min"):
    data = get_json(
        "https://api.twelvedata.com/time_series",
        {
            "symbol": symbol,
            "interval": interval,
            "outputsize": "250",
            "order": "desc",
            "timezone": "UTC",
            "apikey": TWELVE_DATA_KEY
        }
    )

    if data.get("status") == "error":
        raise RuntimeError(
            data.get("message", "Twelve Data error")
        )

    values = data.get("values", [])

    if len(values) < 80:
        raise RuntimeError(
            f"{symbol}: недостаточно свечей"
        )

    values.reverse()

    candles = []

    for x in values:
        try:
            candles.append({
                "time": x["datetime"],
                "open": float(x["open"]),
                "high": float(x["high"]),
                "low": float(x["low"]),
                "close": float(x["close"]),
                "volume": float(x.get("volume", 0) or 0)
            })
        except Exception:
            continue

    return candles


# =========================
# EMA
# =========================

def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for price in values[period:]:
        result = (
            (price - result) * multiplier
            + result
        )

    return result


# =========================
# RSI
# =========================

def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            (avg_gain * (period - 1) + gain)
            / period
        )

        avg_loss = (
            (avg_loss * (period - 1) + loss)
            / period
        )

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =========================
# CANDLE PATTERNS
# =========================

def candle_pattern(prev, cur):
    body = abs(cur["close"] - cur["open"])
    candle_range = cur["high"] - cur["low"]

    if candle_range <= 0:
        return None

    upper_wick = (
        cur["high"]
        - max(cur["open"], cur["close"])
    )

    lower_wick = (
        min(cur["open"], cur["close"])
        - cur["low"]
    )

    # Bullish Pin Bar
    if (
        lower_wick >= body * 2
        and lower_wick > upper_wick
        and cur["close"] >= cur["open"]
    ):
        return "CALL", "Bullish Pin Bar"

    # Bearish Pin Bar
    if (
        upper_wick >= body * 2
        and upper_wick > lower_wick
        and cur["close"] <= cur["open"]
    ):
        return "PUT", "Bearish Pin Bar"

    # Bullish Engulfing
    if (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["open"] <= prev["close"]
        and cur["close"] >= prev["open"]
    ):
        return "CALL", "Bullish Engulfing"

    # Bearish Engulfing
    if (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
    ):
        return "PUT", "Bearish Engulfing"

    # Strong directional candle
    if body / candle_range >= 0.65:
        if cur["close"] > cur["open"]:
            return "CALL", "Strong Bullish Candle"

        if cur["close"] < cur["open"]:
            return "PUT", "Strong Bearish Candle"

    return None


# =========================
# SUPPORT / RESISTANCE
# =========================

def find_zones(candles):
    supports = []
    resistances = []

    start = max(2, len(candles) - 120)
    end = len(candles) - 2

    for i in range(start, end):
        lows = [
            candles[j]["low"]
            for j in range(i - 2, i + 3)
        ]

        highs = [
            candles[j]["high"]
            for j in range(i - 2, i + 3)
        ]

        if candles[i]["low"] == min(lows):
            supports.append(candles[i]["low"])

        if candles[i]["high"] == max(highs):
            resistances.append(candles[i]["high"])

    return supports, resistances


def average_range(candles, period=20):
    recent = candles[-period:]

    ranges = [
        x["high"] - x["low"]
        for x in recent
    ]

    return sum(ranges) / len(ranges)


def nearest_zone(price, levels, width):
    if not levels:
        return None

    near = [
        level
        for level in levels
        if abs(price - level) <= width
    ]

    if not near:
        return None

    # Группируем близкие уровни
    clusters = []

    for level in sorted(near):
        added = False

        for cluster in clusters:
            if abs(level - cluster["price"]) <= width:
                cluster["levels"].append(level)
                cluster["price"] = sum(
                    cluster["levels"]
                ) / len(cluster["levels"])
                added = True
                break

        if not added:
            clusters.append({
                "price": level,
                "levels": [level]
            })

    best = min(
        clusters,
        key=lambda c: abs(price - c["price"])
    )

    return {
        "price": best["price"],
        "touches": len(best["levels"])
    }


# =========================
# STATE / COOLDOWN
# =========================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_signals": {}}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if "last_signals" not in data:
            data["last_signals"] = {}

        return data

    except Exception:
        return {"last_signals": {}}


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


def time_to_bar_index(value):
    dt = datetime.strptime(
        value,
        "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)

    return int(dt.timestamp() // 300)


def in_cooldown(symbol, candle_time, state):
    previous = state["last_signals"].get(symbol)

    if not previous:
        return False

    try:
        current_index = time_to_bar_index(
            candle_time
        )

        previous_index = time_to_bar_index(
            previous["time"]
        )

        difference = current_index - previous_index

        return (
            difference >= 0
            and difference <= COOLDOWN_BARS
        )

    except Exception:
        return False


# =========================
# FORMAT
# =========================

def format_price(price):
    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.5f}"

    return f"{price:.6f}"


# =========================
# ANALYSIS
# =========================

def analyze(symbol, candles, state):

    # Последняя свеча может быть ещё формирующейся.
    # Берём предпоследнюю как последнюю закрытую.
    if len(candles) < 80:
        return None

    current = candles[-2]
    previous = candles[-3]

    if in_cooldown(
        symbol,
        current["time"],
        state
    ):
        return None

    closed = candles[:-1]

    closes = [
        x["close"]
        for x in closed
    ]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    rsi_now = rsi(closes, 14)

    if ema20 is None or ema50 is None or rsi_now is None:
        return None

    # RSI предыдущей закрытой свечи
    rsi_prev = rsi(closes[:-1], 14)

    pattern = candle_pattern(
        previous,
        current
    )

    if pattern is None:
        return None

    direction, pattern_name = pattern

    supports, resistances = find_zones(
        closed
    )

    avg_range = average_range(
        closed,
        20
    )

    zone_width = avg_range * 0.45

    score = 0
    confirmations = []

    zone = None
    zone_name = ""

    # -------------------------
    # SUPPORT / RESISTANCE
    # -------------------------

    if direction == "CALL":

        zone = nearest_zone(
            current["close"],
            supports,
            zone_width
        )

        if zone and zone["touches"] >= 2:
            score += 2
            zone_name = (
                f"Support ~ "
                f"{format_price(zone['price'])}"
            )
            confirmations.append(
                "Сильная зона поддержки"
            )

    else:

        zone = nearest_zone(
            current["close"],
            resistances,
            zone_width
        )

        if zone and zone["touches"] >= 2:
            score += 2
            zone_name = (
                f"Resistance ~ "
                f"{format_price(zone['price'])}"
            )
            confirmations.append(
                "Сильная зона сопротивления"
            )

    # Без сильной зоны сигнал не нужен
    if score < 2:
        return None

    # -------------------------
    # CANDLE
    # -------------------------

    score += 2

    confirmations.append(
        pattern_name
    )

    # -------------------------
    # RSI
    # -------------------------

    rsi_ok = False

    if direction == "CALL":
        rsi_ok = (
            rsi_now <= 50
            or (
                rsi_prev is not None
                and rsi_now > rsi_prev
            )
        )

    else:
        rsi_ok = (
            rsi_now >= 50
            or (
                rsi_prev is not None
                and rsi_now < rsi_prev
            )
        )

    if rsi_ok:
        score += 1
        confirmations.append(
            f"RSI подтверждает направление"
        )

    # -------------------------
    # EMA TREND
    # -------------------------

    trend_ok = False

    if direction == "CALL":
        trend_ok = (
            ema20 >= ema50
            and current["close"] >= ema20
        )

    else:
        trend_ok = (
            ema20 <= ema50
            and current["close"] <= ema20
        )

    if trend_ok:
        score += 1
        confirmations.append(
            "Тренд EMA20/EMA50"
        )

    # -------------------------
    # VOLUME
    # -------------------------

    volume_text = "н/д"

    volumes = [
        x["volume"]
        for x in closed[-21:]
        if x["volume"] > 0
    ]

    current_volume = current["volume"]

    if (
        current_volume > 0
        and len(volumes) >= 5
    ):
        avg_volume = (
            sum(volumes[:-1])
            / len(volumes[:-1])
        )

        if (
            avg_volume > 0
            and current_volume
            >= avg_volume * 1.10
        ):
            score += 1
            confirmations.append(
                "Повышенный объём"
            )

        volume_text = (
            f"{current_volume:.0f}"
        )

    # -------------------------
    # MIN SCORE
    # -------------------------

    if score < MIN_SCORE:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "time": current["time"],
        "zone": zone_name,
        "score": score,
        "rsi": rsi_now,
        "ema20": ema20,
        "ema50": ema50,
        "volume": volume_text,
        "pattern": pattern_name,
        "confirmations": confirmations
    }


# =========================
# TELEGRAM
# =========================

def send_signal(signal):

    emoji = (
        "🟢"
        if signal["direction"] == "CALL"
        else "🔴"
    )

    confirmation_text = "\n".join(
        "• " + x
        for x in signal["confirmations"]
    )

    message = (
        f"🚨 M5 SIGNAL\n\n"
        f"{emoji} {signal['direction']}\n"
        f"💱 {signal['symbol']}\n"
        f"⏱ Таймфрейм: M5\n\n"
        f"🎯 Зона: {signal['zone']}\n"
        f"⭐ Rating: {signal['score']}/7\n"
        f"📊 RSI(14): {signal['rsi']:.1f}\n"
        f"📈 EMA20: {format_price(signal['ema20'])}\n"
        f"📈 EMA50: {format_price(signal['ema50'])}\n"
        f"🔊 Volume: {signal['volume']}\n\n"
        f"✅ Подтверждения:\n"
        f"{confirmation_text}\n\n"
        f"🕐 Ориентир: ближайшие 5 минут\n\n"
        f"⚠️ Сигнал не гарантирует результат. "
        f"Используйте его только для анализа "
        f"и самостоятельно проверяйте рынок."
    )

    result = post_form(
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": message
        }
    )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram error: "
            + str(result)
        )


# =========================
# MAIN
# =========================

def main():
    state = load_state()

    def scan_symbols():
        found = []

        for symbol in SYMBOLS:
            try:
                if symbol.endswith("_otc"):
                    candles = get_otc_candles(symbol)
                else:
                    candles = get_candles(symbol)

                signal = analyze(
                    symbol,
                    candles,
                    state
                )

                if signal is None:
                    print(
                        f"{symbol}: сигнала нет"
                    )
                    continue

                print(
                    f"{symbol}: "
                    f"{signal['direction']} "
                    f"{signal['score']}/7"
                )

                send_signal(signal)

                state["last_signals"][symbol] = {
                    "time": signal["time"],
                    "direction": signal["direction"],
                    "score": signal["score"]
                }

                save_state(state)

                print(
                    f"{symbol}: сигнал отправлен"
                )

                found.append(signal)

            except Exception as error:
                print(
                    f"{symbol}: ошибка: {error}"
                )

        return found

    # Первый обычный автоматический поиск
    scan_symbols()

    # Кнопка ручного поиска
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "🔎 НАЙТИ СИГНАЛ",
                    "callback_data": "find_signal"
                }
            ]
        ]
    }

    post_form(
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": (
                "🔎 Нужен новый сигнал?\n\n"
                "Нажмите кнопку ниже."
            ),
            "reply_markup": json.dumps(
                keyboard,
                ensure_ascii=False
            )
        }
    )

    # Слушаем нажатие кнопки
    offset = None

    for _ in range(180):

        try:
            params = {
                "timeout": 1
            }

            if offset is not None:
                params["offset"] = offset

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_TOKEN}/getUpdates"
            )

            updates = post_form(
                url,
                params
            )

            for update in updates.get(
                "result",
                []
            ):

                offset = update["update_id"] + 1

                callback = update.get(
                    "callback_query"
                )

                if not callback:
                    continue

                if (
                    callback.get("data")
                    != "find_signal"
                ):
                    continue

                callback_id = callback["id"]

                # Убираем "часики" с кнопки
                post_form(
                    f"https://api.telegram.org/"
                    f"bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                    {
                        "callback_query_id":
                            callback_id,
                        "text":
                            "🔎 Ищу сигнал..."
                    }
                )

                # Запускаем тот же анализ
                found = scan_symbols()

                if found:
                    text = (
                        "✅ Поиск завершён.\n"
                        f"Найдено сигналов: "
                        f"{len(found)}"
                    )
                else:
                    text = (
                        "🔎 Сигнал не найден.\n\n"
                        "Проверены все пары по "
                        "текущей стратегии."
                    )

                post_form(
                    f"https://api.telegram.org/"
                    f"bot{TELEGRAM_TOKEN}/sendMessage",
                    {
                        "chat_id": CHAT_ID,
                        "text": text,
                        "reply_markup": json.dumps(
                            keyboard,
                            ensure_ascii=False
                        )
                    }
                )

        except Exception as error:
            print(
                f"Ошибка обработки кнопки: "
                f"{error}"
            )


if __name__ == "__main__":
    main()
