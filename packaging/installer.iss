; Inno Setup script for CodeScope.  Build with:
;   iscc /DAppVersion=1.0.0 /DSourceDir=..\dist\CodeScope packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CodeScope"
#endif

[Setup]
AppId={{6F1E3C2A-9B7D-4C1E-8A51-3D2B7E4F9C10}
AppName=CodeScope
AppVersion={#AppVersion}
AppVerName=CodeScope {#AppVersion}
AppPublisher=CodeScope
DefaultDirName={autopf}\CodeScope
DefaultGroupName=CodeScope
UninstallDisplayIcon={app}\CodeScope.exe
OutputDir=..\dist
OutputBaseFilename=CodeScope-{#AppVersion}-Setup
SetupIconFile=..\codescope\resources\codescope.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
ChangesEnvironment=yes
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assoc"; Description: "Open .csdb project files with CodeScope"; GroupDescription: "Integration:"
Name: "addtopath"; Description: "Add the codescope-cli command to PATH"; GroupDescription: "Integration:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CodeScope"; Filename: "{app}\CodeScope.exe"
Name: "{group}\CodeScope Command Prompt"; Filename: "{cmd}"; Parameters: "/K ""set PATH={app};%PATH% && codescope-cli --help"""; WorkingDir: "{userdocs}"
Name: "{group}\{cm:UninstallProgram,CodeScope}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CodeScope"; Filename: "{app}\CodeScope.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.csdb"; ValueType: string; ValueName: ""; ValueData: "CodeScope.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CodeScope.Project"; ValueType: string; ValueName: ""; ValueData: "CodeScope Project"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CodeScope.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\CodeScope.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CodeScope.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\CodeScope.exe"" ""%1"""; Tasks: assoc
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\CodeScope.exe"; Description: "{cm:LaunchProgram,CodeScope}"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
