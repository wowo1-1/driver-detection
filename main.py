#主函数
import sys
import os
from glob import glob
from datetime import datetime
import csv
from PySide6 import QtWidgets,QtCore,QtGui
from PySide6.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QSlider, QLabel, QHBoxLayout, QVBoxLayout
from PySide6.QtCore import QDir, QTimer,Slot, Qt
from PySide6.QtGui import QPixmap,QImage
from ui_mainwindow import Ui_MainWindow
import cv2
import myframe
import mydetect                      # 用于运行时调阈值
import winsound                       # Windows内置蜂鸣警报
import threading                      # 用于异步播放警报音
import time                           # 用于 FPS 计算
import tracker                        # DeepSORT 风格目标追踪
import pyttsx3                        # 中文语音播报
import faceauth                       # 人脸识别
import alerter                        # 远程邮件报警
import urllib.request                  # 向老板端服务器上报
import urllib.parse

# 定义变量

# 眼睛闭合判断
EYE_AR_THRESH = 0.15        # 眼睛长宽比
EYE_AR_CONSEC_FRAMES = 2    # 闪烁阈值

#嘴巴开合判断
MAR_THRESH = 0.65           # 打哈欠长宽比
MOUTH_AR_CONSEC_FRAMES = 3  # 闪烁阈值

# 定义检测变量，并初始化
COUNTER = 0                 #眨眼帧计数器
TOTAL = 0                   #眨眼总数
mCOUNTER = 0                #打哈欠帧计数器
mTOTAL = 0                  #打哈欠总数

# 分心行为连续帧计数（必须连续 N 帧都检测到才判定为真，防止误报）
CONSEC_FRAMES_THRESH = 5
phone_consec = 0
smoke_consec = 0
drink_consec = 0

# 语音报警状态（记录上次是否已触发过警报，避免重复响）
last_alerted_phone = False
last_alerted_smoke = False
last_alerted_drink = False
last_alerted_fatigue = False

# ========== 🔊 中文语音播报 ==========
_tts_engine = None
_tts_lock = threading.Lock()

def speak_chinese(text):
    """TTS 中文语音播报（异步）"""
    def _speak():
        global _tts_engine
        try:
            if _tts_engine is None:
                _tts_engine = pyttsx3.init()
                _tts_engine.setProperty('rate', 200)    # 语速
                _tts_engine.setProperty('volume', 1.0)  # 音量
                # 尝试选择中文语音
                voices = _tts_engine.getProperty('voices')
                for v in voices:
                    if 'Chinese' in v.name or 'zh' in v.id:
                        _tts_engine.setProperty('voice', v.id)
                        break
            with _tts_lock:
                _tts_engine.say(text)
                _tts_engine.runAndWait()
        except Exception:
            # 如果 TTS 失败，回退到蜂鸣
            try:
                winsound.Beep(880, 300)
            except:
                pass
    threading.Thread(target=_speak, daemon=True).start()

# 保留蜂鸣警报作为 TTS 的备用
def play_beep():
    try:
        for _ in range(2):
            winsound.Beep(880, 150)
    except:
        pass

# ========== 📸 自动截图设置 ==========
SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
screenshot_cooldown = 0                # 截图冷却帧数
SCREENSHOT_COOLDOWN_MAX = 75           # 冷却时间 (~1.5秒 @ 50fps)

def save_screenshot(frame, label):
    """保存当前帧为截图（带违规标签）"""
    global screenshot_cooldown
    if screenshot_cooldown > 0:
        return
    screenshot_cooldown = SCREENSHOT_COOLDOWN_MAX
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{SCREENSHOT_DIR}/{ts}_{label}.jpg"
    cv2.imwrite(filename, frame)
    report_stats['screenshots'] += 1
    Ui_MainWindow.printf(window, f"📸 已保存截图: {filename}")

# ========== 📝 CSV 驾驶日志 ==========
SESSION_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = f"driving_log_{SESSION_ID}.csv"
LOG_HEADER = ["时间", "事件类型", "详情"]
_log_file_handle = None
_log_writer = None

def init_log():
    """初始化日志文件（在打开摄像头时调用）"""
    global _log_file_handle, _log_writer
    if _log_writer is not None:
        return
    _log_file_handle = open(LOG_FILE, "w", newline="", encoding="utf-8-sig")
    _log_writer = csv.writer(_log_file_handle)
    _log_writer.writerow(LOG_HEADER)
    _log_file_handle.flush()
    Ui_MainWindow.printf(window, f"📝 日志文件: {LOG_FILE}")

def log_event(event_type, detail):
    """写入一条日志"""
    global _log_writer, _log_file_handle
    if _log_writer is None:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    _log_writer.writerow([ts, event_type, detail])
    _log_file_handle.flush()

def close_log():
    """关闭日志文件"""
    global _log_file_handle, _log_writer
    if _log_file_handle:
        _log_file_handle.close()
        _log_file_handle = None
        _log_writer = None

# ========== 🎯 全局追踪器 ==========
object_tracker = tracker.ObjectTracker(iou_threshold=0.3, max_lost=8)

# ========== 📊 驾驶报告统计 ==========
report_stats = {
    'phone': 0, 'smoke': 0, 'drink': 0, 'fatigue': 0,
    'yawn': 0, 'blink': 0, 'screenshots': 0
}
session_start_time = None

def show_driving_report():
    """显示本次驾驶报告"""
    global report_stats
    elapsed = time.time() - session_start_time if session_start_time else 0
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    total_distractions = report_stats['phone'] + report_stats['smoke'] + report_stats['drink']

    msg = (
        f"🕐 驾驶时长: {mins}分{secs}秒\n\n"
        f"📱 使用手机: {report_stats['phone']} 次\n"
        f"🚬 抽烟: {report_stats['smoke']} 次\n"
        f"🥤 喝水: {report_stats['drink']} 次\n"
        f"😴 疲劳状态: {report_stats['fatigue']} 次\n"
        f"🥱 打哈欠: {report_stats['yawn']} 次\n"
        f"👁 眨眼: {report_stats['blink']} 次\n"
        f"📸 自动截图: {report_stats['screenshots']} 张\n\n"
    )

    if total_distractions == 0 and report_stats['fatigue'] == 0:
        score = "🌟 优秀 — 安全驾驶，继续保持！"
    elif total_distractions <= 3 and report_stats['fatigue'] <= 1:
        score = "👍 良好 — 偶有分心，请注意"
    elif total_distractions <= 10:
        score = "⚠️ 一般 — 分心次数较多，请提高警惕"
    else:
        score = "🔴 危险 — 分心频繁，为了安全请改正"

    msg += f"综合评分: {score}"

    # 保存报告到日志
    log_event("驾驶报告", f"时长{mins}分, 分心{total_distractions}次, 疲劳{report_stats['fatigue']}次")

    QMessageBox.information(window, "🚗 驾驶报告", msg, QMessageBox.StandardButton.Ok)

# ========== 🌙 暗光增强 ==========
night_mode = False  # 夜间模式开关

def enhance_frame(frame):
    """CLAHE 暗光增强"""
    if not night_mode:
        return frame
    try:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        return enhanced
    except:
        return frame

# ========== 👤 当前驾驶员 ==========
current_driver = "未识别"
employee_name = ""                      # 员工姓名（用于老板端上报）

# ========== ☁️ 老板端服务器 ==========
boss_server_enabled = False
boss_server_url = "http://localhost:6789"

def report_to_server(event_type, detail=""):
    """向老板端服务器上报检测事件"""
    global employee_name
    if not boss_server_enabled or not employee_name:
        return
    try:
        data = json.dumps({
            "name": employee_name,
            "driver": current_driver,
            "type": event_type,
            "detail": detail,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{boss_server_url}/report",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass

# ========== 🔧 阈值滑条回调 ==========
def on_conf_threshold_changed(value):
    """检测置信度阈值滑条回调"""
    val = value / 100.0
    mydetect.opt_conf_thres = val
    Ui_MainWindow.printf(window, f"🎛️ 检测阈值: {val:.2f}")

def on_consec_frames_changed(value):
    """连续帧阈值滑条回调"""
    global CONSEC_FRAMES_THRESH
    CONSEC_FRAMES_THRESH = value
    Ui_MainWindow.printf(window, f"🎛️ 连续帧阈值: {value}帧")

# 疲劳判断变量
# Perclos模型
# perclos = (Rolleye/Roll) + (Rollmouth/Roll)*0.2
Roll = 0                    #整个循环内的帧技术
Rolleye = 0                 #循环内闭眼帧数
Rollmouth = 0               #循环内打哈欠数

class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setupUi(self)
        # 打开文件类型，用于类的定义
        self.f_type = 0

    def apply_modern_style(self):
        """应用现代化暗色主题样式"""
        self.setStyleSheet("""
            /* ===== 全局 ===== */
            QMainWindow {
                background-color: #0d1117;
            }
            QWidget#centralwidget {
                background-color: #0d1117;
            }

            /* ===== 摄像头画面 ===== */
            QLabel#label {
                background-color: #161b22;
                border: 2px solid #30363d;
                border-radius: 12px;
                padding: 4px;
                color: #8b949e;
                font-size: 16px;
            }

            /* ===== 面板标题 ===== */
            QLabel#label_2, QLabel#label_5 {
                color: #58a6ff;
                font-size: 13px;
                font-weight: bold;
                padding: 6px 8px;
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                min-width: 100px;
                max-width: 160px;
            }

            /* ===== 状态标签 ===== */
            QLabel#label_3, QLabel#label_4 {
                color: #c9d1d9;
                font-size: 12px;
                background-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px 6px;
                min-width: 120px;
                max-width: 180px;
            }

            /* ===== 行为标签 (手机/抽烟/喝水) ===== */
            QLabel#label_6, QLabel#label_7, QLabel#label_8 {
                color: #c9d1d9;
                font-size: 13px;
                font-weight: bold;
                background-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 6px 8px;
                min-width: 100px;
                max-width: 160px;
                qproperty-alignment: 'AlignCenter';
            }

            /* ===== 分心状态 ===== */
            QLabel#label_9 {
                color: #c9d1d9;
                font-size: 13px;
                font-weight: bold;
                background-color: #161b22;
                border: 2px solid #30363d;
                border-radius: 8px;
                padding: 8px;
                min-width: 180px;
                max-width: 280px;
                qproperty-alignment: 'AlignCenter';
            }

            /* ===== 疲劳状态 ===== */
            QLabel#label_10 {
                color: #c9d1d9;
                font-size: 13px;
                font-weight: bold;
                background-color: #161b22;
                border: 2px solid #30363d;
                border-radius: 8px;
                padding: 8px;
                min-width: 180px;
                max-width: 260px;
                qproperty-alignment: 'AlignCenter';
            }

            /* ===== 日志框 ===== */
            QTextBrowser#textBrowser {
                background-color: #0d1117;
                color: #8b949e;
                font-size: 12px;
                font-family: "Consolas", "Courier New", monospace;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 6px;
                selection-background-color: #1f6feb;
            }

            /* ===== 菜单栏 ===== */
            QMenuBar {
                background-color: #161b22;
                color: #c9d1d9;
                border-bottom: 1px solid #30363d;
                padding: 2px;
                font-size: 13px;
            }
            QMenuBar::item:selected {
                background-color: #1f6feb;
                border-radius: 4px;
            }
            QMenu {
                background-color: #21262d;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1f6feb;
            }

            /* ===== 状态栏 ===== */
            QStatusBar {
                background-color: #161b22;
                color: #8b949e;
                border-top: 1px solid #30363d;
                font-size: 12px;
            }

            /* ===== 滚动条 ===== */
            QScrollBar:vertical {
                background-color: #0d1117;
                width: 10px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #30363d;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #484f58;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            /* ===== 滑条标签 ===== */
            QLabel#sliderLabel {
                color: #8b949e;
                font-size: 12px;
                font-weight: bold;
                min-width: 80px;
            }
            QLabel#sliderValue {
                color: #58a6ff;
                font-size: 12px;
                font-weight: bold;
                min-width: 40px;
                text-align: right;
            }

            /* ===== 滑条 ===== */
            QSlider::groove:horizontal {
                background: #21262d;
                border: 1px solid #30363d;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #58a6ff;
                border: none;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #79c0ff;
            }
            QSlider::sub-page:horizontal {
                background: #1f6feb;
                border-radius: 3px;
            }
            QSlider::tick:horizontal {
                color: #30363d;
            }
        """)

    def window_init(self):
        # 先应用主题样式
        self.apply_modern_style()
        # 设置窗口标题
        self.setWindowTitle("🚗 驾驶员分心驾驶检测系统 v2.0")
        # 设置控件属性
        # 设置label的初始值
        self.label.setText("📷 请打开摄像头")
        self.label_2.setText("🧠 疲劳检测")
        self.label_3.setText("👁 眨眼次数：0")
        self.label_4.setText("🥱 哈欠次数：0")
        self.label_5.setText("⚠️ 行为检测")
        self.label_6.setText("📱 手机")
        self.label_7.setText("🚬 抽烟")
        self.label_8.setText("🥤 喝水")
        self.label_9.setText("✅ 正常行驶")
        self.label_10.setText("🧠 状态良好")
        self.menu.setTitle("菜单")
        self.actionOpen_camera.setText("📷 打开摄像头")
        self.actionClose_camera.setText("⏹ 关闭摄像头")
        self.actionClose_camera.setEnabled(False)

        # ----- 新菜单项 -----
        # 在菜单中添加分隔符
        self.menu.addSeparator()

        # 人脸注册
        self.actionRegisterFace = QtGui.QAction("👤 注册驾驶员", self)
        self.actionRegisterFace.setObjectName("actionRegisterFace")
        self.actionRegisterFace.triggered.connect(register_face_dialog)
        self.menu.addAction(self.actionRegisterFace)

        # 人脸管理
        self.actionListFaces = QtGui.QAction("👥 驾驶员管理", self)
        self.actionListFaces.setObjectName("actionListFaces")
        self.actionListFaces.triggered.connect(list_faces_dialog)
        self.menu.addAction(self.actionListFaces)

        self.menu.addSeparator()

        # 夜视模式
        self.actionNightMode = QtGui.QAction("🌙 夜视增强", self)
        self.actionNightMode.setObjectName("actionNightMode")
        self.actionNightMode.setCheckable(True)
        self.actionNightMode.setChecked(False)
        self.actionNightMode.triggered.connect(toggle_night_mode)
        self.menu.addAction(self.actionNightMode)

        # 老板端服务器
        self.actionBossConfig = QtGui.QAction("☁️ 老板端服务器设置", self)
        self.actionBossConfig.setObjectName("actionBossConfig")
        self.actionBossConfig.triggered.connect(boss_config_dialog)
        self.menu.addAction(self.actionBossConfig)

        self.menu.addSeparator()

        # 邮件报警配置
        self.actionAlertConfig = QtGui.QAction("📧 邮件报警设置", self)
        self.actionAlertConfig.setObjectName("actionAlertConfig")
        self.actionAlertConfig.triggered.connect(alert_config_dialog)
        self.menu.addAction(self.actionAlertConfig)

        # 菜单按钮 槽连接 到函数
        self.actionOpen_camera.triggered.connect(CamConfig_init)
        self.actionClose_camera.triggered.connect(close_camera)
        # 自适应窗口缩放
        self.label.setScaledContents(True)
        # 窗口稍微调高给滑条留空间
        self.resize(1160, 700)
        # 覆盖 ui 中过小的 max size，让文字有足够空间
        self.label_6.setMaximumSize(160, 40)
        self.label_7.setMaximumSize(160, 40)
        self.label_8.setMaximumSize(160, 40)
        self.label_9.setMaximumSize(280, 40)
        self.label_10.setMaximumSize(260, 40)
        self.textBrowser.setMaximumSize(340, 280)
        # 初始状态栏
        self.statusbar.showMessage("🚗 就绪 — 点击「菜单→打开摄像头」开始检测")

        # ========== 🎛️ 添加阈值滑条 ==========
        # 检测置信度滑条
        self.conf_slider = QSlider(Qt.Horizontal)
        self.conf_slider.setObjectName("confSlider")
        self.conf_slider.setRange(10, 90)
        self.conf_slider.setValue(40)  # 当前 0.4
        self.conf_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.conf_slider.setTickInterval(10)
        self.conf_slider.valueChanged.connect(on_conf_threshold_changed)

        conf_label = QLabel("🎛️ 检测灵敏度")
        conf_label.setObjectName("sliderLabel")
        conf_val = QLabel("0.40")
        conf_val.setObjectName("sliderValue")
        self.conf_val_label = conf_val
        self.conf_slider.valueChanged.connect(
            lambda v: conf_val.setText(f"{v/100:.2f}")
        )

        # 连续帧数滑条
        self.consec_slider = QSlider(Qt.Horizontal)
        self.consec_slider.setObjectName("consecSlider")
        self.consec_slider.setRange(1, 20)
        self.consec_slider.setValue(CONSEC_FRAMES_THRESH)
        self.consec_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.consec_slider.setTickInterval(1)
        self.consec_slider.valueChanged.connect(on_consec_frames_changed)

        consec_label = QLabel("🔁 连续帧判定")
        consec_label.setObjectName("sliderLabel")
        consec_val = QLabel(f"{CONSEC_FRAMES_THRESH}帧")
        consec_val.setObjectName("sliderValue")
        self.consec_val_label = consec_val
        self.consec_slider.valueChanged.connect(
            lambda v: consec_val.setText(f"{v}帧")
        )

        # 将滑条添加到右侧面板（textBrowser 下方）
        slider_layout = QVBoxLayout()
        slider_layout.setSpacing(2)
        slider_layout.setContentsMargins(4, 4, 4, 4)

        row1 = QHBoxLayout()
        row1.addWidget(conf_label)
        row1.addWidget(conf_val)
        slider_layout.addLayout(row1)
        slider_layout.addWidget(self.conf_slider)

        row2 = QHBoxLayout()
        row2.addWidget(consec_label)
        row2.addWidget(consec_val)
        slider_layout.addLayout(row2)
        slider_layout.addWidget(self.consec_slider)

        self.verticalLayout.addLayout(slider_layout)

# 定义摄像头类
class CamConfig:
    def __init__(self):
        Ui_MainWindow.printf(window,"正在打开摄像头请稍后...")
        # 设置时钟
        self.v_timer = QTimer()
        # 打开摄像头（尝试多个编号）
        self.cap = None
        for camera_id in range(3):
            cap_try = cv2.VideoCapture(camera_id, cv2.CAP_DSHOW)  # 用 DirectShow 加快打开速度
            if cap_try.isOpened():
                self.cap = cap_try
                Ui_MainWindow.printf(window,f"已打开摄像头 (编号 {camera_id})")
                break
        if self.cap is None:
            Ui_MainWindow.printf(window,"打开摄像头失败，请检查摄像头权限")
            return
        # 先测试读取一帧
        ret, test_frame = self.cap.read()
        if not ret or test_frame is None:
            Ui_MainWindow.printf(window,"摄像头已打开但读不到画面，请检查")
            return
        Ui_MainWindow.printf(window,f"画面分辨率: {test_frame.shape[1]}x{test_frame.shape[0]}")
        # 初始化日志
        init_log()
        # 重置追踪器
        object_tracker.reset()
        # 启动会话计时
        global session_start_time
        session_start_time = time.time()
        # 初始化追踪器实例属性
        self.tracker = object_tracker
        # 设置定时器周期，单位毫秒
        self.v_timer.start(20)
        # 连接定时器周期溢出的槽函数，用于显示一帧视频
        self.v_timer.timeout.connect(self.show_pic)
        # 在前端UI输出提示信息
        # 切换菜单按钮状态
        window.actionOpen_camera.setEnabled(False)
        window.actionClose_camera.setEnabled(True)
        Ui_MainWindow.printf(window,"载入成功，开始运行程序")
        Ui_MainWindow.printf(window,"")
        Ui_MainWindow.printf(window,"开始执行疲劳检测...")
        window.statusbar.showMessage("🚀 正在使用摄像头...")
    def show_pic(self):
        # 全局变量
        # 在函数中引入定义的全局变量
        global EYE_AR_THRESH,EYE_AR_CONSEC_FRAMES,MAR_THRESH,MOUTH_AR_CONSEC_FRAMES,COUNTER,TOTAL,mCOUNTER,mTOTAL,Roll,Rolleye,Rollmouth,phone_consec,smoke_consec,drink_consec,CONSEC_FRAMES_THRESH,last_alerted_phone,last_alerted_smoke,last_alerted_drink,last_alerted_fatigue,screenshot_cooldown,report_stats,current_driver,night_mode

        try:
            # 截图冷却递减
            if screenshot_cooldown > 0:
                screenshot_cooldown -= 1

            # 读取摄像头的一帧画面
            success, frame = self.cap.read()
            if not success or frame is None:
                return

            # 🌙 暗光增强
            frame = enhance_frame(frame)

            # 👤 人脸识别（每 30 帧一次）
            if not hasattr(self, '_face_counter'):
                self._face_counter = 0
            self._face_counter += 1
            if self._face_counter >= 30:
                self._face_counter = 0
                try:
                    driver_name, confidence = faceauth.recognize_face(frame)
                    if driver_name:
                        current_driver = driver_name
                except:
                    pass

            # 每 30 帧更新一次状态栏 FPS
            if not hasattr(self, '_frame_count'):
                self._frame_count = 0
                self._fps_time = time.time()
            self._frame_count += 1
            if self._frame_count >= 30:
                fps_val = self._frame_count / (time.time() - self._fps_time)
                driver_info = f"👤 {current_driver}" if current_driver != "未识别" else ""
                window.statusbar.showMessage(f"🚀 {fps_val:.1f} FPS  {driver_info}|  正在使用摄像头...")
                self._frame_count = 0
                self._fps_time = time.time()

            # 检测
            # 将摄像头读到的frame传入检测函数myframe.frametest()
            ret,frame = myframe.frametest(frame)
            lab, eye, mouth = ret[:3]
            detections_raw = ret[3] if len(ret) > 3 else []
            # ret为检测结果:
            #   [0] 标签列表 ['phone','smoke','drink']
            #   [1] 眼睛开合度
            #   [2] 嘴巴开合度
            #   [3] 完整检测数据 [(label, conf, [x1,y1,x2,y2]), ...]
            # frame为标注了识别结果的帧画面，画上了标识框

            # ===== 🎯 目标追踪 =====
            # 将 YOLO 原始检测送入追踪器，获得平滑的带ID结果
            tracked = self.tracker.update(detections_raw)
            # 在画面上绘制追踪ID + 平滑框
            for tid, tlabel, tconf, tbbox in tracked:
                x1, y1, x2, y2 = tbbox
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 2)
                cv2.putText(frame, f"#{tid} {tlabel} {tconf:.0%}",
                            (x1, y1 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 2)

            # ===== 分心行为判断（连续帧验证）=====
            # 标记当前帧检测到了什么
            detected_phone = "phone" in lab
            detected_smoke = "smoke" in lab
            detected_drink = "drink" in lab

            # 手机：连续检测到才判定
            if detected_phone:
                phone_consec += 1
            else:
                phone_consec = 0
            if phone_consec >= CONSEC_FRAMES_THRESH:
                window.label_6.setText('<span style="color:#f85149; font-weight:bold;">📱 正在使用手机</span>')
                window.label_9.setText('<span style="color:#f85149; font-weight:bold;">⚠️ 分心驾驶！请专注</span>')
                if not last_alerted_phone:
                    last_alerted_phone = True
                    report_stats['phone'] += 1
                    save_screenshot(frame, "phone")
                    log_event("分心行为", "使用手机")
                    speak_chinese("请不要使用手机，专心驾驶")
                    play_beep()
                    report_to_server("phone", "正在使用手机")
                    ss = f"screenshots/{datetime.now().strftime('%Y%m%d_%H%M%S')}_phone.jpg"
                    alerter.send_alert("phone", f"驾驶员 {current_driver} 正在使用手机", ss)
            elif phone_consec == 0 and last_alerted_phone:
                window.label_6.setText("📱 手机")
                last_alerted_phone = False
                log_event("行为结束", "使用手机")

            # 抽烟：连续检测到才判定
            if detected_smoke:
                smoke_consec += 1
            else:
                smoke_consec = 0
            if smoke_consec >= CONSEC_FRAMES_THRESH:
                window.label_7.setText('<span style="color:#f85149; font-weight:bold;">🚬 检测到抽烟</span>')
                window.label_9.setText('<span style="color:#f85149; font-weight:bold;">⚠️ 分心驾驶！请专注</span>')
                if not last_alerted_smoke:
                    last_alerted_smoke = True
                    report_stats['smoke'] += 1
                    save_screenshot(frame, "smoke")
                    log_event("分心行为", "抽烟")
                    speak_chinese("检测到抽烟，请熄灭香烟")
                    play_beep()
                    report_to_server("smoke", "正在抽烟")
                    ss = f"screenshots/{datetime.now().strftime('%Y%m%d_%H%M%S')}_smoke.jpg"
                    alerter.send_alert("smoke", f"驾驶员 {current_driver} 正在抽烟", ss)
            elif smoke_consec == 0 and last_alerted_smoke:
                window.label_7.setText("🚬 抽烟")
                last_alerted_smoke = False
                log_event("行为结束", "抽烟")

            # 喝水：连续检测到才判定
            if detected_drink:
                drink_consec += 1
            else:
                drink_consec = 0
            if drink_consec >= CONSEC_FRAMES_THRESH:
                window.label_8.setText('<span style="color:#f85149; font-weight:bold;">🥤 正在喝水</span>')
                window.label_9.setText('<span style="color:#f85149; font-weight:bold;">⚠️ 分心驾驶！请专注</span>')
                if not last_alerted_drink:
                    last_alerted_drink = True
                    report_stats['drink'] += 1
                    save_screenshot(frame, "drink")
                    log_event("分心行为", "喝水")
                    speak_chinese("请勿在驾驶时喝水")
                    play_beep()
                    report_to_server("drink", "正在喝水")
                    ss = f"screenshots/{datetime.now().strftime('%Y%m%d_%H%M%S')}_drink.jpg"
                    alerter.send_alert("drink", f"驾驶员 {current_driver} 正在喝水", ss)
            elif drink_consec == 0 and last_alerted_drink:
                window.label_8.setText("🥤 喝水")
                last_alerted_drink = False
                log_event("行为结束", "喝水")

            # 如果三种行为都没触发，清除分心提示
            if phone_consec == 0 and smoke_consec == 0 and drink_consec == 0:
                window.label_9.setText("✅ 正常行驶")

            # 疲劳判断
            # 眨眼判断
            if eye < EYE_AR_THRESH:
                # 如果眼睛开合程度小于设定好的阈值
                # 则两个和眼睛相关的计数器加1
                COUNTER += 1
                Rolleye += 1
            else:
                # 如果连续2次都小于阈值，则表示进行了一次眨眼活动
                if COUNTER >= EYE_AR_CONSEC_FRAMES:
                    TOTAL += 1
                    window.label_3.setText("眨眼次数：" + str(TOTAL))
                    # 重置眼帧计数器
                    COUNTER = 0

            # 哈欠判断，同上
            if mouth > MAR_THRESH:
                mCOUNTER += 1
                Rollmouth += 1
            else:
                # 如果连续3次都小于阈值，则表示打了一次哈欠
                if mCOUNTER >= MOUTH_AR_CONSEC_FRAMES:
                    mTOTAL += 1
                    window.label_4.setText("哈欠次数：" + str(mTOTAL))
                    # 重置嘴帧计数器
                    mCOUNTER = 0

            # 将画面显示在前端UI上
            show = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = show.shape
            bytes_per_line = ch * w
            showImage = QImage(show.data, w, h, bytes_per_line, QImage.Format_RGB888)
            window.label.setPixmap(QPixmap.fromImage(showImage.copy()))  # copy() 确保数据安全

            # 疲劳模型
            # 疲劳模型以150帧为一个循环
            # 每一帧Roll加1
            Roll += 1
            if Roll == 1:
                Ui_MainWindow.printf(window,"摄像头画面已开始显示")
            # 当检测满150帧时，计算模型得分
            if Roll == 150:
                # 计算Perclos模型得分
                perclos = (Rolleye/Roll) + (Rollmouth/Roll)*0.2
                # 在前端UI输出perclos值
                Ui_MainWindow.printf(window,"过去150帧中，Perclos得分为"+str(round(perclos,3)))
                # 当过去的150帧中，Perclos模型得分超过0.38时，判断为疲劳状态
                if perclos > 0.38:
                    Ui_MainWindow.printf(window,"当前处于疲劳状态")
                    window.label_10.setText('<span style="color:#f85149; font-weight:bold;">😴 疲劳！请休息</span>')
                    if not last_alerted_fatigue:
                        last_alerted_fatigue = True
                        report_stats['fatigue'] += 1
                        save_screenshot(frame, "fatigue")
                        log_event("疲劳警报", f"Perclos={perclos:.3f}")
                        speak_chinese("您已疲劳，请靠边停车休息")
                        report_to_server("fatigue", f"Perclos={perclos:.3f}")
                        alerter.send_alert("fatigue", f"驾驶员 {current_driver} 处于疲劳状态 (Perclos={perclos:.3f})")
                    Ui_MainWindow.printf(window,"")
                else:
                    Ui_MainWindow.printf(window,"当前处于清醒状态")
                    window.label_10.setText("🧠 状态良好")
                    if last_alerted_fatigue:
                        log_event("疲劳解除", "恢复清醒")
                    last_alerted_fatigue = False
                    Ui_MainWindow.printf(window,"")

                # 归零
                # 将三个计数器归零
                # 重新开始新一轮的检测
                Roll = 0
                Rolleye = 0
                Rollmouth = 0
                Ui_MainWindow.printf(window,"重新开始执行疲劳检测...")
        except Exception as e:
            import traceback
            Ui_MainWindow.printf(window,f"出错: {str(e)}")
            Ui_MainWindow.printf(window,traceback.format_exc())

def CamConfig_init():
    window.f_type = CamConfig()


def close_camera():
    """关闭摄像头并重置界面"""
    # 重置全局计数器
    global phone_consec, smoke_consec, drink_consec
    global last_alerted_phone, last_alerted_smoke, last_alerted_drink, last_alerted_fatigue
    global COUNTER, TOTAL, mCOUNTER, mTOTAL, Roll, Rolleye, Rollmouth, screenshot_cooldown
    global report_stats, current_driver
    phone_consec = smoke_consec = drink_consec = 0
    last_alerted_phone = last_alerted_smoke = last_alerted_drink = last_alerted_fatigue = False
    COUNTER = TOTAL = mCOUNTER = mTOTAL = 0
    Roll = Rolleye = Rollmouth = 0
    screenshot_cooldown = 0
    current_driver = "未识别"

    # 重置追踪器
    object_tracker.reset()

    # 统计打哈欠和眨眼
    report_stats['yawn'] = mTOTAL
    report_stats['blink'] = TOTAL

    # 释放摄像头
    if hasattr(window, 'f_type') and window.f_type:
        cam = window.f_type
        if hasattr(cam, 'v_timer') and cam.v_timer:
            cam.v_timer.stop()
        if hasattr(cam, 'cap') and cam.cap is not None:
            cam.cap.release()
    window.f_type = 0

    # 重置 UI
    window.label.clear()
    window.label.setText("📷 摄像头已关闭")
    window.label_6.setText("📱 手机")
    window.label_7.setText("🚬 抽烟")
    window.label_8.setText("🥤 喝水")
    window.label_9.setText("✅ 正常行驶")
    window.label_10.setText("🧠 状态良好")
    window.label_3.setText("👁 眨眼次数：0")
    window.label_4.setText("🥱 哈欠次数：0")
    window.actionOpen_camera.setEnabled(True)
    window.actionClose_camera.setEnabled(False)
    window.statusbar.showMessage("🚗 摄像头已关闭")
    Ui_MainWindow.printf(window, "⏹ 摄像头已关闭")


# ========== 👤 人脸识别对话框 ==========
def register_face_dialog():
    """注册当前驾驶员"""
    if not hasattr(window, 'f_type') or not window.f_type:
        QMessageBox.warning(window, "提示", "请先打开摄像头")
        return
    name, ok = QtWidgets.QInputDialog.getText(window, "注册驾驶员", "请输入驾驶员姓名:")
    if not ok or not name.strip():
        return
    name = name.strip()

    # 从当前帧抓取人脸
    cam = window.f_type
    if hasattr(cam, 'cap') and cam.cap is not None:
        ret, frame = cam.cap.read()
        if ret:
            success, msg = faceauth.register_face(name, frame)
            if success:
                QMessageBox.information(window, "成功", msg)
                Ui_MainWindow.printf(window, f"👤 {msg}")
            else:
                QMessageBox.warning(window, "失败", msg)
                Ui_MainWindow.printf(window, f"⚠️ {msg}")

def list_faces_dialog():
    """管理已注册的驾驶员"""
    faces = faceauth.list_faces()
    if not faces:
        QMessageBox.information(window, "驾驶员管理", "暂无注册的驾驶员")
        return
    msg = "已注册的驾驶员:\n\n"
    for i, name in enumerate(faces, 1):
        msg += f"{i}. {name}\n"
    msg += "\n输入姓名可删除 (在下方点取消则不删除)"
    name, ok = QtWidgets.QInputDialog.getItem(window, "驾驶员管理", msg + "\n选择要删除的驾驶员:", faces, editable=True)
    if ok and name.strip():
        if name in faces:
            reply = QMessageBox.question(window, "确认删除", f"确定删除 '{name}' 吗?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                succ, msg2 = faceauth.delete_face(name)
                if succ:
                    QMessageBox.information(window, "成功", msg2)
                    Ui_MainWindow.printf(window, f"👤 {msg2}")

# ========== 🌙 夜视模式切换 ==========
def toggle_night_mode(checked):
    global night_mode
    night_mode = checked
    if checked:
        Ui_MainWindow.printf(window, "🌙 夜视增强已开启")
        window.statusbar.showMessage("🌙 夜视模式已开启")
    else:
        Ui_MainWindow.printf(window, "☀️ 夜视增强已关闭")
        window.statusbar.showMessage("☀️ 正常模式")

# ========== 📱 微信报警配置对话框 ==========
def alert_config_dialog():
    cfg = alerter.load_config()

    dialog = QtWidgets.QDialog(window)
    dialog.setWindowTitle("📱 报警推送设置")
    dialog.resize(460, 360)

    layout = QVBoxLayout(dialog)

    # 启用开关
    enable_cb = QtWidgets.QCheckBox("📢 启用远程报警")
    enable_cb.setChecked(cfg.get("enabled", False))
    layout.addWidget(enable_cb)

    # 方式选择
    method_group = QtWidgets.QGroupBox("推送方式")
    method_layout = QVBoxLayout(method_group)
    wechat_radio = QtWidgets.QRadioButton("📱 微信推送（推荐）— 通过 Server酱 免费推送")
    email_radio = QtWidgets.QRadioButton("📧 邮件推送（备用）")
    wechat_radio.setChecked(cfg.get("method", "wechat") == "wechat")
    email_radio.setChecked(cfg.get("method") == "email")
    method_layout.addWidget(wechat_radio)
    method_layout.addWidget(email_radio)
    layout.addWidget(method_group)

    # ===== 微信设置面板 =====
    wechat_group = QtWidgets.QGroupBox("Server酱 设置")
    wechat_layout = QtWidgets.QFormLayout(wechat_group)

    sckey_edit = QtWidgets.QLineEdit(cfg.get("sckey", ""))
    sckey_edit.setPlaceholderText("请到 https://sct.ftqq.com/ 获取 SendKey")
    wechat_layout.addRow("SendKey:", sckey_edit)

    wechat_notice = QLabel(
        '<a href="https://sct.ftqq.com/" style="color:#58a6ff;">🔗 点此注册 Server酱 获取 SendKey（免费）</a>'
    )
    wechat_notice.setOpenExternalLinks(True)
    wechat_notice.setTextFormat(Qt.RichText)
    wechat_layout.addRow(wechat_notice)

    layout.addWidget(wechat_group)

    # ===== 邮件设置面板 =====
    email_group = QtWidgets.QGroupBox("邮件设置")
    email_layout = QtWidgets.QFormLayout(email_group)

    smtp_edit = QtWidgets.QLineEdit(cfg.get("smtp_server", "smtp.qq.com"))
    port_edit = QtWidgets.QSpinBox()
    port_edit.setRange(1, 65535)
    port_edit.setValue(cfg.get("smtp_port", 465))
    sender_edit = QtWidgets.QLineEdit(cfg.get("sender_email", ""))
    pwd_edit = QtWidgets.QLineEdit(cfg.get("sender_password", ""))
    pwd_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    recip_edit = QtWidgets.QLineEdit(", ".join(cfg.get("recipients", [])))
    recip_edit.setPlaceholderText("多个邮箱用逗号分隔")

    email_layout.addRow("SMTP:", smtp_edit)
    email_layout.addRow("端口:", port_edit)
    email_layout.addRow("发件人:", sender_edit)
    email_layout.addRow("密码:", pwd_edit)
    email_layout.addRow("收件人:", recip_edit)
    layout.addWidget(email_group)

    # 切换显示
    def switch_panel():
        is_wechat = wechat_radio.isChecked()
        wechat_group.setVisible(is_wechat)
        email_group.setVisible(not is_wechat)
        dialog.adjustSize()
    wechat_radio.toggled.connect(switch_panel)
    switch_panel()

    # 按钮
    btn_layout = QHBoxLayout()
    test_btn = QtWidgets.QPushButton("📨 测试推送")
    def do_test():
        new_cfg = {
            "enabled": enable_cb.isChecked(),
            "method": "wechat" if wechat_radio.isChecked() else "email",
            "sckey": sckey_edit.text(),
            "smtp_server": smtp_edit.text(),
            "smtp_port": port_edit.value(),
            "use_ssl": port_edit.value() == 465,
            "sender_email": sender_edit.text(),
            "sender_password": pwd_edit.text(),
            "recipients": [r.strip() for r in recip_edit.text().split(",") if r.strip()],
            "min_interval": 60,
        }
        ok, msg = alerter.test_config(new_cfg)
        if ok:
            QMessageBox.information(dialog, "测试结果", msg)
        else:
            QMessageBox.warning(dialog, "测试结果", f"❌ {msg}")
    test_btn.clicked.connect(do_test)
    btn_layout.addWidget(test_btn)

    save_btn = QtWidgets.QPushButton("💾 保存")
    def do_save():
        new_cfg = {
            "enabled": enable_cb.isChecked(),
            "method": "wechat" if wechat_radio.isChecked() else "email",
            "sckey": sckey_edit.text(),
            "smtp_server": smtp_edit.text(),
            "smtp_port": port_edit.value(),
            "use_ssl": port_edit.value() == 465,
            "sender_email": sender_edit.text(),
            "sender_password": pwd_edit.text(),
            "recipients": [r.strip() for r in recip_edit.text().split(",") if r.strip()],
            "min_interval": 60,
        }
        alerter.save_config(new_cfg)
        QMessageBox.information(dialog, "成功", "设置已保存 ✅\n报警将推送到微信" if wechat_radio.isChecked() else "设置已保存 ✅")
        dialog.accept()
    save_btn.clicked.connect(do_save)
    btn_layout.addWidget(save_btn)

    layout.addLayout(btn_layout)
    dialog.exec()


# ========== ☁️ 老板端服务器配置 ==========
def boss_config_dialog():
    global boss_server_enabled, boss_server_url, employee_name
    cfg = {"enabled": boss_server_enabled, "url": boss_server_url, "name": employee_name}

    dialog = QtWidgets.QDialog(window)
    dialog.setWindowTitle("☁️ 老板端服务器")
    dialog.resize(420, 220)

    layout = QVBoxLayout(dialog)

    enable_cb = QtWidgets.QCheckBox("📡 启用数据上报")
    enable_cb.setChecked(cfg["enabled"])
    layout.addWidget(enable_cb)

    form = QtWidgets.QFormLayout()
    name_edit = QtWidgets.QLineEdit(cfg["name"])
    name_edit.setPlaceholderText("输入员工姓名（用于老板端识别）")
    url_edit = QtWidgets.QLineEdit(cfg["url"])
    url_edit.setPlaceholderText("http://服务器IP:6789")
    form.addRow("👤 我的姓名:", name_edit)
    form.addRow("🌐 服务器地址:", url_edit)
    layout.addLayout(form)

    info = QtWidgets.QLabel("💡 先启动 server.py，再运行 boss_app.py 即可查看")
    info.setStyleSheet("color: #8b949e; font-size: 12px;")
    layout.addWidget(info)

    btn_layout = QHBoxLayout()

    test_btn = QtWidgets.QPushButton("📡 测试连接")
    def do_test():
        url = url_edit.text().strip()
        if "://" not in url: url = "http://" + url
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"{url}/health", timeout=3)
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                QMessageBox.information(dialog, "成功", "✅ 连接服务器成功！")
                Ui_MainWindow.printf(window, "☁️ 老板端服务器连接正常")
            else:
                QMessageBox.warning(dialog, "失败", "❌ 服务器响应异常")
        except Exception as e:
            QMessageBox.warning(dialog, "失败", f"❌ 无法连接:\n{str(e)[:60]}")
    test_btn.clicked.connect(do_test)
    btn_layout.addWidget(test_btn)

    save_btn = QtWidgets.QPushButton("💾 保存")
    def do_save():
        global boss_server_enabled, boss_server_url, employee_name
        boss_server_enabled = enable_cb.isChecked()
        employee_name = name_edit.text().strip()
        boss_server_url = url_edit.text().strip()
        if boss_server_enabled and employee_name:
            Ui_MainWindow.printf(window, f"☁️ 已连接到老板端: {boss_server_url}")
            report_to_server("normal", f"{employee_name} 上线")
        QMessageBox.information(dialog, "成功", "设置已保存 ✅")
        dialog.accept()
    save_btn.clicked.connect(do_save)
    btn_layout.addWidget(save_btn)

    cancel_btn = QtWidgets.QPushButton("取消")
    cancel_btn.clicked.connect(dialog.reject)
    btn_layout.addWidget(cancel_btn)

    layout.addLayout(btn_layout)
    dialog.exec()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.window_init()
    window.show()
    ret = app.exec()
    # 关闭摄像头（如果还开着）
    if hasattr(window, 'f_type') and window.f_type:
        close_camera()
    # 统计最终数据
    report_stats['yawn'] = mTOTAL
    report_stats['blink'] = TOTAL
    # 显示驾驶报告
    if session_start_time is not None:
        try:
            show_driving_report()
        except:
            pass
    close_log()
    sys.exit(ret)