"""
Stale document notification for rmjournal.

Checks all reMarkable documents and sends a Slack/Discord webhook
notification for any document that has not been modified for 47–50 days
(i.e. 3 days or fewer remaining before the 50-day mark).
"""

import logging
from datetime import date
from typing import List, Tuple

import httpx

from cloud.client import RemarkableClient
from journal.sync import ms_to_date

_logger = logging.getLogger(__name__)

# Warn when (50 - days_since_last_edit) <= WARN_DAYS_BEFORE
STALE_DAYS = 50
WARN_DAYS_BEFORE = 3


def _days_since(last_modified_ms: str, today: date) -> int:
    """Return the number of days since a millisecond-epoch timestamp."""
    last_date = ms_to_date(last_modified_ms)
    return (today - last_date).days


def _find_stale_docs(
    client_docs, today: date
) -> List[Tuple[str, date, int]]:
    """
    Return list of (visible_name, last_modified_date, days_remaining)
    for documents approaching the STALE_DAYS threshold.
    """
    results = []
    for doc in client_docs:
        if doc.is_directory or not doc.metadata:
            continue
        days_elapsed = _days_since(doc.metadata.last_modified, today)
        days_remaining = STALE_DAYS - days_elapsed
        if 0 <= days_remaining <= WARN_DAYS_BEFORE:
            last_date = ms_to_date(doc.metadata.last_modified)
            results.append((doc.visible_name, last_date, days_remaining))

    # Sort: most urgent (fewest days remaining) first
    results.sort(key=lambda x: x[2])
    return results


def _build_message(stale: List[Tuple[str, date, int]], today: date) -> str:
    """Build a plain-text Slack/Discord webhook message."""
    lines = [
        f"*rmjournal 未更新警告* — {today}",
        "",
        "以下のノートが更新されないまま {days}日 に近づいています:".format(
            days=STALE_DAYS
        ),
        "",
    ]
    for name, last_date, days_remaining in stale:
        if days_remaining == 0:
            urgency = "本日が期限"
        elif days_remaining == 1:
            urgency = "残り 1日"
        else:
            urgency = f"残り {days_remaining}日"
        lines.append(f"• *{name}* — 最終更新: {last_date}（{urgency}）")
    return "\n".join(lines)


async def check_and_notify(
    client: RemarkableClient,
    webhook_url: str,
    today: date | None = None,
) -> int:
    """
    Fetch all documents, find stale ones, and POST to the webhook if any found.

    Returns the number of stale documents found (0 means no notification sent).
    """
    if today is None:
        today = date.today()

    _logger.info("[notify] Fetching document list for stale check...")
    all_docs = await client.list_docs()

    stale = _find_stale_docs(all_docs, today)
    _logger.info(f"[notify] Found {len(stale)} stale document(s) out of {len(all_docs)}")

    if not stale:
        return 0

    message = _build_message(stale, today)
    payload = {"text": message}

    _logger.info(f"[notify] Sending webhook notification for {len(stale)} document(s)")
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(webhook_url, json=payload, timeout=10)
        if response.status_code == 200:
            _logger.info("[notify] Webhook notification sent successfully")
        else:
            _logger.warning(
                f"[notify] Webhook responded with status {response.status_code}: {response.text}"
            )
    except httpx.TransportError as e:
        _logger.error(f"[notify] Failed to send webhook notification: {e}")

    return len(stale)
