@echo off
title AutoFix Pro - Pagina web
cd /d "%~dp0"
echo ================================================
echo   AutoFix Pro: iniciando la pagina web
echo   La primera vez tarda 1 o 2 minutos (instala todo)
echo ================================================
if not exist .venv\Scripts\python.exe (
  echo Creando el entorno...
  python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r requirements-demo.txt
if errorlevel 1 (
  echo.
  echo No se pudo instalar. Revisa tu internet y vuelve a intentar.
  pause
  exit /b 1
)
echo.
echo Listo. Abriendo la pagina en el navegador...
echo NO CIERRES esta ventana mientras uses la pagina.
echo Para terminar, cierra esta ventana o pulsa Ctrl + C.
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://127.0.0.1:8000/app"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
