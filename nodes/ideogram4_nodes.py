"""Ideogram 4 prompt builder.

A single self-contained node with a visual bbox editor: draw regions on a blank
canvas, set each region's type/desc/text/color palette, and assemble the Ideogram 4 JSON caption prompt.
"""

import json
import os
import re
import logging

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from comfy_api.latest import io


_FONT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts", "FreeMono.ttf")


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else (255, 255, 255)


def _readable(rgb):
    # Lighten toward white if too dark, so box-colored text stays legible on the dark canvas.
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < 130:
        t = (130 - lum) / (255 - lum)
        r, g, b = round(r + (255 - r) * t), round(g + (255 - g) * t), round(b + (255 - b) * t)
    return (r, g, b)


def _font(size):
    try:
        return ImageFont.truetype(_FONT_PATH, size)
    except Exception:
        try:
            return ImageFont.load_default(size)
        except Exception:
            return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = word if not line else line + " " + word
            if line and draw.textlength(test, font=font) > max_w:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
    return lines


def _render_preview(boxes, width, height, bg=None, brightness=50):
    # Render the regions + prompts over the reference image (or a black canvas).
    if bg is not None:
        iw, ih = bg.size
        long_edge = max(iw, ih)
        scale = min(1.0, 1024 / long_edge) if long_edge > 0 else 1.0
        rw, rh = max(1, round(iw * scale)), max(1, round(ih * scale))
        base = bg.convert("RGB").resize((rw, rh), Image.LANCZOS)
        if brightness < 100:                                # dim to match the editor's brightness slider
            base = ImageEnhance.Brightness(base).enhance(max(0.0, brightness / 100.0))
        img = base.convert("RGBA")
    else:
        long_edge = max(width, height)
        scale = min(1.0, 1024 / long_edge) if long_edge > 0 else 1.0
        rw = max(1, round(width * scale))
        rh = max(1, round(height * scale))
        img = Image.new("RGBA", (rw, rh), (0, 0, 0, 255))    # black so the overlay composites cleanly
    overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fs = max(10, round(rh / 64))
    font = _font(fs)
    tag_font = _font(max(9, fs - 2))
    lh = fs + 2

    for i, box in enumerate(boxes):
        if not isinstance(box, dict) or box.get("nobbox"):
            continue                                        # skip unplaced elements (no real location)
        palette = [c for c in (box.get("palette") or []) if c]
        r, g, b = _hex_rgb(palette[0]) if palette else (140, 140, 140)   # box = first palette color, else grey
        x1 = max(0, min(rw, round(box.get("x", 0) * rw)))
        y1 = max(0, min(rh, round(box.get("y", 0) * rh)))
        x2 = max(0, min(rw, round((box.get("x", 0) + box.get("w", 0)) * rw)))
        y2 = max(0, min(rh, round((box.get("y", 0) + box.get("h", 0)) * rh)))
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1

        draw.rectangle([x1, y1, x2, y2], outline=(r, g, b, 255), width=2)

        pal5 = palette[:5]                                   # palette shown as a strip along the top edge
        if pal5 and (x2 - x1) > 2:
            sh = max(5, fs // 2)
            seg = (x2 - x1) / len(pal5)
            for p, hexc in enumerate(pal5):
                sx = x1 + round(p * seg)
                draw.rectangle([sx, y1, x1 + round((p + 1) * seg), y1 + sh], fill=_hex_rgb(hexc))

        etype = "text" if box.get("type") == "text" else "obj"
        tag = str(i + 1).zfill(2)
        tw = draw.textlength(tag, font=tag_font)
        draw.rectangle([x1, y1, x1 + tw + 6, y1 + fs + 2], fill=(r, g, b, 255))  # tag chip = box color
        tagfill = (0, 0, 0, 255) if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else (255, 255, 255, 255)
        draw.text((x1 + 3, y1 + 1), tag, fill=tagfill, font=tag_font)

        body = box.get("desc", "") or ""
        if etype == "text" and box.get("text"):
            body = '"%s"%s' % (box["text"], " — " + body if body else "")
        if body and (x2 - x1) > 8:
            ty = y1 + fs + 5
            for line in _wrap(draw, body, font, x2 - x1 - 8):
                if ty > y2:
                    break
                draw.text((x1 + 4, ty), line, fill=_readable((r, g, b)) + (255,), font=font)
                ty += lh

    img = Image.alpha_composite(img, overlay).convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


def _norm_bbox(box):
    # Normalized {x, y, w, h} fractions (0-1) -> [ymin, xmin, ymax, xmax] on a 0-1000 grid.
    def c(v):
        return max(0, min(1000, round(v * 1000)))
    x, y, w, h = box.get("x", 0.0), box.get("y", 0.0), box.get("w", 0.0), box.get("h", 0.0)
    ymin, xmin, ymax, xmax = c(y), c(x), c(y + h), c(x + w)
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    return [ymin, xmin, ymax, xmax]


def _palette(colors):
    # ["#rrggbb", ...] (or autogrow dict) -> ["#RRGGBB", ...] in order, dropping empties.
    if isinstance(colors, dict):
        colors = colors.values()
    return [c.upper() for c in colors if c]


def _dumps(v, lvl=0):
    # Like json.dumps(ensure_ascii=False, indent=4), but scalar arrays stay on one line.
    pad, end = "    " * (lvl + 1), "    " * lvl
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in v):
            return "[" + ", ".join(_dumps(x, lvl) for x in v) + "]"
        return "[\n" + ",\n".join(pad + _dumps(x, lvl + 1) for x in v) + "\n" + end + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        items = [pad + json.dumps(k, ensure_ascii=False) + ": " + _dumps(val, lvl + 1) for k, val in v.items()]
        return "{\n" + ",\n".join(items) + "\n" + end + "}"
    return json.dumps(v, ensure_ascii=False)


def _parse_json_list(s):
    if s:
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass
    return []


def _repair_json(s):
    # Slice out the outermost {...} (drops ``` fences / prose), then strip trailing commas before
    # } or ] — the leading "(...)" alt matches whole strings first, so quoted commas are untouched.
    i, j = s.find("{"), s.rfind("}")
    t = s[i:j + 1] if (i != -1 and j > i) else s
    return re.sub(r'("(?:[^"\\]|\\.)*")|,(\s*[}\]])', lambda m: m.group(1) or m.group(2), t)


def _loads_caption(s):
    # Parse a caption dict; on failure retry once with the lenient repair. Returns dict or None.
    for cand in ((s, _repair_json(s)) if s and s.strip() else ()):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                if cand is not s:
                    logging.warning("[Ideogram4PromptBuilderKJ] import_json had errors; recovered with lenient parse")
                return v
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _caption_to_boxes(cap):
    # Caption dict -> editor box list ({x,y,w,h, type, text, desc, palette}) for preview/bboxes.
    cd = cap.get("compositional_deconstruction") or {}
    boxes = []
    for el in (cd.get("elements") or []):
        if not isinstance(el, dict):
            continue
        box = {"type": "text" if el.get("type") == "text" else "obj",
               "text": el.get("text", "") or "", "desc": el.get("desc", "") or "",
               "palette": list(el.get("color_palette") or [])}
        bb = el.get("bbox")
        if isinstance(bb, (list, tuple)) and len(bb) == 4:
            ymin, xmin, ymax, xmax = bb
            box.update(x=xmin / 1000.0, y=ymin / 1000.0,
                       w=(xmax - xmin) / 1000.0, h=(ymax - ymin) / 1000.0)
        else:                                                # no bbox: unplaced placeholder
            box.update(x=0.03, y=0.03, w=0.22, h=0.14, nobbox=True)
        boxes.append(box)
    return boxes


class Ideogram4PromptBuilderKJ(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Ideogram4PromptBuilderKJ",
            display_name="Ideogram 4 Prompt Builder KJ",
            category="KJNodes/text",
            search_aliases=["ideogram", "caption", "bbox", "prompt builder", "json prompt"],
            is_experimental=True,
            description="""
Visual prompt builder for Ideogram 4's structured JSON caption format.

Drag on the canvas to draw regions; select a region to set its type (obj/text),  
description, text, and color palette. Set the background and optional style fields  
as widgets. Outputs the assembled caption JSON string.  

bbox is normalized to a 0-1000 grid as [ymin, xmin, ymax, xmax]; width/height set
the canvas aspect ratio.

Canvas controls:
- Drag: draw a new region
- Ctrl/Cmd-drag: force-draw a new region even on top of an existing one
- Click: select a region · Alt-click: cycle overlapping regions
- Double-click: edit the description inline
- Right-click: region list (select / delete / duplicate / reorder, top = front)
- Del / Backspace: remove the selected region
- Ctrl/Cmd + C / V / D: copy / paste / duplicate the selected region
- bbox fields (px / out) next to obj/text are editable

Color swatches:
- Click: edit · Drag: reorder · Right-click: remove
- Hover + Ctrl/Cmd + C / V: copy / paste the hex
- "+": add a color (uses the clipboard color if it is one)

Toolbar:
- Live: use the live sampling preview as the background (and grab the final result)
- Grab BG / Clear BG: use the last generated image as the background
- brightness slider, token estimate, and Copy / Paste / Clear all""",
            inputs=[
                io.Int.Input("width", default=1024, min=64, max=16384, step=16,
                             tooltip="Canvas aspect width (also the pixel grid the bbox is measured in). Ideogram 4 needs multiples of 16."),
                io.Int.Input("height", default=1024, min=64, max=16384, step=16,
                             tooltip="Canvas aspect height (also the pixel grid the bbox is measured in). Ideogram 4 needs multiples of 16."),
                io.String.Input("high_level_description", multiline=True, default="",
                                tooltip="Optional one-line overview of the whole image (blank = omitted)."),
                io.String.Input("background", multiline=True, default="",
                                tooltip="Required scene background description."),
                io.DynamicCombo.Input("style", options=[
                    io.DynamicCombo.Option("none", []),
                    io.DynamicCombo.Option("photo", [
                        io.String.Input("photo", default=""),
                    ]),
                    io.DynamicCombo.Option("art_style", [
                        io.String.Input("art_style", default=""),
                    ]),
                ]),
                io.String.Input("aesthetics", default="", tooltip="Style descriptor (blank = omitted)."),
                io.String.Input("lighting", default="", tooltip="Style descriptor (blank = omitted)."),
                io.String.Input("medium", default="", tooltip="Style descriptor (blank = omitted)."),
                io.Image.Input("image", optional=True,
                               tooltip="Optional reference image shown as the editor background (and behind the preview)."),
                io.String.Input("import_json", default="", optional=True, force_input=True,
                                tooltip="Optional: a full caption JSON. When connected, it loads into the editor "
                                        "and drives the output per 'import_mode'."),
                io.String.Input("style_palette_data", default="", socketless=True, advanced=True,
                                tooltip="Serialized style color palette from the editor (managed by the node UI)."),
                io.String.Input("elements_data", default="", socketless=True, advanced=True,
                                tooltip="Serialized regions from the editor (managed by the node UI)."),
                io.Int.Input("bg_brightness", default=25, min=0, max=100, socketless=True, advanced=True,
                             tooltip="Background image brightness % (managed by the node UI slider)."),
                io.Combo.Input("import_mode", options=["when empty", "always"], default="when empty",
                               tooltip="How a wired import_json is used: 'when empty' only seeds the editor while "
                                       "it has no regions (then the editor wins, so you can edit); 'always' makes "
                                       "the wired JSON authoritative so its changes always propagate to the output."),
                io.String.Input("output_format", default="compact", socketless=True, advanced=True,
                                tooltip="Output JSON formatting (set via the editor toolbar): 'compact' (default, what "
                                        "Ideogram 4 expects) or 'pretty' (indented, for readability)."),
                io.BoundingBox.Input("bboxes", optional=True, force_input=True,
                                     tooltip="Optional pixel-space boxes ({x, y, width, height}) used to seed the "
                                             "editor's regions when it has none. Ignored once regions exist."),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.Image.Output(display_name="preview"),
                io.BoundingBox.Output(display_name="bboxes"),
                io.Int.Output(display_name="width"),
                io.Int.Output(display_name="height"),
            ],
        )

    @classmethod
    def execute(cls, width, height, background, style,
                high_level_description="", aesthetics="", lighting="", medium="",
                style_palette_data="", elements_data="", import_json="", import_mode="when empty",
                output_format="pretty", bboxes=None, image=None, bg_brightness=25) -> io.NodeOutput:
        if import_mode not in ("when empty", "always"):      # old workflows saved before this widget existed
            import_mode = "when empty"
        dump = _dumps if output_format == "pretty" else (lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":")))
        boxes = _parse_json_list(elements_data)
        boxes_seeded = False
        if not boxes and bboxes:
            if isinstance(bboxes, dict):                     # a single BoundingBox is a bare {x,y,width,height} dict
                frame = [bboxes]
            elif bboxes and isinstance(bboxes[0], (list, tuple)):
                frame = bboxes[0]                            # per-frame nesting: [[box, ...], ...]
            else:
                frame = bboxes                               # flat list of boxes
            for bb in frame:
                if not isinstance(bb, dict):
                    continue
                boxes.append({"x": bb.get("x", 0) / width, "y": bb.get("y", 0) / height,
                              "w": bb.get("width", 0) / width, "h": bb.get("height", 0) / height,
                              "type": "obj", "text": "", "desc": "", "palette": []})
            boxes_seeded = bool(boxes)

        imported = _loads_caption(import_json)               # strict parse, then a lenient repair fallback

        kind = style["style"]                               # "none" | "photo" | "art_style"

        # Use the wired import_json directly per import_mode: "always" -> authoritative (its changes
        # always propagate); "when empty" -> only seed the editor while it has no regions, then the
        # editor wins so manual edits stick. The editor mirrors it via ui when used.
        used_import = imported is not None and (import_mode == "always" or not boxes)

        if used_import:
            caption = imported
            boxes = _caption_to_boxes(imported)
        else:
            caption = {}
            if high_level_description.strip():
                caption["high_level_description"] = high_level_description

            if kind != "none":
                # The verifier requires every style key present (in order) once a style is
                # chosen; only color_palette is conditional. Emit blanks rather than omit.
                sd = {"aesthetics": aesthetics, "lighting": lighting}
                # photo: ...photo, medium...  |  art_style: ...medium, art_style...  (key order)
                if kind == "photo":
                    sd["photo"] = style.get("photo", "")
                    sd["medium"] = medium
                else:
                    sd["medium"] = medium
                    sd["art_style"] = style.get("art_style", "")
                palette = _palette(_parse_json_list(style_palette_data))
                if palette:
                    sd["color_palette"] = palette
                caption["style_description"] = sd

            elements = []
            for box in boxes:
                if not isinstance(box, dict):
                    continue
                etype = "text" if box.get("type") == "text" else "obj"
                elem = {"type": etype}                      # key order matters
                if not box.get("nobbox"):                   # unplaced elements omit bbox
                    elem["bbox"] = _norm_bbox(box)
                if etype == "text":
                    elem["text"] = box.get("text", "")
                elem["desc"] = box.get("desc", "")
                palette = _palette(box.get("palette", []))
                if palette:
                    elem["color_palette"] = palette[:5]
                elements.append(elem)

            caption["compositional_deconstruction"] = {
                "background": background,
                "elements": elements,
            }
        bg = None
        if image is not None:                                # composite over the input image, else black
            try:
                bg = Image.fromarray((image[0].detach().cpu().numpy() * 255).clip(0, 255).astype(np.uint8))
            except Exception:
                bg = None
        preview = _render_preview(boxes, width, height, bg, bg_brightness)

        # Pixel-space bboxes ({x, y, width, height}) for SAM3 / BoundingBox consumers.
        bbox_dicts = []
        for box in boxes:
            if not isinstance(box, dict) or box.get("nobbox"):
                continue
            x, y = box.get("x", 0.0), box.get("y", 0.0)
            bw, bh = box.get("w", 0.0), box.get("h", 0.0)
            if bw < 0:
                x += bw
                bw = -bw
            if bh < 0:
                y += bh
                bh = -bh
            bbox_dicts.append({"x": round(x * width), "y": round(y * height),
                               "width": round(bw * width), "height": round(bh * height)})
        # Per-frame nesting (list[list[dict]]) — the canonical BoundingBox shape that
        # SAM3 / crop nodes expect (bboxes[frame] -> list of boxes).
        bboxes_out = [bbox_dicts] if bbox_dicts else []

        # ui: send the resolved width/height so the editor canvas can follow connected
        # inputs; import_json (if wired) loads into the editor (output reflects editor only).
        ui = {"dims": [width, height]}
        if boxes_seeded:
            ui["boxes"] = [json.dumps(boxes)]
        if used_import:                                       # mirror the import in the editor (only when used)
            ui["caption"] = [_dumps(imported)]
        return io.NodeOutput(dump(caption), preview, bboxes_out, width, height, ui=ui)


_ARTIST_CONTROL_PROFILES = {
    "look_recipe": {
        "unchanged": {},
        "Leica M6 clean coral-green editorial": {
            "high_level_description": "clean Leica rangefinder editorial image with cool daylight, green-cyan shadow density, coral skin highlights, and restrained high-end analog texture",
            "aesthetics": "high-end analog fashion photograph, Leica M6 rangefinder color response, cinestock 800T-like local color covariance, cool daylight base, shadows biased slightly green-cyan, highlights biased slightly coral, smooth fine film texture, no fake halation, no HDR, no plastic retouching",
            "lighting": "cool north-window daylight, weak negative fill from the darker side of the room, subtle coral warmth only in skin edges and highlights, local color covariance rather than obvious digital split-toning",
            "photo": "Leica M6, 50mm Summicron-M, clean professional color-negative scan, natural lens falloff, realistic sharpness, restrained fine grain, f/4, subject close but not macro",
            "color_palette": ["#101413", "#1F3A32", "#263D36", "#6E756C", "#B9B1A5", "#E8D8CB", "#F0A58F", "#FFD0C0"],
        },
        "Leica M3 natural rangefinder grit": {
            "high_level_description": "natural Leica M3-style 35mm rangefinder photograph with candid framing, available light, tactile skin and fabric, and no AI-poster polish",
            "aesthetics": "classic 35mm rangefinder realism, understated contrast, human-scale imperfection, natural skin texture, organic edge sharpness, restrained grain, no fake antique damage",
            "lighting": "available light with believable falloff, gentle highlight rolloff, shadows allowed to stay natural instead of lifted into HDR",
            "photo": "Leica M3 rangefinder, 50mm lens feel, eye-level candid perspective, slight human framing imperfection, clean color-negative or black-and-white scan depending on the base prompt",
        },
        "Canon G7X Mark II flash digicam": {
            "high_level_description": "compact Canon G7X Mark II flash photo with direct subject exposure, humid real-world atmosphere, saturated summer color, and casual non-studio realism",
            "aesthetics": "2010s compact digicam realism, 1-inch CMOS feel, flash-dominant subject exposure, saturated blues and greens, JPEG color response, slight compact-camera harshness, no HDR, no glossy stock-photo finish",
            "lighting": "direct on-camera flash mixed with late-afternoon sun, flash white balance, crisp foreground exposure, localized specular skin highlights, background allowed to fall off naturally",
            "photo": "Canon PowerShot G7X Mark II, built-in zoom around 24mm equivalent, ISO 125, 1/1000s, f/2.8 to f/4, flash always firing, compact-camera depth and perspective",
        },
        "ARRI Alexa daylight rolloff": {
            "high_level_description": "digital cinema daylight frame with ARRI Alexa-like color science, soft highlight rolloff, controlled saturation, and natural skin",
            "aesthetics": "ARRI Alexa-like motion-picture color pipeline, natural skin tones, controlled saturation, deep but clean shadows, creamy highlight rolloff, no fake film scratches, no crunchy artificial grain",
            "lighting": "cool bright daytime light, highlights can bloom or clip gently while skin remains believable, balanced greens and blues, no harsh AI-poster contrast",
            "photo": "digital cinema camera feel, natural motion-picture sharpness, stable dynamic range, believable focus plane and optical falloff",
        },
        "Kodak Portra 400 clean Frontier scan": {
            "aesthetics": "Kodak Portra 400 color-negative response, natural skin tones, soft pastel color, gentle highlight rolloff, clean neutral Frontier lab scan, restrained contrast, fine natural grain",
            "lighting": "skin-friendly color separation with lifted but not flat shadows, highlights remain soft and printable",
            "photo": "35mm color negative film, rated at box speed, clean professional scan, no artificial digital smoothness",
        },
        "CineStill 800T tungsten practical": {
            "aesthetics": "CineStill 800T tungsten-balanced color negative response, cinematic color separation, pronounced red halation only around bright practical highlights, denser shadows, visible but controlled grain",
            "lighting": "warm tungsten practicals with subtle neon spill, small bright light sources in frame, red halation tied to real highlight sources rather than red frame edges",
            "photo": "35mm tungsten-balanced color negative film, practical-light night interior or storefront feel, clean scan without fake scratches",
        },
    },
    "lens": {
        "unchanged": {},
        "portrait telephoto": {
            "photo": "moderate telephoto portrait lens, natural compression, close focus, realistic iris and skin detail",
            "aesthetics": "controlled portrait perspective, no wide-angle distortion, believable facial proportions",
            "lighting": "soft catchlights with gentle highlight rolloff",
        },
        "editorial wide normal": {
            "photo": "35mm to 45mm editorial lens feel, environmental context, natural perspective",
            "aesthetics": "camera-native editorial framing, grounded spatial depth, no exaggerated distortion",
            "lighting": "available-light look with realistic falloff across the scene",
        },
        "macro product": {
            "photo": "macro product lens behavior, close focusing distance, shallow depth of field, crisp material edges",
            "aesthetics": "precise product geometry, controlled specular highlights, clean surface detail",
            "lighting": "softbox-style highlight control with defined reflection shape",
        },
        "documentary 35mm": {
            "photo": "documentary 35mm camera feel, natural hand-held framing, unforced perspective",
            "aesthetics": "real-world composition, slight observational imperfection, non-advertising finish",
            "lighting": "ambient natural light with believable mixed-color spill",
        },
    },
    "color": {
        "unchanged": {},
        "cinematic natural color": {
            "aesthetics": "cinematic natural color, restrained saturation, accurate skin and material color",
            "lighting": "neutral-to-warm highlights, clean midtones, soft cool shadows",
        },
        "high-key pastel editorial": {
            "aesthetics": "high-key pastel editorial grade, airy contrast, controlled pale color separation",
            "lighting": "soft bright highlights, lifted shadows, gentle bloom without washed-out subject detail",
        },
        "wet neon night": {
            "aesthetics": "wet neon color separation, saturated reflections, deep blue shadows, clean magenta-cyan accents",
            "lighting": "blue-hour ambient light mixed with localized neon reflections and glossy pavement highlights",
        },
        "muted filmic documentary": {
            "aesthetics": "muted filmic palette, restrained contrast, natural greens and warm skin tones",
            "lighting": "soft practical light, low digital harshness, realistic shadow color",
        },
        "product accurate color": {
            "aesthetics": "product-accurate color, clean whites, stable neutral balance, no unwanted hue drift",
            "lighting": "controlled studio highlights that preserve texture and material color",
        },
    },
    "surface": {
        "unchanged": {},
        "natural skin and fabric": {
            "aesthetics": "natural skin pores, subtle freckles, realistic fabric weave, no porcelain smoothing",
            "photo": "fine skin texture and cloth fibers resolved without over-sharpening",
        },
        "polished automotive": {
            "aesthetics": "polished paint, chrome edge definition, curved reflection fidelity, realistic tire and glass materials",
            "lighting": "large soft reflections across body panels, sharp pin highlights on chrome",
        },
        "ceramic and window light": {
            "aesthetics": "matte ceramic texture, hand-made surface variation, soft clay detail, quiet studio realism",
            "lighting": "north-window softness, gentle rim on ceramic edges, warm interior bounce",
        },
        "rain and wet pavement": {
            "aesthetics": "wet pavement micro-reflections, rain beads, mist, believable glossy surface breakup",
            "lighting": "small specular points in water droplets and broad reflections on slick ground",
        },
    },
}


def _append_artist_text(existing, addition):
    existing = (existing or "").strip()
    addition = (addition or "").strip()
    if not addition:
        return existing
    if not existing:
        return addition
    if addition.lower() in existing.lower():
        return existing
    return f"{existing}. {addition}"


def _append_artist_list(existing, addition):
    base = existing if isinstance(existing, list) else []
    out = list(base)
    seen = {str(item).lower() for item in out}
    for item in addition:
        key = str(item).lower()
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _apply_artist_profile(caption, profile):
    style = caption.setdefault("style_description", {})
    if not isinstance(style, dict):
        style = {}
        caption["style_description"] = style

    for field, value in profile.items():
        if field == "high_level_description":
            caption[field] = _append_artist_text(caption.get(field, ""), value)
        elif isinstance(value, list):
            style[field] = _append_artist_list(style.get(field), value)
        else:
            style[field] = _append_artist_text(style.get(field, ""), value)


def _loads_artist_caption(prompt):
    try:
        parsed = json.loads(prompt)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


_FINGERPRINT_SECTION_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:[1-6]\s*[\).]\s*)?"
    r"(visual fingerprint|drift risks|counter-spec|prompt|negative constraints|optional shorthand references)\s*$"
)


def _clean_control_lines(text):
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^\s*(?:[-*]+|\d+\s*[\).])\s*", "", line).strip()
        if line:
            lines.append(line)
    return lines


def _clean_control_text(text):
    return " ".join(_clean_control_lines(text))


def _parse_fingerprint_protocol(text):
    if not text or not text.strip():
        return {}
    matches = list(_FINGERPRINT_SECTION_RE.finditer(text))
    if not matches:
        return {}
    sections = {}
    aliases = {
        "visual fingerprint": "visual_fingerprint",
        "drift risks": "drift_risks",
        "counter-spec": "counter_spec",
        "prompt": "prompt_block",
        "negative constraints": "negative_constraints",
        "optional shorthand references": "optional_shorthand_refs",
    }
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        key = aliases[match.group(1).lower()]
        body = text[start:end].strip()
        if body:
            sections[key] = body
    return sections


def _merge_section(parsed, explicit, key):
    parts = []
    if parsed.get(key):
        parts.append(parsed[key])
    if explicit and explicit.strip():
        parts.append(explicit.strip())
    return "\n".join(parts)


def _join_control_lines(lines):
    seen = set()
    out = []
    for line in lines:
        key = line.lower()
        if key not in seen:
            out.append(line)
            seen.add(key)
    return "; ".join(out)


def _extract_hex_palette(*texts):
    colors = []
    seen = set()
    for text in texts:
        for color in re.findall(r"#[0-9A-Fa-f]{6}", text or ""):
            up = color.upper()
            if up not in seen:
                colors.append(up)
                seen.add(up)
    return colors


class Ideogram4ArtistControlsKJ(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Ideogram4ArtistControlsKJ",
            display_name="Ideogram 4 Artist Controls KJ",
            category="KJNodes/text",
            search_aliases=["ideogram", "artist controls", "look recipe", "camera", "film", "lens", "color", "surface"],
            is_experimental=True,
            description="""
Deterministic pro-artist control layer for Ideogram 4 structured JSON prompts.

Use this after Ideogram 4 Prompt Builder KJ. It appends concise, named control
profiles into the standard Ideogram JSON fields instead of adding non-standard
schema keys. This keeps runtime latency at zero and preserves compatibility with
existing Ideogram workflows while making look, lens, color, and surface intent explicit.
""",
            inputs=[
                io.String.Input("prompt", multiline=True, force_input=True,
                                tooltip="Structured Ideogram JSON prompt from Ideogram 4 Prompt Builder KJ."),
                io.Combo.Input("look_recipe", options=list(_ARTIST_CONTROL_PROFILES["look_recipe"].keys()),
                               default="unchanged",
                               tooltip="One coherent capture/look recipe. Use lens, color, and surface controls as overrides."),
                io.Combo.Input("lens_profile", options=list(_ARTIST_CONTROL_PROFILES["lens"].keys()),
                               default="unchanged",
                               tooltip="Camera/lens behavior to append to photo, aesthetics, and lighting fields."),
                io.Combo.Input("color_profile", options=list(_ARTIST_CONTROL_PROFILES["color"].keys()),
                               default="unchanged",
                               tooltip="Color science and grade behavior to append to aesthetics and lighting fields."),
                io.Combo.Input("surface_profile", options=list(_ARTIST_CONTROL_PROFILES["surface"].keys()),
                               default="unchanged",
                               tooltip="Texture/material behavior to append to relevant style fields."),
                io.Combo.Input("control_strength", options=["light", "medium", "strong"], default="medium",
                               tooltip="How much custom artist language to apply. Profiles stay concise at every strength."),
                io.String.Input("artist_notes", multiline=True, default="",
                                tooltip="Optional short notes appended to high_level_description. Keep this intentional."),
                io.Combo.Input("output_format", options=["compact", "pretty"], default="compact",
                               tooltip="JSON formatting for the output prompt."),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
            ],
        )

    @classmethod
    def execute(cls, prompt, look_recipe="unchanged", lens_profile="unchanged", color_profile="unchanged",
                surface_profile="unchanged", control_strength="medium",
                artist_notes="", output_format="compact") -> io.NodeOutput:
        caption = _loads_artist_caption(prompt)
        if caption is None:
            caption = {"high_level_description": prompt or ""}

        for group, selected in (
            ("look_recipe", look_recipe),
            ("lens", lens_profile),
            ("color", color_profile),
            ("surface", surface_profile),
        ):
            profile = _ARTIST_CONTROL_PROFILES[group].get(selected, {})
            _apply_artist_profile(caption, profile)

        if artist_notes.strip():
            notes = artist_notes.strip()
            if control_strength == "light":
                notes = notes.split(".")[0].strip()
            elif control_strength == "strong":
                notes = _append_artist_text(notes, "preserve this control intent over generic AI-poster polish")
            caption["high_level_description"] = _append_artist_text(
                caption.get("high_level_description", ""),
                notes,
            )

        if output_format == "pretty":
            output = _dumps(caption)
        else:
            output = json.dumps(caption, ensure_ascii=False, separators=(",", ":"))
        return io.NodeOutput(output)


class Ideogram4VisualFingerprintKJ(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Ideogram4VisualFingerprintKJ",
            display_name="Ideogram 4 Visual Fingerprint KJ",
            category="KJNodes/text",
            search_aliases=["ideogram", "visual fingerprint", "reference", "anti drift", "camera", "film", "prompt"],
            is_experimental=True,
            description="""
Converts a reference-analysis visual fingerprint into Ideogram 4 structured JSON.

This node does not identify cameras or analyze pixels. Paste the output of a
reference-analysis protocol, or fill the sections manually. The node preserves
observable rendering controls - color relationships, tonal mapping, edge
behavior, texture, lighting, and anti-drift constraints - in normal Ideogram
JSON fields.
""",
            inputs=[
                io.String.Input("protocol_text", multiline=True, default="",
                                tooltip="Optional full 1-6 protocol output. Parsed sections are merged with the fields below."),
                io.String.Input("base_prompt", multiline=True, default="",
                                tooltip="Subject/composition prompt if the protocol has no prompt block."),
                io.String.Input("visual_fingerprint", multiline=True, default="",
                                tooltip="Concrete observable traits: color relationships, tone, texture, edges, lighting, framing."),
                io.String.Input("counter_spec", multiline=True, default="",
                                tooltip="Generator-safe control layer that prevents drift."),
                io.String.Input("drift_risks", multiline=True, default="",
                                tooltip="Likely wrong neighboring aesthetics. Preserved as anti-drift guidance."),
                io.String.Input("negative_constraints", multiline=True, default="",
                                tooltip="Dense avoid line. Also returned as a separate negative output."),
                io.String.Input("optional_shorthand_refs", multiline=True, default="",
                                tooltip="Optional secondary camera/film shorthand with limits unpacked in text."),
                io.Combo.Input("target_mode", options=["text-to-image", "image-to-image"], default="text-to-image",
                               tooltip="Image-to-image mode adds reference-preservation language without adding image analysis."),
                io.Combo.Input("output_format", options=["compact", "pretty"], default="compact",
                               tooltip="JSON formatting for the Ideogram prompt output."),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.String.Output(display_name="negative"),
            ],
        )

    @classmethod
    def execute(cls, protocol_text="", base_prompt="", visual_fingerprint="",
                counter_spec="", drift_risks="", negative_constraints="",
                optional_shorthand_refs="", target_mode="text-to-image",
                output_format="compact") -> io.NodeOutput:
        parsed = _parse_fingerprint_protocol(protocol_text)
        visual_fingerprint = _merge_section(parsed, visual_fingerprint, "visual_fingerprint")
        counter_spec = _merge_section(parsed, counter_spec, "counter_spec")
        drift_risks = _merge_section(parsed, drift_risks, "drift_risks")
        negative_constraints = _merge_section(parsed, negative_constraints, "negative_constraints")
        optional_shorthand_refs = _merge_section(parsed, optional_shorthand_refs, "optional_shorthand_refs")
        prompt_block = _merge_section(parsed, "", "prompt_block")

        fingerprint_lines = _clean_control_lines(visual_fingerprint)
        counter_lines = _clean_control_lines(counter_spec)
        drift_lines = _clean_control_lines(drift_risks)
        shorthand_lines = _clean_control_lines(optional_shorthand_refs)
        negative_text = _clean_control_text(negative_constraints)

        base = prompt_block.strip() or base_prompt.strip()
        if not base:
            base = _join_control_lines((fingerprint_lines + counter_lines)[:4]) or "Image controlled by the visual fingerprint."
        if target_mode == "image-to-image":
            base = _append_artist_text(
                base,
                "preserve the reference image's observable rendering fingerprint rather than guessing camera metadata",
            )

        all_control_lines = fingerprint_lines + counter_lines
        control_text = _join_control_lines(all_control_lines)

        aesthetics_parts = []
        if fingerprint_lines:
            aesthetics_parts.append("visual fingerprint: " + _join_control_lines(fingerprint_lines))
        if counter_lines:
            aesthetics_parts.append("counter-spec: " + _join_control_lines(counter_lines))
        if drift_lines:
            aesthetics_parts.append("avoid neighboring drift: " + _join_control_lines(drift_lines))
        if shorthand_lines:
            aesthetics_parts.append("secondary shorthand only: " + _join_control_lines(shorthand_lines))
        if negative_text:
            aesthetics_parts.append("avoid: " + negative_text)

        style = {
            "aesthetics": ". ".join(aesthetics_parts) if aesthetics_parts else "observable rendering behavior, not broad aesthetic shorthand",
            "lighting": control_text or "follow the prompt block's lighting while preserving the visual fingerprint",
            "photo": control_text or "rendering-language control over camera-language attribution",
            "medium": "photorealistic image with generator-safe visual fingerprint controls",
        }
        palette = _extract_hex_palette(visual_fingerprint, counter_spec, prompt_block, base_prompt)
        if palette:
            style["color_palette"] = palette

        caption = {
            "high_level_description": base,
            "style_description": style,
            "compositional_deconstruction": {
                "background": "Follow the prompt block for scene and background; apply the same tonal, color, edge, and texture behavior across the whole frame.",
                "elements": [],
            },
        }

        if output_format == "pretty":
            output = _dumps(caption)
        else:
            output = json.dumps(caption, ensure_ascii=False, separators=(",", ":"))
        return io.NodeOutput(output, negative_text)
