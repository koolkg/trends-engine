"""CONN-1's fixture, read and sliced into poll-sized batches (CONN-3).

A PoC limitation: this is a single file-based connector
replaying static data, with no poll-state tracking behind it (not even in
Redis) — so "where we are in the fixture" lives only in this in-process
generator, never persisted. `iter_batches(..., loop=True)` (the default)
wraps around forever once the fixture is exhausted, so the trigger contract
downstream keeps getting exercised for as long as the connector runs.
"""
import itertools
import json
from typing import Any, Dict, Iterator, List

Item = Dict[str, Any]


def load_fixture(path: str) -> List[Item]:
    """Parses a JSON-Lines fixture file into a list of dicts. Raises
    ValueError (with the offending line number) on malformed JSON, and on
    an empty file — an empty fixture can't simulate anything."""
    items: List[Item] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON — {exc}") from exc
    if not items:
        raise ValueError(f"{path}: fixture file has no content")
    return items


def iter_batches(items: List[Item], batch_size: int, loop: bool = True) -> Iterator[List[Item]]:
    """Yields successive slices of `items`, each slice simulating one poll
    cycle's worth of "new" content.

    loop=True (the default): cycles through `items` indefinitely, wrapping
    back to the start once exhausted — this generator never stops on its
    own, matching a connector that keeps "polling" a source that never runs
    dry. loop=False: plain slicing, stopping (a shorter final batch, then
    StopIteration) once `items` is exhausted — used by tests and by a
    one-shot/finite run.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not items:
        return
    if not loop:
        for i in range(0, len(items), batch_size):
            yield items[i : i + batch_size]
        return
    source = itertools.cycle(items)
    while True:
        yield [next(source) for _ in range(batch_size)]
