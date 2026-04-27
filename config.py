import os
from dotenv import load_dotenv

load_dotenv()

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

if DEMO_MODE:
    API_KEY = os.getenv("DEMO_API_KEY")
    API_SECRET = os.getenv("DEMO_API_SECRET")
else:
    API_KEY = os.getenv("REAL_API_KEY")
    API_SECRET = os.getenv("REAL_API_SECRET")

SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # USDT perpetual futures

# Лимит капитала — бот не использует больше этой суммы
CAPITAL_LIMIT = 500.0        # $500 (симулируем реальный старт)

# Risk management
LEVERAGE = 5                  # x5 плечо
RISK_PER_TRADE = 0.05        # 5% от депозита на сделку
STOP_LOSS_PCT = 0.008        # 0.8% стоп-лосс
TAKE_PROFIT_PCT = 0.02       # 2% тейк-профит  (ratio 1:2.5)
MAX_OPEN_POSITIONS = 2

# Strategy params
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
EMA_FAST = 9
EMA_SLOW = 21
TIMEFRAME = "5"              # 5-минутные свечи
SCAN_INTERVAL = 30           # секунд между проверками
