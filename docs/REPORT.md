# Lighter line vision: reverse-engineering report

Scope: the files copied from the lighter machine PCs into `untitled folder 2` (14 GB, about 46,000 files),
analysed on 19 Nov 2026. The machine PCs themselves were not touched. Full automatic scan: `machine_scan_report.md`.

**Bottom line.** The casing pick-up station runs a custom Qt/C++ program, LighterGrab, whose core is classic
OpenCV outline matching. It uses no AI. It can be rebuilt with free software, and `replacement/` is that
rebuild. Nothing from the flame-height station was in the folder.

## 1. The two stations

| Station | Job | Cameras | Software | Method | Output |
| --- | --- | --- | --- | --- | --- |
| Casing pick-up | Find each lighter casing, tell which side is up, send the grab position | 2 GigE, now Dahua (earlier Hikrobot), 30 fps | LighterGrab 1.2.3 (Qt C++) | Outline (contour) matching + defect chain check | Position, front/back and a flag over Modbus TCP to a FATEK PLC at 192.168.8.4 |
| Flame height | Reject lighters whose flame is too short or too tall | 1 Basler acA640-120gm, 0.3 MP, 120 fps | Omron FZ-PanDA (from photos of its screen) | Edge scan of the flame vs min/max limits | OK/NG over serial |

## 2. How LighterGrab is built (traced from which file loads which)

| Part | Job | Built on |
| --- | --- | --- |
| `ContourPos.dll` | The core: learn the outline, save/load it, match it (`ContourPos_Learn`, `ContourPos_Match`), algorithm version "0311 with max defect chain length" | OpenCV 2.4.11 (core, imgproc, calib3d) |
| `BlobAnalysis.dll` | Blob (bright/dark patch) analysis | OpenCV 3.0.0 |
| `VisionGraph.dll`, `XVRegionAnalysis.dll` | Drawing results, region analysis | Adaptive Vision Library (Future Processing, commercial) |
| `XVCamera.dll` -> `DahuaCamera.dll`, `LocalCamera.dll` | Live camera, or images from a folder for testing | Dahua MV SDK (`MVSDKmd.dll`) |
| `modbus.dll` | PLC link; only `modbus_write_registers` is used (write-only) | libmodbus (free) |
| `LighterGrab.exe` | Window, recipes, login, glue | Qt 5 |

Earlier guess corrected: Halcon is **not** used. The word only appeared in the developer's folder name
(`E:/project/projects/Halcon/LightGrab20210331/...-haikang`) and in Dahua's Halcon adapter docs.

## 3. Settings in use (config dated 9 Mar 2026, the copy outside 终结版)

| Setting | Value | Meaning |
| --- | --- | --- |
| PLC | Modbus TCP 192.168.8.4 | FATEK PLC (WinProladder is installed; its default link is FATEK-TCP port 500) |
| Camera 1 | 192.168.10.150, image flipped X and Y, 30 fps | test images in `D:\picture2\透明A\手动保存` ("transparent A / manual save") |
| Camera 2 | 192.168.8.250, image flipped Y, 30 fps | test images in `D:/picture2/LeftLight` |
| Recipes | 4 (`ToolParam1-4.xml` + `contour1-4.data`) | likely 2 per camera; convert with `tools/toolparam_to_recipe.py` |
| Recipe 1 | similarity 75 %, angle -180 to 180, defect check on, max chain 16 px, exposure 50, grab point (279, 344) | |
| Recipes 2-4 | similarity 70 %, max chain 15-16 px | |
| Login | password `123` in plain text (`AppInfo.xml`) | change it |

The `终结版` ("final version") folder is the maker's installer package: LighterGrab 1.2.1 and 1.2.3,
the 2021 settings (camera IPs 192.168.10.250 / 192.168.8.150), VC++ runtimes, SenseShield licence installer
(加密许可, "encryption licence"), and Sunlogin remote desktop (远程-向日葵).

## 4. Version history (from the changelog text inside LighterGrab.exe)

Newest first, translated:
1. Draggable screen layout; **front/back detection for each camera**.
2. Admin (UAC) login; start with Windows; camera connection protection; **image auto-adapt**; camera exposure and image size changes.
3. Fixes when switching recipes (grab point, exposure, algorithm); **detection level** added; exit protection.
4. Template save fix; crash after opening a recipe fixed; camera, project and UI modules updated.
5. Camera library replaced; recognition algorithm updated (accuracy and speed); thread model replaced.
6. Camera module replaced; contour algorithm version 0311 with max defect chain length.
7. First version.

Messages the program sends show what goes to the PLC each cycle: "send data", "send front/back", "send flag",
plus "count recognised" and "time in ms". It also has a **calibration / distance measurement** screen (标定测距).

## 5. Other software on the PC

| Software | Why it matters |
| --- | --- |
| SenseShield / Virbox | Licence protection. Copying LighterGrab to another PC probably will not run. |
| Sunlogin (向日葵) | The maker can log in remotely. Keep or disable it on purpose. |
| FATEK WinProladder | Use it to upload the PLC program (.pdw) and read the register map. |
| Hikrobot MVS + Dahua MV Viewer | Camera tools: IP setup, live view, settings export (.mfs). |

## 6. What is missing

- [ ] PLC program (.pdw) from WinProladder: the exact registers for position, front/back and flag
- [ ] Images from `D:\picture2` on the machine PC (none were copied)
- [ ] Camera settings exports (.mfs)
- [ ] Everything from the flame station (no Omron, Basler or flame files were in the folder)

`tools/collect_from_machine.ps1` gathers all of it except the two manual exports, which it lists in `TODO_BY_HAND.txt`.

## 7. Rebuild with free tools

| Part | Current | Replacement (`replacement/lightergrab_cv.py`) |
| --- | --- | --- |
| Outline matching | ContourPos on OpenCV 2.4.11 | Own outline matcher on OpenCV 5 (Apache 2.0) |
| Defect check | Max defect chain length | Same rule: longest run of edge points off the learned outline |
| Front/back | Per-camera detection | Mirrored-outline fit |
| Grab point + calibration | Pose-moved point, 标定测距 | Same maths, `mm_per_px` + `origin_px` |
| PLC link | libmodbus, write registers | pymodbus (BSD), write registers |
| Records | None (logs empty, saveImage empty) | CSV for every lighter, image for every NG |
| Licence cost | AVL + SenseShield | None |

Steps: collect the missing files; convert the recipes; re-learn each outline from one good image;
run on the saved images with no PLC; run beside the old PC for a shift (logging only); switch the PLC link over
in a planned stop with the old PC kept as fallback.
