"""Storage. Plain JSONL in git, sharded by month so a weekly run rewrites a few
small files rather than one large one.

Every file under data/ is owned by exactly one workflow (see README). The persist
step relies on that: it lays a workflow's outputs back onto the new head after a push
race, which is only safe when no other workflow writes the same file.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

_MONTH = re.compile(r"^(\d{4}-\d{2})")


def dumps(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: {e}") from e
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    """Atomic: a crash mid-write leaves the old file, never half of a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(dumps(r) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(dumps(r) + "\n")
            n += 1
    return n


def month_of(ts: str | None) -> str:
    if ts:
        m = _MONTH.match(ts)
        if m:
            return m.group(1)
    return "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ShardedTable:
    """Rows keyed by `key`, stored in `<dir>/<shard>.jsonl`.

    A row stays in the shard it was first written to, so a row's shard never
    depends on fields that change later.
    """

    def __init__(self, directory: Path, key: str):
        self.dir = directory
        self.key = key
        self.rows: dict[str, dict] = {}
        self.shard_of: dict[str, str] = {}
        self._touched: set[str] = set()
        for p in sorted(directory.glob("*.jsonl")):
            for r in read_jsonl(p):
                k = r[key]
                if k in self.rows:
                    raise ValueError(f"duplicate {key}={k} in {p.name} and {self.shard_of[k]}.jsonl")
                self.rows[k] = r
                self.shard_of[k] = p.stem

    def __contains__(self, k: str) -> bool:
        return k in self.rows

    def get(self, k: str) -> dict | None:
        return self.rows.get(k)

    def upsert(self, row: dict, shard: str, merge: Callable[[dict, dict], dict] | None = None) -> bool:
        """Insert or update. Returns True when the row is new."""
        k = row[self.key]
        old = self.rows.get(k)
        if old is None:
            self.rows[k] = row
            self.shard_of[k] = shard
            self._touched.add(shard)
            return True
        new = merge(old, row) if merge else {**old, **row}
        if new != old:
            self.rows[k] = new
            self._touched.add(self.shard_of[k])
        return False

    def save(self) -> list[str]:
        """Rewrite only the shards that changed. Returns their names."""
        by_shard: dict[str, list[dict]] = {}
        for k, s in self.shard_of.items():
            if s in self._touched:
                by_shard.setdefault(s, []).append(self.rows[k])
        for s, rows in by_shard.items():
            rows.sort(key=lambda r: str(r[self.key]))
            write_jsonl(self.dir / f"{s}.jsonl", rows)
        done = sorted(self._touched)
        self._touched.clear()
        return done


def read_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
