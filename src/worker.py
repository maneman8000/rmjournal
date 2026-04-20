"""
rmjournal Worker entrypoint for Cloudflare Workers (Python Workers / Pyodide).

Handles:
  - scheduled(): Cron Trigger — daily sync from reMarkable Cloud → R2
  - fetch(): HTTP handler
      POST /trigger        — manual sync trigger
      GET  /view/<path>    — R2 content viewer (token auth required)
"""

import logging
import re
from datetime import date, datetime
from typing import Optional, Set
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


def _cache_control(r2_key: str) -> str:
    """Return appropriate Cache-Control header based on file type/path."""
    # SVG images: long cache, cache-busted via ?v= when re-rendered
    if "/images/" in r2_key and r2_key.endswith(".svg"):
        return "private, max-age=86400"
    # Daily pages: short cache for today (may be updated), long cache for past dates
    m = re.match(r"(\d{4}/\d{2}/\d{2})/index\.html$", r2_key)
    if m:
        page_date = m.group(1)  # "YYYY/MM/DD"
        today = date.today().strftime("%Y/%m/%d")
        if page_date == today:
            return "private, max-age=300"   # 5分（当日は sync で更新される）
        return "private, max-age=86400"     # 24時間（過去は更新されない）
    # Main index: short cache (updated on each sync)
    if r2_key == "index.html":
        return "private, max-age=300"
    # Archive pages: long cache (updated once a day)
    if re.match(r"index_\d{4}\.html$", r2_key):
        return "private, max-age=86400"
    # Default: short cache
    return "private, max-age=300"


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

        ctx = JournalContext(
            target_date=target_date,
            storage=storage,
            client=client,
            render_queue=self.env.RENDER_QUEUE,
        )
        queued = await process_journal(ctx)
        # generate_index_page() はページが Queue に送られた場合は Queue Consumer 側で実行する
        # Queue Consumer 側で SVG が揃ってから生成することでサムネイルが最新になる
        # Queue を使わなかった場合（当日変更なし or インラインモード）はここで実行する
        if not queued:
            await generate_index_page(storage)

        _logger.info(f"[sync] Journal sync complete for {target_date}")

    async def _run_archive(self):
        """Archive Cron handler: generate paginated archive index pages."""
        _logger.info("[archive] Starting archive page generation")
        storage = R2StorageProvider(self.env.R2_BUCKET)
        await generate_archive_pages(storage)
        _logger.info("[archive] Archive page generation complete")

    async def queue(self, batch, env=None, ctx=None):
        """
        Queue Consumer: SVG レンダリングを Queue で実行する。
        Cron Worker が R2 tmp/ に保存した .rm ファイルを読み込み、
        SVG に変換して R2 に保存する。
        全ページのレンダリング完了後に generate_daily_page() と
        _update_dates_index() を呼ぶ。
        """
        from renderer.svg import rm_content_to_svg
        from renderer.canvas import PAPER_PRO
        from exporter import export_svg_to_storage
        from journal.web import generate_daily_page, generate_index_page
        from journal.sync import _update_dates_index
        from datetime import date as date_type

        storage = R2StorageProvider(self.env.R2_BUCKET)

        for message in batch.messages:
            try:
                body = message.body
                # body は JsProxy なので getattr でアクセス、リストは list() でアンラップ
                target_date_str = str(getattr(body, "target_date"))
                tmp_keys = [str(k) for k in list(getattr(body, "tmp_keys"))]
                image_keys = [str(k) for k in list(getattr(body, "image_keys"))]

                _logger.info(
                    f"[queue] Rendering {len(tmp_keys)} pages for {target_date_str}"
                )

                # 全ページをレンダリング
                rendered_at = int(datetime.utcnow().timestamp())
                rendered_image_keys: Set[str] = set()

                for tmp_key, image_key in zip(tmp_keys, image_keys):
                    rm_content = await storage.get(tmp_key)
                    if rm_content is None:
                        _logger.warning(f"[queue] tmp file not found: {tmp_key}")
                        continue

                    svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
                    await export_svg_to_storage(svg_data, storage, image_key)
                    await storage.delete(tmp_key)
                    rendered_image_keys.add(image_key)
                    _logger.info(f"[queue] Rendered and saved: {image_key}")

                # 全ページ完了後に daily page を生成し、dates.json を更新、index.html を再生成
                target_date = date_type.fromisoformat(target_date_str)
                await generate_daily_page(target_date, storage, rendered_image_keys=rendered_image_keys, rendered_at=rendered_at)
                await _update_dates_index(target_date, storage)
                await generate_index_page(storage, rendered_image_keys=rendered_image_keys, rendered_at=rendered_at)
                _logger.info(f"[queue] Generated daily page, updated dates.json, regenerated index.html for {target_date_str}")

                message.ack()
            except Exception as e:
                _logger.error(f"[queue] Failed to process message: {e}")
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
            cc = _cache_control(r2_key)
            # Text formats: decode to string and return directly
            if ct.startswith("text/") or ct in ("image/svg+xml", "application/json"):
                return Response(data.decode("utf-8"), headers={"Content-Type": ct, "Cache-Control": cc})
            # Binary formats: convert to JS Uint8Array
            from js import Uint8Array

            return Response(Uint8Array.new(data), headers={"Content-Type": ct, "Cache-Control": cc})

        return Response("Not Found", status=404)
