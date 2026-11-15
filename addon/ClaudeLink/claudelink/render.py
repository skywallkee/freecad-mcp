"""Presentation renders without external programs: a small numpy rasterizer with soft key/fill/rim lighting,
plastic material, cast shadows on a ground plane (shadow map with soft filtering), screen-space ambient
occlusion and 2x supersampling. Works in the GUI and headless; writes PNG itself (zlib)."""
import base64
import math
import os
import struct
import time
import zlib

import FreeCAD

from .server import UserError
from .snapshot import fmt

V = FreeCAD.Vector


def _doc(name=None):
    from .handlers import _doc as d
    return d(name)


def _png_bytes(rgb):
    import numpy as np
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _mesh(objs, defl):
    """Per-face tessellation (smooth inside a face, crisp across edges): verts, normals, tris, colour index."""
    import numpy as np
    from .analysis import global_shape
    P, N, T, C = [], [], [], []
    nv = 0
    for k, (o, rgb) in enumerate(objs):
        s = global_shape(o)
        for f in s.Faces:
            try:
                pts, tris = f.tessellate(defl)
            except Exception:
                continue
            if not tris:
                continue
            p = np.array([(q.x, q.y, q.z) for q in pts], float)
            t = np.array(tris, int)
            a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
            fn = np.cross(b - a, c - a)
            vn = np.zeros_like(p)
            for j in range(3):
                np.add.at(vn, t[:, j], fn)
            try:
                u0, u1, v0, v1 = f.ParameterRange
                ref = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                mean = vn.sum(0)
                if mean @ np.array([ref.x, ref.y, ref.z]) < 0:      # triangle winding opposite to the face normal
                    t = t[:, ::-1]
                    vn = -vn
            except Exception:
                pass
            vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
            P.append(p)
            N.append(vn)
            T.append(t + nv)
            C.append(np.full(len(t), k))
            nv += len(p)
    if not P:
        raise UserError("nothing to render")
    return np.vstack(P), np.vstack(N), np.vstack(T), np.concatenate(C)


def _raster(xy, z, tris, W, H, chunk=2_000_000):
    """Scanline z-buffer: nearest triangle per pixel centre -> (depth, tri id, b1, b2); only pixels inside each
    triangle are generated, so long thin triangles cost what they cover."""
    import numpy as np
    depth = np.full(W * H, np.inf)
    tid = np.full(W * H, -1, int)
    bb1 = np.zeros(W * H)
    bb2 = np.zeros(W * H)
    A, B, Cc = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    area = (B[:, 0] - A[:, 0]) * (Cc[:, 1] - A[:, 1]) - (B[:, 1] - A[:, 1]) * (Cc[:, 0] - A[:, 0])
    ymin = np.minimum(np.minimum(A[:, 1], B[:, 1]), Cc[:, 1])
    ymax = np.maximum(np.maximum(A[:, 1], B[:, 1]), Cc[:, 1])
    ys = np.clip(np.ceil(ymin - 0.5), 0, H).astype(int)
    ye = np.clip(np.floor(ymax - 0.5), -1, H - 1).astype(int)
    hts = np.where(np.abs(area) > 1e-9, np.maximum(ye - ys + 1, 0), 0)
    ids = np.nonzero(hts)[0]
    if not len(ids):
        return depth.reshape(H, W), tid.reshape(H, W), bb1.reshape(H, W), bb2.reshape(H, W)
    # chunks of triangles with a bounded number of rows
    cum = np.cumsum(hts[ids])
    starts = [0]
    lim = chunk // 8
    while starts[-1] < len(ids):
        base = cum[starts[-1] - 1] if starts[-1] else 0
        nxt = int(np.searchsorted(cum, base + lim, side="right"))
        starts.append(max(nxt, starts[-1] + 1))
    for s0, s1 in zip(starts[:-1], starts[1:]):
        ii = ids[s0:s1]
        h = hts[ii]
        tr = np.repeat(ii, h)
        off = np.repeat(np.cumsum(h) - h, h)
        rows = ys[tr] + (np.arange(len(tr)) - off)
        cy = rows + 0.5
        xl = np.full(len(tr), np.inf)
        xr = np.full(len(tr), -np.inf)
        for P0, P1 in ((A, B), (B, Cc), (Cc, A)):
            p0, p1 = P0[tr], P1[tr]
            dy = p1[:, 1] - p0[:, 1]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (cy - p0[:, 1]) / dy
            ok = (np.abs(dy) > 1e-12) & (t >= -1e-9) & (t <= 1 + 1e-9)
            x = p0[:, 0] + np.where(ok, t, 0) * (p1[:, 0] - p0[:, 0])
            xl = np.where(ok, np.minimum(xl, x), xl)
            xr = np.where(ok, np.maximum(xr, x), xr)
        xs = np.clip(np.ceil(xl - 0.5), 0, W).astype(np.int64)
        xe = np.clip(np.floor(xr - 0.5), -1, W - 1).astype(np.int64)
        ln = np.where(np.isfinite(xl), np.maximum(xe - xs + 1, 0), 0)
        if ln.sum() == 0:
            continue
        f_row = np.repeat(np.arange(len(tr)), ln)
        f_off = np.repeat(np.cumsum(ln) - ln, ln)
        px = xs[f_row] + (np.arange(len(f_row)) - f_off)
        py = rows[f_row]
        ft = tr[f_row]
        cx, cyy = px + 0.5, py + 0.5
        a, b, c = A[ft], B[ft], Cc[ft]
        ar = area[ft]
        w1 = ((cx - a[:, 0]) * (c[:, 1] - a[:, 1]) - (cyy - a[:, 1]) * (c[:, 0] - a[:, 0])) / ar
        w2 = ((b[:, 0] - a[:, 0]) * (cyy - a[:, 1]) - (b[:, 1] - a[:, 1]) * (cx - a[:, 0])) / ar
        w0 = 1 - w1 - w2
        zz = w0 * z[tris[ft, 0]] + w1 * z[tris[ft, 1]] + w2 * z[tris[ft, 2]]
        pix = py * W + px
        order = np.lexsort((zz, pix))
        pix, zz, ft, w1, w2 = pix[order], zz[order], ft[order], w1[order], w2[order]
        first = np.ones(len(pix), bool)
        first[1:] = pix[1:] != pix[:-1]
        pix, zz, ft, w1, w2 = pix[first], zz[first], ft[first], w1[first], w2[first]
        better = zz < depth[pix]
        pix = pix[better]
        depth[pix] = zz[better]
        tid[pix] = ft[better]
        bb1[pix] = w1[better]
        bb2[pix] = w2[better]
    return depth.reshape(H, W), tid.reshape(H, W), bb1.reshape(H, W), bb2.reshape(H, W)


def _blur(a, r):
    import numpy as np
    if r < 1:
        return a
    k = 2 * r + 1
    c = np.cumsum(np.pad(a, ((r + 1, r), (0, 0)), mode="edge"), axis=0)
    a = (c[k:] - c[:-k]) / k
    c = np.cumsum(np.pad(a, ((0, 0), (r + 1, r)), mode="edge"), axis=1)
    return (c[:, k:] - c[:, :-k]) / k


def _basis(d):
    import numpy as np
    d = d / np.linalg.norm(d)
    up = np.array([0, 0, 1.0]) if abs(d[2]) < 0.999 else np.array([0, 1.0, 0])
    x = np.cross(d, up)
    x /= np.linalg.norm(x)
    y = np.cross(x, d)
    return x, y, d


def _shift(a, dy, dx, fill):
    import numpy as np
    out = np.full_like(a, fill)
    H, W = a.shape
    ys, yd = (slice(0, H - dy), slice(dy, H)) if dy >= 0 else (slice(-dy, H), slice(0, H + dy))
    xs, xd = (slice(0, W - dx), slice(dx, W)) if dx >= 0 else (slice(-dx, W), slice(0, W + dx))
    out[yd, xd] = a[ys, xs]
    return out


def render(objects=None, view="iso", size=900, aspect=1.33, shadows=True, ao=True, edges=False, colors=None,
           background="studio", out=None, doc=None, image=True, quality="normal", _array=False):
    """See the MCP tool."""
    import numpy as np
    from .analysis import _resolve_objects
    from .illustrate import PALETTE, _is_grey, VIEWS
    t0 = time.time()
    d = _doc(doc)
    objs = _resolve_objects(d, objects)
    if not objs:
        raise UserError("no solids to render")
    cols = []
    same = len(objs) > 1
    raw = []
    for o in objs:
        rgb = None
        if colors and o.Name in colors:
            rgb = tuple(colors[o.Name])
        else:
            from . import cl
            rgb = cl.color_of(o)
        raw.append(rgb)
    if len({r for r in raw}) > 1:
        same = False
    for k, (o, rgb) in enumerate(zip(objs, raw)):
        if rgb is None or _is_grey(rgb) or (same and not colors):
            rgb = PALETTE[k % len(PALETTE)]
        cols.append((o, rgb))
    # geometry
    bb = FreeCAD.BoundBox()
    for o in objs:
        bb.add(o.Shape.BoundBox)
    diag = bb.DiagonalLength
    defl = diag / (450.0 if quality == "normal" else 900.0)
    P, N, T, C = _mesh(cols, defl)
    if isinstance(view, str):
        if view.startswith("dir:"):
            dv = np.array([float(x) for x in view[4:].split(",")])
        elif view in VIEWS:
            dv = np.array(VIEWS[view], float)
        else:
            raise UserError("unknown view %r" % view)
    else:
        dv = np.array(view, float)
    xc, yc, dc = _basis(dv)
    ss = 2
    W = int(size * aspect) * ss
    H = int(size) * ss
    px, py, pz = P @ xc, P @ yc, P @ dc
    zg = bb.ZMin
    # fit: model + a bit of ground for the shadow
    mx = 0.10
    span = max((px.max() - px.min()) / (W * (1 - 2 * mx)), (py.max() - py.min()) / (H * (1 - 2 * mx)))
    cx, cy = (px.max() + px.min()) / 2, (py.max() + py.min()) / 2
    sx = (px - cx) / span + W / 2
    sy = H / 2 - (py - cy) / span
    xy = np.c_[sx, sy]
    depth, tid, b1, b2 = _raster(xy, pz, T, W, H)
    hit = tid >= 0
    # per-pixel normal, colour, world position
    ti = np.where(hit, tid, 0)
    w0 = 1 - b1 - b2
    n = (w0[..., None] * N[T[ti, 0]] + b1[..., None] * N[T[ti, 1]] + b2[..., None] * N[T[ti, 2]])
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
    back = (n @ dc) > 0                       # seeing the inside of a face (open/cut geometry): flip
    n[back] = -n[back]
    base = np.array([c for _, c in cols])[C[ti]]
    gy, gx = np.mgrid[0:H, 0:W]
    wx = (gx + 0.5 - W / 2) * span + cx
    wy = (H / 2 - (gy + 0.5)) * span + cy
    world = wx[..., None] * xc + wy[..., None] * yc + np.where(hit, depth, 0)[..., None] * dc
    # ground plane points for background pixels (views from above only)
    ground = np.zeros((H, W), bool)
    gdepth = np.full((H, W), np.inf)
    if dc[2] < -0.05:
        o0 = wx[..., None] * xc + wy[..., None] * yc
        tg = (zg - o0[..., 2]) / dc[2]
        gpos = o0 + tg[..., None] * dc
        ground = ~hit
        gdepth = tg
        world = np.where(ground[..., None], gpos, world)
    # lights (relative to the camera, so every view is lit the same way)
    up = np.array([0, 0, 1.0])
    key = -0.55 * xc + 0.75 * up - 0.45 * dc
    key /= np.linalg.norm(key)
    fill = 0.6 * xc + 0.25 * up - 0.6 * dc
    fill /= np.linalg.norm(fill)
    rim = 0.2 * xc + 0.5 * up + 0.85 * dc
    rim /= np.linalg.norm(rim)
    # shadow map from the key light
    shadow = np.ones((H, W))
    if shadows:
        lx, ly, ld = _basis(-key)
        SM = 1600 if quality == "normal" else 2400
        qx, qy, qz = P @ lx, P @ ly, P @ ld
        lspan = max(qx.max() - qx.min(), qy.max() - qy.min()) / (SM * 0.9)
        lcx, lcy = (qx.max() + qx.min()) / 2, (qy.max() + qy.min()) / 2
        lxy = np.c_[(qx - lcx) / lspan + SM / 2, SM / 2 - (qy - lcy) / lspan]
        sdepth, _, _, _ = _raster(lxy, qz, T, SM, SM)
        recv = hit | ground
        wp = world[recv]
        ux = (wp @ lx - lcx) / lspan + SM / 2
        uy = SM / 2 - (wp @ ly - lcy) / lspan
        uz = wp @ ld
        bias = diag * 0.004
        lit = np.zeros(len(wp))
        taps = [(dx, dy) for dx in (-1.5, 0, 1.5) for dy in (-1.5, 0, 1.5)]
        for dx, dy in taps:                       # soft (PCF) filtering
            ix = np.clip((ux + dx).astype(int), 0, SM - 1)
            iy = np.clip((uy + dy).astype(int), 0, SM - 1)
            inside = (ux + dx >= 0) & (ux + dx < SM) & (uy + dy >= 0) & (uy + dy < SM)
            occ = sdepth[iy, ix] < uz - bias
            lit += np.where(inside, ~occ, True)
        shadow[recv] = lit / len(taps)
    # ambient occlusion (screen space)
    occl = np.ones((H, W))
    if ao:
        Dm = np.where(hit, depth, np.where(ground, gdepth, np.inf))
        nn = np.where(hit[..., None], n, np.array([0, 0, 1.0]))
        ndc = np.minimum(nn @ dc, -0.15)                    # avoid blow-up at grazing angles
        gxd = -span * (nn @ xc) / ndc                         # depth change per pixel to the right
        gyd = span * (nn @ yc) / ndc                          # ... per pixel down
        rng = diag * 0.05
        acc = np.zeros((H, W))
        cnt = 0
        for r in (0.006, 0.015, 0.035):
            rp = max(1, int(r * H))
            for k in range(8):
                a = 2 * math.pi * (k + 0.5 * (r * 1000 % 2)) / 8
                dy, dx = int(round(rp * math.sin(a))), int(round(rp * math.cos(a)))
                Ds = _shift(Dm, dy, dx, np.inf)
                exp = Dm - gxd * dx - gyd * dy     # _shift brings the pixel at (-dy, -dx): its depth on a flat surface
                diff = exp - Ds                                 # > 0: something sticks out towards the camera
                acc += np.clip(diff / (rng * 0.35), 0, 1) * (diff < rng)
                cnt += 1
        occl = 1 - 0.8 * np.clip(_blur(acc / cnt, max(1, int(0.004 * H))) * 2.0, 0, 1)
        occl = np.where(np.isfinite(Dm), occl, 1.0)
    # shading
    ndl = np.clip(n @ key, 0, 1)
    ndf = np.clip(n @ fill, 0, 1)
    ndr = np.clip(n @ rim, 0, 1) ** 2
    h = key - dc
    h /= np.linalg.norm(h)
    spec = np.clip(n @ h, 0, 1) ** 48
    lin = base ** 2.2
    col = lin * (0.16 * occl[..., None] + 0.95 * (ndl * shadow)[..., None] + 0.28 * ndf[..., None]) + \
        0.35 * (spec * shadow)[..., None] + 0.18 * ndr[..., None]
    # background: soft studio gradient, ground darkened by shadow and AO
    t_ = (gy / float(H))[..., None]
    if background == "white":
        bg = np.ones((H, W, 3))
    else:
        bg = (1 - t_) * np.array([0.93, 0.94, 0.96]) + t_ * np.array([0.80, 0.82, 0.86])
    bgl = bg ** 2.2
    gfac = (0.55 + 0.45 * shadow) * occl
    bgl = np.where(ground[..., None], bgl * gfac[..., None], bgl)
    img = np.where(hit[..., None], col, bgl)
    if edges:
        Dm = np.where(hit, depth, np.inf)
        e = np.zeros((H, W), bool)
        for dy, dx in ((0, 1), (1, 0)):
            Ds = _shift(Dm, dy, dx, np.inf)
            ns = _shift(n.reshape(H, W, 3)[..., 0], dy, dx, 0)
            e |= (np.abs(np.nan_to_num(Dm - Ds, posinf=1e9, neginf=1e9)) > diag * 0.01)
            e |= hit & (np.abs(n[..., 0] - ns) > 0.35)
        img = np.where(e[..., None], img * 0.35, img)
    img = np.clip(img, 0, 1) ** (1 / 2.2)
    img = img.reshape(H // ss, ss, W // ss, ss, 3).mean(axis=(1, 3))     # 2x supersampling
    rgb = (img * 255 + 0.5).astype(np.uint8)
    if _array:
        return rgb
    data = _png_bytes(rgb)
    if out is None:
        if d.FileName:
            folder = os.path.join(os.path.dirname(d.FileName), "images")
        else:
            base_c = FreeCAD.getUserCachePath() if hasattr(FreeCAD, "getUserCachePath") else os.environ.get("TEMP", ".")
            folder = os.path.join(base_c, "ClaudeLink", "renders")
        os.makedirs(folder, exist_ok=True)
        vn = view if isinstance(view, str) and not view.startswith("dir:") else "view"
        out = os.path.join(folder, "%s_render_%s.png" % (d.Name.lower(), vn))
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    text = "%s: rendered %d object(s), %d triangles, %dx%d px (%s%s%s) -> %s (%.1fs)" % (
        d.Name, len(objs), len(T), W // ss, H // ss, "shadows" if shadows else "no shadows", ", AO" if ao else "",
        ", edges" if edges else "", out, time.time() - t0)
    if not image:
        return text
    return {"text": text, "image_b64": base64.b64encode(data).decode("ascii"), "mime": "image/png"}


def sheet(doc=None, views=None, size=380, objects=None, highlight=None):
    """Contact sheet for freecad_view when there is no window: one software-rendered tile per view."""
    import numpy as np
    t0 = time.time()
    d = _doc(doc)
    views = views or ["iso", "front", "top", "right"]
    size = max(120, min(int(size), 1000))
    alias = {"dimetric": "iso", "trimetric": "iso", "isometric": "iso"}
    cols = {k: (0.15, 0.85, 0.35) for k in (highlight or [])}
    tiles = []
    for v in views:
        tiles.append(render(objects=objects, view=alias.get(v, v), size=size, aspect=1.0, shadows=False, ao=True,
                            edges=True, colors=cols or None, background="white", doc=d.Name, _array=True))
    nc = 1 if len(tiles) == 1 else 2 if len(tiles) <= 4 else 3
    nr = (len(tiles) + nc - 1) // nc
    out = np.full((nr * size, nc * size, 3), 255, np.uint8)
    for i, t in enumerate(tiles):
        y, x = (i // nc) * size, (i % nc) * size
        t = t[:size, :size]
        out[y:y + t.shape[0], x:x + t.shape[1]] = t
        out[y:y + size, x] = 200
        out[y, x:x + size] = 200
    text = "%s: %s, %dx%d px (windowless render, tiles left to right then down%s) (%.1fs)" % (
        d.Name, ", ".join(views), out.shape[1], out.shape[0],
        (", isolated " + ", ".join(objects)) if objects else "", time.time() - t0)
    return {"text": text, "image_b64": base64.b64encode(_png_bytes(out)).decode("ascii"), "mime": "image/png"}


METHODS = {"render": render}
