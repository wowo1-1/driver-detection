Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\ai\pilaojiance\Yolov5-deepsort-driverDistracted-driving-behavior-detection"
WshShell.Run "venv\Scripts\python.exe boss_app.py", 0, False
