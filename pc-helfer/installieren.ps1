# Friday-PC: Installer. Friday liefert ihn mit einem Einmal-Code aus (Freigaben -> PC -> Einrichten);
# er enthaelt den Schluessel. Bewusst nur ASCII: PowerShell 5.1 liest sonst Umlaute falsch.
# Nochmal ausfuehren = aktualisieren. Eigene Ordner in config.json bleiben erhalten.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Ziel = Join-Path $env:LOCALAPPDATA 'FridayPC'
$Port = __PORT__
$Quellen = @('__QUELLEN__'.Split(',') | Where-Object { $_ })
$Skript = Join-Path $Ziel 'friday_pc.py'

Write-Host ''
Write-Host 'Friday-PC wird eingerichtet ...' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Ziel | Out-Null

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

# 1. Python
$Python = Finde-Python
if (-not $Python) {
    Write-Host 'Python fehlt - wird mit winget installiert ...'
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $Python = Finde-Python
    if (-not $Python) { throw 'Python liess sich nicht installieren. Bitte von python.org installieren und den Befehl erneut ausfuehren.' }
}
$Pythonw = Join-Path (Split-Path $Python) 'pythonw.exe'
Write-Host "Python: $Python"

# 2. Pakete: pillow (Bildschirmfoto), playwright (Friday-Browser ueber Edge - kein extra Browser-Download)
Write-Host 'Pakete pillow und playwright ...'
& $Python -m pip install --user --upgrade --disable-pip-version-check --quiet pillow playwright 2>&1 | Out-Host

# 3. Der Helfer
[IO.File]::WriteAllBytes($Skript, [Convert]::FromBase64String('__HELFER__'))

# 4. Einstellungen: Schluessel, Port, erlaubte Absender setzen - eigene Ordner/Programme behalten
$KonfigDatei = Join-Path $Ziel 'config.json'
$Konfig = [ordered]@{}
if (Test-Path $KonfigDatei) {
    try { (Get-Content $KonfigDatei -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $Konfig[$_.Name] = $_.Value } } catch {}
}
$Konfig['token'] = '__SCHLUESSEL__'
$Konfig['port'] = $Port
$Konfig['erlaubte_quellen'] = $Quellen
[IO.File]::WriteAllText($KonfigDatei, ($Konfig | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))
icacls $KonfigDatei /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null

# 5. Firewall: nur Friday (hphome) darf herein, nur auf diesen Port, nur fuer diesen Python.
#    Einmal Administrator (Windows fragt nach). Die Programm-Regel verhindert auch die
#    Windows-Rueckfrage beim ersten Start - wer dort "Abbrechen" drueckt, sperrt sonst den Helfer aus.
$Fern = ($Quellen | Where-Object { $_ -ne '127.0.0.1' }) -join ','
if (-not $Fern) { $Fern = 'CGNAT-Bereich' }
$Regel = "Get-NetFirewallRule -DisplayName 'Friday-PC' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; " +
         "New-NetFirewallRule -DisplayName 'Friday-PC' -Direction Inbound -Action Allow -Protocol TCP " +
         "-LocalPort $Port -RemoteAddress $Fern -Program '$Pythonw' -Profile Any | Out-Null"
$Admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
try {
    if ($Admin) { Invoke-Expression $Regel }
    else {
        Write-Host 'Firewall-Regel: Windows fragt jetzt nach Administratorrechten ...'
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $Regel)
    }
    Write-Host "Firewall: Port $Port offen nur fuer $Fern."
} catch {
    Write-Host "Firewall-Regel nicht gesetzt ($($_.Exception.Message)). Friday erreicht den PC dann evtl. nicht." -ForegroundColor Yellow
}

# 6. Autostart bei der Anmeldung (in Eikes Sitzung - sonst oeffnen sich Programme unsichtbar)
$Startup = [Environment]::GetFolderPath('Startup')
$Wsh = New-Object -ComObject WScript.Shell
$Lnk = $Wsh.CreateShortcut((Join-Path $Startup 'Friday-PC.lnk'))
$Lnk.TargetPath = $Pythonw
$Lnk.Arguments = '"' + $Skript + '"'
$Lnk.WorkingDirectory = $Ziel
$Lnk.Description = 'Friday-PC Helfer'
$Lnk.Save()

# 7. (Neu) starten
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*friday_pc.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 1
Start-Process -FilePath $Pythonw -ArgumentList ('"' + $Skript + '"') -WorkingDirectory $Ziel

# 8. Selbsttest
Start-Sleep -Seconds 3
try {
    $st = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status" -Headers @{ Authorization = 'Bearer __SCHLUESSEL__' } -TimeoutSec 15
    Write-Host ("Fertig: Friday-PC {0} laeuft auf {1}. Ordner: {2}" -f $st.version, $st.rechner, ($st.ordner -join ', ')) -ForegroundColor Green
    Write-Host 'Zurueck in Friday: Freigaben -> PC -> "Anmeldung pruefen".'
} catch {
    Write-Host "Der Helfer antwortet noch nicht ($($_.Exception.Message)). Log: $Ziel\friday_pc.log" -ForegroundColor Yellow
}
