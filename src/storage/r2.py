import json
import logging
from typing import List, Optional

from .base import StorageProvider

_logger = logging.getLogger(__name__)


class R2StorageProvider(StorageProvider):
    """
    Cloudflare R2-backed StorageProvider implementation.

    Uses the R2 bucket binding exposed via Python Workers JS FFI.
    All methods are async as R2 operations are inherently asynchronous
    in the Workers runtime.

    Args:
        bucket: The R2 bucket binding (env.R2_BUCKET from wrangler.jsonc).
    """

    def __init__(self, bucket):
        self._bucket = bucket

    async def put(
        self, key: str, data: bytes, content_type: Optional[str] = None
    ) -> None:
        """Store data at the given key."""
        options = {}
        if content_type:
            options["httpMetadata"] = {"contentType": content_type}
        await self._bucket.put(key, data, **options)
        _logger.debug(f"R2 put: {key}")

    async def get(self, key: str) -> Optional[bytes]:
        """Retrieve data for the given key. Returns None if not found."""
        obj = await self._bucket.get(key)
        if obj is None:
            return None
        return await obj.arrayBuffer()

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        obj = await self._bucket.head(key)
        return obj is not None

    async def list(self, prefix: str = "") -> List[str]:
        """List all keys with the given prefix."""
        keys = []
        cursor = None

        while True:
            options = {"prefix": prefix, "limit": 1000}
            if cursor:
                options["cursor"] = cursor

            result = await self._bucket.list(options)
            for obj in result.objects:
                keys.append(obj.key)

            if not result.truncated:
                break
            cursor = result.cursor

        return keys

    async def delete(self, key: str) -> None:
        """Delete the object at the given key."""
        await self._bucket.delete(key)
        _logger.debug(f"R2 delete: {key}")
