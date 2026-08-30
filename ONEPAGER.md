# OptionPilot — bounded-autonomy options agent

**Alpaca AI Trading Agents Hackathon · Aug 28 – Sep 4, 2026**

An LLM that can place any order it wants is a liability, not a strategy. An
LLM that can only choose *within* a mandate written by a proven quantitative
core is an execution specialist. OptionPilot is built on that split.

## AI logic

**Layer 1 — deterministic strategy core.** A multi-factor signal engine
(EMA 15/40 trend scaled by ADX, RSI + MACD momentum, Bollinger mean reversion)
runs on closed daily bars for eight liquid megacaps. This engine is not
written for the hackathon: it has been trading a live paper portfolio
continuously since June 2026, and OptionPilot inherits it directly.

**Layer 2 — the mandate.** A BUY signal does not become an order; it becomes
an `OptionMandate`: *sell one cash-secured put on NVDA, |delta| 0.20–0.35,
4–35 DTE, OI ≥ 300, spread ≤ 10% of mid, strike ≤ $376, using exactly this
client_order_id.* Every account-level cap is resolved here, before any
executor sees it.

**Layer 3 — Claude as execution trader.** A Claude session with Alpaca's
official MCP server attached over stdio reads the live chain, compares the
plausible candidates on premium-per-unit-risk, IV, spread and open interest,
places one limit order, and returns a structured decision log with its thesis.
A real session picked the Sep-18 210 put over the nearer weeklies, reasoning
that post-earnings IV was flat across the window so the edge was in liquidity
and theta-per-day rather than vol timing — the monthly had a 5-cent market
(1.4% of mid vs 3–6% elsewhere) and the best theta per dollar of secured cash
in the band. That is judgment the rule-based selector cannot express: the
deterministic path picks nearest-delta and would have taken a wider,
thinner strike.

## Risk controls

Every control is programmatic and lives outside the LLM's reach.

| Control | Mechanism |
|---|---|
| Sizing | Quantity is fixed by the core; the LLM cannot change it |
| Concentration | ≤35% strike notional per underlying, ≤60% total short-put notional |
| Selection bounds | delta band, DTE window, OI floor, spread ceiling, strike cap |
| Execution | Limit orders only, priced inside the NBBO |
| Drawdown | Halt new entries at −12% with hysteresis; exits keep running |
| Exits | Buy-to-close at 50% of premium, stop at 2.2× premium, forced close ≤1 DTE |
| Verification | Every order re-read from the broker via alpaca-py and validated against its mandate |
| Kill switch | Violations are cancelled and counted; two violations permanently demote execution to the deterministic path |

The verification layer is the part worth stressing: the agent's own account of
what it did is treated as evidence, never as truth. Orders are fetched
independently, OCC symbols parsed, and eleven properties checked. A violating
order is cancelled; if it already filled, the position is flattened. This is
red-teamed in `tests/test_reconcile_redteam.py` with a broker simulator that
feeds the reconciler oversized, off-mandate, foreign-ID, and already-filled
violating orders.

## Alpaca infrastructure

- **Trading API via alpaca-py** — account state, option contract discovery,
  snapshots with Greeks, limit orders, positions, the independent audit path,
  and exits.
- **Alpaca MCP Server (official, v2)** — spawned as a stdio child process of
  the agent container, credentials never leaving the host. The Claude session
  is granted a whitelist of six read tools plus `place_option_order`; stock
  orders, account configuration and close-all tools are withheld.
- **Paper environment** — options level 3, dedicated competition account.

Deployed as an isolated Docker stack (agent + its own Postgres) alongside, and
strictly separate from, the author's production trading bot.

## What this is not

The LightGBM confirmator from the parent project is trained on BTC funding and
sentiment features; applying it to equities would be dressing. It is excluded.
Multi-leg spreads are implemented in Alpaca's API and reachable at level 3, but
are only enabled if the single-leg path proves stable first.

MIT licensed · built by one developer in seven days
