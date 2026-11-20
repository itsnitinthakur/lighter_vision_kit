"""
lightergrab_cv.py - open-source replacement for the LighterGrab (Halcon) pick-up station.

What it does, per frame (same loop as the original machine):
  1. find each lighter outline in the image (edge contours)
  2. match it against the outline learned from one good lighter (any angle)
  3. check the outline for defects (broken / deformed edge longer than N px)
  4. move the saved grab point to the lighter's actual position and angle
  5. tell front side from back side (mirrored outline) and convert pixels to mm (calibration)
  6. send position, angle, front/back and OK/NG flag to the PLC over Modbus TCP (or just log, in dry-run)
  7. save a CSV row for every lighter and the image of every NG  (the original saved nothing)

Licences: OpenCV (Apache 2.0), NumPy (BSD), pymodbus (BSD). Runs fully offline.

Usage
  python lightergrab_cv.py learn  --image good.png --grab 241.9 134.5 --recipe recipes/recipe1.json
  python lightergrab_cv.py run    --recipe recipes/recipe1.json --images D:/picture2/LeftLight
  python lightergrab_cv.py run    --recipe recipes/recipe1.json --images ./test --modbus 192.168.8.4
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


# --------------------------------------------------------------------------- recipe

@dataclass
class Recipe:
    """One product type. Mirrors the fields of LighterGrab's ToolParamN.xml."""
    name: str = "recipe1"
    contour: list = field(default_factory=list)      # learned outline, points relative to its centre, angle 0
    grab_point: tuple = (0.0, 0.0)                     # grab point relative to outline centre, angle 0
    area: float = 0.0                                  # learned outline area (px^2)
    similarity_threshold: float = 0.75                 # ToolParam similarityThreshold 75 -> 0.75
    start_angle: float = -180.0                        # startAngle
    end_angle: float = 180.0                           # endAngle
    area_tolerance: float = 0.25                       # accept outlines within +-25 % of learned area
    canny_low: int = 30                                # edge contrast, ToolParam gradientThreshold analogue
    canny_high: int = 90
    blur: int = 5
    defect_check: bool = True                          # isDefectDetect
    defect_tolerance_px: float = 3.0                   # how far an edge may sit from the learned one
    max_defect_chain: int = 16                         # maxDefectChainLength
    flip_x: bool = False                               # System.xml reverseX
    flip_y: bool = False                               # System.xml reverseY
    detect_side: bool = True                           # changelog: "each camera does front/back detection"
    side_margin: float = 0.05                          # mirrored fit must be this much better to call it "back"
    mm_per_px: float = 1.0                             # calibration (标定测距); 1.0 = send pixels
    origin_px: tuple = (0.0, 0.0)                      # calibration origin in the image (pixels)
    grab_point_image_px: tuple | None = None           # grab point in the learning image (from LighterGrab)
    source: dict = field(default_factory=dict)         # original LighterGrab values, kept for reference

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Recipe":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        d["grab_point"] = tuple(d["grab_point"])
        if d.get("grab_point_image_px"):
            d["grab_point_image_px"] = tuple(d["grab_point_image_px"])
        d["origin_px"] = tuple(d.get("origin_px", (0.0, 0.0)))
        return cls(**d)


@dataclass
class Result:
    ok: bool
    x: float              # grab point in image pixels
    y: float
    angle: float          # degrees, image coordinates
    score: float          # 0..1 outline similarity
    defect_chain: int     # longest run of out-of-place edge points
    reason: str = ""
    side: str = "front"   # "front" or "back" (lighter lying the other way up = mirrored outline)
    x_mm: float = 0.0     # grab point after calibration
    y_mm: float = 0.0


# --------------------------------------------------------------------------- vision

def preprocess(img: np.ndarray, r: Recipe) -> np.ndarray:
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if r.flip_x and r.flip_y:
        img = cv2.flip(img, -1)
    elif r.flip_x:
        img = cv2.flip(img, 1)
    elif r.flip_y:
        img = cv2.flip(img, 0)
    k = max(1, r.blur | 1)
    return cv2.GaussianBlur(img, (k, k), 0)


def find_outlines(gray: np.ndarray, r: Recipe) -> list[np.ndarray]:
    edges = cv2.Canny(gray, r.canny_low, r.canny_high)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return [c for c in contours if cv2.contourArea(c) > 200]


def pose_of(contour: np.ndarray) -> tuple[float, float, float]:
    """Centre and main-axis angle (degrees) of an outline, via image moments."""
    m = cv2.moments(contour)
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    angle = 0.5 * math.degrees(math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"]))
    return cx, cy, angle


def transform(points: np.ndarray, cx: float, cy: float, angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return points @ rot.T + np.array([cx, cy])


def defect_run(template_pts: np.ndarray, found: np.ndarray, shape: tuple, tol: float) -> tuple[int, float]:
    """Longest consecutive run of template points farther than tol from the found outline,
    plus the mean distance (lower = better fit). Same idea as Halcon's defect chain length."""
    canvas = np.full(shape, 255, np.uint8)
    cv2.drawContours(canvas, [found], -1, 0, 1)
    dist = cv2.distanceTransform(canvas, cv2.DIST_L2, 3)
    pts = np.round(template_pts).astype(int)
    h, w = shape
    inside = (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
    d = np.full(len(pts), 1e6)
    d[inside] = dist[pts[inside, 1], pts[inside, 0]]
    bad = d > tol
    # longest run, wrapping round the closed outline
    run = best = 0
    for b in np.concatenate([bad, bad]):
        run = run + 1 if b else 0
        best = max(best, run)
    return min(best, len(pts)), float(np.mean(np.minimum(d, 50)))


def learn(img: np.ndarray, grab_xy: tuple[float, float], r: Recipe) -> Recipe:
    """Learn the outline from ONE good lighter (largest outline in the image)."""
    gray = preprocess(img, r)
    outlines = find_outlines(gray, r)
    if not outlines:
        raise RuntimeError("No outline found - check lighting / canny thresholds")
    c = max(outlines, key=cv2.contourArea)
    cx, cy, ang = pose_of(c)
    pts = c.reshape(-1, 2).astype(float)
    local = transform(pts - [cx, cy], 0, 0, -ang)                 # remove pose -> angle 0
    g = transform(np.array([[grab_xy[0] - cx, grab_xy[1] - cy]]), 0, 0, -ang)[0]
    step = max(1, len(local) // 400)                             # keep about 400 points
    r.contour = local[::step].round(2).tolist()
    r.grab_point = (round(float(g[0]), 2), round(float(g[1]), 2))
    r.area = float(cv2.contourArea(c))
    return r


def inspect(img: np.ndarray, r: Recipe) -> list[Result]:
    """Find every lighter in the frame and return one Result each."""
    gray = preprocess(img, r)
    tmpl = np.array(r.contour, dtype=float)
    tmpl_cnt = tmpl.astype(np.float32).reshape(-1, 1, 2)
    results = []
    for c in find_outlines(gray, r):
        area = cv2.contourArea(c)
        if not (1 - r.area_tolerance) * r.area <= area <= (1 + r.area_tolerance) * r.area:
            continue
        cx, cy, ang = pose_of(c)
        # main-axis angle is ambiguous by 180 deg, and a lighter lying back-side-up shows the
        # mirrored outline: try all four, keep the best fit
        fits = {}
        for side, t in (("front", tmpl), ("back", tmpl * [1, -1])):
            if side == "back" and not r.detect_side:
                continue
            for cand in (ang, ang + 180):
                placed = transform(t, cx, cy, cand)
                chain, mean_d = defect_run(placed, c, gray.shape, r.defect_tolerance_px)
                if side not in fits or mean_d < fits[side][2]:
                    fits[side] = (cand, chain, mean_d)
        side = "front"
        if "back" in fits and fits["back"][2] < fits["front"][2] * (1 - r.side_margin):
            side = "back"
        angle, chain, mean_d = fits[side]
        angle = (angle + 180) % 360 - 180
        shape_d = cv2.matchShapes(tmpl_cnt, c.astype(np.float32), cv2.CONTOURS_MATCH_I1, 0)
        score = max(0.0, 1.0 - shape_d) * max(0.0, 1.0 - mean_d / 20.0)
        gp = np.array([r.grab_point]) * ([1, -1] if side == "back" else [1, 1])
        gx, gy = transform(gp, cx, cy, angle)[0]

        reason = ""
        if score < r.similarity_threshold:
            reason = f"low similarity {score:.2f}"
        elif not (r.start_angle <= angle <= r.end_angle):
            reason = f"angle {angle:.1f} outside range"
        elif r.defect_check and chain > r.max_defect_chain:
            reason = f"defect chain {chain} px"
        results.append(Result(ok=not reason, x=float(gx), y=float(gy), angle=float(angle),
                              score=float(score), defect_chain=int(chain), reason=reason, side=side,
                              x_mm=float((gx - r.origin_px[0]) * r.mm_per_px),
                              y_mm=float((gy - r.origin_px[1]) * r.mm_per_px)))
    return results


def draw(img: np.ndarray, results: list[Result]) -> np.ndarray:
    out = img.copy() if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    for res in results:
        col = (0, 200, 0) if res.ok else (0, 0, 255)
        p = (int(res.x), int(res.y))
        cv2.drawMarker(out, p, col, cv2.MARKER_CROSS, 24, 2)
        e = (int(res.x + 30 * math.cos(math.radians(res.angle))), int(res.y + 30 * math.sin(math.radians(res.angle))))
        cv2.arrowedLine(out, p, e, col, 2)
        cv2.putText(out, ("OK " if res.ok else "NG ") + res.side, (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    return out


# --------------------------------------------------------------------------- PLC link

class PlcLink:
    """Sends one result to the PLC over Modbus TCP.

    REGISTER MAP IS A PLACEHOLDER. The original program writes three things (its messages:
    "send data", "send front/back", "send flag") to a FATEK PLC. Replace BASE and this layout with
    the real map from the uploaded WinProladder .pdw before connecting to a live machine.
    Values are scaled x10 into signed 16-bit:
        BASE+0  flag     1 = OK, 2 = NG, 0 = nothing found
        BASE+1  X  * 10  (mm after calibration; pixels if mm_per_px = 1)
        BASE+2  Y  * 10
        BASE+3  angle * 10
        BASE+4  side     1 = front, 2 = back
        BASE+5  frame counter (wraps at 65535) - PLC can use it as a "new data" handshake
    """
    BASE = 100

    def __init__(self, host: str | None, port: int = 502, unit: int = 1):
        self.host, self.unit, self.counter, self.client = host, unit, 0, None
        if host:
            from pymodbus.client import ModbusTcpClient  # imported only when a PLC is used
            self.client = ModbusTcpClient(host, port=port, timeout=1)
            if not self.client.connect():
                raise ConnectionError(f"Cannot reach PLC at {host}:{port}")

    @staticmethod
    def _s16(v: float) -> int:
        v = int(round(v * 10))
        v = max(-32768, min(32767, v))
        return v & 0xFFFF

    def send(self, res: Result | None) -> list[int]:
        self.counter = (self.counter + 1) % 65536
        if res is None:
            regs = [0, 0, 0, 0, 0, self.counter]
        else:
            regs = [1 if res.ok else 2, self._s16(res.x_mm), self._s16(res.y_mm), self._s16(res.angle),
                    1 if res.side == "front" else 2, self.counter]
        if self.client:
            try:
                self.client.write_registers(self.BASE, regs, device_id=self.unit)   # pymodbus >= 3.10
            except TypeError:
                self.client.write_registers(self.BASE, regs, slave=self.unit)       # older pymodbus 3.x
        return regs

    def close(self):
        if self.client:
            self.client.close()


# --------------------------------------------------------------------------- run loop

def run_folder(recipe: Recipe, folder: Path, out_dir: Path, plc: PlcLink) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ng").mkdir(exist_ok=True)
    log_path = out_dir / "results.csv"
    new = not log_path.exists()
    counts = {"frames": 0, "ok": 0, "ng": 0, "none": 0}
    with log_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "image", "ok", "x", "y", "angle", "score", "defect_chain", "side", "x_mm", "y_mm", "reason"])
        for p in sorted(x for x in folder.iterdir() if x.suffix.lower() in IMAGE_EXT):
            img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)   # handles Chinese paths
            if img is None:
                continue
            counts["frames"] += 1
            t0 = time.perf_counter()
            results = inspect(img, recipe)
            ms = (time.perf_counter() - t0) * 1000
            if not results:
                counts["none"] += 1
                plc.send(None)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), p.name, "", "", "", "", "", "", "", "", "", "nothing found"])
            for res in results:
                counts["ok" if res.ok else "ng"] += 1
                plc.send(res)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), p.name, int(res.ok), f"{res.x:.1f}", f"{res.y:.1f}",
                            f"{res.angle:.1f}", f"{res.score:.3f}", res.defect_chain, res.side,
                            f"{res.x_mm:.2f}", f"{res.y_mm:.2f}", res.reason])
            if any(not r.ok for r in results):
                cv2.imwrite(str(out_dir / "ng" / p.name), draw(img, results))
            print(f"{p.name}: {len(results)} found, {sum(r.ok for r in results)} OK, {ms:.0f} ms")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("learn", help="learn the outline from one good lighter image")
    a.add_argument("--image", required=True)
    a.add_argument("--grab", nargs=2, type=float, metavar=("X", "Y"),
                   help="grab point in pixels (optional with --base if it holds grab_point_image_px)")
    a.add_argument("--base", help="recipe .json from toolparam_to_recipe.py: keeps its thresholds")
    a.add_argument("--recipe", help="recipe .json to write (default: overwrite --base)")
    b = sub.add_parser("run", help="inspect a folder of images")
    b.add_argument("--recipe", required=True)
    b.add_argument("--images", required=True)
    b.add_argument("--out", default="output")
    b.add_argument("--modbus", default=None, help="PLC IP; leave out for dry-run (no writes)")
    args = ap.parse_args(argv)

    if args.cmd == "learn":
        img = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
        out = args.recipe or args.base
        if not out:
            ap.error("give --recipe or --base")
        base = Recipe.load(args.base) if args.base else Recipe(name=Path(out).stem)
        grab = tuple(args.grab) if args.grab else base.grab_point_image_px
        if not grab:
            ap.error("no grab point: pass --grab X Y")
        r = learn(img, grab, base)
        r.save(out)
        print(f"Learned {len(r.contour)} outline points, area {r.area:.0f} px, grab {grab}, saved {out}")
    else:
        plc = PlcLink(args.modbus)
        try:
            counts = run_folder(Recipe.load(args.recipe), Path(args.images), Path(args.out), plc)
        finally:
            plc.close()
        print("Summary:", counts)


if __name__ == "__main__":
    sys.exit(main())
