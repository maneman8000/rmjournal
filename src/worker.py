"""
rmjournal Worker entrypoint for Cloudflare Workers (Python Workers / Pyodide).

Handles:
  - scheduled(): Cron Trigger — daily sync from reMarkable Cloud → R2
  - fetch(): HTTP handler
      POST /trigger        — manual sync trigger
      GET  /view/<path>    — R2 content viewer (token auth required)
"""

import logging
from datetime import date
from urllib.parse import urlparse, parse_qs

from workers import WorkerEntrypoint, Response

from cloud.auth import AuthManager
from cloud.client import RemarkableClient
from cloud.cache import KVMetadataCache
from storage.r2 import R2StorageProvider
from journal.cli import JournalContext
from journal.sync import process_journal
from journal.web import generate_index_page

_logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".css": "text/css",
    ".js": "text/javascript",
}


def _content_type(path: str) -> str:
    for ext, ct in _CONTENT_TYPES.items():
        if path.endswith(ext):
            return ct
    return "application/octet-stream"


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
        HTTP handler.

        POST /trigger       — manual sync trigger
        GET  /view/<path>   — R2 content viewer with token auth
        """
        url = str(request.url)
        parsed = urlparse(url)
        path = parsed.path

        # --- POST /trigger ---
        if request.method == "POST" and path == "/trigger":
            await self.scheduled(None)
            return Response("OK", status=200)

        # --- GET /view/<path> ---
        if request.method == "GET" and path.startswith("/view/"):
            expected = str(self.env.VIEW_TOKEN)

            # 1. token が URL クエリにある場合 → Cookie に保存してリダイレクト
            params = parse_qs(parsed.query)
            token_from_query = params.get("token", [None])[0]
            if token_from_query:
                if token_from_query != expected:
                    return Response("Unauthorized", status=401)
                # Cookie に保存してトークンなし URL にリダイレクト
                redirect_url = parsed.scheme + "://" + parsed.netloc + path
                return Response(
                    None,
                    status=302,
                    headers={
                        "Location": redirect_url,
                        "Set-Cookie": f"rmjournal_token={token_from_query}; Path=/view/; HttpOnly; Secure; SameSite=Strict",
                    },
                )

            # 2. Cookie からトークンを取得
            cookie_header = request.headers.get("Cookie") or ""
            token_from_cookie = None
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith("rmjournal_token="):
                    token_from_cookie = part[len("rmjournal_token=") :]
                    break

            if not token_from_cookie or token_from_cookie != expected:
                return Response(
                    "Unauthorized - add ?token=<your_token> to the URL", status=401
                )

            # Map /view/<r2_key> → R2 key
            r2_key = path[len("/view/") :]
            if not r2_key or r2_key.endswith("/"):
                r2_key = r2_key + "index.html"

            storage = R2StorageProvider(self.env.R2_BUCKET)
            data = await storage.get(r2_key)
            if data is None:
                return Response("Not Found", status=404)

            ct = _content_type(r2_key)
            # Text formats: decode to string and return directly
            if ct.startswith("text/") or ct in ("image/svg+xml", "application/json"):
                return Response(data.decode("utf-8"), headers={"Content-Type": ct})
            # Binary formats: convert to JS Uint8Array
            from js import Uint8Array

            return Response(Uint8Array.new(data), headers={"Content-Type": ct})

        return Response("Not Found", status=404)
