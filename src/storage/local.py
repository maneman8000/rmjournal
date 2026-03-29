import os
from pathlib import Path
from typing import List, Optional
from storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    """
    Storage provider that uses the local file system.
    Methods are async to match the StorageProvider interface.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        path = (self.base_dir / key).resolve()
        if not str(path).startswith(str(self.base_dir)):
            raise ValueError(f"Invalid key (path traversal attempt): {key}")
        return path

    async def put(self, key: str, data: bytes, content_type: Optional[str] = None):
        path = self._get_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    async def get(self, key: str) -> Optional[bytes]:
        path = self._get_path(key)
        if not path.exists():
            return None
        with open(path, "rb") as f:
            return f.read()

    async def exists(self, key: str) -> bool:
        return self._get_path(key).exists()

    async def list(self, prefix: str = "") -> List[str]:
        search_dir = self.base_dir / prefix
        if not search_dir.exists():
            return []

        keys = []
        for root, _, files in os.walk(search_dir):
            for file in files:
                full_path = Path(root) / file
                key = str(full_path.relative_to(self.base_dir))
                keys.append(key)
        return keys

    async def delete(self, key: str):
        path = self._get_path(key)
        if path.exists():
            path.unlink()
