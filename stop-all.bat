@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在关闭 VPet 后端窗口与前端进程...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$titles = @('VPet-Speaking','VPet-Gaze','VPet-FaceDetect','VPet-Audio');" ^
  "Get-Process cmd,powershell -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "  try { $t = $_.MainWindowTitle } catch { $t = '' }" ^
  "  foreach ($p in $titles) { if ($t -like ('*' + $p + '*')) { Write-Host ('  stop window: ' + $t); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }" ^
  "};" ^
  "Get-Process 'VPet-Simulator.Windows' -ErrorAction SilentlyContinue | ForEach-Object {" ^
  "  Write-Host ('  stop frontend PID ' + $_.Id); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue" ^
  "}"

echo.
echo 完成。
timeout /t 2 >nul
exit /b 0
