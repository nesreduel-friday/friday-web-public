# Friday-Overlay: Installer fuer den Windows-PC.
# Ausfuehren aus dem Ordner pc-helfer\overlay (dort liegen die Dateien):
#   powershell -ExecutionPolicy Bypass -File .\installieren_overlay.ps1
# Nochmal ausfuehren = aktualisieren. Eine vorhandene konfig.json bleibt erhalten.
# Bewusst nur ASCII: PowerShell 5.1 liest sonst Umlaute falsch.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Hier = $PSScriptRoot
$Modell = 'vosk-model-small-en-us-0.15'
$ModellUrl = "https://alphacephei.com/vosk/models/$Modell.zip"
$Venv = Join-Path $Hier 'venv'
$Modelle = Join-Path $Hier 'modelle'

Write-Host ''
Write-Host 'Friday-Overlay wird eingerichtet ...' -ForegroundColor Cyan

function Finde-Python {
    $kandidaten = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        try { $p = & py.exe -3 -c "import sys;print(sys.executable)" 2>$null; if ($p) { $kandidaten += $p.Trim() } } catch {}
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' }
    if ($cmd) { $kandidaten += $cmd.Source }
    $kandidaten += @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($k in $kandidaten) {
        if ($k -and (Test-Path $k)) {
            try { $ok = & $k -c "import sys;print(sys.version_info>=(3,10))" 2>$null } catch { $ok = '' }
            if ($ok -eq 'True') { return $k }
        }
    }
    return $null
}

# 1. Python 3.10 oder neuer
$Python = Finde-Python
if (-not $Python) {
    Write-Host 'Python fehlt - wird mit winget installiert ...'
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $Python = Finde-Python
    if (-not $Python) { throw 'Python liess sich nicht installieren. Bitte von python.org installieren und den Befehl erneut ausfuehren.' }
}
Write-Host "Python: $Python"

# 2. Eigene Umgebung mit den Paketen aus requirements.txt
if (-not (Test-Path (Join-Path $Venv 'Scripts\pythonw.exe'))) {
    & $Python -m venv $Venv
}
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvPythonw = Join-Path $Venv 'Scripts\pythonw.exe'
Write-Host 'Pakete (vosk, sounddevice, PySide6 ...) ...'
& $VenvPython -m pip install --upgrade --disable-pip-version-check --quiet -r (Join-Path $Hier 'requirements.txt') | Out-Host

# 3. Wake-Word-Modell (englisch, klein, ~40 MB, offline) - nur einmal laden
New-Item -ItemType Directory -Force -Path $Modelle | Out-Null
$ModellOrdner = Join-Path $Modelle $Modell
if (-not (Test-Path $ModellOrdner)) {
    $Zip = Join-Path $Modelle "$Modell.zip"
    Write-Host "Wake-Word-Modell laden ($Modell) ..."
    Invoke-WebRequest -Uri $ModellUrl -OutFile $Zip -UseBasicParsing
    Expand-Archive -Path $Zip -DestinationPath $Modelle -Force
    Remove-Item $Zip
}

# 4. Konfiguration nur anlegen, wenn es noch keine gibt
$KonfigDatei = Join-Path $Hier 'konfig.json'
if (-not (Test-Path $KonfigDatei)) {
    Copy-Item (Join-Path $Hier 'konfig.beispiel.json') $KonfigDatei
    Write-Host 'konfig.json angelegt (Standardwerte).'
}

# 5. Autostart: bei jeder Anmeldung ohne Konsolenfenster
$Wsh = New-Object -ComObject WScript.Shell
$Startup = [Environment]::GetFolderPath('Startup')
$Lnk = $Wsh.CreateShortcut((Join-Path $Startup 'Friday-Overlay.lnk'))
$Lnk.TargetPath = $VenvPythonw
$Lnk.Arguments = '"' + (Join-Path $Hier 'friday_overlay.py') + '"'
$Lnk.WorkingDirectory = $Hier
$Lnk.Description = 'Friday-Overlay (Wake-Word Friday)'
$Lnk.Save()

# 6. Gleich starten (ein laufendes Overlay vorher beenden)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*friday_overlay.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process -FilePath $VenvPythonw -ArgumentList ('"' + (Join-Path $Hier 'friday_overlay.py') + '"') -WorkingDirectory $Hier

Write-Host ''
Write-Host 'Fertig. Sag "Friday" - das Overlay erscheint unten in der Mitte.' -ForegroundColor Green
Write-Host 'Protokoll: overlay.log neben friday_overlay.py. Beenden: Rechtsklick auf das Overlay, Beenden.'
