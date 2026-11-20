# lightergrab_cv: open-source replacement for the LighterGrab pick-up station

Same loop as the original LighterGrab machine: find the lighter outline, check it for defects, tell
front side from back side, move the grab point to its position and angle, convert pixels to mm,
and send position / angle / front-back / OK-NG flag to the (FATEK) PLC over Modbus TCP.
It also logs every lighter to `output/results.csv` and saves every NG image, which the old machine
did not do. It uses only OpenCV, NumPy and pymodbus (Apache 2.0 / BSD) and runs with no internet.

## Install offline on the line PC
On a PC with internet:  `pip download -r requirements.txt -d wheels`
Copy the folder, then on the line PC:  `pip install --no-index --find-links wheels -r requirements.txt`

## Use
0. (Optional) Convert LighterGrab's recipes first, keeping the maker's settings:
   `python ../tools/toolparam_to_recipe.py <LighterGrab config folder> -o recipes`
   then learn with `--base recipes/recipe1.json` (the grab point comes from the recipe).
1. Learn from one good lighter (grab point in pixels, as in ToolParam `strechPoint`):
   `python lightergrab_cv.py learn --image good.png --grab 241.9 134.5 --recipe recipes/recipe1.json`
2. Test on saved images, with no PLC writes:
   `python lightergrab_cv.py run --recipe recipes/recipe1.json --images "D:/picture2/LeftLight"`
3. Only after the real register map is filled in `PlcLink`:
   `... run ... --modbus 192.168.8.4`

Tune `similarity_threshold`, `max_defect_chain`, `canny_low/high` and `flip_x/flip_y` in the recipe JSON.
Set `mm_per_px` and `origin_px` from a calibration target (1.0 / 0,0 = send pixels).
Front/back works by fitting the mirrored outline; set `detect_side` false for symmetric parts.

## Before connecting to a live PLC
- `PlcLink.BASE` and the register layout are PLACEHOLDERS. Get the real map from the PLC program
  (upload it in WinProladder and save the .pdw).
- Run in dry-run beside the old system for one shift and compare positions before switching over.

## Not done yet
- A live camera reader (Hikrobot MVS or Mindvision SDK). It currently reads image folders.
- An operator screen (PySide6).
- Posting counts to Odoo.

`python -m pytest -q` runs the 18 tests on synthetic lighter images.
