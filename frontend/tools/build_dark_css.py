"""
Builds frontend/static/dark.css - the dashboard's dark mode - from style.css.

The stylesheet grew in layers, most of them written for a white page with
fixed colours. Rather than hand-maintain a second theme, this reads every rule
and writes an override, active under <html data-mode="dark">, for each
declaration that paints a light surface, dark text or a light border. Colours
keep their hue: white cards become dark slate, blue tints deep blue, green /
amber / red tints stay recognisably green / amber / red, and saturated accent
colours (buttons, candles, badges) are left alone.

    python frontend/tools/build_dark_css.py      # re-run after changing style.css
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
SRC, OUT = STATIC / "style.css", STATIC / "dark.css"
PREFIX = 'html[data-mode="dark"]'

HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")
BG_PROPS = {"background", "background-color"}
TEXT_PROPS = {"color", "fill", "-webkit-text-fill-color"}
LINE_PROPS = {"border", "border-color", "border-top", "border-bottom", "border-left", "border-right", "border-top-color",
              "border-bottom-color", "border-left-color", "border-right-color", "outline", "outline-color", "stroke", "column-rule"}


def _rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(r, g, b):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in (r, g, b))


def surface(r, g, b):
    """A light background -> a dark one of the same hue."""
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.80:
        return None                      # already a mid / dark colour (a button, a badge): leave it
    tint = s * (1 - l) * 10              # how coloured the light tint is
    if tint < 0.35:                      # near-white / grey: the slate surfaces
        nl = 0.075 + (1 - l) * 0.75
        return _hex(*colorsys.hls_to_rgb(0.57, nl, 0.28))
    return _hex(*colorsys.hls_to_rgb(h, 0.13 + (1 - l) * 0.35, min(0.55, s * 0.6)))


def text(r, g, b):
    """Dark or grey text -> light text of the same hue."""
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l > 0.62:
        return None
    if s < 0.25:                         # neutral inks and greys
        nl = 0.90 if l < 0.2 else 0.80 if l < 0.38 else 0.66
        return _hex(*colorsys.hls_to_rgb(0.57, nl, 0.12))
    return _hex(*colorsys.hls_to_rgb(h, max(0.66, 1 - l * 0.55), min(0.85, s)))


def line(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.70:
        return None
    return _hex(*colorsys.hls_to_rgb(h if s > 0.3 else 0.57, 0.19 + (1 - l) * 0.3, min(0.35, s * 0.5 + 0.12)))


def convert(value: str, kind) -> str | None:
    changed = False

    def sub_hex(m):
        nonlocal changed
        out = kind(*_rgb(m.group(0)))
        if out:
            changed = True
            return out
        return m.group(0)

    def sub_rgba(m):
        nonlocal changed
        r, g, b = (int(m.group(i)) / 255 for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        if kind is surface and a < 0.6:
            return m.group(0)            # translucent white on a coloured banner: keep
        out = kind(r, g, b)
        if not out:
            return m.group(0)
        changed = True
        rr, gg, bb = _rgb(out)
        return f"rgba({round(rr*255)},{round(gg*255)},{round(bb*255)},{m.group(4) or 1})"

    v = HEX.sub(sub_hex, value)
    v = RGBA.sub(sub_rgba, v)
    if kind is surface and re.search(r"\bwhite\b", v):
        v, changed = re.sub(r"\bwhite\b", surface(1, 1, 1), v), True
    return v if changed else None


def prefix(selector: str) -> str:
    out = []
    for sel in selector.split(","):
        sel = sel.strip()
        if not sel:
            continue
        m = re.match(r"^(:root|html)((?:\[[^\]]*\])*)(.*)$", sel)
        if m:
            out.append(f"{PREFIX}{m.group(2)}{m.group(3)}")
        else:
            out.append(f"{PREFIX} {sel}")
    return ",".join(out)


def rules(css: str):
    """Yield (media, selector, body) for style rules, descending into @media (not print)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n = 0, len(css)

    def block_end(j):
        depth = 0
        while j < n:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        return n

    def walk(start, stop, media):
        j = start
        while j < stop:
            brace = css.find("{", j, stop)
            if brace < 0:
                return
            head = css[j:brace].strip()
            end = block_end(brace)
            if head.startswith("@media"):
                if "print" not in head:
                    walk(brace + 1, end, head)
            elif head.startswith("@"):
                pass                     # keyframes, font-face, supports...
            elif head:
                yield_list.append((media, head, css[brace + 1:end]))
            j = end + 1

    yield_list = []
    walk(0, n, None)
    return yield_list


TOKENS_TEXT = ("ink", "muted", "acc-dark", "good-ink")
TOKENS_LINE = ("grid", "axis", "ring", "line")


def main():
    css = SRC.read_text(encoding="utf-8")
    out_rules: dict[str | None, list[str]] = {}
    tokens: dict[str, str] = {}
    for media, sel, body in rules(css):
        decls = []
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            prop, val = decl.split(":", 1)
            prop, val = prop.strip().lower(), val.strip()
            if prop.startswith("--"):
                if media is None and any(x in sel for x in (":root", "html", ".bp", "body")):
                    name = prop[2:]
                    kind = text if any(t in name for t in TOKENS_TEXT) else line if any(t in name for t in TOKENS_LINE) else surface
                    new = convert(val, kind)
                    if new:
                        tokens[prop] = new
                continue
            important = "!important" in val
            val_clean = val.replace("!important", "").strip()
            kind = surface if prop in BG_PROPS else text if prop in TEXT_PROPS else line if prop in LINE_PROPS else None
            if prop == "box-shadow" or kind is None:
                continue
            new = convert(val_clean, kind)
            if new:
                decls.append(f"{prop}:{new}{' !important' if important else ''}")
        if decls:
            out_rules.setdefault(media, []).append(f"{prefix(sel)}{{{';'.join(decls)}}}")

    lines = ["/* GENERATED by frontend/tools/build_dark_css.py from style.css - do not edit by hand. */",
             f"{PREFIX}{{color-scheme:dark;" + ";".join(f"{k}:{v}" for k, v in sorted(tokens.items())) + "}",
             f"{PREFIX}[data-theme=\"light\"]{{" + ";".join(f"{k}:{v}" for k, v in sorted(tokens.items())) + "}",
             f"{PREFIX} body{{background:#0b141c;color:#dce4ea}}"]
    for media, items in out_rules.items():
        if media is None:
            lines += items
        else:
            lines.append(media + "{" + "".join(items) + "}")
    lines.append((STATIC / "dark-extra.css").read_text(encoding="utf-8") if (STATIC / "dark-extra.css").exists() else "")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"dark.css: {sum(len(v) for v in out_rules.values())} rule overrides, {len(tokens)} tokens")


if __name__ == "__main__":
    main()
