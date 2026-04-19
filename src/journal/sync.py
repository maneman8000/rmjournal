import logging
import json
from datetime import date, datetime
from typing import List, Dict, Any
import httpx
from cloud.models import BlobDoc, Entry
from renderer.svg import rm_content_to_svg
from renderer.canvas import PAPER_PRO
from exporter import export_svg_to_storage
from journal.cli import JournalContext
from journal.web import generate_daily_page

_logger = logging.getLogger(__name__)


def ms_to_date(ms_str: str) -> date:
    """Convert millisecond string to date."""
    try:
        return datetime.fromtimestamp(int(ms_str) / 1000.0).date()
    except (ValueError, TypeError):
        return date(1970, 1, 1)


async def process_journal(ctx: JournalContext) -> bool:
    """
    Main processing loop.

    On subrequest limit errors, saves progress for completed docs
    and exits gracefully. Remaining docs will be processed on next run.

    If ctx.render_queue is set, SVG rendering is offloaded to the Queue Consumer.
    In that case, .rm files are saved to R2 tmp/ and a single Queue message is sent.
    generate_daily_page() and generate_index_page() will be called by the Queue Consumer
    after rendering completes.

    Returns True if a Queue message was successfully sent (caller should NOT call
    generate_index_page()), False otherwise (caller should call generate_index_page()).
    """
    _logger.info("Fetching document list...")
    all_docs = await ctx.client.list_docs()

    target_docs = [
        doc
        for doc in all_docs
        if not doc.is_directory
        and doc.metadata
        and ms_to_date(doc.metadata.last_modified) == ctx.target_date
    ]

    _logger.info(f"Found {len(target_docs)} documents modified on {ctx.target_date}")

    doc_titles = {}
    # Collect tmp_keys / image_keys for Queue rendering
    tmp_keys: List[str] = []
    image_keys: List[str] = []

    for doc in target_docs:
        _logger.info(f"Processing document: {doc.visible_name} ({doc.id})")

        try:
            full_doc = await ctx.client.get_doc(doc.id)
            if not full_doc:
                _logger.warning(f"Could not fetch full doc info for {doc.id}")
                continue

            if ctx.render_queue is not None:
                # Queue mode: save .rm files to R2 tmp/ and collect keys
                processed_pages = await _collect_pages_for_queue(
                    ctx, full_doc, tmp_keys, image_keys
                )
            else:
                # Inline mode: render SVG immediately (local / fallback)
                processed_pages = await process_document_pages(ctx, full_doc)

            for page_id in processed_pages:
                filename = f"{doc.id}_{page_id}.svg"
                doc_titles[filename] = doc.visible_name

        except httpx.TransportError as e:
            _logger.warning(
                f"Network error during process_journal ({e}), "
                f"saving progress and stopping. Remaining docs deferred to next run."
            )
            break

    if doc_titles:
        date_prefix = ctx.target_date.strftime("%Y/%m/%d")
        meta_key = f"{date_prefix}/metadata.json"

        # 既存の metadata.json とマージ（複数回実行時にタイトルが失われないように）
        existing_meta = {}
        if await ctx.storage.exists(meta_key):
            try:
                existing_meta = json.loads(
                    (await ctx.storage.get(meta_key)).decode("utf-8")
                )
            except Exception as e:
                _logger.warning(f"Failed to load existing metadata for merge: {e}")

        merged_titles = {**existing_meta, **doc_titles}
        await ctx.storage.put(
            meta_key,
            json.dumps(merged_titles, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )

    if ctx.render_queue is not None:
        if tmp_keys:
            # Queue mode: send a single message with all .rm keys
            try:
                from pyodide.ffi import to_js
                from js import Object
                msg = to_js(
                    {
                        "target_date": str(ctx.target_date),
                        "tmp_keys": tmp_keys,
                        "image_keys": image_keys,
                    },
                    dict_converter=Object.fromEntries,
                )
                await ctx.render_queue.send(msg)
                _logger.info(
                    f"[queue] Sent {len(tmp_keys)} pages to render queue for {ctx.target_date}"
                )
                # Queue Consumer が generate_daily_page / generate_index_page を実行する
                return True
            except Exception as e:
                _logger.error(f"[queue] Failed to send render queue message: {e}")
                # Fallback: render inline
                _logger.warning("[queue] Falling back to inline rendering")
                for tmp_key, image_key in zip(tmp_keys, image_keys):
                    try:
                        rm_content = await ctx.storage.get(tmp_key)
                        if rm_content:
                            svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
                            await export_svg_to_storage(svg_data, ctx.storage, image_key)
                        await ctx.storage.delete(tmp_key)
                    except Exception as ex:
                        _logger.error(f"[queue] Fallback render failed for {tmp_key}: {ex}")
                await generate_daily_page(ctx.target_date, ctx.storage)
                await _update_dates_index(ctx.target_date, ctx.storage)
                return False
        else:
            # No pages to render today: generate daily page inline and update dates
            await generate_daily_page(ctx.target_date, ctx.storage)
            await _update_dates_index(ctx.target_date, ctx.storage)
            return False
    else:
        # Inline mode: generate daily page and update dates immediately
        await generate_daily_page(ctx.target_date, ctx.storage)
        await _update_dates_index(ctx.target_date, ctx.storage)
        return False


async def _collect_pages_for_queue(
    ctx: JournalContext,
    doc: BlobDoc,
    tmp_keys: List[str],
    image_keys: List[str],
) -> List[str]:
    """
    Fetch .rm files for target-date pages and save them to R2 tmp/.
    Appends to tmp_keys and image_keys lists (shared across documents).
    Returns list of page_ids that were collected.
    """
    content_entry = next((e for e in doc.entries if e.id.endswith(".content")), None)
    if not content_entry:
        _logger.warning(f"  No .content found for document {doc.id}")
        return []

    try:
        content_bytes = await ctx.client.get_blob(content_entry.hash)
    except httpx.TransportError as e:
        _logger.warning(f"  Network error fetching .content for {doc.id}: {e}")
        return []

    if not content_bytes:
        return []

    try:
        content_json = json.loads(content_bytes.decode("utf-8"))
        pages = content_json.get("cPages", {}).get("pages", [])
        if not pages:
            pages = content_json.get("pages", [])
    except Exception as e:
        _logger.error(f"  Failed to parse .content for {doc.id}: {e}")
        return []

    entry_map = {e.id: e.hash for e in doc.entries if e.type == "0"}
    collected = []

    for page in pages:
        page_id = page.get("id")
        last_mod = page.get("modifed") or page.get("lastModified", "0")

        if not page_id or ms_to_date(last_mod) != ctx.target_date:
            continue

        rm_id = f"{doc.id}/{page_id}.rm"
        rm_hash = entry_map.get(rm_id) or entry_map.get(f"{page_id}.rm")
        if not rm_hash:
            _logger.warning(f"    Could not find .rm hash for page {page_id}")
            continue

        try:
            rm_content = await ctx.client.get_blob(rm_hash)
        except httpx.TransportError as e:
            _logger.warning(f"    Network error fetching .rm for page {page_id}: {e}")
            break

        if not rm_content:
            continue

        date_prefix = ctx.target_date.strftime("%Y/%m/%d")
        tmp_key = f"tmp/render/{doc.id}_{page_id}.rm"
        image_key = f"{date_prefix}/images/{doc.id}_{page_id}.svg"

        try:
            await ctx.storage.put(tmp_key, rm_content)
            tmp_keys.append(tmp_key)
            image_keys.append(image_key)
            collected.append(page_id)
            _logger.info(f"    Saved tmp: {tmp_key}")
        except Exception as e:
            _logger.error(f"    Failed to save tmp file for page {page_id}: {e}")

    return collected


async def process_document_pages(ctx: JournalContext, doc: BlobDoc):
    """
    Inspect pages and render those modified on the target date.
    Uses .content blob for metadata and ordering.

    On subrequest limit errors, returns pages processed so far.
    This is used in inline (non-Queue) mode.
    """
    # 1. Find .content entry
    content_entry = next((e for e in doc.entries if e.id.endswith(".content")), None)
    if not content_entry:
        _logger.warning(f"  No .content found for document {doc.id}")
        return []

    # 2. Fetch and parse .content
    try:
        content_bytes = await ctx.client.get_blob(content_entry.hash)
    except httpx.TransportError as e:
        _logger.warning(f"  Network error fetching .content for {doc.id}: {e}")
        return []

    if not content_bytes:
        _logger.warning(f"  Failed to fetch .content for {doc.id}")
        return []

    try:
        content_json = json.loads(content_bytes.decode("utf-8"))
        # Sync v1.5 (format v2) structure
        pages = content_json.get("cPages", {}).get("pages", [])
        if not pages:
            pages = content_json.get("pages", [])
    except Exception as e:
        _logger.error(f"  Failed to parse .content for {doc.id}: {e}")
        return []

    # 3. Build a map of entry ID -> hash for quick lookup
    entry_map = {e.id: e.hash for e in doc.entries if e.type == "0"}

    processed_pages = []

    for page in pages:
        page_id = page.get("id")
        # Note the misspelling 'modifed' in reMarkable format
        last_mod = page.get("modifed") or page.get("lastModified", "0")

        if not page_id:
            continue

        _logger.debug(
            f"    Page {page_id} last_mod: {last_mod} (Parsed: {ms_to_date(last_mod)})"
        )

        if ms_to_date(last_mod) != ctx.target_date:
            continue

        # 4. Target date match! Download and render
        _logger.info(f"  Rendering page {page_id} (Modified: {ms_to_date(last_mod)})")

        # In Sync v1.5, the .rm file is at <doc_id>/<page_id>.rm
        rm_id = f"{doc.id}/{page_id}.rm"
        rm_hash = entry_map.get(rm_id) or entry_map.get(f"{page_id}.rm")

        if not rm_hash:
            _logger.warning(f"    Could not find .rm hash for page {page_id}")
            continue

        try:
            rm_content = await ctx.client.get_blob(rm_hash)
        except httpx.TransportError as e:
            _logger.warning(
                f"    Network error fetching .rm for page {page_id}: {e}, "
                f"returning {len(processed_pages)} pages processed so far"
            )
            break

        if not rm_content:
            _logger.warning(f"    Failed to fetch .rm content for page {page_id}")
            continue

        try:
            # Render to SVG string using Paper Pro dimensions
            svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)

            # Save to storage: {YYYY}/{MM}/{DD}/images/<doc_id>_<page_id>.svg
            date_prefix = ctx.target_date.strftime("%Y/%m/%d")
            image_key = f"{date_prefix}/images/{doc.id}_{page_id}.svg"

            await export_svg_to_storage(svg_data, ctx.storage, image_key)
            _logger.info(f"    Saved to {image_key}")
            processed_pages.append(page_id)

        except httpx.TransportError as e:
            _logger.warning(
                f"    Network error saving page {page_id}: {e}, "
                f"returning {len(processed_pages)} pages processed so far"
            )
            break
        except Exception as e:
            _logger.error(f"    Failed to render page {page_id}: {e}")

    return processed_pages


async def _update_dates_index(target_date: date, storage):
    """
    Update dates.json in R2 with the target date.
    dates.json holds a sorted (newest first) list of all dated journal entries.
    """
    date_str = target_date.strftime("%Y/%m/%d")

    # Check if the daily page was actually generated
    daily_key = f"{date_str}/index.html"
    if not await storage.exists(daily_key):
        _logger.info(f"No daily page for {date_str}, skipping dates.json update")
        return

    # Load existing dates
    existing: list = []
    if await storage.exists("dates.json"):
        try:
            existing = json.loads((await storage.get("dates.json")).decode("utf-8"))
        except Exception as e:
            _logger.warning(f"Failed to load dates.json: {e}")

    if date_str in existing:
        return  # Already listed, no update needed

    updated = sorted(set(existing + [date_str]), reverse=True)
    await storage.put(
        "dates.json",
        json.dumps(updated, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )
    _logger.info(f"Updated dates.json: added {date_str} ({len(updated)} total)")
