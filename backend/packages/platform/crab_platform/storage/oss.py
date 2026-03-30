"""Alibaba Cloud OSS ObjectStorage implementation.

The OSS SDK (oss2) is synchronous; all calls are wrapped with
``run_in_executor`` so the async interface never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import functools
from io import BytesIO
from typing import BinaryIO

from crab_platform.config.platform_config import get_platform_config


def _get_oss_bucket():  # type: ignore[return]
    """Lazy-import OSS client so the module can be imported without oss2."""
    try:
        import oss2
    except ImportError as exc:
        raise ImportError(
            "oss2 is required for OSS storage. Install it with: uv add oss2"
        ) from exc

    config = get_platform_config()
    auth = oss2.Auth(
        config.oss_access_key_id or "",
        config.oss_access_key_secret or "",
    )
    endpoint = config.oss_endpoint or "https://oss-cn-hangzhou.aliyuncs.com"
    bucket_name = config.oss_bucket or "crab-harness"
    return oss2.Bucket(auth, endpoint, bucket_name)


class OSSObjectStorage:
    """Alibaba Cloud OSS implementation of the ObjectStorage protocol."""

    def __init__(self, bucket_name: str | None = None) -> None:
        config = get_platform_config()
        self._bucket_name = bucket_name or config.oss_bucket or "crab-harness"
        self._bucket = _get_oss_bucket()

    def _run_sync(self, fn, *args):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, functools.partial(fn, *args))

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str = "application/octet-stream") -> str:
        import oss2

        if isinstance(data, bytes):
            data = BytesIO(data)
        elif not isinstance(data, BytesIO):
            data = BytesIO(data.read())

        headers = {"Content-Type": content_type}
        await self._run_sync(
            self._bucket.put_object, key, data, headers,
        )
        return key

    async def get(self, key: str) -> bytes:
        result = await self._run_sync(self._bucket.get_object, key)
        # oss2 returns a stream-like object; read all content
        content = await self._run_sync(result.read)
        return content if isinstance(content, bytes) else content.encode()

    async def delete(self, key: str) -> None:
        try:
            await self._run_sync(self._bucket.delete_object, key)
        except Exception:
            pass  # No-op if not found

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        url = await self._run_sync(
            self._bucket.sign_url, "GET", key, expires_seconds,
        )
        return url

    async def list_keys(self, prefix: str) -> list[str]:
        import oss2

        keys: list[str] = []

        def _list_all():
            for obj in oss2.ObjectIterator(self._bucket, prefix=prefix):
                keys.append(obj.key)

        await self._run_sync(_list_all)
        return keys

    async def exists(self, key: str) -> bool:
        try:
            exists = await self._run_sync(self._bucket.object_exists, key)
            return bool(exists)
        except Exception:
            return False
