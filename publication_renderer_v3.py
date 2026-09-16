from __future__ import annotations

"""Publication renderer V3: interactive styles, arrow types and transparency.

Builds on the proven V2 chemistry depiction helpers and changes only final
layout/rendering.  No ORCA code is imported or modified.
"""

import io
from fractions import Fraction
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

import publication_renderer as base
import publication_renderer_v2 as v2

RENDER_DPI = 600
DEFAULT_FONT_PX = v2.DEFAULT_FONT_PX
DEFAULT_GAP_PX = v2.DEFAULT_GAP_PX
DEFAULT_ARROW_PX = v2.DEFAULT_ARROW_PX
INK = v2.INK
WHITE = v2.WHITE

split_reaction_side = v2.split_reaction_side
formula_for_display = v2.formula_for_display


def _build_tokens(reactant_pairs, product_pairs, small_as_formula: bool):
    return v2._build_tokens(reactant_pairs, product_pairs, small_as_formula)


def _transparentize(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    data = list(rgba.getdata())
    cleaned = []
    for r, g, b, a in data:
        if r > 248 and g > 248 and b > 248:
            cleaned.append((255, 255, 255, 0))
        else:
            cleaned.append((r, g, b, a))
    rgba.putdata(cleaned)
    return rgba


def _draw_arrow_png(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, style: str) -> None:
    end = x + w
    head = 16
    if style == "equilibrium":
        off = 6
        # upper arrow: left -> right
        draw.line((x, y - off, end - head, y - off), fill=INK, width=3)
        draw.polygon([(end, y-off), (end-head, y-off-6), (end-head, y-off+6)], fill=INK)
        # lower arrow: right -> left
        draw.line((x + head, y + off, end, y + off), fill=INK, width=3)
        draw.polygon([(x, y+off), (x+head, y+off-6), (x+head, y+off+6)], fill=INK)
    elif style == "reversible":
        draw.line((x + head, y, end - head, y), fill=INK, width=3)
        draw.polygon([(end, y), (end-head, y-7), (end-head, y+7)], fill=INK)
        draw.polygon([(x, y), (x+head, y-7), (x+head, y+7)], fill=INK)
    else:
        draw.line((x, y, end - head, y), fill=INK, width=3)
        draw.polygon([(end, y), (end-head, y-7), (end-head, y+7)], fill=INK)


def _svg_arrow(x: int, cy: int, w: int, style: str) -> str:
    end = x + w - 7
    head = 16
    if style == "equilibrium":
        off = 6
        return (
            f'<line x1="{x}" y1="{cy-off}" x2="{end-head}" y2="{cy-off}" stroke="#141414" stroke-width="3"/>'
            f'<path d="M {end} {cy-off} L {end-head} {cy-off-6} L {end-head} {cy-off+6} Z" fill="#141414"/>'
            f'<line x1="{x+head}" y1="{cy+off}" x2="{end}" y2="{cy+off}" stroke="#141414" stroke-width="3"/>'
            f'<path d="M {x} {cy+off} L {x+head} {cy+off-6} L {x+head} {cy+off+6} Z" fill="#141414"/>'
        )
    if style == "reversible":
        return (
            f'<line x1="{x+head}" y1="{cy}" x2="{end-head}" y2="{cy}" stroke="#141414" stroke-width="3"/>'
            f'<path d="M {end} {cy} L {end-head} {cy-7} L {end-head} {cy+7} Z" fill="#141414"/>'
            f'<path d="M {x} {cy} L {x+head} {cy-7} L {x+head} {cy+7} Z" fill="#141414"/>'
        )
    return (
        f'<line x1="{x}" y1="{cy}" x2="{end-head}" y2="{cy}" stroke="#141414" stroke-width="3"/>'
        f'<path d="M {end} {cy} L {end-head} {cy-7} L {end-head} {cy+7} Z" fill="#141414"/>'
    )


def render_reaction_png(
    reactant_pairs,
    product_pairs,
    *,
    small_as_formula: bool = True,
    arrow_top: str = "",
    arrow_bottom: str = "",
    dpi: int = RENDER_DPI,
    arrow_style: str = "forward",
    transparent_background: bool = False,
) -> bytes | None:
    tokens = _build_tokens(reactant_pairs, product_pairs, small_as_formula)
    if not tokens:
        return None

    rendered = []
    font = base._load_font(DEFAULT_FONT_PX)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    ann_w = max(base._annotation_width(arrow_top), base._annotation_width(arrow_bottom))
    arrow_w = max(DEFAULT_ARROW_PX, ann_w + 72)

    for kind, payload in tokens:
        if kind == "mol":
            image = v2._molecule_png(str(payload))
            if image is None:
                return None
            rendered.append((kind, image, image.width, image.height))
        elif kind == "condensed":
            image = v2._condensed_png(str(payload), DEFAULT_FONT_PX)
            rendered.append((kind, image, image.width, image.height))
        elif kind == "formula":
            image = base._formula_png(str(payload), DEFAULT_FONT_PX)
            rendered.append((kind, image, image.width, image.height))
        elif kind in {"op", "coef"}:
            box = probe.textbbox((0, 0), str(payload), font=font)
            rendered.append((kind, payload, max(1, box[2]-box[0]), max(1, box[3]-box[1])))
        else:
            rendered.append((kind, None, arrow_w, 30))

    margin_x = 40
    top_room = 46 if arrow_top else 0
    bottom_room = 43 if arrow_bottom else 0
    chemistry_h = max((h for _, _, _, h in rendered), default=108)
    width = sum(w for _, _, w, _ in rendered) + DEFAULT_GAP_PX * (len(rendered)-1) + 2*margin_x
    height = chemistry_h + top_room + bottom_room + 54

    mode = "RGBA" if transparent_background else "RGB"
    bg = (255,255,255,0) if transparent_background else WHITE
    canvas = Image.new(mode, (max(1,width), max(1,height)), bg)
    draw = ImageDraw.Draw(canvas)
    center_y = top_room + 27 + chemistry_h // 2
    x = margin_x

    for kind, payload, w, h in rendered:
        if kind in {"mol", "formula", "condensed"}:
            image = payload
            if transparent_background:
                image = _transparentize(image)
                canvas.alpha_composite(image, (x, center_y-image.height//2))
            else:
                canvas.paste(image, (x, center_y-image.height//2))
        elif kind == "arrow":
            _draw_arrow_png(draw, x, center_y, w, arrow_style)
            cx = x + w//2
            if arrow_top:
                base._draw_annotation(draw, cx, center_y-16, arrow_top, font_size=29)
            if arrow_bottom:
                base._draw_annotation(draw, cx, center_y+40, arrow_bottom, font_size=29)
        else:
            box = draw.textbbox((0,0), str(payload), font=font)
            draw.text((x-box[0], center_y-(box[3]-box[1])//2-box[1]), str(payload), fill=INK, font=font)
        x += w + DEFAULT_GAP_PX

    out = io.BytesIO()
    canvas.save(out, format="PNG", dpi=(dpi,dpi), compress_level=6)
    return out.getvalue()


def render_reaction_svg(
    reactant_pairs,
    product_pairs,
    *,
    small_as_formula: bool = True,
    arrow_top: str = "",
    arrow_bottom: str = "",
    arrow_style: str = "forward",
    transparent_background: bool = False,
) -> bytes | None:
    tokens = _build_tokens(reactant_pairs, product_pairs, small_as_formula)
    if not tokens:
        return None

    items = []
    ann_w = max(len(arrow_top), len(arrow_bottom)) * 15
    arrow_w = max(DEFAULT_ARROW_PX, ann_w + 72)
    for kind, payload in tokens:
        if kind == "mol":
            frag = v2._molecule_svg_fragment(str(payload))
            if frag is None:
                return None
            items.append((kind, frag, frag[1], frag[2]))
        elif kind == "condensed":
            parts = v2._split_condensed(str(payload))
            approx_w = 0
            for pk, pv in parts:
                approx_w += max(36, int(len(pv)*DEFAULT_FONT_PX*0.48+8)) if pk == "group" else max(34,int(DEFAULT_FONT_PX*0.92))+2*max(4,int(DEFAULT_FONT_PX*0.10))
            items.append((kind, payload, approx_w, 62))
        elif kind == "formula":
            items.append((kind, payload, max(68,len(str(payload))*23+18), 58))
        elif kind in {"op","coef"}:
            items.append((kind,payload,max(34,len(str(payload))*24),52))
        else:
            items.append((kind,None,arrow_w,30))

    margin = 40
    top = 54 if arrow_top else 18
    bottom = 50 if arrow_bottom else 22
    chemistry_h = max((h for _,_,_,h in items), default=108)
    width = sum(w for _,_,w,_ in items) + DEFAULT_GAP_PX*(len(items)-1) + 2*margin
    height = top + chemistry_h + bottom
    cy = top + chemistry_h//2

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    if not transparent_background:
        out.append('<rect width="100%" height="100%" fill="white"/>')
    out.append('<style>text{font-family:Arial,Helvetica,sans-serif;fill:#141414}</style>')

    x = margin
    for kind,payload,w,h in items:
        if kind == "mol":
            frag,_,_ = payload
            out.append(f'<g transform="translate({x},{cy-h//2})">{frag}</g>')
        elif kind == "condensed":
            frag,_,_ = v2._condensed_svg(str(payload), x, cy, DEFAULT_FONT_PX)
            out.append(frag)
        elif kind == "formula":
            out.append(v2._svg_formula(str(payload), x+5, cy+14))
        elif kind in {"op","coef"}:
            out.append(f'<text x="{x}" y="{cy+14}" font-size="{DEFAULT_FONT_PX}" font-weight="500">{escape(str(payload))}</text>')
        else:
            out.append(_svg_arrow(x, cy, w, arrow_style))
            cx = x+w//2
            if arrow_top:
                out.append(base._svg_annotation(arrow_top,cx,cy-16,font_size=29))
            if arrow_bottom:
                out.append(base._svg_annotation(arrow_bottom,cx,cy+37,font_size=29))
        x += w + DEFAULT_GAP_PX

    out.append('</svg>')
    return ''.join(out).encode('utf-8')


def render_reaction_scheme(reactant_pairs, product_pairs, sub_size=None, small_as_formula=True, arrow_top="", arrow_bottom="", arrow_style="forward", transparent_background=False):
    return render_reaction_png(
        reactant_pairs, product_pairs,
        small_as_formula=small_as_formula,
        arrow_top=arrow_top, arrow_bottom=arrow_bottom,
        arrow_style=arrow_style,
        transparent_background=transparent_background,
    )
