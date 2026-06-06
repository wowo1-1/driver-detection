"""
驾驶检测中央服务器
接收员工检测数据，提供 REST API 供老板端查询
"""

import http.server
import json
import time
import os
import threading
import urllib.parse
from datetime import datetime

HOST = "0.0.0.0"
PORT = 6789
DATA_FILE = "server_data.json"

# ========== 数据存储 ==========
_lock = threading.Lock()
employees = {}       # { "员工姓名": { "status": "...", "last_event": "...", "timestamp": ... } }
events = []          # [ { "name", "type", "detail", "timestamp" }, ... ]
MAX_EVENTS = 1000    # 最多保留的事件数


def save_data():
    """持久化保存数据"""
    with _lock:
        data = {"employees": employees, "events": events[-200:]}
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def load_data():
    """加载已有数据"""
    global employees, events
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                employees = data.get("employees", {})
                events = data.get("events", [])
        except:
            pass


def add_event(name, event_type, detail):
    """添加一条事件记录"""
    global events
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    event = {"name": name, "type": event_type, "detail": detail, "time": ts}
    events.append(event)
    if len(events) > MAX_EVENTS:
        events = events[-MAX_EVENTS:]


# ========== HTTP 处理 ==========
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/status":
            """获取所有员工当前状态"""
            with _lock:
                self._send_json({"employees": dict(employees), "total": len(employees)})

        elif path == "/events":
            """获取事件历史"""
            with _lock:
                limit = min(int(self.headers.get("X-Limit", 100)), MAX_EVENTS)
                self._send_json({"events": events[-limit:], "total": len(events)})

        elif path == "/health":
            self._send_json({"status": "ok", "time": datetime.now().isoformat()})

        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/report":
            """接收员工端检测报告"""
            try:
                data = self._read_body()
                name = data.get("name", "未知")
                event_type = data.get("type", "unknown")
                detail = data.get("detail", "")

                with _lock:
                    # 更新员工状态
                    now = time.time()
                    employees[name] = {
                        "name": name,
                        "status": event_type,
                        "detail": detail,
                        "timestamp": now,
                        "time_str": datetime.now().strftime("%H:%M:%S"),
                        "driver_label": data.get("driver", "未识别"),
                    }
                    # 记录事件（非"正常"才记录）
                    if event_type != "normal":
                        add_event(name, event_type, detail)

                save_data()

                # 触发同步通知（不等待）
                self._send_json({"ok": True})

            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif path == "/clear":
            """清空所有数据"""
            with _lock:
                employees.clear()
                events.clear()
            save_data()
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "not found"}, 404)


def run_server():
    """启动服务器"""
    load_data()
    server = http.server.HTTPServer((HOST, PORT), Handler)
    print(f"[OK] 驾驶检测服务器已启动: http://{HOST}:{PORT}")
    print(f"  [上报] POST http://你的IP:{PORT}/report")
    print(f"  [查看] GET  http://你的IP:{PORT}/status")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
