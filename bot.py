import time
import math
import logging
from decimal import Decimal, ROUND_DOWN
from pybit.unified_trading import HTTP
from config import (
    API_KEY, API_SECRET, DEMO_MODE, CATEGORY,
    LEVERAGE, RISK_PER_TRADE, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    MAX_OPEN_POSITIONS, TIMEFRAME, SCAN_INTERVAL, CAPITAL_LIMIT,
    TOP_SYMBOLS_COUNT
)
from strategy import calculate_signals
from telegram_notify import notify_start, notify_trade, notify_close, notify_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# Кэш спецификаций инструментов: symbol -> {qtyStep, minOrderQty, tickSize}
_instrument_cache: dict[str, dict] = {}


def get_client() -> HTTP:
    return HTTP(
        testnet=False,
        demo=DEMO_MODE,
        api_key=API_KEY,
        api_secret=API_SECRET,
    )


def load_instruments(client: HTTP):
    """Загружает спецификации всех инструментов в кэш."""
    r = client.get_instruments_info(category=CATEGORY)
    for item in r["result"]["list"]:
        sym = item["symbol"]
        lot = item["lotSizeFilter"]
        price_filter = item["priceFilter"]
        _instrument_cache[sym] = {
            "qtyStep": lot.get("qtyStep", "1"),
            "minOrderQty": lot.get("minOrderQty", "1"),
            "tickSize": price_filter.get("tickSize", "0.01"),
        }
    log.info(f"Загружено {len(_instrument_cache)} инструментов")


def round_step(value: float, step: str) -> str:
    """Округляет value вниз до шага step, возвращает строку."""
    d_value = Decimal(str(value))
    d_step = Decimal(step)
    rounded = (d_value // d_step) * d_step
    return str(rounded.quantize(d_step, rounding=ROUND_DOWN))


def get_top_symbols(client: HTTP) -> list[str]:
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


def calc_qty(symbol: str, price: float, balance: float) -> str | None:
    """Считает размер позиции с учётом qtyStep и minOrderQty конкретной пары."""
    spec = _instrument_cache.get(symbol)
    if not spec:
        return None

    effective_balance = min(balance, CAPITAL_LIMIT)
    trade_capital = effective_balance * RISK_PER_TRADE * LEVERAGE
    raw_qty = trade_capital / price

    qty_str = round_step(raw_qty, spec["qtyStep"])

    # Проверяем минимальный размер
    if Decimal(qty_str) < Decimal(spec["minOrderQty"]):
        log.warning(f"{symbol}: qty {qty_str} < minOrderQty {spec['minOrderQty']}, пропускаем")
        return None

    return qty_str


def round_price(price: float, tick_size: str) -> str:
    return round_step(price, tick_size)


def place_order(client: HTTP, symbol: str, side: str, price: float, balance: float):
    spec = _instrument_cache.get(symbol, {})
    tick = spec.get("tickSize", "0.01")

    qty_str = calc_qty(symbol, price, balance)
    if not qty_str:
        return

    if side == "BUY":
        sl = round_price(price * (1 - STOP_LOSS_PCT), tick)
        tp = round_price(price * (1 + TAKE_PROFIT_PCT), tick)
        bybit_side = "Buy"
    else:
        sl = round_price(price * (1 + STOP_LOSS_PCT), tick)
        tp = round_price(price * (1 - TAKE_PROFIT_PCT), tick)
        bybit_side = "Sell"

    try:
        r = client.place_order(
            category=CATEGORY,
            symbol=symbol,
            side=bybit_side,
            orderType="Market",
            qty=qty_str,
            stopLoss=sl,
            takeProfit=tp,
            timeInForce="GTC",
        )
        order_id = r["result"].get("orderId")
        log.info(f"ОРДЕР {side} {symbol} | qty={qty_str} | цена={price} | SL={sl} | TP={tp} | id={order_id}")
        notify_trade(side, symbol, float(qty_str), price, float(sl), float(tp), DEMO_MODE)
    except Exception as e:
        log.error(f"Ошибка ордера {symbol}: {e}")


def run():
    log.info(f"=== БОТ ЗАПУЩЕН | ДЕМО={DEMO_MODE} | ТОП-{TOP_SYMBOLS_COUNT} пар | x{LEVERAGE} ===")
    client = get_client()
    load_instruments(client)

    balance = get_balance(client)
    notify_start(DEMO_MODE, balance, CAPITAL_LIMIT)

    consecutive_errors = 0
    prev_positions: dict[str, dict] = {}

    while True:
        try:
            balance = get_balance(client)
            open_positions = get_open_positions(client)
            open_count = len(open_positions)

            # Уведомляем о закрытых позициях
            for sym, pos in prev_positions.items():
                if sym not in open_positions:
                    pnl = float(pos.get("unrealisedPnl", 0))
                    side = pos.get("side", "")
                    notify_close(sym, side, pnl, DEMO_MODE)
                    log.info(f"ЗАКРЫТА {sym} {side} | PnL={pnl:.4f}")
            prev_positions = dict(open_positions)

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
                        log.info(
                            f"СИГНАЛ {sig['signal']} {symbol} | "
                            f"RSI={sig['rsi']} | BUY={sig['buy_score']} SELL={sig['sell_score']} | "
                            f"цена={sig['price']}"
                        )
                        set_leverage(client, symbol)
                        place_order(client, symbol, sig["signal"], sig["price"], balance)
                        signals_found += 1
                    else:
                        log.debug(f"{symbol} | BUY={sig['buy_score']} SELL={sig['sell_score']} | нет сигнала")

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
