import httpx
import logging
import json
import io
import zipfile
from typing import List, Optional, Dict
from .auth import AuthManager
from .models import Entry, MetaItem, BlobDoc, ItemType
from .cache import MetadataCache, KVMetadataCache

_logger = logging.getLogger(__name__)

# Sync v1.5 Hosts
NEW_FILE_HOST = "https://eu.tectonic.remarkable.com"
ROOT_URL = f"{NEW_FILE_HOST}/sync/v4/root"
BLOB_URL = f"{NEW_FILE_HOST}/sync/v3/files/"


class RemarkableClient:
    def __init__(
        self, auth_manager: AuthManager, cache: Optional[MetadataCache] = None
    ):
        self.auth = auth_manager
        self.cache = cache or KVMetadataCache()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        token = await self.auth.get_user_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, headers=headers, **kwargs)

        # Handle 401 Unauthorized by refreshing token once
        if response.status_code == 401:
            _logger.info("User token expired, refreshing...")
            token = await self.auth.get_user_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, headers=headers, **kwargs)

        return response

    async def get_root_info(self) -> Dict:
        """Fetch the current root hash and generation."""
        response = await self._request("GET", ROOT_URL)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to get root info: {response.status_code} {response.text}"
            )

    async def get_blob(self, hash_str: str) -> Optional[bytes]:
        """Fetch a blob by its hash."""
        url = f"{BLOB_URL}{hash_str}"
        response = await self._request("GET", url)
        if response.status_code == 200:
            return response.content
        return None

    def parse_index(self, content: bytes) -> List[Entry]:
        """Parse Schema v3 index content."""
        lines = content.decode("utf-8").splitlines()
        if not lines:
            return []

        schema_version = lines[0].strip()
        if schema_version != "3":
            _logger.warning(f"Unexpected schema version: {schema_version}")

        entries = []
        for line in lines[1:]:
            if line.strip():
                entries.append(Entry.from_line(line.strip()))
        return entries

    async def list_docs(self) -> List[BlobDoc]:
        """List all documents by traversing the hash tree with caching."""
        root_info = await self.get_root_info()
        root_hash = root_info.get("hash") or root_info.get("Hash")
        if not root_hash:
            return []

        root_blob_content = await self.get_blob(root_hash)
        root_entries = self.parse_index(root_blob_content)

        docs = []
        for entry in root_entries:
            cached_doc = await self.cache.get(entry.id, entry.hash)
            if cached_doc:
                docs.append(cached_doc)
                continue

            try:
                doc_index_content = await self.get_blob(entry.hash)
                doc_subentries = self.parse_index(doc_index_content)

                doc = BlobDoc(id=entry.id, hash=entry.hash, entries=doc_subentries)

                meta_entry = next(
                    (e for e in doc_subentries if e.id.endswith(".metadata")), None
                )
                if meta_entry:
                    meta_json = await self.get_blob(meta_entry.hash)
                    meta_data = json.loads(meta_json)
                    doc.metadata = MetaItem.from_dict(meta_data)

                await self.cache.set(entry.id, doc)
                docs.append(doc)
            except Exception as e:
                _logger.error(f"Error processing doc {entry.id}: {e}")

        await self.cache.prune([e.id for e in root_entries])

        return docs

    async def get_doc(self, doc_id: str) -> Optional[BlobDoc]:
        """Fetch a single document's metadata (uses cache)."""
        root_info = await self.get_root_info()
        root_hash = root_info.get("hash") or root_info.get("Hash")
        if not root_hash:
            return None

        root_blob_content = await self.get_blob(root_hash)
        root_entries = self.parse_index(root_blob_content)

        entry = next((e for e in root_entries if e.id == doc_id), None)
        if not entry:
            return None

        cached_doc = await self.cache.get(doc_id, entry.hash)
        if cached_doc:
            return cached_doc

        try:
            doc_index_content = await self.get_blob(entry.hash)
            doc_subentries = self.parse_index(doc_index_content)

            doc = BlobDoc(id=entry.id, hash=entry.hash, entries=doc_subentries)

            meta_entry = next(
                (e for e in doc_subentries if e.id.endswith(".metadata")), None
            )
            if meta_entry:
                meta_json = await self.get_blob(meta_entry.hash)
                meta_data = json.loads(meta_json)
                doc.metadata = MetaItem.from_dict(meta_data)

            await self.cache.set(doc_id, doc)
            return doc
        except Exception as e:
            _logger.error(f"Error fetching doc {doc_id}: {e}")
            return None

    async def download_doc_zip(self, doc_id: str) -> bytes:
        """
        Fetch all component blobs for a document and assemble them into a ZIP archive.
        (Sync v1.5 does not provide pre-zipped files server-side.)
        """
        doc = await self.get_doc(doc_id)

        if not doc:
            raise ValueError(f"Document with ID {doc_id} not found.")
        if not doc.entries:
            raise Exception(f"Document {doc_id} has no components to download.")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for entry in doc.entries:
                try:
                    blob_content = await self.get_blob(entry.hash)
                    zip_file.writestr(entry.id, blob_content)
                except Exception as e:
                    _logger.error(
                        f"Failed to fetch component {entry.id} for doc {doc_id}: {e}"
                    )
                    raise

        return zip_buffer.getvalue()
