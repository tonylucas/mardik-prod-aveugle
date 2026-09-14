# L'application qui ment sur sa santé

Chez Mardik, l'application tombe en panne sans qu'on sache pourquoi : pas de tests en conditions réelles, pas de traces. Les incidents reviennent. Vous rendez l'application diagnosticable : tests d'intégration multi-sessions et tracing, pour voir enfin ce qui se passe et réparer les pannes récurrentes.


# Modalités pédagogiques
### Travail préliminaire de conception
- Qu'est-ce qu'un test d'intégration pour un agent (vs unitaire), et que veut dire observer une application IA ?
- Quelles métriques révèlent la santé d'une application ?
- Quels points instrumenter pour rendre une trace exploitable ?
- Comment relier un test d'intégration échoué à une cause via les traces ?
Livrable de conception : schéma d'observabilité (points de trace + métriques).

Trouver un article d'ingénierie sur l'observabilité d'un système à base de LLM et/ou ses tests d'intégration.

### Développement
- écrire des tests d'intégration rejouant des sessions réelles.
- instrumenter l'application (traces, métriques, logs structurés).
- diagnostiquer et corriger au moins deux incidents récurrents.
- vérifier que les tests d'intégration détectent désormais ces incidents.
- documenter causes et correctifs.

# Modalités d'évaluation
3 jours. 

# Livrables
- Note de diagnostic + schéma d'observabilité.
- PR : tests d'intégration + instrumentation + correctifs.
- Journal des incidents résolus.

# Critères de performance
- Les tests d'intégration rejouent des sessions réelles et détectent les incidents.
- Les traces permettent de remonter à la cause d'une panne.
- Au moins deux incidents récurrents sont corrigés.