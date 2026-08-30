"""Telegram alerts.

A silent failure during a 5-day judged window costs half the window before
anyone notices, so the agent announces what it does. Every call is best-effort:
a notification failure must never interrupt trading.
"""
import os
from typing import Optional

import requests

TIMEOUT = 8
PREFIX = "\U0001F9ED OptionPilot"   # compass


def _creds():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip().strip('"')
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip().strip('"')
    return (token, chat) if token and chat else (None, None)


def send(text: str, silent: bool = False) -> bool:
    token, chat = _creds()
    if not token:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{PREFIX}\n{text}",
                  "parse_mode": "HTML", "disable_notification": silent},
            timeout=TIMEOUT)
        if not r.ok:
            print(f"[notify] telegram {r.status_code}: {r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"[notify] failed: {e}")
        return False


def on_start(equity: float, execute: bool, llm: bool) -> None:
    send(f"<b>started</b>\nequity ${equity:,.2f} · "
         f"execute={execute} · llm={llm}", silent=True)


def on_fill(strategy: str, underlying: str, occ_symbol: str, qty: int,
            price: float, premium: float, delta, dte) -> None:
    send(f"<b>{strategy} {underlying}</b> filled\n"
         f"<code>{occ_symbol}</code>\n"
         f"{qty}x @ ${price} → ${premium:,.0f} credit\n"
         f"delta {delta} · {dte} DTE")


def on_exit(occ_symbol: str, reason: str, qty: float, mark) -> None:
    send(f"<b>exit</b> {reason}\n<code>{occ_symbol}</code>\n"
         f"{qty}x @ ~${mark}")


def on_stock_liquidation(symbol: str, qty: float, reason: str) -> None:
    send(f"<b>assigned stock closed</b> {symbol}\n{qty} shares · {reason}")


def on_guardrail(symbol: str, event: str, detail: str, action: str) -> None:
    send(f"⚠️ <b>guardrail</b> {symbol}\n{event}: {detail}\n→ {action}")


def on_kill_switch(limit: int) -> None:
    send(f"\U0001F6D1 <b>KILL SWITCH</b>\n{limit} violations — "
         f"LLM execution disabled, falling back to rules")


def on_halt(reason: str) -> None:
    send(f"\U0001F6D1 <b>EMERGENCY HALT</b>\n{reason}")


def on_cycle(slot: str, equity: float, signals: str, mandates: int) -> None:
    send(f"<b>cycle {slot}</b>\nequity ${equity:,.2f} · "
         f"{mandates} mandate(s)\n{signals}", silent=True)


def on_no_trade(underlying: str, reason: str) -> None:
    send(f"no trade {underlying}: {reason}", silent=True)
