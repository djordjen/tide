@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.uv-cache"

rem Local SQL Server deployment settings. Keep the whole SET assignment quoted:
rem the SQLAlchemy URL contains ampersands, which otherwise have meaning to cmd.exe.
set "TIDE_DATABASE_URL=mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"

rem Commands below the SQL Server group take an optional application name:
rem   start.bat web-demo contacts
rem No command names an application itself. Adding one means adding a single
rem settings block under :use_application -- the previous arrangement wanted a
rem copy of every command, and a copied command line drifts. `start.bat seed`
rem was passing `--customers` for weeks after the CLI replaced it with
rem `--count NAME=NUMBER`, and nothing noticed until a second application made
rem the duplication visible.

if /I "%~1"=="init" goto initialize
if /I "%~1"=="check" goto check
if /I "%~1"=="diff" goto diff
if /I "%~1"=="seed" goto seed
if /I "%~1"=="auditor" goto auditor
if /I "%~1"=="api" goto api
if /I "%~1"=="mcp" goto mcp
if /I "%~1"=="api-check" goto api_check
if /I "%~1"=="remote" goto remote
if /I "%~1"=="web" goto web
if /I "%~1"=="auth-user" goto auth_user
if /I "%~1"=="demo" goto demo
if /I "%~1"=="auditor-demo" goto auditor_demo
if /I "%~1"=="studio" goto studio
if /I "%~1"=="api-demo" goto api_demo
if /I "%~1"=="mcp-demo" goto mcp_demo
if /I "%~1"=="web-demo" goto web_demo
if /I "%~1"=="contacts-demo" goto contacts_demo
if /I "%~1"=="contacts-viewer-demo" goto contacts_viewer_demo
if /I "%~1"=="contacts-studio" goto contacts_studio
if /I "%~1"=="contacts-api-demo" goto contacts_api_demo
if /I "%~1"=="contacts-mcp-demo" goto contacts_mcp_demo
if /I "%~1"=="contacts-web-demo" goto contacts_web_demo
if /I "%~1"=="help" goto help
if not "%~1"=="" goto unknown

:start
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --role sales_clerk
goto finish

rem --- SQL Server deployment: Invoicing only, because it is the only
rem --- application with a managed SQL Server database behind it.

:initialize
echo Initializing the managed TIDE database and starting the application...
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --create-schema --role sales_clerk
goto finish

:check
echo Checking SQL Server connectivity, schema, durable state, and query support...
uv run --extra sqlserver tide db check applications/invoicing --database-env
goto finish

:diff
echo Comparing SQL Server with the compiled managed schema without writing...
uv run --extra sqlserver tide db diff applications/invoicing --database-env
goto finish

:seed
echo Seeding the empty managed TIDE database with deterministic fake data...
uv run --extra seed --extra sqlserver tide db seed applications/invoicing --database-env --role sales_clerk --count customers=25 --count products=20 --count invoices=100 --random-seed 20260716
goto finish

:auditor
echo Starting the read-only auditor workspace against SQL Server...
uv run --extra tui --extra sqlserver tide run applications/invoicing --database-env --role auditor
goto finish

:api
call :prepare_api_token
echo Starting the API against SQL Server...
uv run --extra api --extra client --extra report --extra sqlserver tide serve applications/invoicing --database-env --role sales_clerk --port 8000
goto finish

:mcp
call :prepare_api_token
echo Starting the API and secured runtime MCP server against SQL Server...
echo MCP clients connect to http://127.0.0.1:8000/mcp using the token above.
uv run --extra api --extra client --extra mcp --extra report --extra sqlserver tide serve applications/invoicing --database-env --role sales_clerk --role auditor --port 8000 --mcp
goto finish

:api_check
call :read_api_token
if errorlevel 1 goto finish
uv run --extra client tide api check-server applications/invoicing --url http://127.0.0.1:8000
goto finish

:remote
call :read_api_token
if errorlevel 1 goto finish
uv run --extra tui --extra client tide run applications/invoicing --api-url http://127.0.0.1:8000
goto finish

:web
call :use_application invoicing
if errorlevel 1 goto finish
call :ensure_local_auth
if errorlevel 1 goto finish
call :prepare_web
if errorlevel 1 goto finish
echo Starting the TIDE Web renderer against SQL Server...
echo Sign in with your local TIDE username and password.
call npm --prefix web run dev:sqlserver
goto finish

:auth_user
call :use_application invoicing
if errorlevel 1 goto finish
uv run tide auth create-user applications/%APP_ID% --store "%APP_STORE%" %APP_ROLES%
goto finish

rem --- Application-aware commands. Each one exists once; the application it
rem --- runs comes from :use_application.

:demo
call :use_application "%~2"
if errorlevel 1 goto finish
:run_demo
echo Starting the %APP_LABEL% application with isolated demo data...
uv run --extra tui tide run applications/%APP_ID% --demo %APP_DEMO_ROLE%
goto finish

:auditor_demo
call :use_application "%~2"
if errorlevel 1 goto finish
:run_viewer_demo
echo Starting the read-only %APP_LABEL% workspace with isolated demo data...
uv run --extra tui tide run applications/%APP_ID% --demo %APP_VIEWER_ROLE%
goto finish

:studio
call :use_application "%~2"
if errorlevel 1 goto finish
:run_studio
echo Opening %APP_LABEL% in TIDE Studio with in-memory property editing...
uv run --extra studio tide studio applications/%APP_ID%
goto finish

:api_demo
call :use_application "%~2"
if errorlevel 1 goto finish
:run_api_demo
call :prepare_api_token
echo Starting the %APP_LABEL% API with isolated demo data...
uv run --extra api --extra client %APP_REPORT% tide serve applications/%APP_ID% --demo %APP_ROLES% --port 8000
goto finish

:mcp_demo
call :use_application "%~2"
if errorlevel 1 goto finish
:run_mcp_demo
call :prepare_api_token
echo Starting the %APP_LABEL% API and secured runtime MCP with isolated demo data...
echo MCP clients connect to http://127.0.0.1:8000/mcp using the token above.
uv run --extra api --extra client --extra mcp %APP_REPORT% tide serve applications/%APP_ID% --demo %APP_ROLES% --port 8000 --mcp
goto finish

:web_demo
call :use_application "%~2"
if errorlevel 1 goto finish
:run_web_demo
call :ensure_local_auth
if errorlevel 1 goto finish
call :prepare_web
if errorlevel 1 goto finish
echo Starting the %APP_LABEL% Web renderer with isolated demo data...
echo Sign in with your local %APP_LABEL% username and password.
call npm --prefix web run dev:app -- --app %APP_ID% %APP_REPORT%
goto finish

rem --- Documented spellings kept working. `contacts-demo` is `demo contacts`;
rem --- a third application needs no entries here to be runnable.

:contacts_demo
call :use_application contacts
if errorlevel 1 goto finish
goto run_demo

:contacts_viewer_demo
call :use_application contacts
if errorlevel 1 goto finish
goto run_viewer_demo

:contacts_studio
call :use_application contacts
if errorlevel 1 goto finish
goto run_studio

:contacts_api_demo
call :use_application contacts
if errorlevel 1 goto finish
goto run_api_demo

:contacts_mcp_demo
call :use_application contacts
if errorlevel 1 goto finish
goto run_mcp_demo

:contacts_web_demo
call :use_application contacts
if errorlevel 1 goto finish
goto run_web_demo

rem --- One block per application. This is the only place an application is
rem --- named, and the only edit a new one needs.

:use_application
set "APP_ID=%~1"
if "%APP_ID%"=="" set "APP_ID=invoicing"
if /I "%APP_ID%"=="invoicing" goto app_invoicing
if /I "%APP_ID%"=="contacts" goto app_contacts
echo Unknown application: %APP_ID%
echo Known applications: invoicing, contacts
exit /b 2

:app_invoicing
set "APP_ID=invoicing"
set "APP_LABEL=Invoicing"
rem Deliberately empty: `tide run --demo` already starts the most capable role,
rem which is what this shortcut has always done for Invoicing.
set "APP_DEMO_ROLE="
set "APP_VIEWER_ROLE=--role auditor"
set "APP_ROLES=--role sales_clerk --role auditor"
set "APP_STORE=.tide\local-auth.sqlite3"
set "APP_REPORT=--extra report"
exit /b 0

:app_contacts
set "APP_ID=contacts"
set "APP_LABEL=Contacts"
set "APP_DEMO_ROLE=--role contact_editor"
set "APP_VIEWER_ROLE=--role contact_viewer"
set "APP_ROLES=--role contact_editor --role contact_viewer"
set "APP_STORE=.tide\contacts-local-auth.sqlite3"
rem Contacts declares no reports, so it does not request the report extra.
set "APP_REPORT="
exit /b 0

:read_api_token
set "TIDE_API_TOKEN="
for /f "delims=" %%I in ('powershell -NoProfile -Command "$s = Read-Host 'Paste API token' -AsSecureString; [System.Net.NetworkCredential]::new('', $s).Password"') do set "TIDE_API_TOKEN=%%I"
if not defined TIDE_API_TOKEN (
    echo No API token was entered.
    exit /b 1
)
exit /b 0

:prepare_web
where npm >nul 2>nul
if errorlevel 1 (
    echo Web startup failed: Node.js 20 or later with npm is required.
    exit /b 1
)
if not exist "web\node_modules\" (
    echo Installing the locked Web dependencies for this checkout...
    call npm --prefix web install
    if errorlevel 1 exit /b 1
)
exit /b 0

:ensure_local_auth
if exist "%APP_STORE%" exit /b 0
echo.
echo First-time %APP_LABEL% Web setup: create the local TIDE administrator.
echo Username: admin
echo The password is entered securely and is not saved in this batch file.
uv run tide auth create-user applications/%APP_ID% --store "%APP_STORE%" --username admin --display-name Administrator %APP_ROLES%
exit /b %ERRORLEVEL%

:prepare_api_token
if not defined TIDE_API_TOKEN for /f "delims=" %%I in ('powershell -NoProfile -Command "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')"') do set "TIDE_API_TOKEN=%%I"
echo.
echo Local development API token:
echo %TIDE_API_TOKEN%
echo Paste this token into Swagger Authorize or the TIDE Web connection screen.
echo This development token and server are restricted to this computer.
echo.
exit /b 0

:help
echo TIDE Windows shortcut
echo.
echo SQL Server deployment (Invoicing):
echo   start.bat init   Create missing managed tables, then start SQL Server mode
echo   start.bat        Start normally against the existing SQL Server database
echo   start.bat check  Read-only SQL Server connectivity and compatibility check
echo   start.bat diff   Read-only SQL Server migration proposal; never applies DDL
echo   start.bat seed   Seed an empty initialized database with fake data
echo   start.bat auditor Start read-only audit/report mode against SQL Server
echo   start.bat api    Start local API against SQL Server
echo   start.bat mcp    Start local API plus secured runtime MCP against SQL Server
echo   start.bat api-check Verify the running API and remote client contract
echo   start.bat remote Start the TUI as an API client with no database access
echo   start.bat web    Start the Web renderer and API against SQL Server
echo   start.bat auth-user Add a local Web user with invoicing roles
echo.
echo Any application, with demo data. Add an application name to choose one;
echo the default is invoicing. Known applications: invoicing, contacts.
echo   start.bat demo [application]         Start the TUI with demo data
echo   start.bat auditor-demo [application] Start the TUI in its read-only role
echo   start.bat studio [application]       Inspect and edit metadata in memory
echo   start.bat api-demo [application]     Start the REST API with demo data
echo   start.bat mcp-demo [application]     Start REST plus secured runtime MCP
echo   start.bat web-demo [application]     Start the Web renderer with demo data
echo   Example: start.bat web-demo contacts
echo.
echo   start.bat contacts-demo and the other contacts-* names remain as
echo     aliases for the same command with "contacts" as the application
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
