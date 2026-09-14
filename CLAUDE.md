# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Nature du dépôt

Ce dépôt est le **point de départ d'un exercice**, pas une application saine. `brief.md`
décrit la mission (rendre l'application diagnosticable : tests d'intégration multi-sessions
et tracing), `conception.md` contient le travail préliminaire de conception.

**La suite de tests est rouge à dessein : 9 échecs sur 10 tests.** Les tests décrivent le
comportement *attendu* ; c'est le code source qui est défaillant. Ne « corrige » jamais un
test pour le faire passer — c'est la spécification. L'inverse est le travail demandé.

## Commandes

```bash
make install                      # uv sync
cp .env.example .env              # puis renseigner les valeurs
make up                           # Jaeger all-in-one (UI sur :16686, OTLP gRPC sur :4317)
make test                         # uv run pytest -v
make fmt / make lint / make typecheck
make down
```

Un test seul : `uv run pytest tests/unit/test_session.py::test_record_turn_counts_every_concurrent_turn -v`
Un dossier : `uv run pytest tests/integration -v`

`pyproject.toml` pose `pythonpath = ["src"]` et `asyncio_mode = "auto"` — pas besoin
d'installer le paquet ni de décorer les tests asynchrones.

## Architecture

Un tour de conversation traverse toujours la même chaîne :

```
app.build_agent()      assemblage depuis la configuration (llm + tools + telemetry)
   └─ Agent.run_turn(store, session_id, message)     agent.py — l'orchestrateur
        ├─ SessionStore.append / record_turn          session.py — état partagé
        ├─ Agent._invoke_llm → thread → _invoke_llm_sync → LLM.invoke
        └─ Agent._dispatch_tool(call)                 tools.py — outils métier
```

Points structurants, non devinables à la lecture d'un seul fichier :

- **Injection de dépendances partout.** `Agent` reçoit son `llm`, ses `tools` et sa
  `telemetry` ; `build_telemetry()` accepte des exporteurs. C'est ce qui permet aux tests
  de brancher un `InMemorySpanExporter` et un faux LLM sans toucher au réseau. Le protocole
  `LLM` (agent.py) tient en une méthode `invoke(messages) -> Reply`.
- **Deux télémétries.** `Telemetry` (tracer + logger structlog + instruments) et
  `NoOpTelemetry` qui n'enregistre rien. L'agent retombe silencieusement sur la seconde
  quand aucune n'est fournie — c'est la source de plusieurs défauts ci-dessous.
- **Le LLM est appelé dans un thread worker** (`_invoke_llm`), parce que le SDK Azure est
  bloquant. Le contexte OpenTelemetry ne traverse pas une frontière de thread tout seul.
- **`SessionStore` est partagé entre tours concurrents** d'une même session, par
  construction (cf. sa docstring). C'est l'axe multi-sessions du brief.
- **Le rejeu** (`runner.py`) charge un JSON de `sessions/` (`MARDIK_SESSIONS_DIR`) et
  rejoue son dernier message utilisateur à travers l'agent complet. C'est le support des
  tests d'intégration.

## Défauts connus (le travail à faire)

Chacun est couvert par un test rouge :

| Où | Défaut |
|---|---|
| `app.py` | `build_agent` ignore son paramètre `telemetry` (`# TODO`) → tout tourne en `NoOpTelemetry` |
| `agent.py` | `_invoke_llm_sync` avale `TimeoutError` et renvoie `None` au lieu de lever `LLMTimeoutError` |
| `agent.py` | aucun span `tool.call`, aucune mesure `latency_ms`, et la fin de tour part dans un `print()` au lieu d'un log structuré `turn.completed` |
| `agent.py` | le contexte de trace ne franchit pas le thread worker → `llm.invoke` n'est pas rattaché à `agent.turn` |
| `session.py` | `record_turn` lit-modifie-écrit sans verrou → tours perdus en concurrence |
| `sessions/` | `incident_timeout.json` est absent, le test de rejeu correspondant échoue sur `FileNotFoundError` |

## Pièges

- **La CI ne lance que `tests/unit`** (`.github/workflows/ci.yml`) : les tests d'intégration
  ne tournent jamais en CI. C'est cohérent avec le titre du dépôt.
- **`uv.lock` est dans `.gitignore` et n'est pas suivi** — le dépôt n'est donc pas
  reproductible. À corriger : le lock doit être committé.
- Les logs applicatifs et les messages utilisateur sont en français, le code en anglais.
