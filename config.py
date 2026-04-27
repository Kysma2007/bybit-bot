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

CATEGORY = "linear"  # USDT perpetual futures

# Сколько топ-пар по объёму сканировать
TOP_SYMBOLS_COUNT = 50

# Лимит капитала
CAPITAL_LIMIT = 500.0

# Risk management
LEVERAGE = 5
RISK_PER_TRADE = 0.02        # 2% на сделку (много пар — меньше риск на каждую)
STOP_LOSS_PCT = 0.008        # 0.8% стоп-лосс
TAKE_PROFIT_PCT = 0.02       # 2% тейк-профит
MAX_OPEN_POSITIONS = 10      # до 10 одновременных позиций

# Strategy
TIMEFRAME = "5"
SCAN_INTERVAL = 60           # 60 сек между полными циклами сканирования
