import pytest

from app.agent import Agent
from app.db import Store
from app.llm import LLMResponse, ToolCall
from app.memory.core import MemorySystem
from app.triage import Triager

_TRIAGER = Triager()  # entraîné une fois pour toute la suite


@pytest.fixture
def store():
    return Store(sqlite_path=":memory:", database_url="")


@pytest.fixture
def memory(store, tmp_path):
    return MemorySystem(store, notes_dir=str(tmp_path / "notes"))


@pytest.fixture
def make_agent(store, memory):
    def _make(llm):
        return Agent(store, llm, memory=memory, triager=_TRIAGER)
    return _make


def say(text):
    return LLMResponse(text, usage={"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 60, "cost": 0.0001})


def call(name, **args):
    return LLMResponse(None, [ToolCall(f"c_{name}", name, args)],
                       usage={"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 60, "cost": 0.0001})
