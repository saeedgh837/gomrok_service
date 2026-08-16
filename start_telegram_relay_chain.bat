@echo off
rem Keep the Telegram relay chain alive (self-healing watchdog, run via Startup VBS):
rem   cloudflare_http_proxy.py (127.0.0.1:18081) -> telegram_api_relay.py (127.0.0.1:8752) -> cloudflared named tunnel
rem
rem PITFALL: findstr /R "PATTERN WITH SPACE" splits the pattern on the space into
rem TWO OR-patterns, so ":18081 .*LISTENING" matched EVERY listening socket and the
rem checks below always reported "already running". Always use /C: for one literal regex.
setlocal
set HERE=C:\Users\Saeed\ai_projects\irica\laptop\irica
set PY=C:\Program Files\Python310\python.exe
set LOG=%HERE%\relay_chain_watchdog.log
cd /d "%HERE%"

rem 1) outbound HTTP CONNECT proxy -> authenticated SOCKS5 (h1.saeedvps.ir:1570)
netstat -ano | findstr /R /C:":18081 .*LISTENING" >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%PY%' -ArgumentList '-u','cloudflare_http_proxy.py' -WorkingDirectory '%HERE%'"
  echo %date% %time% started http_proxy 18081 >> "%LOG%"
)

rem 2) restricted Telegram Bot API relay (localhost:8752)
netstat -ano | findstr /R /C:":8752 .*LISTENING" >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%PY%' -ArgumentList '-u','telegram_api_relay.py' -WorkingDirectory '%HERE%'"
  echo %date% %time% started telegram_relay 8752 >> "%LOG%"
)

rem 3) named tunnel: telegram.techbysaeed.ir + register.techbysaeed.ir
tasklist /FI "IMAGENAME eq cloudflared.exe" | findstr /I /C:"cloudflared.exe" >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%HERE%\.tools\cloudflared.exe' -ArgumentList 'tunnel','--config','%HERE%\cloudflare_named_tunnel.yml','run' -WorkingDirectory '%HERE%\.tools'"
  echo %date% %time% started cloudflared >> "%LOG%"
)
