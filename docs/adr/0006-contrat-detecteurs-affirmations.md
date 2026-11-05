# ADR 0006 : un contrat pour les détecteurs d'affirmations

- Statut : accepté
- Date : 2026-11-05
- Complète l'[ADR 0002](0002-verification-par-recus.md) et l'[ADR 0005](0005-verbes-et-objets.md).
  Met en œuvre la sous-étape c2 de la [conception 0001](../design/0001-comparatif-detecteurs.md).

## Problème

La décision « cette phrase affirme-t-elle une action, de quelle classe, sur quel objet » était
écrite en dur dans `core/verifier.py`. Pour comparer des candidats (règles, spaCy, embeddings,
NLI, cascade) sur les mêmes jeux, il faut pouvoir en changer sans toucher au reste, et sans que le
vérificateur ou l'agent sache lequel tourne.

## Décision

1. **Un contrat** (`core/claims/base.py`) : `ClaimDetector` avec un `name` et
   `detect(reply, domains) -> list[Claim]`. `Claim` et `Domains` vivent là, pour que tous les
   candidats les partagent.
2. **R = le code actuel, déplacé** dans `core/claims/rules.py` sans changement de comportement
   (`RulesDetector`). `core/verifier.py` ne garde que la confrontation aux reçus : `verify()` prend
   un détecteur facultatif (R par défaut), ainsi que l'`Agent`.
3. **Le choix par la configuration** : `ATLAS_CLAIM_DETECTOR` (défaut `rules`), validé par
   `Settings`. `core/claims/factory.py` tient le registre ; un nom inconnu est refusé avec la liste
   des valeurs acceptées, et `atlas chat` refuse de démarrer. Un détecteur qui ne peut pas démarrer
   (extra ou modèle absent) lève `DetectorUnavailable`, avec la commande à lancer.
4. **Panne ou lenteur à l'exécution → repli sur R.** Tout détecteur autre que R est enveloppé
   dans un `FallbackDetector` : si `detect` lève une exception, ou dépasse le délai maximal par
   appel (`ATLAS_CLAIM_DETECTOR_TIMEOUT`, 1 s par défaut, strictement positif), R répond à sa
   place et un avertissement est journalisé (détecteur, cause, repli). Une réponse n'est jamais
   laissée sans contrôle parce qu'un modèle a échoué ou se fait attendre. L'appel se fait dans un
   fil démon que Python ne peut pas interrompre : un détecteur bloqué continue en arrière-plan, son
   résultat est ignoré.
   **Disjoncteur** : après `ATLAS_CLAIM_DETECTOR_MAX_TIMEOUTS` dépassements de délai (3 par défaut,
   cumulés sur la session), le détecteur est désactivé : repli permanent sur R, sans nouvel appel
   ni nouveau fil bloqué, avec un avertissement. Il ne se réarme qu'à la session suivante. Les
   exceptions ordinaires ne comptent pas : un détecteur qui échoue vite ne bloque rien. Les runners de mesure demandent `resilient=False` : une
   panne y doit se voir, pas être masquée.
5. **Mesurer par le même chemin.** `evals/run_verifier_eval.py` et `evals/compare_detectors.py`
   passent par la même fonction `evaluate(data, group, detector)` : les chiffres de R sont ceux de
   `run_verifier_eval.py` par construction, et un test le vérifie sur le développement.

## Outils de mesure (hors du cœur)

- `evals/compare_detectors.py` : mêmes jeux pour tous les détecteurs ; détection et fausses alertes,
  globales et par effet, avec effectifs (x/n) et intervalle de Wilson à 95 % ; latence p50/p95 par
  réponse : des tours de chauffe sur tout le jeu (`--warmup`, 1 par défaut), puis chaque phrase
  mesurée N fois (`--repeats`, 5 par défaut), une réponse à la fois, avant tout appel MLflow. La
  sortie et MLflow consignent la machine (processeur, cœurs, RAM, système, Python), le nombre de
  répétitions et les tours de chauffe.
  Il n'affiche jamais une phrase, y compris pour le développement.
- **MLflow** (`--mlflow`) : un run par (détecteur, jeu) avec paramètres, métriques (dont les bornes de
  Wilson), empreinte SHA-256 du jeu, commit git et rapport agrégé. Il vit dans un **groupe de
  dépendances `eval`**, distinct de `dev`, pour que `uv sync --locked` (la CI) ne l'installe pas :
  les groupes par défaut d'uv sont `dev` seulement. Stockage SQLite dans `mlruns/` (ignoré par git) :
  MLflow 3.18 refuse le stockage par fichiers, en « mode maintenance ».
- `evals/check_leakage.py` : doublons exacts et quasi-doublons entre développement, test interne et
  un fichier externe facultatif, en effectifs seulement. Un doublon exact fait échouer le script.
  Un quasi-doublon est une paire dont la similarité de Jaccard des ensembles de mots atteint 0,75.
  Ce seuil est étalonné par des tests sur des paires plantées : les bigrammes de mots à 0,70,
  choisis d'abord, manquaient les six quasi-doublons plantés (similarités de 0,40 à 0,69), alors
  que les mots seuls donnent 0,80 ou plus pour eux et 0,67 au plus pour des phrases différentes
  de même gabarit.

## Conséquences

- **R n'a pas changé.** Les 151 détections du développement et le rapport complet de
  `run_verifier_eval.py` sont identiques avant et après le déplacement (vérifié sur une copie du
  commit précédent).
- Le registre ne contient que `rules` : ajouter un candidat (c3 à c6) = un fichier dans
  `core/claims/` et une ligne dans `_BUILDERS`, avec ses dépendances lourdes importées dans son
  constructeur.
- **Le repli sur R protège contre la panne et la lenteur, pas contre une réponse fausse.** Un
  détecteur qui répond vite mais mal n'est pas détecté par ce mécanisme ; sa qualité se juge à la
  mesure. Le délai de 1 s est un garde-fou de production, distinct du critère d'élimination de la
  conception (p95 > 300 ms), qui se juge au comparatif.
- Un détecteur de repli qui échouerait à son tour (R ne lève pas, mais c'est un contrat) laisse
  l'exception remonter jusqu'au filet de sécurité de l'agent plutôt que d'être avalée.
- `Settings()` refuse maintenant une valeur inconnue de `ATLAS_CLAIM_DETECTOR` pour toute commande
  qui lit les réglages ; `atlas chat` et `atlas skills` affichent l'erreur proprement.
- L'ADR de décision du comparatif (c7) devient l'ADR 0008 : les numéros 0006 (ce contrat) et 0007
  (réponse structurée) sont pris.

## Remesure du test interne (2026-11-05)

Les planchers du test interne dataient d'avant l'ADR 0005 et son repli. Remesurés avec R
(`run_verifier_eval.py --test`, agrégats seulement, confirmés par `compare_detectors.py --test`) :

| | Avant (anciens planchers) | Après (nouveaux planchers) |
| --- | --- | --- |
| Détection | 44,0 % (22/50) | 80,0 % (40/50) |
| Fausses alertes | 11,5 % (6/52) | 13,5 % (7/52) |
| calendar.write | 28,6 % / 4,9 % | 90,5 % / 2,5 % |
| email.send | 35,7 % / 2,3 % | 92,9 % / 3,4 % |
| tasks.write | 83,3 % / 1,2 % | 77,8 % / 1,2 % |
| tasks.delete (2 positives, effectif insuffisant) | 100 % / 1,0 % | 100 % / 0,0 % |

Les planchers du développement n'ont pas bougé. Deux planchers du test sont moins exigeants
qu'avant : la détection de `tasks.write` (83,3 % → 77,8 %, une phrase sur 18) et le plafond de
fausses alertes global (11,5 % → 13,5 %, une phrase de plus sur 52). Les recaler était une décision
humaine, prise après cette remesure. Le plafond de 13,5 % dépasse le seuil d'élimination de 10 %
du critère de la conception (§ 6) : R ne passerait pas l'éliminatoire sur ce jeu, qui ne sert
pourtant jamais à décider.
