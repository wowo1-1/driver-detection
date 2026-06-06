"""
Boss Monitor - Real-time employee driving status dashboard
Uses QNetworkAccessManager for non-blocking network requests
"""

import sys, json, os
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

DEFAULT_SERVER = "http://localhost:6789"
POLL_INTERVAL = 3000

STATUS_COLORS = {
    "normal": "#2ea043", "phone": "#f85149", "smoke": "#f85149",
    "drink": "#f85149", "fatigue": "#d29922", "unknown": "#484f58",
}
STATUS_LABELS = {
    "normal": "OK", "phone": "PHONE", "smoke": "SMOKE",
    "drink": "DRINK", "fatigue": "FATIGUE", "unknown": "?",
}


class EmployeeCard(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(240, 160)
        self.setStyleSheet("EmployeeCard { background-color: #161b22; border: 2px solid #30363d; border-radius: 10px; padding: 10px; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 8, 12, 8)
        self.name_label = QtWidgets.QLabel("--")
        self.name_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #c9d1d9;")
        layout.addWidget(self.name_label)
        self.driver_label = QtWidgets.QLabel("")
        self.driver_label.setStyleSheet("font-size: 11px; color: #8b949e;")
        layout.addWidget(self.driver_label)
        self.status_label = QtWidgets.QLabel("waiting...")
        self.status_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(self.status_label)
        self.time_label = QtWidgets.QLabel("")
        self.time_label.setStyleSheet("font-size: 10px; color: #484f58;")
        layout.addWidget(self.time_label)
        layout.addStretch()

    def update_data(self, emp):
        name = emp.get("name", "--")
        status = emp.get("status", "unknown")
        color = STATUS_COLORS.get(status, "#484f58")
        label = STATUS_LABELS.get(status, "?")
        self.name_label.setText(name)
        self.driver_label.setText(f"Driver: {emp.get('driver_label', '?')}")
        self.status_label.setText(label)
        self.status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color}; padding: 4px 0;")
        self.time_label.setText(emp.get("time_str", ""))
        self.setStyleSheet(f"EmployeeCard {{ background-color: #161b22; border: 2px solid {color}; border-radius: 10px; padding: 10px; }}")


class QFlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, index): return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index): return self._items.pop(index) if 0 <= index < len(self._items) else None
    def expandingDirections(self): return Qt.Orientations()
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self._do(QtCore.QRect(0, 0, width, 0), True)
    def setGeometry(self, rect): super().setGeometry(rect); self._do(rect, False)
    def sizeHint(self): return QtCore.QSize(240, 160)
    def minimumSize(self): return QtCore.QSize(240, 160)
    def _do(self, rect, test):
        x, y, lh, sp = rect.x(), rect.y(), 0, max(self.spacing(), 12)
        for item in self._items:
            w = item.widget()
            if not w or not w.isVisible(): continue
            h = w.sizeHint()
            nx = x + h.width() + sp
            if nx > rect.right() + sp and x > rect.x():
                x, y, lh = rect.x(), y + lh + sp, 0
            item.setGeometry(QtCore.QRect(x, y, h.width(), h.height()))
            x += h.width() + sp
            lh = max(lh, h.height())
        return y + lh - rect.y()


class BossWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.server_url = DEFAULT_SERVER
        self._nam = QNetworkAccessManager()
        self._nam.finished.connect(self._on_reply)
        self._pending = "status"
        self._employees = {}
        self._events = []
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        self.setWindowTitle("Boss Monitor - Driver Detection")
        self.resize(1100, 700)
        self.setStyleSheet("""
            QMainWindow { background-color: #0d1117; }
            QWidget { color: #c9d1d9; font-size: 13px; }
            QLabel { color: #c9d1d9; }
            QPushButton { background-color: #21262d; border: 1px solid #30363d;
                border-radius: 6px; padding: 8px 16px; color: #c9d1d9; }
            QPushButton:hover { background-color: #30363d; border-color: #58a6ff; }
            QScrollBar:vertical { background: #0d1117; width: 8px; border: none; }
            QScrollBar::handle:vertical { background: #30363d; border-radius: 4px; }
        """)
        cw = QtWidgets.QWidget(); self.setCentralWidget(cw)
        ml = QtWidgets.QVBoxLayout(cw); ml.setSpacing(12); ml.setContentsMargins(16, 12, 16, 12)

        # Top bar
        tb = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Boss Monitor"); title.setStyleSheet("font-size: 22px; font-weight: bold; color: #c9d1d9;")
        tb.addWidget(title); tb.addStretch()
        self.conn_label = QtWidgets.QLabel("(disconnected)"); self.conn_label.setStyleSheet("color: #f85149;")
        tb.addWidget(self.conn_label)
        self.emp_label = QtWidgets.QLabel("Employees: 0"); self.emp_label.setStyleSheet("color: #8b949e;")
        tb.addWidget(self.emp_label)
        self.addr_edit = QtWidgets.QLineEdit(self.server_url); self.addr_edit.setFixedWidth(200)
        self.addr_edit.setStyleSheet("background: #21262d; border: 1px solid #30363d; border-radius: 4px; padding: 6px 8px; color: #c9d1d9;")
        tb.addWidget(self.addr_edit)
        btn = QtWidgets.QPushButton("Connect"); btn.clicked.connect(self._connect); tb.addWidget(btn)
        ml.addLayout(tb)

        # Cards
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setStyleSheet("QScrollArea { border: none; }")
        sw = QtWidgets.QWidget(); self.cl = QFlowLayout(sw); sc.setWidget(sw); ml.addWidget(sc, 3)

        # Events
        el = QtWidgets.QLabel("Alerts"); el.setStyleSheet("font-size: 15px; font-weight: bold; color: #58a6ff;")
        ml.addWidget(el)
        es = QtWidgets.QScrollArea(); es.setWidgetResizable(True); es.setFixedHeight(180)
        es.setStyleSheet("QScrollArea { border: 1px solid #30363d; border-radius: 6px; }")
        ew = QtWidgets.QWidget(); self.evl = QtWidgets.QVBoxLayout(ew); self.evl.setSpacing(0)
        self.evl.setContentsMargins(4, 4, 4, 4); self.evl.addStretch()
        es.setWidget(ew); ml.addWidget(es, 1)

        self.sb = QtWidgets.QStatusBar(); self.sb.setStyleSheet("background: #161b22; border-top: 1px solid #30363d; padding: 4px;")
        self.setStatusBar(self.sb); self.sb.showMessage("Waiting...")

    def _async_get(self, path):
        url = QUrl(f"{self.server_url}{path}")
        req = QNetworkRequest(url); req.setTransferTimeout(3000)
        self._nam.get(req)

    def _setup_timer(self):
        QTimer(self).timeout.connect(self._tick)
        QTimer(self).start(POLL_INTERVAL)
        QTimer.singleShot(200, self._tick)

    def _connect(self):
        addr = self.addr_edit.text().strip()
        if "://" not in addr: addr = "http://" + addr
        self.server_url = addr
        QTimer.singleShot(100, self._tick)

    def _tick(self):
        self._pending = "status"
        self._async_get("/status")

    def _on_reply(self, reply):
        if reply.error():
            self.conn_label.setText("(disconnected)"); self.conn_label.setStyleSheet("color: #f85149;")
            self.sb.showMessage("Connection failed")
            return
        try:
            data = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
        except Exception:
            return

        if self._pending == "status":
            self._pending = "events"
            emps = data.get("employees", {})
            self._employees = emps
            total = len(emps)
            self.conn_label.setText("(connected)"); self.conn_label.setStyleSheet("color: #2ea043;")
            self.emp_label.setText(f"Employees: {total}")
            self.sb.showMessage(f"Connected | {total} employees")
            names = list(emps.keys())
            while self.cl.count() > len(names):
                it = self.cl.takeAt(self.cl.count()-1)
                if it and it.widget(): it.widget().deleteLater()
            for i, n in enumerate(names):
                if i < self.cl.count():
                    self.cl.itemAt(i).widget().update_data(emps[n])
                else:
                    c = EmployeeCard(); c.update_data(emps[n]); self.cl.addWidget(c)
            self._async_get("/events")
        else:
            evs = data.get("events", [])
            self._events = evs
            while self.evl.count() > 1:
                it = self.evl.takeAt(0)
                if it and it.widget(): it.widget().deleteLater()
            for evt in reversed(evs[-50:]):
                h = QtWidgets.QHBoxLayout()
                h.setContentsMargins(8, 2, 8, 2)
                t = QtWidgets.QLabel(evt.get("time","")[-8:]); t.setFixedWidth(70); t.setStyleSheet("color: #484f58;")
                n = QtWidgets.QLabel(evt.get("name","?")); n.setFixedWidth(60); n.setStyleSheet("color: #c9d1d9; font-weight: bold;")
                s = STATUS_LABELS.get(evt.get("type",""), evt.get("type",""))
                c = STATUS_COLORS.get(evt.get("type",""), "#484f58")
                st = QtWidgets.QLabel(s); st.setFixedWidth(80); st.setStyleSheet(f"color: {c}; font-weight: bold;")
                d = QtWidgets.QLabel(evt.get("detail","")); d.setStyleSheet("color: #8b949e;")
                w = QtWidgets.QWidget()
                hl = QtWidgets.QHBoxLayout(w); hl.setContentsMargins(0,0,0,0)
                hl.addWidget(t); hl.addWidget(n); hl.addWidget(st); hl.addWidget(d, 1)
                self.evl.insertWidget(self.evl.count()-1, w)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = BossWindow(); win.show()
    sys.exit(app.exec())
