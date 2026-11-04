-- Reçus d'outils : la trace de ce qui a réellement été tenté (args et result en JSON).
CREATE TABLE receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    effect TEXT,
    args TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL,
    parent_id INTEGER REFERENCES receipts (id)
);

CREATE INDEX idx_receipts_turn ON receipts (turn_id, id);

-- Journal de consommation du modèle : un appel par ligne.
CREATE TABLE usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    cost REAL,
    estimated INTEGER NOT NULL
);

CREATE INDEX idx_usage_day ON usage_log (day);
CREATE INDEX idx_usage_turn ON usage_log (turn_id);
