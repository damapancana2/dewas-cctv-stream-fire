' =========================================================================
' DEWaS CCTV Fire & Smoke AI Edge Gateway Background Launcher
' Menjalankan gateway secara silent di background tanpa jendela cmd hitam
' =========================================================================
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

batFile = scriptDir & "\run_gateway.bat"
If fso.FileExists(batFile) Then
    ' 0 = Hide window, False = don't wait for return
    WshShell.Run "cmd /c """ & batFile & """ --no-pause", 0, False
End If

