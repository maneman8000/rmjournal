import argparse
import logging
from datetime import datetime, date
from typing import Optional
from cloud.client import RemarkableClient
from storage.base import StorageProvider
from storage.local import LocalStorageProvider

_logger = logging.getLogger(__name__)


class JournalContext:
    """
    Execution context for the journal batch process.
    """

    def __init__(
        self, target_date: date, storage: StorageProvider, client: RemarkableClient
    ):
        self.target_date = target_date
        self.storage = storage
        self.client = client
        _logger.info(f"Journal context initialized for date: {self.target_date}")


def parse_args():
    parser = argparse.ArgumentParser(description="reMarkable Journal Batch Processor")
    parser.add_argument(
        "--date",
        type=str,
        help="Target date for processing (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Determine target date
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format: {args.date}. Use YYYY-MM-DD.")
            return
    else:
        target_date = date.today()

    print(f"Target Date: {target_date}")

    # Initialize dependencies
    storage = LocalStorageProvider("output")
    client = RemarkableClient()

    # Initialize context
    ctx = JournalContext(target_date, storage, client)

    from journal.sync import process_journal
    from journal.web import generate_index_page

    print(f"Starting sync for {target_date}...")
    process_journal(ctx)

    print("Generating archive index...")
    generate_index_page(storage)

    print("CLI processing complete.")


if __name__ == "__main__":
    main()
