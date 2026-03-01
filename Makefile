PYTHON = uv run python
SRC_DIR = src
OUTPUT_DIR = output

.PHONY: help sync index clean

help:
	@echo "Usage:"
	@echo "  make sync DATE=YYYY-MM-DD  - Process journal for a specific date"
	@echo "  make sync-today            - Process journal for today"
	@echo "  make index                 - Regenerate the archive index page"
	@echo "  make clean                 - Remove the output directory"

sync:
	@if [ -z "$(DATE)" ]; then \
		echo "Error: DATE is required. Usage: make sync DATE=YYYY-MM-DD"; \
		exit 1; \
	fi
	export PYTHONPATH=$${PYTHONPATH}:. && $(PYTHON) -m $(SRC_DIR).journal.cli --date $(DATE)

sync-today:
	export PYTHONPATH=$${PYTHONPATH}:. && $(PYTHON) -m $(SRC_DIR).journal.cli

index:
	export PYTHONPATH=$${PYTHONPATH}:. && $(PYTHON) -c "from src.storage.local import LocalStorageProvider; from src.journal.web import generate_index_page; storage = LocalStorageProvider('$(OUTPUT_DIR)'); generate_index_page(storage)"

clean:
	rm -rf $(OUTPUT_DIR)
