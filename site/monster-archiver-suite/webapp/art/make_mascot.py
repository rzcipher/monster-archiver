"""
Generates the "Reely" mascot for Monster Archiver — a small tape-reel-eared
archive monster. Outputs raw SVG, geometric/flat (no baked-in raster glow)
so it stays crisp at any raster size. Eyes and chest core are left as plain
pale "unlit" shapes on purpose — the live web component overlays colored,
animated glow/pupils on top of those regions (see Mascot.tsx), so the PNG
itself never hard-codes a theme color into the pixels for those parts.
"""

import math

VOID_950 = "#05070c"
VOID_900 = "#0b0e16"
VOID_800 = "#12161f"
VOID_700 = "#1e2532"
VOID_600 = "#2c3444"
DEEZER_400 = "#a855f7"
DEEZER_500 = "#8b2ce8"
FLOW_400 = "#e84fc0"
FLOW_500 = "#d926a9"
PALE = "#f3ecff"


def scallop_path(points_xy, y_base, y_dip, n, direction=1):
    """points_xy: (x_start, x_end). Returns a 'C ...' string tracing scallops
    from x_start to x_end (direction=1) — call with swapped/negative for R->L."""
    x_start, x_end = points_xy
    width = x_end - x_start
    seg = width / n
    d = ""
    for i in range(n):
        x0 = x_start + i * seg
        xm = x0 + seg / 2
        x1 = x0 + seg
        d += f" C {x0 + seg*0.15:.1f} {y_base:.1f}, {xm - seg*0.28:.1f} {y_dip:.1f}, {xm:.1f} {y_dip:.1f}"
        d += f" C {xm + seg*0.28:.1f} {y_dip:.1f}, {x1 - seg*0.15:.1f} {y_base:.1f}, {x1:.1f} {y_base:.1f}"
    return d


def reel(cx, cy, r_outer, r_inner, r_hub, stroke, fill_outer, fill_inner):
    """A tape-reel ear: outer disc, inset ring, hub, three spokes."""
    parts = []
    parts.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="{fill_outer}" '
        f'stroke="{stroke}" stroke-width="3"/>'
    )
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_inner}" fill="{fill_inner}"/>')
    for k in range(3):
        ang = math.radians(90 + k * 120)
        x1 = cx + math.cos(ang) * (r_hub + 3)
        y1 = cy + math.sin(ang) * (r_hub + 3)
        x2 = cx + math.cos(ang) * (r_inner - 4)
        y2 = cy + math.sin(ang) * (r_inner - 4)
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{fill_outer}" stroke-width="5" stroke-linecap="round"/>'
        )
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_hub}" fill="{stroke}"/>')
    return "\n".join(parts)


def build(pad=0):
    W, H = 400, 460

    # ---- body silhouette -------------------------------------------------
    left_shoulder = (100, 192)
    right_shoulder = (300, 192)
    peak = (200, 54)
    right_flare = (307, 300)
    left_flare = (93, 300)
    hem = scallop_path((307, 93), 300, 346, 4)  # right -> left

    body_d = (
        f"M {left_shoulder[0]},{left_shoulder[1]} "
        f"C {left_shoulder[0]},128 {peak[0]-58},{peak[1]} {peak[0]},{peak[1]} "
        f"C {peak[0]+58},{peak[1]} {right_shoulder[0]},128 {right_shoulder[0]},{right_shoulder[1]} "
        f"C {right_flare[0]-2},224 {right_flare[0]},262 {right_flare[0]},{right_flare[1]} "
        f"{hem} "
        f"C {left_flare[0]},262 {left_flare[0]+2},224 {left_shoulder[0]},{left_shoulder[1]} "
        f"Z"
    )

    necks = (
        f'<path d="M 84,150 C 80,175 85,196 108,206 L 128,196 '
        f'C 108,186 104,168 108,148 Z" fill="url(#bodyGrad)" stroke="{VOID_950}" stroke-width="2.5"/>'
        f'<path d="M 316,150 C 320,175 315,196 292,206 L 272,196 '
        f'C 292,186 296,168 292,148 Z" fill="url(#bodyGrad)" stroke="{VOID_950}" stroke-width="2.5"/>'
    )
    ears = "\n".join([
        reel(100, 118, 40, 20, 7, VOID_950, VOID_800, VOID_600),
        reel(300, 118, 40, 20, 7, VOID_950, VOID_800, VOID_600),
    ])

    # eyes — deliberately flat/unlit pale shapes; the app overlays the
    # animated glow + pupil in CSS/SVG on top of these at runtime.
    eye_l = (f'<ellipse cx="152" cy="182" rx="29" ry="35" fill="url(#eyeGrad)" '
              f'stroke="{VOID_900}" stroke-width="2" stroke-opacity="0.45"/>')
    eye_r = (f'<ellipse cx="248" cy="182" rx="29" ry="35" fill="url(#eyeGrad)" '
              f'stroke="{VOID_900}" stroke-width="2" stroke-opacity="0.45"/>')

    # mouth — happy upward curve + two small hanging fangs
    mouth = (
        '<path d="M 163,236 C 175,254 188,262 200,262 '
        'C 212,262 225,254 237,236" fill="none" '
        f'stroke="{VOID_950}" stroke-width="7" stroke-linecap="round"/>'
    )
    fangs = (
        f'<path d="M 179,240 L 172,258 L 188,244 Z" fill="{PALE}"/>'
        f'<path d="M 221,240 L 228,258 L 212,244 Z" fill="{PALE}"/>'
    )

    # chest core — a reel/"play" emblem, also left unlit for runtime glow
    core = (
        '<circle cx="200" cy="303" r="30" fill="url(#coreGrad)" '
        f'stroke="{VOID_950}" stroke-width="3"/>'
        f'<path d="M 191,289 L 191,317 L 215,303 Z" fill="{VOID_900}" opacity="0.55"/>'
    )

    # stubby side arms
    arms = (
        f'<path d="M 84,268 C 62,266 54,282 60,300 C 66,314 84,312 92,298 '
        f'C 98,286 96,270 84,268 Z" fill="url(#bodyGrad)" stroke="{VOID_950}" stroke-width="3"/>'
        f'<path d="M 316,268 C 338,266 346,282 340,300 C 334,314 316,312 308,298 '
        f'C 302,286 304,270 316,268 Z" fill="url(#bodyGrad)" stroke="{VOID_950}" stroke-width="3"/>'
    )

    svg = f'''<svg viewBox="{-pad} {-pad} {W + pad*2} {H + pad*2}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bodyGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{VOID_600}"/>
      <stop offset="55%" stop-color="{VOID_800}"/>
      <stop offset="100%" stop-color="{VOID_900}"/>
    </linearGradient>
    <linearGradient id="rimGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{DEEZER_400}"/>
      <stop offset="100%" stop-color="{FLOW_400}"/>
    </linearGradient>
    <radialGradient id="eyeGrad" cx="38%" cy="32%" r="75%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="{PALE}"/>
    </radialGradient>
    <radialGradient id="coreGrad" cx="38%" cy="32%" r="75%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="{PALE}"/>
    </radialGradient>
    <filter id="softBlur" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="9"/>
    </filter>
  </defs>

  <!-- baked-in rim glow (static brand signature, always nebula-toned) -->
  <path d="{body_d}" fill="none" stroke="url(#rimGrad)" stroke-width="14"
        opacity="0.35" filter="url(#softBlur)"/>

  {ears}
  {necks}

  <path d="{body_d}" fill="url(#bodyGrad)" stroke="url(#rimGrad)" stroke-width="3.5"/>
  <ellipse cx="150" cy="120" rx="55" ry="38" fill="#ffffff" opacity="0.05" filter="url(#softBlur)"/>

  {arms}
  {eye_l}
  {eye_r}
  {mouth}
  {fangs}
  {core}
</svg>
'''
    return svg


if __name__ == "__main__":
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    flat_path = os.path.join(here, "mascot.svg")
    padded_path = os.path.join(here, "mascot_padded.svg")

    with open(flat_path, "w") as f:
        f.write(build(pad=0))
    with open(padded_path, "w") as f:
        f.write(build(pad=20))
    print(f"wrote {flat_path}")
    print(f"wrote {padded_path}")

    # Optional: re-render public/mascot.png straight from the padded SVG so
    # Mascot.tsx's baked-in art stays in sync with any edits here. Needs
    # `pip install cairosvg`; skipped (not a hard dependency of this repo)
    # if it isn't installed.
    try:
        import cairosvg

        png_path = os.path.join(here, "..", "public", "mascot.png")
        # 3x the 440x500 viewBox Mascot.tsx expects, for a crisp render at
        # the largest size it's currently used at (BootSequence, 104px).
        cairosvg.svg2png(url=padded_path, write_to=png_path, output_width=1320, output_height=1500)
        print(f"wrote {png_path}")
    except ImportError:
        print("cairosvg not installed — skipping public/mascot.png render.")
        print("Run `pip install cairosvg` and re-run this script to regenerate it.")
