import requests
import logging
import json
import io
import zipfile
from typing import List, Optional, Dict
from .auth import AuthManager
from .models import Entry, MetaItem, BlobDoc, ItemType
from .cache import MetadataCache, FileMetadataCache

_logger = logging.getLogger(__name__)

# Sync v1.5 Hosts
NEW_FILE_HOST = "https://eu.tectonic.remarkable.com"
ROOT_URL = f"{NEW_FILE_HOST}/sync/v4/root"
BLOB_URL = f"{NEW_FILE_HOST}/sync/v3/files/"

class RemarkableClient:
    def __init__(self, auth_manager: Optional[AuthManager] = None, cache: Optional[MetadataCache] = None):
        self.auth = auth_manager or AuthManager()
        self.cache = cache or FileMetadataCache()

    def _request(self, method: str, url: str, **kwargs):
        token = self.auth.get_user_token()
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        kwargs["headers"] = headers

        response = requests.request(method, url, **kwargs)
        
        # Handle 401 Unauthorized by refreshing token
        if response.status_code == 401:
            _logger.info("User Token expired, refreshing...")
            token = self.auth.get_user_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            response = requests.request(method, url, **kwargs)
            
        return response

    def get_root_info(self) -> Dict:
        """Fetch the current Root Hash and Generation."""
        response = self._request("GET", ROOT_URL)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get root info: {response.status_code} {response.text}")

    def get_blob(self, hash_str: str) -> bytes:
        """Fetch a blob by its hash."""
        url = f"{BLOB_URL}{hash_str}"
        response = self._request("GET", url)
        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"Failed to fetch blob {hash_str}: {response.status_code}")

    def parse_index(self, content: bytes) -> List[Entry]:
        """Parse Schema v3 index content."""
        lines = content.decode("utf-8").splitlines()
        if not lines:
            return []
        
        # First line is Schema Version (e.g., "3")
        schema_version = lines[0].strip()
        if schema_version != "3":
            _logger.warning(f"Unexpected schema version: {schema_version}")
        
        entries = []
        for line in lines[1:]:
            if line.strip():
                entries.append(Entry.from_line(line.strip()))
        return entries

    def list_docs(self) -> List[BlobDoc]:
        """List all documents by traversing the hash tree with caching."""
        root_info = self.get_root_info()
        root_hash = root_info.get("hash") or root_info.get("Hash")
        if not root_hash:
            return []

        # 1. Get Root Index Blob
        root_blob_content = self.get_blob(root_hash)
        root_entries = self.parse_index(root_blob_content)
        
        docs = []
        for entry in root_entries:
            # Check cache first
            cached_doc = self.cache.get(entry.id, entry.hash)
            if cached_doc:
                docs.append(cached_doc)
                continue

            # Cache miss: fetch and parse
            try:
                doc_index_content = self.get_blob(entry.hash)
                doc_subentries = self.parse_index(doc_index_content)
                
                doc = BlobDoc(id=entry.id, hash=entry.hash, entries=doc_subentries)
                
                # Find .metadata entry
                meta_entry = next((e for e in doc_subentries if e.id.endswith(".metadata")), None)
                if meta_entry:
                    meta_json = self.get_blob(meta_entry.hash)
                    meta_data = json.loads(meta_json)
                    doc.metadata = MetaItem.from_dict(meta_data)
                
                # Update cache
                self.cache.set(entry.id, doc)
                docs.append(doc)
            except Exception as e:
                _logger.error(f"Error processing doc {entry.id}: {e}")
                
        # 3. Prune stale cache
        self.cache.prune([e.id for e in root_entries])
                
        return docs

    def get_doc(self, doc_id: str) -> Optional[BlobDoc]:
        """Fetch a single document's metadata (uses cache)."""
        root_info = self.get_root_info()
        root_hash = root_info.get("hash") or root_info.get("Hash")
        if not root_hash:
            return None

        root_blob_content = self.get_blob(root_hash)
        root_entries = self.parse_index(root_blob_content)
        
        entry = next((e for e in root_entries if e.id == doc_id), None)
        if not entry:
            return None

        # Check cache
        cached_doc = self.cache.get(doc_id, entry.hash)
        if cached_doc:
            return cached_doc

        # Cache miss
        try:
            doc_index_content = self.get_blob(entry.hash)
            doc_subentries = self.parse_index(doc_index_content)
            
            doc = BlobDoc(id=entry.id, hash=entry.hash, entries=doc_subentries)
            
            # Find .metadata entry
            meta_entry = next((e for e in doc_subentries if e.id.endswith(".metadata")), None)
            if meta_entry:
                meta_json = self.get_blob(meta_entry.hash)
                meta_data = json.loads(meta_json)
                doc.metadata = MetaItem.from_dict(meta_data)
            
            self.cache.set(doc_id, doc)
            return doc
        except Exception as e:
            _logger.error(f"Error fetching doc {doc_id}: {e}")
            return None

    def get_blob(self, blob_hash: str) -> Optional[bytes]:
        """Fetch raw blob content by hash."""
        url = f"{BLOB_URL}{blob_hash}"
        response = self._request("GET", url)
        if response.status_code == 200:
            return response.content
        return None

    def download_doc_zip(self, doc_id: str) -> bytes:
        """
        In Sync v1.5, documents aren't pre-zipped on the server.
        This method fetches all component blobs and assembles them into a ZIP archive.
        """
        # 1. Get the document metadata and component list (entries)
        doc = self.get_doc(doc_id)
        
        if not doc:
            raise ValueError(f"Document with ID {doc_id} not found.")
        
        if not doc.entries:
            raise Exception(f"Document {doc_id} has no components to download.")

        # 2. Create an in-memory ZIP archive
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for entry in doc.entries:
                try:
                    # entry.id is the filename (e.g., "id.metadata", "id/0.rm")
                    # entry.hash is the blob hash
                    blob_content = self.get_blob(entry.hash)
                    zip_file.writestr(entry.id, blob_content)
                except Exception as e:
                    _logger.error(f"Failed to fetch component {entry.id} for doc {doc_id}: {e}")
                    raise

        return zip_buffer.getvalue()
