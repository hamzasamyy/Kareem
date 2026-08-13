' ============================================================================
'  run_jarvis_silent.vbs  --  Start Jarvis in the BACKGROUND with NO window,
'  always using the correct Python 3.12 (its windowless pythonw.exe).
'
'  Double-click this (or point a desktop / startup-folder shortcut at it) for a
'  silent launch. Output goes to jarvis_background.log in this folder; quit from
'  the system-tray icon. A bare "pythonw" could resolve to Python 3.11 (which
'  has no dependencies installed), so this hardcodes the 3.12 interpreter --
'  the same reason run_jarvis.bat exists for the normal foreground launch.
' ============================================================================
Option Explicit
Dim shell, fso, scriptDir, pyw, mainPy, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe"
mainPy = scriptDir & "\main.py"

If Not fso.FileExists(pyw) Then
    MsgBox "Jarvis: Python 3.12 was not found at:" & vbCrLf & pyw & vbCrLf & vbCrLf & _
           "Install Python 3.12, or edit the 'pyw' line in run_jarvis_silent.vbs " & _
           "to point at your pythonw.exe.", vbExclamation, "Jarvis launcher"
    WScript.Quit 1
End If

' Run from the project folder so .env loads. Window style 0 = hidden;
' False = don't wait (return immediately, fully detached, no console).
shell.CurrentDirectory = scriptDir
cmd = """" & pyw & """ """ & mainPy & """ --background"
shell.Run cmd, 0, False
