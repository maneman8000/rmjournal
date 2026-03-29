import logging
from typing import List, Optional

from storage.base import StorageProvider

_logger = logging.getLogger(__name__)


def _to_js_bytes(data: bytes):
    """Convert Python bytes to a JS Uint8Array via pyodide FFI."""
    from pyodide.ffi import to_js
    import js

    return to_js(data, dict_converter=js.Object.fromEntries)


class R2StorageProvider(StorageProvider):
    """
    Cloudflare R2-backed StorageProvider implementation.

    Uses the R2 bucket binding exposed via Python Workers JS FFI.
    Python bytes must be converted to JS ArrayBufferView (Uint8Array)
    before passing to R2 APIs.

    Args:
        bucket: The R2 bucket binding (env.R2_BUCKET from wrangler.jsonc).
    """

    def __init__(self, bucket):
        self._bucket = bucket

    async def put(
        self, key: str, data: bytes, content_type: Optional[str] = None
    ) -> None:
        """Store data at the given key."""
        # R2 requires a JS-compatible buffer type, not Python bytes
        js_data = _to_js_bytes(data)
        if content_type:
            from pyodide.ffi import to_js
            import js

            http_metadata = to_js(
                {"contentType": content_type},
                dict_converter=js.Object.fromEntries,
            )
            await self._bucket.put(key, js_data, httpMetadata=http_metadata)
        else:
            await self._bucket.put(key, js_data)
        _logger.debug(f"R2 put: {key}")

    async def get(self, key: str) -> Optional[bytes]:
        """Retrieve data for the given key. Returns None if not found."""
        obj = await self._bucket.get(key)
        # R2 returns JS null when key doesn't exist
        if obj is None or not hasattr(obj, "arrayBuffer"):
            return None
        array_buffer = await obj.arrayBuffer()
        # Convert JS ArrayBuffer back to Python bytes
        from pyodide.ffi import to_py

        return bytes(to_py(array_buffer))

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        obj = await self._bucket.head(key)
        return obj is not None and hasattr(obj, "key")

    async def list(self, prefix: str = "") -> List[str]:
        """List all keys with the given prefix."""
        from pyodide.ffi import to_js
        import js

        keys = []
        cursor = None

        while True:
            options = {"prefix": prefix, "limit": 1000}
            if cursor:
                options["cursor"] = cursor
            js_options = to_js(options, dict_converter=js.Object.fromEntries)

            result = await self._bucket.list(js_options)
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
