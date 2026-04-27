import pandas as pd
import ta


def calculate_signals(candles: list[dict]) -> dict:
    """
    Принимает список свечей от Bybit (list of OHLCV).
    Возвращает {"signal": "BUY" | "SELL" | None, "rsi": float, "ema_fast": float, "ema_slow": float}
    """
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    df = df.sort_values("timestamp").reset_index(drop=True)

    close = df["close"]

    rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    ema_fast = ta.trend.EMAIndicator(close=close, window=9).ema_indicator()
    ema_slow = ta.trend.EMAIndicator(close=close, window=21).ema_indicator()

    last_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]
    last_ema_fast = ema_fast.iloc[-1]
    last_ema_slow = ema_slow.iloc[-1]
    prev_ema_fast = ema_fast.iloc[-2]
    prev_ema_slow = ema_slow.iloc[-2]

    signal = None

    # BUY: EMA fast пересекает slow снизу вверх + RSI выходит из oversold
    if (prev_ema_fast <= prev_ema_slow and last_ema_fast > last_ema_slow and last_rsi > 30):
        signal = "BUY"

    # SELL: EMA fast пересекает slow сверху вниз + RSI выходит из overbought
    elif (prev_ema_fast >= prev_ema_slow and last_ema_fast < last_ema_slow and last_rsi < 70):
        signal = "SELL"

    return {
        "signal": signal,
        "rsi": round(last_rsi, 2),
        "ema_fast": round(last_ema_fast, 2),
        "ema_slow": round(last_ema_slow, 2),
        "price": close.iloc[-1],
    }
