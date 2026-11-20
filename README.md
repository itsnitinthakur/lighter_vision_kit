# Lighter vision kit: reverse engineering + free replacement

| Folder | What is in it |
| --- | --- |
| `docs/REPORT.md` | What the lighter machines are, how LighterGrab works, settings in use, what is missing, rebuild plan |
| `docs/machine_scan_report.md` | Full automatic scan of the copied machine folder (software, versions, dependencies, Chinese UI text) |
| `docs/LICENSES.md` | Which free tools are safe to sell inside a machine, and which to avoid |
| `tools/inspect_machine.py` | Scanner that produced the scan report. Run it on any machine folder (flame station next) |
| `tools/toolparam_to_recipe.py` | Converts LighterGrab recipes (`ToolParamN.xml`, `System.xml`) into recipes for the replacement |
| `tools/collect_from_machine.ps1` | Run once on a machine PC: collects settings, recipes, program folders, images, network facts |
| `replacement/` | The free replacement: `lightergrab_cv.py`, its tests, README |
| `replacement/lighter_inspection_demo.ipynb` | Visual walkthrough in Jupyter: outline learning, any-angle matching, front/back, defect chain, PLC values, folder run |
| `samples/lightergrab_config_2026/` | The current LighterGrab settings (9 Mar 2026), used by the tests |

Needs Python 3.10+ and `pip install -r replacement/requirements.txt`. Everything runs offline.

## Typical use

```bash
# 1. On the machine PC (PowerShell), collect what is missing, then copy the folder to USB
powershell -ExecutionPolicy Bypass -File tools/collect_from_machine.ps1

# 2. Scan any copied machine folder
python tools/inspect_machine.py "D:/for_nitin" -o flame_station_report.md

# 3. Convert LighterGrab's recipes, keeping the maker's tuned settings
python tools/toolparam_to_recipe.py samples/lightergrab_config_2026 -o recipes

# 4. Re-learn each outline from one good-lighter image (the .data outlines are a private format)
python replacement/lightergrab_cv.py learn --base recipes/recipe1.json --image good.png

# 5. Test on saved images with no PLC connected
python replacement/lightergrab_cv.py run --recipe recipes/recipe1.json --images "D:/picture2/LeftLight"

# 6. See every step as pictures
cd replacement && jupyter notebook lighter_inspection_demo.ipynb

# 7. Run the tests (18)
cd replacement && python -m pytest -q
```

Before connecting to a live PLC: replace the placeholder register map in `PlcLink` with the real one from
the PLC program (.pdw uploaded in WinProladder).
# lighter_vision_kit
