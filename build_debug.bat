@echo off
setlocal
REM ============================================================
REM IDS Camera EXE - DEBUG BUILD
REM ============================================================
REM Bu script konsol penceresi acik (--console) bir EXE uretir.
REM Hedef makinede sorun yasaniyorsa logdan hatayi gormek icin
REM kullanilir. Dagitilacak surum icin build.bat kullanin.
REM ============================================================

cd /d %~dp0

if not exist ".venv" (
    py -3 -m venv .venv 2>nul
    if errorlevel 1 ( python -m venv .venv )
)

call .venv\Scripts\activate.bat

python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 ( pause & exit /b 1 )

if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist
if exist "IDSCamera.spec" del /q IDSCamera.spec

echo.
echo Debug EXE olusturuluyor (konsol acik)...
pyinstaller ^
  --noconfirm ^
  --onefile ^
  --console ^
  --name IDSCamera_debug ^
  --collect-all pyueye ^
  --collect-submodules PyQt5 ^
  --hidden-import PyQt5.sip ^
  --hidden-import PyQt5.QtCore ^
  --hidden-import PyQt5.QtGui ^
  --hidden-import PyQt5.QtWidgets ^
  camera.py

if errorlevel 1 ( pause & exit /b 1 )

echo.
echo Tamamlandi: dist\IDSCamera_debug.exe
echo CMD'den calistirip log mesajlarini gorebilirsiniz.
pause
endlocal
