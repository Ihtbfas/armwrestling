@echo off
setlocal
pushd "%~dp0" || exit /b 1
for /f "tokens=2 delims=:" %%C in ('chcp') do set "stm32_previous_cp=%%C"
chcp 65001 >nul

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m tools.stm32_setup --launch
) else (
    where py.exe >nul 2>&1
    if errorlevel 1 (
        where python.exe >nul 2>&1
        if errorlevel 1 (
            echo 未找到 Python。请安装 Python 3.10 或更新版本，启用 Tcl/Tk，并将 Python 加入 PATH。
            cmd /c exit 1
        ) else (
            python -X utf8 -m tools.stm32_setup --launch
        )
    ) else (
        py -3 -X utf8 -m tools.stm32_setup --launch
    )
)
set "stm32_panel_exit=%errorlevel%"
if not "%stm32_panel_exit%"=="0" (
    echo STM32 联调助手未能启动。请检查上方的 Python 路径和依赖提示。
    pause
)
if defined stm32_previous_cp chcp %stm32_previous_cp% >nul
popd
exit /b %stm32_panel_exit%
