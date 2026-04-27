"""
================================================================================
  ..Custom for trade..   —   улучшенная торговая стратегия для bybit-bot
================================================================================

  Что это:
      Drop-in замена strategy.py с расширенным функционалом:
        • multi-filter стратегия (Supertrend + ADX + Volume + RSI sweet-zone)
        • динамические SL/TP на основе ATR
        • готовый форматтер для Telegram-уведомлений
        • совместимость со старым контрактом calculate_signals()

  ----------------------------------------------------------------------------
  ИНТЕГРАЦИЯ В bybit-bot — 4 шага:
  ----------------------------------------------------------------------------

  1) Переименуй этот файл в strategy.py и положи в корень репо
     (заменив старый strategy.py).

         mv "..Custom for trade...py" strategy.py

  2) В bot.py при запросе свечей с Bybit поменяй интервал 5 → 15:

         # БЫЛО:
         klines = session.get_kline(category="linear", symbol=sym, interval="5",  limit=200)
         # СТАЛО:
         klines = session.get_kline(category="linear", symbol=sym, interval="15", limit=200)

  3) В bot.py при открытии сделки используй динамические SL/TP вместо фикс. %:

         from strategy import calculate_signals, get_dynamic_sl_tp, format_telegram_message

         result = calculate_signals(candles)
         if result["signal"]:
             sl, tp = get_dynamic_sl_tp(result["price"], result["atr"], result["signal"])
             # ... вызов session.place_order(..., stopLoss=sl, takeProfit=tp)
             text = format_telegram_message(symbol, result, sl, tp)
             telegram_bot.send_message(chat_id, text, parse_mode="HTML")

  4) Никаких новых зависимостей — всё на pandas + numpy
     (которые уже есть в requirements.txt).

  ----------------------------------------------------------------------------
  ЛОГИКА СТРАТЕГИИ (15-минутный таймфрейм):
  ----------------------------------------------------------------------------

  Базовый триггер  : EMA(9) пересекает EMA(21) (bull/bear cross)
  Фильтр 1         : Supertrend(10, 3.0) — направление тренда
  Фильтр 2         : ADX(14) ≥ 20         — отсекаем флэт
  Фильтр 3         : RSI(14) в sweet-zone — не входим в перекупленность
                       LONG : 40-65
                       SHORT: 35-60
  Фильтр 4         : Volume ≥ 1.2× SMA(20) — пробой подтверждён объёмом

  Сделка открывается ТОЛЬКО если все 4 фильтра ОК (signal_strength == 4).

  SL/TP            : динамические по ATR
                       SL = price ∓ 1.5 × ATR
                       TP = price ± 3.0 × ATR     (R:R = 1:2)

================================================================================
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


# ============================================================================
#  ПАРАМЕТРЫ СТРАТЕГИИ — единая точка тюнинга
# ============================================================================

EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
RSI_PERIOD = 14

RSI_LONG_MIN, RSI_LONG_MAX = 40.0, 65.0
RSI_SHORT_MIN, RSI_SHORT_MAX = 35.0, 60.0

SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 20.0
ADX_STRONG_THRESHOLD = 30.0

ATR_PERIOD = 14

VOLUME_MA_PERIOD = 20
VOLUME_RATIO_MIN = 1.2

CROSSOVER_LOOKBACK = 1     # сигнал берём только на свежем кроссовере

# Множители ATR для динамических уровней
SL_ATR_MULT = 1.5
TP_ATR_MULT = 3.0          # даёт R:R = 1:2

MIN_CANDLES = 50


# ============================================================================
#  УТИЛИТЫ — нормализация входных данных
# ============================================================================

def _to_dataframe(candles: Any) -> pd.DataFrame:
    """
    Принимает свечи в одном из распространённых форматов и приводит к DataFrame
    с колонками: open, high, low, close, volume.

    Поддерживаемые форматы:
        1) pandas.DataFrame (уже OHLCV)
        2) list[list]:  [[ts, o, h, l, c, v, ...], ...]   (Bybit V5 raw)
        3) list[dict]:  [{"open":..,"high":..,"low":..,"close":..,"volume":..}, ...]
    """
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
        df.columns = [str(c).lower() for c in df.columns]
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError(
                f"DataFrame должен содержать колонки {required}, "
                f"получено: {list(df.columns)}"
            )
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    if not isinstance(candles, Iterable):
        raise TypeError(f"candles должен быть iterable, получено: {type(candles)}")

    rows = list(candles)
    if not rows:
        raise ValueError("Список свечей пуст")

    first = rows[0]

    if isinstance(first, dict):
        df = pd.DataFrame(rows)
        df.columns = [str(c).lower() for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    if isinstance(first, (list, tuple)):
        if len(first) < 6:
            raise ValueError(
                f"Каждая свеча должна содержать минимум 6 значений (ts,o,h,l,c,v), "
                f"получено {len(first)}"
            )
        arr = np.asarray(rows, dtype=object)
        df = pd.DataFrame({
            "open":   arr[:, 1].astype(float),
            "high":   arr[:, 2].astype(float),
            "low":    arr[:, 3].astype(float),
            "close":  arr[:, 4].astype(float),
            "volume": arr[:, 5].astype(float),
        })
        return df

    raise TypeError(f"Неизвестный формат свечи: {type(first)}")


# ============================================================================
#  ИНДИКАТОРЫ — чистый numpy/pandas
# ============================================================================

def ema(series: pd.Series, period: int) -> pd.Series:
    """Экспоненциальная скользящая средняя."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range — основа ATR/ADX."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    ADX — сила тренда.
        ADX < 20  : боковик/слабый тренд (сделки не открываем)
        ADX 20-30 : тренд формируется
        ADX > 30  : уверенный тренд
        ADX > 40  : очень сильный тренд (часто на излёте)
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    tr = true_range(high, low, close)
    atr_ = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_.replace(0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx_.fillna(0.0)


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Supertrend — лучший single-line trend filter для крипты на 15m.
    Возвращает: (st_line, direction)  где direction ∈ {+1, -1}.
    """
    atr_ = atr(high, low, close, period)
    hl2 = (high + low) / 2.0

    upper_band = hl2 + multiplier * atr_
    lower_band = hl2 - multiplier * atr_

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, len(close)):
        if upper_band.iat[i] < final_upper.iat[i - 1] or close.iat[i - 1] > final_upper.iat[i - 1]:
            final_upper.iat[i] = upper_band.iat[i]
        else:
            final_upper.iat[i] = final_upper.iat[i - 1]

        if lower_band.iat[i] > final_lower.iat[i - 1] or close.iat[i - 1] < final_lower.iat[i - 1]:
            final_lower.iat[i] = lower_band.iat[i]
        else:
            final_lower.iat[i] = final_lower.iat[i - 1]

    direction = pd.Series(np.ones(len(close), dtype=int), index=close.index)
    st_line = pd.Series(np.zeros(len(close)), index=close.index)

    direction.iat[0] = 1 if close.iat[0] >= final_upper.iat[0] else -1
    st_line.iat[0] = final_lower.iat[0] if direction.iat[0] == 1 else final_upper.iat[0]

    for i in range(1, len(close)):
        prev_dir = direction.iat[i - 1]
        if prev_dir == 1:
            if close.iat[i] < final_lower.iat[i]:
                direction.iat[i] = -1
                st_line.iat[i] = final_upper.iat[i]
            else:
                direction.iat[i] = 1
                st_line.iat[i] = final_lower.iat[i]
        else:
            if close.iat[i] > final_upper.iat[i]:
                direction.iat[i] = 1
                st_line.iat[i] = final_lower.iat[i]
            else:
                direction.iat[i] = -1
                st_line.iat[i] = final_upper.iat[i]

    return st_line, direction


# ============================================================================
#  ОСНОВНАЯ ФУНКЦИЯ — calculate_signals (контракт совместим со старым)
# ============================================================================

def _empty_result(price: float = float("nan"), reason: str = "недостаточно данных") -> dict:
    return {
        "signal": None,
        "rsi": float("nan"),
        "ema_fast": float("nan"),
        "ema_slow": float("nan"),
        "price": price,
        "adx": float("nan"),
        "atr": float("nan"),
        "supertrend": None,
        "volume_ratio": float("nan"),
        "signal_strength": 0,
        "reason": reason,
        "is_flat": True,
        "trend_strength": "flat",
    }


def calculate_signals(candles: Any) -> dict:
    """
    Главная точка входа — вызывается ботом для каждой пары на закрытии свечи.

    Параметры
    ---------
    candles : list / DataFrame
        OHLCV-свечи в хронологическом порядке (старые → новые).
        Минимум MIN_CANDLES свечей.

    Возвращает
    ----------
    dict с ключами:
        signal           : "BUY" | "SELL" | None
        rsi              : float           — текущий RSI
        ema_fast         : float           — EMA9
        ema_slow         : float           — EMA21
        price            : float           — close последней свечи
        adx              : float           — сила тренда
        atr              : float           — для динамических SL/TP
        supertrend       : "UP" | "DOWN"
        volume_ratio     : float           — текущий объём / SMA(20)
        signal_strength  : int (0..4)      — сколько фильтров прошло
        reason           : str             — почему сигнал / почему нет
        is_flat          : bool            — рынок в боковике
        trend_strength   : "strong" | "weak" | "flat"
    """
    try:
        df = _to_dataframe(candles)
    except (TypeError, ValueError) as exc:
        return _empty_result(reason=f"ошибка парсинга свечей: {exc}")

    if len(df) < MIN_CANDLES:
        return _empty_result(
            price=float(df["close"].iat[-1]) if len(df) else float("nan"),
            reason=f"мало свечей: {len(df)} < {MIN_CANDLES}",
        )

    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    ema_fast_s = ema(c, EMA_FAST_PERIOD)
    ema_slow_s = ema(c, EMA_SLOW_PERIOD)
    rsi_s = rsi(c, RSI_PERIOD)
    adx_s = adx(h, l, c, ADX_PERIOD)
    atr_s = atr(h, l, c, ATR_PERIOD)
    _, st_dir_s = supertrend(h, l, c, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
    vol_ma = v.rolling(VOLUME_MA_PERIOD).mean()
    vol_ratio_s = v / vol_ma.replace(0, np.nan)

    price = float(c.iat[-1])
    ema_fast_v = float(ema_fast_s.iat[-1])
    ema_slow_v = float(ema_slow_s.iat[-1])
    rsi_v = float(rsi_s.iat[-1])
    adx_v = float(adx_s.iat[-1])
    atr_v = float(atr_s.iat[-1])
    st_dir = int(st_dir_s.iat[-1])
    vol_ratio = float(vol_ratio_s.iat[-1]) if not math.isnan(vol_ratio_s.iat[-1]) else 0.0

    supertrend_label = "UP" if st_dir == 1 else "DOWN"
    is_flat = adx_v < ADX_TREND_THRESHOLD
    if adx_v >= ADX_STRONG_THRESHOLD:
        trend_strength = "strong"
    elif adx_v >= ADX_TREND_THRESHOLD:
        trend_strength = "weak"
    else:
        trend_strength = "flat"

    # --- EMA-crossover в окне CROSSOVER_LOOKBACK -----------------------------
    bull_cross = False
    bear_cross = False
    look = max(1, CROSSOVER_LOOKBACK)
    for i in range(1, look + 1):
        if ema_fast_s.iat[-i] > ema_slow_s.iat[-i] and ema_fast_s.iat[-i - 1] <= ema_slow_s.iat[-i - 1]:
            bull_cross = True
        if ema_fast_s.iat[-i] < ema_slow_s.iat[-i] and ema_fast_s.iat[-i - 1] >= ema_slow_s.iat[-i - 1]:
            bear_cross = True

    # --- Проверка фильтров ----------------------------------------------------
    def _eval_long() -> tuple[bool, int, str]:
        passed = 0
        misses: list[str] = []

        if st_dir == 1: passed += 1
        else: misses.append("Supertrend=DOWN")

        if adx_v >= ADX_TREND_THRESHOLD: passed += 1
        else: misses.append(f"ADX={adx_v:.1f}<{ADX_TREND_THRESHOLD}")

        if RSI_LONG_MIN <= rsi_v <= RSI_LONG_MAX: passed += 1
        else: misses.append(f"RSI={rsi_v:.1f} вне [{RSI_LONG_MIN}-{RSI_LONG_MAX}]")

        if vol_ratio >= VOLUME_RATIO_MIN: passed += 1
        else: misses.append(f"Volume={vol_ratio:.2f}x<{VOLUME_RATIO_MIN}x")

        ok = passed == 4
        reason = "LONG: все 4 фильтра ОК" if ok else "LONG отклонён: " + ", ".join(misses)
        return ok, passed, reason

    def _eval_short() -> tuple[bool, int, str]:
        passed = 0
        misses: list[str] = []

        if st_dir == -1: passed += 1
        else: misses.append("Supertrend=UP")

        if adx_v >= ADX_TREND_THRESHOLD: passed += 1
        else: misses.append(f"ADX={adx_v:.1f}<{ADX_TREND_THRESHOLD}")

        if RSI_SHORT_MIN <= rsi_v <= RSI_SHORT_MAX: passed += 1
        else: misses.append(f"RSI={rsi_v:.1f} вне [{RSI_SHORT_MIN}-{RSI_SHORT_MAX}]")

        if vol_ratio >= VOLUME_RATIO_MIN: passed += 1
        else: misses.append(f"Volume={vol_ratio:.2f}x<{VOLUME_RATIO_MIN}x")

        ok = passed == 4
        reason = "SHORT: все 4 фильтра ОК" if ok else "SHORT отклонён: " + ", ".join(misses)
        return ok, passed, reason

    # --- Принятие решения -----------------------------------------------------
    signal: str | None = None
    signal_strength = 0
    reason = "нет EMA-кроссовера"

    if bull_cross:
        ok, strength, reason = _eval_long()
        signal_strength = strength
        if ok:
            signal = "BUY"
    elif bear_cross:
        ok, strength, reason = _eval_short()
        signal_strength = strength
        if ok:
            signal = "SELL"
    else:
        # Диагностика — насколько рынок "готов", но это не сигнал.
        passed = 0
        if st_dir == 1 and ema_fast_v > ema_slow_v:
            passed += 1
        elif st_dir == -1 and ema_fast_v < ema_slow_v:
            passed += 1
        if adx_v >= ADX_TREND_THRESHOLD:
            passed += 1
        if vol_ratio >= VOLUME_RATIO_MIN:
            passed += 1
        signal_strength = passed

    return {
        "signal": signal,
        "rsi": round(rsi_v, 2),
        "ema_fast": round(ema_fast_v, 6),
        "ema_slow": round(ema_slow_v, 6),
        "price": round(price, 6),
        "adx": round(adx_v, 2),
        "atr": round(atr_v, 6),
        "supertrend": supertrend_label,
        "volume_ratio": round(vol_ratio, 2),
        "signal_strength": signal_strength,
        "reason": reason,
        "is_flat": is_flat,
        "trend_strength": trend_strength,
    }


# ============================================================================
#  ХЕЛПЕРЫ ДЛЯ bot.py
# ============================================================================

def get_dynamic_sl_tp(
    price: float,
    atr_value: float,
    direction: str,
    sl_mult: float = SL_ATR_MULT,
    tp_mult: float = TP_ATR_MULT,
) -> tuple[float, float]:
    """
    Считает динамические SL и TP на основе текущего ATR.

    Преимущество над фиксированными % :
        В волатильности шире стопы (не выбьет шумом),
        в штиле уже стопы (не отдашь много на разворот).

    Параметры
    ---------
    price       : текущая цена входа
    atr_value   : значение ATR (берётся из result["atr"])
    direction   : "BUY" → long, "SELL" → short
    sl_mult     : множитель ATR для стопа (default 1.5)
    tp_mult     : множитель ATR для тейка (default 3.0 → R:R = 1:2)

    Возвращает
    ----------
    (sl_price, tp_price)
    """
    if direction.upper() == "BUY":
        sl = price - sl_mult * atr_value
        tp = price + tp_mult * atr_value
    elif direction.upper() == "SELL":
        sl = price + sl_mult * atr_value
        tp = price - tp_mult * atr_value
    else:
        raise ValueError(f"direction должен быть BUY или SELL, получено: {direction}")

    return round(sl, 6), round(tp, 6)


def format_telegram_message(symbol: str, result: dict, sl: float, tp: float) -> str:
    """
    Форматирует HTML-сообщение для Telegram уведомления о сделке.
    Используй с parse_mode='HTML'.
    """
    sig = result["signal"]
    if sig is None:
        return ""

    emoji = "🟢" if sig == "BUY" else "🔴"
    arrow = "📈" if sig == "BUY" else "📉"
    price = result["price"]
    risk = abs(price - sl)
    reward = abs(tp - price)
    rr = reward / risk if risk > 0 else 0.0

    return (
        f"{emoji} <b>{sig}  {symbol}</b>  {arrow}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Цена:</b>  <code>{price}</code>\n"
        f"<b>SL:</b>     <code>{sl}</code>\n"
        f"<b>TP:</b>     <code>{tp}</code>\n"
        f"<b>R:R:</b>    1 : {rr:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Тренд:</b>     {result['trend_strength']}  (Supertrend {result['supertrend']})\n"
        f"<b>ADX:</b>       {result['adx']}\n"
        f"<b>RSI:</b>       {result['rsi']}\n"
        f"<b>Volume:</b>    {result['volume_ratio']}x от среднего\n"
        f"<b>ATR:</b>       {result['atr']}\n"
        f"<b>Фильтров:</b>  {result['signal_strength']}/4 ✅"
    )


def should_trade_now(result: dict) -> bool:
    """
    Проверяет: можно ли открывать сделку прямо сейчас?
    Удобно для main-loop, чтобы не дублировать проверки.
    """
    return result["signal"] in ("BUY", "SELL") and result["signal_strength"] == 4


# ============================================================================
#  Самотест — `python "..Custom for trade...py"`
# ============================================================================

if __name__ == "__main__":
    rng = np.random.default_rng(42)
    n = 300

    trend = np.linspace(100, 130, n)
    noise = rng.normal(0, 0.5, n)
    closes = trend + noise
    highs = closes + rng.uniform(0.1, 0.6, n)
    lows = closes - rng.uniform(0.1, 0.6, n)
    opens = np.r_[closes[0], closes[:-1]]
    volumes = rng.uniform(800, 1200, n)
    volumes[-5:] *= 1.8

    candles_list = [
        [i * 900_000, opens[i], highs[i], lows[i], closes[i], volumes[i]]
        for i in range(n)
    ]

    print("=" * 60)
    print("  ..Custom for trade..   —   self-test")
    print("=" * 60)
    result = calculate_signals(candles_list)
    for k, v in result.items():
        print(f"  {k:<18} : {v}")
    print("=" * 60)

    if result["signal"]:
        sl, tp = get_dynamic_sl_tp(result["price"], result["atr"], result["signal"])
        print(f"\n  SL = {sl}    TP = {tp}")
        print(f"\n  Telegram preview:\n")
        print(format_telegram_message("BTCUSDT", result, sl, tp))
    else:
        print(f"\n  Сигнала нет → бот пропускает свечу.")
        print(f"  Причина: {result['reason']}")

    print("\n  OK — стратегия отработала без ошибок.")
