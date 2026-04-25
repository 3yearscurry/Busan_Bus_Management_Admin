#define AppName "부산광역시 실시간 버스 안내"
#define AppExe "부산실시간버스안내.exe"
#define AppDir "dist\부산실시간버스안내"
#define OutputDir "installer"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
AppCopyright=버전 {#AppVersion}
AppPublisher=Jiugae
DefaultDirName={autopf}\BusanBusInfo
DefaultGroupName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=BusanBus_Setup_v{#AppVersion}
SetupIconFile=icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; Flags: unchecked
Name: "runonstartup"; Description: "Windows 시작 시 프로그램 실행"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "BusanRealtimeBusInfo"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: runonstartup

[Run]
Filename: "{app}\{#AppExe}"; Description: "설치 후 바로 실행"; Flags: nowait postinstall skipifsilent

[Code]
function KillRunningApp(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/IM "{#AppExe}" /F /T',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    Sleep(1200);
    Result := (ResultCode = 0) or (ResultCode = 128) or (ResultCode = 255);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  if not KillRunningApp() then
  begin
    Result := '실행 중인 프로그램을 종료하지 못했습니다. 작업 관리자에서 "' + '{#AppExe}' + '"를 종료한 뒤 다시 시도해 주세요.';
    exit;
  end;
  Result := '';
end;
