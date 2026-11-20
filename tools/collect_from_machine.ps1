# collect_from_machine.ps1 - gather everything needed from a vision machine PC, in one go.
# READ-ONLY: it copies and lists files, it changes nothing on the machine.
#
# Run on the machine PC (PowerShell, no admin needed):
#   powershell -ExecutionPolicy Bypass -File collect_from_machine.ps1
# Optional:  -Out E:\for_nitin   (default: Desktop\for_nitin)   -MaxImagesMB 2000
# Then copy the output folder to a USB stick.

param(
    [string]$Out = "$env:USERPROFILE\Desktop\for_nitin",
    [int]$MaxImagesMB = 2000
)

$ErrorActionPreference = "SilentlyContinue"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Write-Host "Collecting into $Out"

# 1. PC, network and camera link facts -------------------------------------------------------
systeminfo                                        > "$Out\systeminfo.txt"
ipconfig /all                                     > "$Out\ipconfig.txt"
Get-NetAdapter | Format-Table -AutoSize           > "$Out\network_adapters.txt"
Get-NetAdapterAdvancedProperty | Where-Object { $_.DisplayName -match "Jumbo|Buffer|Interrupt" } |
    Format-Table -AutoSize                        > "$Out\nic_settings.txt"
Get-NetIPAddress | Format-Table -AutoSize         > "$Out\ip_addresses.txt"
Get-Process | Sort-Object CPU -Descending | Select-Object -First 40 Name, Path |
    Format-Table -AutoSize                        > "$Out\running_programs.txt"

# Programs that start with Windows (the real inspection program is usually here)
Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location |
    Format-Table -AutoSize                        > "$Out\startup_programs.txt"
Copy-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" "$Out\startup_folder" -Recurse
Copy-Item "$env:PUBLIC\Desktop" "$Out\public_desktop_shortcuts" -Recurse
Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*,
                 HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\* |
    Where-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher |
    Sort-Object DisplayName | Format-Table -AutoSize > "$Out\installed_software.txt"

# 2. Every file that holds settings, recipes, models or PLC programs -------------------------
$drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -ne $null } | ForEach-Object { $_.Root }
$wanted = "*.pdw","*.pdz","*.gxw","*.ap1*","*.mfs","*.sol","*.scn","*.bkd","*.onnx","*.pt","*.pth",
          "*.engine","*.hdev","*.shm","*.avproj","ToolParam*.xml","System.xml","AppInfo.xml","*.data"
$skip = "\\Windows\\|\\Program Files\\WindowsApps\\|\\\$Recycle|\\AppData\\Local\\Temp\\"
$found = foreach ($d in $drives) {
    Get-ChildItem $d -Recurse -Include $wanted -File | Where-Object { $_.FullName -notmatch $skip }
}
$found | Select-Object FullName, Length, LastWriteTime | Export-Csv "$Out\important_files.csv" -NoTypeInformation -Encoding UTF8
New-Item -ItemType Directory -Force -Path "$Out\files" | Out-Null
foreach ($f in $found) {
    if ($f.Length -lt 50MB) {
        $dest = Join-Path "$Out\files" ($f.FullName -replace ":", "" )
        New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
        Copy-Item $f.FullName $dest
    }
}

# 3. The inspection program folders (exe, dll list, config, logs) ----------------------------
foreach ($d in $drives) {
    Get-ChildItem $d -Recurse -Include "LighterGrab*.exe","*Grab*.exe","*Vision*.exe","*Inspect*.exe" -File |
        Where-Object { $_.FullName -notmatch $skip } | ForEach-Object {
            $dir = $_.DirectoryName
            $name = ($dir -replace "[:\\]", "_")
            Get-ChildItem $dir -Recurse | Select-Object FullName, Length, LastWriteTime |
                Export-Csv "$Out\program_folder_$name.csv" -NoTypeInformation -Encoding UTF8
            New-Item -ItemType Directory -Force -Path "$Out\program_$name" | Out-Null
            foreach ($sub in "config","logfile","log","saveImage") {
                if (Test-Path "$dir\$sub") { Copy-Item "$dir\$sub" "$Out\program_$name\$sub" -Recurse }
            }
        }
}

# 4. Images (D:\picture2 first, then any image folder), capped at MaxImagesMB ---------------------
$imgOut = "$Out\images"
New-Item -ItemType Directory -Force -Path $imgOut | Out-Null
$budget = $MaxImagesMB * 1MB
# D:\picture2 (LighterGrab's test-image folder) goes first, so it is never cut by the size limit
$first = @()
if (Test-Path "D:\picture2") { $first = Get-ChildItem "D:\picture2" -Recurse -Include *.bmp,*.jpg,*.png,*.tif -File }
$rest = foreach ($root in $drives) {
    Get-ChildItem $root -Recurse -Include *.bmp,*.jpg,*.png,*.tif -File |
        Where-Object { $_.FullName -notmatch $skip -and $_.FullName -notmatch "\\Program Files" -and
                       $_.FullName -notlike "D:\picture2*" }
}
$copied = 0
foreach ($i in @($first) + @($rest)) {
    if ($copied + $i.Length -gt $budget) { break }
    $dest = Join-Path $imgOut ($i.FullName -replace ":", "")
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $i.FullName $dest
    $copied += $i.Length
}

# 5. What still needs a person ------------------------------------------------------------------
@"
STILL TO DO BY HAND
1. PLC program: open WinProladder (FATEK) -> connect to the PLC -> Upload -> File > Save as .pdw
   -> put the .pdw in this folder. It shows which registers receive position, front/back and the flag.
2. Camera settings: open MVS (Hikrobot) or MV Viewer (Dahua) -> Import/Export Features -> Export each camera
   -> put the .mfs / .xml files in this folder.
3. Take a photo of the inspection program's screen while the line is running.
Images copied: $([math]::Round($copied / 1MB)) MB (limit $MaxImagesMB MB)
"@ | Out-File "$Out\TODO_BY_HAND.txt" -Encoding UTF8

Write-Host "Done. Files found: $($found.Count). Images copied: $([math]::Round($copied / 1MB)) MB."
Write-Host "Now do the 3 manual steps in TODO_BY_HAND.txt, then copy $Out to a USB stick."
