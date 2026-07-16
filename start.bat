@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Local SQL Server deployment settings. Keep the whole SET assignment quoted:
rem the SQLAlchemy URL contains ampersands, which otherwise have meaning to cmd.exe.
set "TIDE_DATABASE_URL=mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"

if /I "%~1"=="init" goto initialize
if /I "%~1"=="seed" goto seed
if /I "%~1"=="demo" goto demo
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

:help
echo TIDE Windows shortcut
echo.
echo   start.bat init   Create missing managed tables, then start SQL Server mode
echo   start.bat        Start normally against the existing SQL Server database
echo   start.bat seed   Seed an empty initialized database with fake data
echo   start.bat demo   Start isolated in-memory demo data
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
