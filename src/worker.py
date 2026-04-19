"""
rmjournal Worker entrypoint for Cloudflare Workers (Python Workers / Pyodide).

Handles:
  - scheduled(): Cron Trigger — daily sync from reMarkable Cloud → R2
  - fetch(): HTTP handler
      POST /trigger        — manual sync trigger
      GET  /view/<path>    — R2 content viewer (token auth required)
"""

import logging
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs

# rmscene は新しいフォーマットのファイルに対して warning を出すことがあるが
# 処理は継続されるため、error/warning としてログに出ないよう抑制する
logging.getLogger("rmscene").setLevel(logging.ERROR)

from workers import WorkerEntrypoint, Response

from cloud.auth import AuthManager
from cloud.client import RemarkableClient
from cloud.cache import KVMetadataCache
from storage.r2 import R2StorageProvider
from journal.cli import JournalContext
from journal.sync import process_journal
from journal.web import generate_index_page, generate_archive_pages

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


def _get_cookie_token(request) -> Optional[str]:
    """Cookie から rmjournal_token を取得して返す。なければ None。"""
    cookie_header = request.headers.get("Cookie") or ""
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("rmjournal_token="):
            return part[len("rmjournal_token=") :]
    return None


class Default(WorkerEntrypoint):
    async def _run_sync(self, target_date: date):
        """共通の同期処理。Cron と /trigger の両方から呼ばれる。"""
        _logger.info(f"[sync] Starting journal sync for {target_date}")

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

        _logger.info(f"[sync] Journal sync complete for {target_date}")

        # TEST: Queue 動作検証用（確認後に削除）
        # /trigger 実行後に Queue にメッセージを送り、Consumer が R2 に書き込めるか確認する
        try:
            from pyodide.ffi import to_js
            from js import Object
            msg = to_js(
                {
                    "test": True,
                    "target_date": str(target_date),
                    "triggered_at": datetime.utcnow().isoformat(),
                },
                dict_converter=Object.fromEntries,
            )
            await self.env.RENDER_QUEUE.send(msg)
            _logger.info("[queue-test] Message sent to RENDER_QUEUE")
        except Exception as e:
            _logger.warning(f"[queue-test] Failed to send to queue: {e}")

    async def _run_archive(self):
        """Archive Cron handler: generate paginated archive index pages."""
        _logger.info("[archive] Starting archive page generation")
        storage = R2StorageProvider(self.env.R2_BUCKET)
        await generate_archive_pages(storage)
        _logger.info("[archive] Archive page generation complete")

    # TEST: Queue Consumer ハンドラ（動作確認後に削除）
    # Queue から受け取ったメッセージを R2 の tmp/ に書き込む
    async def queue(self, batch, env):
        """Queue Consumer test handler."""
        storage = R2StorageProvider(self.env.R2_BUCKET)
        for message in batch.messages:
            try:
                body = message.body
                # body は JsProxy の可能性があるため to_py で Python dict に変換
                try:
                    from pyodide.ffi import to_py
                    body = to_py(body)
                except Exception:
                    pass
                triggered_at = str(body.get("triggered_at", "unknown")) if isinstance(body, dict) else "unknown"
                target_date = str(body.get("target_date", "unknown")) if isinstance(body, dict) else "unknown"
                key = f"tmp/queue-test/{triggered_at}.txt"
                content = (
                    f"Queue consumer executed successfully\n"
                    f"target_date={target_date}\n"
                    f"triggered_at={triggered_at}\n"
                )
                await storage.put(key, content.encode("utf-8"), content_type="text/plain")
                _logger.info(f"[queue-test] Wrote {key}")
                message.ack()
            except Exception as e:
                _logger.error(f"[queue-test] Failed to process message: {e}")
                message.retry()

    async def scheduled(self, controller, env=None, ctx=None):
        """Cron Trigger handler. Dispatches to sync or archive based on cron schedule."""
        cron = getattr(controller, "cron", "") if controller else ""
        if cron == "0 15 * * *":
            await self._run_archive()
        else:
            await self._run_sync(date.today())

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
            token = _get_cookie_token(request)
            if not token or token != str(self.env.VIEW_TOKEN):
                return Response("Unauthorized", status=401)

            # オプションで日付を指定できる: ?date=2026-03-29
            params = parse_qs(parsed.query)
            date_str = params.get("date", [None])[0]
            if date_str:
                try:
                    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    return Response(
                        f"Invalid date format: '{date_str}'. Use YYYY-MM-DD.",
                        status=400,
                    )
            else:
                target_date = date.today()

            await self._run_sync(target_date)
            return Response(f"OK: synced {target_date}", status=200)

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
                        "Set-Cookie": f"rmjournal_token={token_from_query}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=34560000",
                    },
                )

            # 2. Cookie からトークンを取得
            token_from_cookie = _get_cookie_token(request)

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
