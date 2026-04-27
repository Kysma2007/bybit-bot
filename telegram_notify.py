import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")


def _send(text: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


def get_chat_id() -> str:
    """Получить chat_id — запусти один раз после /start боту."""
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=5)
    updates = r.json().get("result", [])
    if updates:
        return str(updates[-1]["message"]["chat"]["id"])
    return ""


def notify_trade(side: str, symbol: str, qty: float, price: float, sl: float, tp: float, demo: bool):
    mode = "🟡 ДЕМО" if demo else "🟢 РЕАЛ"
    emoji = "📈" if side == "BUY" else "📉"
    _send(
        f"{emoji} <b>СДЕЛКА ОТКРЫТА</b> [{mode}]\n"
        f"Пара: <b>{symbol}</b>\n"
        f"Направление: <b>{side}</b>\n"
        f"Объём: {qty}\n"
        f"Цена входа: <b>${price:,.2f}</b>\n"
        f"Стоп-лосс: ${sl:,.2f}\n"
        f"Тейк-профит: ${tp:,.2f}"
    )


def notify_signal(symbol: str, rsi: float, ema_fast: float, ema_slow: float, price: float):
    _send(
        f"🔔 <b>Сигнал на {symbol}</b>\n"
        f"Цена: ${price:,.2f}\n"
        f"RSI: {rsi} | EMA9: {ema_fast} | EMA21: {ema_slow}"
    )


def notify_start(demo: bool, balance: float, capital_limit: float):
    mode = "ДЕМО" if demo else "РЕАЛЬНЫЙ"
    _send(
        f"🚀 <b>Бот запущен [{mode}]</b>\n"
        f"Баланс: ${balance:,.2f}\n"
        f"Торговый лимит: ${capital_limit:,.2f}\n"
        f"Пара: BTCUSDT | Плечо: x5"
    )


def notify_error(msg: str):
    _send(f"⚠️ <b>Ошибка бота:</b>\n{msg}")
