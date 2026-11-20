"""
inspect_machine.py - reverse-engineering scanner for a vision machine's PC folder.

Point it at a folder copied from a machine PC. It writes a Markdown report that answers:
  * which programs and DLLs are there, who made them, which version
  * which program loads which library (dependency map)  -> where the real logic lives
  * which known vision / camera / PLC / licence / remote-access software is present
  * which config, recipe, image, log and PLC files exist
  * the Chinese (or English) UI text inside the programs (menus, errors, changelog)

Pure Python standard library only. Read-only: it never changes the scanned folder.

Usage:
  python inspect_machine.py "D:/copied_from_machine" -o machine_report.md
  python inspect_machine.py "D:/copied_from_machine" -o report.md --skip code --skip Programs
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- known software fingerprints
# (pattern in file name or content, label, what it means for us)
KNOWN = [
    (r"opencv_\w*?(\d{3,4})\.dll", "OpenCV", "free vision library (BSD / Apache 2.0)"),
    (r"^avl\.dll$", "Adaptive Vision Library (Future Processing / Aurora Vision)", "commercial vision library, needs licence"),
    (r"halcon", "MVTec Halcon", "commercial vision library, needs licence"),
    (r"^modbus\.dll$", "libmodbus", "free Modbus library (LGPL) -> PLC link is Modbus"),
    (r"^qt5core\.dll$|^qt6core\.dll$", "Qt framework", "UI toolkit -> program is C++/Qt"),
    (r"mvcameracontrol", "Hikrobot MVS SDK", "Hikrobot / Hikvision industrial cameras"),
    (r"mvsdkmd\.dll|mv viewer|dahuacamera", "Dahua / iRAYPLE MV SDK", "Dahua industrial cameras"),
    (r"pylon|basler", "Basler pylon SDK", "Basler cameras"),
    (r"mindvision|mvcamsdk", "MindVision SDK", "MindVision cameras"),
    (r"senseshield|virbox|ss_monitor", "SenseShield / Virbox", "software licence protection (copy protection)"),
    (r"sunlogin", "Sunlogin (Oray)", "remote desktop - maker can log in remotely"),
    (r"wprolad|prolad|fapl", "FATEK WinProladder", "FATEK PLC programming software -> PLC is FATEK"),
    (r"gx works|gxw|melsoft", "Mitsubishi GX Works", "Mitsubishi PLC software"),
    (r"tia portal|step 7|s7", "Siemens TIA / Step 7", "Siemens PLC software"),
    (r"fzpanda|fz-panda|omron", "Omron FZ vision", "Omron vision software"),
    (r"visionmaster", "Hikrobot VisionMaster", "Hikrobot vision application"),
    (r"onnx|tensorrt|torch|tensorflow|openvino|yolo", "AI runtime / model", "deep-learning model present"),
]
INTERESTING_EXT = {
    "config": {".xml", ".ini", ".json", ".cfg", ".yaml", ".yml", ".properties"},
    "recipe/model": {".data", ".shm", ".hdict", ".hobj", ".sol", ".scn", ".bkd", ".onnx", ".pt", ".pth", ".engine", ".mfs", ".avdata", ".avproj"},
    "plc program": {".pdw", ".pdz", ".gxw", ".ckp", ".ap15", ".ap16", ".ap17", ".smc", ".s7p"},
    "image": {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff"},
    "log": {".log", ".txt"},
}
BINARY_EXT = {".exe", ".dll", ".bpl", ".cti", ".ocx"}


# ---------------------------------------------------------------- PE helpers (no pefile needed)

def version_info(data: bytes) -> dict:
    """Read CompanyName / FileDescription / FileVersion / ProductName from a PE version resource."""
    out = {}
    for key in ("CompanyName", "FileDescription", "FileVersion", "ProductName", "LegalCopyright"):
        pat = key.encode("utf-16le") + rb"\x00\x00(?:\x00\x00)?((?:[^\x00][\x00]|[\x00][^\x00]){1,120}?)\x00\x00"
        m = re.search(pat, data)
        if m:
            try:
                val = m.group(1).decode("utf-16le").strip("\x00 ").strip()
                if val and val.isprintable():
                    out[key] = val
            except UnicodeDecodeError:
                pass
    return out


def imported_dlls(data: bytes, own_name: str) -> list[str]:
    names = set(n.decode("ascii", "ignore").lower() for n in re.findall(rb"[A-Za-z0-9_\-.]{2,60}\.dll", data, re.I))
    skip = {own_name.lower()}
    # Windows / C runtime noise
    noise = re.compile(r"^(kernel32|user32|gdi32|advapi32|shell32|ole32|oleaut32|ws2_32|msvc|vcruntime|ucrtbase|api-ms|ntdll|"
                       r"comctl32|comdlg32|winmm|version|imm32|shlwapi|crypt32|wsock32|setupapi|dbghelp|psapi|iphlpapi|"
                       r"winspool|mpr|netapi32|userenv|rpcrt4|opengl32|d3d|dxgi|dwmapi|uxtheme|concrt|msvcp)")
    return sorted(n for n in names if n not in skip and not noise.match(n))


def cjk_strings(data: bytes, min_len: int = 3) -> list[str]:
    """UTF-16 strings made of common Chinese characters (GB2312 level 1) plus digits/punctuation.
    Filters out the random 'CJK-looking' noise that binary data produces."""
    found = []
    for m in re.finditer(rb"(?:[\x00-\xff][\x4e-\x9f]|[\x20-\x7e]\x00|[\x00-\x0f][\x30\xff]){3,}", data):
        try:
            s = m.group().decode("utf-16le")
        except UnicodeDecodeError:
            continue
        s = s.strip()
        cjk = [ch for ch in s if "\u4e00" <= ch <= "\u9fff"]
        if len(cjk) < min_len or len(s) > 80:
            continue
        ok = True
        for ch in cjk:
            try:
                b = ch.encode("gb2312")
            except UnicodeEncodeError:
                ok = False
                break
            if not 0xB0 <= b[0] <= 0xD7:      # GB2312 level-1 = the 3755 most common characters
                ok = False
                break
        if ok and all(ch.isprintable() for ch in s):
            found.append(s)
    return list(dict.fromkeys(found))


def ascii_keywords(data: bytes) -> list[str]:
    key = re.compile(rb"(?i)modbus_\w+|ToolParam\w*|similarityThreshold|startAngle|endAngle|exposureTime|"
                     rb"ContourPos_\w+|ModbusTcp|cameralist|on_btn\w+|password")
    return sorted(set(m.group().decode() for m in key.finditer(data)))


# ---------------------------------------------------------------- scan

def scan(root: Path, skip: list[str]) -> dict:
    info = {"files": 0, "bytes": 0, "binaries": [], "groups": collections.defaultdict(list), "known": {}, "deps": {}}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if not any(str(rel_dir / d).startswith(s) or d == s for s in skip)]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            info["files"] += 1
            info["bytes"] += size
            low = fn.lower()
            ext = p.suffix.lower()
            hay = str(rel).lower()
            for pat, label, meaning in KNOWN:
                if re.search(pat, low) or re.search(pat, hay):
                    info["known"].setdefault(label, {"meaning": meaning, "where": []})["where"].append(str(rel))
            for group, exts in INTERESTING_EXT.items():
                if ext in exts:
                    info["groups"][group].append((str(rel), size))
            if ext in BINARY_EXT and size < 60_000_000:
                info["binaries"].append(rel)
    return info


def analyse_binaries(root: Path, binaries: list[Path], focus: list[str]) -> list[dict]:
    rows = []
    for rel in binaries:
        if focus and not any(str(rel).startswith(f) for f in focus):
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        rows.append({"file": str(rel), "ver": version_info(data), "imports": imported_dlls(data, rel.name),
                     "cjk": cjk_strings(data) if rel.suffix.lower() == ".exe" else [],
                     "keys": ascii_keywords(data)})
    return rows


def write_report(root: Path, info: dict, bins: list[dict], out: Path) -> None:
    L = []
    w = L.append
    w(f"# Machine folder report: `{root.name}`\n")
    w(f"{info['files']} files, {info['bytes'] / 1e9:.2f} GB scanned.\n")

    # libraries that are only referenced (imported), not present as files, still count
    for r in bins:
        for imp in r["imports"]:
            for pat, label, meaning in KNOWN:
                if re.search(pat, imp):
                    d = info["known"].setdefault(label, {"meaning": meaning, "where": []})
                    d["where"].append(f"{imp} (loaded by {Path(r['file']).name})")
    w("## Known software found\n")
    w("| Software | What it means | Example location |\n| --- | --- | --- |")
    for label, d in sorted(info["known"].items()):
        w(f"| {label} | {d['meaning']} | `{d['where'][0]}` ({len(d['where'])} files) |")
    ocv = sorted({m for d in info["known"].get("OpenCV", {}).get("where", [])
                  for m in re.findall(r"opencv_[a-z0-9]*?(\d{3,4})\.dll", d.lower())})
    if ocv:
        pretty = [v[0] + "." + v[1] + "." + v[2:] for v in ocv]   # 2411 -> 2.4.11, 300 -> 3.0.0
        w(f"\nOpenCV versions (from DLL names): {', '.join(pretty)}\n")

    w("\n## Programs and libraries (who made them, what they load)\n")
    w("| File | Maker / description | Version | Loads |\n| --- | --- | --- | --- |")
    for r in sorted(bins, key=lambda r: r["file"]):
        v = r["ver"]
        maker = " / ".join(x for x in (v.get("CompanyName"), v.get("FileDescription")) if x) or "-"
        w(f"| `{r['file']}` | {maker} | {v.get('FileVersion', '-')} | {', '.join(r['imports'][:12]) or '-'} |")

    for r in sorted(bins, key=lambda r: (not r["file"].lower().endswith(".exe"), r["file"])):
        if r["cjk"] or r["keys"]:
            w(f"\n### Text inside `{r['file']}`\n")
            if r["keys"]:
                w("Keywords: " + ", ".join(f"`{k}`" for k in r["keys"][:60]) + "\n")
            if r["cjk"]:
                w("Chinese UI / log text (translate these: menus, errors, changelog):\n")
                for s in r["cjk"][:300]:
                    w(f"- {s}")

    w("\n## Files by kind\n")
    for group in ("plc program", "recipe/model", "config", "image", "log"):
        items = info["groups"].get(group, [])
        w(f"\n### {group} ({len(items)})\n")
        if not items:
            w("None found.")
        for path, size in sorted(items)[:80]:
            w(f"- `{path}` ({size:,} bytes)")
        if len(items) > 80:
            w(f"- ... {len(items) - 80} more")

    w("\n## Still missing (check on the machine)\n")
    if not info["groups"].get("plc program"):
        w("- [ ] PLC program file: upload it from the PLC with its programming software and save it")
    if not any(re.search(r"picture|image|img", p, re.I) for p, _ in info["groups"].get("image", [])):
        w("- [ ] Production / test images")
    if not any(p.endswith(".mfs") for p, _ in info["groups"].get("recipe/model", [])):
        w("- [ ] Camera settings export (.mfs from the camera tool)")
    out.write_text("\n".join(L), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("-o", "--out", default="machine_report.md")
    ap.add_argument("--skip", action="append", default=[], help="sub-folder to skip (repeatable)")
    ap.add_argument("--focus", action="append", default=[],
                    help="only analyse binaries under this sub-folder (repeatable); default: all")
    a = ap.parse_args(argv)
    root = Path(a.folder)
    info = scan(root, a.skip)
    bins = analyse_binaries(root, info["binaries"], a.focus)
    write_report(root, info, bins, Path(a.out))
    print(f"Scanned {info['files']} files; {len(bins)} binaries analysed; report -> {a.out}")


if __name__ == "__main__":
    sys.exit(main())
