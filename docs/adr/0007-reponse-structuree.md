# ADR 0007 : le journal des reçus est la source de vérité, le modèle termine par « respond »

- Statut : accepté
- Date : 2026-11-05
- Complète l'[ADR 0002](0002-verification-par-recus.md) (vérification par reçus) et l'
  [ADR 0003](0003-boucle-agent.md) (boucle agent).

## Problème

Jusqu'ici, la réponse du modèle était du texte libre, et le vérificateur devait deviner dans ce texte
quelles actions il affirmait (règles de grammaire, lexique de verbes, noms d'objets). Cette
détection plafonne : sur un jeu écrit sans connaître les règles, la v0.1 trouvait 47,5 % des
affirmations (voir le README et la [conception 0001](../design/0001-comparatif-detecteurs.md)), et
chaque amélioration coûte des règles de plus et des fausses alertes. Surtout, **l'utilisateur lit
le texte du modèle**, donc un oubli du détecteur laisse passer un mensonge à l'écran.

Le journal des reçus, lui, est exact par construction : il ne contient que ce que les outils ont
vraiment fait. Il doit être ce que l'utilisateur voit en premier pour savoir ce qui a eu lieu.

## Décision

1. **Le journal des reçus est la source de vérité.** L'écran montre les actions d'un tour à partir
   des reçus (outil, résumé des arguments, statut, numéro), séparées du message du modèle. Ce que
   le modèle écrit commente ces actions ; il ne les définit pas.
2. **Le modèle termine son tour par un outil `respond(message, actions)`**, où `actions` liste les
   numéros (`receipt_id`) des reçus dont le message rend compte. Le prompt système demande de
   toujours terminer ainsi, et d'appeler `respond` seul, après les autres outils.
3. **`respond` appartient au cœur** (`core/respond.py`), pas à une compétence : il n'exécute
   rien, ne produit aucun reçu, et une compétence ne peut pas définir un outil de ce nom (refus au
   démarrage de l'agent). Son schéma est une constante, ajoutée après les outils des compétences :
   la partie fixe de la requête (prompt système et outils) reste identique d'un appel à l'autre
   et profite du cache de prompt du fournisseur.
4. **L'agent valide les numéros contre le journal.** Chaque numéro doit être un reçu **de ce tour**
   au statut `ok`, `pending_approval` ou `rejected` (les deux derniers sont acceptés et affichés
   comme tels ; ils ne prouvent aucune action). Un reçu `error` n'est pas une action réalisée, un
   numéro inconnu ou d'un autre tour n'est pas un reçu de ce tour : ces numéros sont refusés.
   - Premier refus : **une seule correction** par tour, renvoyée au modèle comme réponse de
     l'outil `respond`, avec la liste des reçus du tour.
   - Au refus suivant : le numéro est **retiré** et un avertissement est affiché.
5. **Le détecteur de texte devient un garde-fou secondaire.** Il vérifie que le message n'affirme
   pas d'action que les reçus **cités** ne prouvent pas (seuls les reçus de `actions` comptent).
   Une affirmation non prouvée suit la même règle : une correction, puis le message est remplacé
   (verdict `BLOCKED`). Le détecteur reste choisi par `ATLAS_CLAIM_DETECTOR` ([ADR 0006](0006-contrat-detecteurs-affirmations.md)).
6. **Repli sans `respond`.** Si le modèle répond en texte libre, l'ancien fonctionnement s'applique
   (texte contrôlé par le détecteur contre tous les reçus du tour) et le tour est **marqué non
   conforme**, avec un avertissement visible. Aucun nouvel essai n'est imposé au modèle.
7. **Indicateur de conformité.** Chaque tour est journalisé avec son issue (`respond`, `free_text`,
   `error`) dans la table `turn_log`, à côté de la consommation. Le taux de conformité est la part
   des tours **où le modèle a répondu** qui se sont terminés par `respond` ; les tours sans réponse
   (budget, indisponibilité, limite d'outils, `respond` inutilisable) sont comptés à part. La
   commande `atlas stats` l'affiche (aujourd'hui et au total), avec les effectifs.

## Pourquoi le tool calling plutôt qu'un format JSON brut

- **Le canal existe déjà.** Le modèle appelle des outils avec des arguments validés par un schéma :
  les fournisseurs sont entraînés à le respecter, et notre boucle sait déjà le traiter. Un format
  JSON brut en sortie demanderait un second canal, avec son propre analyseur.
- **Le fournisseur aide à tenir le format.** Un appel d'outil malformé est signalé comme tel (JSON
  illisible), ce que nous traitons avec un reçu d'erreur ; un JSON glissé dans le texte, lui, se
  mélange avec la prose, les blocs de code et les guillemets, et le « mode JSON » n'est pas offert
  par tous les modèles que nous voulons pouvoir échanger (OpenRouter, Ollama).
- **Une erreur se corrige dans la conversation.** Un refus devient la réponse de l'outil
  `respond`, que le modèle lit à son tour suivant, sans mécanisme particulier.
- **Le cache de prompt est préservé.** Le schéma de l'outil est fixe et vit dans la partie stable
  de la requête ; une consigne de format variable ou un schéma qui dépendrait du tour la ferait
  changer.
- **Pas de nouveau point de défaillance.** Si le modèle ignore `respond`, le repli en texte libre
  garde le comportement d'avant au lieu de bloquer le tour.

## Conséquences

- **Ce que cette décision ne règle pas.** Un modèle peut citer un reçu valide et écrire dans le
  message autre chose que ce que ce reçu a fait (une autre date, un autre destinataire). Seul le
  détecteur, secondaire et imparfait, regarde le texte ; l'encart des actions, construit depuis
  les reçus, est ce qui permet à l'humain de le voir. De même, une action réelle que le message
  ne mentionne pas apparaît dans l'encart sans être citée.
- Le repli en texte libre garde les faiblesses du détecteur, mais le taux de conformité les rend
  visibles : un modèle qui ne suit pas la consigne se voit dans `atlas stats`.
- Chaque tour ajoute au moins un appel au modèle (le tour de `respond`), donc des tokens et de la
  latence ; une correction en ajoute un autre. Le surcoût n'est pas encore mesuré.
- La table `turn_log` (migration `core` n° 2) est créée à la première ouverture ; `atlas stats`
  l'applique aussi, mais seulement pour les migrations du cœur.
- L'encart montre une action soumise à validation sur deux lignes : le reçu en attente, puis la
  décision liée par `← n°`.
- L'ADR de décision du comparatif de détecteurs (c7) devient l'ADR 0008.
