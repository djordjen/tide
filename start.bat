@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.uv-cache"

rem Local SQL Server deployment settings. Keep the whole SET assignment quoted:
rem the SQLAlchemy URL contains ampersands, which otherwise have meaning to cmd.exe.
set "TIDE_DATABASE_URL=mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"

if /I "%~1"=="init" goto initialize
if /I "%~1"=="check" goto check
if /I "%~1"=="diff" goto diff
if /I "%~1"=="seed" goto seed
if /I "%~1"=="demo" goto demo
if /I "%~1"=="auditor" goto auditor
if /I "%~1"=="auditor-demo" goto auditor_demo
if /I "%~1"=="studio" goto studio
if /I "%~1"=="api" goto api
if /I "%~1"=="api-demo" goto api_demo
if /I "%~1"=="mcp" goto mcp
if /I "%~1"=="mcp-demo" goto mcp_demo
if /I "%~1"=="api-check" goto api_check
if /I "%~1"=="remote" goto remote
if /I "%~1"=="help" goto help
if not "%~1"=="" goto unknown

:start
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --role sales_clerk --page-size 5
goto finish

:initialize
echo Initializing the managed TIDE database and starting the application...
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --create-schema --role sales_clerk --page-size 5
goto finish

:check
echo Checking SQL Server connectivity, schema, durable state, and query support...
uv run --extra sqlserver tide db check applications/invoicing --database-env
goto finish

:diff
echo Comparing SQL Server with the compiled managed schema without writing...
uv run --extra sqlserver tide db diff applications/invoicing --database-env
goto finish

:demo
uv run --extra tui tide run applications/invoicing --demo --page-size 5
goto finish

:auditor
echo Starting the read-only auditor workspace against SQL Server...
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --role auditor --page-size 5
goto finish

:auditor_demo
echo Starting the read-only auditor workspace with isolated demo data...
uv run --extra tui tide run applications/invoicing --demo --role auditor --page-size 5
goto finish

:studio
echo Starting TIDE Studio with in-memory property editing...
uv run --extra studio tide studio applications/invoicing
goto finish

:seed
echo Seeding the empty managed TIDE database with deterministic fake data...
uv run --extra seed --extra sqlserver tide db seed applications/invoicing --database-env --customers 25 --products 20 --invoices 100 --random-seed 20260716
goto finish

:api
call :prepare_api_token
echo Starting the API against SQL Server...
uv run --extra api --extra client --extra sqlserver tide serve applications/invoicing --database-env --role sales_clerk --port 8000
goto finish

:api_demo
call :prepare_api_token
echo Starting the API with isolated demo data...
uv run --extra api --extra client tide serve applications/invoicing --demo --role sales_clerk --port 8000
goto finish

:mcp
call :prepare_api_token
echo Starting the API and secured runtime MCP server against SQL Server...
echo MCP clients connect to http://127.0.0.1:8000/mcp using the token above.
uv run --extra api --extra client --extra mcp --extra sqlserver tide serve applications/invoicing --database-env --role sales_clerk --role auditor --port 8000 --mcp
goto finish

:mcp_demo
call :prepare_api_token
echo Starting the API and secured runtime MCP server with isolated demo data...
echo MCP clients connect to http://127.0.0.1:8000/mcp using the token above.
uv run --extra api --extra client --extra mcp tide serve applications/invoicing --demo --role sales_clerk --role auditor --port 8000 --mcp
goto finish

:api_check
call :read_api_token
if errorlevel 1 goto finish
uv run --extra client tide api check-server applications/invoicing --url http://127.0.0.1:8000
goto finish

:remote
call :read_api_token
if errorlevel 1 goto finish
uv run --extra tui --extra client tide run applications/invoicing --api-url http://127.0.0.1:8000 --page-size 5
goto finish

:read_api_token
set "TIDE_API_TOKEN="
for /f "delims=" %%I in ('powershell -NoProfile -Command "$s = Read-Host 'Paste API token' -AsSecureString; [System.Net.NetworkCredential]::new('', $s).Password"') do set "TIDE_API_TOKEN=%%I"
if not defined TIDE_API_TOKEN (
    echo No API token was entered.
    exit /b 1
)
exit /b 0

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
echo   start.bat check  Read-only SQL Server connectivity and compatibility check
echo   start.bat diff   Read-only SQL Server migration proposal; never applies DDL
echo   start.bat seed   Seed an empty initialized database with fake data
echo   start.bat demo   Start isolated in-memory demo data
echo   start.bat auditor Start read-only audit/report mode against SQL Server
echo   start.bat auditor-demo Start read-only audit/report mode with demo data
echo   start.bat studio Inspect and edit application metadata in memory
echo   start.bat api    Start local API against SQL Server
echo   start.bat api-demo Start local API with demo data
echo   start.bat mcp    Start local API plus secured runtime MCP against SQL Server
echo   start.bat mcp-demo Start local API plus secured runtime MCP with demo data
echo   start.bat api-check Verify the running API and remote client contract
echo   start.bat remote Start the TUI as an API client with no database access
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
