-- Trace des tours : événements dans l'ordre (appels au modèle, reçus, refus et leur raison, issue).
-- Sert à la commande « atlas tour ». Le numéro d'un tour est le rang de son événement « start ».
CREATE TABLE turn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_turn_events_turn ON turn_events (turn_id, id);
