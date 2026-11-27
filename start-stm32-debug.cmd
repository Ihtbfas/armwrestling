@echo off
setlocal
pushd "%~dp0" || exit /b 1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m tools.stm32_debug
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        python -m tools.stm32_debug
    ) else (
        py -3 -m tools.stm32_debug
    )
)
set "stm32_panel_exit=%errorlevel%"
popd
if not "%stm32_panel_exit%"=="0" pause
exit /b %stm32_panel_exit%
