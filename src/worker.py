"""
rmjournal Worker entrypoint for Cloudflare Workers (Python Workers / Pyodide).

Handles:
  - scheduled(): Cron Trigger — daily sync from reMarkable Cloud → R2
  - fetch(): HTTP handler — manual trigger via POST /trigger
"""

import logging
from datetime import date

from workers import WorkerEntrypoint, Response

from cloud.auth import AuthManager
from cloud.client import RemarkableClient
from cloud.cache import KVMetadataCache
from storage.r2 import R2StorageProvider
from journal.cli import JournalContext
from journal.sync import process_journal
from journal.web import generate_index_page

_logger = logging.getLogger(__name__)


class Default(WorkerEntrypoint):
    async def scheduled(self, controller):
        """
        Cron Trigger handler. Runs daily (see wrangler.jsonc triggers.crons).
        """
        target_date = date.today()
        _logger.info(f"[scheduled] Starting journal sync for {target_date}")

        auth = AuthManager(
            device_token=self.env.RM_DEVICE_TOKEN,
            user_token=self.env.RM_USER_TOKEN,
            kv_namespace=self.env.RMJOURNAL_AUTH,
        )
        cache = KVMetadataCache(kv_namespace=self.env.RMJOURNAL_CACHE)
        client = RemarkableClient(auth_manager=auth, cache=cache)
        storage = R2StorageProvider(self.env.R2_BUCKET)

        ctx = JournalContext(target_date=target_date, storage=storage, client=client)
        await process_journal(ctx)
        await generate_index_page(storage)

        _logger.info(f"[scheduled] Journal sync complete for {target_date}")

    async def fetch(self, request):
        """
        HTTP handler for manual trigger.
        Only accepts POST /trigger to avoid accidental invocations.
        """
        if request.method == "POST" and request.url.endswith("/trigger"):
            await self.scheduled(None)
            return Response("OK", status=200)

        return Response("Not Found", status=404)
