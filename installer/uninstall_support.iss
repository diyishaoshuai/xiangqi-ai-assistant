// No taskkill by image name and no recursive delete of the application root.
const
  ManifestName = '.xiangqi-installed-files.txt';
  ProcessQuery = $1000;
  ProcessTerminate = $0001;
  Synchronize = $100000;
  WaitTimeout = $102;
  ReparsePoint = $400;
  InvalidHandleValue = $FFFFFFFF;

type
  TProcessIds = array[0..4095] of Cardinal;
  TTrackedProcess = record
    Handle: THandle;
    Id: Cardinal;
    ImagePath: String;
  end;
  TTrackedProcesses = array of TTrackedProcess;

function EnumProcesses(var Ids: TProcessIds; BufferBytes: Cardinal;
  var BytesNeeded: Cardinal): Boolean;
  external 'EnumProcesses@psapi.dll stdcall';
function OpenProcess(Access: Cardinal; Inherit: Boolean; Id: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function QueryFullProcessImageName(Handle: THandle; Flags: Cardinal;
  Buffer: String; var BufferChars: Cardinal): Boolean;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function TerminateProcess(Handle: THandle; ExitCode: Cardinal): Boolean;
  external 'TerminateProcess@kernel32.dll stdcall';
function EnumWindows(Callback, Param: LongWord): Boolean;
  external 'EnumWindows@user32.dll stdcall';
function GetWindowThreadProcessId(Window: HWND; var ProcessId: Cardinal): Cardinal;
  external 'GetWindowThreadProcessId@user32.dll stdcall';
function PostCloseMessage(Window: HWND; Msg: Cardinal; WParam, LParam: LongWord): Boolean;
  external 'PostMessageW@user32.dll stdcall';
function CreateEvent(Attributes: LongWord; ManualReset, InitialState: Boolean;
  Name: String): THandle;
  external 'CreateEventW@kernel32.dll stdcall';
function GetFileAttributes(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function SetFileAttributes(Name: String; Attributes: Cardinal): Boolean;
  external 'SetFileAttributesW@kernel32.dll stdcall';
function OpenFileForDelete(Name: String; Access, Share: Cardinal; Security: LongWord;
  Creation, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';

var
  MaintenanceHandle: THandle;
  InstalledFiles: TStringList;

function NormalPath(const Path: String): String;
begin
  Result := Lowercase(RemoveBackslash(ExpandFileName(Path)));
end;

function IsSafeInstallRoot(const Path: String): Boolean;
var
  Root: String;
begin
  Root := NormalPath(Path);
  Result := (Length(Root) > 3) and
    (Root <> NormalPath(GetEnv('USERPROFILE'))) and
    (Root <> NormalPath(ExpandConstant('{userdesktop}'))) and
    (Root <> NormalPath(ExpandConstant('{userdocs}'))) and
    (Root <> NormalPath(ExpandConstant('{localappdata}'))) and
    (Root <> NormalPath(ExpandConstant('{userappdata}'))) and
    (Root <> NormalPath(ExpandConstant('{autopf}'))) and
    (Root <> NormalPath(ExpandConstant('{localappdata}\Programs'))) and
    (Root <> NormalPath(ExpandConstant('{win}'))) and
    (Pos(NormalPath(ExpandConstant('{win}')) + '\', Root + '\') <> 1);
end;

function HasReparseParent(const Path: String): Boolean;
var
  Current, Parent: String;
  Attributes: Cardinal;
begin
  Result := False;
  Current := RemoveBackslash(ExpandFileName(Path));
  while Length(Current) > 3 do begin
    Attributes := GetFileAttributes(Current);
    if (Attributes <> $FFFFFFFF) and ((Attributes and ReparsePoint) <> 0) then begin
      Result := True;
      Exit;
    end;
    Parent := RemoveBackslash(ExtractFileDir(Current));
    if Parent = Current then Exit;
    Current := Parent;
  end;
end;

procedure ReleaseMaintenance;
begin
  if MaintenanceHandle <> 0 then begin
    CloseHandle(MaintenanceHandle);
    MaintenanceHandle := 0;
  end;
end;

function BeginMaintenance: Boolean;
var
  Name: String;
begin
  Name := 'Local\XiangqiAI.Maintenance.' +
    GetSHA256OfUnicodeString(NormalPath(ExpandConstant('{app}')));
  if MaintenanceHandle = 0 then
    MaintenanceHandle := CreateEvent(0, True, True, Name);
  Result := MaintenanceHandle <> 0;
end;

function IsInstalledProcess(const ImagePath: String): Boolean;
var
  Root, Image: String;
begin
  Root := NormalPath(ExpandConstant('{app}'));
  Image := NormalPath(ImagePath);
  Result := (Image = Root + '\xiangqiai.exe') or
    (Image = Root + '\_internal\engine\pikafish-sse41-popcnt.exe') or
    (Image = Root + '\engine\pikafish-sse41-popcnt.exe');
end;

function ProcessImage(Handle: THandle): String;
var
  Size: Cardinal;
begin
  Size := 32768;
  SetLength(Result, Size);
  if QueryFullProcessImageName(Handle, 0, Result, Size) then
    SetLength(Result, Size)
  else Result := '';
end;

function CloseProcessWindow(Window: HWND; Param: LongWord): Boolean;
var
  Id: Cardinal;
begin
  GetWindowThreadProcessId(Window, Id);
  if Id = Cardinal(Param) then PostCloseMessage(Window, $0010, 0, 0);
  Result := True;
end;

procedure ReleaseProcesses(var Processes: TTrackedProcesses);
var
  I: Integer;
begin
  for I := 0 to GetArrayLength(Processes) - 1 do CloseHandle(Processes[I].Handle);
  SetArrayLength(Processes, 0);
end;

function CollectProcesses(var Processes: TTrackedProcesses): Boolean;
var
  Ids: TProcessIds;
  Needed: Cardinal;
  I, Count: Integer;
  Handle: THandle;
  Image: String;
begin
  Result := False;
  SetArrayLength(Processes, 0);
  if not EnumProcesses(Ids, SizeOf(Ids), Needed) then Exit;
  if Needed >= SizeOf(Ids) then Exit;
  for I := 0 to (Needed div 4) - 1 do begin
    Handle := OpenProcess(ProcessQuery or Synchronize, False, Ids[I]);
    if Handle <> 0 then begin
      Image := ProcessImage(Handle);
      if (Image <> '') and IsInstalledProcess(Image) and
        (WaitForSingleObject(Handle, 0) = WaitTimeout) then begin
        Count := GetArrayLength(Processes);
        SetArrayLength(Processes, Count + 1);
        Processes[Count].Handle := Handle;
        Processes[Count].Id := Ids[I];
        Processes[Count].ImagePath := Image;
      end else CloseHandle(Handle);
    end;
  end;
  Result := True;
end;

function StopInstalledProcesses: String;
var
  Processes: TTrackedProcesses;
  Round, I, Tick: Integer;
  Running: Boolean;
  Handle: THandle;
begin
  Result := '';
  for Round := 1 to 3 do begin
    if not CollectProcesses(Processes) then begin
      Result := '无法可靠检查运行中的程序，未开始删除文件。';
      Exit;
    end;
    if GetArrayLength(Processes) = 0 then Exit;
    try
      for I := 0 to GetArrayLength(Processes) - 1 do begin
        Log('Request close: ' + Processes[I].ImagePath);
        EnumWindows(CreateCallback(@CloseProcessWindow), Processes[I].Id);
      end;
      for Tick := 1 to 50 do begin
        Running := False;
        for I := 0 to GetArrayLength(Processes) - 1 do
          if WaitForSingleObject(Processes[I].Handle, 0) = WaitTimeout then Running := True;
        if not Running then Break;
        Sleep(100);
      end;
      for I := 0 to GetArrayLength(Processes) - 1 do begin
        if WaitForSingleObject(Processes[I].Handle, 0) = WaitTimeout then begin
          Handle := OpenProcess(ProcessQuery or ProcessTerminate or Synchronize,
            False, Processes[I].Id);
          if Handle = 0 then begin
            Result := '程序无法退出，请先关闭或以相同权限重试：' + Processes[I].ImagePath;
            Exit;
          end;
          try
            { Recheck using the termination handle, protecting against PID reuse. }
            if NormalPath(ProcessImage(Handle)) <> NormalPath(Processes[I].ImagePath) then begin
              Result := '进程身份变化，已取消卸载，请重试。';
              Exit;
            end;
            Log('Force close verified installation process: ' + Processes[I].ImagePath);
            if not TerminateProcess(Handle, 0) or
              (WaitForSingleObject(Handle, 5000) <> 0) then begin
              Result := '程序仍在占用文件：' + Processes[I].ImagePath;
              Exit;
            end;
          finally
            CloseHandle(Handle);
          end;
        end;
      end;
    finally
      ReleaseProcesses(Processes);
    end;
  end;
  if CollectProcesses(Processes) then begin
    if GetArrayLength(Processes) <> 0 then Result := '程序正在被其他进程重新启动，请关闭后重试。';
    ReleaseProcesses(Processes);
  end else Result := '无法确认程序已退出，已取消卸载。';
end;

procedure LoadManifest;
var
  Manifest: String;
begin
  if InstalledFiles <> nil then Exit;
  InstalledFiles := TStringList.Create;
  Manifest := ExpandConstant('{app}\') + ManifestName;
  if FileExists(Manifest) and not HasReparseParent(Manifest) then
    InstalledFiles.LoadFromFile(Manifest);
end;

procedure RecordInstalledFile;
var
  Relative, Prefix, Destination: String;
begin
  LoadManifest;
  Prefix := AddBackslash(ExpandConstant('{app}'));
  Destination := ExpandConstant(CurrentFilename);
  if Pos(Lowercase(Prefix), Lowercase(Destination)) <> 1 then
    RaiseException('安装文件不在目标目录中：' + Destination);
  Relative := Copy(Destination, Length(Prefix) + 1, MaxInt);
  if InstalledFiles.IndexOf(Relative) < 0 then InstalledFiles.Add(Relative);
end;

function ValidManifestEntry(const Relative: String): Boolean;
begin
  Result := (Relative <> '') and (Pos(':', Relative) = 0) and
    (Copy(Relative, 1, 1) <> '\') and (Pos('..', Relative) = 0) and
    (Pos('/', Relative) = 0);
end;

function CheckFileRemovable(const Path: String): String;
var
  Handle: THandle;
  Attributes, OpenFlags: Cardinal;
begin
  Result := '';
  if not FileExists(Path) and not DirExists(Path) then Exit;
  if HasReparseParent(Path) then begin
    Result := '发现指向其他位置的链接，无法安全清理：' + Path;
    Exit;
  end;
  Attributes := GetFileAttributes(Path);
  if (Attributes <> $FFFFFFFF) and ((Attributes and FILE_ATTRIBUTE_READONLY) <> 0) then
    SetFileAttributes(Path, Attributes and not FILE_ATTRIBUTE_READONLY);
  OpenFlags := 0;
  if (Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
    OpenFlags := $02000000; { FILE_FLAG_BACKUP_SEMANTICS: open directory handle. }
  Handle := OpenFileForDelete(Path, $10000, 7, 0, 3, OpenFlags, 0);
  if Handle = InvalidHandleValue then
    Result := '文件被占用或没有删除权限：' + Path
  else CloseHandle(Handle);
end;

function CheckInstalledFiles: String;
var
  I: Integer;
  Directory, Root: String;
  CheckedDirectories: TStringList;
begin
  LoadManifest;
  Result := '';
  if InstalledFiles.IndexOf('XiangqiAI.exe') < 0 then begin
    Result := '安装文件清单丢失或损坏，请将新版安装包覆盖安装到原目录后再卸载。';
    Exit;
  end;
  Result := CheckFileRemovable(ExpandConstant('{app}\') + ManifestName);
  if Result <> '' then Exit;
  Root := NormalPath(ExpandConstant('{app}'));
  CheckedDirectories := TStringList.Create;
  try
    for I := 0 to InstalledFiles.Count - 1 do begin
      if ValidManifestEntry(InstalledFiles[I]) then begin
        Result := CheckFileRemovable(ExpandConstant('{app}\') + InstalledFiles[I]);
        if Result <> '' then Exit;
        Directory := NormalPath(ExtractFileDir(ExpandConstant('{app}\') + InstalledFiles[I]));
        while (Directory = Root) or (Pos(Root + '\', Directory) = 1) do begin
          if CheckedDirectories.IndexOf(Directory) >= 0 then Break;
          CheckedDirectories.Add(Directory);
          Result := CheckFileRemovable(Directory);
          if Result <> '' then Exit;
          if Directory = Root then Break;
          Directory := ExtractFileDir(Directory);
        end;
      end;
    end;
  finally
    CheckedDirectories.Free;
  end;
end;

function RemoveOwnedFile(const Path: String): String;
begin
  Result := CheckFileRemovable(Path);
  if Result <> '' then Exit;
  if FileExists(Path) and not DeleteFile(Path) then
    Result := '无法清理程序数据：' + Path;
end;

function CleanupLogs(const Directory: String): String;
var
  I: Integer;
  Path: String;
begin
  Result := CheckFileRemovable(Directory);
  if Result <> '' then Exit;
  for I := 0 to 3 do begin
    Path := AddBackslash(Directory) + 'xiangqi-ai.log';
    if I > 0 then Path := Path + '.' + IntToStr(I);
    Result := RemoveOwnedFile(Path);
    if Result <> '' then Exit;
  end;
  if not HasReparseParent(Directory) then RemoveDir(Directory);
end;

function CleanupBytecode(const Directory: String): String;
var
  FindRec: TFindRec;
  Path, Name: String;
begin
  Result := '';
  if HasReparseParent(Directory) then Exit;
  Result := CheckFileRemovable(Directory);
  if Result <> '' then Exit;
  if FindFirst(AddBackslash(Directory) + '*', FindRec) then begin
    try
      repeat
        Name := FindRec.Name;
        if (Name <> '.') and (Name <> '..') and ((FindRec.Attributes and ReparsePoint) = 0) then begin
          Path := AddBackslash(Directory) + Name;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            Result := CleanupBytecode(Path)
          else if (Lowercase(ExtractFileName(Directory)) = '__pycache__') and
            (Pos('.cpython-', Name) > 0) and (Lowercase(ExtractFileExt(Name)) = '.pyc') then
            Result := RemoveOwnedFile(Path);
          if Result <> '' then Exit;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  { Only an empty directory can be removed. User-added files are preserved. }
  RemoveDir(Directory);
end;

function CleanupApplicationData: String;
var
  LegacyRoot, FallbackRoot: String;
begin
  LegacyRoot := ExpandConstant('{localappdata}\XiangqiAI');
  FallbackRoot := AddBackslash(GetEnv('USERPROFILE')) + '.xiangqi_ai';
  Result := CheckFileRemovable(LegacyRoot);
  if Result <> '' then Exit;
  Result := CheckFileRemovable(FallbackRoot);
  if Result <> '' then Exit;
  Result := CleanupLogs(ExpandConstant('{app}\logs'));
  if Result <> '' then Exit;
  Result := CleanupLogs(ExpandConstant('{app}\XiangqiAI\logs'));
  if Result <> '' then Exit;
  Result := CleanupLogs(LegacyRoot + '\logs');
  if Result <> '' then Exit;
  Result := RemoveOwnedFile(LegacyRoot + '\recognition_templates.json');
  if Result <> '' then Exit;
  Result := RemoveOwnedFile(FallbackRoot + '\recognition_templates.json');
  if Result <> '' then Exit;
  Result := CleanupBytecode(ExpandConstant('{app}\_internal'));
  if not HasReparseParent(LegacyRoot) then RemoveDir(LegacyRoot);
  if not HasReparseParent(FallbackRoot) then RemoveDir(FallbackRoot);
  if not HasReparseParent(ExpandConstant('{app}\XiangqiAI')) then
    RemoveDir(ExpandConstant('{app}\XiangqiAI'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not IsSafeInstallRoot(ExpandConstant('{app}')) or
    HasReparseParent(ExpandConstant('{app}')) then begin
    Result := '请选择独立的程序文件夹，不要直接安装到桌面、系统目录或链接目录。';
    Exit;
  end;
  if not BeginMaintenance then begin
    Result := '无法创建维护保护，未修改安装文件。';
    Exit;
  end;
  LoadManifest;
  Result := StopInstalledProcesses;
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if Step = ssPostInstall then begin
    LoadManifest;
    InstalledFiles.SaveToFile(ExpandConstant('{app}\') + ManifestName);
    ReleaseMaintenance;
  end;
end;

procedure DeinitializeSetup;
begin
  ReleaseMaintenance;
  if InstalledFiles <> nil then InstalledFiles.Free;
end;

procedure CurUninstallStepChanged(Step: TUninstallStep);
var
  Error: String;
begin
  if Step = usUninstall then begin
    if not IsSafeInstallRoot(ExpandConstant('{app}')) or
      HasReparseParent(ExpandConstant('{app}')) then begin
      SuppressibleMsgBox('安装位置不安全，已取消卸载，未删除文件。', mbError, MB_OK, IDOK);
      Abort;
    end;
    if not BeginMaintenance then begin
      SuppressibleMsgBox('无法创建维护保护，已取消卸载。', mbError, MB_OK, IDOK);
      Abort;
    end;
    repeat
      Error := StopInstalledProcesses;
      if Error = '' then Error := CheckInstalledFiles;
      if Error = '' then Error := CleanupApplicationData;
      if Error = '' then Break;
      Log('Uninstall blocked before deleting program files: ' + Error);
      if SuppressibleMsgBox(Error + #13#10#13#10 +
        '尚未删除程序文件，卸载程序已保留。请关闭占用程序后重试。',
        mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY then Abort;
    until False;
  end else if Step = usPostUninstall then begin
    CleanupBytecode(ExpandConstant('{app}\_internal'));
    RemoveDir(ExpandConstant('{app}'));
  end;
end;

procedure DeinitializeUninstall;
begin
  ReleaseMaintenance;
  if InstalledFiles <> nil then InstalledFiles.Free;
end;
