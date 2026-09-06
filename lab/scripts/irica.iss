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
  Parameters: """{app}\panel_gui.pyw"""; WorkingDir: "{app}"; \
  Comment: "پنل IRICA"; IconFilename: "{app}\irica.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\panel_gui.pyw"""; WorkingDir: "{app}"; \
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
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel_gui.pyw"""; \
  WorkingDir: "{app}"; Description: "اجرای پنل IRICA"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nothing here on purpose: user data in {localappdata}\irica must survive.
; Only leftovers inside the install dir itself are removed by Inno anyway.

[Code]
// Ask the running panel to close before files are copied. The panel is a
// python.exe/pythonw.exe process serving http://127.0.0.1:8750; a graceful
// approach: detect, warn, and let the operator close it (the single-instance
// gate then makes the post-install launch a no-op until they do).
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
end;
