from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EventStore:
    """외출 중 이벤트 로그 (JSONL)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 50, since_iso: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_iso and str(item.get("timestamp", "")) < since_iso:
                    continue
                rows.append(item)
        return rows[-limit:]

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")
