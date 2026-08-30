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

## Día 3 real (dom 30) — hecho
- [x] Soak del server verificado: 30h up, 1 error de red transitorio manejado por el loop
- [x] Red-team con broker simulado: violación→cancel, filled violatoria→flatten, orden ajena→cancel, kill switch persistente — ALL PASSED
- [x] report.py (equity, posiciones, trades, tesis del LLM, eventos guardrail) + conectado al loop
- [x] ONEPAGER.md borrador (lógica IA / controles de riesgo / infra Alpaca)
- [x] BUG ARREGLADO: due_decision_slot replayaba slots viejos tras restart a media sesión
      (arranque a las 15:00 disparaba 09:45+12:30+15:15 seguidos) → grace de 45min + tests

## ⚠️ RIESGO IDENTIFICADO — no ejecutar en cuenta TEST
La cuenta TEST es la MISMA cuenta paper donde el bot de producción opera STOCKS.
Vender una CSP real ahí: consume options buying power, mete posición corta en las
lecturas de equity/cash del bot de prod → puede disparar sus safety checks
(cash<0 → NO_NEW_BUYS) y descuadrar equity_audit.
DECISIÓN: la primera ejecución real espera a la cuenta de COMPETICIÓN.
Dry-run + shadow ya validaron el camino completo; no hace falta arriesgar prod.

## Lun 31 — CUENTA DE COMPETICIÓN ACTIVA (configurado dom 30 noche)
- [x] Cuenta competición creada: PA3TCQ3FZKYI, $100,000 exactos, options level 3, sin posiciones
- [x] Keys en .env local + server; EXECUTE=1 (ejecución real activa)
- [x] risk_state y tablas del server reseteadas (arrastraban peak_equity=$108,633 de la cuenta TEST
      → habría dado un falso drawdown del 8% desde el arranque)
- [x] Agente redesplegado: equity=$100,000 EXECUTE=True LLM=False
- [ ] FALTA ANTHROPIC_API_KEY → LLM_ENABLED=1 (hoy corre con executor determinista)
- [ ] Supervisar primer ciclo real: lunes 9:45 ET
- [x] CLAUDE_CODE_OAUTH_TOKEN (créditos de suscripción, no API key) en .env + LLM_ENABLED=1
- [x] SESIÓN LLM VERIFICADA DENTRO DEL CONTENEDOR: Node+CLI+OAuth+MCP stdio OK,
      eligió NVDA260918P00210000 δ-0.30, 14 turnos, $0.66, validated=True
- [x] Agente live: equity=$100,000 EXECUTE=True LLM=True

## Entrega en lablab (confirmado 30-ago)
- [x] Equipo creado: `ldanielrod_team` (Alpaca AI Trading Agents Hackathon)
- Deadline real mostrado: 4D 17H desde el 30-ago noche → cierra ~4-sep
- Checklist oficial: 1 Crear equipo ✓ · 6 Prototipo ✓ · faltan 7 Presentación, 8 VIDEO, 9 Submit
- Items 2-4 (invitar, Discord) opcionales para equipo solo, PERO hay 2 premios de
  participación en redes sociales → entrar al Discord y postear tiene valor aparte

## Diferenciador #1: atribución LLM vs reglas (30-ago noche, LIVE antes de la apertura)
- [x] Schema: llm_decisions + deterministic_pick JSONB + agreed BOOLEAN (ALTER idempotente)
- [x] decision_cycle calcula la elección determinista (execute=False) en cada mandato LLM
      y la registra junto a la de Claude — el LLM nunca la ve
- [x] attribution.py: marca a mercado ambos contratos y calcula el edge atribuible al modelo
- [x] report.py muestra divergencia por decisión
- [x] Verificado end-to-end: divergencia detectada y registrada, attribution.py corre
- [ ] Correr attribution.py el jueves con datos reales para el one-pager

## Diferenciador #2 (jueves): guardrails trazados a incidentes reales de producción
Cada control ← la cicatriz que lo produjo: histéresis del halt (trampa absorbente),
doble lectura de equity (83 reinicios por una lectura mala), señales en diario
(churn intradía mataba el edge), close_position None vs 0.0 (2,915 trades fantasma).

## Mejoras quant (30-ago noche, antes de la apertura)
- [x] FILTRO DE PRIMA DE VOLATILIDAD: IV del contrato >= realizada 20d x 1.10.
      Sin esto vendíamos prima sobre una señal direccional, que es otra apuesta.
      Aplica al executor determinista, al prompt del LLM y a los guardrails.
- [x] TOPE DE DELTA AGREGADA: 25% del equity en delta larga equivalente.
      El notional de strike ocultaba que 6 puts a 0.30 delta sobre megacaps
      correlacionadas son ~40% de exposición direccional en un solo factor.
- [x] CSP de income exige estimación de vol (sin ella no hay tesis)
- [x] 5 tests nuevos + suite completa pasando
- [x] VALIDADO EN VIVO: AAPL pasa (rv 18.9% vs IV 26%), NVDA BLOQUEADO
      (rv 46.5% vs IV 34-40% → vender vol por debajo de la realizada, IV crush
      post-earnings). Claude había elegido NVDA el sábado: el filtro lo habría
      parado. Evidencia directa de que el núcleo acota bien al LLM.
- Limitación conocida: la realizada incluye el salto de earnings → filtro
  conservador tras reportes. Documentar en el one-pager, no venderlo como
  estimador sofisticado.

## Huecos técnicos cerrados (30-ago noche)
- [x] HUECO DE RIESGO: acción asignada sin salida. exits.py se saltaba las
      acciones ("lo maneja el mandato CC") y mandate.py se saltaba las señales
      bajistas ("salimos de la acción") — ninguno lo hacía. 100 acciones son
      20-30% de la cuenta sin gestión. Nuevo ExitManager.manage_assigned_stock:
      liquida con señal SELL o stop del 8%, y CIERRA LA COVERED CALL PRIMERO
      (vender la acción bajo una call abierta la dejaría desnuda). 6 tests.
- [x] ALERTAS: notify.py con Telegram (reusa el bot de producción). Avisa de
      arranque, fills, exits, liquidación de asignados, violaciones de
      guardrail, kill switch, halt y resumen de ciclo. Probado: mensaje recibido.
- [x] Healthcheck del contenedor vía heartbeat en /tmp (un loop atascado se ve
      igual que uno sano desde fuera)
- [x] Suite completa: 5 archivos de test, todos pasando
- DESCARTADO conscientemente: spreads multi-pata (MLEG). Nivel 3 los permite
  pero meter un tipo de orden nunca probado en una cuenta viva a 4 días del
  cierre, sin haber visto un fill real, es como se rompe una entrega sana.
  Se declara en el one-pager como decisión de disciplina de riesgo.
