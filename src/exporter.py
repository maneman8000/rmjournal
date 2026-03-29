from .storage.base import StorageProvider


async def export_svg_to_storage(svg_data: str, storage: StorageProvider, key: str):
    """
    Save SVG data to storage.
    Currently just passes through, but allows for future optimization or conversion.
    """
    await storage.put(key, svg_data.encode("utf-8"), content_type="image/svg+xml")
