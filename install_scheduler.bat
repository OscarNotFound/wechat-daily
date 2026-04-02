@echo off
REM ============================================
REM WeChat Daily - Windows 定时任务安装脚本
REM ============================================
REM 
REM 这个脚本会在 Windows 任务计划程序中创建一个定时任务，
REM 每天晚上 23:00 自动运行 WeChat Daily。
REM
REM 使用方法：右键 → 以管理员身份运行
REM ============================================

echo.
echo =============================================
echo   WeChat Daily - 安装定时任务
echo =============================================
echo.

REM 获取当前目录
set PROJECT_DIR=%~dp0
set PYTHON_PATH=python

REM 检查 Python 是否可用
%PYTHON_PATH% --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请确保已安装并添加到 PATH
    pause
    exit /b 1
)

echo 项目目录: %PROJECT_DIR%
echo Python: %PYTHON_PATH%
echo.

REM 创建运行脚本
echo 正在创建运行脚本...
(
echo @echo off
echo cd /d "%PROJECT_DIR%"
echo echo [%%date%% %%time%%] 开始执行 WeChat Daily >> logs\scheduler.log
echo %PYTHON_PATH% main.py >> logs\scheduler.log 2^>^&1
echo echo [%%date%% %%time%%] 执行完成 >> logs\scheduler.log
) > "%PROJECT_DIR%run_daily.bat"

echo 运行脚本已创建: %PROJECT_DIR%run_daily.bat
echo.

REM 创建定时任务
echo 正在创建定时任务...
echo 任务名称: WeChatDaily
echo 运行时间: 每天 23:00
echo.

schtasks /create /tn "WeChatDaily" /tr "\"%PROJECT_DIR%run_daily.bat\"" /sc daily /st 23:00 /f

if errorlevel 1 (
    echo.
    echo [错误] 创建定时任务失败
    echo 请确保以管理员身份运行此脚本
    pause
    exit /b 1
)

echo.
echo =============================================
echo   安装完成!
echo =============================================
echo.
echo 定时任务已创建，每天 23:00 自动运行。
echo.
echo 你可以通过以下方式管理：
echo   - 查看任务: schtasks /query /tn "WeChatDaily"
echo   - 手动运行: schtasks /run /tn "WeChatDaily"
echo   - 删除任务: schtasks /delete /tn "WeChatDaily" /f
echo   - 修改时间: schtasks /change /tn "WeChatDaily" /st 22:00
echo.
echo 日志文件: %PROJECT_DIR%logs\scheduler.log
echo.

pause
