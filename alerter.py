"""
远程报警模块
支持：
  1️⃣ Server酱（WeChat Push）— 免费、简单、直接推送到微信
  2️⃣ 邮件（SMTP）— 备用方案
"""

import threading
import json
import os
import time
import urllib.request
import urllib.parse

CONFIG_FILE = "alerter_config.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "method": "wechat",           # "wechat" | "email"
    # WeChat (Server酱)
    "sckey": "",                  # Server酱 SendKey (https://sct.ftqq.com/)
    # 邮件
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "use_ssl": True,
    "sender_email": "",
    "sender_password": "",
    "recipients": [],
    # 通用
    "min_interval": 60,           # 同一类警报最小间隔（秒）
}

_last_alert_time = {"phone": 0, "smoke": 0, "drink": 0, "fatigue": 0}
_lock = threading.Lock()


def load_config():
    """加载配置"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def test_config(cfg):
    """测试配置是否可用"""
    try:
        if cfg.get("method") == "wechat":
            sckey = cfg.get("sckey", "")
            if not sckey:
                return False, "请输入 Server酱 SendKey"
            # 发送测试消息
            url = f"https://sct.ftqq.com/{sckey}.send"
            data = urllib.parse.urlencode({
                "title": "🚗 驾驶检测系统测试",
                "desp": "这是一条测试消息\n如果收到说明配置正确 ✅"
            }).encode()
            req = urllib.request.Request(url, data=data)
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                return True, "✅ 测试消息已发送到微信，请查看"
            else:
                return False, f"发送失败: {result.get('message', '未知错误')}"
        else:
            # 测试邮件
            import smtplib
            server = smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"]) if cfg["use_ssl"] else smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
            if not cfg["use_ssl"]:
                server.starttls()
            server.login(cfg["sender_email"], cfg["sender_password"])
            server.quit()
            return True, "邮件服务器连接成功"
    except Exception as e:
        return False, str(e)


def send_alert(alert_type, detail, screenshot_path=None):
    """
    发送报警（异步，带频率控制）
    alert_type: 'phone' | 'smoke' | 'drink' | 'fatigue'
    """
    cfg = load_config()
    if not cfg.get("enabled"):
        return

    # 频率控制
    now = time.time()
    with _lock:
        last = _last_alert_time.get(alert_type, 0)
        if now - last < cfg.get("min_interval", 60):
            return
        _last_alert_time[alert_type] = now

    method = cfg.get("method", "wechat")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    label_map = {"phone": "📱 使用手机", "smoke": "🚬 抽烟", "drink": "🥤 喝水", "fatigue": "😴 疲劳"}

    if method == "wechat":
        _send_wechat(alert_type, detail, ts, label_map, cfg)
    else:
        _send_email(alert_type, detail, ts, screenshot_path, label_map, cfg)


def _send_wechat(alert_type, detail, ts, label_map, cfg):
    """通过 Server酱 发送微信推送"""
    sckey = cfg.get("sckey", "")
    if not sckey:
        return

    def _do():
        try:
            label = label_map.get(alert_type, alert_type)
            title = f"🚗 {label}"
            desp = f"""
## 🚗 驾驶警报

| 项目 | 内容 |
|------|------|
| **类型** | {label} |
| **详情** | {detail} |
| **时间** | {ts} |

---
*由驾驶员分心检测系统自动发送*
"""
            url = f"https://sct.ftqq.com/{sckey}.send"
            data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _send_email(alert_type, detail, ts, screenshot_path, label_map, cfg):
    """通过 SMTP 发送邮件"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.image import MIMEImage

    recipients = cfg.get("recipients", [])
    if not recipients or not cfg.get("sender_email"):
        return

    def _do():
        try:
            label = label_map.get(alert_type, alert_type)
            msg = MIMEMultipart()
            msg["From"] = cfg["sender_email"]
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = f"🚗 驾驶警报 - {label}"

            body = f"""<h2>🚗 驾驶员分心检测系统</h2>
<p><b>类型:</b> {label}</p>
<p><b>详情:</b> {detail}</p>
<p><b>时间:</b> {ts}</p>
<hr><p style="color:gray;">自动发送</p>"""
            msg.attach(MIMEText(body, "html", "utf-8"))

            if screenshot_path and os.path.exists(screenshot_path):
                with open(screenshot_path, "rb") as f:
                    img = MIMEImage(f.read())
                    img.add_header("Content-Disposition", "attachment", filename=os.path.basename(screenshot_path))
                    msg.attach(img)

            if cfg["use_ssl"]:
                server = smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"])
            else:
                server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
                server.starttls()
            server.login(cfg["sender_email"], cfg["sender_password"])
            server.send_message(msg)
            server.quit()
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()
