import time
import math
import logging
from pybit.unified_trading import HTTP
from config import (
    API_KEY, API_SECRET, DEMO_MODE, SYMBOL, CATEGORY,
    LEVERAGE, RISK_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    MAX_OPEN_POSITIONS, TIMEFRAME, SCAN_INTERVAL, CAPITAL_LIMIT
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


def get_balance(client: HTTP) -> float:
    r = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    coins = r["result"]["list"][0]["coin"]
    for c in coins:
        if c["coin"] == "USDT":
            val = c.get("availableToWithdraw") or c.get("walletBalance") or "0"
            return float(val)
    return 0.0


def get_candles(client: HTTP) -> list:
    r = client.get_kline(category=CATEGORY, symbol=SYMBOL, interval=TIMEFRAME, limit=100)
    return r["result"]["list"]


def get_open_positions(client: HTTP) -> list:
    r = client.get_positions(category=CATEGORY, symbol=SYMBOL)
    return [p for p in r["result"]["list"] if float(p["size"]) > 0]


def set_leverage(client: HTTP):
    try:
        client.set_leverage(
            category=CATEGORY,
            symbol=SYMBOL,
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


def place_order(client: HTTP, side: str, price: float, balance: float):
    qty = calc_qty(price, balance)
    if qty <= 0:
        log.warning("Недостаточно баланса для открытия позиции")
        return

    if side == "BUY":
        sl = round(price * (1 - STOP_LOSS_PCT), 2)
        tp = round(price * (1 + TAKE_PROFIT_PCT), 2)
        bybit_side = "Buy"
    else:
        sl = round(price * (1 + STOP_LOSS_PCT), 2)
        tp = round(price * (1 - TAKE_PROFIT_PCT), 2)
        bybit_side = "Sell"

    r = client.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side=bybit_side,
        orderType="Market",
        qty=str(qty),
        stopLoss=str(sl),
        takeProfit=str(tp),
        timeInForce="GTC",
    )
    order_id = r["result"].get("orderId")
    log.info(f"ОРДЕР {side} | qty={qty} | цена={price} | SL={sl} | TP={tp} | id={order_id}")
    notify_trade(side, SYMBOL, qty, price, sl, tp, DEMO_MODE)


def run():
    log.info(f"=== БОТ ЗАПУЩЕН | ДЕМО={DEMO_MODE} | {SYMBOL} x{LEVERAGE} ===")
    client = get_client()
    set_leverage(client)

    balance = get_balance(client)
    notify_start(DEMO_MODE, balance, CAPITAL_LIMIT)

    consecutive_errors = 0

    while True:
        try:
            balance = get_balance(client)
            positions = get_open_positions(client)
            open_count = len(positions)

            candles = get_candles(client)
            sig = calculate_signals(candles)

            log.info(
                f"Баланс={balance:.2f} | Позиций={open_count} | "
                f"RSI={sig['rsi']} | EMA9={sig['ema_fast']} | EMA21={sig['ema_slow']} | "
                f"Цена={sig['price']} | Сигнал={sig['signal']}"
            )

            if sig["signal"] and open_count < MAX_OPEN_POSITIONS:
                place_order(client, sig["signal"], sig["price"], balance)
            elif open_count >= MAX_OPEN_POSITIONS:
                log.info("Лимит позиций достигнут, пропускаем")

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
