# OptionPilot — bounded-autonomy options agent

**Alpaca AI Trading Agents Hackathon · team `ldanielrod_team` · Aug 28 – Sep 4, 2026**

An LLM that can place any order it wants is a liability. An LLM that can only
choose *within* a mandate, and whose every order is re-read from the broker and
audited, is an execution specialist. OptionPilot is built on that split, and the
most useful thing it produced in seven days is evidence about where each layer
earns its place.

## AI logic

**Layer 1 — deterministic strategy core.** Multi-factor signals (EMA 15/40 trend
scaled by ADX, RSI + MACD momentum, Bollinger mean reversion) on closed daily
bars for eight liquid megacaps.

**Layer 2 — the volatility gate.** A BUY signal is not sufficient to sell a put.
Selling premium is a bet that implied vol is rich relative to what the
underlying actually delivers, which is a different claim from "price goes up."
A mandate is only issued if contract IV exceeds the underlying's 20-day realized
vol by 10%. Without this gate the strategy sells premium on a directional signal
alone — a common and unexamined mistake.

**Layer 3 — the mandate.** The signal becomes a bounded instruction: *sell one
cash-secured put on NVDA, |delta| 0.20–0.35, 4–35 DTE, OI ≥ 300, spread ≤ 10% of
mid, strike ≤ $376, IV ≥ 51.2%, using exactly this client_order_id.* Sizing,
concentration and every account-level cap are resolved here, before any executor
sees it.

**Layer 4 — Claude as execution trader.** A Claude session with Alpaca's official
MCP server attached over stdio reads the live chain and picks one contract inside
the mandate, then returns a structured decision log with its thesis.

**Layer 5 — verification.** Every order is re-read from the broker through
alpaca-py and validated against eleven mandate properties. Violations are
cancelled; a violating order that already filled is flattened. Two violations
permanently demote execution to the deterministic selector.

## What the layers actually did — the NVDA case

Asked to select a put on NVDA, Claude compared the chain and chose the Sep-18
210 strike over the richer-looking near-week contracts, arguing that the front
week's superior annualized return was compensation for peak gamma 2.3% out of
the money on a name that had just moved 4.7% in a session, not exploitable edge,
and that the monthly's 1.4% spread beat the 3–6% available elsewhere.

That is real judgment, and the rule-based selector — which takes the contract
nearest the middle of the delta band — cannot express it.

**And the volatility gate blocked the trade anyway.** NVDA's 20-day realized vol
was 46.5% while every qualifying contract paid 34–40% implied: the stock was
moving more than the options charged, the classic post-earnings IV crush. Zero
of seven contracts cleared the floor. Claude reasoned well about term structure,
spread and gamma, and still did not notice it was being underpaid for the risk.

This is the argument of the whole system in one trade. The model adds
discrimination *within* a decision that has already been judged worth making.
It is not qualified to decide whether the trade should exist, and it is not
asked to.

## Risk controls

Every control is programmatic and lives outside the LLM's reach.

| Control | Mechanism |
|---|---|
| Sizing | Quantity fixed by the core; the LLM cannot change it |
| Vol premium | Contract IV ≥ 20-day realized vol × 1.10, or no mandate |
| Concentration | ≤35% strike notional per name, ≤60% total, **≤25% of equity in aggregate long-delta equivalent** |
| Selection bounds | Delta band, DTE window, OI floor, spread ceiling, strike cap |
| Execution | Limit orders only, priced inside the NBBO |
| Drawdown | Halt new entries at −12%, with hysteresis; exits keep running |
| Exits | Buy-to-close at 50% of premium, stop at 2.2× premium, forced close ≤1 DTE |
| Assignment | Assigned shares exit on a bearish signal or an 8% stop — closing any covering call first, so the account is never left holding a naked call |
| Verification | Independent broker re-read; violations cancelled, fills flattened |
| Kill switch | Two violations permanently disable LLM execution |

Strike notional understates real exposure: six 0.30-delta puts on correlated
megacaps carry roughly 40% of equity in long-delta equivalent, concentrated in a
single factor. The aggregate delta cap exists because the notional caps hid that.

## Alpaca infrastructure

- **Trading API via alpaca-py** — account state, contract discovery, snapshots
  with Greeks, limit orders, positions, exits, and the independent audit path
  that verifies what the agent did.
- **Alpaca MCP Server (official, v2)** — spawned as a stdio child process of the
  agent container, so credentials never leave the host. The session is granted a
  whitelist of six read tools plus `place_option_order`; stock orders, account
  configuration and close-all tools are withheld.
- **Paper environment**, options level 3, dedicated competition account.

Deployed as an isolated Docker stack (agent plus its own Postgres) with Telegram
alerting on fills, exits, guardrail events and halts.

## Provenance

The signal engine (`core/signals.py`, `core/indicators.py`), the risk manager
(`core/risk.py`) and the market data feed (`data/feed.py`) are adapted from the
author's own production trading bot, which has run continuously in a live paper
account since June 2026 across four markets. Everything else — mandates,
guardrails, verification, the LLM layer, options data, exits, attribution — is
new for this hackathon.

That inheritance is not an alpha claim, and it should not be read as one. What
months of live operation produced is **operational hardening**, and the specific
controls it bought are visible in the code: the drawdown halt has hysteresis
because without it a halted bot needed a gain it was forbidden to earn in order
to resume; equity is confirmed with a second read because one bad broker
response triggered 83 restarts in a day; signals run on daily bars because
intraday churn destroyed the edge in live trading; a failed close is
distinguished from a flat close because conflating them once produced thousands
of phantom trade records. Those are scars, not backtests.

## Limitations, stated plainly

- **Five sessions is not evidence.** Short puts at 0.25 delta win most of the
  time individually and lose rarely and largely. A one-week sample of a
  short-premium strategy is very likely to look profitable and tells you almost
  nothing. Whatever P&L this account shows, it does not establish edge.
- **The attribution experiment is a method, not a result.** Every mandate logs
  both Claude's contract and the rule-based baseline for the same mandate,
  computed at the same instant and never shown to the model (`attribution.py`).
  With fewer than ten paired decisions it has no statistical power. It is
  reported either way, including if the baseline wins.
- **The structure is conventional.** A cash-secured put at 0.25 delta is the most
  common retail options trade there is. The contribution here is not the
  structure; it is the gate that decides when the structure is worth selling and
  the architecture that lets a model choose inside it safely.
- **Realized vol is trailing** and includes earnings gaps, so the filter is
  conservative right after a report. In the NVDA case that bias pointed the
  right way; it will not always.
- **Single-leg only, by choice.** Level 3 permits spreads. Introducing an
  untested order class into a live account with four days left, before a single
  real fill had been observed, is how a working submission breaks. Spreads are
  future work.

MIT licensed · one developer · seven days
