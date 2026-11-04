# ATLAS — un assistant personnel qui ne peut pas dire « c'est fait » sans preuve

> Un jour, un assistant IA m'a répondu : « Confirmé, l'événement est ajouté à ton agenda. »
> Aucun outil n'avait tourné. ATLAS est construit autour de ce bug.

Le modèle de langage est une pièce interchangeable (via OpenRouter). Tout le reste est construit autour :
la vérification, la mémoire, le tri des messages, les outils et l'interface.

## Ce qui le distingue

| Brique | Rôle | Fichier |
|---|---|---|
| **Vérificateur** | Détecte dans chaque réponse les affirmations d'accomplissement (FR/EN, passé composé, hors négations, questions et futur). Il les confronte ensuite aux reçus d'outils du tour. Si une affirmation n'a pas de preuve, le modèle est renvoyé corriger ; s'il insiste, la réponse est bloquée. | `app/verifier.py` |
| **Reçus** | Chaque appel d'outil est journalisé avec un effet déclaré (`calendar.write`, `email.send`…) et un statut. Une erreur ne prouve rien, et un brouillon n'est pas un envoi. | `app/tools/registry.py` |
| **Trieur** | Classifieur TF-IDF + régression logistique, sans LLM. Il traite un message en environ 1 ms et décide de l'effort (light / standard / deep), du modèle, des groupes d'outils à charger et du besoin de rappel mémoire. | `app/triage.py` |
| **Mémoire en 4 couches** | 1. Vecteurs (pgvector) pour retrouver par le sens<br>2. Registre SQL exact<br>3. Faits durables étiquetés par locuteur, avec dédoublonnage<br>4. Notes Markdown que l'assistant écrit lui-même<br>Un seul `recall()` les interroge toutes, reranke les résultats et renvoie des sources citables `[fact#12]`. | `app/memory/core.py` |
| **E-mail avec approbation** | L'assistant ne peut que préparer un brouillon. L'envoi n'existe qu'après un clic humain. | `app/main.py` |
| **Coût maîtrisé** | Le prompt système est figé octet pour octet ; tout ce qui varie (date, souvenirs) est placé à la fin. Le préfixe reste ainsi en cache. Tokens, cache et coût sont suivis par tour. | `app/agent.py` |

## Démarrage

```bash
pip install -r requirements.txt
cp .env.example .env            # optionnel : sans clé, mode démo
uvicorn app.main:create_app --factory --reload
# http://localhost:8000
```

**Mode démo (sans clé API).** Un faux modèle volontairement menteur répond « c'est ajouté » sans appeler d'outil. On voit le vérificateur le rattraper en direct (badge « corrigé par le vérificateur »). C'est idéal pour une démo en entretien.

**Avec Supabase.**
1. Appliquer `supabase/migrations/001_init.sql`.
2. Mettre la chaîne de connexion Postgres dans `DATABASE_URL`.

Sans `DATABASE_URL`, ATLAS tourne sur SQLite local.

## Tests

```bash
pytest -q        # 50 tests, < 1 s, aucun appel réseau
```

Les tests rejouent le bug d'origine :
- le modèle ment ;
- il est corrigé ;
- l'événement existe vraiment en base ;
- un menteur obstiné est bloqué ;
- un e-mail « envoyé » alors qu'il n'est qu'en brouillon est rejeté.

## Flux d'un message

```
message → trieur (1 ms) → rappel mémoire (exclut ce qui est déjà dans le contexte)
        → modèle + outils filtrés → reçus → vérificateur ──✗──> correction (1 essai) ──✗──> blocage
                                                         └─✓──> réponse + verdict
        → (tâche de fond) extraction des faits durables
```

## API

`POST /api/chat` · `GET /api/sessions[/{id}]` · `GET /api/outbox` · `POST /api/outbox/{id}/approve|reject` ·
`GET /api/memory/facts` · `DELETE /api/memory/facts/{id}` · `GET /api/memory/search?q=` · `GET /api/notes` · `GET /api/stats`

## Limites connues / suite

- Embeddings : par défaut, un embedder local par hachage (déterministe, hors ligne). Brancher `EMBEDDINGS_URL` (n'importe quel endpoint compatible OpenAI) pour un vrai rappel sémantique.
- La détection d'affirmations repose sur des règles. Elle est volontairement prudente et testée, mais pas exhaustive. Piste : un second classifieur entraîné sur les verdicts journalisés.
- Le chemin Postgres est écrit mais à valider sur ton projet Supabase ; les tests tournent sur SQLite.
- Envoi d'e-mail simulé après approbation. Brancher Gmail API / SMTP dans `decide()`.
- Prochaines briques : agenda Google réel, « watches » (surveillance planifiée), app mobile Expo, voix.

---
Stack : Python 3.11+, FastAPI, Supabase (Postgres + pgvector), scikit-learn, OpenRouter, HTML/JS sans framework.
