#!/usr/bin/env python3
"""
One-time geometry build for the dashboard maps.

Outputs (committed to the repo, read by build_dashboard.py every day):
  geo/states.json     - every state pre-projected to an Albers USA-style
                        layout (lower 48 + Alaska/Hawaii insets) as SVG path
                        strings in a 960 x 600 coordinate space.
  geo/house_hex.json  - a hexagon cartogram of all 435 House districts
                        (one hexagon per seat, 2020 apportionment), with
                        each state's seats grouped into one contiguous blob
                        placed near its real location, plus state border
                        segments and label positions.

Why pre-project: the dashboard then needs zero network requests and zero JS
libraries to draw a map, so it renders anywhere, including a file opened
straight from disk. Why a hex cartogram for the House: every seat counts the
same toward the 218 majority, so every seat gets the same area - on a true
geographic map, rural districts the size of Montana would dominate and
city districts would be invisible. No public source publishes the 2026
district boundaries for the ten states that redrew mid-decade, so the hex
layout also avoids drawing stale lines for those states. Positions of
individual districts inside a state are schematic.

Source: Natural Earth 1:50m admin-1 (public domain), US subset stored at
geo/source/ne_50m_us_states.geojson.

Run: python3 tools/build_geometry.py
Pure Python + stdlib (math/json/heapq): no geo libraries needed.
"""
import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "geo" / "source" / "ne_50m_us_states.geojson"
OUT_STATES = ROOT / "geo" / "states.json"
OUT_HEX = ROOT / "geo" / "house_hex.json"

W, H = 960, 600

# 2020 census apportionment (in effect 2022-2030)
SEATS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5, "DE": 1,
    "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9, "IA": 4, "KS": 4,
    "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9, "MI": 13, "MN": 8, "MS": 4,
    "MO": 8, "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12, "NM": 3, "NY": 26,
    "NC": 14, "ND": 1, "OH": 15, "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7,
    "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2,
    "WI": 8, "WY": 1,
}
assert sum(SEATS.values()) == 435


# --------------------------------------------------------------- projection
def albers(lon0, lat0, lat1, lat2):
    p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
    n = (math.sin(p1) + math.sin(p2)) / 2
    c = math.cos(p1) ** 2 + 2 * n * math.sin(p1)
    rho0 = math.sqrt(c - 2 * n * math.sin(p0)) / n

    def f(lon, lat):
        theta = n * math.radians(lon - lon0)
        rho = math.sqrt(max(c - 2 * n * math.sin(math.radians(lat)), 0)) / n
        return rho * math.sin(theta), -(rho0 - rho * math.cos(theta))

    return f


PROJ_48 = albers(-96, 37.5, 29.5, 45.5)
PROJ_AK = albers(-154, 50, 55, 65)
PROJ_HI = albers(-157, 3, 8, 18)


def rings_of(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    return geom["coordinates"]


def ring_area(pts):
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2


def ring_centroid(pts):
    a = ring_area(pts)
    if abs(a) < 1e-12:
        xs, ys = zip(*pts)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        cr = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    return cx / (6 * a), cy / (6 * a)


def dp_simplify(pts, tol):
    """Douglas-Peucker; closed rings are split at the vertex farthest from the
    start so neither half has a degenerate (zero-length) baseline."""
    if len(pts) < 4:
        return pts
    if pts[0] == pts[-1]:
        x0, y0 = pts[0]
        far = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
        a = _dp(pts[: far + 1], tol)
        b = _dp(pts[far:], tol)
        return a[:-1] + b
    return _dp(pts, tol)


def _dp(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        x1, y1 = pts[i]
        x2, y2 = pts[j]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-9
        best, idx = -1.0, None
        for k in range(i + 1, j):
            x0, y0 = pts[k]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


def load_states():
    gj = json.loads(SRC.read_text())
    out = {}
    for f in gj["features"]:
        code = f["properties"]["postal"]
        if code not in SEATS:
            continue  # skips DC
        proj = PROJ_AK if code == "AK" else PROJ_HI if code == "HI" else PROJ_48
        polys = []
        for poly in rings_of(f["geometry"]):
            if code == "HI" and min(p[0] for p in poly[0]) < -170:
                continue  # Midway / far NW islands would blow up the inset
            fix = (lambda lon: lon - 360 if lon > 0 else lon) if code == "AK" else (lambda lon: lon)
            polys.append([[proj(fix(lon), lat) for lon, lat in ring] for ring in poly])
        out[code] = polys
    return out


def fit_transform(states):
    # scale + translate the lower 48 into the canvas, then place insets
    xs, ys = [], []
    for code, polys in states.items():
        if code in ("AK", "HI"):
            continue
        for poly in polys:
            for x, y in poly[0]:
                xs.append(x)
                ys.append(y)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = min((W - 40) / (maxx - minx), (H - 60) / (maxy - miny))
    ox = (W - (maxx - minx) * s) / 2 - minx * s
    oy = 14 - miny * s

    def t48(p):
        return (p[0] * s + ox, p[1] * s + oy)

    def inset(code, scale_factor, anchor):
        pts = [p for poly in states[code] for p in poly[0]]
        ixs, iys = zip(*pts)
        cx, cy = (min(ixs) + max(ixs)) / 2, (min(iys) + max(iys)) / 2
        k = s * scale_factor

        def t(p):
            return ((p[0] - cx) * k + anchor[0], (p[1] - cy) * k + anchor[1])

        return t

    tf = {code: t48 for code in states}
    tf["AK"] = inset("AK", 0.34, (118, H - 78))
    tf["HI"] = inset("HI", 0.95, (268, H - 52))
    return tf


def build_states():
    states = load_states()
    tf = fit_transform(states)
    result = {}
    for code, polys in states.items():
        t = tf[code]
        parts, largest, largest_area = [], None, 0.0
        for poly in polys:
            for ri, ring in enumerate(poly):
                pts = [t(p) for p in ring]
                a = abs(ring_area(pts))
                if a < 1.5 and ri == 0 and code not in ("HI", "RI", "DE"):
                    continue
                pts = dp_simplify(pts, 0.3)
                if len(pts) < 4:
                    continue
                if ri == 0 and a > largest_area:
                    largest, largest_area = pts, a
                parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts[:-1]) + "Z")
        cx, cy = ring_centroid(largest)
        allpts = [t(p) for poly in polys for p in poly[0]]
        bx, by = zip(*allpts)
        result[code] = {
            "d": "".join(parts),
            "cx": round(cx, 1),
            "cy": round(cy, 1),
            "bbox": [round(min(bx), 1), round(min(by), 1), round(max(bx), 1), round(max(by), 1)],
            "area": round(largest_area, 1),
        }
    # hand-tuned label nudges for states whose centroid sits badly
    nudge = {"FL": (12, 6), "LA": (-10, -4), "MI": (12, 16), "CA": (-8, 4),
             "KY": (6, 2), "VA": (8, 2), "ID": (0, 14), "MN": (-4, 6), "NY": (6, 2),
             "WV": (-2, 4), "HI": (-20, -8), "AK": (0, 0), "MD": (-6, -6)}
    for code, (dx, dy) in nudge.items():
        result[code]["cx"] = round(result[code]["cx"] + dx, 1)
        result[code]["cy"] = round(result[code]["cy"] + dy, 1)
    return result


# ------------------------------------------------------------ hex cartogram
SQ3 = math.sqrt(3)
NEIGH = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]


def hex_center(q, r, size):
    return size * SQ3 * (q + r / 2), size * 1.5 * r


def pixel_to_hex(x, y, size):
    q = (SQ3 / 3 * x - y / 3) / size
    r = (2 / 3 * y) / size
    # cube round
    cx, cz = q, r
    cy = -cx - cz
    rx, ry, rz = round(cx), round(cy), round(cz)
    dx, dy, dz = abs(rx - cx), abs(ry - cy), abs(rz - cz)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return rx, rz


def dorling(centers, radii, iters=900, gravity=0.0025):
    pos = {k: list(v) for k, v in centers.items()}
    keys = list(pos)
    for it in range(iters):
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                d = math.hypot(dx, dy) or 1e-6
                need = radii[a] + radii[b] + 0.5
                if d < need:
                    push = (need - d) / 2
                    ux, uy = dx / d, dy / d
                    wa = radii[b] / (radii[a] + radii[b])
                    wb = 1 - wa
                    pos[a][0] -= ux * push * wa * 2
                    pos[a][1] -= uy * push * wa * 2
                    pos[b][0] += ux * push * wb * 2
                    pos[b][1] += uy * push * wb * 2
        k = 0.012 if it < iters * 0.7 else 0.004
        mx = sum(p[0] for p in pos.values()) / len(pos)
        my = sum(p[1] for p in pos.values()) / len(pos)
        for a in keys:
            pos[a][0] += (centers[a][0] - pos[a][0]) * k
            pos[a][1] += (centers[a][1] - pos[a][1]) * k
            # gentle packing pull toward the middle closes the "archipelago"
            # gaps in the sparse West without reordering neighbours
            pos[a][0] += (mx - pos[a][0]) * gravity
            pos[a][1] += (my - pos[a][1]) * gravity
    return pos


def build_hex(states_geo):
    lower = [c for c in SEATS if c not in ("AK", "HI")]
    # geographic anchor = label centroid of each state on the 960x600 map
    centers = {c: (states_geo[c]["cx"], states_geo[c]["cy"]) for c in lower}
    # anchor nudges (pixels on the 960x600 map) so crowded East Coast states
    # keep their real north/south order after the circles push apart
    anchor_nudge = {"VA": (-34, -6), "NC": (-10, 16), "SC": (-4, 16), "GA": (-6, 10),
                    "MD": (2, -20), "DE": (6, -6), "WV": (-8, -8), "KY": (-8, 0),
                    "NJ": (8, -4), "MI": (10, 10)}
    for c, (dx, dy) in anchor_nudge.items():
        centers[c] = (centers[c][0] + dx, centers[c][1] + dy)
    kscale = 12.5  # circle radius per sqrt(seat); tuned by eye (see preview)
    radii = {c: kscale * math.sqrt(SEATS[c]) for c in lower}
    pos = dorling(centers, radii)

    size = kscale * math.sqrt(2 * math.pi / (3 * SQ3)) * 0.98
    taken = {}
    need = dict(SEATS)
    region = {c: [] for c in SEATS}
    frontier = {c: [] for c in SEATS}

    def place_seed(c, x, y):
        q, r = pixel_to_hex(x, y, size)
        # walk outward to the nearest free cell
        ring = [(q, r)]
        seen = {(q, r)}
        while ring:
            nxt = []
            for cell in ring:
                if cell not in taken:
                    return cell
                for dq, dr in NEIGH:
                    nb = (cell[0] + dq, cell[1] + dr)
                    if nb not in seen:
                        seen.add(nb)
                        nxt.append(nb)
            ring = nxt

    def claim(c, cell):
        taken[cell] = c
        region[c].append(cell)
        need[c] -= 1
        cx, cy = pos[c] if c in pos else anchor_px[c]
        for dq, dr in NEIGH:
            nb = (cell[0] + dq, cell[1] + dr)
            if nb not in taken:
                hx, hy = hex_center(nb[0], nb[1], size)
                heapq.heappush(frontier[c], (math.hypot(hx - cx, hy - cy), nb))

    anchor_px = {}
    order = sorted(lower, key=lambda c: -SEATS[c])
    for c in order:
        claim(c, place_seed(c, *pos[c]))

    # round-robin growth: the state furthest from full grows next
    while any(need[c] > 0 for c in lower):
        c = max((c for c in lower if need[c] > 0), key=lambda c: need[c] / SEATS[c])
        grown = False
        while frontier[c]:
            _, cell = heapq.heappop(frontier[c])
            if cell not in taken:
                claim(c, cell)
                grown = True
                break
        if not grown:
            # boxed in: re-seed at nearest free cell (rare; keeps totals exact)
            claim(c, place_seed(c, *pos[c]))

    # Alaska / Hawaii below the lower 48 on the left, like the map insets
    ys = [hex_center(q, r, size)[1] for q, r in taken]
    xs = [hex_center(q, r, size)[0] for q, r in taken]
    miny, maxy, minx = min(ys), max(ys), min(xs)
    base_r = pixel_to_hex(minx, maxy, size)[1] + 1
    for c, dq in (("AK", 0), ("HI", 3)):
        x0, _ = hex_center(0, base_r, size)
        q0 = pixel_to_hex(minx + dq * size * SQ3, maxy + size * 1.5, size)[0]
        cells = [(q0 + i, base_r) for i in range(SEATS[c])]
        for cell in cells:
            taken[cell] = c
            region[c].append(cell)

    # district numbering: schematic reading order inside each state
    districts = []
    for c, cells in region.items():
        cells_sorted = sorted(cells, key=lambda qr: (qr[1], qr[0] + qr[1] / 2))
        for i, (q, r) in enumerate(cells_sorted, start=1):
            x, y = hex_center(q, r, size)
            label = "AL" if SEATS[c] == 1 else str(i)
            districts.append({"id": f"{c}-{label}", "state": c, "district": label,
                              "q": q, "r": r, "x": x, "y": y})

    # normalise into a 960-wide canvas with margin
    minx = min(d["x"] for d in districts) - size
    maxx = max(d["x"] for d in districts) + size
    miny = min(d["y"] for d in districts) - size
    maxy = max(d["y"] for d in districts) + size
    k = (W - 20) / (maxx - minx)
    for d in districts:
        d["x"] = round((d["x"] - minx) * k + 10, 2)
        d["y"] = round((d["y"] - miny) * k + 10, 2)
    hsize = size * k
    height = round((maxy - miny) * k + 20, 1)

    # state border segments = hex edges whose neighbour is another state / empty
    corner = [(math.cos(math.radians(60 * i - 30)), math.sin(math.radians(60 * i - 30))) for i in range(6)]
    # edge i joins corner i and i+1; its outward neighbour direction:
    edge_dir = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]
    owner = {(d["q"], d["r"]): d["state"] for d in districts}
    segs = []
    for d in districts:
        for i, (dq, dr) in enumerate(edge_dir):
            nb = (d["q"] + dq, d["r"] + dr)
            if owner.get(nb) != d["state"]:
                a, b = corner[i], corner[(i + 1) % 6]
                segs.append(f"M{d['x'] + a[0] * hsize:.1f},{d['y'] + a[1] * hsize:.1f}"
                            f"L{d['x'] + b[0] * hsize:.1f},{d['y'] + b[1] * hsize:.1f}")
    labels = {}
    for c in SEATS:
        pts = [(d["x"], d["y"]) for d in districts if d["state"] == c]
        labels[c] = [round(sum(p[0] for p in pts) / len(pts), 1), round(sum(p[1] for p in pts) / len(pts), 1)]
    for d in districts:
        del d["q"], d["r"]
    return {"width": W, "height": height, "hex_size": round(hsize, 3),
            "districts": districts, "borders": "".join(segs), "labels": labels}


def main():
    states_geo = build_states()
    OUT_STATES.write_text(json.dumps({"width": W, "height": H, "states": states_geo}, separators=(",", ":")))
    hexmap = build_hex(states_geo)
    OUT_HEX.write_text(json.dumps(hexmap, separators=(",", ":")))
    counts = {}
    for d in hexmap["districts"]:
        counts[d["state"]] = counts.get(d["state"], 0) + 1
    assert counts == SEATS, "seat counts mismatch"
    print(f"states.json: {OUT_STATES.stat().st_size/1024:.0f} KB, house_hex.json: {OUT_HEX.stat().st_size/1024:.0f} KB,"
          f" {len(hexmap['districts'])} districts, hex canvas height {hexmap['height']}")


if __name__ == "__main__":
    main()
