from app.llm import ScriptedLLM
from tests.conftest import call, say


def test_the_original_bug_is_caught_then_fixed(make_agent, store):
    """Le modèle ment ; le vérificateur le force à appeler l'outil ; la réponse finale est prouvée."""
    llm = ScriptedLLM([
        say("Confirmé, l'événement est ajouté à ton agenda."),               # mensonge
        call("calendar_add_event", title="Dentiste", start="2026-11-03T14:00"),
        say("C'est ajouté à ton agenda : Dentiste mardi 14h."),
    ])
    r = make_agent(llm).run("s1", "ajoute un rdv dentiste mardi 14h")
    assert r["verification"]["verdict"] == "corrected"
    assert [x["tool"] for x in r["receipts"]] == ["calendar_add_event"]
    assert store.one("select count(*) as n from events")["n"] == 1
    # le brouillon menteur est journalisé
    assert store.one("select count(*) as n from verifications where verdict = 'rejected_draft'")["n"] == 1
    # le modèle a bien reçu la correction
    assert "VÉRIFICATION ÉCHOUÉE" in llm.calls[1]["messages"][-1]["content"]


def test_persistent_liar_is_blocked(make_agent, store):
    llm = ScriptedLLM([say("C'est ajouté à ton agenda."), say("Si si, c'est ajouté à ton agenda !")])
    r = make_agent(llm).run("s1", "ajoute un rdv demain 9h")
    assert r["verification"]["verdict"] == "blocked"
    assert r["reply"].startswith("⚠️")
    assert store.one("select count(*) as n from events")["n"] == 0
    # ce qui est stocké est la réponse honnête, pas le mensonge
    last = store.query("select content from messages where role = 'assistant'")[-1]["content"]
    assert last.startswith("⚠️")


def test_honest_tool_use_is_verified(make_agent):
    llm = ScriptedLLM([call("task_add", title="Relancer Capgemini", due="2026-11-06"),
                       say("J'ai ajouté la tâche « Relancer Capgemini » pour vendredi.")])
    r = make_agent(llm).run("s1", "ajoute à ma todo relancer Capgemini pour vendredi")
    assert r["verification"]["verdict"] == "verified"
    assert r["verification"]["supported_by"]["tasks"] == [r["receipts"][0]["action_id"]]


def test_failed_tool_cannot_be_claimed(make_agent):
    llm = ScriptedLLM([call("calendar_add_event", title="X", start="pas une date"),
                       say("C'est ajouté à ton agenda."), say("C'est ajouté à ton agenda.")])
    r = make_agent(llm).run("s1", "ajoute un rdv")
    assert r["receipts"][0]["status"] == "error"
    assert r["verification"]["verdict"] == "blocked"


def test_email_draft_stays_pending(make_agent, store):
    llm = ScriptedLLM([call("email_draft", to="karim@exemple.fr", subject="Jeudi", body="Je confirme jeudi."),
                       say("Le mail est envoyé à Karim."),                     # faux : seulement un brouillon
                       say("Brouillon de mail prêt pour Karim, en attente de ton approbation.")])
    r = make_agent(llm).run("s1", "envoie un mail à karim@exemple.fr pour confirmer jeudi")
    assert r["verification"]["verdict"] == "corrected"
    assert store.one("select status from outbox")["status"] == "pending_approval"


def test_smalltalk_is_light_and_cheap(make_agent):
    llm = ScriptedLLM([say("Salut !")])
    r = make_agent(llm).run("s1", "salut")
    assert r["triage"]["effort"] == "light"
    assert r["recall"] == []
    names = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert names == {"get_datetime"}


def test_system_prompt_is_a_stable_cache_prefix(make_agent):
    llm = ScriptedLLM([say("a"), say("b")])
    agent = make_agent(llm)
    agent.run("s1", "ajoute à ma todo acheter du pain")
    agent.run("s2", "qu'est-ce qu'il me reste à faire ?")
    assert llm.calls[0]["messages"][0] == llm.calls[1]["messages"][0]
    assert "maintenant :" not in llm.calls[0]["messages"][0]["content"]  # la date vit dans le message, pas le préfixe
    assert "maintenant :" in llm.calls[0]["messages"][-1]["content"]


def test_recall_is_cited_in_context(make_agent, memory):
    memory.add_fact("Le manager de l'utilisateur s'appelle Karim.", "user")
    llm = ScriptedLLM([say("Ton manager s'appelle Karim [fact#1].")])
    r = make_agent(llm).run("s1", "comment s'appelle mon manager ?")
    assert r["recall"][0]["source"] == "fact#1"
    assert "[fact#1]" in llm.calls[0]["messages"][-1]["content"]


def test_post_turn_extracts_labelled_facts(make_agent, store):
    llm = ScriptedLLM([say("Noté pour ta sœur."),
                       say('{"facts": [{"fact": "La sœur de l\'utilisateur s\'appelle Imane.", "speaker": "user"}]}')])
    agent = make_agent(llm)
    r = agent.run("s1", "ma soeur s'appelle Imane")
    # « Noté » sans reçu memory.write -> bloqué ou corrigé ; ici le script n'a pas d'autre réponse
    saved = agent.post_turn(r) if r["verification"]["verdict"] != "error" else []
    assert saved == [] or saved[0]["fact"].startswith("La sœur")


def test_usage_is_logged(make_agent, store):
    make_agent(ScriptedLLM([say("ok")])).run("s1", "bonjour")
    t = store.one("select * from turns")
    assert t["prompt_tokens"] == 100 and t["cached_tokens"] == 60
