"""Run exactly one decision cycle through the REAL main-loop code path
(decision_cycle), with DB writes, EXECUTE honored from .env. Works with the
market closed — quotes are stale but the flow is identical.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from alpaca.trading.client import TradingClient

import db
from config import CONFIG
from core.scanner import Scanner
from core.mandate import MandateBuilder
from core.executor_direct import DirectExecutor
from core.risk import PositionManager
from data.feed import AlpacaFeed
from data.options import OptionsData
from ledger import Ledger
from main import decision_cycle, MARKET

key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]

db.init_db()
trading = TradingClient(key, secret, paper=True)
ledger = Ledger()
scanner = Scanner(AlpacaFeed(key, secret), CONFIG)
builder = MandateBuilder(CONFIG, ledger)
executor = DirectExecutor(key, secret, OptionsData(key, secret))
equity = float(trading.get_account().equity)
pm = PositionManager(CONFIG, initial_equity=equity, market=MARKET)

print(f"one cycle: EXECUTE={CONFIG.execute} equity=${equity:,.2f}")
decision_cycle(trading, scanner, builder, executor, None, pm, ledger, equity)

conn = db.get_connection()
c = conn.cursor()
for table in ("decisions", "trades", "equity"):
    c.execute(f"SELECT COUNT(*) FROM {table}")
    print(f"  db.{table}: {c.fetchone()[0]} rows")
conn.close()
