# ADR 0002 : vérification par reçus

- Statut : accepté ; la forme du vocabulaire et la détection des verbes sont remplacées par
  l'[ADR 0005](0005-verbes-et-objets.md)
- Date : 2026-11-04

## Problème

Un modèle de langage peut affirmer avoir agi sans l'avoir fait. Le cas d'origine est la réponse
« Confirmé, l'événement est ajouté à ton agenda. », alors qu'aucun outil n'avait été appelé.
L'utilisateur croit l'action faite et n'a aucun moyen de voir l'écart. Demander au modèle de ne
pas mentir ne suffit pas : la garantie doit venir du code.

## Décision

**Chaque demande d'outil produit un reçu.** `ToolExecutor` ne lève jamais d'exception et enregistre
un reçu `ok`, `error` ou `pending_approval`. Un outil dont l'effet exige une validation humaine
n'est pas exécuté. Si le reçu ne peut pas être conservé, l'exécuteur retourne un reçu `error`, car
une réussite sans trace conservée ne prouve rien.

**Une affirmation d'action exige une preuve.** Le vérificateur repère les affirmations d'une
réponse, puis cherche pour chacune un reçu du même effet au statut `ok`. Un reçu `error`,
`pending_approval` ou d'un autre effet ne prouve rien. Les règles génériques « c'est fait » et
« done » exigent un reçu `ok` portant un effet : une lecture ne les prouve pas. Toute affirmation
non prouvée donne un message de correction à renvoyer au modèle.

**Le moteur reste générique.** `core/verifier.py` ne contient que de la grammaire. Les mots métier
viennent du champ `claims` des manifestes, fusionnés par `SkillRegistry.claim_vocabulary()`.

**La détection reste une heuristique explicable, sans LLM.** Un participe passé, ou un verbe
d'écriture au présent à la 1re personne (« j'ajoute », « I'm adding »), proche d'un mot du
vocabulaire forme une affirmation, sauf s'il suit une négation ou une marque de futur, de
possibilité ou d'hypothèse. Deux exceptions : « je (peux) te/vous confirme(r) que », « je t'assure
que » et « I can confirm » sont des affirmations malgré « peux » ; « ne … que » restreint et ne
nie pas. Elle est déterministe, rapide, testable hors ligne, et ne dépend pas du
modèle qu'elle contrôle.

En cas de doute, la détection préfère signaler : une fausse alerte coûte une correction de plus,
alors qu'une affirmation manquée rétablit le bug d'origine.

## Limites

La vérification réduit le risque, elle ne l'élimine pas.

**Affirmations manquées :**

- Une action décrite sans mot du vocabulaire n'est pas détectée. Seules les règles « c'est fait »
  et « done » ne dépendent pas du vocabulaire.
- Le présent à la 1re personne ne reconnaît que les verbes d'écriture d'une liste fermée
  (`_WRITE_VERBS_FR` et `_WRITE_VERBS_EN` dans `core/verifier.py`) : « j'archive » ou « I
  archive » passent tant que le verbe n'y figure pas. Cette liste est volontairement positive :
  un verbe quelconque donnerait de fausses alertes sur « j'utilise ton agenda » ou « je lis ton
  agenda » (7 sur 9 phrases de lecture essayées). Elle contient des verbes que les variantes de
  l'éval utilisent, donc ces variantes mesurent le comportement, pas la généralisation à des
  verbes absents de la liste.
- Seule la 1re personne du singulier est reconnue au présent. Ni « il ajoute » ni « nous
  ajoutons », ni le progressif français (« je suis en train d'ajouter »), ne sont détectés.
- Un marqueur placé avant le participe annule toute la proposition, sauf les formules de
  confirmation ci-dessus et « ne … que » sans autre négation.
- Une condition (dès que, quand, si, une fois que, once, when, if) bloque aussi le reste de la
  phrase, pour écarter « dès que tu valides, l'événement est ajouté ». Une vraie affirmation
  placée après elle est donc manquée : « quand tu l'as demandé, j'ai ajouté l'événement ».
- Une compétence qui n'a pas pu être chargée n'apporte pas son vocabulaire.
- Seuls le français et l'anglais sont couverts.

**Fausses alertes :**

- **Limite connue : les adjectifs en -é.** « Ton agenda est chargé », « l'événement programmé
  demain commence à 10h », « voici les événements enregistrés » ou « un événement annulé » se lisent
  comme des affirmations, car rien dans la forme du mot ne distingue un participe passé d'un
  adjectif. Une réponse de pure lecture peut donc déclencher une correction. Ces 4 cas restent
  signalés dans l'éval (groupe « base », 4 fausses alertes sur 16) : ce n'est pas une régression,
  c'est une limite qu'on choisit de mesurer plutôt que de masquer. Les rustines par liste de mots
  ne passent pas à l'échelle, et la détection préfère signaler en cas de doute.
- **Piste pour la v0.2 : l'analyse grammaticale avec spaCy** (modèles `fr_core_news_sm` et
  `en_core_web_sm`). L'étiquette morphologique distingue un participe verbal (`VerbForm=Part`
  avec un auxiliaire ou un sujet : « l'événement est ajouté ») d'un adjectif (`ADJ` : « agenda
  chargé »), et donne le sujet, le temps et la personne, ce qui remplacerait aussi la liste des
  verbes d'écriture. Coûts à peser avant de l'adopter : une dépendance et des modèles à télécharger,
  un démarrage plus lent, et une précision à mesurer sur l'éval avant de remplacer l'heuristique
  (elle ne devient la règle que si elle fait mieux sur `evals/verifier_cases.yaml`).
- Le rappel d'une action passée (« je l'ai ajouté hier ») est signalé, car seuls les reçus du tour
  courant sont fournis.
- Un mot présent dans le vocabulaire de deux effets exige un reçu pour chacun d'eux. Chaque effet
  doit donc avoir ses propres mots. Exception : si le participe (ou le verbe au présent) est
  lui-même un mot du vocabulaire d'un effet (« supprimé » pour `tasks.delete`), il désigne cette
  action et seuls les effets qui le listent comptent. C'est ce qui évite que « la tâche est
  supprimée » exige aussi un reçu d'ajout parce que « tâche » appartient à `tasks.write`.
  Conséquence : un verbe listé dans le vocabulaire d'une compétence vaut pour toutes. Une future
  compétence agenda qui supprime des événements verrait « événement supprimé » exiger un reçu
  `tasks.delete`. Il faudra alors lier les verbes à leur domaine (piste : un champ du manifeste).

**Preuve incomplète :**

- La preuve porte sur l'effet, pas sur le contenu. Un reçu `calendar.write` pour « Dentiste »
  prouve aussi une affirmation sur « Anniversaire », et un seul reçu couvre « trois événements
  ajoutés ».
- Un reçu `ok` signifie que l'outil a terminé sans erreur. Il ne garantit pas que le résultat est
  correct.

## Conséquences

- Toute compétence à effet doit déclarer `claims`, sinon ses affirmations ne sont pas vérifiées.
- `verify` doit recevoir les reçus du tour en cours (`store.for_turn`).
- Toute modification de l'heuristique se mesure avec `evals/run_verifier_eval.py`.
- Ajouter une langue revient à compléter les listes grammaticales de `core/verifier.py`.
