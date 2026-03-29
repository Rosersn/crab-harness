"""ObjectStorage protocol — abstract interface for cloud object storage."""

from __future__ import annotations

from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class ObjectStorage(Protocol):
    """Vendor-neutral interface for blob/object storage (e.g. Baidu BOS, S3)."""

    async def put(self, key: str, data: bytes | BinaryIO, content_type: str = "application/octet-stream") -> str:
        """Upload an object. Returns the key."""
        ...

    async def get(self, key: str) -> bytes:
        """Download an object. Raises if not found."""
        ...

    async def delete(self, key: str) -> None:
        """Delete an object. No-op if not found."""
        ...

    async def generate_presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        """Generate a time-limited download URL."""
        ...

    async def list_keys(self, prefix: str) -> list[str]:
        """List object keys under a prefix."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if an object exists."""
        ...
