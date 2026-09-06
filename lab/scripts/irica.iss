; Inno Setup script for the IRICA panel installer.
; Built by lab/scripts/build_windows_installer.py, which pre-processes the
; __VERSION__ placeholder. Never edit the version here by hand.
;
; Design decisions (do not regress):
;   * DefaultDirName is {localappdata}\Programs\IRICA — no admin rights needed.
;   * The app is self-contained; user data lives OUTSIDE the install dir in
;     {localappdata}\irica and is NEVER touched by install or uninstall.
;   * CloseApplication is used instead of killing processes: the panel asks
;     the operator. The single-instance gate in panel.py prevents a corrupt
;     restart in between.
;   * Everything is per-user (PrivilegesRequired=lowest) — no UAC prompt.

#define AppName "IRICA"
#define AppExeName "IRICA Panel"
#define AppPublisher "saeedgh837"
#define AppURL "https://github.com/saeedgh837/gomrok_service"

[Setup]
AppId={{7C0F4A2E-9B1D-4E3A-8F5C-IRICA-PANEL-01}
AppName={#AppName}
AppVersion=__VERSION__
AppVerName={#AppName} __VERSION__
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={localappdata}\Programs\{#AppName}
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=IRICA-Setup-v__VERSION__
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\python\pythonw.exe
SetupIconFile=irica.ico
Uninstallable=yes
CreateUninstallRegKey=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
en.WelcomeLabel2=این نصب‌کننده، پنل IRICA را روی این کامپیوتر نصب/به‌روزرسانی می‌کند.%n%nتنظیمات و پروفایل‌های شما در پوشهٔ دادهٔ جدا نگهداری می‌شوند و با آپدیت دست نمی‌خورند.
en.SelectDirLabel=پوشهٔ نصب را انتخاب کنید
en.SelectDirDesc=پنل کجا نصب شود؟
en.ReadyLabel2d=برای شروع نصب، روی نصب کلیک کنید.

[Tasks]
Name: "desktopicon"; Description: "ساخت میان‌بر روی دسکتاپ"; GroupDescription: "میان‌برها:"
Name: "autostart"; Description: "اجرای خودکار پنل بعد از روشن شدن ویندوز"; \
  GroupDescription: "راه‌اندازی:"; Flags: unchecked

[Files]
; The whole staged bundle (python runtime, app modules, solvers, .tools).
; StageDir is passed by build_windows_installer.py as /DStageDir=<abs path>.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\panel_tray.pyw"""; WorkingDir: "{app}"; \
  Comment: "پنل IRICA"; IconFilename: "{app}\irica.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\panel_tray.pyw"""; WorkingDir: "{app}"; \
  Tasks: desktopicon; Comment: "پنل IRICA"; IconFilename: "{app}\irica.ico"
Name: "{autoprograms}\{#AppName}\پوشهٔ داده"; Filename: "{localappdata}\irica"; \
  Comment: "پوشهٔ دادهٔ پنل (تنظیمات، پروفایل‌ها، لاگ‌ها)"
Name: "{autoprograms}\{#AppName}\حذف نصب {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; Autostart task only (unchecked by default)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "IRICA Panel"; \
  ValueData: """{app}\python\pythonw.exe"" ""{app}\panel_gui.pyw"""; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel_tray.pyw"""; \
  WorkingDir: "{app}"; Description: "اجرای پنل IRICA"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nothing here on purpose: user data in {localappdata}\irica must survive.
; Only leftovers inside the install dir itself are removed by Inno anyway.

[Code]
// If the panel is running from the install dir, its python/pythonw processes
// hold the bundled DLLs and file replacement fails with "DeleteFile failed;
// code 5". Detect the panel (HTTP probe on 127.0.0.1:8750), warn the
// operator, and force-stop every python process running from {app} — that
// includes robot children launched by the panel. The profile store uses
// atomic writes, so a force-stop between jobs is safe.

function PanelIsRunning(): Boolean;
var
  Http: Variant;
  Body: String;
begin
  Result := False;
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.SetTimeouts(1500, 1500, 1500, 1500);
    Http.Open('GET', 'http://127.0.0.1:8750/', False);
    Http.Send('');
    // 200 + our markup = this project's panel; other services do not block us
    if Http.Status = 200 then
    begin
      Body := Http.ResponseText;
      Result := (Pos('site-1', Body) > 0);
    end;
  except
    Result := False;
  end;
end;

procedure KillPanelProcesses(const AppDir: String);
var
  Script, ScriptPath: String;
  ResultCode: Integer;
begin
  Script :=
    '$ErrorActionPreference = ''SilentlyContinue''' + #13#10 +
    '$dir = ''' + AppDir + '''' + #13#10 +
    '$procs = Get-CimInstance Win32_Process -Filter "Name=''pythonw.exe'' OR Name=''python.exe''" | ' +
    'Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) }' + #13#10 +
    'foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force }' + #13#10 +
    'if ($procs) { Start-Sleep -Seconds 2 }' + #13#10;
  ScriptPath := ExpandConstant('{tmp}\close-irica-panel.ps1');
  SaveStringToFile(ScriptPath, Script, False);
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  DeleteFile(ScriptPath);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  // Close any python/pythonw launched from {app} even if the HTTP probe
  // missed it (a crashed-but-locked process still holds the DLLs).
  if PanelIsRunning() then
  begin
    if MsgBox('پنل IRICA در حال اجراست.' #13#10 #13#10 +
              'نصب‌کننده پنل را می‌بندد و نصب را ادامه می‌دهد.' #13#10 +
              'اگر ربات یا استعلامی در حال اجرا باشد ممکن است متوقف شود.' #13#10 #13#10 +
              'ادامه می‌دهید؟', mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := 'نصب لغو شد — ابتدا پنل IRICA را ببندید و دوباره تلاش کنید.';
      exit;
    end;
  end;
  // Always sweep {app} processes: HTTP probe or not, they lock the DLLs.
  KillPanelProcesses(ExpandConstant('{app}'));
  if PanelIsRunning() then
  begin
    // Force-stopped panel still answering: give it one settle window.
    Sleep(2000);
    if PanelIsRunning() then
      Result := 'پنل IRICA بسته نشد — لطفاً خودتان پنل را ببندید و دوباره تلاش کنید.';
  end;
end;
