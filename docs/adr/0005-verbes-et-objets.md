# ADR 0005 : les verbes au cœur, les objets dans les compétences

- Statut : accepté
- Date : 2026-11-05 (repli ajouté le même jour, voir « Repli sans objet »)
- Complète l'[ADR 0002](0002-verification-par-recus.md) : le champ `claims` des manifestes change de
  forme, et la détection ne repose plus sur « tout participe en -é ».

## Problème

Jusqu'ici, le vocabulaire d'une compétence mêlait les noms (« tâche ») et les verbes
(« supprimé ») dans une liste par effet. Trois défauts en découlaient :

1. **Un verbe valait pour toutes les compétences.** Pour que « la tâche est supprimée » ne
   demande pas aussi un reçu d'ajout, `tasks.delete` listait « supprimé ». Alors « j'ai supprimé
   le rendez-vous » était lu comme `tasks.delete` : constaté sur le jeu indépendant analysé
   (v0.1) et noté comme limite dans l'ADR 0002.
2. **Chaque compétence réécrivait les verbes** (supprimé, effacé, retiré…), et un oubli (« retiré »)
   donnait une fausse attribution.
3. **Tout mot en -é était un verbe d'action.** « Agenda chargé », « mail envoyé par la banque »,
   « événement programmé demain » déclenchaient des fausses alertes que rien ne distinguait
   d'un vrai participe.

## Décision

**Le cœur connaît les verbes, une seule fois.** `core/lexicon.py` range les verbes en trois
classes, `write`, `delete` et `send`, en français et en anglais, avec leurs formes : participes
passés au masculin, féminin et pluriel (réguliers et irréguliers : « mis, mise, mises »,
« transmis », « parti »…), présent de la 1re personne (« j'ajoute », « I add »), formes en -ing
et prétérit anglais. Le passé composé se reconnaît par son participe. Le lexique est la seule
source de vérité : au chargement, une forme présente dans deux classes est une erreur. Ajouter un
verbe profite à tous les domaines.

**Les compétences connaissent leurs objets.** Le champ `claims` du manifeste devient :

```yaml
claims:
  nouns: [tâche, task, todo, to-do, liste de tâches]
  actions:
    write: tasks.write
    delete: tasks.delete
```

`nouns` désigne les objets ; `actions` dit, pour chaque classe de verbe que la compétence gère,
l'effet qu'elle produit chez elle. Le domaine porte le nom de la compétence.

**Validation au chargement** (une compétence invalide est refusée, jamais ignorée) : chaque effet
cité par `actions` est déclaré par un outil de la compétence ; chaque classe est connue du cœur
(la liste des classes connues figure dans le message) ; `nouns` et `actions` ne sont pas vides ;
l'ancien format « effet → mots » est refusé.

**Le vérificateur calcule l'effet = classe du verbe + domaine du nom le plus proche.** Pour chaque
verbe du lexique rencontré dans une proposition affirmative, il cherche le nom de domaine le plus
proche (6 mots au plus, à égalité le nom qui suit le verbe, puis l'ordre des domaines). L'effet est
celui que `actions` associe à la classe. Sans nom à portée, la règle de repli s'applique.

**Classe non déclarée : signalée, jamais déplacée.** Si le domaine ne déclare pas la classe du verbe,
l'affirmation est signalée avec l'effet `<domaine>.<classe>` (par exemple `calendar.delete`). Elle
est marquée non déclarée et **aucun reçu ne peut la prouver**, même un reçu portant ce nom d'effet :
la compétence n'a pas d'outil pour cette action, donc l'assistant ne peut pas l'avoir faite par
elle. Elle n'est jamais attribuée à un autre domaine. Le message de correction le dit.

**Repli sans objet.** Les phrases courtes (« C'est envoyé ! », « Supprimé. ») n'ont aucun nom de
domaine : la séparation verbes/objets leur faisait perdre leur détection. Un verbe d'action sans nom
à portée (6 mots, voir plus haut) suit donc la déclaration des domaines pour sa classe :

- déclarée par **un seul** domaine : affirmation de l'effet de ce domaine (`C'est envoyé !` →
  `email.send`) ;
- déclarée par **plusieurs** domaines : affirmation **générique** de la classe, d'effet `*.<classe>`,
  prouvée par n'importe quel reçu `ok` d'un des effets de cette classe, quel que soit le domaine
  (`C'est ajouté !` avec `calendar.write` et `tasks.write` : l'un ou l'autre suffit) ;
- déclarée par **aucun** : rien à affirmer ni à prouver.

Un nom à portée garde toujours la priorité : « l'événement est supprimé » reste `calendar.delete`,
non déclaré, et n'est pas repris par le repli vers `tasks.delete` (seul domaine à déclarer delete).
Les marqueurs bloquants (négation, futur, possibilité, condition, question) s'appliquent comme pour
toute proposition : « Ce n'est pas envoyé », « Je vais envoyer », « Dès que tu valides, c'est envoyé »
n'affirment rien. Le repli s'applique aussi au présent de la 1re personne (« J'envoie. »).

**Convention : rayer une tâche, c'est la marquer comme faite.** « Rayer » est un verbe de la classe
`write` (`tasks.write`), pas `delete`.

**Les verbes des formules de confirmation ne sont pas des actions.** Dans « je te confirme que le
mail est parti » ou « I can confirm that… », « confirme » annonce l'affirmation qui suit ; seul le
verbe de la subordonnée compte.

**Les fichiers d'éval suivent.** `domains` (noms et actions) remplace `vocabulary`. Les fichiers du
dépôt sont convertis (leurs phrases n'ont pas bougé : même empreinte avant et après pour le jeu de
test) et `docs/eval-format.md` donne l'en-tête modèle d'un fichier externe.

## Mesures

Jeu de développement (`verifier_cases` + `verifier_dev`, 151 phrases), avant et après :

| Catégorie | Avant | Après |
| --- | --- | --- |
| Détection globale | 68,4 % (52/76) | 71,1 % (54/76) |
| Fausses alertes globales | 10,7 % (8/75) | 8,0 % (6/75) |
| calendar.write, détection | 66,7 % (26/39) | 64,1 % (25/39) |
| calendar.write, fausses alertes | 4,5 % (5/112) | 3,6 % (4/112) |
| email.send, détection | 82,6 % (19/23) | 95,7 % (22/23) |
| email.send, fausses alertes | 0,8 % (1/128) | 0,8 % (1/128) |
| tasks.write, détection | 64,3 % (9/14) | 64,3 % (9/14) |
| tasks.write, fausses alertes | 4,5 % (4/88) | 1,1 % (1/88) |
| tasks.delete, détection | 75,0 % (3/4) | 100 % (4/4) |
| tasks.delete, fausses alertes | 0,0 % (0/98) | 0,0 % (0/98) |

`tasks.delete` n'a que 4 phrases positives : « effectif insuffisant ». Le lexique a été écrit de
mémoire, **avant** de regarder les erreurs du développement, et n'a pas été ajusté pour elles ; deux
corrections ont suivi les premiers tests unitaires (le présent « enlève », et l'exclusion des
verbes de formule). Le jeu de test n'a pas été mesuré pour cette décision.

Le recul de `calendar.write` (une phrase) vient du changement lui-même : l'ancienne règle prenait
n'importe quel participe en -é, le lexique ne connaît que ses verbes. Les planchers du développement
ont été recalés au niveau actuel (`evals/thresholds.yaml`) : c'est la seule raison, assumée ici, de
baisser un plancher (calendar.write, détection : 66,7 % → 64,1 %). Les planchers du test n'ont pas
été touchés : ils seront vérifiés au prochain tag.

## Mesure du repli (développement, 151 phrases)

Avant le repli = l'état décrit au tableau précédent. Le jeu de test n'a pas été lu ni mesuré.

| Catégorie | Avant le repli | Après le repli |
| --- | --- | --- |
| Détection globale | 71,1 % (54/76) | **86,8 %** (66/76) |
| Fausses alertes globales | 8,0 % (6/75) | 10,7 % (8/75) |
| calendar.write, détection | 64,1 % (25/39) | 89,7 % (35/39) |
| calendar.write, fausses alertes | 3,6 % (4/112) | 3,6 % (4/112) |
| email.send, détection | 95,7 % (22/23) | 100 % (23/23) |
| email.send, fausses alertes | 0,8 % (1/128) | 0,8 % (1/128) |
| tasks.write, détection | 64,3 % (9/14) | 71,4 % (10/14) |
| tasks.write, fausses alertes | 1,1 % (1/88) | 1,1 % (1/88) |
| tasks.delete, détection | 100 % (4/4) [effectif insuffisant] | 100 % (4/4) [effectif insuffisant] |
| tasks.delete, fausses alertes | 0,0 % (0/98) | 0,0 % (0/98) |

**Convention de mesure à connaître.** Dans les fichiers d'éval, `calendar` et `tasks` déclarent tous
deux `write` : « Noté pour samedi » donne donc une affirmation générique `*.write`, qui ne peut pas
porter le nom exact `calendar.write` de l'annotation. Le runner la compte comme **détectée** quand
l'annotation attend un effet qu'elle peut prouver (`calendar.write` ou `tasks.write`), car un reçu de
cet effet la prouverait ; elle est une fausse alerte quand rien n'était attendu, et manquée quand
l'annotation attend un effet d'une autre classe. Sans cette convention (comparaison stricte des noms
d'effets), la détection reste à 71,1 % (54/76) : tout le gain sur les classes partagées vient d'elle,
tandis que la hausse de `email.send` (classe `send` déclarée par un seul domaine) vient du repli seul.
Les deux fausses alertes ajoutées sont des phrases où un participe n'est pas une action :
« Ajouté à cela, il faut prévoir… » et « La réunion annulée de la semaine dernière… ».

## Limites

- **Le repli coûte des fausses alertes.** Sans objet de domaine à portée, tout verbe du lexique dont
  la classe est déclarée par un domaine se lit comme une affirmation : « J'ajoute un peu de sel dans
  la sauce » est signalé `calendar.write` (test dans `tests/test_verifier_rules.py`). La hausse
  mesurée est de 6 à 8 fausses alertes sur 75 ; elle sera plus forte sur des conversations qui
  parlent d'autre chose que des compétences. Les adjectifs du lexique (« événements enregistrés »,
  « réunion annulée ») y contribuent.
- **Une affirmation sans objet est dédoublée** quand elle précède une virgule : « Confirmé,
  l'événement est ajouté » donne deux affirmations de même effet (la correction les liste toutes
  deux ; un seul reçu les prouve).
- **Liste fermée.** Un verbe absent du lexique n'est pas détecté (« j'actualise l'agenda » l'est,
  « j'ai bloqué ton créneau » l'est mais « créneau » n'est pas un nom du domaine). Les infinitifs
  (« je viens d'ajouter ») ne sont pas reconnus. L'étendre est simple : un fichier, une source.
- **Les noms manquants coûtent autant que les verbes.** « Réunion », « rdv », « entretien » ne sont
  pas dans le domaine calendar : une phrase qui n'emploie que ces noms n'est pas détectée.
- **Le nom le plus proche peut se tromper.** « J'ai ajouté le mail à l'événement » vise le mail
  (le plus proche) et donne `email.write`, non déclaré. « J'ai écrit et envoyé le mail » donne
  `email.send` et `email.write` : le second est signalé alors que l'envoi a bien eu lieu et que
  l'annotation n'en attendait qu'un.
- **Les brouillons.** « J'ai rédigé le mail » est signalé comme `email.write`, non prouvable. C'est
  la règle stricte voulue (aucun outil d'écriture de brouillon) ; des annotations qui placent les
  brouillons dans « aucune affirmation » compteront ces cas en fausses alertes.
- **Un domaine par compétence**, nommé comme elle ; deux compétences qui partagent un nom se
  départagent par l'ordre de chargement.
- **Les adjectifs du lexique** (« événements enregistrés ») restent des fausses alertes tant que
  l'analyse grammaticale (spaCy, v0.2) n'est pas là. Les adjectifs hors lexique (« chargé »,
  « partagé ») n'en déclenchent plus.
- **Conventions d'annotation.** Dans les jeux du dépôt, annuler ou supprimer un rendez-vous
  s'annote `calendar.write` (le domaine `calendar` des fichiers d'éval déclare `delete:
  calendar.write`) ; il n'existe pas d'outil de suppression d'événement. Dans les tests unitaires,
  le domaine calendar ne déclare que `write`, ce qui exerce la règle des classes non déclarées.

## Conséquences

- Écrire une compétence demande ses noms et le lien classe → effet, plus la liste des verbes.
- Un nouveau verbe ou une nouvelle langue se traite dans `core/lexicon.py`, une fois.
- Une nouvelle classe de verbes (par exemple `read`) est une décision du cœur : elle apparaît dans
  `VERB_CLASSES`, que les manifestes et les jeux d'éval valident.
- Les jeux d'éval externes (jeu aveugle de la v0.2) suivent `docs/eval-format.md`.
