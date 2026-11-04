"""Compétence tasks : une liste de tâches personnelle, conservée dans la base SQLite."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from atlas.skills.base import Skill, SkillContext, tool

# Un titre vide ou ne contenant que des espaces est refusé ; 200 caractères au plus.
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TaskId = Annotated[
    int, Field(ge=1, description="Numéro de la tâche, tel qu'affiché par list_tasks.")
]


class TaskNotFoundError(ValueError):
    """Aucune tâche ne porte ce numéro."""

    def __init__(self, task_id: int) -> None:
        super().__init__(f"Tâche n°{task_id} introuvable : vérifie le numéro avec list_tasks.")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TasksSkill(Skill):
    """Ajoute, liste, termine et supprime des tâches."""

    name = "tasks"

    def __init__(self, context: SkillContext | None = None) -> None:
        super().__init__(context)
        # Échoue au chargement, pas au premier appel, si le contexte manque.
        self._connection = self.require_context().connection

    def _get(self, task_id: int) -> Any:
        row = self._connection.execute(
            "SELECT id, title, due, done FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(task_id)
        return row

    @tool(effect="tasks.write")
    def add_task(self, title: Title, due: date | None = None) -> dict[str, Any]:
        """Ajoute une tâche à faire, avec une échéance facultative (date au format AAAA-MM-JJ)."""
        cursor = self._connection.execute(
            "INSERT INTO tasks (title, due, created_at) VALUES (?, ?, ?)",
            (title, due.isoformat() if due else None, _now()),
        )
        return {"id": cursor.lastrowid, "title": title, "due": due.isoformat() if due else None}

    @tool()
    def list_tasks(self, include_done: bool = False) -> list[dict[str, Any]]:
        """Liste les tâches en cours (et les tâches terminées si include_done est vrai)."""
        rows = self._connection.execute(
            "SELECT id, title, due, done FROM tasks WHERE done = 0 OR ? "
            "ORDER BY done, due IS NULL, due, id",
            (int(include_done),),
        ).fetchall()
        return [
            {"id": row["id"], "title": row["title"], "due": row["due"], "done": bool(row["done"])}
            for row in rows
        ]

    @tool(effect="tasks.write")
    def complete_task(self, task_id: TaskId) -> dict[str, Any]:
        """Marque une tâche comme terminée."""
        row = self._get(task_id)
        already_done = bool(row["done"])
        if not already_done:
            self._connection.execute(
                "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?", (_now(), task_id)
            )
        return {"id": task_id, "title": row["title"], "done": True, "already_done": already_done}

    @tool(effect="tasks.delete")
    def delete_task(self, task_id: TaskId) -> dict[str, Any]:
        """Supprime définitivement une tâche (demande une validation humaine)."""
        row = self._get(task_id)
        self._connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return {"id": task_id, "title": row["title"]}
