-- Tâches de la compétence tasks. Le titre est borné aussi ici : défense en profondeur.
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    due TEXT,
    done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX idx_tasks_done_due ON tasks (done, due);
