"""
人脸识别模块 — 区分不同驾驶员
使用 dlib 面部 68 关键点之间的几何距离作为人脸特征向量
无需额外下载模型，复用项目中已有的 shape_predictor
"""

import os
import json
import datetime
import numpy as np
import cv2
import dlib
from imutils import face_utils

FACES_DIR = "registered_faces"
os.makedirs(FACES_DIR, exist_ok=True)
FACES_DB = os.path.join(FACES_DIR, "faces_db.json")

# 人脸特征定义：用哪些关键点之间的距离作为特征
# 参考 68 点 facial landmarks 的索引
FEATURE_PAIRS = [
    # 眉眼距离 (左)
    ((38, 42), (19, 24)),   # 左眼中心到左眉
    ((43, 47), (20, 24)),   # 右眼中心到右眉
    # 眼睛宽度
    ((36, 39), None),        # 左眼宽度
    ((42, 45), None),        # 右眼宽度
    # 鼻子特征
    ((31, 35), None),        # 鼻梁长度
    ((27, 30), None),        # 鼻根到鼻尖
    # 嘴巴特征
    ((48, 54), None),        # 嘴宽
    ((51, 57), None),        # 嘴高
    # 面部宽度 (颧骨)
    ((0, 16), None),         # 脸宽
    # 下巴到鼻尖
    ((8, 30), None),         # 下巴到鼻尖
    # 下巴到眉
    ((8, 19), (8, 24)),      # 下巴到左眉 / 下巴到右眉
]


def _get_feature_vector(shape):
    """
    从 68 关键点提取特征向量 (归一化后的几何距离)
    shape: dlib.full_object_detection 或 numpy array
    """
    if isinstance(shape, dlib.full_object_detection):
        shape = face_utils.shape_to_np(shape)

    features = []
    # 用眼距作为归一化基准（每个人眼距相对稳定）
    left_eye_center = np.mean(shape[36:42], axis=0)
    right_eye_center = np.mean(shape[42:48], axis=0)
    baseline = np.linalg.norm(left_eye_center - right_eye_center)
    if baseline == 0:
        baseline = 1.0

    for pair1, pair2 in FEATURE_PAIRS:
        # 计算第一个距离
        if pair1[0] == pair1[1] or len(pair1) == 1:
            d1 = 0
        elif len(pair1) == 2:
            d1 = np.linalg.norm(shape[pair1[0]] - shape[pair1[1]])
        else:
            pts = shape[pair1[0]:pair1[1]+1]
            d1 = np.linalg.norm(pts.mean(axis=0) - shape[pair1[0]])

        if pair2 is None:
            features.append(d1 / baseline)
        else:
            if len(pair2) == 2:
                d2 = np.linalg.norm(shape[pair2[0]] - shape[pair2[1]])
            else:
                pts = shape[pair2[0]:pair2[1]+1]
                d2 = np.linalg.norm(pts.mean(axis=0) - shape[pair2[0]])
            features.append(d1 / d2 if d2 != 0 else 0)

    return np.array(features, dtype=np.float32)


def _load_db():
    """加载已注册的人脸数据库"""
    if os.path.exists(FACES_DB):
        with open(FACES_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_db(db):
    """保存人脸数据库"""
    with open(FACES_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


# 全局检测器（复用 myfatigue 中的，或者自己初始化）
_detector = dlib.get_frontal_face_detector()
_predictor = None

def _get_predictor():
    """延迟加载 dlib 预测器"""
    global _predictor
    if _predictor is None:
        predictor_path = 'weights/shape_predictor_68_face_landmarks.dat'
        if os.path.exists(predictor_path):
            _predictor = dlib.shape_predictor(predictor_path)
        else:
            raise FileNotFoundError(f"找不到 dlib 模型: {predictor_path}")
    return _predictor


def detect_face(frame):
    """
    检测帧中最大的正面人脸
    返回: (face_rect, shape_array) 或 (None, None)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    rects = _detector(gray, 0)
    if not rects:
        return None, None

    # 取最大的人脸
    rect = max(rects, key=lambda r: r.width() * r.height())
    predictor = _get_predictor()
    shape = predictor(gray, rect)
    shape_np = face_utils.shape_to_np(shape)
    return rect, shape_np


def register_face(name, frame, force=False):
    """
    注册新驾驶员
    name: 驾驶员姓名
    frame: 包含人脸的帧
    force: 是否覆盖已有注册
    返回: True/False + 消息
    """
    db = _load_db()
    if name in db and not force:
        return False, f"驾驶员 '{name}' 已存在"

    rect, shape = detect_face(frame)
    if rect is None:
        return False, "未检测到人脸"

    features = _get_feature_vector(shape).tolist()

    # 裁切人脸区域保存预览
    x1, y1, x2, y2 = max(0, rect.left()), max(0, rect.top()), rect.right(), rect.bottom()
    face_img = frame[y1:y2, x1:x2]
    preview_path = os.path.join(FACES_DIR, f"{name}.jpg")
    if face_img.size > 0:
        cv2.imwrite(preview_path, face_img)

    db[name] = {
        "features": features,
        "preview": f"{name}.jpg",
        "registered": datetime.datetime.now().isoformat()
    }
    _save_db(db)
    return True, f"驾驶员 '{name}' 注册成功"


def recognize_face(frame, threshold=0.35):
    """
    识别人脸
    返回: (name, similarity) 或 (None, None)
    threshold: 相似度阈值（越低越严格）
    """
    db = _load_db()
    if not db:
        return None, None

    rect, shape = detect_face(frame)
    if rect is None:
        return None, None

    query_features = _get_feature_vector(shape)

    best_name = None
    best_sim = float('inf')

    for name, data in db.items():
        stored = np.array(data["features"], dtype=np.float32)
        # 欧氏距离
        dist = np.linalg.norm(query_features - stored)
        # 归一化到 0~1 的相似度 (越小越相似)
        sim = dist / (1 + dist)  # 映射到 (0,1)
        if sim < best_sim:
            best_sim = sim
            best_name = name

    if best_sim < threshold:
        return best_name, round(1 - best_sim, 3)
    return None, None


def list_faces():
    """列出所有注册的驾驶员"""
    db = _load_db()
    return list(db.keys())


def delete_face(name):
    """删除驾驶员"""
    db = _load_db()
    if name in db:
        preview = db[name].get("preview")
        if preview:
            p_path = os.path.join(FACES_DIR, preview)
            if os.path.exists(p_path):
                os.remove(p_path)
        del db[name]
        _save_db(db)
        return True, f"已删除 '{name}'"
    return False, f"未找到 '{name}'"
