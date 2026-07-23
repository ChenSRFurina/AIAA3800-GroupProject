@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在关闭 VPet 后端窗口、监听端口与前端进程...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'SilentlyContinue';" ^
  "$titles = @('VPet-Speaking','VPet-Gaze','VPet-FaceDetect','VPet-Audio','VPet one-click start');" ^
  "$ports = @(8765,8766,8000,8010);" ^
  "$pyMarkers = @('start_server.py','fast_gen.py','gaze_server.py','gaze_server_mock.py','face-detect-local\\backend\\server.py','face-detect-remote\\backend\\relay.py','audio\\backend\\main.py');" ^
  "Get-Process cmd,powershell -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "  try { $t = $_.MainWindowTitle } catch { $t = '' }" ^
  "  foreach ($p in $titles) { if ($t -like ('*' + $p + '*')) { Write-Host ('  stop window: ' + $t); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }" ^
  "};" ^
  "foreach ($port in $ports) {" ^
  "  Get-NetTCPConnection -LocalPort $port -State Listen,Established -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "    if ($_.OwningProcess -gt 0) {" ^
  "      Write-Host ('  stop port ' + $port + ' pid=' + $_.OwningProcess);" ^
  "      Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue;" ^
  "    }" ^
  "  }" ^
  "};" ^
  "$procList = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue;" ^
  "$procList | ForEach-Object {" ^
  "  $p = $_;" ^
  "  $isRunner = ($p.Name -ieq 'python.exe' -or $p.Name -ieq 'pythonw.exe' -or $p.Name -ieq 'cmd.exe' -or $p.Name -ieq 'powershell.exe');" ^
  "  if (-not $isRunner) { return };" ^
  "  $cmd = (($p.CommandLine + '')).ToLower();" ^
  "  foreach ($m in $pyMarkers) {" ^
  "    if ($m -and $cmd.Contains($m.ToLower())) {" ^
  "      Write-Host ('  stop proc: pid=' + $p.ProcessId + ' name=' + $p.Name);" ^
  "      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue;" ^
  "      break;" ^
  "    }" ^
  "  }" ^
  "};" ^
  "Get-Process 'VPet-Simulator.Windows' -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "  Write-Host ('  stop frontend PID ' + $_.Id); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue" ^
  "};" ^
  "Start-Sleep -Milliseconds 300;" ^
  "$left = @();" ^
  "foreach ($port in $ports) {" ^
  "  if (Get-NetTCPConnection -LocalPort $port -State Listen,Established -ErrorAction SilentlyContinue) { $left += $port }" ^
  "};" ^
  "if ($left.Count -gt 0) { Write-Host ('  警告: 仍有端口占用 -> ' + (($left | Sort-Object -Unique) -join ', ')) } else { Write-Host '  所有目标端口已释放' }"

echo.
echo 完成。
timeout /t 2 >nul
exit /b 0
