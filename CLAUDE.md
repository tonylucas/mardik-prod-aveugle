# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Nature du dépôt

Ce dépôt est un **exercice** : rendre une application diagnosticable (tests d'intégration
multi-sessions et tracing). `docs/brief.md` décrit la mission, `docs/conception.md` le
travail préliminaire de conception, `docs/INCIDENTS.md` le journal des incidents résolus.

La suite de tests était **rouge à dessein** au départ (9 échecs sur 10). Les six défauts
sont corrigés sur la branche `chore/pyright-venv` ; la suite compte désormais **33 tests,
tous verts**. `docs/INCIDENTS.md` donne la cause racine, le correctif et le test qui
verrouille chacun.

**Les tests sont la spécification.** Ne « corrige » jamais un test pour le faire passer :
c'est le code source qu'on répare. Un test peut en revanche être *durci* quand il
n'assertait rien d'utile (cf. `test_replay_smoke`, commit `98a0d75`).

## Commandes

```bash
make install                      # uv sync
cp .env.example .env              # puis renseigner les valeurs
make up                           # Jaeger all-in-one (UI sur :16686, OTLP gRPC sur :4317)
make test                         # uv run pytest -v (exporteurs en mémoire, pas de réseau)
make trace                        # rejoue le corpus vers Jaeger via OTLP réel
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

## Observabilité

Arbre de spans d'un tour : `agent.turn → llm.invoke` (dans le thread worker) et
`agent.turn → tool.call`. Tous portent `session_id` et `turn_index`, passés **explicitement
en argument** (`agent._tag`) : les mettre en état d'instance recréerait la course corrigée
dans `SessionStore`.

- `llm.invoke` : `gen_ai.operation.name`, `gen_ai.request.message_count`,
  `gen_ai.response.tool_call_count`, et `gen_ai.request.model` si l'adaptateur l'expose.
- `tool.call` : `tool.name`, `tool.arguments` (toujours), `tool.status` = `ok` / `not_found`
  — « l'outil a répondu » n'est pas « l'utilisateur a été aidé ».
- Métriques : `latency_ms`, `errors_total`, `turns_total`, toutes par `session_id`.
- Log `turn.completed` (structlog JSON) avec le `trace_id`, pour passer du log à la trace.
- Le statut `ERROR` et l'événement `exception` viennent **gratuitement** du SDK
  (`start_as_current_span` a `record_exception=True` par défaut) : ne pas les réimplémenter.
- Contenu des prompts et réponses derrière `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`,
  éteint par défaut (donnée personnelle), plafonné à 4000 caractères.

## Pièges

- Les tests utilisent `InMemorySpanExporter` / `InMemoryMetricReader` : **`make test`
  n'envoie rien à Jaeger**, c'est voulu (la CI n'a pas de conteneur). Seul `make trace`
  passe par l'exporteur OTLP.
- Rien ne charge `.env` : `load_settings()` lit `os.environ`. Faire
  `set -a; source .env; set +a` avant `make trace`.
- `app.main()` n'est pas une boucle de conversation : un unique « Bonjour » en dur.
- Le modèle de production n'a aucun outil branché (`get_llm` ne fait pas de `bind_tools`) :
  seuls les faux LLM des tests émettent des `tool_calls`.
- Les logs applicatifs et les messages utilisateur sont en français, le code en anglais.
