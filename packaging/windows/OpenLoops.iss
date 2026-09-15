; Open Loops - Windows installer (Inno Setup 6.3+).
;
; A small setup.exe that does NOT contain the app. When run it downloads the repo from GitHub,
; unpacks it into the per-user Programs folder (%LOCALAPPDATA%\Programs\Open Loops), and runs the
; repo's own setup.ps1, which installs Python / Claude Code if missing, writes config.json, puts an
; "Open Loops" icon on the Desktop and in the Start menu, and registers the weekday refresh.
; The app writes its own state next to itself, so it is installed per user, never under the
; machine-wide C:\Program Files (which needs admin rights to write to).
;
; Build (dist\OpenLoops-Setup.exe):
;     ISCC packaging\windows\OpenLoops.iss
;     ISCC /DAppVersion=0.2 /DRef=<commit sha> packaging\windows\OpenLoops.iss
;         (release build: the workflow passes the commit the tag pointed to, so a moved tag cannot
;          change what an already-downloaded installer installs)
; Run-time switches (all optional):
;     /Ref=<branch, tag or sha>  which GitHub ref to download (default: the Ref baked in at build time)
;     /ZipUrl=<url>          download this zip instead of GitHub (testing)
;     /Name=<first name> /At=HH:MM  prefill the wizard; used as-is in /SILENT and /VERYSILENT runs.
;                            On an update (config.json already there) the wizard skips these and the
;                            refresh time in config.json is kept unless /At= is given.
;     /DIR="<folder>"        install somewhere else (standard Inno switch)

#ifndef AppVersion
  #define AppVersion "0.1"
#endif
#ifndef Repo
  #define Repo "OscarC178/Open-Loops"
#endif
#ifndef Ref
  #define Ref "main"
#endif
#define AppName "Open Loops"

[Setup]
AppId={{B7C1E2A4-5D3F-4A8B-9C6E-0F1D2E3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Open Loops
AppPublisherURL=https://github.com/{#Repo}
AppSupportURL=https://github.com/{#Repo}/issues
AppUpdatesURL=https://github.com/{#Repo}/releases
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17134
OutputDir=..\..\dist
OutputBaseFilename=OpenLoops-Setup
SetupIconFile=..\..\docs\AppIcon.ico
UninstallDisplayIcon={app}\docs\AppIcon.ico
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will download the latest Open Loops from GitHub and set it up on your computer.%n%nIt installs two helpers if they are missing (Python and Claude Code), puts an Open Loops icon on your Desktop and in the Start menu, and sets a weekday morning refresh.%n%nEverything runs on this computer. Nothing is sent anywhere.
ReadyLabel1=Setup is now ready to download and install [name] on your computer.
FinishedLabel=Setup has finished installing [name] on your computer. Open it from the Desktop icon each morning; the first time, it walks you through connecting Slack and email.
FinishedLabelNoIcons=Setup has finished installing [name] on your computer. Open it from the Desktop icon each morning.

[Run]
Filename: "{userdesktop}\{#AppName}.lnk"; Description: "Open {#AppName} now"; Flags: postinstall nowait shellexec skipifsilent skipifdoesntexist

[UninstallRun]
; Ask a running instance to quit (python -m openloops.app --stop), then drop the weekday task.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Set-Location -LiteralPath '{app}'; python -m openloops.app --stop"""; Flags: runhidden waituntilterminated; RunOnceId: "StopApp"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\register-task.ps1"" -Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveRefreshTask"

[UninstallDelete]
; Program files only. Personal files (config.json, state.json, voice.json, state\) are kept unless the
; person says yes to the question in CurUninstallStepChanged below.
Type: filesandordirs; Name: "{app}\openloops"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\tests"
Type: filesandordirs; Name: "{app}\.grok"
Type: filesandordirs; Name: "{app}\.github"
Type: filesandordirs; Name: "{app}\packaging"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\*.md"
Type: files; Name: "{app}\*.cmd"
Type: files; Name: "{app}\*.command"
Type: files; Name: "{app}\*.sh"
Type: files; Name: "{app}\*.ps1"
Type: files; Name: "{app}\LICENSE"
Type: files; Name: "{app}\icon.png"
Type: files; Name: "{app}\.gitignore"
Type: files; Name: "{app}\config.template.json"
Type: files; Name: "{app}\package.json"
Type: files; Name: "{userdesktop}\{#AppName}.lnk"
Type: files; Name: "{userprograms}\{#AppName}.lnk"

[Code]
var
  DownloadPage: TDownloadWizardPage;
  AboutPage: TInputQueryWizardPage;

function ZipUrl: String;
begin
  Result := ExpandConstant('{param:ZipUrl|}');
  if Result = '' then
    Result := 'https://github.com/{#Repo}/archive/' + ExpandConstant('{param:Ref|{#Ref}}') + '.zip';
end;

function FirstNameGuess: String;
var
  S: String;
  P: Integer;
begin
  S := GetUserNameString;
  P := Pos(' ', S); if P > 0 then S := Copy(S, 1, P - 1);
  P := Pos('.', S); if P > 0 then S := Copy(S, 1, P - 1);
  Result := S;
end;

function IsHHMM(const S: String): Boolean;
begin
  { Pascal Script has no set/range 'in'; comparisons only. Hours 00-23, minutes 00-59. }
  Result := (Length(S) = 5) and (S[3] = ':') and
            (S[1] >= '0') and (S[1] <= '2') and (S[2] >= '0') and (S[2] <= '9') and
            (S[4] >= '0') and (S[4] <= '5') and (S[5] >= '0') and (S[5] <= '9') and
            ((S[1] <> '2') or (S[2] <= '3'));
end;

{ An update: this folder already holds a set-up copy. Its settings win over the wizard defaults. }
function IsUpdate: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\config.json'));
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if Progress = ProgressMax then
    Log(Format('Downloaded %s (%d bytes)', [FileName, ProgressMax]));
  Result := True;
end;

procedure InitializeWizard;
begin
  AboutPage := CreateInputQueryPage(wpSelectDir, 'About you',
    'Two quick settings. Both can be changed later in the app''s Settings.',
    'Your first name is used so chase messages sound like you. The refresh time is when Open Loops re-reads Slack and email each weekday morning.');
  AboutPage.Add('Your first name:', False);
  AboutPage.Add('Morning refresh time (HH:MM, 24-hour):', False);
  AboutPage.Values[0] := ExpandConstant('{param:Name|}');
  AboutPage.Values[1] := ExpandConstant('{param:At|09:15}');
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), @OnDownloadProgress);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { Updating an existing install: keep their settings, don't ask again. }
  if PageID = AboutPage.ID then
    Result := IsUpdate;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = AboutPage.ID then begin
    if Trim(AboutPage.Values[0]) = '' then begin
      MsgBox('Please enter your first name.', mbError, MB_OK);
      Result := False;
    end else if not IsHHMM(Trim(AboutPage.Values[1])) then begin
      MsgBox('Please enter the refresh time as HH:MM, for example 09:15.', mbError, MB_OK);
      Result := False;
    end;
  end else if CurPageID = wpReady then begin
    DownloadPage.Clear;
    DownloadPage.Add(ZipUrl, 'openloops.zip', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Result := True;
      except
        if DownloadPage.AbortedByUser then
          Log('Download aborted by user.')
        else
          SuppressibleMsgBox('Could not download Open Loops from ' + ZipUrl + '.' + #13#10#13#10 +
                             AddPeriod(GetExceptionMessage) + #13#10#13#10 +
                             'Check your internet connection and try again.', mbCriticalError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

{ Find the single top-level folder GitHub puts inside its zip (Open-Loops-main, Open-Loops-0.2, ...). }
function ExtractedRoot(const Dir: String): String;
var
  FR: TFindRec;
begin
  Result := '';
  if FindFirst(Dir + '\*', FR) then begin
    try
      repeat
        if ((FR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and (FR.Name <> '.') and (FR.Name <> '..') then
          Result := Dir + '\' + FR.Name;
      until not FindNext(FR);
    finally
      FindClose(FR);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Zip, Ext, Src, Name, At, Params: String;
  RC: Integer;
begin
  Result := '';
  Zip := ExpandConstant('{tmp}\openloops.zip');
  Ext := ExpandConstant('{tmp}\src');
  if not FileExists(Zip) then begin
    Result := 'The download did not complete. Please run Setup again.';
    exit;
  end;

  WizardForm.PreparingLabel.Caption := 'Unpacking Open Loops...';
  ForceDirectories(Ext);
  if not Exec(ExpandConstant('{sys}\tar.exe'), '-xf "' + Zip + '" -C "' + Ext + '"', '', SW_HIDE, ewWaitUntilTerminated, RC) or (RC <> 0) then begin
    Result := 'Could not unpack the download (tar exit code ' + IntToStr(RC) + ').';
    exit;
  end;
  Src := ExtractedRoot(Ext);
  if (Src = '') or not FileExists(Src + '\setup.ps1') then begin
    Result := 'The download did not look like Open Loops (no setup.ps1 inside).';
    exit;
  end;

  Name := Trim(AboutPage.Values[0]);
  StringChangeEx(Name, '"', '', True);
  if Name = '' then Name := FirstNameGuess;
  At := Trim(AboutPage.Values[1]);
  if not IsHHMM(At) then At := '09:15';

  Params := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe') +
            ' -NoProfile -ExecutionPolicy Bypass -File "' + Src + '\setup.ps1"' +
            ' -Dest "' + ExpandConstant('{app}') + '" -Name "' + Name + '" -NoLaunch';
  { On an update the About page was skipped, so only pass a time the person actually chose
    (the wizard, or /At= on the command line). Otherwise setup.ps1 keeps the one in config.json. }
  if (not IsUpdate) or (ExpandConstant('{param:At|}') <> '') then
    Params := Params + ' -At "' + At + '"';
  { Interactive runs: keep the console open on failure so the person can read what went wrong. }
  if not WizardSilent then
    Params := Params + ' || (echo. & echo   Setup could not finish. Read the message above, then press any key to close this window. & pause >nul & exit /b 1)';
  Log('Running setup.ps1: ' + Params);
  WizardForm.PreparingLabel.Caption := 'Installing Open Loops. This can take a few minutes if Python or Claude Code need installing - watch the blue window.';
  if not Exec(ExpandConstant('{cmd}'), '/c "' + Params + '"', Src, SW_SHOW, ewWaitUntilTerminated, RC) then begin
    Result := 'Could not start the Open Loops setup script.';
    exit;
  end;
  if RC <> 0 then
    Result := 'The Open Loops setup script stopped with exit code ' + IntToStr(RC) + '. ' +
              'Fix the problem it reported and run Setup again.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('Open Loops has been removed.' + #13#10#13#10 +
              'Also delete your settings and your list (config.json, state.json, voice.json and the logs) from' + #13#10 +
              ExpandConstant('{app}') + ' ?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
end;
