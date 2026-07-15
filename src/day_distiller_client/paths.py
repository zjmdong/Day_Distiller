from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    database: Path
    imports: Path
    reports: Path
    cache: Path
    logs: Path

    @classmethod
    def default(cls) -> "AppPaths":
        override = os.environ.get("DAY_DISTILLER_DATA_DIR")
        if override:
            root = Path(override).expanduser().resolve()
        else:
            local = os.environ.get("LOCALAPPDATA")
            root = Path(local) / "DayDistillerV2" if local else Path.home() / ".day-distiller-v2"
        return cls.from_root(root)

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        root = Path(root)
        return cls(
            root=root,
            database=root / "day_distiller.sqlite3",
            imports=root / "imports",
            reports=root / "reports",
            cache=root / "cache",
            logs=root / "logs",
        )

    def ensure(self) -> "AppPaths":
        for path in (self.root, self.imports, self.reports, self.cache, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        return self

