from abc import ABC, abstractmethod
from typing import List, Optional
from pathlib import Path

class StorageProvider(ABC):
    """
    Abstract base class for storage providers (Local, R2, etc.)
    """
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: Optional[str] = None):
        """Store data with a given key."""
        pass

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Retrieve data for a given key."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        pass

    @abstractmethod
    def list(self, prefix: str = "") -> List[str]:
        """List keys with a given prefix."""
        pass

    @abstractmethod
    def delete(self, key: str):
        """Delete a key."""
        pass
