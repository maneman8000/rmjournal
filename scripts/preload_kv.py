"""
KV Preload Script

Fetches all documents from reMarkable Cloud and uploads their metadata
to Cloudflare KV (RMJOURNAL_CACHE) using wrangler CLI.

This is a one-time operation to prime the KV cache so that the Worker
does not hit the 50 subrequest limit on its first execution.

Usage:
    cd /home/akihiro/Work/rmjournal
    uv run python scripts/preload_kv.py
"""

import sys
import os
import asyncio
import json
import subprocess
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from cloud.auth import AuthManager
from cloud.client import RemarkableClient
from cloud.cache import FileMetadataCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_logger = logging.getLogger(__name__)

KV_BINDING = "RMJOURNAL_CACHE"


def kv_put(key: str, value: str):
    """Upload a single KV entry via wrangler CLI."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "pywrangler",
            "kv",
            "key",
            "put",
            "--binding",
            KV_BINDING,
            key,
            value,
        ],
        capture_output=True,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    if result.returncode != 0:
        raise RuntimeError(f"wrangler kv put failed for {key}:\n{result.stderr}")


async def main():
    device_token = os.environ.get("RM_DEVICE_TOKEN")
    user_token = os.environ.get("RM_USER_TOKEN")

    if not device_token or not user_token:
        print("ERROR: RM_DEVICE_TOKEN and RM_USER_TOKEN must be set in .env")
        sys.exit(1)

    auth = AuthManager(device_token=device_token, user_token=user_token)
    # Use FileMetadataCache locally (no KV needed for fetching)
    cache = FileMetadataCache()
    client = RemarkableClient(auth_manager=auth, cache=cache)

    _logger.info("Fetching all documents from reMarkable Cloud...")
    docs = await client.list_docs()
    _logger.info(f"Fetched {len(docs)} documents")

    success = 0
    failed = 0

    for doc in docs:
        key = f"meta:{doc.id}"
        data = {
            "hash": doc.hash,
            "entries": [e.to_dict() for e in doc.entries],
            "metadata": doc.metadata.to_dict() if doc.metadata else None,
        }
        value = json.dumps(data, ensure_ascii=False)

        try:
            kv_put(key, value)
            _logger.info(
                f"  [{success + 1}/{len(docs)}] Uploaded: {doc.visible_name} ({doc.id[:8]}...)"
            )
            success += 1
        except Exception as e:
            _logger.error(f"  Failed: {doc.id[:8]}... - {e}")
            failed += 1

    _logger.info(f"\nDone. {success} uploaded, {failed} failed.")


if __name__ == "__main__":
    asyncio.run(main())
