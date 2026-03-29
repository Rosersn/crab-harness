"""Baidu BOS ObjectStorage implementation.

The BOS SDK is synchronous; all calls are wrapped with ``run_in_executor``
so the async interface never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import functools
from io import BytesIO
from typing import BinaryIO

from crab_platform.config.platform_config import get_platform_config


def _get_bos_client():  # type: ignore[return]
    """Lazy-import BOS client so the module can be imported without bce-python-sdk."""
    try:
        from baidubce.bce_client_configuration import BceClientConfiguration
        from baidubce.auth.bce_credentials import BceCredentials
        from baidubce.services.bos.bos_client import BosClient
    except ImportError as exc:
        raise ImportError(
            "bce-python-sdk is required for BOS storage. Install it with: uv add bce-python-sdk"
        ) from exc

    config = get_platform_config()
    bce_config = BceClientConfiguration(
        credentials=BceCredentials(
            access_key_id=config.bos_access_key or "",
            secret_access_key=config.bos_secret_key or "",
        ),
        endpoint=config.bos_endpoint or "https://bj.bcebos.com",
    )
    return BosClient(bce_config)


class BOSObjectStorage:
    """Baidu BOS implementation of the ObjectStorage protocol."""

    def __init__(self, bucket: str | None = None) -> None:
        config = get_platform_config()
        self._bucket = bucket or config.bos_bucket or "crab-harness"
        self._client = _get_bos_client()

    def _run_sync(self, fn, *args):  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, functools.partial(fn, *args))

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str = "application/octet-stream") -> str:
        if isinstance(data, bytes):
            data = BytesIO(data)
        elif not isinstance(data, BytesIO):
            # Read non-BytesIO file-like objects into BytesIO so we can get content_length
            data = BytesIO(data.read())
        content_length = len(data.getvalue())
        await self._run_sync(
            self._client.put_object,
            self._bucket, key, data, content_length,
            content_type,
        )
        return key

    async def get(self, key: str) -> bytes:
        response = await self._run_sync(self._client.get_object_as_string, self._bucket, key)
        return response if isinstance(response, bytes) else response.encode()

    async def delete(self, key: str) -> None:
        try:
            await self._run_sync(self._client.delete_object, self._bucket, key)
        except Exception:
            pass  # No-op if not found

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        url = await self._run_sync(
            self._client.generate_pre_signed_url, self._bucket, key, expires_seconds,
        )
        return url

    async def list_keys(self, prefix: str) -> list[str]:
        response = await self._run_sync(
            self._client.list_objects, self._bucket, prefix=prefix,
        )
        return [obj.key for obj in getattr(response, "contents", []) or []]

    async def exists(self, key: str) -> bool:
        try:
            await self._run_sync(self._client.get_object_meta_data, self._bucket, key)
            return True
        except Exception:
            return False
