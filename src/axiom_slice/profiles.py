from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import SliceRequest

PROFILE_TYPES = ("machine", "process", "filament")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileDocument:
    kind: str
    name: str
    path: Path
    data: dict[str, Any]
    vendor: str | None
    user: bool


class ProfileResolver:
    def __init__(self, user_root: Path, system_root: Path):
        self.user_root = user_root
        self.system_root = system_root
        self._index: dict[str, dict[str, list[ProfileDocument]]] | None = None

    @staticmethod
    def _load(path: Path, kind: str, vendor: str | None, user: bool) -> ProfileDocument | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        name = data.get("name") or path.stem
        if not isinstance(name, str) or not name:
            return None
        return ProfileDocument(kind, name, path, data, vendor, user)

    def _build_index(self) -> dict[str, dict[str, list[ProfileDocument]]]:
        index: dict[str, dict[str, list[ProfileDocument]]] = {kind: {} for kind in PROFILE_TYPES}
        for kind in PROFILE_TYPES:
            directory = self.user_root / kind
            if directory.is_dir():
                for path in sorted(directory.glob("*.json")):
                    doc = self._load(path, kind, None, True)
                    if doc:
                        index[kind].setdefault(doc.name, []).append(doc)
        if self.system_root.is_dir():
            for vendor_dir in sorted(path for path in self.system_root.iterdir() if path.is_dir()):
                for kind in PROFILE_TYPES:
                    directory = vendor_dir / kind
                    if not directory.is_dir():
                        continue
                    for path in sorted(directory.glob("*.json")):
                        doc = self._load(path, kind, vendor_dir.name, False)
                        if doc:
                            index[kind].setdefault(doc.name, []).append(doc)
        self._index = index
        return index

    @property
    def index(self) -> dict[str, dict[str, list[ProfileDocument]]]:
        return self._index or self._build_index()

    def refresh(self) -> None:
        self._index = None

    def _select(
        self, kind: str, name: str, child: ProfileDocument | None = None, user_only: bool = False
    ) -> ProfileDocument:
        candidates = list(self.index[kind].get(name.removesuffix(".json"), []))
        if user_only:
            candidates = [candidate for candidate in candidates if candidate.user]
        if child and child.vendor:
            same_vendor = [candidate for candidate in candidates if candidate.vendor == child.vendor]
            if same_vendor:
                candidates = same_vendor
        if not candidates:
            location = "user profile" if user_only else "profile"
            raise ProfileError(f"missing {kind} {location}: {name}")
        if len(candidates) > 1:
            paths = ", ".join(str(candidate.path) for candidate in candidates)
            raise ProfileError(f"ambiguous {kind} profile {name}: {paths}")
        return candidates[0]

    def resolve(self, kind: str, name: str, user_only: bool = True) -> tuple[dict[str, Any], list[str]]:
        if kind not in PROFILE_TYPES:
            raise ProfileError(f"unknown profile type: {kind}")
        leaf = self._select(kind, name, user_only=user_only)
        chain: list[ProfileDocument] = []
        seen: set[Path] = set()
        current = leaf
        while True:
            if current.path in seen:
                names = " -> ".join([item.name for item in chain] + [current.name])
                raise ProfileError(f"inheritance cycle in {kind} profiles: {names}")
            seen.add(current.path)
            chain.append(current)
            parent_name = current.data.get("inherits")
            if not parent_name:
                break
            current = self._select(kind, str(parent_name), child=current)
        merged: dict[str, Any] = {}
        for document in reversed(chain):
            merged.update(document.data)
        merged.pop("inherits", None)
        merged["name"] = leaf.name
        return merged, [document.name for document in chain]

    def resolve_request(self, request: SliceRequest) -> dict[str, dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}
        chains: dict[str, list[str]] = {}
        for kind in PROFILE_TYPES:
            profile, chain = self.resolve(kind, getattr(request, kind))
            resolved[kind] = profile
            chains[kind] = chain
        machine_names = set(chains["machine"])
        for kind in ("process", "filament"):
            compatible = resolved[kind].get("compatible_printers")
            if compatible and not machine_names.intersection(compatible):
                raise ProfileError(f"{request.machine} is not compatible with {getattr(request, kind)}")
        resolved["chains"] = chains
        return resolved

    def options(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for kind in PROFILE_TYPES:
            result[kind] = sorted(
                name for name, documents in self.index[kind].items() if any(document.user for document in documents)
            )
        return result
