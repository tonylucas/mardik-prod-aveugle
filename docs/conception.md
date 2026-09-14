# Travail préliminaire de conception — observabilité de l'agent Mardik

## 1. Test d'intégration d'un agent, et « observer » une application IA

**Unitaire** : une fonction déterministe, sans LLM (parser de tool call,
assemblage de prompt, validation d'arguments, politique de retry). Entrée fixe
→ sortie fixe.

**Intégration** : la *boucle* complète — modèle + outils + mémoire + état de
session — sur un scénario réel. La sortie n'étant pas déterministe, on
n'assertionne pas un texte mais des **invariants** :

- la trajectoire : quels outils, dans quel ordre, combien de fois ;
- l'état final : la réservation est en base, le fichier est écrit ;
- les garde-fous : plafond de tours et de tokens tenu, aucun secret en sortie ;
- le contrat de forme : JSON valide, schéma respecté.

**Multi-sessions** — l'exigence du brief : rejouer plusieurs sessions
concurrentes fait sortir les bugs qui n'existent qu'en concurrence (état de
session qui fuit d'un utilisateur à l'autre, cache partagé, rate-limit,
connexions DB épuisées). Ce sont ceux qui « reviennent sans qu'on sache
pourquoi ».

Quatre régimes, séparés par deux axes : ce qu'on assertionne (invariant binaire
ou score) et quel modèle répond (réponses enregistrées ou modèle réel).

| Régime | Modèle | Assertion | Déterminisme | Cadence | Bloquant |
|---|---|---|---|---|---|
| Unitaire | absent | égalité | total | chaque commit | oui |
| Intégration rejouée | réponses enregistrées | invariants | total | chaque commit / PR | oui |
| Intégration live | réel | invariants | quasi | nocturne | non |
| Évaluation | réel | score de qualité | non | nocturne + prod | non |

« Réponses enregistrées » : on enregistre une fois les réponses réelles du
modèle dans un fichier (`vcrpy`, `respx`), les tests suivants le relisent au
lieu d'appeler le fournisseur — déterministe, gratuit, rapide.

**Seuls les invariants déterministes bloquent la CI** : un rouge y signifie une
régression, pas du bruit. Les **évaluations** (similarité sémantique,
LLM-as-judge) rendent un score qui varie d'un run à l'autre — le modèle comme
le juge sont probabilistes — donc dossier `evals/` séparé, nocturne, non
bloquant, et assertion sur une statistique (moyenne sur N runs, ou 8/10
au-dessus du seuil), jamais sur un run isolé.

**Observer une application IA** = pouvoir répondre après coup, sans reproduire :
*quelle session, quel tour, quel prompt, quels outils, quelle latence, quel
coût, où ça a cassé*. Pour un service classique, un 500 et une stack trace
suffisent. Pour un agent, la panne est souvent un **succès technique au mauvais
résultat** : 200 OK, 8 secondes, et l'agent a bouclé six fois sur le même outil
avant de rendre une réponse inutile — invisible dans les métriques d'infra.
D'où trois différences :

1. l'unité d'observation est la **session**, pas la requête ;
2. il faut capturer le **contenu** (prompt, réponse, arguments d'outil), seul
   moyen de comprendre une décision du modèle — d'où un enjeu immédiat de
   données personnelles et de volume ;
3. les **évaluations en ligne** font partie de l'observabilité : elles seules
   détectent la dégradation silencieuse, quand rien ne casse mais que la
   qualité baisse après un changement de modèle ou de prompt.

## 2. Métriques qui révèlent la santé

**Fiabilité de la boucle agent** — le cœur du diagnostic :
- *tours par session* (p50/p95) : distribution qui s'étale = l'agent tourne en rond ;
- *taux d'échec d'appel d'outil*, par outil (timeout, arguments invalides, exception) ;
- *taux de tool call malformé* : mesure directe d'une dérive du modèle ou d'une régression de prompt ;
- *taux d'atteinte du plafond de tours* — un agent qui touche `max_turns` a abandonné ;
- *taux de sessions sans réponse finale*.

**Qualité / résultat** : taux de complétion de tâche, taux de refus, pertinence
du retrieval (documents trouvés, taux de « zéro résultat »), signal
utilisateur — la reformulation dans les 30 s est le meilleur détecteur d'échec
gratuit qu'on ait.

**Coût et capacité** : tokens et coût par session et par tâche réussie,
latence par appel LLM séparée de la latence outil, time-to-first-token, taux de
429 du fournisseur, hit du cache de prompt.

**Infra / service** : taux d'erreur, latence p50/p95/p99, débit, saturation des
pools (DB, file d'attente) — c'est ce qui casse en multi-sessions. Le CPU/RAM
plateforme est collecté sans code et reste plat sur une app I/O-bound ; deux
exceptions à surveiller vraiment :
- *RAM et OOMKilled* si l'index vectoriel est **embarqué** (FAISS, Chroma
  local) : chaque réplica en détient une copie, la RAM plafonne l'autoscaling.
  Suivre `retrieval.index.size_bytes` et le temps de chargement au démarrage.
  Index managé ou pgvector → la RAM est chez le fournisseur, on surveille sa
  latence.
- *CPU/GPU sur `embed_query`* si le modèle d'embedding tourne **en local**
  (sentence-transformers en process) : seul endroit réellement CPU-bound. Avec
  une API d'embedding distante, inutile de l'instrumenter.

**Tableau de bord de garde, cinq courbes** : taux d'erreur, latence p95, tours
p95, taux d'échec par outil, coût par session. Le reste sert à l'enquête, pas à
l'alerte.

## 3. Points à instrumenter

Une trace exploitable = un arbre de spans dont la racine est la session.

```
session (trace)
└─ turn N                         un message utilisateur → une réponse
   ├─ retrieval                   si RAG
   │  ├─ embed_query
   │  └─ vector_search
   ├─ prompt_build                assemblage, troncature d'historique
   ├─ llm_call                    un aller-retour modèle
   ├─ tool_call: search_stock     un outil, un span
   │  └─ http_request             l'appel sortant réel
   ├─ llm_call                    tour de boucle suivant
   └─ response_validate           schéma, garde-fous, rédaction
```

Attributs par span :

- **session** : `session.id`, `user.id` (pseudonymisé), `app.version`,
  `git.sha`, `prompt.version`, `env`. Sans `git.sha` et `prompt.version`, on ne
  peut pas corréler une dégradation à un déploiement — premier réflexe
  d'enquête, il doit coûter un filtre.
- **turn** : `turn.index`, `turn.outcome` (`completed` / `max_turns` / `error` /
  `refused`), `turn.tokens.total`, `turn.cost_usd`, `turn.duration_ms`.
- **llm_call** : `llm.model`, `llm.tokens.input/output/cached`,
  `llm.finish_reason`, `llm.ttft_ms`, `llm.attempt`, `llm.request.id`
  (identifiant fournisseur, indispensable pour ouvrir un ticket chez lui),
  messages en événements de span.
- **tool_call** : `tool.name`, `tool.args` (rédactés), `tool.status`,
  `tool.error.type`, `tool.duration_ms`. **Un span par appel**, jamais un span
  agrégé : la répétition du même `tool.name` dans une trace *est* le symptôme
  de la boucle.
- **retrieval** : `retrieval.query`, `retrieval.k`, `retrieval.hits`,
  `retrieval.scores` (min/max), `retrieval.index.version`.
- **erreur** : type, message, stack sur le span le plus profond, statut d'erreur
  propagé aux parents.

Trois exigences transverses, sans lesquelles le reste ne sert à rien :

1. **Corrélation** — même `trace.id` et `session.id` dans les traces, les logs
   JSON, les métriques (en exemplar) et la réponse HTTP renvoyée au client :
   c'est ce qui permet de partir d'un ticket utilisateur et d'arriver à la
   trace. Sans ça, trois silos.
2. **Rédaction et échantillonnage** — secrets et PII filtrés *avant* l'export ;
   100 % des traces en métadonnées, une fraction des prompts complets, 100 % sur
   les traces en erreur (*tail sampling*).
3. **OpenTelemetry + conventions `gen_ai.*`** plutôt qu'un schéma maison :
   quelques lignes de setup, et le back-end reste interchangeable.

## 4. Relier un test échoué à une cause via les traces

Principe : **le test émet une trace comme la production**. La CI ne dit pas
« échec », elle dit « échec, voici le lien ».

1. Chaque test ouvre un span racine avec `test.name`, `test.run.id`,
   `session.replay.source` — même exporteur et même schéma d'attributs que la
   prod.
2. Sur échec, le rapport attache le `trace.id` et l'URL du back-end. Le message
   d'assertion nomme l'invariant violé : « 7 appels à `search_stock`, plafond
   3 ».
3. On lit la trajectoire de haut en bas. Le diagnostic tombe presque toujours
   dans un de ces motifs :

| Symptôme dans la trace | Cause typique |
|---|---|
| Même `tool.name` + mêmes `tool.args` répétés | l'agent ne voit pas le résultat : résultat vide, erreur avalée, message d'outil mal réinjecté dans l'historique |
| `tool.status=error`, `error.type=timeout` + retries | dépendance en panne, pas de budget de timeout |
| `llm.finish_reason=length` | troncature : `prompt_build` coupe le contexte utile |
| Tokens croissants tour après tour puis erreur | fenêtre de contexte saturée, pas de résumé d'historique |
| `retrieval.hits=0` puis réponse inventée | index vide ou mauvaise version, pas de garde-fou « aucun document » |
| Arguments d'outil invalides de façon répétée | schéma d'outil ambigu, ou changement de `prompt.version` |
| `session.id` d'un autre utilisateur dans les spans | fuite d'état entre sessions |
| Rouge uniquement en multi-sessions | ressource partagée : cache global, pool DB, rate-limit |

4. **Diff de traces** : on conserve la trace d'une exécution verte de référence
   pour le même scénario. Le premier span qui diverge — en ordre, en attributs
   ou en durée — localise la régression sans lire le reste.
5. **Vérification du correctif** : le test rejoué passe, *et* la métrique
   correspondante redescend en production. Un correctif qui ne bouge pas la
   courbe n'a pas corrigé l'incident.

C'est cette boucle qui ferme le brief : la trace explique le test rouge, le test
rouge garantit que l'incident ne revient pas.

## Schéma d'observabilité

L'arbre de spans de la section 3 est le schéma de trace, les cinq courbes de la
section 2 le tableau de bord. Chaîne d'export :

```
app (SDK OpenTelemetry, conventions gen_ai.*)
  ├─ traces    → collecteur OTel → back-end LLM (Langfuse / Phoenix) + Jaeger
  ├─ métriques → Prometheus → Grafana → alertes
  └─ logs JSON (avec trace.id, session.id) → agrégateur
```

Un seul SDK, trois signaux, corrélés par `trace.id` et `session.id`.

## Références

- **OpenTelemetry, conventions sémantiques GenAI** —
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/> — référence normative
  pour nommer les attributs de span, à reprendre telle quelle.
- **Anthropic, « Building effective agents »** —
  <https://www.anthropic.com/engineering/building-effective-agents> — la boucle
  agent, les plafonds de tours, la trajectoire comme objet à observer.
- Documentation Langfuse « Tracing concepts » et l'article Arize Phoenix sur le
  *LLM tracing* couvrent le même terrain côté outillage.
