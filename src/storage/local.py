import os
from pathlib import Path
from typing import List, Optional
from .base import StorageProvider

class LocalStorageProvider(StorageProvider):
    """
    Storage provider that uses the local file system.
    """
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        # Prevent path traversal by joining and resolving
        path = (self.base_dir / key).resolve()
        if not str(path).startswith(str(self.base_dir)):
            raise ValueError(f"Invalid key (path traversal attempt): {key}")
        return path

    def put(self, key: str, data: bytes, content_type: Optional[str] = None):
        path = self._get_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def get(self, key: str) -> bytes:
        path = self._get_path(key)
        with open(path, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return self._get_path(key).exists()

    def list(self, prefix: str = "") -> List[str]:
        search_dir = self.base_dir / prefix
        if not search_dir.exists():
            return []
        
        keys = []
        for root, _, files in os.walk(search_dir):
            for file in files:
                full_path = Path(root) / file
                # Get relative path from base_dir as the key
                key = str(full_path.relative_to(self.base_dir))
                keys.append(key)
        return keys

    def delete(self, key: str):
        path = self._get_path(key)
        if path.exists():
            path.unlink()
