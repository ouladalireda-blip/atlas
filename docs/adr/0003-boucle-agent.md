# ADR 0003 : boucle agent

- Statut : accepté
- Date : 2026-11-04

## Problème

L'ADR 0002 fournit les reçus et le vérificateur, mais rien ne les relie encore à un modèle. La
boucle agent doit faire dialoguer un modèle interchangeable avec les outils, imposer le contrôle
des affirmations, et ne jamais laisser une panne (réseau, budget, réponse vide) se transformer
soit en trace d'exécution pour l'utilisateur, soit en fausse promesse.

## Décision

**Le modèle est une interface.** `LLMClient.chat(messages, tools) -> LLMResponse` (texte, demandes
d'outils, consommation, modèle). Trois implémentations : `OpenRouterClient`, `OllamaClient`
(endpoint compatible OpenAI) et `ScriptedLLM` pour les tests. Le moteur ne connaît que le
protocole.

**Un tour.** `Agent.run_turn` suit toujours le même trajet :

1. Avant chaque appel au modèle, le budget du jour est contrôlé.
2. Si le modèle demande des outils, **tous** sont exécutés, dans l'ordre reçu, par `ToolExecutor` ;
   chaque reçu repart au modèle en rôle `tool`. Puis le modèle est rappelé.
3. Si le modèle répond par du texte, `verify()` le confronte aux reçus du tour avec
   `claim_vocabulary()`.
4. Affirmation non prouvée : **une seule correction** par tour (message renvoyé au modèle). Si la
   nouvelle réponse reste non prouvée, elle est **remplacée** par un message honnête qui cite ce
   qui n'est pas prouvé et liste les reçus réels.

Verdicts : `verified` (affirmations toutes prouvées), `corrected` (une correction a suffi),
`blocked` (réponse remplacée), `no_claims` (aucune affirmation d'action détectée), `error` (le tour
n'a pas pu aboutir).

**Le prompt système est fixe.** Il est calculé une fois par `Agent` à partir de `address_form`
(`tu` ou `vous`) et reste identique à chaque tour, ce qui évite de faire varier le contexte et
facilite la mise en cache par le fournisseur.

**Aucune exception ne remonte jusqu'à l'utilisateur.** Limite de tours d'outils, réponse vide,
`LLMUnavailable`, budget atteint ou bug inattendu donnent un message honnête qui dit pourquoi le
tour s'arrête et ce qui a réellement eu lieu (liste des reçus). Si un outil a agi avant la panne,
le message le dit.

**Les outils ne sont jamais relancés automatiquement.** Un outil en échec reste en échec ; seule
une nouvelle demande du modèle, ou de l'utilisateur, peut en créer un autre appel, avec son propre
reçu. Les nouvelles tentatives ne concernent que les appels HTTP au modèle, qui n'ont pas d'effet.

**Les résultats d'outils sont des données.** Ils partent au modèle uniquement en rôle `tool`,
tronqués à 4 000 caractères, et le prompt système fixe interdit de les suivre comme des
instructions. C'est une mitigation, pas une garantie : voir les limites.

**Erreurs et nouvelles tentatives du modèle.**

| Cas | Comportement |
| --- | --- |
| 429, 5xx | 2 nouvelles tentatives, attente 1 s puis 2 s, puis `LLMUnavailable` |
| 400, 401, 402, 403, 404 | aucune nouvelle tentative, message propre à chaque code |
| autre 4xx | aucune nouvelle tentative, message générique avec le code |
| délai dépassé, connexion impossible | `LLMUnavailable` immédiat, sans nouvelle tentative |
| réponse illisible ou erreur dans un 200 | `LLMUnavailable` |
| arguments d'outil non-JSON | transmis tels quels ; l'exécuteur produit un reçu `error` |

La clé API n'apparaît jamais dans un message d'erreur. Le délai maximal est configurable
(`ATLAS_TIMEOUT`).

**Consommation et budgets.** Chaque appel est consigné (`UsageLog`, en mémoire) : tokens, et
`usage.cost` réel quand le fournisseur le communique (OpenRouter, demandé par
`usage: {include: true}`). Le tour et le total du jour sont affichés par `atlas chat`. Deux
budgets quotidiens : `daily_token_budget` et `daily_cost_budget` (en dollars) ; dès que l'un est
atteint, plus aucun appel n'est fait jusqu'au lendemain. Sans consommation communiquée, elle est
estimée (environ 4 caractères par token) pour que le budget ne soit pas contourné.

**Configuration.** Une clé OpenRouter absente, ou un modèle Ollama non précisé, bloque
`atlas chat` au démarrage avec un message clair (`LLMConfigError`). Les autres commandes n'ont pas
besoin de clé.

## OpenRouter et confidentialité

Le modèle prévu, `deepseek/deepseek-v4.1-flash-1017` (nom indiqué par le propriétaire du projet,
non vérifié depuis ce dépôt), **n'est pas disponible en routage UE**. Tout message envoyé via
OpenRouter peut donc quitter l'Union européenne.

**Règle : aucune donnée sensible via OpenRouter** (santé, finances, identifiants, documents
personnels, contenu d'autrui). Pour ces cas, utiliser Ollama (`ATLAS_PROVIDER=ollama`), qui reste
sur la machine. Rien dans le code ne détecte les données sensibles : le choix du fournisseur
appartient à l'utilisateur. Les résultats d'outils sont renvoyés au modèle, donc une compétence
qui lit des données personnelles les envoie aussi au fournisseur choisi.

## Limites

- **Contrôle uniquement textuel.** Le vérificateur reste une heuristique (ADR 0002) : une
  affirmation sans mot du vocabulaire passe. La boucle ne fait que l'appliquer.
- **Injection de consignes.** Un résultat d'outil hostile peut influencer le modèle ; seuls les
  effets exigeant une validation humaine sont réellement protégés.
- **Pas de validation humaine encore.** Un outil à effet soumis à validation reçoit un reçu
  `pending_approval` et n'est pas exécuté ; rien ne propose encore de l'approuver.
- **Budget approximatif.** Le contrôle a lieu avant chaque appel : le dernier peut dépasser le
  budget. Sans coût communiqué (Ollama, ou réponse sans `usage.cost`), seul le budget de tokens
  s'applique. Le journal est en mémoire et repart de zéro à chaque lancement : le budget
  « quotidien » n'est donc pas encore partagé entre deux lancements.
- **Pas de flux continu** : la réponse s'affiche en entier à la fin du tour.
- **Historique court et textuel.** Seuls les messages utilisateur et les réponses finales sont
  gardés (`history_messages`) ; les reçus d'un tour précédent ne prouvent rien pour le suivant.
- **Réponse remplacée gardée dans l'historique.** Le message honnête remplace la réponse
  mensongère, qui n'est jamais montrée ni conservée.

## Conséquences

- Tout test de l'agent utilise `ScriptedLLM` : aucun appel réseau.
- Ajouter un fournisseur compatible OpenAI revient à écrire une sous-classe de
  `OpenAICompatibleClient` qui précise ses messages d'erreur.
- Passer à une persistance des reçus et de la consommation ne change ni `Agent` ni `verify` :
  seuls `ReceiptStore` et `UsageLog` sont à remplacer.
