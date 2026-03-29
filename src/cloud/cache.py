import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any, List
from cloud.models import Entry, MetaItem, BlobDoc

_logger = logging.getLogger(__name__)

from abc import ABC, abstractmethod


class MetadataCache(ABC):
    """
    Abstract base class for metadata caching.
    """

    @abstractmethod
    def get(self, doc_id: str, current_hash: str) -> Optional[BlobDoc]:
        """Retrieve cached document info if the hash matches."""
        pass

    @abstractmethod
    def set(self, doc_id: str, doc: BlobDoc):
        """Save document info to cache."""
        pass

    @abstractmethod
    def prune(self, active_ids: List[str]):
        """Remove stale cache entries."""
        pass


class FileMetadataCache(MetadataCache):
    """
    File-based implementation of document metadata caching.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            # Default to ~/.cache/rmjournal/metadata
            home = Path.home()
            self.cache_dir = home / ".cache" / "rmjournal" / "metadata"
        else:
            self.cache_dir = cache_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, doc_id: str) -> Path:
        return self.cache_dir / f"{doc_id}.json"

    def get(self, doc_id: str, current_hash: str) -> Optional[BlobDoc]:
        path = self._get_path(doc_id)
        if not path.exists():
            return None

        try:
            with open(path, "r") as f:
                data = json.load(f)

            if data.get("hash") != current_hash:
                return None

            # Reconstruct BlobDoc
            entries = [Entry.from_dict(e) for e in data.get("entries", [])]
            metadata = None
            if data.get("metadata"):
                metadata = MetaItem.from_dict(data["metadata"])

            return BlobDoc(
                id=doc_id, hash=current_hash, entries=entries, metadata=metadata
            )
        except Exception:
            return None

    def set(self, doc_id: str, doc: BlobDoc):
        path = self._get_path(doc_id)
        data = {
            "hash": doc.hash,
            "entries": [e.to_dict() for e in doc.entries],
            "metadata": doc.metadata.to_dict() if doc.metadata else None,
        }

        # Atomic write
        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(data, f)
        temp_path.replace(path)

    def prune(self, active_ids: List[str]):
        active_set = set(active_ids)
        for cache_file in self.cache_dir.glob("*.json"):
            doc_id = cache_file.stem
            if doc_id not in active_set:
                try:
                    cache_file.unlink()
                    _logger.info(f"Pruned stale cache file: {cache_file.name}")
                except Exception as e:
                    _logger.error(f"Failed to prune cache file {cache_file.name}: {e}")


class KVMetadataCache(MetadataCache):
    """
    Cloudflare KV-backed metadata cache.

    Uses the KV namespace binding exposed via Python Workers JS FFI.
    All methods are async as KV operations are inherently asynchronous
    in the Workers runtime.

    Args:
        kv_namespace: The KV namespace binding (env.RMJOURNAL_CACHE).
                      If None, all operations are no-ops (useful for testing).
    """

    def __init__(self, kv_namespace=None):
        self._kv = kv_namespace

    def _key(self, doc_id: str) -> str:
        return f"meta:{doc_id}"

    async def get(self, doc_id: str, current_hash: str) -> Optional[BlobDoc]:
        if self._kv is None:
            return None
        try:
            raw = await self._kv.get(self._key(doc_id))
            if raw is None:
                return None
            data = json.loads(raw)
            if data.get("hash") != current_hash:
                return None
            entries = [Entry.from_dict(e) for e in data.get("entries", [])]
            metadata = (
                MetaItem.from_dict(data["metadata"]) if data.get("metadata") else None
            )
            return BlobDoc(
                id=doc_id, hash=current_hash, entries=entries, metadata=metadata
            )
        except Exception as e:
            _logger.warning(f"KV cache get failed for {doc_id}: {e}")
            return None

    async def set(self, doc_id: str, doc: BlobDoc):
        if self._kv is None:
            return
        try:
            data = {
                "hash": doc.hash,
                "entries": [e.to_dict() for e in doc.entries],
                "metadata": doc.metadata.to_dict() if doc.metadata else None,
            }
            await self._kv.put(self._key(doc_id), json.dumps(data))
        except Exception as e:
            _logger.warning(f"KV cache set failed for {doc_id}: {e}")

    async def prune(self, active_ids: List[str]):
        if self._kv is None:
            return
        try:
            active_keys = {self._key(doc_id) for doc_id in active_ids}
            result = await self._kv.list({"prefix": "meta:"})
            for item in result.keys:
                if item.name not in active_keys:
                    await self._kv.delete(item.name)
                    _logger.info(f"KV cache pruned: {item.name}")
        except Exception as e:
            _logger.warning(f"KV cache prune failed: {e}")
