# Journal des incidents

Six défauts, tous rendus visibles par un test rouge et corrigés sur la branche
`chore/pyright-venv`. Les deux premiers sont les incidents récurrents demandés
par le brief : ils se reproduisaient en production sans laisser de trace
exploitable. Les quatre suivants sont la raison pour laquelle ils ne laissaient
pas de trace.

Chaque entrée suit la même grille : **symptôme observé → ce que la trace
montrait → cause racine → correctif → test qui verrouille**. Pour les deux
premiers, la détection est prouvée en réintroduisant le défaut.

---

## Incident A — des tours de conversation disparaissent

**Symptôme observé.** Le compteur de tours d'une session est plus bas que le
nombre de messages réellement échangés. Aucune erreur, aucune alerte : la
facturation et les statistiques d'usage sous-comptent, silencieusement.

**Ce que la trace montrait.** Rien d'anormal. Chaque tour produisait sa trace,
son span, sa latence. Le défaut ne portait pas sur un tour mais sur la
*collision* entre deux tours — invisible dans une trace, qui décrit un tour à la
fois. C'est le compteur agrégé qui mentait.

**Cause racine.** `SessionStore.record_turn` lisait, modifiait puis réécrivait le
compteur sans verrou :

```python
count = self._turns.get(session_id, 0)
time.sleep(0.0005)              # l'accès au back-office prend un instant
self._turns[session_id] = count + 1
```

Deux tours concurrents de la même session lisent la même valeur et écrivent la
même valeur + 1 : un des deux tours est perdu. La fenêtre est le temps entre la
lecture et l'écriture, et `SessionStore` est partagé entre tours concurrents par
construction (cf. sa docstring).

**Correctif.** `threading.Lock` autour des quatre accesseurs du store, et
`record_turn` renvoie désormais l'index du tour — commits `1257fc1`, `798f2af`.

```python
# ponytail: un seul verrou pour tout le store ; à découper par session si la
# contention apparaît un jour dans l'histogramme de latence.
```

**Tests qui verrouillent.**

| Test | Ce qu'il exerce |
|---|---|
| `tests/unit/test_session.py::test_record_turn_counts_every_concurrent_turn` | 20 threads × 50 tours = 1000 |
| `tests/integration/test_replay_sessions.py::test_concurrent_turns_on_one_session_are_all_counted` | 8 rejeux concurrents de la même session |

**Preuve de détection.** Verrou retiré de `record_turn` :

```
FAILED tests/integration/...::test_concurrent_turns_on_one_session_are_all_counted
AssertionError: assert 1 == 8
```

Sept tours sur huit perdus. Verrou remis : 33 tests passent.

---

## Incident B — le modèle ne répond pas, et personne ne le sait

**Symptôme observé.** Des tours qui « réussissent » en rendant une réponse vide
à l'utilisateur. Le taux d'erreur reste à zéro, donc aucune alerte ne part.
L'application se déclare en bonne santé pendant que le modèle est injoignable.

**Ce que la trace montrait.** Un span `agent.turn` au statut `OK`, sans span
`llm.invoke` en erreur, et une latence normale. La panne était rigoureusement
invisible : c'est le cas d'école du titre du brief.

**Cause racine.** `Agent._invoke_llm_sync` attrapait `TimeoutError` et renvoyait
`None` au lieu de lever. L'appelant faisait ensuite `reply.content` sur ce
`None`, donc l'erreur ne se manifestait pas là où elle naissait mais trois
lignes plus loin, avec un message qui ne parle ni de timeout ni de LLM :

```
AttributeError: 'NoneType' object has no attribute 'content'
src/mardik/agent.py:154
```

Un `except` qui renvoie une valeur par défaut convertit une panne franche en
donnée corrompue qui voyage. C'est le défaut le plus coûteux du lot : il ne
casse pas, il ment.

**Correctif.** Lever `LLMTimeoutError`, une erreur du domaine, et compter le tour
dans `errors_total` — commits `d99e779`, `87f070e`.

```python
except TimeoutError as exc:
    raise LLMTimeoutError(str(exc)) from exc
```

Le `finally` qui enregistre la latence est conservé : un tour qui échoue a une
durée, et c'est souvent la plus intéressante.

**Test qui verrouille.**
`tests/integration/test_replay.py::test_replay_timeout_incident`, qui rejoue la
session enregistrée `sessions/incident_timeout.json` et exige
`pytest.raises(LLMTimeoutError)`.

**Preuve de détection.** `return None` réintroduit :

```
FAILED tests/integration/test_replay.py::test_replay_timeout_incident
AttributeError: 'NoneType' object has no attribute 'content'
```

Le test est rouge, et le span porte désormais le statut `ERROR` avec l'exception
en événement (`test_failure_marks_the_spans_in_error`).

---

## Les quatre défauts d'observabilité

Ceux-ci n'étaient pas des pannes fonctionnelles : c'est ce qui empêchait de
diagnostiquer les deux précédentes.

| Cause racine | Conséquence | Correctif |
|---|---|---|
| `build_agent` ignorait son paramètre `telemetry` (`# TODO`) | toute la production tournait en `NoOpTelemetry` : **zéro trace émise** | `3c46a34` |
| aucun span `tool.call`, aucune métrique de latence, fin de tour dans un `print()` | pas d'appel d'outil visible, pas de latence mesurable, logs non exploitables | `f5f389a`, `87f070e`, `76e7288` |
| le contexte OpenTelemetry ne franchissait pas le thread worker du LLM | `llm.invoke` ouvrait une trace orpheline, détachée de son `agent.turn` | `3a57953` |
| `runner.replay` ne rejouait que le dernier message | les rejeux ne reproduisaient pas les sessions réelles : le modèle ne voyait pas l'historique | `66badc2` |

Le troisième mérite un mot : le contexte OpenTelemetry est stocké en
thread-local, donc il ne traverse pas `threading.Thread` tout seul. Il faut le
capturer côté appelant et le rattacher dans le worker.

```python
parent = otel_context.get_current()

def worker() -> None:
    token = otel_context.attach(parent)
    try:
        ...
    finally:
        otel_context.detach(token)
```

**Preuve de détection.** `attach`/`detach` retirés → 2 tests rouges
(`test_trace_context_propagated_across_threads` et
`test_concurrent_sessions_produce_separate_traces`).

---

## Ce que l'enquête a changé dans l'instrumentation

Trois manques n'étaient pas des défauts listés, mais sont apparus en relisant
les traces dans Jaeger :

- **`session_id` et `turn_index` sur tous les spans.** Sans eux, quatre sessions
  concurrentes sont indistinguables dans le backend.
- **`tool.status` (`ok` / `not_found`).** Une recherche de commande introuvable
  répond sans rien résoudre : techniquement un succès, humainement un échec.
  Aucun compteur d'erreurs ne verra jamais ça.
- **Les entrées du tour.** La trace comptait les messages sans en montrer aucun :
  on voyait *qu'un* tour avait échoué, jamais *pourquoi*. `tool.arguments` est
  désormais toujours enregistré ; les prompts et réponses sont derrière
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, éteint par défaut — un
  message client est une donnée personnelle et le backend de traces est un tiers.

## Ce qui empêchait ces incidents d'être détectés en CI

- **La CI ne lançait que `tests/unit`.** Tous les tests de rejeu étaient du poids
  mort : les défauts pouvaient revenir sans que personne ne le voie — `1a92a73`.
- **`test_replay_smoke` n'assertait que `reply is not None`**, vrai d'une chaîne
  vide comme d'une trace d'exception. Ce test est resté vert à travers *tous* les
  défauts ci-dessus. C'est le brief en miniature : un indicateur qui ne peut pas
  passer au rouge ne surveille rien — `98a0d75`.
- **`uv.lock` était gitignoré**, donc la CI ne testait pas les versions contre
  lesquelles la suite était verte — `28a68f4`.

## Reproduire

```bash
make install
make up                                  # Jaeger : UI 16686, OTLP 4317
make test                                # 33 tests, exporteurs en mémoire
set -a; source .env; set +a
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true make trace
```

`make trace` rejoue les quatre sessions du corpus à travers l'exporteur OTLP
réel et produit une trace par session. Capture d'écran dans
`docs/Les traces permettent de remonter à la cause d'une panne.png`.
