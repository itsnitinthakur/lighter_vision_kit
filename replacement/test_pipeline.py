"""End-to-end: real LighterGrab config -> converter -> learn --base -> inspect."""
import json
import subprocess
import sys
from pathlib import Path

import cv2

from lightergrab_cv import Recipe, inspect
from test_lightergrab_cv import lighter

KIT = Path(__file__).resolve().parent.parent
CONFIG = KIT / "samples" / "lightergrab_config_2026"


def test_convert_learn_inspect(tmp_path):
    out = tmp_path / "recipes"
    subprocess.run([sys.executable, str(KIT / "tools" / "toolparam_to_recipe.py"), str(CONFIG), "-o", str(out)],
                   check=True, capture_output=True)
    r1 = json.loads((out / "recipe1.json").read_text(encoding="utf-8"))
    assert r1["similarity_threshold"] == 0.75 and r1["max_defect_chain"] == 16
    assert r1["flip_x"] and r1["flip_y"]                       # camera1 flips X and Y in System.xml
    assert json.loads((out / "system.json").read_text())["modbus_host"] == "192.168.8.4"

    # the synthetic learning image is not flipped, so switch flips off for this test
    base = Recipe.load(out / "recipe1.json")
    base.flip_x = base.flip_y = False
    base.grab_point_image_px = (280.0, 240.0)
    base.save(out / "recipe1.json")
    img = tmp_path / "good.png"
    cv2.imwrite(str(img), lighter(320, 240, 0))
    subprocess.run([sys.executable, str(KIT / "replacement" / "lightergrab_cv.py"), "learn",
                    "--base", str(out / "recipe1.json"), "--image", str(img)], check=True, capture_output=True)
    r = Recipe.load(out / "recipe1.json")
    assert len(r.contour) > 50 and r.similarity_threshold == 0.75 and r.source["file"] == "ToolParam1.xml"
    res = inspect(lighter(300, 260, 75), r)
    assert len(res) == 1 and res[0].ok and res[0].side == "front"
