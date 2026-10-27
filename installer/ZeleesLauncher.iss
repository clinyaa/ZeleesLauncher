; Установщик ZeleesLauncher (Inno Setup 6). Собирается из tools\build_release.py.
; Ставится в профиль пользователя без прав администратора — поэтому лаунчер может обновлять себя сам.

#define AppName "ZeleesLauncher"
#define AppExe "ZeleesLauncher.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{1C07AE19-7219-46D6-A80F-4D4FD0F581CA}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Zelees
DefaultDirName={localappdata}\Programs\{#AppName}
; окно выбора папки показываем всегда (при тихом обновлении оно пропускается само)
DisableDirPage=no
AppendDefaultDirName=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableReadyPage=no
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\ZeleesLauncher.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardSizePercent=100
WizardImageFile=wizard_164.bmp,wizard_205.bmp,wizard_246.bmp,wizard_328.bmp
WizardSmallImageFile=wizard_small_55.bmp,wizard_small_69.bmp,wizard_small_83.bmp,wizard_small_110.bmp
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[InstallDelete]
; перед установкой новой версии убираем старые служебные файлы, чтобы не копился мусор
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ZeleesLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; без skipifsilent: после тихого обновления лаунчер запустится сам
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure InitializeWizard;
begin
  WizardForm.SelectDirLabel.Caption :=
    'Выберите папку, в которую будет установлен {#AppName}.' + #13#10 +
    'Игры, моды и настройки хранятся отдельно: ' + ExpandConstant('{userappdata}\{#AppName}');
end;

// При удалении папка приложения стирается целиком, поэтому лаунчер всегда ставится в свою подпапку
// (иначе выбор, например, D:\Games удалил бы при деинсталляции всё содержимое D:\Games).
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir, Probe: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  Dir := RemoveBackslashUnlessRoot(WizardDirValue);
  if CompareText(ExtractFileName(Dir), '{#AppName}') <> 0 then
  begin
    Dir := AddBackslash(Dir) + '{#AppName}';
    WizardForm.DirEdit.Text := Dir;
  end;
  // установщик работает без прав администратора — проверяем, что в папку можно писать
  if not ForceDirectories(Dir) then
  begin
    MsgBox('Нет доступа к папке:' + #13#10 + Dir + #13#10 + #13#10 +
           'Выберите другое место, например D:\Games\{#AppName}.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Probe := AddBackslash(Dir) + '.write-test';
  if not SaveStringToFile(Probe, 'ok', False) then
  begin
    MsgBox('Нет прав на запись в папку:' + #13#10 + Dir + #13#10 + #13#10 +
           'Папки вроде Program Files требуют прав администратора. ' +
           'Выберите другое место, например D:\Games\{#AppName}.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  DeleteFile(Probe);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Удалить также игры, моды, скины и настройки?' + #13#10 + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
