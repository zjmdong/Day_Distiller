from __future__ import annotations

import os
import platform
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
            if local:
                root = Path(local) / "DayDistillerV2"
            elif platform.system() == "Darwin":
                root = Path.home() / "Library" / "Application Support" / "Day Distiller"
            else:
                root = Path.home() / ".day-distiller-v2"
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
