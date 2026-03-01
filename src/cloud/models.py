from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict

class ItemType(Enum):
    DOCUMENT = "DocumentType"
    COLLECTION = "CollectionType"
    FILE = "0"
    DOC_INDEX = "80000000"

@dataclass
class Entry:
    hash: str
    type: str
    id: str
    subfiles: int
    size: int = 0

    def to_dict(self) -> dict:
        return {
            "hash": self.hash,
            "type": self.type,
            "id": self.id,
            "subfiles": self.subfiles,
            "size": self.size
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            hash=data["hash"],
            type=data["type"],
            id=data["id"],
            subfiles=data["subfiles"],
            size=data.get("size", 0)
        )

    @classmethod
    def from_line(cls, line: str):
        # Schema v3: hash:type:id:subfiles:size
        fields = line.split(':')
        if len(fields) != 5:
            raise ValueError(f"Invalid entry line: {line}")
        return cls(
            hash=fields[0],
            type=fields[1],
            id=fields[2],
            subfiles=int(fields[3]),
            size=int(fields[4])
        )

@dataclass
class MetaItem:
    id: str
    version: int
    visible_name: str
    parent: str
    type: str
    last_modified: str
    last_opened_page: int
    success: bool = True
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "ID": self.id,
            "Version": self.version,
            "visibleName": self.visible_name,
            "parent": self.parent,
            "type": self.type,
            "lastModified": self.last_modified,
            "lastOpenedPage": self.last_opened_page,
            "Success": self.success,
            "Message": self.message
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            id=data.get("ID", ""),
            version=data.get("Version", 0),
            visible_name=data.get("visibleName", ""),
            parent=data.get("parent", ""),
            type=data.get("type", "DocumentType"),
            last_modified=data.get("lastModified", ""),
            last_opened_page=data.get("lastOpenedPage", 0),
            success=data.get("Success", True),
            message=data.get("Message", "")
        )

@dataclass
class BlobDoc:
    id: str
    hash: str
    entries: List[Entry] = field(default_factory=list)
    metadata: Optional[MetaItem] = None

    @property
    def visible_name(self) -> str:
        meta = self.metadata
        if meta:
            return meta.visible_name
        return self.id

    @property
    def parent(self) -> str:
        meta = self.metadata
        if meta:
            return meta.parent
        return ""

    @property
    def is_directory(self) -> bool:
        meta = self.metadata
        if meta:
            return meta.type == "CollectionType"
        return False
