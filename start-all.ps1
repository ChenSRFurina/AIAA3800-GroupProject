#Requires -Version 5.1
param(
    [switch]$SkipSpeaking,
    [switch]$SkipGaze,
    [switch]$SkipFaceDetect,
    [switch]$SkipAudio,
    [switch]$NoFrontend,
    [switch]$GazeMock,
    [ValidateSet("cuda", "cpu")]
    [string]$Device = "cuda",
    [switch]$Release
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Fixed conda env names (override python via VPET_*_PYTHON if needed)
$CondaEnvF5 = "F5TTS"
$CondaEnvFace = "FACE"
$AudioVenvPython = Join-Path $Root "audio\backend\.venv\Scripts\python.exe"

function Write-Step([string]$msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

function Get-CondaBase {
    if ($env:CONDA_ROOT -and (Test-Path -LiteralPath $env:CONDA_ROOT)) { return $env:CONDA_ROOT }
    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        try {
            $base = & conda info --base 2>$null
            if ($base) {
                $base = ($base | Select-Object -First 1).ToString().Trim()
                if (Test-Path -LiteralPath $base) { return $base }
            }
        } catch {}
    }
    foreach ($c in @(
        (Join-Path $env:USERPROFILE "miniconda3"),
        (Join-Path $env:USERPROFILE "anaconda3"),
        (Join-Path $env:USERPROFILE "mambaforge"),
        (Join-Path $env:USERPROFILE "miniforge3"),
        (Join-Path $env:LOCALAPPDATA "miniconda3"),
        (Join-Path $env:LOCALAPPDATA "anaconda3"),
        "C:\ProgramData\miniconda3",
        "C:\ProgramData\anaconda3"
    )) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

function Resolve-CondaEnvPython {
    param([Parameter(Mandatory = $true)][string]$EnvName)
    $bases = New-Object System.Collections.Generic.List[string]
    $condaBase = Get-CondaBase
    if ($condaBase) { [void]$bases.Add($condaBase) }
    foreach ($b in @(
        (Join-Path $env:USERPROFILE "miniconda3"),
        (Join-Path $env:USERPROFILE "anaconda3"),
        (Join-Path $env:USERPROFILE "mambaforge"),
        (Join-Path $env:USERPROFILE "miniforge3"),
        (Join-Path $env:LOCALAPPDATA "miniconda3"),
        (Join-Path $env:USERPROFILE ".conda")
    )) {
        if ($b -and (Test-Path -LiteralPath $b) -and -not $bases.Contains($b)) { [void]$bases.Add($b) }
    }

    foreach ($base in $bases) {
        foreach ($p in @(
            (Join-Path $base ("envs\{0}\python.exe" -f $EnvName)),
            (Join-Path $base ("{0}\python.exe" -f $EnvName))
        )) {
            if (Test-Path -LiteralPath $p) { return (Resolve-Path -LiteralPath $p).Path }
        }
    }
    return $null
}

function Build-CondaLaunch {
    param(
        [Parameter(Mandatory = $true)][string]$EnvName,
        [string]$OverridePath,
        [Parameter(Mandatory = $true)][string]$ScriptArgs
    )
    if ($OverridePath -and (Test-Path -LiteralPath $OverridePath)) {
        $py = (Resolve-Path -LiteralPath $OverridePath).Path
        return @{
            CommandLine = ('"' + $py + '" ' + $ScriptArgs)
            Source = $py
        }
    }
    $py = Resolve-CondaEnvPython -EnvName $EnvName
    if ($py) {
        return @{
            CommandLine = ('"' + $py + '" ' + $ScriptArgs)
            Source = ("conda:{0}" -f $EnvName)
        }
    }
    if (Get-Command conda -ErrorAction SilentlyContinue) {
        return @{
            CommandLine = ("conda run -n {0} --no-capture-output python {1}" -f $EnvName, $ScriptArgs)
            Source = ("conda run:{0}" -f $EnvName)
        }
    }
    Write-Warn ("Conda env '{0}' not found. Falling back to PATH python." -f $EnvName)
    return @{
        CommandLine = ("python {0}" -f $ScriptArgs)
        Source = "PATH"
    }
}

function Start-BackendWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$WorkingDir,
        [Parameter(Mandatory = $true)][string]$CommandLine
    )
    if (-not (Test-Path -LiteralPath $WorkingDir)) {
        Write-Warn ("Skip {0}: dir missing {1}" -f $Title, $WorkingDir)
        return $false
    }

    $inner = 'chcp 65001 >nul & cd /d "' + $WorkingDir + '" & title ' + $Title + ' & echo ===== ' + $Title + ' ===== & ' + $CommandLine
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $inner) -WorkingDirectory $WorkingDir | Out-Null
    Write-Ok ("Started: {0}" -f $Title)
    return $true
}

$config = if ($Release) { "Release" } else { "Debug" }
$Exe = Join-Path $Root ("VPet-Simulator.Windows\bin\x64\{0}\net8.0-windows\VPet-Simulator.Windows.exe" -f $config)

Write-Host ""
Write-Host "===================================================="
Write-Host "  VPet one-click start (backends + frontend)"
Write-Host "===================================================="
Write-Host ("  Root : {0}" -f $Root)
Write-Host "  Ports: Speaking 8765 | Gaze 8766 | Face 8000 | Audio 8010"
Write-Host "  Envs : Speaking/Gaze -> conda F5TTS | Face -> conda FACE | Audio -> audio\backend\.venv"
Write-Host ""

if (-not $SkipSpeaking) {
    $wd = Join-Path $Root "VPet-Speaking\Local_model\F5-TTS\Fast_generating"
    $launch = Build-CondaLaunch -EnvName $CondaEnvF5 -OverridePath $env:VPET_F5_PYTHON `
        -ScriptArgs ("start_server.py --device {0}" -f $Device)
    Write-Ok ("Speaking env: {0}" -f $launch.Source)
    Start-BackendWindow -Title "VPet-Speaking F5-TTS :8765" -WorkingDir $wd `
        -CommandLine $launch.CommandLine | Out-Null
} else {
    Write-Step "Skip Speaking"
}

if (-not $SkipGaze) {
    $wd = Join-Path $Root "VPet-Gaze\python"
    $script = if ($GazeMock) { "gaze_server_mock.py" } else { "gaze_server.py" }
    $launch = Build-CondaLaunch -EnvName $CondaEnvF5 -OverridePath $env:VPET_GAZE_PYTHON `
        -ScriptArgs $script
    Write-Ok ("Gaze env: {0}" -f $launch.Source)
    Start-BackendWindow -Title "VPet-Gaze :8766" -WorkingDir $wd `
        -CommandLine $launch.CommandLine | Out-Null
} else {
    Write-Step "Skip Gaze"
}

if (-not $SkipFaceDetect) {
    $wd = Join-Path $Root "face-detect\backend"
    $launch = Build-CondaLaunch -EnvName $CondaEnvFace -OverridePath $env:VPET_FACE_PYTHON `
        -ScriptArgs "server.py"
    Write-Ok ("FaceDetect env: {0}" -f $launch.Source)
    # HF_ENDPOINT：国内下载 py-feat 模型用镜像，避免 WinError 10054
    $faceCmd = 'set HF_ENDPOINT=https://hf-mirror.com& ' + $launch.CommandLine
    Start-BackendWindow -Title "VPet-FaceDetect :8000" -WorkingDir $wd `
        -CommandLine $faceCmd | Out-Null
} else {
    Write-Step "Skip FaceDetect"
}

if (-not $SkipAudio) {
    $wd = Join-Path $Root "audio\backend"
    if (Test-Path -LiteralPath $AudioVenvPython) {
        $cmd = 'set AUDIO_PORT=8010& "' + $AudioVenvPython + '" main.py'
        Write-Ok ("Audio env: {0}" -f $AudioVenvPython)
    } else {
        Write-Warn "Audio .venv not found. Run audio\setup.bat first (creates backend\.venv)."
        $UvCmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($UvCmd) {
            $cmd = "set AUDIO_PORT=8010& uv run main.py"
            Write-Ok "Audio fallback: uv run (will create/use .venv on sync)"
        } else {
            $cmd = "set AUDIO_PORT=8010& python main.py"
            Write-Warn "Audio fallback: PATH python"
        }
    }
    Start-BackendWindow -Title "VPet-Audio :8010" -WorkingDir $wd -CommandLine $cmd | Out-Null
} else {
    Write-Step "Skip Audio"
}

if (-not $NoFrontend) {
    if (-not (Test-Path -LiteralPath $Exe)) {
        Write-Warn ("Frontend missing: {0}" -f $Exe)
        Write-Warn ("Build first: dotnet build VPet.sln -c {0} -p:Platform=x64" -f $config)
        Write-Warn "First run (Admin): VPet-Simulator.Windows\mklink.bat"
    } else {
        Write-Step "Wait 3s for backends, then start frontend..."
        Start-Sleep -Seconds 3
        Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe) | Out-Null
        Write-Ok ("Frontend started: {0}" -f $Exe)
    }
} else {
    Write-Step "Skip frontend (-NoFrontend)"
}

Write-Host ""
Write-Ok "Launch requests sent. Close a backend window to stop that service."
Write-Host "  DIY menu: Speak / Gaze / FaceDetect / Audio"
Write-Host ""
