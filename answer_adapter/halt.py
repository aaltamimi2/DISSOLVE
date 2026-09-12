"""Halt record for closed-form adapter failures."""

from __future__ import annotations


class AdapterHalt(Exception):
    def __init__(self, halt: str, **info: object) -> None:
        self.halt = halt
        self.info = dict(info)
        super().__init__(halt)

    def as_dict(self) -> dict:
        out = {"halt": self.halt}
        out.update(self.info)
        return out
