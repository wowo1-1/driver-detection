"""
👔 老板端监控面板
实时查看所有员工驾驶状态，接收违规报警
"""

import sys
import json
import time
import threading
import urllib.request
from datetime import datetime
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

# ===== 配置 =====
DEFAULT_SERVER = "localhost:6789"
POLL_INTERVAL = 2  # 每2秒刷新一次

# 状态颜色映射
STATUS_COLORS = {
    "normal": "#2ea043",       # 绿色 - 正常
    "phone": "#f85149",        # 红色 - 手机
    "smoke": "#f85149",        # 红色 - 抽烟
    "drink": "#f85149",        # 红色 - 喝水
    "fatigue": "#d29922",      # 黄色 - 疲劳
    "unknown": "#484f58",      # 灰色 - 未知
}
STATUS_LABELS = {
    "normal": "✅ 正常行驶",
    "phone": "📱 使用手机",
    "smoke": "🚬 抽烟",
    "drink": "🥤 喝水",
    "fatigue": "😴 疲劳",
    "unknown": "❓ 未知",
}


class EmployeeCard(QtWidgets.QFrame):
    """员工状态卡片"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(260, 180)
        self.setStyleSheet("""
            EmployeeCard {
                background-color: #161b22;
                border: 2px solid #30363d;
                border-radius: 12px;
                padding: 12px;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(16, 12, 16, 12)

        # 员工姓名
        self.name_label = QtWidgets.QLabel("—")
        self.name_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #c9d1d9;")
        layout.addWidget(self.name_label)

        # 驾驶员
        self.driver_label = QtWidgets.QLabel("")
        self.driver_label.setStyleSheet("font-size: 12px; color: #8b949e;")
        layout.addWidget(self.driver_label)

        # 状态
        self.status_label = QtWidgets.QLabel("等待数据...")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 6px 0;")
        layout.addWidget(self.status_label)

        # 详情
        self.detail_label = QtWidgets.QLabel("")
        self.detail_label.setStyleSheet("font-size: 12px; color: #8b949e;")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        # 时间
        self.time_label = QtWidgets.QLabel("")
        self.time_label.setStyleSheet("font-size: 11px; color: #484f58;")
        layout.addWidget(self.time_label)

        layout.addStretch()

    def update_data(self, emp_data):
        """更新卡片数据"""
        name = emp_data.get("name", "—")
        status = emp_data.get("status", "unknown")
        detail = emp_data.get("detail", "")
        time_str = emp_data.get("time_str", "")
        driver = emp_data.get("driver_label", "未识别")

        color = STATUS_COLORS.get(status, "#484f58")
        label = STATUS_LABELS.get(status, "❓ 未知")

        self.name_label.setText(f"👤 {name}")
        self.driver_label.setText(f"驾驶员: {driver}")
        self.status_label.setText(label)
        self.status_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color}; padding: 6px 0;")
        self.detail_label.setText(detail)
        self.time_label.setText(f"⏱ {time_str}")

        # 根据状态切换卡片边框颜色
        border_color = color
        self.setStyleSheet(f"""
            EmployeeCard {{
                background-color: #161b22;
                border: 2px solid {border_color};
                border-radius: 12px;
                padding: 12px;
            }}
        """)


class EventWidget(QtWidgets.QWidget):
    """事件列表项"""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        name = event.get("name", "?")
        etype = event.get("type", "?")
        detail = event.get("detail", "")
        etime = event.get("time", "")
        elabel = STATUS_LABELS.get(etype, etype)
        ecolor = STATUS_COLORS.get(etype, "#484f58")

        time_lbl = QtWidgets.QLabel(etime)
        time_lbl.setFixedWidth(80)
        time_lbl.setStyleSheet("color: #484f58; font-size: 11px;")

        name_lbl = QtWidgets.QLabel(name)
        name_lbl.setFixedWidth(60)
        name_lbl.setStyleSheet("color: #c9d1d9; font-weight: bold; font-size: 12px;")

        type_lbl = QtWidgets.QLabel(elabel)
        type_lbl.setFixedWidth(100)
        type_lbl.setStyleSheet(f"color: {ecolor}; font-size: 12px; font-weight: bold;")

        detail_lbl = QtWidgets.QLabel(detail)
        detail_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")

        layout.addWidget(time_lbl)
        layout.addWidget(name_lbl)
        layout.addWidget(type_lbl)
        layout.addWidget(detail_lbl, 1)


class BossWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.server_url = DEFAULT_SERVER
        self.prev_event_count = 0
        self._poll_busy = False          # 避免请求堆积
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        self.setWindowTitle("👔 老板驾驶监控面板")
        self.resize(1100, 700)

        # 暗色主题
        self.setStyleSheet("""
            QMainWindow { background-color: #0d1117; }
            QWidget { color: #c9d1d9; font-size: 13px; }
            QLabel { color: #c9d1d9; }
            QPushButton {
                background-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 8px 16px;
                color: #c9d1d9;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #30363d;
                border-color: #58a6ff;
            }
            QScrollBar:vertical {
                background: #0d1117; width: 8px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #30363d; border-radius: 4px;
            }
        """)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 12, 16, 12)

        # ===== 顶部栏 =====
        top_bar = QtWidgets.QHBoxLayout()

        title = QtWidgets.QLabel("👔 驾驶监控中心")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #c9d1d9;")
        top_bar.addWidget(title)

        top_bar.addStretch()

        self.conn_label = QtWidgets.QLabel("🔴 未连接")
        self.conn_label.setStyleSheet("font-size: 13px; color: #f85149;")
        top_bar.addWidget(self.conn_label)

        self.emp_count_label = QtWidgets.QLabel("员工: 0")
        self.emp_count_label.setStyleSheet("font-size: 13px; color: #8b949e;")
        top_bar.addWidget(self.emp_count_label)

        # 服务器地址输入
        self.addr_edit = QtWidgets.QLineEdit(self.server_url)
        self.addr_edit.setFixedWidth(180)
        self.addr_edit.setPlaceholderText("服务器地址:端口")
        self.addr_edit.setStyleSheet("""
            background-color: #21262d; border: 1px solid #30363d;
            border-radius: 4px; padding: 6px 8px; color: #c9d1d9;
        """)
        top_bar.addWidget(self.addr_edit)

        connect_btn = QtWidgets.QPushButton("🔄 连接")
        connect_btn.clicked.connect(self._connect_server)
        top_bar.addWidget(connect_btn)

        main_layout.addLayout(top_bar)

        # ===== 员工卡片区域 =====
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        scroll_widget = QtWidgets.QWidget()
        self.cards_layout = QFlowLayout(scroll_widget)
        self.cards_layout.setSpacing(12)
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 3)

        # ===== 事件日志 =====
        log_label = QtWidgets.QLabel("📋 实时报警记录")
        log_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #58a6ff;")
        main_layout.addWidget(log_label)

        self.event_scroll = QtWidgets.QScrollArea()
        self.event_scroll.setWidgetResizable(True)
        self.event_scroll.setFixedHeight(200)
        self.event_scroll.setStyleSheet("QScrollArea { border: 1px solid #30363d; border-radius: 8px; }")

        self.event_widget = QtWidgets.QWidget()
        self.event_layout = QtWidgets.QVBoxLayout(self.event_widget)
        self.event_layout.setSpacing(0)
        self.event_layout.setContentsMargins(4, 4, 4, 4)
        self.event_layout.addStretch()
        self.event_scroll.setWidget(self.event_widget)
        main_layout.addWidget(self.event_scroll, 1)

        # ===== 底部状态栏 =====
        self.statusbar = QtWidgets.QStatusBar()
        self.statusbar.setStyleSheet("background: #161b22; border-top: 1px solid #30363d; padding: 4px;")
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("🚗 等待连接服务器...")

    def _setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self._poll_trigger)
        self.timer.start(2000)

    def _connect_server(self):
        addr = self.addr_edit.text().strip()
        if "://" not in addr:
            addr = "http://" + addr
        self.server_url = addr.replace("http://http://", "http://")
        self.statusbar.showMessage(f"🔄 连接 {self.server_url}...")
        self._poll_trigger()

    def _poll_trigger(self):
        """触发后台轮询（不卡主线程）"""
        if self._poll_busy:
            return
        self._poll_busy = True
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_worker(self):
        """后台线程拉取数据"""
        try:
            # 拉取员工状态
            url = f"{self.server_url}/status"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read().decode("utf-8"))
            employees = data.get("employees", {})
            total = data.get("total", 0)

            # 在主线程更新 UI
            QTimer.singleShot(0, lambda: self._update_status(employees, total, None))

            # 拉取事件
            url2 = f"{self.server_url}/events"
            req2 = urllib.request.Request(url2, headers={"X-Limit": "50"})
            resp2 = urllib.request.urlopen(req2, timeout=3)
            events_data = json.loads(resp2.read().decode("utf-8"))
            events = events_data.get("events", [])

            QTimer.singleShot(0, lambda: self._update_events(events))

        except Exception as e:
            QTimer.singleShot(0, lambda: self._update_status({}, 0, str(e)))
        finally:
            QTimer.singleShot(0, lambda: setattr(self, '_poll_busy', False))

    def _update_status(self, employees, total, error):
        """主线程更新 UI（安全调用）"""
        if error:
            self.conn_label.setText("🔴 未连接")
            self.conn_label.setStyleSheet("font-size: 13px; color: #f85149;")
            self.statusbar.showMessage(f"🔴 无法连接服务器: {error[:30]}...")
            return

        self.conn_label.setText("🟢 已连接")
        self.conn_label.setStyleSheet("font-size: 13px; color: #2ea043;")
        self.emp_count_label.setText(f"👥 员工: {total}")
        self.statusbar.showMessage(f"🟢 已连接  |  {total} 名员工在线")

        # 更新卡片
        names = list(employees.keys())
        while self.cards_layout.count() > len(names):
            item = self.cards_layout.takeAt(self.cards_layout.count() - 1)
            if item and item.widget():
                item.widget().deleteLater()
        for i, name in enumerate(names):
            if i < self.cards_layout.count():
                card = self.cards_layout.itemAt(i).widget()
            else:
                card = EmployeeCard()
                self.cards_layout.addWidget(card)
            card.update_data(employees[name])

    def _update_events(self, events):
        """主线程更新事件列表"""
        while self.event_layout.count() > 1:
            item = self.event_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        for evt in reversed(events[-50:]):
            ew = EventWidget(evt)
            self.event_layout.insertWidget(self.event_layout.count() - 1, ew)

    def _update_cards(self, employees):
        """更新员工卡片"""
        # 清除多余卡片
        names = list(employees.keys())
        while self.cards_layout.count() > len(names):
            item = self.cards_layout.takeAt(self.cards_layout.count() - 1)
            if item and item.widget():
                item.widget().deleteLater()

        # 更新/新建卡片
        for i, name in enumerate(names):
            if i < self.cards_layout.count():
                card = self.cards_layout.itemAt(i).widget()
            else:
                card = EmployeeCard()
                self.cards_layout.addWidget(card)
            card.update_data(employees[name])

    def _update_events(self):
        """更新事件列表"""
        try:
            url = f"{self.server_url}/events"
            req = urllib.request.Request(url, headers={"X-Limit": "50"})
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read().decode("utf-8"))
            events = data.get("events", [])

            # 清除旧事件
            while self.event_layout.count() > 1:
                item = self.event_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

            # 新事件倒序显示
            for evt in reversed(events[-50:]):
                ew = EventWidget(evt)
                self.event_layout.insertWidget(self.event_layout.count() - 1, ew)

        except:
            pass


# ===== QFlowLayout: 让卡片自动换行 =====
class QFlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return QtCore.QSize(260, 180)

    def minimumSize(self):
        return QtCore.QSize(260, 180)

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        if spacing < 0:
            spacing = 12

        for item in self._items:
            widget = item.widget()
            if not widget or not widget.isVisible():
                continue
            hint = widget.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x > rect.right() + spacing and x > rect.x():
                x = rect.x()
                y += line_height + spacing
                line_height = 0
            item.setGeometry(QtCore.QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = BossWindow()
    win.show()
    sys.exit(app.exec())
