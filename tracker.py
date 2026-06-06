"""
轻量级目标追踪器（SORT 风格）
- 为每个检测到的目标分配唯一 ID
- 用卡尔曼滤波器平滑位置
- IoU 匹配关联帧间目标
- 减少检测抖动和误报
"""

import numpy as np
from collections import defaultdict


def iou(box1, box2):
    """计算两个边界框的 IoU (xyxy 格式)"""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0


class KalmanBoxTracker:
    """
    单个目标的卡尔曼滤波器追踪器
    使用恒速度模型在图像空间中追踪边界框
    """
    count = 0

    def __init__(self, bbox, label, conf):
        """
        初始化追踪器
        bbox: [x1, y1, x2, y2]
        """
        KalmanBoxTracker.count += 1
        self.id = KalmanBoxTracker.count
        self.label = label
        self.conf = conf

        # 确保 bbox 是普通 Python float/int（防止 PyTorch 张量传入）
        bbox = [float(v) for v in bbox]
        # 状态: [x, y, s, r, dx, dy, ds]
        # x, y = 中心点, s = 面积, r = 宽高比
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        self.x = bbox[0] + w / 2
        self.y = bbox[1] + h / 2
        self.s = w * h
        self.r = w / h if h > 0 else 1.0

        # 卡尔曼滤波器参数（简单实现）
        self.dx = 0
        self.dy = 0
        self.ds = 0

        self.history = [bbox]
        self.hits = 1          # 匹配成功次数
        self.no_loss = 0       # 丢失帧数
        self.confirmed = False # 是否已确认（连续匹配 HIT_THRESH 帧后确认）

        # 平滑后的边界框
        self.smoothed_bbox = bbox

    def update(self, bbox, conf):
        """
        用新的检测更新追踪器
        """
        bbox = [float(v) for v in bbox]
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        new_x = bbox[0] + w / 2
        new_y = bbox[1] + h / 2
        new_s = w * h
        new_r = w / h if h > 0 else 1.0

        # 速度估计（用于预测）
        self.dx = new_x - self.x
        self.dy = new_y - self.y
        self.ds = new_s - self.s

        self.x = new_x
        self.y = new_y
        self.s = new_s
        self.r = new_r

        self.conf = conf
        self.hits += 1
        self.no_loss = 0

        # 确认条件：连续匹配帧数
        if self.hits >= 3:
            self.confirmed = True

        # 指数移动平均平滑
        alpha = 0.6
        if len(self.history) > 0:
            last = self.history[-1]
            smooth_bbox = [
                int(alpha * bbox[0] + (1 - alpha) * last[0]),
                int(alpha * bbox[1] + (1 - alpha) * last[1]),
                int(alpha * bbox[2] + (1 - alpha) * last[2]),
                int(alpha * bbox[3] + (1 - alpha) * last[3]),
            ]
        else:
            smooth_bbox = [int(v) for v in bbox]

        self.smoothed_bbox = smooth_bbox
        self.history.append(smooth_bbox)
        if len(self.history) > 10:
            self.history.pop(0)

    def predict(self):
        """预测下一帧位置（恒速度模型）"""
        # 丢失多帧后衰减速度，防止漂移
        decay = max(0.5, 1.0 - self.no_loss * 0.1)
        self.x += self.dx * decay
        self.y += self.dy * decay
        self.s += self.ds * decay

        # 防止面积 / 宽高比变成负数导致 sqrt(NaN)
        self.s = max(self.s, 16.0)        # 至少 4x4 像素
        self.r = max(self.r, 0.1)         # 宽高比 > 0

        w = np.sqrt(self.s * self.r)
        h = self.s / w if w > 0 else 16.0

        cx = max(0, self.x)
        cy = max(0, self.y)

        return [
            int(max(0, cx - w / 2)),
            int(max(0, cy - h / 2)),
            int(cx + w / 2),
            int(cy + h / 2),
        ]

    def get_state(self):
        """获取当前平滑后的边界框（防止 NaN）"""
        if any(np.isnan(v) for v in self.smoothed_bbox):
            return [0, 0, 0, 0]
        return self.smoothed_bbox


class ObjectTracker:
    """
    多目标追踪器
    管理所有 KalmanBoxTracker 实例
    """
    def __init__(self, iou_threshold=0.3, max_lost=10):
        self.trackers = []
        self.iou_threshold = iou_threshold
        self.max_lost = max_lost

    def update(self, detections):
        """
        更新追踪状态
        detections: [(label, conf, [x1,y1,x2,y2]), ...]
        returns: [(track_id, label, conf, [x1,y1,x2,y2]), ...]
        """
        # ---- 预测现有追踪位置 ----
        for t in self.trackers:
            t.predict()

        # ---- 过滤无效检测（NaN / 负数框） ----
        clean_detections = []
        for d in detections:
            if len(d) < 3:
                continue
            label, conf, bbox = d[:3]
            if len(bbox) < 4:
                continue
            bbox = [float(v) for v in bbox]
            if any(np.isnan(v) or v < 0 for v in bbox):
                continue
            clean_detections.append([label, conf, bbox])
        detections = clean_detections

        # ---- IoU 匹配 ----
        matched_tracks = set()
        matched_dets = set()
        assignments = []

        if self.trackers and detections:
            # 构建 IoU 矩阵
            iou_matrix = np.zeros((len(self.trackers), len(detections)))
            for ti, t in enumerate(self.trackers):
                for di, d in enumerate(detections):
                    iou_matrix[ti, di] = iou(t.get_state(), d[2])

            # 贪心匹配（从最高 IoU 开始）
            for _ in range(min(len(self.trackers), len(detections))):
                ti, di = np.unravel_index(iou_matrix.argmax(), iou_matrix.shape)
                if iou_matrix[ti, di] < self.iou_threshold:
                    break
                assignments.append((ti, di))
                matched_tracks.add(ti)
                matched_dets.add(di)
                iou_matrix[ti, :] = 0
                iou_matrix[:, di] = 0

        # ---- 更新匹配的追踪 ----
        for ti, di in assignments:
            label, conf, bbox = detections[di]
            # 同一个目标类别才更新（不同类别不匹配）
            if self.trackers[ti].label == label:
                self.trackers[ti].update(bbox, conf)

        # ---- 未匹配的追踪：标记丢失 ----
        for ti, t in enumerate(self.trackers):
            if ti not in matched_tracks:
                t.no_loss += 1

        # ---- 删除丢失过久的追踪 ----
        self.trackers = [t for t in self.trackers if t.no_loss < self.max_lost]

        # ---- 未匹配的检测：创建新追踪 ----
        for di, d in enumerate(detections):
            if di not in matched_dets:
                label, conf, bbox = d
                self.trackers.append(KalmanBoxTracker(bbox, label, conf))

        # ---- 输出已确认的追踪结果 ----
        results = []
        for t in self.trackers:
            if t.confirmed:
                results.append((t.id, t.label, t.conf, t.get_state()))

        return results

    def reset(self):
        """重置所有追踪"""
        self.trackers = []
        KalmanBoxTracker.count = 0
