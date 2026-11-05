# ADR 0004 : jeu de test caché pour le vérificateur

- Statut : accepté
- Date : 2026-11-04

## Problème

Le vérificateur est une heuristique : on la règle en regardant des phrases où elle se trompe. Plus on
la règle sur les mêmes phrases, plus ses chiffres sur ces phrases mentent. La v0.1 en a donné la
preuve : 100 % de détection sur le jeu de développement (49 phrases écrites avec les règles), 47,5 %
sur le jeu indépendant analysé (v0.1, 204 phrases écrites sans les connaître). Un jeu qu'on lit pour corriger les
erreurs cesse de mesurer.

## Décision

**Deux jeux séparés.** `evals/independent_cases.yaml` est découpé par `evals/split_cases.py` en :

- `evals/verifier_dev.yaml` : développement. Avec `evals/verifier_cases.yaml` (49 phrases), il forme
  le jeu de développement : celui qu'on lit, dont on corrige les erreurs.
- `evals/verifier_test.yaml` : test. Il ne sert qu'à mesurer.

**Découpage reproductible et équilibré.** 50/50, stratifié par combinaison exacte d'effets attendus
(« aucun » compris), graine fixe (`20261104`). Les phrases à plusieurs effets forment leurs propres
strates et sont réparties comme les autres. Le script n'affiche que des effectifs. Il refuse
d'écraser un fichier dont le contenu diffère : regénérer le jeu de test après coup le ferait glisser
vers des phrases déjà vues.

**Le runner ne montre rien du test.** `run_verifier_eval.py --test` n'affiche que les chiffres globaux
et par effet (calendar.write, email.send, tasks.write, tasks.delete). Jamais de phrase, jamais le
nombre d'erreurs, et même un fichier illisible n'est signalé que par son type d'erreur (un message de
validation citerait une phrase). Cela vaut pour tout fichier nommé `verifier_test.yaml`, quelle que
soit la configuration, et pour le chemin du jeu de test passé en argument.

**Planchers séparés, au niveau actuel.** `evals/thresholds.yaml` liste les fichiers de chaque jeu et
les planchers (détection minimale, fausses alertes maximales), globaux et par effet. `--check` vérifie
le développement, `--check --test` le test : un recul fait échouer la commande, sans jamais afficher
d'erreur du test. La CI les lance selon la politique de la section suivante.
Les planchers sont des filets contre la régression, pas des objectifs de qualité. Ils se régénèrent
avec `--write-floors` et ne se baissent pas pour faire passer la CI.

**Règle de conduite.** `CLAUDE.md` interdit de lire `verifier_test.yaml` ou de s'en servir pour
modifier le vérificateur. Elle s'applique aussi à `independent_cases.yaml`, qui en contient les phrases.

## Décisions complémentaires (2026-11-04)

1. **La CI ne mesure le test qu'aux jalons.** À chaque push, elle lance uniquement `--check` sur le
   développement. `--check --test` ne s'exécute que sur les tags `v*` (condition d'étape dans le
   workflow). Pytest ne mesure pas non plus le vrai jeu de test, sinon cette règle serait
   contournée à chaque push ; ses tests du test utilisent des jeux synthétiques. Un test du
   dépôt vérifie cette politique dans `ci.yml`.
2. **Les effectifs sont toujours affichés (x/n) et les petits échantillons signalés.** Toute
   catégorie (détection ou fausses alertes, globale ou par effet) dont le dénominateur est
   inférieur à 10 est marquée « effectif insuffisant ». Avec moins de 10 phrases, une phrase pèse plus
   de 10 points : le chiffre ne se compare pas à un plancher avec confiance. Les planchers
   restent appliqués (ce sont des filets), mais un recul sur une catégorie marquée est à lire avec
   prudence. Sans aucune phrase, le rapport écrit « non mesurable (0/0) », jamais un taux inventé.
3. **Un jeu de test peut se trouver hors du dépôt.** `run_verifier_eval.py --test <chemin>...` mesure
   un ou plusieurs fichiers n'importe où (y compris sans configuration) comme un jeu de test :
   agrégats seulement, quel que soit le nom du fichier, erreurs de lecture réduites à leur type,
   sans combinaison possible avec `--check` ni `--write-floors` (un jeu externe n'a pas de plancher
   du dépôt). C'est l'outil prévu pour le jeu aveugle de la v0.2.
4. **Le jeu de 204 phrases s'appelle « jeu indépendant analysé (v0.1) ».** Ses erreurs ont été
   lues et publiées : il a servi à comprendre, il ne peut plus servir de jeu aveugle. **À partir de
   la v0.2, la mesure de référence sera un jeu aveugle gardé hors du dépôt**, écrit sans connaître
   les règles, mesuré par `--test <chemin>`. Tant qu'il n'existe pas, les chiffres du test du dépôt
   sont une mesure imparfaite (voir les limites).

## Chiffres de référence (2026-11-04, vérificateur de la v0.1)

| Jeu | Phrases | Détection | Fausses alertes |
| --- | --- | --- | --- |
| Développement (`verifier_cases` + `verifier_dev`) | 151 | 68,4 % (52/76) | 10,7 % (8/75) |
| Test (`verifier_test`) | 102 | 44,0 % (22/50) | 11,5 % (6/52) |

Les chiffres par effet sont dans `evals/thresholds.yaml` (champs `floors`). Les catégories de moins
de 10 phrases (par exemple `tasks.delete`) sont marquées « effectif insuffisant » par le runner.

## Limites

- **Le jeu de test n'est pas aveugle.** Lors de la clôture de la v0.1, les phrases du jeu indépendant
  complet et leurs erreurs ont été affichées dans une session de travail, et une analyse en a été
  publiée dans le README. Les phrases du test ont donc été vues avant d'être séparées. La règle
  protège contre la dérive à partir de maintenant, pas contre ce qui est déjà su. Avant de
  s'appuyer sur le test pour une décision importante, il faudra des phrases écrites après le
  découpage, sans connaître les règles, et gardées hors du dépôt jusqu'à leur première mesure.
- **Une règle de conduite n'est pas un verrou.** Rien n'empêche techniquement d'ouvrir le fichier ou
  la source `independent_cases.yaml`. Archiver la source hors du dépôt, une fois le découpage
  validé, durcirait la protection ; je ne l'ai pas fait, la décision revient au propriétaire.
- **Chaque mesure du test donne un peu d'information.** Même agrégée, ajuster jusqu'à passer finit
  par ajuster au test. La CI ne le mesure plus qu'aux tags ; en local, rien n'empêche de le lancer
  à chaque essai : la discipline reste humaine.
- **Effectifs faibles.** Le test du dépôt compte 50 phrases qui affirment une action, 2 pour `tasks.delete`
  et 14 pour `email.send`. Une phrase pèse de 2 à 50 points selon l'effet : les chiffres par effet sont
  indicatifs.
- **Annotation non relue.** Les attendus du jeu indépendant restent à relire par un humain.
- **Le découpage dépend de la source.** Modifier `independent_cases.yaml` change le découpage ; le
  script refusera alors d'écraser les fichiers, ce qui est voulu.

## Conséquences

- Le travail sur le vérificateur se fait avec `run_verifier_eval.py` (développement) ; le test se
  mesure avec `--test` aux jalons.
- Toute nouvelle phrase d'évaluation destinée à mesurer est écrite avant les règles qu'elle teste et
  va dans un jeu qu'on ne lit pas.
- Ajouter un effet demande de l'ajouter au vocabulaire des jeux et, si besoin, de régénérer les
  planchers (décision à justifier).
