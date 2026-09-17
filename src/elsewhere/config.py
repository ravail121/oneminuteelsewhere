from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    root: Path
    raw: dict[str, Any]

    @property
    def brand(self) -> dict[str, Any]:
        return self.raw["brand"]

    @property
    def models(self) -> dict[str, Any]:
        return self.raw["models"]

    @property
    def formats(self) -> dict[str, Any]:
        return self.raw["formats"]

    @property
    def publishing(self) -> dict[str, Any]:
        return self.raw["publishing"]

    @property
    def safety(self) -> dict[str, Any]:
        return self.raw["safety"]

    @property
    def costs(self) -> dict[str, Any]:
        return self.raw["costs"]

    def path(self, name: str) -> Path:
        return self.root / self.raw["paths"][name]


def load_settings(config_path: str | Path = "config.yaml") -> Settings:
    path = Path(config_path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return Settings(root=path.parent, raw=raw)
