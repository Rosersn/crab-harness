"""Storage subsystem — ObjectStorage protocol and implementations."""

import threading

from crab_platform.storage.interface import ObjectStorage
from crab_platform.storage.local import LocalObjectStorage

__all__ = ["ObjectStorage", "LocalObjectStorage", "get_object_storage"]

_instance: ObjectStorage | None = None
_lock = threading.Lock()


def get_object_storage() -> ObjectStorage:
    """Return the shared ObjectStorage instance (lazy-initialized from config)."""
    global _instance
    if _instance is not None:
        return _instance

    with _lock:
        if _instance is not None:
            return _instance

        from crab_platform.config.platform_config import get_platform_config

        config = get_platform_config()
        backend = (config.storage_backend or "local").lower()

        if backend == "bos":
            from crab_platform.storage.bos import BOSObjectStorage
            _instance = BOSObjectStorage()
        elif backend == "oss":
            from crab_platform.storage.oss import OSSObjectStorage
            _instance = OSSObjectStorage()
        else:
            _instance = LocalObjectStorage()

    return _instance
