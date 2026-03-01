import json
from pathlib import Path
from dataclasses import dataclass

@dataclass
class CanvasDim:
    width: int
    height: int
    dpi: int = 226

# Paper Pro: 954 x 1696 (Portrait)
PAPER_PRO = CanvasDim(width=954, height=1696)
RM1_2 = CanvasDim(width=1404, height=1872)

def get_canvas_dim(content_path: str | Path | None = None) -> CanvasDim:
    """
    Determine canvas dimensions.
    Priority: .content file > Default (rM1/2)
    Note: Paper Pro might still report 1404x1872 in .content for compatibility,
    so we might need to be careful.
    """
    if content_path and Path(content_path).exists():
        try:
            with open(content_path, 'r') as f:
                data = json.load(f)
                # Check for Paper Pro specific markers or resolution
                # For now, let's trust the .content if it has specific values,
                # but allow overrides.
                w = data.get("customZoomPageWidth", 1404)
                h = data.get("customZoomPageHeight", 1872)
                return CanvasDim(width=w, height=h)
        except Exception:
            pass
    
    # Default to RM1/2 dimensions if nothing else found
    return RM1_2
