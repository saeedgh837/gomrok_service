; Inno Setup script for the IRICA panel installer.
; Built by lab/scripts/build_windows_installer.py, which pre-processes the
; __VERSION__ placeholder. Never edit the version here by hand.
;
; Design decisions (do not regress):
;   * DefaultDirName is {localappdata}\Programs\IRICA — no admin rights needed.
;   * The app is self-contained; user data lives OUTSIDE the install dir in
;     {localappdata}\irica and is NEVER touched by install or uninstall.
;   * Active work is never terminated. A self-update holds the maintenance
;     event until the original panel HANDLE exits; normal installs require
;     the operator to close installed application processes first.
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
CloseApplications=no
RestartApplications=no
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
  Flags: nowait postinstall skipifsilent; Check: NormalPanelLaunch
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\panel_tray.pyw"""; \
  WorkingDir: "{app}"; Description: "Reopen IRICA after the confirmed update"; \
  Flags: nowait; Check: SelfUpdatePanelLaunch

[UninstallDelete]
; Nothing here on purpose: user data in {localappdata}\irica must survive.
; Only leftovers inside the install dir itself are removed by Inno anyway.

[Code]
// The self-update protocol never closes processes. It retains the original
// panel HANDLE and the signaled admission event before acknowledging readiness.
// File replacement is allowed only after that HANDLE exits and a bounded native
// process inventory confirms the installation is idle. User data is not opened.

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7C0F4A2E-9B1D-4E3A-8F5C-IRICA-PANEL-01}_is1';
  WaitSignaled = 0;
  WaitTimedOut = 258;
  SynchronizeAccess = $00100000;
  EventModifyAccess = $00000002;
  QueryProcessAccess = $00001000;
  InvalidNativeHandle = -1;

type
  TNativeFileTime = record
    LowDateTime: Cardinal;
    HighDateTime: Cardinal;
  end;
  TNativeProcessEntry = record
    Size: Cardinal;
    Usage: Cardinal;
    ProcessID: Cardinal;
    DefaultHeapID: UINT_PTR;
    ModuleID: Cardinal;
    Threads: Cardinal;
    ParentProcessID: Cardinal;
    BasePriority: Integer;
    Flags: Cardinal;
    ExeFile: array[0..259] of Char;
  end;
  TTwoHandles = array[0..1] of THandle;
  TVersionParts = array[0..2] of Integer;
  TNativePathAttributes = record
    Attributes: Cardinal;
    ReparseTag: Cardinal;
  end;

function OpenNativePath(Name: String; Access, Sharing: Cardinal; Security: Integer;
  Creation, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function NativeFinalPath(Handle: THandle; Path: String; Size, Flags: Cardinal): Cardinal;
  external 'GetFinalPathNameByHandleW@kernel32.dll stdcall';
function NativePathAttributes(Handle: THandle; InformationClass: Integer;
  var Information: TNativePathAttributes; Size: Cardinal): Boolean;
  external 'GetFileInformationByHandleEx@kernel32.dll stdcall';

function OpenNativeProcess(Access: Cardinal; Inherit: Boolean; ProcessID: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function NativeProcessTimes(Process: THandle; var Created, Exited, Kernel, User: TNativeFileTime): Boolean;
  external 'GetProcessTimes@kernel32.dll stdcall';
function NativeProcessImage(Process: THandle; Flags: Cardinal; Image: String; var Size: Cardinal): Boolean;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function OpenNativeEvent(Access: Cardinal; Inherit: Boolean; Name: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function CreateNativeEvent(Attributes: Integer; ManualReset, InitialState: Boolean; Name: String): THandle;
  external 'CreateEventW@kernel32.dll stdcall';
function CreateNativeMutex(Attributes: Integer; InitialOwner: Boolean; Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';
function ReleaseNativeMutex(Mutex: THandle): Boolean;
  external 'ReleaseMutex@kernel32.dll stdcall';
function SetNativeEvent(Event: THandle): Boolean;
  external 'SetEvent@kernel32.dll stdcall';
function WaitNativeHandle(Handle: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function WaitNativeHandles(Count: Cardinal; var Handles: TTwoHandles; WaitAll: Boolean; Milliseconds: Cardinal): Cardinal;
  external 'WaitForMultipleObjects@kernel32.dll stdcall';
function CloseNativeHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function NativeLastError(): Cardinal;
  external 'GetLastError@kernel32.dll stdcall';
function ProcessSnapshot(Flags, ProcessID: Cardinal): THandle;
  external 'CreateToolhelp32Snapshot@kernel32.dll stdcall';
function FirstProcess(Snapshot: THandle; var Entry: TNativeProcessEntry): Boolean;
  external 'Process32FirstW@kernel32.dll stdcall';
function NextProcess(Snapshot: THandle; var Entry: TNativeProcessEntry): Boolean;
  external 'Process32NextW@kernel32.dll stdcall';

var
  SelfUpdateRequested, SelfUpdateAccepted, PanelExited: Boolean;
  SelfUpdateRoot: String;
  PanelHandle, GateHandle, ReadyHandle, CancelHandle: THandle;

procedure CloseOwnedHandle(var Handle: THandle);
begin
  if Handle <> 0 then
  begin
    CloseNativeHandle(Handle);
    Handle := 0;
  end;
end;

procedure CloseHandoffHandles();
begin
  CloseOwnedHandle(ReadyHandle);
  CloseOwnedHandle(CancelHandle);
  CloseOwnedHandle(PanelHandle);
  CloseOwnedHandle(GateHandle);
end;

function ArgumentValue(const Name: String): String;
var
  I, Matches: Integer;
  Prefix, Argument: String;
begin
  Result := '';
  Matches := 0;
  Prefix := '/' + Uppercase(Name) + '=';
  for I := 1 to ParamCount do
  begin
    Argument := ParamStr(I);
    if Uppercase(Copy(Argument, 1, Length(Prefix))) = Prefix then
    begin
      Matches := Matches + 1;
      Result := Copy(Argument, Length(Prefix) + 1, Length(Argument));
    end;
  end;
  if Matches <> 1 then
    Result := '';
end;

function HasSelfUpdateSwitch(): Boolean;
var
  I, Matches: Integer;
begin
  Matches := 0;
  for I := 1 to ParamCount do
    if Uppercase(ParamStr(I)) = '/IRICASELFUPDATE' then
      Matches := Matches + 1;
  Result := Matches = 1;
end;

function ValidNewPathPart(const Part: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if (Part = '') or (Part = '.') or (Part = '..') then exit;
  if (Part[Length(Part)] = '.') or (Part[Length(Part)] = ' ') then exit;
  for I := 1 to Length(Part) do
    if (Ord(Part[I]) < 32) or (Pos(Part[I], '\/:*?"<>|') > 0) then exit;
  Result := True;
end;

function CanonicalDirectory(const Directory: String): String;
var
  Candidate, Parent, Part, Suffix, Resolved: String;
  Handle: THandle;
  ErrorCode, Size: Cardinal;
  Depth: Integer;
  Information: TNativePathAttributes;
begin
  // ExpandFileName alone does not resolve junctions, short names or aliases.
  // Resolve the deepest existing ancestor by HANDLE, then append only validated
  // missing components. A fresh destination inherits its real parent's identity.
  Candidate := RemoveBackslashUnlessRoot(ExpandFileName(Directory));
  Suffix := '';
  Handle := THandle(InvalidNativeHandle);
  for Depth := 1 to 256 do
  begin
    Handle := OpenNativePath(Candidate, $80, 7, 0, 3, $02000000, 0);
    if Handle <> THandle(InvalidNativeHandle) then break;
    ErrorCode := NativeLastError();
    if (ErrorCode <> 2) and (ErrorCode <> 3) then
      RaiseException('The selected path identity cannot be verified. No files were replaced.');
    Part := ExtractFileName(Candidate);
    Parent := RemoveBackslashUnlessRoot(ExtractFileDir(Candidate));
    if (Parent = Candidate) or not ValidNewPathPart(Part) then
      RaiseException('The selected path contains an invalid or unresolved component.');
    if Suffix = '' then Suffix := Part else Suffix := Part + '\' + Suffix;
    Candidate := Parent;
  end;
  if Handle = THandle(InvalidNativeHandle) then
    RaiseException('The selected path exceeded the bounded ancestor check.');
  try
    if not NativePathAttributes(Handle, 9, Information, SizeOf(Information)) then
      RaiseException('The existing path attributes cannot be verified.');
    if (Suffix <> '') and ((Information.Attributes and $10) = 0) then
      RaiseException('The existing installation ancestor is not a directory.');
    SetLength(Resolved, 32768);
    Size := NativeFinalPath(Handle, Resolved, Length(Resolved), 0);
    if (Size = 0) or (Size >= 32768) then
      RaiseException('The final installation path cannot be verified.');
    SetLength(Resolved, Size);
  finally
    CloseNativeHandle(Handle);
  end;
  if Copy(Resolved, 1, 8) = '\\?\UNC\' then
    Resolved := '\\' + Copy(Resolved, 9, Length(Resolved))
  else if Copy(Resolved, 1, 4) = '\\?\' then
    Resolved := Copy(Resolved, 5, Length(Resolved));
  if Suffix <> '' then Resolved := AddBackslash(Resolved) + Suffix;
  Result := Lowercase(RemoveBackslashUnlessRoot(Resolved));
end;

function SelectedPathMatchesResolved(const Resolved: String): Boolean;
begin
  // Do not keep an alias in Inno's write target after checking a physical root.
  // Existing aliases and aliases in a fresh destination's ancestors are refused;
  // the operator can select the resolved directory without losing valid spaces.
  Result := Lowercase(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}')))) = Resolved;
end;

function IsWithin(const Filename, Directory: String): Boolean;
var
  Candidate, Root: String;
begin
  Candidate := CanonicalDirectory(Filename);
  Root := CanonicalDirectory(Directory);
  Result := (Candidate = Root) or (Pos(AddBackslash(Root), Candidate) = 1);
end;

function GateName(const Directory: String): String;
begin
  // Python hashes UTF-8 normcase(realpath(root)); use the same physical root.
  Result := 'Local\IRICA_' + Copy(GetSHA256OfString(UTF8Encode(CanonicalDirectory(Directory))), 1, 24) + '_install';
end;

function ParseVersion(const Version: String; var Parts: TVersionParts): Boolean;
var
  I, Separator: Integer;
  Remaining, Part: String;
begin
  Result := False;
  if (Length(Version) = 0) or (Length(Version) > 64) then
    exit;
  Remaining := Version;
  for I := 0 to 2 do
  begin
    Separator := Pos('.', Remaining);
    if I < 2 then
    begin
      if Separator = 0 then exit;
      Part := Copy(Remaining, 1, Separator - 1);
      Delete(Remaining, 1, Separator);
    end
    else
    begin
      if Separator <> 0 then exit;
      Part := Remaining;
    end;
    Parts[I] := StrToIntDef(Part, -1);
    if (Parts[I] < 0) or (Parts[I] > 65535) or (IntToStr(Parts[I]) <> Part) then
      exit;
  end;
  Result := True;
end;

function VersionPermitted(const Installed: String): Boolean;
var
  Previous, Incoming: TVersionParts;
  I: Integer;
begin
  Result := False;
  if not ParseVersion(Installed, Previous) or not ParseVersion('__VERSION__', Incoming) then
    exit;
  for I := 0 to 2 do
  begin
    if Incoming[I] > Previous[I] then
    begin
      Result := True;
      exit;
    end;
    if Incoming[I] < Previous[I] then exit;
  end;
  Result := True;
end;

function CheckInstalledVersion(const Directory: String): String;
var
  RegisteredRoot, RegisteredVersion: String;
  VersionText: AnsiString;
begin
  Result := '';
  if FileExists(AddBackslash(Directory) + 'VERSION') then
  begin
    if not LoadStringFromFile(AddBackslash(Directory) + 'VERSION', VersionText) then
    begin
      Result := 'The installed version cannot be read. No files were replaced.';
      exit;
    end;
    if not VersionPermitted(Trim(String(VersionText))) then
    begin
      Result := 'The installed version is invalid or newer. Downgrades are not permitted.';
      exit;
    end;
  end;
  if RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', RegisteredRoot) and
     (CanonicalDirectory(RegisteredRoot) = CanonicalDirectory(Directory)) then
  begin
    if not RegQueryStringValue(HKCU64, UninstallKey, 'DisplayVersion', RegisteredVersion) or
       not VersionPermitted(RegisteredVersion) then
      Result := 'The registered version is invalid or newer. Downgrades are not permitted.';
  end;
end;

function IsHex(const Value: String; RequiredLength: Integer): Boolean;
var
  I: Integer;
begin
  Result := False;
  if Length(Value) <> RequiredLength then exit;
  for I := 1 to Length(Value) do
    if Pos(Lowercase(Value[I]), '0123456789abcdef') = 0 then exit;
  Result := True;
end;

function HexFileTimeWord(Value: Cardinal): String;
var
  I: Integer;
  Digits: String;
begin
  Digits := '0123456789abcdef';
  Result := '';
  for I := 1 to 8 do
  begin
    Result := Digits[(Value and 15) + 1] + Result;
    Value := Value shr 4;
  end;
end;

function ProcessImage(Process: THandle; var Image: String): Boolean;
var
  Size: Cardinal;
begin
  Size := 32768;
  SetLength(Image, Size);
  Result := NativeProcessImage(Process, 0, Image, Size);
  if Result then
    SetLength(Image, Size)
  else
    Image := '';
end;

function PrepareSelfUpdate(): Boolean;
var
  RegisteredRoot, RegisteredApp, PIDText, CreatedText, Image: String;
  ReadyName, CancelName, Token, ExpectedGate: String;
  PID: Integer;
  Created, Exited, Kernel, User: TNativeFileTime;
begin
  Result := False;
  SelfUpdateRoot := ArgumentValue('DIR');
  if (SelfUpdateRoot = '') or not DirExists(SelfUpdateRoot) then exit;
  SelfUpdateRoot := CanonicalDirectory(SelfUpdateRoot);
  if not RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', RegisteredRoot) or
     not RegQueryStringValue(HKCU64, UninstallKey, 'Inno Setup: App Path', RegisteredApp) then exit;
  if (CanonicalDirectory(RegisteredRoot) <> SelfUpdateRoot) or
     (CanonicalDirectory(RegisteredApp) <> SelfUpdateRoot) then exit;
  if IsWithin(ExpandConstant('{localappdata}\irica'), SelfUpdateRoot) or
     IsWithin(SelfUpdateRoot, ExpandConstant('{localappdata}\irica')) then exit;
  if CheckInstalledVersion(SelfUpdateRoot) <> '' then exit;
  PIDText := ArgumentValue('IRICAPANELPID');
  PID := StrToIntDef(PIDText, 0);
  if (PID <= 0) or (IntToStr(PID) <> PIDText) then exit;
  CreatedText := ArgumentValue('IRICAPANELCREATED');
  if not IsHex(CreatedText, 16) then exit;
  ReadyName := ArgumentValue('IRICAREADY');
  if Copy(ReadyName, 1, 19) <> 'Local\IRICA_update_' then exit;
  Token := Copy(ReadyName, 20, 64);
  if not IsHex(Token, 64) or (ReadyName <> 'Local\IRICA_update_' + Token + '_ready') then exit;
  CancelName := ArgumentValue('IRICACANCEL');
  if CancelName <> 'Local\IRICA_update_' + Token + '_cancel' then exit;
  ExpectedGate := GateName(SelfUpdateRoot);
  if ArgumentValue('IRICAGATE') <> ExpectedGate then exit;
  PanelHandle := OpenNativeProcess(QueryProcessAccess or SynchronizeAccess, False, PID);
  if PanelHandle = 0 then exit;
  if WaitNativeHandle(PanelHandle, 0) <> WaitTimedOut then exit;
  if not NativeProcessTimes(PanelHandle, Created, Exited, Kernel, User) then exit;
  if HexFileTimeWord(Created.HighDateTime) + HexFileTimeWord(Created.LowDateTime) <>
     Lowercase(CreatedText) then exit;
  if not ProcessImage(PanelHandle, Image) then exit;
  Image := CanonicalDirectory(Image);
  if (Image <> AddBackslash(SelfUpdateRoot) + 'python\python.exe') and
     (Image <> AddBackslash(SelfUpdateRoot) + 'python\pythonw.exe') then exit;
  // Retain all handles before sending readiness; PID reuse cannot affect waits.
  GateHandle := OpenNativeEvent(SynchronizeAccess, False, ExpectedGate);
  if (GateHandle = 0) or (WaitNativeHandle(GateHandle, 0) <> WaitSignaled) then exit;
  CancelHandle := OpenNativeEvent(SynchronizeAccess, False, CancelName);
  if (CancelHandle = 0) or (WaitNativeHandle(CancelHandle, 0) <> WaitTimedOut) then exit;
  ReadyHandle := OpenNativeEvent(EventModifyAccess, False, ReadyName);
  if ReadyHandle = 0 then exit;
  if not SetNativeEvent(ReadyHandle) then exit;
  SelfUpdateAccepted := True;
  Result := True;
end;

function InitializeSetup(): Boolean;
begin
  SelfUpdateRequested := HasSelfUpdateSwitch();
  Result := True;
  if SelfUpdateRequested then
  begin
    Result := PrepareSelfUpdate();
    if not Result then
    begin
      CloseHandoffHandles();
      SuppressibleMsgBox('The confirmed self-update handoff is invalid. Keep the panel open and try again.', mbError, MB_OK, IDOK);
    end;
  end;
end;

function EntryName(const Entry: TNativeProcessEntry): String;
var
  I: Integer;
begin
  Result := '';
  for I := 0 to 259 do
  begin
    if Entry.ExeFile[I] = #0 then exit;
    Result := Result + Entry.ExeFile[I];
  end;
end;

function CandidateProcess(const Name, Directory: String): Boolean;
begin
  Result := (CompareText(Name, 'python.exe') = 0) or
    (CompareText(Name, 'pythonw.exe') = 0) or
    FileExists(AddBackslash(Directory) + Name) or
    FileExists(AddBackslash(Directory) + 'python\' + Name) or
    FileExists(AddBackslash(Directory) + '.tools\' + Name);
end;

function InstalledProcessCheck(const Directory: String): String;
var
  Snapshot, Process: THandle;
  Entry: TNativeProcessEntry;
  Found: Boolean;
  Name, Image: String;
  Count, WaitResult: Cardinal;
begin
  Result := '';
  Snapshot := ProcessSnapshot(2, 0);
  if Snapshot = THandle(InvalidNativeHandle) then
  begin
    Result := 'Active application processes cannot be inspected. No files were replaced.';
    exit;
  end;
  try
    Entry.Size := SizeOf(Entry);
    Found := FirstProcess(Snapshot, Entry);
    Count := 0;
    while Found do
    begin
      Count := Count + 1;
      if Count > 32768 then
      begin
        Result := 'The active-process inventory exceeded its safety limit.';
        exit;
      end;
      Name := EntryName(Entry);
      if CandidateProcess(Name, Directory) then
      begin
        Process := OpenNativeProcess(QueryProcessAccess or SynchronizeAccess, False, Entry.ProcessID);
        if Process = 0 then
        begin
          if (NativeLastError() <> 87) and (NativeLastError() <> 1168) then
          begin
            Result := 'An application process cannot be verified. Close IRICA and retry.';
            exit;
          end;
        end
        else
        begin
          try
            WaitResult := WaitNativeHandle(Process, 0);
            if WaitResult = WaitTimedOut then
            begin
              if not ProcessImage(Process, Image) then
              begin
                if WaitNativeHandle(Process, 0) <> WaitSignaled then
                begin
                  Result := 'An application process location cannot be verified. Close IRICA and retry.';
                  exit;
                end;
              end
              else if IsWithin(Image, Directory) then
              begin
                Result := 'IRICA is still running. Close the panel and let all jobs finish before installing.';
                exit;
              end;
            end
            else if WaitResult <> WaitSignaled then
            begin
              Result := 'An application process state cannot be verified. No files were replaced.';
              exit;
            end;
          finally
            CloseNativeHandle(Process);
          end;
        end;
      end;
      Found := NextProcess(Snapshot, Entry);
    end;
    if NativeLastError() <> 18 then
      Result := 'The complete active-process inventory cannot be verified. No files were replaced.';
  finally
    CloseNativeHandle(Snapshot);
  end;
end;

function AcquireNormalInstallGate(const Directory: String): String;
var
  Mutex, Existing: THandle;
  Acquired: Boolean;
  Name: String;
  WaitResult: Cardinal;
begin
  Result := '';
  Name := GateName(Directory);
  Mutex := CreateNativeMutex(0, False, Copy(Name, 1, Length(Name) - 8) + '_admission');
  if Mutex = 0 then
  begin
    Result := 'The installation admission guard cannot be opened.';
    exit;
  end;
  Acquired := False;
  try
    WaitResult := WaitNativeHandle(Mutex, 5000);
    Acquired := (WaitResult = WaitSignaled) or (WaitResult = 128);
    if not Acquired then
    begin
      Result := 'IRICA is starting work. Close the panel and retry installation.';
      exit;
    end;
    Existing := OpenNativeEvent(SynchronizeAccess, False, Name);
    if Existing <> 0 then
    begin
      CloseNativeHandle(Existing);
      Result := 'Another IRICA maintenance operation is in progress. Retry when it finishes.';
      exit;
    end;
    if NativeLastError() <> 2 then
    begin
      Result := 'The installation admission state cannot be verified.';
      exit;
    end;
    GateHandle := CreateNativeEvent(0, True, True, Name);
    if GateHandle = 0 then
    begin
      Result := 'New application work cannot be paused safely.';
      exit;
    end;
    Result := InstalledProcessCheck(Directory);
    if Result <> '' then CloseOwnedHandle(GateHandle);
  finally
    if Acquired then ReleaseNativeMutex(Mutex);
    CloseNativeHandle(Mutex);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Directory: String;
  Handles: TTwoHandles;
  WaitResult: Cardinal;
begin
  Directory := CanonicalDirectory(ExpandConstant('{app}'));
  if not SelectedPathMatchesResolved(Directory) then
  begin
    Result := 'The selected installation path is an alias. Select its resolved directory and retry.';
    exit;
  end;
  Result := CheckInstalledVersion(Directory);
  if Result <> '' then exit;
  if IsWithin(ExpandConstant('{localappdata}\irica'), Directory) or
     IsWithin(Directory, ExpandConstant('{localappdata}\irica')) then
  begin
    Result := 'The application directory must be separate from the IRICA user data directory.';
    exit;
  end;
  if SelfUpdateRequested then
  begin
    if not SelfUpdateAccepted or (Directory <> SelfUpdateRoot) then
    begin
      Result := 'The self-update target does not match the registered installation.';
      exit;
    end;
    Handles[0] := CancelHandle;
    Handles[1] := PanelHandle;
    WaitResult := WaitNativeHandles(2, Handles, False, 60000);
    if (WaitResult <> 1) or (WaitNativeHandle(CancelHandle, 0) <> WaitTimedOut) then
    begin
      Result := 'The panel did not exit safely within 60 seconds or canceled the update. No files were replaced.';
      exit;
    end;
    PanelExited := True;
    Result := InstalledProcessCheck(Directory);
  end
  else
  begin
    CloseOwnedHandle(GateHandle);
    Result := AcquireNormalInstallGate(Directory);
  end;
end;

function NormalPanelLaunch(): Boolean;
begin
  Result := not SelfUpdateRequested;
  if Result then CloseOwnedHandle(GateHandle);
end;

function SelfUpdatePanelLaunch(): Boolean;
begin
  Result := SelfUpdateRequested and SelfUpdateAccepted and PanelExited;
  if Result then CloseOwnedHandle(GateHandle);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Replacement has finished; release admission before either relaunch entry.
  if CurStep = ssPostInstall then CloseOwnedHandle(GateHandle);
end;

procedure DeinitializeSetup();
begin
  CloseHandoffHandles();
end;
