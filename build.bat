@echo off
setlocal
REM ============================================================
REM IDS Camera EXE Build Script (Windows)
REM ============================================================
REM Tek dosyalik (--onefile), pencereli (--windowed) bir EXE uretir.
REM
REM Hedef makinede mutlaka IDS Software Suite (uEye driver) kurulu
REM olmalidir, aksi halde pyueye/ueye_api_64.dll yuklenemez.
REM https://en.ids-imaging.com/download-ueye.html
REM ============================================================

cd /d %~dp0

echo.
echo [1/5] Sanal ortam hazirlaniyor...
if not exist ".venv" (
    py -3 -m venv .venv 2>nul
    if errorlevel 1 (
        python -m venv .venv
    )
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo HATA: Sanal ortam aktif edilemedi.
    pause
    exit /b 1
)

echo.
echo [2/5] Bagimliliklar yukleniyor...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo HATA: Bagimliliklar yuklenemedi.
    pause
    exit /b 1
)

echo.
echo [3/5] Onceki build temizleniyor...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist
if exist "IDSCamera.spec" del /q IDSCamera.spec

echo.
echo [4/5] EXE olusturuluyor (pencereli mod)...
pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name IDSCamera ^
  --collect-all pyueye ^
  --collect-submodules PyQt5 ^
  --hidden-import PyQt5.sip ^
  --hidden-import PyQt5.QtCore ^
  --hidden-import PyQt5.QtGui ^
  --hidden-import PyQt5.QtWidgets ^
  camera.py

if errorlevel 1 (
    echo HATA: PyInstaller basarisiz oldu.
    pause
    exit /b 1
)

echo.
echo [5/5] Tamamlandi!
echo ============================================================
echo  EXE konumu: %~dp0dist\IDSCamera.exe
echo ============================================================
echo.
echo  ONEMLI: Bu EXE'yi calistiracak HER bilgisayarda
echo  IDS Software Suite kurulu olmalidir!
echo  Indir: https://en.ids-imaging.com/download-ueye.html
echo.
pause
endlocal
