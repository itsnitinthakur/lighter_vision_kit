"""
toolparam_to_recipe.py - convert LighterGrab recipes (config/Template/ToolParamN.xml + config/System.xml)
into lightergrab_cv recipe JSON files, keeping the settings the maker tuned.

What carries over:  similarity threshold, angle range, defect check on/off, max defect chain length,
                    grab point, camera image flips, exposure (noted), the original values (kept in "source").
What cannot:        the learned outline (contourN.data is a private binary format). Re-learn it once from a
                    good-lighter image with:  python lightergrab_cv.py learn --base <recipe.json> --image good.png

Usage:
  python toolparam_to_recipe.py "D:/copied/LighterGrab1.2.3/config" -o recipes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def load_xml(path: Path) -> ET.Element:
    """LighterGrab's XML files have several top-level <object> elements (not valid XML), so wrap them."""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    return ET.fromstring(f"<root>{text}</root>")


def find(node: ET.Element, *names: str) -> ET.Element | None:
    for n in names:
        node = next((c for c in node if c.get("name") == n), None)
        if node is None:
            return None
    return node


def val(node: ET.Element, *names: str, default=None, cast=float):
    n = find(node, *names)
    if n is None or n.get("value") is None:
        return default
    try:
        return cast(n.get("value"))
    except ValueError:
        return default


def cameras(system_xml: Path) -> dict:
    root = load_xml(system_xml)
    out = {"modbus_host": None, "cameras": {}}
    mb = find(root, "ModbusTcp")
    if mb is not None:
        out["modbus_host"] = mb.get("value")
    cl = find(root, "cameralist")
    for cam in (cl if cl is not None else []):
        out["cameras"][cam.get("name")] = {
            "ip": val(cam, "RemoteCamera", cast=str),
            "test_image_folder": val(cam, "LocalCamera", cast=str),
            "flip_x": val(cam, "reverseX", cast=str) == "true",
            "flip_y": val(cam, "reverseY", cast=str) == "true",
            "frame_rate": val(cam, "frameRate"),
        }
    return out


def convert(tp_xml: Path, cam: dict | None, modbus_host: str | None) -> dict:
    root = load_xml(tp_xml)
    learn, match = find(root, "learnIn"), find(root, "matchIn")
    gx = val(match, "strechPoint", "x")
    gy = val(match, "strechPoint", "y")
    recipe = {
        "name": tp_xml.stem.replace("ToolParam", "recipe"),
        "contour": [],                                     # must be re-learned
        "grab_point": [0.0, 0.0],
        "grab_point_image_px": [gx, gy],                   # used by `learn --base`
        "area": 0.0,
        "similarity_threshold": (val(match, "similarityThreshold", default=75) or 75) / 100.0,
        "start_angle": val(match, "startAngle", default=-180.0),
        "end_angle": val(match, "endAngle", default=180.0),
        "defect_check": bool(val(match, "isDefectDetect", default=1, cast=int)),
        "max_defect_chain": val(match, "maxDefectChainLength", default=16, cast=int),
        "flip_x": bool(cam and cam["flip_x"]),
        "flip_y": bool(cam and cam["flip_y"]),
        "detect_side": True,
        "source": {
            "file": tp_xml.name,
            "exposure_time": val(root, "exposureTime", cast=int),
            "coarse_precision": val(learn, "coarsePatternPrecision", cast=int),
            "fine_precision": val(learn, "finePatternPrecision", cast=int),
            "sample_rate": val(learn, "sampleRate", cast=int),
            "pyramid_levels": val(learn, "pyramidNum", cast=int),
            "gradient_threshold": val(learn, "gradientThreshold", cast=int),
            "overlap": val(match, "overlapPara", cast=int),
            "fine_similarity": val(match, "fineSimilarityThreshold", cast=int),
            "defect_precision": val(match, "defectDetectPrecision"),
            "use_background": val(match, "useBackground", cast=int),
            "detect_level": val(match, "defectDetectLevel", cast=int),
            "camera": cam,
            "modbus_host": modbus_host,
        },
    }
    return recipe


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config_dir", help="LighterGrab config folder (holds System.xml and Template/)")
    ap.add_argument("-o", "--out", default="recipes")
    ap.add_argument("--camera-map", default="1:camera1,2:camera1,3:camera2,4:camera2",
                    help="which camera each ToolParamN belongs to (a GUESS until confirmed on the machine)")
    a = ap.parse_args(argv)
    cfg = Path(a.config_dir)
    sysinfo = cameras(cfg / "System.xml") if (cfg / "System.xml").exists() else {"modbus_host": None, "cameras": {}}
    cmap = dict(item.split(":") for item in a.camera_map.split(","))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tps = sorted(cfg.rglob("ToolParam*.xml"))
    if not tps:
        sys.exit(f"No ToolParam*.xml under {cfg}")
    for tp in tps:
        n = re.sub(r"\D", "", tp.stem)
        cam = sysinfo["cameras"].get(cmap.get(n, ""))
        r = convert(tp, cam, sysinfo["modbus_host"])
        dest = out / f"{r['name']}.json"
        dest.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{tp.name} -> {dest}  similarity {r['similarity_threshold']:.2f}, "
              f"max defect {r['max_defect_chain']} px, grab {r['grab_point_image_px']}, camera {cmap.get(n, '?')}")
    (out / "system.json").write_text(json.dumps(sysinfo, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"System: Modbus {sysinfo['modbus_host']}, cameras {list(sysinfo['cameras'])}")
    print("Next: re-learn each outline:  python lightergrab_cv.py learn --base recipes/recipe1.json --image good.png")


if __name__ == "__main__":
    sys.exit(main())
