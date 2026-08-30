"""Write path to the OptionPilot ledger (PostgreSQL).

Pattern adapted from trading_bot fund_logger.py: every write is best-effort —
a DB outage must never stop the trading loop — but failures are printed loudly.
"""
from typing import Optional

from psycopg2.extras import Json

from db import get_connection


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"[ledger] {fn.__name__} failed: {e}")
            return None
    return wrapper


class Ledger:

    @_safe
    def log_trade(self, symbol: str, occ_symbol: Optional[str], strategy: str,
                  side: str, qty: float, price: float, premium: float,
                  delta_at_entry: Optional[float], dte: Optional[int],
                  reason: str, client_order_id: str,
                  broker_order_id: Optional[str], equity_before: float,
                  legs: Optional[list] = None, pnl: Optional[float] = None) -> Optional[int]:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("""
            INSERT INTO trades (symbol, occ_symbol, strategy, side, qty, price,
                                premium, delta_at_entry, dte, legs, pnl, reason,
                                client_order_id, broker_order_id, equity_before)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """, (symbol, occ_symbol, strategy, side, qty, price, premium,
                  delta_at_entry, dte, Json(legs) if legs else None, pnl,
                  reason, client_order_id, broker_order_id, equity_before))
            trade_id = c.fetchone()[0]
            conn.commit()
            return trade_id
        finally:
            conn.close()

    @_safe
    def log_equity(self, equity: float, cash: float,
                   options_buying_power: Optional[float] = None) -> None:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("INSERT INTO equity (equity, cash, options_buying_power) "
                      "VALUES (%s,%s,%s)", (equity, cash, options_buying_power))
            conn.commit()
        finally:
            conn.close()

    @_safe
    def log_decision(self, symbol: str, signal: str, action: str, reason: str,
                     price: Optional[float] = None,
                     details: Optional[dict] = None) -> None:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("INSERT INTO decisions (symbol, signal, action, reason, price, details) "
                      "VALUES (%s,%s,%s,%s,%s,%s)",
                      (symbol, signal, action, reason, price,
                       Json(details) if details else None))
            conn.commit()
        finally:
            conn.close()

    @_safe
    def log_llm_decision(self, session_id: str, symbol: str, mandate: dict,
                         decision: Optional[dict], reasoning: str,
                         num_turns: int, cost_usd: Optional[float],
                         validated: bool,
                         validation_errors: Optional[list] = None,
                         deterministic_pick: Optional[dict] = None,
                         agreed: Optional[bool] = None) -> None:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("""
            INSERT INTO llm_decisions (session_id, symbol, mandate, decision,
                                       reasoning, num_turns, cost_usd, validated,
                                       validation_errors, deterministic_pick, agreed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (session_id, symbol, Json(mandate),
                  Json(decision) if decision else None, reasoning, num_turns,
                  cost_usd, validated,
                  Json(validation_errors) if validation_errors else None,
                  Json(deterministic_pick) if deterministic_pick else None,
                  agreed))
            conn.commit()
        finally:
            conn.close()

    @_safe
    def log_guardrail_event(self, session_id: str, symbol: str, event: str,
                            details: dict, action_taken: str) -> None:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("INSERT INTO guardrail_events (session_id, symbol, event, details, action_taken) "
                      "VALUES (%s,%s,%s,%s,%s)",
                      (session_id, symbol, event, Json(details), action_taken))
            conn.commit()
        finally:
            conn.close()
