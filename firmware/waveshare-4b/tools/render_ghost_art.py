"""Render the room screen's Ghost artwork from the desktop/phone avatar.

The body is the approved "Ghost Sheet 2.4 Soft Hem" cloth (tools/ghost_sheet.py,
copied unchanged from the desktop repo), rendered front-on with the same pearl
look: soft key light, pink/blue sheen, painted blush, translucent hem. The face
parts are separate sprites so the screen can still blink, glance and talk:
eye white, iris, and six mouth shapes from closed smile to wide open.

    python3 firmware/waveshare-4b/tools/render_ghost_art.py [--preview out.png]

Writes main/ghost_art.c (LVGL RGB565A8 images) and main/ghost_art.h.
Needs numpy and Pillow. Deterministic: same input, same bytes.
"""
import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import ghost_sheet

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / "main"
SS = 3                      # supersampling
BODY_H = 330                # body sprite height on the 480x480 screen
BG = (17, 9, 36)            # phone/desktop backdrop purple


# ── body ────────────────────────────────────────────────────────────────────
def body_mesh():
    verts, faces = ghost_sheet.mesh()
    v = np.array(verts, dtype=np.float64)
    tris = []
    for f in faces:
        if len(f) == 3:
            tris.append(f)
        else:
            a, b, c, d = f
            tris.append((a, b, c)); tris.append((a, c, d))
    t = np.array(tris, dtype=np.int64)
    rows = np.zeros(len(v)); rows[1:] = (np.arange(len(v) - 1) // ghost_sheet.SEGMENTS + 1) / ghost_sheet.RINGS
    return v, t, rows


def vertex_normals(v, t):
    n = np.zeros_like(v)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    for k in range(3):
        np.add.at(n, t[:, k], fn)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    return n


def render_body():
    v, t, rows = body_mesh()
    n = vertex_normals(v, t)
    # Camera: front (-Y) with a slight look down, orthographic. Screen x = X, y = -Z.
    tilt = math.radians(7)
    rot = np.array([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
    pv, pn = v @ rot.T, n @ rot.T
    xs, zs = pv[:, 0], pv[:, 2]
    scale = (BODY_H * SS - 8 * SS) / (zs.max() - zs.min())
    W = int((xs.max() - xs.min()) * scale) + 8 * SS
    H = BODY_H * SS
    sx = (xs - xs.min()) * scale + 4 * SS
    sy = (zs.max() - zs) * scale + 4 * SS
    depth = pv[:, 1]                       # smaller = nearer the camera
    color = np.zeros((H, W, 3)); alpha = np.zeros((H, W)); zbuf = np.full((H, W), np.inf)

    view = np.array([0, -1, 0.0])
    key = np.array([-0.45, -0.75, 0.5]); key /= np.linalg.norm(key)
    fill = np.array([0.6, -0.6, -0.2]); fill /= np.linalg.norm(fill)
    base = np.array([0.95, 0.94, 1.0])

    for a, b, c in t:
        xa, xb, xc = sx[a], sx[b], sx[c]; ya, yb, yc = sy[a], sy[b], sy[c]
        x0, x1 = int(max(min(xa, xb, xc), 0)), int(min(max(xa, xb, xc) + 1, W))
        y0, y1 = int(max(min(ya, yb, yc), 0)), int(min(max(ya, yb, yc) + 1, H))
        if x0 >= x1 or y0 >= y1:
            continue
        den = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        if abs(den) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1) + .5, np.arange(y0, y1) + .5)
        w0 = ((yb - yc) * (gx - xc) + (xc - xb) * (gy - yc)) / den
        w1 = ((yc - ya) * (gx - xc) + (xa - xc) * (gy - yc)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * depth[a] + w1 * depth[b] + w2 * depth[c]
        zb = zbuf[y0:y1, x0:x1]
        win = inside & (z < zb)
        if not win.any():
            continue
        nn = w0[..., None] * pn[a] + w1[..., None] * pn[b] + w2[..., None] * pn[c]
        nn /= np.linalg.norm(nn, axis=-1, keepdims=True) + 1e-12
        facing = (nn @ view)
        back = facing < 0
        nn[back] *= -1                         # inside of the sheet seen through the hem
        row = w0 * rows[a] + w1 * rows[b] + w2 * rows[c]
        wrap = np.clip((nn @ key + 0.45) / 1.45, 0, 1)
        fil = np.clip(nn @ fill, 0, 1)
        nv = np.clip(np.abs(facing), 0, 1)
        fres = (1 - nv) ** 2.5
        light = 0.66 + 0.40 * wrap + 0.08 * fil
        spec = np.clip(nn @ ((key - view) / np.linalg.norm(key - view)), 0, 1) ** 28 * 0.18
        col = base * light[..., None] + spec[..., None]
        # Pearl sheen: pink towards the light side, blue on the shadow side.
        side = np.clip(nn[..., 0], -1, 1)
        sheen = np.where(side[..., None] < 0, [1.0, 0.82, 0.92], [0.80, 0.88, 1.0])
        col = col * (1 - 0.35 * fres[..., None]) + sheen * 0.35 * fres[..., None]
        col = np.where(back[..., None], col * 0.72, col)
        al = np.where(row > 0.86, 1 - 0.28 * np.clip((row - 0.86) / 0.14, 0, 1), 1.0)
        zb[win] = z[win]
        color[y0:y1, x0:x1][win] = col[win]
        alpha[y0:y1, x0:x1][win] = al[win]

    # Soft contact shading where folds overlap (cheap ambient occlusion).
    zfin = np.where(np.isfinite(zbuf), zbuf, np.nan)
    rgb = np.clip(color, 0, 1)
    img = Image.fromarray((np.dstack([rgb, alpha]) * 255).astype(np.uint8), "RGBA")
    zimg = np.nan_to_num(zfin, nan=np.nanmax(zfin))
    zblur = np.array(Image.fromarray(((zimg - np.nanmin(zfin)) / (np.nanmax(zfin) - np.nanmin(zfin)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6 * SS)), dtype=np.float64) / 255
    zn = (zimg - np.nanmin(zfin)) / (np.nanmax(zfin) - np.nanmin(zfin))
    ao = np.clip(1 - np.clip(zn - zblur, 0, 1) * 2.6, 0.55, 1)
    arr = np.array(img, dtype=np.float64)
    arr[..., :3] *= ao[..., None]
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
    return img, (sx, sy, scale, v, pv)


def face_layout(body_w, body_h):
    """Face positions in body-sprite pixels (final scale), matched to the avatar."""
    cx = body_w * 0.5
    return {
        "eye_y": body_h * 0.235, "eye_dx": body_h * 0.1, "eye_d": body_h * 0.165,
        "mouth_y": body_h * 0.39, "blush_y": body_h * 0.335, "blush_dx": body_h * 0.15,
        "brow_y": body_h * 0.135, "cx": cx,
    }


def paint_blush(img, lay):
    W, H = img.size
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for s in (-1, 1):
        x = lay["cx"] + s * lay["blush_dx"]; y = lay["blush_y"]
        rx, ry = H * 0.05, H * 0.024
        d.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(255, 140, 170, 170))
    over = over.filter(ImageFilter.GaussianBlur(H * 0.012))
    a = np.array(img); o = np.array(over).astype(np.float64)
    oa = o[..., 3:4] / 255 * (a[..., 3:4] > 0)
    a[..., :3] = (a[..., :3] * (1 - oa) + o[..., :3] * oa).astype(np.uint8)
    return Image.fromarray(a, "RGBA")


# ── face sprites (drawn at SS and downscaled) ──────────────────────────────
def eye_white(d):
    S = d * SS; pad = 3 * SS
    im = Image.new("RGBA", (S + 2 * pad, S + 2 * pad), (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    # soft socket shadow, white ball, faint lower shading
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0)); ImageDraw.Draw(sh).ellipse((pad - SS, pad, pad + S + SS, pad + S + 2 * SS), fill=(120, 110, 150, 110))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(2 * SS)))
    g.ellipse((pad, pad, pad + S, pad + S), fill=(255, 255, 255, 255))
    shade = Image.new("RGBA", im.size, (0, 0, 0, 0)); ImageDraw.Draw(shade).ellipse((pad + S * .1, pad + S * .55, pad + S * .9, pad + S * 1.1), fill=(200, 205, 225, 90))
    mask = Image.new("L", im.size, 0); ImageDraw.Draw(mask).ellipse((pad, pad, pad + S, pad + S), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(S * .08)); shade.putalpha(Image.fromarray(np.minimum(np.array(shade)[..., 3], np.array(mask))))
    im.alpha_composite(shade)
    return im.resize((d + 6, d + 6), Image.LANCZOS)


def iris(d):
    S = d * SS
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:S, 0:S] + .5
    r = np.hypot(xx - S / 2, yy - S / 2) / (S / 2)
    a = np.clip((1 - r) * S / 2, 0, 1)
    outer = np.array([30, 105, 190]); inner = np.array([95, 190, 245])
    t = np.clip(r, 0, 1)[..., None]
    col = inner * (1 - t) + outer * t
    col = np.where((r > .88)[..., None], np.array([25, 70, 140]), col)        # limbal ring
    col = np.where((r < .46)[..., None], np.array([10, 18, 35]), col)          # pupil
    arr = np.dstack([col, a * 255]).astype(np.uint8)
    im = Image.fromarray(arr, "RGBA"); g = ImageDraw.Draw(im)
    g.ellipse((S * .22, S * .14, S * .50, S * .42), fill=(255, 255, 255, 245))   # main catchlight
    g.ellipse((S * .62, S * .62, S * .74, S * .74), fill=(255, 255, 255, 200))
    return im.resize((d, d), Image.LANCZOS)


def mouth(open_t, w):
    """0 = closed smile, 1 = wide open with tongue (desktop's friendly open smile)."""
    S = w * SS; Hh = int(S * .8)
    im = Image.new("RGBA", (S, Hh), (0, 0, 0, 0)); g = ImageDraw.Draw(im)
    top = Hh * .18
    depth = Hh * (.16 + .6 * open_t)
    lw = max(int(SS * 2.2), 1)
    if open_t < .05:
        g.arc((S * .12, top - Hh * .30, S * .88, top + Hh * .38), 25, 155, fill=(52, 30, 40, 255), width=lw + SS)
        return im.resize((w, int(w * .8)), Image.LANCZOS)
    # D-shaped open smile: flat-ish top edge, round bottom.
    poly = []
    for i in range(41):
        f = i / 40; x = S * (.14 + .72 * f)
        poly.append((x, top + Hh * .05 * math.sin(f * math.pi)))
    for i in range(41):
        f = 1 - i / 40; x = S * (.14 + .72 * f)
        poly.append((x, top + depth * math.sin(f * math.pi) ** .8))
    g.polygon(poly, fill=(48, 18, 30, 255))
    # tongue
    tw = S * .40; th = depth * .55
    tongue = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(tongue).ellipse((S / 2 - tw / 2, top + depth - th * .9, S / 2 + tw / 2, top + depth + th * .6), fill=(236, 110, 140, 255))
    mask = Image.new("L", im.size, 0); ImageDraw.Draw(mask).polygon(poly, fill=255)
    tongue.putalpha(Image.fromarray(np.minimum(np.array(tongue)[..., 3], np.array(mask))))
    im.alpha_composite(tongue)
    ImageDraw.Draw(im).line(poly + [poly[0]], fill=(40, 20, 30, 255), width=lw)
    return im.resize((w, int(w * .8)), Image.LANCZOS)


def sparkle(d):
    S = d * SS; im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); g = ImageDraw.Draw(im)
    c = S / 2
    for ang in (0, 90, 45, 135):
        r = S * (.48 if ang % 90 == 0 else .28); a = math.radians(ang)
        g.line((c - r * math.cos(a), c - r * math.sin(a), c + r * math.cos(a), c + r * math.sin(a)), fill=(210, 230, 160, 255), width=SS)
    g.ellipse((c - SS * 1.2, c - SS * 1.2, c + SS * 1.2, c + SS * 1.2), fill=(255, 255, 220, 255))
    return im.resize((d, d), Image.LANCZOS)


# ── output ──────────────────────────────────────────────────────────────────
def rgb565a8(img):
    a = np.array(img.convert("RGBA"), dtype=np.uint16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    px = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    return px.astype("<u2").tobytes() + al.astype(np.uint8).tobytes()


def c_image(name, img):
    data = rgb565a8(img); w, h = img.size
    hexes = ",".join(f"0x{b:02x}" for b in data)
    lines = [hexes[i:i + 120] for i in range(0, len(hexes), 120)]
    return (f"static const uint8_t {name}_map[] = {{\n" + "\n".join(lines) + "\n};\n"
            f"const lv_image_dsc_t {name} = {{\n"
            f"    .header = {{.magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_RGB565A8, .w = {w}, .h = {h}, .stride = {w * 2}}},\n"
            f"    .data_size = sizeof({name}_map), .data = {name}_map,\n}};\n\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    big, _ = render_body()
    body = big.resize((big.width // SS, big.height // SS), Image.LANCZOS)
    lay = face_layout(*body.size)
    body = paint_blush(body, lay)
    eye_d = int(round(lay["eye_d"])); iris_d = int(round(eye_d * .76)); mouth_w = int(round(body.height * .19))
    sprites = {"ghost_art_body": body, "ghost_art_eye": eye_white(eye_d), "ghost_art_iris": iris(iris_d),
               "ghost_art_sparkle": sparkle(11)}
    for i in range(6):
        sprites[f"ghost_art_mouth{i}"] = mouth(i / 5, mouth_w)

    names = list(sprites)
    hdr = ["#pragma once", '#include "lvgl.h"', "// Generated by tools/render_ghost_art.py - do not edit.", ""]
    for n in names:
        hdr.append(f"extern const lv_image_dsc_t {n};")
    hdr += ["", f"#define GHOST_ART_EYE_Y {int(round(lay['eye_y']))}", f"#define GHOST_ART_EYE_DX {int(round(lay['eye_dx']))}",
            f"#define GHOST_ART_MOUTH_Y {int(round(lay['mouth_y']))}", f"#define GHOST_ART_BROW_Y {int(round(lay['brow_y']))}",
            f"#define GHOST_ART_CX {int(round(lay['cx']))}", f"#define GHOST_ART_MOUTHS 6", ""]
    (MAIN / "ghost_art.h").write_text("\n".join(hdr))
    src = '#include "ghost_art.h"\n// Generated by tools/render_ghost_art.py from the desktop avatar profile - do not edit.\n\n'
    src += "".join(c_image(n, sprites[n]) for n in names)
    (MAIN / "ghost_art.c").write_text(src)
    print({n: sprites[n].size for n in names}, "bytes", sum(len(rgb565a8(s)) for s in sprites.values()))

    if args.preview:
        canvas = Image.new("RGBA", (480, 480), BG + (255,))
        for i, (x, y) in enumerate([(40, 70), (430, 110), (60, 330), (420, 300), (120, 40), (370, 420), (30, 200), (455, 220)]):
            canvas.alpha_composite(sprites["ghost_art_sparkle"], (x, y))
        ox = (480 - body.width) // 2; oy = 40
        canvas.alpha_composite(body, (ox, oy))
        e, ir = sprites["ghost_art_eye"], sprites["ghost_art_iris"]
        for s in (-1, 1):
            ex = ox + int(lay["cx"] + s * lay["eye_dx"]); ey = oy + int(lay["eye_y"])
            canvas.alpha_composite(e, (ex - e.width // 2, ey - e.height // 2))
            canvas.alpha_composite(ir, (ex - ir.width // 2 + 1, ey - ir.height // 2 + 1))
            bw = eye_d * .7; by = oy + lay["brow_y"]
            ImageDraw.Draw(canvas).arc((ex - bw / 2, by, ex + bw / 2, by + eye_d * .5), 200, 340, fill=(180, 174, 200, 255), width=3)
        m = sprites["ghost_art_mouth3"]
        canvas.alpha_composite(m, (ox + int(lay["cx"]) - m.width // 2, oy + int(lay["mouth_y"]) - m.height // 4))
        strip = Image.new("RGBA", (6 * (mouth_w + 8), m.height + 8), (235, 233, 245, 255))
        for i in range(6):
            strip.alpha_composite(sprites[f"ghost_art_mouth{i}"], (4 + i * (mouth_w + 8), 4))
        sheet = Image.new("RGBA", (480, 480 + strip.height), BG + (255,)); sheet.paste(canvas, (0, 0)); sheet.paste(strip, (0, 480))
        sheet.convert("RGB").save(args.preview)


if __name__ == "__main__":
    main()
