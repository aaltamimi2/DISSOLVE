"""Active injected defect for adversarial demonstrations. Production path leaves this unset."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

CURRENT: ContextVar[str | None] = ContextVar("answer_eval_mutation", default=None)


def get() -> str | None:
    return CURRENT.get()


@contextmanager
def using(name: str | None) -> Iterator[None]:
    token = CURRENT.set(name)
    try:
        yield
    finally:
        CURRENT.reset(token)
