#Requires -Version 5.1
param(
    [switch]$SkipSpeaking,
    [switch]$SkipGaze,
    [switch]$SkipFaceDetect,
    [switch]$SkipAudio,
    [switch]$NoFrontend,
    [switch]$GazeMock,
    [switch]$Remote,
    [string]$FaceRemoteUrl = "",
    [ValidateSet("cuda", "cpu")]
    [string]$Device = "cuda",
    [switch]$Release,
    [switch]$NoFaceBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# Fixed conda env names (override python via VPET_*_PYTHON if needed)
$CondaEnvF5 = "F5TTS"
$CondaEnvGaze = "GAZE"
$CondaEnvFace = "FACE"
$CondaEnvAudio = "AUDIO"

function Write-Step([string]$msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

function Read-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Key
    )
    if (-not (Test-Path -LiteralPath $FilePath)) { return $null }
    foreach ($line in Get-Content -LiteralPath $FilePath -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed -match '^\s*([^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            if ($name -ne $Key) { continue }
            $value = $matches[2].Trim()
            if (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

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
if ($Remote) {
    Write-Host "  Face : REMOTE relay on :8000 (no local infer)"
} else {
    Write-Host "  Face : LOCAL face-detect-local on :8000"
}
Write-Host "  Ports: Speaking 8765 | Gaze 8766 | Face 8000 | Audio 8010"
Write-Host "  Envs : Speaking -> conda F5TTS | Gaze -> conda GAZE | Face -> conda FACE | Audio -> conda AUDIO"
Write-Host ""

if (-not $SkipSpeaking) {
    $wd = Join-Path $Root "VPet-Speaking\Local_model\Fast_generating"
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
    $launch = Build-CondaLaunch -EnvName $CondaEnvGaze -OverridePath $env:VPET_GAZE_PYTHON `
        -ScriptArgs $script
    Write-Ok ("Gaze env: {0}" -f $launch.Source)
    Start-BackendWindow -Title "VPet-Gaze :8766" -WorkingDir $wd `
        -CommandLine $launch.CommandLine | Out-Null
} else {
    Write-Step "Skip Gaze"
}

$FaceDetectStarted = $false
if (-not $SkipFaceDetect) {
    if ($Remote) {
        $remoteUrl = $FaceRemoteUrl
        if (-not $remoteUrl) {
            $remoteUrl = Read-DotEnvValue -FilePath (Join-Path $Root ".env") -Key "FACE_REMOTE_URL"
        }
        if (-not $remoteUrl) {
            $remoteUrl = $env:FACE_REMOTE_URL
        }
        if (-not $remoteUrl) {
            Write-Warn "Remote mode: set FACE_REMOTE_URL in .env or pass -FaceRemoteUrl http://host:8000"
            Write-Step "Skip FaceDetect relay"
        } else {
            $wd = Join-Path $Root "face-detect-remote\backend"
            $launch = Build-CondaLaunch -EnvName $CondaEnvFace -OverridePath $env:VPET_FACE_PYTHON `
                -ScriptArgs "relay.py"
            Write-Ok ("FaceDetect relay env: {0}" -f $launch.Source)
            Write-Ok ("FaceDetect remote target: {0}" -f $remoteUrl)
            $faceCmd = ('set FACE_REMOTE_URL=' + $remoteUrl + '& ') + $launch.CommandLine
            Start-BackendWindow -Title "VPet-FaceDetect RELAY :8000" -WorkingDir $wd `
                -CommandLine $faceCmd | Out-Null
            $FaceDetectStarted = $true
        }
    } else {
        $wd = Join-Path $Root "face-detect-local\backend"
        $launch = Build-CondaLaunch -EnvName $CondaEnvFace -OverridePath $env:VPET_FACE_PYTHON `
            -ScriptArgs "server.py"
        Write-Ok ("FaceDetect local env: {0}" -f $launch.Source)
        # HF_ENDPOINT：multitask 权重未放本地时用镜像，避免 WinError 10054
        # 默认从 Gaze :8766/camera/jpeg 拉帧，可与视线同时开、不抢摄像头
        $faceCmd = 'set HF_ENDPOINT=https://hf-mirror.com& set FACE_USE_GAZE_CAMERA=1& set FACE_GAZE_JPEG_URL=http://127.0.0.1:8766/camera/jpeg& ' + $launch.CommandLine
        Start-BackendWindow -Title "VPet-FaceDetect LOCAL :8000" -WorkingDir $wd `
            -CommandLine $faceCmd | Out-Null
        $FaceDetectStarted = $true
    }
} else {
    Write-Step "Skip FaceDetect"
}

# 浏览器推流页：等 :8000/health 就绪后自动打开（VPet 情绪陪伴靠此推流更新 /latest）
$FaceTestUrl = "http://127.0.0.1:8000/test-frontend/"
if ($FaceDetectStarted -and -not $NoFaceBrowser) {
    Write-Step "Wait for FaceDetect :8000, then open browser test page..."
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($ready) {
        Start-Process $FaceTestUrl
        Write-Ok ("Opened FaceDetect page: {0}" -f $FaceTestUrl)
        Write-Host "  Allow camera in the browser tab so VPet can poll /latest."
    } else {
        Write-Warn "FaceDetect health not ready yet; open manually:"
        Write-Warn ("  {0}" -f $FaceTestUrl)
    }
} elseif ($FaceDetectStarted -and $NoFaceBrowser) {
    Write-Step ("Skip FaceDetect browser (-NoFaceBrowser). Page: {0}" -f $FaceTestUrl)
}

if (-not $SkipAudio) {
    $wd = Join-Path $Root "audio\backend"
    $launch = Build-CondaLaunch -EnvName $CondaEnvAudio -OverridePath $env:VPET_AUDIO_PYTHON `
        -ScriptArgs "main.py"
    $cmd = 'set AUDIO_PORT=8010& ' + $launch.CommandLine
    Write-Ok ("Audio env: {0}" -f $launch.Source)
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
if ($Remote) {
    Write-Host "  FaceDetect: browser -> 127.0.0.1:8000 (relay) -> remote GPU"
} else {
    Write-Host "  FaceDetect LOCAL: pulls frames from Gaze /camera/jpeg (no need to close Gaze)"
}
Write-Host "  VPet DIY: 启动情绪陪伴 (polls /latest)"
Write-Host ""
