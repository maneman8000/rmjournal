"""
rmjournal Worker entrypoint for Cloudflare Workers (Python Workers / Pyodide).

This module replaces the CLI-based entry point (src/journal/cli.py) for the
Workers environment. It handles:
  - Cron Trigger (scheduled): daily sync from reMarkable Cloud → R2
  - HTTP fetch (optional): manual trigger via HTTP request

TODO (see WORKERS_MIGRATION.md for full task list):
  - 2-3: Refactor src/cloud/auth.py to use Workers Secrets + KV
  - 2-4: Replace requests with httpx.AsyncClient in src/cloud/client.py
  - 2-5: Implement KVMetadataCache in src/cloud/cache.py
  - 2-6: Implement R2StorageProvider in src/storage/r2.py
  - 2-9: Make process_journal(), generate_index_page() async
"""

import logging
from datetime import date

from workers import WorkerEntrypoint, Response

_logger = logging.getLogger(__name__)


class Default(WorkerEntrypoint):
    async def scheduled(self, controller):
        """
        Cron Trigger handler. Runs daily (see wrangler.jsonc triggers.crons).
        """
        target_date = date.today()
        _logger.info(f"[scheduled] Starting journal sync for {target_date}")

        # TODO 2-3: Replace with Workers Secrets-based auth
        # device_token = self.env.RM_DEVICE_TOKEN
        # user_token = await self.env.RMJOURNAL_AUTH.get("auth:user_token") or self.env.RM_USER_TOKEN
        # auth = RemarkableAuth(device_token=device_token, user_token=user_token)

        # TODO 2-4: Replace with async RemarkableClient
        # client = RemarkableClient(auth=auth)

        # TODO 2-6: Replace with R2StorageProvider
        # storage = R2StorageProvider(self.env.R2_BUCKET)

        # TODO 2-5: Replace with KVMetadataCache
        # cache = KVMetadataCache(self.env.RMJOURNAL_CACHE)

        # TODO 2-9: Await async versions of these calls
        # from journal.sync import process_journal
        # from journal.web import generate_index_page
        # ctx = JournalContext(target_date, storage, client)
        # await process_journal(ctx)
        # await generate_index_page(storage)

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
