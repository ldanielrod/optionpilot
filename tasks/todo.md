# OptionPilot — Alpaca AI Trading Agents Hackathon (28 ago – 4 sep 2026)

Plan aprobado: `/Users/apple/.claude/plans/fluttering-swinging-tide.md`

## Acciones del usuario (bloqueantes)
- [ ] Registrarse en lablab.ai (enroll en el hackathon)
- [ ] Crear cuenta paper de COMPETICIÓN dedicada en Alpaca ($100k) — la actual queda como TEST
- [ ] Pasar las keys de la cuenta de competición (para el `.env` live del 28)
- [ ] API key de Anthropic para la capa LLM (`ANTHROPIC_API_KEY`)

## Mié 27 — scaffold + smoke test
- [x] Repo `optionpilot` creado + git init
- [x] Smoke test alpaca-py opciones en cuenta TEST: cuenta nivel 3, cadena AAPL, orden límite submit→cancel — PASSED
- [x] Módulos copiados: signals_enhanced, indicators, feed, risk (PositionManager), db, ledger
- [x] config.py nuevo (bandas delta/DTE, caps, flags EXECUTE/LLM_ENABLED)
- [x] db.py adaptado (llm_decisions, guardrail_events, risk_state + helpers)
- [x] Earnings verificados: NVDA reportó 26-ago; ninguno de los 8 reporta en la ventana
- [x] core/signals.py verificado (fallback permisivo de regime funciona)
- [x] ledger.py adaptado a opciones
- [x] data/options.py (wrapper cadena/quotes para el core)
- [x] core/scanner.py (barras diarias → SignalEvent)
- [x] core/mandate.py (SignalEvent → OptionMandate) — cap por nombre corregido a 35% tras dry-run
- [x] core/executor_direct.py (CSP delta-nearest determinista) — dry-run OK: NVDA260904P00220000 δ-0.27
- [x] main.py MVP (loop determinista) + core/exits.py + core/occ.py
- [x] .env.example, README, LICENSE, requirements.txt, .gitignore, .env local con keys TEST

## Jue 28 / Vie 28 — MVP live
- [ ] Loop completo con EXECUTE=false en TEST
- [ ] EXECUTE=true en TEST → 1 CSP real
- [ ] Cambiar a keys de COMPETICIÓN → primera CSP live el viernes (INNEGOCIABLE)

## Sáb 29 — capa LLM
- [ ] Dockerfile (py3.11 + Node + claude-agent-sdk + alpaca-mcp-server)
- [ ] agent/llm_trader.py + prompts.py + decision_schema.py
- [ ] Shadow mode vs TEST

## Dom 30 — guardrails + deploy
- [ ] core/guardrails.py + reconcile.py + kill switch + tests
- [ ] core/exits.py + mandato CC
- [ ] Deploy a /opt/optionpilot en el server (prod intacto)

## Lun 31 – Vie 4 — live + entrega
- [ ] LLM_ENABLED=true en competición (lun 31)
- [ ] report.py + one-pager + presentación/video
- [ ] Vie 4: cierre ≤1 DTE 15:30 ET, snapshot final, submit

## Notas técnicas
- Venv fuera de Documents (iCloud evicta archivos → imports cuelgan): `~/.venvs/optionpilot` (alpaca-py 0.44.0)
- Keys en .env están ENTRE COMILLAS — strip al extraer por shell
- Snapshots de strikes ilíquidos vienen sin griegas (delta=None) — tolerar en executor y filtrar por OI
- Cuenta TEST = paper actual del bot (PA3AY0UVTI8Y, equity ~$107k, options level 3)

## Día 2 real (sáb 29) — hecho
- [x] Postgres local dev (docker, 5433) + schema + risk_state helpers probados
- [x] main.py refactor: decision_cycle() extraído y testeable
- [x] Ciclo completo con DB y EXECUTE=0 vía código real de main: OK (AAPL+NVDA CSP dry-run)
- [x] Capa LLM completa: decision_schema, prompts, llm_trader (Agent SDK + alpaca-mcp-server stdio)
- [x] guardrails.py + tests unitarios (8 violaciones detectadas + caso limpio) — ALL PASSED
- [x] Shadow session real: LLM eligió NVDA260918P00210000 δ-0.302 con tesis sólida (18 turnos, $1.06)
- [x] Dockerfile (py3.11+node20+claude-code CLI+mcp-server) + compose + deploy.sh
- [x] Desplegado al server en ~/optionpilot (NO /opt: sin sudo passwordless) — agente corriendo EXECUTE=0
- [x] REPO MOVIDO a ~/Projects/optionpilot (iCloud evictaba archivos en Documents, Errno 89)

## Lunes 31 (mercado abre)
- [ ] Verificar ciclos dry-run del fin de semana en logs del server
- [ ] EXECUTE=1 en TEST → 1 CSP real de validación
- [ ] Keys de competición + ANTHROPIC_API_KEY al .env del server → LLM_ENABLED=1
- [ ] Primera operación en cuenta de competición

## Notas nuevas
- Disco del Mac al 98% (4.6GB libres) — causa cuelgues de FS; usuario debe liberar espacio
- Costo LLM: ~$1/sesión (18 turnos) → presupuestar ~$3-10/día live
- Puerto 5433 ocupado en server → postgres de optionpilot sin puerto al host
