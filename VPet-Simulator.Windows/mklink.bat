@echo off
setlocal

rem Create a directory link only when destination does not already exist.
call :LinkDir "%~dp0\bin\x64\Debug\net8.0-windows\mod" "%~dp0\mod"

echo The following is the automatic link generation for other related MODs.

call :LinkDir "%~dp0\bin\x86\Debug\net8.0-windows\mod" "%~dp0\mod"
call :LinkDir "%~dp0\bin\x64\Release\net8.0-windows\mod" "%~dp0\mod"
call :LinkDir "%~dp0\..\VPet.Solution\bin\Debug\net8.0-windows\mod" "%~dp0\mod"

call :LinkDir "%~dp0\mod\0001_ModMaker" "%~dp0\..\..\VPet.ModMaker\0001_ModMaker"
call :LinkDir "%~dp0\mod\1100_DemoClock" "%~dp0\..\..\VPet.Plugin.Demo\VPet.Plugin.DemoClock\1100_DemoClock"
call :LinkDir "%~dp0\mod\1111_ChatGPTPlus" "%~dp0\..\..\VPet.Plugin.ChatGPTPlus\VPet.Plugin.ChatGPTPlus\1111_ChatGPTPlus"
call :LinkDir "%~dp0\mod\1101_EdgeTTS" "%~dp0\..\..\VPet.Plugin.Demo\VPet.Plugin.EdgeTTS\1101_EdgeTTS"
call :LinkDir "%~dp0\mod\1110_ChatGPT" "%~dp0\..\..\VPet.Plugin.Demo\VPet.Plugin.ChatGPT\1110_ChatGPT"
call :LinkDir "%~dp0\mod\1111_MutiPlayerStream" "%~dp0\..\..\VPet.Plugin.Demo\VPet.MutiPlayer.Stream\1111_MutiPlayerStream"
call :LinkDir "%~dp0\mod\1123_MutiRedEnvelope" "%~dp0\..\..\VPet.Plugin.Demo\VPet.Plugin.MutiRedEnvelope\1123_MutiRedEnvelope"

echo.
echo Done.
pause
exit /b 0

:LinkDir
set "DST=%~1"
set "SRC=%~2"
for %%I in ("%DST%") do set "DST_PARENT=%%~dpI"

if exist "%DST%" (
	echo [SKIP] Exists: "%DST%"
	exit /b 0
)

if not exist "%SRC%" (
	echo [SKIP] Source not found: "%SRC%"
	exit /b 0
)

if not exist "%DST_PARENT%" (
	echo [SKIP] Destination parent not found: "%DST_PARENT%"
	exit /b 0
)

mklink /d "%DST%" "%SRC%" >nul
if errorlevel 1 (
	echo [FAIL] "%DST%" ^<= "%SRC%"
	exit /b 1
)

echo [ OK ] "%DST%" ^<= "%SRC%"
exit /b 0