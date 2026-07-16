@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Local SQL Server deployment settings. Keep the whole SET assignment quoted:
rem the SQLAlchemy URL contains ampersands, which otherwise have meaning to cmd.exe.
set "TIDE_DATABASE_URL=mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"

if /I "%~1"=="init" goto initialize
if /I "%~1"=="seed" goto seed
if /I "%~1"=="demo" goto demo
if /I "%~1"=="api" goto api
if /I "%~1"=="api-demo" goto api_demo
if /I "%~1"=="help" goto help
if not "%~1"=="" goto unknown

:start
uv run tide run applications/invoicing --database-env --role sales_clerk --page-size 5
goto finish

:initialize
echo Initializing the managed TIDE database and starting the application...
uv run tide run applications/invoicing --database-env --create-schema --role sales_clerk --page-size 5
goto finish

:demo
uv run tide run applications/invoicing --demo --page-size 5
goto finish

:seed
echo Seeding the empty managed TIDE database with deterministic fake data...
uv run tide db seed applications/invoicing --database-env --customers 25 --products 20 --invoices 100 --random-seed 20260716
goto finish

:api
call :prepare_api_token
echo Starting the API against SQL Server...
uv run tide serve applications/invoicing --database-env --role sales_clerk --port 8000
goto finish

:api_demo
call :prepare_api_token
echo Starting the API with isolated demo data...
uv run tide serve applications/invoicing --demo --role sales_clerk --port 8000
goto finish

:prepare_api_token
if not defined TIDE_API_TOKEN for /f "delims=" %%I in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')"') do set "TIDE_API_TOKEN=%%I"
echo.
echo Local development API token:
echo %TIDE_API_TOKEN%
echo Paste this token into http://127.0.0.1:8000/docs using Authorize.
echo This development token and server are restricted to this computer.
echo.
exit /b 0

:help
echo TIDE Windows shortcut
echo.
echo   start.bat init   Create missing managed tables, then start SQL Server mode
echo   start.bat        Start normally against the existing SQL Server database
echo   start.bat seed   Seed an empty initialized database with fake data
echo   start.bat demo   Start isolated in-memory demo data
echo   start.bat api    Start local API against SQL Server
echo   start.bat api-demo Start local API with demo data
echo   start.bat help   Show this help
exit /b 0

:unknown
echo Unknown mode: %~1
echo Run "start.bat help" for available commands.
exit /b 2

:finish
set "TIDE_EXIT_CODE=%ERRORLEVEL%"
if not "%TIDE_EXIT_CODE%"=="0" pause
exit /b %TIDE_EXIT_CODE%
