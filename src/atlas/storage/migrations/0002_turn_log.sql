-- Issue de chaque tour : terminé par l'outil respond (conforme), par du texte libre, ou en erreur.
-- Sert au taux de conformité du modèle (commande « atlas stats »).
CREATE TABLE turn_log (
    turn_id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('respond', 'free_text', 'error')),
    verdict TEXT NOT NULL
);

CREATE INDEX idx_turn_log_day ON turn_log (day);
