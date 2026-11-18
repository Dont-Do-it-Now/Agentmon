Set WShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
Dir = FSO.GetParentFolderName(WScript.ScriptFullName)
WShell.Run "cmd /c """"" & Dir & "\run_log.bat""""", 0, False
