@echo off
setlocal
for %%I in ("%~dp0..") do set "WORKSPACE_ROOT=%%~fI"
set "TARGET=%~dp0ops\scripts\home-control-stack\stop-home-control-launcher.ps1"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" %*
)
endlocal
