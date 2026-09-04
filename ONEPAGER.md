# OptionPilot — bounded-autonomy options agent

**Alpaca AI Trading Agents Hackathon · team `ldanielrod_team`**
Repo: github.com/ldanielrod/optionpilot · Full write-up: [DESIGN.md](DESIGN.md)

An LLM that can place any order it wants is a liability. An LLM that can only
choose *within* a mandate, and whose every order is re-read from the broker and
audited, is an execution specialist. OptionPilot is built on that split.

## AI logic

A deterministic core decides **whether a trade is worth making**; Claude decides
**which contract**.

1. **Signals** — EMA 15/40 trend scaled by ADX, RSI + MACD momentum, Bollinger
   mean reversion, on closed daily bars for eight megacaps.
2. **Volatility gate** — a BUY signal is not enough to sell a put. Selling
   premium is a bet that implied vol is rich relative to what the underlying
   delivers, a different claim from "price goes up." No mandate unless contract
   IV beats 20-day realized vol by 10%.
3. **Mandate** — *sell one cash-secured put on AAPL, |delta| 0.20–0.35, 4–35 DTE,
   OI ≥ 300, spread ≤ 10% of mid, strike ≤ $350, IV ≥ 20.8%, using this exact
   client_order_id.* Every cap is resolved here, before any executor sees it.
4. **Claude via Alpaca's MCP server** reads the live chain plus news, picks one
   contract inside the mandate, and returns a thesis.
5. **Verification** — the core re-reads the order from the broker through
   alpaca-py and checks eleven mandate properties. Violations are cancelled;
   a violating fill is flattened; two violations permanently demote execution
   to the rule-based selector.

**The week in one trade:** Claude argued well for an NVDA put — term structure,
gamma, spread. The volatility gate refused it anyway: realized vol was 46%
against 34–40% implied. The model discriminates *within* a decision already
judged worth making; it is not asked whether the trade should exist.

## Risk controls

All programmatic, all outside the LLM's reach: quantity fixed by the core; IV
floor; ≤35% strike notional per name, ≤60% total, **≤25% of equity in aggregate
long-delta equivalent**; limit orders inside the NBBO only; −12% drawdown halt
with hysteresis; profit take at 50% of premium, stop at 2.2×, forced close ≤1
DTE; assigned shares exit on a bearish signal or an 8% stop, unwinding any
covering call first so the account never holds a naked call.

## Alpaca infrastructure

**Trading API via alpaca-py** for account state, contract discovery, snapshots
with Greeks, limit orders, exits, and the independent audit path.
**Alpaca MCP Server (official, v2)** spawned as a stdio child of the container
so credentials never leave the host; the session gets seven read tools plus
`place_option_order` — stock orders, account config and close-all are withheld.
**Corporate actions feed** refuses splits and blocks covered calls across
ex-dividend dates (early-assignment risk). Paper account, options level 3.

## Results

**$100,000 → $100,142.89 (+0.14%)**, five sessions. One completed round trip:
AAPL Sep-18 310 put sold at $2.82, bought back at $1.39 on the profit take —
**+$143 realized, closed autonomously**. LLM cost: $4.68.

Every mandate also logged what the rule-based selector would have chosen, at the
same instant, never shown to the model. Marked to market: **Claude +$726 vs
rules +$799 — −$73 attributable to model choice**, across four paired decisions
with no statistical power. We report it because a comparison that is only
credible when it flatters the author is not a comparison.

MIT licensed · one developer · seven days
