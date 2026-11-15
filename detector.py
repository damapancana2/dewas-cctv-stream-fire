import os
import sys
import time
import datetime
import cv2
import numpy as np
import torch

LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
YOLO_CORE_DIR = os.path.join(LOCAL_DIR, 'yolo_core')
if YOLO_CORE_DIR not in sys.path:
    sys.path.insert(0, YOLO_CORE_DIR)

from utils.general import non_max_suppression, scale_coords
from utils.datasets import letterbox


class FireDetector:
    """
    YOLOv5 Fire and Smoke Detector for DEWaS CCTV Stream.
    Uses best.pt weights trained for 'fire' and 'smoke' classes.
    """
    _instance = None

    def __init__(self, weights_path: str = None, device: str = 'cpu'):
        if weights_path is None:
            weights_path = os.path.join(LOCAL_DIR, 'weights', 'best.pt')

        self.weights_path = weights_path
        self.device = torch.device(device)
        self.model = None
        self.class_names = ['fire', 'smoke']
        self.img_size = 640
        self.last_inference_ms = 0.0

        self._load_model()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_model(self):
        if not os.path.exists(self.weights_path):
            print(f"[FireDetector] Warning: Weights file not found at {self.weights_path}")
            return

        print(f"[FireDetector] Loading YOLOv5 weights from: {self.weights_path}")
        t0 = time.time()
        ckpt = torch.load(self.weights_path, map_location=self.device, weights_only=False)
        self.model = ckpt['model'].float().fuse().eval()

        names = self.model.module.names if hasattr(self.model, 'module') else self.model.names
        if isinstance(names, (list, tuple)):
            self.class_names = list(names)
        elif isinstance(names, dict):
            self.class_names = [names[i] for i in sorted(names.keys())]

        print(f"[FireDetector] Model loaded in {(time.time() - t0):.2f}s! Classes: {self.class_names}")

    def detect(self, img0: np.ndarray, conf_thres: float = 0.25, iou_thres: float = 0.45):
        """
        Runs detection on a BGR image frame (numpy ndarray).
        Returns:
            detections: list of dicts with keys ['class', 'confidence', 'box': [x1, y1, x2, y2]]
            summary: dict with metadata (has_fire, has_smoke, max_fire_conf, max_smoke_conf, inference_ms)
        """
        if self.model is None or img0 is None:
            return [], {
                'has_fire': False,
                'has_smoke': False,
                'max_fire_conf': 0.0,
                'max_smoke_conf': 0.0,
                'inference_ms': 0.0,
                'detections_count': 0
            }

        t_start = time.time()

        # Letterbox and preprocess
        img = letterbox(img0, new_shape=self.img_size, auto=True)[0]
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, CHW
        img = np.ascontiguousarray(img)
        img_tensor = torch.from_numpy(img).to(self.device).float() / 255.0
        if img_tensor.ndimension() == 3:
            img_tensor = img_tensor.unsqueeze(0)

        # Inference
        with torch.no_grad():
            pred = self.model(img_tensor)[0]
            pred = non_max_suppression(pred, conf_thres, iou_thres, classes=None, agnostic=False)

        inference_ms = (time.time() - t_start) * 1000.0
        self.last_inference_ms = inference_ms

        detections = []
        has_fire = False
        has_smoke = False
        max_fire_conf = 0.0
        max_smoke_conf = 0.0

        if pred is not None:
            for det in pred:
                if det is not None and len(det):
                    det[:, :4] = scale_coords(img_tensor.shape[2:], det[:, :4], img0.shape).round()
                    for *xyxy, conf, cls_idx in reversed(det):
                        c = int(cls_idx)
                        conf_val = float(conf)
                        label = self.class_names[c] if c < len(self.class_names) else f'class_{c}'
                        box = [int(coord) for coord in xyxy]

                        if label == 'fire':
                            has_fire = True
                            if conf_val > max_fire_conf:
                                max_fire_conf = conf_val
                        elif label == 'smoke':
                            has_smoke = True
                            if conf_val > max_smoke_conf:
                                max_smoke_conf = conf_val

                        detections.append({
                            'class': label,
                            'confidence': round(conf_val, 4),
                            'box': box
                        })

        summary = {
            'has_fire': has_fire,
            'has_smoke': has_smoke,
            'max_fire_conf': round(max_fire_conf, 4),
            'max_smoke_conf': round(max_smoke_conf, 4),
            'inference_ms': round(inference_ms, 1),
            'detections_count': len(detections),
            'timestamp': datetime.datetime.now().isoformat()
        }

        return detections, summary

    def annotate(self, img: np.ndarray, detections: list, summary: dict = None, camera_id: str = "CAM", fps: float = None):
        """
        Draws high-visibility bounding boxes and a sleek modern HUD overlay on the image.
        """
        if img is None:
            return img

        annotated = img.copy()
        h, w = annotated.shape[:2]

        if detections is None:
            detections = []

        # 1. Bounding Boxes
        for det in detections:
            cls_name = det.get('class', 'unknown')
            conf = det.get('confidence', 0.0)
            box = det.get('box', [0, 0, 0, 0])
            x1, y1, x2, y2 = box

            if cls_name == 'fire':
                box_color = (0, 50, 255)  # Bright Red
                badge_bg = (0, 0, 200)
                badge_text = f"FIRE {conf*100:.0f}%"
            elif cls_name == 'smoke':
                box_color = (0, 180, 250)  # Amber / Gold
                badge_bg = (0, 140, 220)
                badge_text = f"SMOKE {conf*100:.0f}%"
            else:
                box_color = (255, 200, 0)
                badge_bg = (200, 150, 0)
                badge_text = f"{cls_name.upper()} {conf*100:.0f}%"

            thickness = max(2, int(min(w, h) / 360))
            cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, thickness)

            # High-tech corner accents
            c_len = min(20, (x2 - x1) // 4, (y2 - y1) // 4)
            if c_len > 4:
                accent = (255, 255, 255)
                cv2.line(annotated, (x1, y1), (x1 + c_len, y1), accent, thickness + 1)
                cv2.line(annotated, (x1, y1), (x1, y1 + c_len), accent, thickness + 1)
                cv2.line(annotated, (x2, y1), (x2 - c_len, y1), accent, thickness + 1)
                cv2.line(annotated, (x2, y1), (x2, y1 + c_len), accent, thickness + 1)
                cv2.line(annotated, (x1, y2), (x1 + c_len, y2), accent, thickness + 1)
                cv2.line(annotated, (x1, y2), (x1, y2 - c_len), accent, thickness + 1)
                cv2.line(annotated, (x2, y2), (x2 - c_len, y2), accent, thickness + 1)
                cv2.line(annotated, (x2, y2), (x2, y2 - c_len), accent, thickness + 1)

            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = max(0.45, min(w, h) / 1400.0)
            text_thick = max(1, int(font_scale * 2))
            (tw, th), baseline = cv2.getTextSize(badge_text, font, font_scale, text_thick)

            badge_y1 = max(0, y1 - th - 8)
            badge_y2 = badge_y1 + th + 8
            badge_x2 = min(w, x1 + tw + 12)

            cv2.rectangle(annotated, (x1, badge_y1), (badge_x2, badge_y2), badge_bg, -1)
            cv2.rectangle(annotated, (x1, badge_y1), (badge_x2, badge_y2), (255, 255, 255), 1)
            cv2.putText(annotated, badge_text, (x1 + 6, badge_y2 - baseline - 2),
                        font, font_scale, (255, 255, 255), text_thick, cv2.LINE_AA)

        # 2. Modern Glassmorphic Top HUD Banner
        hud_h = max(38, int(h * 0.055))
        overlay = annotated.copy()
        cv2.rectangle(overlay, (0, 0), (w, hud_h), (18, 20, 24), -1)
        cv2.addWeighted(overlay, 0.80, annotated, 0.20, 0, annotated)

        border_color = (0, 50, 255) if (summary and summary.get('has_fire')) else (
            (0, 180, 250) if (summary and summary.get('has_smoke')) else (80, 220, 100)
        )
        cv2.line(annotated, (0, hud_h), (w, hud_h), border_color, 2)

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.45, min(w, h) / 1300.0)
        text_thick = 1 if font_scale < 0.65 else 2

        if summary and summary.get('has_fire'):
            status_text = f" [!] FIRE ALERT: KEBAKARAN TERDETEKSI ({summary.get('max_fire_conf', 0)*100:.0f}%)"
            status_color = (0, 50, 255)
        elif summary and summary.get('has_smoke'):
            status_text = f" [*] PERINGATAN: ASAP TERDETEKSI ({summary.get('max_smoke_conf', 0)*100:.0f}%)"
            status_color = (0, 190, 250)
        else:
            status_text = " [OK] DEWaS CCTV FIRE AI : NORMAL"
            status_color = (80, 220, 100)

        cv2.putText(annotated, status_text, (12, int(hud_h * 0.68)),
                    font, font_scale, status_color, text_thick, cv2.LINE_AA)

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        info_parts = [f"{camera_id.upper()}", now_str]
        if fps is not None and fps > 0:
            info_parts.append(f"{fps:.1f} FPS")
        if summary and summary.get('inference_ms'):
            info_parts.append(f"{summary['inference_ms']:.0f}ms")

        right_text = " | ".join(info_parts)
        (rt_w, _), _ = cv2.getTextSize(right_text, font, font_scale * 0.85, 1)
        cv2.putText(annotated, right_text, (max(12, w - rt_w - 14), int(hud_h * 0.68)),
                    font, font_scale * 0.85, (220, 220, 220), 1, cv2.LINE_AA)

        return annotated
