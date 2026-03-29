import string
import logging
from rmscene import SceneTree, read_tree
from rmscene import scene_items as si
from renderer.canvas import CanvasDim, RM1_2

_logger = logging.getLogger(__name__)

SVG_HEADER = string.Template("""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" height="$height" width="$width" viewBox="$viewbox">
""")

import io


def rm_to_svg(rm_path, svg_path, dim: CanvasDim = RM1_2):
    """Convert `rm_path` to SVG at `svg_path`."""
    with open(rm_path, "rb") as infile:
        svg_str = rm_content_to_svg(infile.read(), dim)
        with open(svg_path, "wt") as outfile:
            outfile.write(svg_str)


def rm_content_to_svg(rm_content: bytes, dim: CanvasDim = RM1_2) -> str:
    """Convert .rm bytes to SVG string."""
    infile = io.BytesIO(rm_content)
    tree = read_tree(infile)
    output = io.StringIO()
    tree_to_svg(tree, output, dim)
    return output.getvalue()


def tree_to_svg(tree: SceneTree, output, dim: CanvasDim):
    # Scale calculation (standard DPI is 226)
    scale_factor = 72.0 / dim.dpi

    width_pt = dim.width * scale_factor
    height_pt = dim.height * scale_factor

    # Paper Pro might need different offsets, but for now let's use standard rM centering
    # rM usually centers the 1404 width around 0
    viewbox = f"{-width_pt / 2} 0 {width_pt} {height_pt}"

    output.write(
        SVG_HEADER.substitute(width=width_pt, height=height_pt, viewbox=viewbox)
    )

    # Group for the whole page
    output.write('\t<g id="page1" style="display:inline">\n')

    # Recursively draw the tree
    draw_group(tree.root, output, scale_factor)

    output.write("\t</g>\n")
    output.write("</svg>\n")


def draw_group(group: si.Group, output, scale):
    # rM groups often have anchors, but let's stick to basics for now
    # simplified from rmc logic
    output.write(f'\t\t<g id="{group.node_id}">\n')
    for child in group.children.values():
        if isinstance(child, si.Group):
            draw_group(child, output, scale)
        elif isinstance(child, si.Line):
            draw_stroke(child, output, scale)
    output.write(f"\t\t</g>\n")


def draw_stroke(line: si.Line, output, scale):
    # Simplified rendering: polyline for the whole stroke
    # In a full-featured renderer, we'd handle different pens

    # Basic color mapping (rmscene.scene_items.PenColor)
    color_map = {
        si.PenColor.BLACK: "black",
        si.PenColor.GRAY: "gray",
        si.PenColor.WHITE: "white",
        si.PenColor.YELLOW: "#fbf719",
        si.PenColor.GREEN: "#00ff00",
        si.PenColor.PINK: "#ffc0cb",
        si.PenColor.BLUE: "#4e69c9",
        si.PenColor.RED: "#b33e39",
    }

    color = color_map.get(line.color, "black")
    width = line.thickness_scale * scale

    points_str = " ".join([f"{p.x * scale:.3f},{p.y * scale:.3f}" for p in line.points])

    output.write(
        f'\t\t\t<polyline fill="none" stroke="{color}" stroke-width="{width:.3f}" '
        f'stroke-linecap="round" points="{points_str}" />\n'
    )
