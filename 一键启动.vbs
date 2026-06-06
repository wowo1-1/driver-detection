Set WshShell = CreateObject("WScript.Shell")
projectDir = "D:\ai\pilaojiance\Yolov5-deepsort-driverDistracted-driving-behavior-detection"
pythonExe = projectDir & "\venv\Scripts\python.exe"

' start server (hidden)
WshShell.Run pythonExe & " " & projectDir & "\server.py", 0, False
WScript.Sleep 2000

' start boss app (visible)
WshShell.Run pythonExe & " " & projectDir & "\boss_app.py", 1, False

MsgBox "Server and Boss App started!" & vbCrLf & vbCrLf & "1. Now open run.bat" & vbCrLf & "2. Menu -> Boss Server Settings" & vbCrLf & "3. Enable, enter your name, save", vbInformation, "Driver Detection System"
