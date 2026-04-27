import time
import math
import logging
from pybit.unified_trading import HTTP
from config import (
    API_KEY, API_SECRET, DEMO_MODE, CATEGORY,
    LEVERAGE, RISK_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    MAX_OPEN_POSITIONS, TIMEFRAME, SCAN_INTERVAL, CAPITAL_LIMIT,
    TOP_SYMBOLS_COUNT
)
from strategy import calculate_signals
from telegram_notify import notify_start, notify_trade, notify_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)


def get_client() -> HTTP:
    return HTTP(
        testnet=False,
        demo=DEMO_MODE,
        api_key=API_KEY,
        api_secret=API_SECRET,
    )


def get_top_symbols(client: HTTP) -> list[str]:
    """Топ пар по 24ч объёму торгов."""
    r = client.get_tickers(category=CATEGORY)
    tickers = r["result"]["list"]
    usdt = [t for t in tickers if t["symbol"].endswith("USDT")]
    usdt.sort(key=lambda t: float(t.get("turnover24h", 0)), reverse=True)
    symbols = [t["symbol"] for t in usdt[:TOP_SYMBOLS_COUNT]]
    log.info(f"Сканируем {len(symbols)} пар: {symbols[:10]}...")
    return symbols


def get_balance(client: HTTP) -> float:
    r = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    coins = r["result"]["list"][0]["coin"]
    for c in coins:
        if c["coin"] == "USDT":
            val = c.get("availableToWithdraw") or c.get("walletBalance") or "0"
            return float(val)
    return 0.0


def get_candles(client: HTTP, symbol: str) -> list:
    r = client.get_kline(category=CATEGORY, symbol=symbol, interval=TIMEFRAME, limit=100)
    return r["result"]["list"]


def get_open_positions(client: HTTP) -> dict[str, dict]:
    r = client.get_positions(category=CATEGORY, settleCoin="USDT")
    return {
        p["symbol"]: p
        for p in r["result"]["list"]
        if float(p["size"]) > 0
    }


def set_leverage(client: HTTP, symbol: str):
    try:
        client.set_leverage(
            category=CATEGORY,
            symbol=symbol,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
    except Exception:
        pass


def calc_qty(price: float, balance: float) -> float:
    effective_balance = min(balance, CAPITAL_LIMIT)
    trade_capital = effective_balance * RISK_PER_TRADE * LEVERAGE
    qty = trade_capital / price
    return math.floor(qty * 1000) / 1000


def place_order(client: HTTP, symbol: str, side: str, price: float, balance: float):
    qty = calc_qty(price, balance)
    if qty <= 0:
        return

    if side == "BUY":
        sl = round(price * (1 - STOP_LOSS_PCT), 4)
        tp = round(price * (1 + TAKE_PROFIT_PCT), 4)
        bybit_side = "Buy"
    else:
        sl = round(price * (1 + STOP_LOSS_PCT), 4)
        tp = round(price * (1 - TAKE_PROFIT_PCT), 4)
        bybit_side = "Sell"

    try:
        r = client.place_order(
            category=CATEGORY,
            symbol=symbol,
            side=bybit_side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl),
            takeProfit=str(tp),
            timeInForce="GTC",
        )
        order_id = r["result"].get("orderId")
        log.info(f"ОРДЕР {side} {symbol} | qty={qty} | цена={price} | SL={sl} | TP={tp} | id={order_id}")
        notify_trade(side, symbol, qty, price, sl, tp, DEMO_MODE)
    except Exception as e:
        log.error(f"Ошибка ордера {symbol}: {e}")


def run():
    log.info(f"=== БОТ ЗАПУЩЕН | ДЕМО={DEMO_MODE} | ТОП-{TOP_SYMBOLS_COUNT} пар | x{LEVERAGE} ===")
    client = get_client()

    balance = get_balance(client)
    notify_start(DEMO_MODE, balance, CAPITAL_LIMIT)

    consecutive_errors = 0

    while True:
        try:
            balance = get_balance(client)
            open_positions = get_open_positions(client)
            open_count = len(open_positions)

            log.info(f"Баланс={balance:.2f} USDT | Открытых позиций={open_count}/{MAX_OPEN_POSITIONS}")

            if open_count >= MAX_OPEN_POSITIONS:
                log.info("Лимит позиций достигнут, ждём")
                time.sleep(SCAN_INTERVAL)
                continue

            symbols = get_top_symbols(client)
            signals_found = 0

            for symbol in symbols:
                if open_count + signals_found >= MAX_OPEN_POSITIONS:
                    break
                if symbol in open_positions:
                    continue

                try:
                    candles = get_candles(client, symbol)
                    sig = calculate_signals(candles)

                    if sig["signal"]:
                        log.info(f"СИГНАЛ {sig['signal']} на {symbol} | RSI={sig['rsi']} | цена={sig['price']}")
                        set_leverage(client, symbol)
                        place_order(client, symbol, sig["signal"], sig["price"], balance)
                        signals_found += 1

                except Exception as e:
                    log.warning(f"Ошибка {symbol}: {e}")
                    continue

                time.sleep(0.2)

            consecutive_errors = 0

        except Exception as e:
            consecutive_errors += 1
            log.error(f"Ошибка цикла: {e}")
            if consecutive_errors >= 3:
                notify_error(str(e))
                consecutive_errors = 0

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run()
