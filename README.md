# OptionPilot

Hybrid AI options-trading agent for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (Aug 28 – Sep 4, 2026).

## Architecture

**Deterministic core proposes; a bounded LLM executes.**

1. **Signal engine** (`core/signals.py`): multi-factor daily-bar signals (EMA
   15/40 trend scaled by ADX, RSI + MACD momentum, Bollinger mean-reversion)
   proven in months of live paper trading by the production bot this project
   descends from.
2. **Mandate layer** (`core/mandate.py`): converts signals into
   `OptionMandate`s — strategy (cash-secured put / covered call), fixed
   quantity, delta band, DTE window, liquidity floor, strike cap, and a
   mandatory `client_order_id`. Every account-level cap is enforced here,
   before any executor sees the mandate.
3. **LLM trader** (`agent/`): a Claude session connected to Alpaca's official
   MCP server picks the concrete contract inside the mandate, places the limit
   order, and returns a structured decision log with its thesis.
4. **Verification** (`core/reconcile.py` + `core/guardrails.py`): the core
   independently audits every order via alpaca-py. Out-of-mandate orders are
   cancelled; two violations permanently demote the agent to the
   deterministic executor (`core/executor_direct.py`).
5. **Risk** (`core/risk.py`, `core/exits.py`): drawdown halt with hysteresis,
   per-name and total notional caps, profit-taking at 50% of premium, 2.2×
   premium stop, forced close of anything ≤1 DTE.

## Run

```bash
cp .env.example .env   # fill in Alpaca paper keys + Anthropic key
docker compose up -d
```

`EXECUTE=0` runs every cycle end-to-end without placing orders.
`LLM_ENABLED=0` uses the deterministic executor only.

MIT licensed. Built on Alpaca's Trading API, alpaca-py, and Alpaca's MCP server.
