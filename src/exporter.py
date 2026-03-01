import cairosvg
from pathlib import Path

from .storage.base import StorageProvider

def export_svg_to_pdf(svg_path: str | Path, pdf_path: str | Path, background_color: str = "white"):
    """Convert SVG to PDF."""
    cairosvg.svg2pdf(url=str(svg_path), write_to=str(pdf_path), background_color=background_color)

def export_svg_to_png(svg_path: str | Path, png_path: str | Path, background_color: str = "white"):
    """Convert SVG to PNG."""
    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), background_color=background_color)

def export_svg_to_storage(svg_data: str, storage: StorageProvider, key: str):
    """
    Save SVG data to storage. 
    Currently just passes through, but allows for future optimization or conversion.
    """
    storage.put(key, svg_data.encode("utf-8"), content_type="image/svg+xml")
