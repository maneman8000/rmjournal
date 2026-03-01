import logging
from datetime import date
from typing import List, Dict
from ..storage.base import StorageProvider

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
        h1 { margin-bottom: 40px; font-weight: 800; letter-spacing: -0.02em; }
        .archive-list {
            width: 100%;
            max-width: 800px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .date-item {
            background: white;
            padding: 24px;
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
            gap: 15px;
            flex-grow: 1;
        }
        .date-text { font-size: 1.25rem; font-weight: 700; color: #1a1a1a; }
        .thumbnails {
            display: flex;
            gap: 10px;
            overflow: hidden;
            margin-top: 15px;
        }
        .thumb {
            width: 160px;
            height: 90px;
            background: white;
            border-radius: 6px;
            overflow: hidden;
            border: 1px solid #e9ecef;
            position: relative;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
        }
        .thumb svg {
            position: absolute;
            /* Scale to 40% of Paper Pro (954 -> 382) */
            width: 382px;
            height: auto;
            top: -30px; 
            left: -30px;
        }
        .arrow { color: #adb5bd; font-size: 1.5rem; margin-left: 10px; flex-shrink: 0; }
    </style>
</head>
<body>
    <h1>Journal Archives</h1>
    <div class="archive-list">
        $items
    </div>
</body>
</html>
"""

import re
import json

def generate_daily_page(target_date: date, storage: StorageProvider):
    """
    Generate an index.html for a specific date containing all journal images.
    """
    date_path = target_date.strftime("%Y/%m/%d")
    
    # Load metadata.json for titles
    metadata = {}
    if storage.exists(f"{date_path}/metadata.json"):
        try:
            metadata = json.loads(storage.get(f"{date_path}/metadata.json").decode("utf-8"))
        except Exception as e:
            _logger.warning(f"Failed to load metadata: {e}")

    # List all SVGs in the images directory
    image_dir = f"{date_path}/images"
    all_files = storage.list(image_dir)
    svg_keys = sorted([k for k in all_files if k.endswith(".svg")])
    
    if not svg_keys:
        _logger.warning(f"No rendered images found for {target_date} in {image_dir}")
        return

    content_html = ""
    for s_key in svg_keys:
        svg_content = storage.get(s_key).decode("utf-8")
        # Remove XML declaration and DOCTYPE if present for cleaner embedding
        if "<?xml" in svg_content:
            svg_content = svg_content[svg_content.find(">")+1:].strip()
        if "<!DOCTYPE" in svg_content:
            svg_content = svg_content[svg_content.find(">")+1:].strip()

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

    html = DAILY_TEMPLATE.replace("$date_formatted", target_date.strftime("%A, %B %d, %Y"))
    html = html.replace("$date", target_date.isoformat())
    html = html.replace("$content", content_html)

    storage.put(f"{date_path}/index.html", html.encode("utf-8"), content_type="text/html")
    _logger.info(f"Generated daily page: {date_path}/index.html")

def generate_index_page(storage: StorageProvider):
    """
    Generate the main index.html listing all available dates.
    """
    _logger.info("Scanning storage for journal entries...")
    
    # Find all index.html files at {YYYY}/{MM}/{DD}/index.html
    all_keys = storage.list("")
    date_pattern = re.compile(r"^(\d{4}/\d{2}/\d{2})/index\.html$")
    
    date_paths = []
    for k in all_keys:
        match = date_pattern.match(k)
        if match:
            date_paths.append(match.group(1))

    # Sort descending
    date_paths.sort(reverse=True)

    items_html = ""
    for d_path in date_paths:
        d_display = d_path.replace("/", "-")
        
        # Get thumbnails (first 4 SVGs)
        image_dir = f"{d_path}/images"
        images = storage.list(image_dir)
        svg_keys = sorted([k for k in images if k.endswith(".svg")])[:4]
        
        thumbnails_html = ""
        for s_key in svg_keys:
            try:
                svg_content = storage.get(s_key).decode("utf-8")
                # Remove XML declaration if present
                if "<?xml" in svg_content:
                    svg_content = svg_content[svg_content.find(">")+1:].strip()
                # Remove DOCTYPE if present
                if "<!DOCTYPE" in svg_content:
                    svg_content = svg_content[svg_content.find(">")+1:].strip()
                thumbnails_html += f'<div class="thumb">{svg_content}</div>'
            except Exception as e:
                _logger.warning(f"Failed to load thumbnail {s_key}: {e}")

        items_html += f"""
        <a href="{d_path}/index.html" class="date-item">
            <div class="date-info">
                <span class="date-text">{d_display}</span>
                <div class="thumbnails">
                    {thumbnails_html}
                </div>
            </div>
            <span class="arrow">→</span>
        </a>
        """

    if not items_html:
        items_html = "<p>No entries found yet.</p>"

    html = INDEX_TEMPLATE.replace("$items", items_html)
    storage.put("index.html", html.encode("utf-8"), content_type="text/html")
    _logger.info("Generated main index.html")
