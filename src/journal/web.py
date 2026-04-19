import logging
from datetime import date
from typing import List, Dict
from storage.base import StorageProvider

_logger = logging.getLogger(__name__)

DAILY_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Journal - $date</title>
    <style>
        :root {
            --bg-color: #f8f9fa;
            --text-color: #212529;
            --card-bg: #ffffff;
            --accent-color: #007bff;
        }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        header {
            width: 100%;
            max-width: 1200px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }
        h1 { margin: 0; font-size: 1.5rem; }
        .back-link { text-decoration: none; color: var(--accent-color); font-weight: 600; }
        .page-container {
            width: 100%;
            max-width: 1300px;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px;
        }
        .page-item {
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.05);
            padding: 0; /* No padding as requested */
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .page-metadata {
            font-size: 0.85rem;
            color: #6c757d;
            padding: 12px 15px;
            background: #fafafa;
            border-bottom: 1px solid #eee;
        }
        .svg-wrapper {
            width: 100%;
            height: auto;
            padding: 0;
            background: white;
        }
        svg {
            width: 100%;
            height: auto;
            display: block;
        }
    </style>
</head>
<body>
    <header>
        <a href="../../../index.html" class="back-link">← Archives</a>
        <h1>$date_formatted</h1>
        <div style="width: 80px;"></div> <!-- spacer -->
    </header>
    <div class="page-container">
        $content
    </div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>reMarkable Journal Archives</title>
    <style>
        :root {
            --bg-color: #f8f9fa;
            --text-color: #212529;
            --accent-color: #007bff;
        }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 40px 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .header-row {
            display: flex;
            align-items: center;
            gap: 20px;
            margin-bottom: 40px;
            width: 100%;
            max-width: 800px;
        }
        .header-row h1 { margin: 0; font-weight: 800; letter-spacing: -0.02em; flex-grow: 1; }
        #sync-btn {
            padding: 10px 20px;
            background: var(--accent-color);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.2s;
            white-space: nowrap;
        }
        #sync-btn:hover { opacity: 0.85; }
        #sync-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .archive-list {
            width: 100%;
            max-width: 800px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .date-item {
            background: white;
            padding: 20px 24px;
            border-radius: 16px;
            text-decoration: none;
            color: inherit;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 15px rgba(0,0,0,0.05);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .date-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.1);
        }
        .date-info {
            display: flex;
            flex-direction: column;
            gap: 10px;
            flex-grow: 1;
        }
        .date-text { font-size: 1.25rem; font-weight: 700; color: #1a1a1a; }
        .thumbnails {
            display: flex;
            gap: 8px;
            overflow: hidden;
        }
        .thumb {
            width: 120px;
            height: 80px;
            background: #f8f9fa;
            border-radius: 6px;
            overflow: hidden;
            border: 1px solid #e9ecef;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
        }
        .thumb img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: top left;
        }
        .arrow { color: #adb5bd; font-size: 1.5rem; margin-left: 10px; flex-shrink: 0; }
        .pagination {
            width: 100%;
            max-width: 800px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 30px;
            padding: 0 4px;
        }
        .pagination a {
            color: var(--accent-color);
            text-decoration: none;
            font-weight: 600;
            padding: 10px 18px;
            border-radius: 8px;
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            transition: box-shadow 0.2s;
        }
        .pagination a:hover { box-shadow: 0 4px 15px rgba(0,0,0,0.12); }
        .pagination .spacer { flex: 1; }
    </style>
</head>
<body>
    <div class="header-row">
        <h1>Journal Archives</h1>
        $sync_button
    </div>
    <div class="archive-list">
        $items
    </div>
    <div class="pagination">
        $pagination
    </div>
    $scripts
</body>
</html>
"""

SYNC_BUTTON_HTML = """<button id="sync-btn" onclick="triggerSync()">今すぐ同期</button>"""

SYNC_SCRIPT_HTML = """<script>
    async function triggerSync() {
        const btn = document.getElementById('sync-btn');
        btn.disabled = true;
        btn.textContent = '同期中...';
        try {
            const res = await fetch('/trigger', { method: 'POST' });
            if (res.ok) {
                btn.textContent = '完了！';
                setTimeout(() => location.reload(), 1500);
            } else if (res.status === 401) {
                btn.textContent = '認証エラー（再ログインしてください）';
                btn.disabled = false;
            } else {
                btn.textContent = 'エラー(' + res.status + ')';
                btn.disabled = false;
            }
        } catch (e) {
            btn.textContent = 'エラー';
            btn.disabled = false;
        }
    }
    </script>"""


def _build_date_item_html(d_path: str, image_keys: list) -> str:
    """Build HTML for a single date entry with <img> thumbnails."""
    d_display = d_path.replace("/", "-")
    thumbnails_html = ""
    for img_key in image_keys[:4]:
        img_src = f"/view/{img_key}"
        thumbnails_html += f'<div class="thumb"><img src="{img_src}" loading="lazy" alt=""></div>'

    return f"""
        <a href="/view/{d_path}/index.html" class="date-item">
            <div class="date-info">
                <span class="date-text">{d_display}</span>
                <div class="thumbnails">{thumbnails_html}</div>
            </div>
            <span class="arrow">→</span>
        </a>
        """


def _build_index_html(
    items_html: str,
    pagination_html: str,
    show_sync_button: bool = False,
) -> str:
    """Render INDEX_TEMPLATE with given items and pagination."""
    sync_button = SYNC_BUTTON_HTML if show_sync_button else ""
    scripts = SYNC_SCRIPT_HTML if show_sync_button else ""
    html = INDEX_TEMPLATE.replace("$sync_button", sync_button)
    html = html.replace("$items", items_html)
    html = html.replace("$pagination", pagination_html)
    html = html.replace("$scripts", scripts)
    return html


import re
import json


async def generate_daily_page(target_date: date, storage: StorageProvider):
    """
    Generate an index.html for a specific date containing all journal images.
    """
    date_path = target_date.strftime("%Y/%m/%d")

    # Load metadata.json for titles
    metadata = {}
    if await storage.exists(f"{date_path}/metadata.json"):
        try:
            metadata = json.loads(
                (await storage.get(f"{date_path}/metadata.json")).decode("utf-8")
            )
        except Exception as e:
            _logger.warning(f"Failed to load metadata: {e}")

    # List all SVGs in the images directory
    image_dir = f"{date_path}/images"
    all_files = await storage.list(image_dir)
    svg_keys = sorted([k for k in all_files if k.endswith(".svg")])

    if not svg_keys:
        _logger.info(f"No rendered images found for {target_date} in {image_dir}")
        return

    content_html = ""
    for s_key in svg_keys:
        svg_content = (await storage.get(s_key)).decode("utf-8")
        # Remove XML declaration and DOCTYPE if present for cleaner embedding
        if "<?xml" in svg_content:
            svg_content = svg_content[svg_content.find(">") + 1 :].strip()
        if "<!DOCTYPE" in svg_content:
            svg_content = svg_content[svg_content.find(">") + 1 :].strip()

        # Extract original filename to match with metadata
        filename = s_key.split("/")[-1]
        title = metadata.get(filename, filename)

        content_html += f"""
        <div class="page-item">
            <div class="page-metadata">{title}</div>
            <div class="svg-wrapper">
                {svg_content}
            </div>
        </div>
        """

    html = DAILY_TEMPLATE.replace(
        "$date_formatted", target_date.strftime("%A, %B %d, %Y")
    )
    html = html.replace("$date", target_date.isoformat())
    html = html.replace("$content", content_html)

    await storage.put(
        f"{date_path}/index.html", html.encode("utf-8"), content_type="text/html"
    )
    _logger.info(f"Generated daily page: {date_path}/index.html")


async def _load_date_paths(storage: StorageProvider) -> list:
    """
    Load the list of dated journal entries from dates.json in R2.
    Falls back to R2 full scan if dates.json is not found.
    Returns a list of date paths sorted newest-first (e.g. ["2026/04/19", ...]).
    """
    if await storage.exists("dates.json"):
        try:
            return json.loads((await storage.get("dates.json")).decode("utf-8"))
        except Exception as e:
            _logger.warning(f"Failed to load dates.json, falling back to R2 scan: {e}")

    # Fallback: full R2 scan (slow but safe)
    _logger.info("dates.json not found, scanning R2...")
    all_keys = await storage.list("")
    date_pattern = re.compile(r"^(\d{4}/\d{2}/\d{2})/index\.html$")
    return sorted(
        [m.group(1) for k in all_keys if (m := date_pattern.match(k))],
        reverse=True,
    )


async def generate_index_page(storage: StorageProvider):
    """
    Generate index.html with the latest 10 dates.
    SVG thumbnails are referenced via <img> tags (no inline embedding).
    Uses dates.json for efficient date listing instead of R2 full scan.
    """
    _logger.info("Generating index.html (latest 10 dates)...")

    date_paths = await _load_date_paths(storage)

    # Latest 10 only
    latest = date_paths[:10]

    items_html = ""
    for d_path in latest:
        image_dir = f"{d_path}/images"
        images = await storage.list(image_dir)
        svg_keys = sorted([k for k in images if k.endswith(".svg")])[:4]
        items_html += _build_date_item_html(d_path, svg_keys)

    if not items_html:
        items_html = "<p>No entries found yet.</p>"

    # Pagination: link to archive index if more than 10 dates exist
    total_pages = _calc_total_archive_pages(len(date_paths))
    if total_pages > 0:
        older_link = f'<a href="/view/index_{total_pages:04d}.html">← Older</a>'
        pagination_html = f'{older_link}<span class="spacer"></span>'
    else:
        pagination_html = ""

    html = _build_index_html(items_html, pagination_html, show_sync_button=True)
    await storage.put("index.html", html.encode("utf-8"), content_type="text/html")
    _logger.info("Generated index.html")


def _calc_total_archive_pages(total_dates: int, page_size: int = 10) -> int:
    """Calculate how many archive index pages are needed."""
    if total_dates <= page_size:
        return 0
    # All dates go into archive pages; the last page overlaps with index.html
    return (total_dates + page_size - 1) // page_size


async def generate_archive_pages(storage: StorageProvider):
    """
    Generate paginated archive index pages (index_0001.html, index_0002.html, ...).

    - Pages are numbered oldest-first (index_0001 = oldest 10 dates).
    - Existing pages are skipped (immutable once written).
    - The last page (highest number) is always regenerated as new dates may be added.
    - Uses dates.json for efficient date listing instead of R2 full scan.
    """
    _logger.info("Generating archive pages...")

    date_paths = await _load_date_paths(storage)

    if not date_paths:
        _logger.info("No dated pages found, skipping archive generation")
        return

    page_size = 10
    total_pages = _calc_total_archive_pages(len(date_paths))
    if total_pages == 0:
        _logger.info("Not enough dates for archive pages yet")
        return

    # Split into chunks: oldest first (reverse the list, then chunk)
    oldest_first = list(reversed(date_paths))
    chunks = [oldest_first[i:i + page_size] for i in range(0, len(oldest_first), page_size)]

    for page_num, chunk in enumerate(chunks, start=1):
        filename = f"index_{page_num:04d}.html"

        # Skip existing pages unless it's the last page (may have new dates added)
        is_last_page = (page_num == len(chunks))
        if not is_last_page and await storage.exists(filename):
            _logger.info(f"Skipping existing archive page: {filename}")
            continue

        # Chunk is oldest-first; display newest-first in HTML
        display_chunk = list(reversed(chunk))

        items_html = ""
        for d_path in display_chunk:
            image_dir = f"{d_path}/images"
            images = await storage.list(image_dir)
            svg_keys = sorted([k for k in images if k.endswith(".svg")])[:4]
            items_html += _build_date_item_html(d_path, svg_keys)

        # Build pagination links
        prev_link = ""
        next_link = ""

        if page_num > 1:
            prev_filename = f"index_{page_num - 1:04d}.html"
            prev_link = f'<a href="/view/{prev_filename}">← Older</a>'

        if is_last_page:
            next_link = '<a href="/view/index.html">Newer →</a>'
        else:
            next_filename = f"index_{page_num + 1:04d}.html"
            next_link = f'<a href="/view/{next_filename}">Newer →</a>'

        if prev_link and next_link:
            pagination_html = f'{prev_link}<span class="spacer"></span>{next_link}'
        elif prev_link:
            pagination_html = f'<span class="spacer"></span>{prev_link}<span class="spacer"></span>'
        else:
            pagination_html = f'<span class="spacer"></span>{next_link}'

        html = _build_index_html(items_html, pagination_html, show_sync_button=False)
        await storage.put(filename, html.encode("utf-8"), content_type="text/html")
        _logger.info(f"Generated archive page: {filename} ({len(display_chunk)} dates)")
