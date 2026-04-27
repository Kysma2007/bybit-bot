import pandas as pd
import ta


def calculate_signals(candles: list) -> dict:
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    df = df.sort_values("timestamp").reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # --- Индикаторы ---
    rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    ema9 = ta.trend.EMAIndicator(close=close, window=9).ema_indicator()
    ema21 = ta.trend.EMAIndicator(close=close, window=21).ema_indicator()
    ema50 = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()

    macd_obj = ta.trend.MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
    macd = macd_obj.macd()
    macd_signal = macd_obj.macd_signal()
    macd_hist = macd_obj.macd_diff()

    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()

    stoch = ta.momentum.StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
    stoch_k = stoch.stoch()
    stoch_d = stoch.stoch_signal()

    # --- Последние значения ---
    i = -1
    p = -2  # предыдущая свеча

    r, r_p = rsi.iloc[i], rsi.iloc[p]
    e9, e9_p = ema9.iloc[i], ema9.iloc[p]
    e21, e21_p = ema21.iloc[i], ema21.iloc[p]
    e50 = ema50.iloc[i]
    mc, mc_p = macd.iloc[i], macd.iloc[p]
    ms, ms_p = macd_signal.iloc[i], macd_signal.iloc[p]
    mh = macd_hist.iloc[i]
    bbu = bb_upper.iloc[i]
    bbl = bb_lower.iloc[i]
    bbm = bb_mid.iloc[i]
    sk, sk_p = stoch_k.iloc[i], stoch_k.iloc[p]
    sd, sd_p = stoch_d.iloc[i], stoch_d.iloc[p]
    price = close.iloc[i]

    # --- Сигналы на покупку (считаем очки) ---
    buy_score = 0
    sell_score = 0

    # 1. EMA тренд
    if e9 > e21 > e50:
        buy_score += 2
    if e9 < e21 < e50:
        sell_score += 2

    # 2. EMA пересечение
    if e9_p <= e21_p and e9 > e21:
        buy_score += 2
    if e9_p >= e21_p and e9 < e21:
        sell_score += 2

    # 3. RSI зоны
    if 30 < r < 60 and r > r_p:
        buy_score += 1
    if 40 < r < 70 and r < r_p:
        sell_score += 1

    if r_p < 35 and r > r_p:   # выход из oversold
        buy_score += 2
    if r_p > 65 and r < r_p:   # выход из overbought
        sell_score += 2

    # 4. MACD
    if mc_p <= ms_p and mc > ms:   # бычье пересечение MACD
        buy_score += 2
    if mc_p >= ms_p and mc < ms:   # медвежье пересечение MACD
        sell_score += 2

    if mh > 0 and macd_hist.iloc[p] < 0:  # гистограмма ушла в плюс
        buy_score += 1
    if mh < 0 and macd_hist.iloc[p] > 0:
        sell_score += 1

    # 5. Bollinger Bands
    if price <= bbl * 1.005:    # цена у нижней полосы
        buy_score += 2
    if price >= bbu * 0.995:    # цена у верхней полосы
        sell_score += 2

    if price > bbm and close.iloc[p] <= bbm:   # пробой середины вверх
        buy_score += 1
    if price < bbm and close.iloc[p] >= bbm:
        sell_score += 1

    # 6. Stochastic
    if sk_p <= sd_p and sk > sd and sk < 40:   # пересечение в зоне oversold
        buy_score += 2
    if sk_p >= sd_p and sk < sd and sk > 60:   # пересечение в зоне overbought
        sell_score += 2

    # --- Решение: нужно 4+ очков ---
    signal = None
    if buy_score >= 4 and buy_score > sell_score:
        signal = "BUY"
    elif sell_score >= 4 and sell_score > buy_score:
        signal = "SELL"

    return {
        "signal": signal,
        "rsi": round(r, 2),
        "ema_fast": round(e9, 4),
        "ema_slow": round(e21, 4),
        "macd_hist": round(mh, 6),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "price": price,
    }
