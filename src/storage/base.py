from abc import ABC, abstractmethod
from typing import List, Optional


class StorageProvider(ABC):
    """
    Abstract base class for storage providers (Local, R2, etc.)
    All methods are async to support both local and remote (R2) implementations.
    """

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: Optional[str] = None):
        """Store data with a given key."""
        pass

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """Retrieve data for a given key."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        pass

    @abstractmethod
    async def list(self, prefix: str = "") -> List[str]:
        """List keys with a given prefix."""
        pass

    @abstractmethod
    async def delete(self, key: str):
        """Delete a key."""
        pass
