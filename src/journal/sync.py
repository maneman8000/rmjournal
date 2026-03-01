import logging
import json
from datetime import date, datetime
from typing import List, Dict, Any
from ..cloud.models import BlobDoc, Entry
from ..renderer.svg import rm_content_to_svg
from ..renderer.canvas import PAPER_PRO
from ..exporter import export_svg_to_storage
from .cli import JournalContext
from .web import generate_daily_page

_logger = logging.getLogger(__name__)

def ms_to_date(ms_str: str) -> date:
    """Convert millisecond string to date."""
    try:
        return datetime.fromtimestamp(int(ms_str) / 1000.0).date()
    except (ValueError, TypeError):
        return date(1970, 1, 1)

def process_journal(ctx: JournalContext):
    """
    Main processing loop for Step 4.
    """
    _logger.info("Fetching document list...")
    all_docs = ctx.client.list_docs()
    
    target_docs = [
        doc for doc in all_docs 
        if not doc.is_directory and doc.metadata and ms_to_date(doc.metadata.last_modified) == ctx.target_date
    ]
    
    _logger.info(f"Found {len(target_docs)} documents modified on {ctx.target_date}")
    
    doc_titles = {}
    
    for doc in target_docs:
        _logger.info(f"Processing document: {doc.visible_name} ({doc.id})")
        
        full_doc = ctx.client.get_doc(doc.id)
        if not full_doc:
            _logger.warning(f"Could not fetch full doc info for {doc.id}")
            continue
            
        processed_pages = process_document_pages(ctx, full_doc)
        for page_id in processed_pages:
            filename = f"{doc.id}_{page_id}.svg"
            doc_titles[filename] = doc.visible_name

    if doc_titles:
        date_prefix = ctx.target_date.strftime("%Y/%m/%d")
        meta_key = f"{date_prefix}/metadata.json"
        ctx.storage.put(meta_key, json.dumps(doc_titles, ensure_ascii=False).encode("utf-8"), content_type="application/json")

    # 5. Generate Daily HTML Page
    generate_daily_page(ctx.target_date, ctx.storage)

def process_document_pages(ctx: JournalContext, doc: BlobDoc):
    """
    Inspect pages and render those modified on the target date.
    Uses .content blob for metadata and ordering.
    """
    # 1. Find .content entry
    content_entry = next((e for e in doc.entries if e.id.endswith(".content")), None)
    if not content_entry:
        _logger.warning(f"  No .content found for document {doc.id}")
        return []

    # 2. Fetch and parse .content
    content_bytes = ctx.client.get_blob(content_entry.hash)
    if not content_bytes:
        _logger.warning(f"  Failed to fetch .content for {doc.id}")
        return
        
    try:
        content_json = json.loads(content_bytes.decode("utf-8"))
        # Sync v1.5 (format v2) structure
        pages = content_json.get("cPages", {}).get("pages", [])
        if not pages:
            # Fallback for old/other format?
            pages = content_json.get("pages", [])
    except Exception as e:
        _logger.error(f"  Failed to parse .content for {doc.id}: {e}")
        return

    # 3. Build a map of entry ID -> hash for quick lookup
    entry_map = {e.id: e.hash for e in doc.entries if e.type == "0"}

    processed_pages = []

    for page in pages:
        page_id = page.get("id")
        # Note the misspelling 'modifed' in reMarkable format
        last_mod = page.get("modifed") or page.get("lastModified", "0")
        
        if not page_id:
            continue
            
        _logger.debug(f"    Page {page_id} last_mod: {last_mod} (Parsed: {ms_to_date(last_mod)})")
        
        if ms_to_date(last_mod) != ctx.target_date:
            continue

        # 4. Target date match! Download and render
        _logger.info(f"  Rendering page {page_id} (Modified: {ms_to_date(last_mod)})")
        
        # In Sync v1.5, the .rm file is at <doc_id>/<page_id>.rm
        rm_id = f"{doc.id}/{page_id}.rm"
        rm_hash = entry_map.get(rm_id)
        
        if not rm_hash:
            # Try without doc_id prefix just in case
            rm_hash = entry_map.get(f"{page_id}.rm")

        if not rm_hash:
            _logger.warning(f"    Could not find .rm hash for page {page_id}")
            continue
            
        rm_content = ctx.client.get_blob(rm_hash)
        if not rm_content:
            _logger.warning(f"    Failed to fetch .rm content for page {page_id}")
            continue
            
        try:
            # Render to SVG string using Paper Pro dimensions
            svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
            
            # Save to storage
            # Hierarchy: {YYYY}/{MM}/{DD}/images/<doc_id>_<page_id>.svg
            date_prefix = ctx.target_date.strftime("%Y/%m/%d")
            image_key = f"{date_prefix}/images/{doc.id}_{page_id}.svg"
            
            export_svg_to_storage(svg_data, ctx.storage, image_key)
            _logger.info(f"    Saved to {image_key}")
            processed_pages.append(page_id)
            
        except Exception as e:
            _logger.error(f"    Failed to render page {page_id}: {e}")

    return processed_pages
