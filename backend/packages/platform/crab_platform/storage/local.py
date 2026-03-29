"""Local filesystem ObjectStorage implementation for development."""

from __future__ import annotations

import os
import shutil
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from crab_platform.config.platform_config import get_platform_config


class LocalObjectStorage:
    """Stores objects on the local filesystem under a configurable root directory.

    Intended for local development only — no presigned URLs, no real cloud storage.
    """

    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            config = get_platform_config()
            root_dir = config.storage_root or os.path.join(os.getcwd(), ".crab-storage")
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Prevent directory traversal
        resolved = (self._root / key).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(f"Invalid key (traversal detected): {key}")
        return resolved

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str = "application/octet-stream") -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_bytes(data.read())
        return key

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(f"Object not found: {key}")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        # Local dev: return a relative path — not a real URL
        return f"/storage/{key}"

    async def list_keys(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        root_str = str(self._root.resolve())
        result: list[str] = []
        for p in base.rglob("*"):
            if p.is_file():
                result.append(str(p.resolve()).removeprefix(root_str).lstrip("/"))
        return sorted(result)

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()
