import os
import sys
import time
import datetime
import threading
import cv2
import numpy as np

# Ensure yolo_core is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YOLO_CORE_DIR = os.path.join(BASE_DIR, 'yolo_core')
if YOLO_CORE_DIR not in sys.path:
    sys.path.insert(0, YOLO_CORE_DIR)

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSlider, QCheckBox,
    QGroupBox, QTextEdit, QProgressBar
)

from config import Config
from detector import FireDetector


class RTSPStreamReader:
    """
    Dedicated background thread that drains the RTSP buffer at full camera FPS.
    Prevents buffer bloat, buffer overrun, and connection dropouts caused by AI processing delays.
    """
    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.latest_frame = None
        self.has_frame = False
        self.running = True
        self.is_connected = False
        self.error_msg = ""

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000|reorder_queue_size;0"
        )
        self.cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG if isinstance(source, str) else cv2.CAP_ANY)
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.is_connected = True
        else:
            self.error_msg = "Gagal membuka koneksi RTSP. Periksa IP, Port 554, dan kredensial."

        self.thread = threading.Thread(target=self._grab_loop, daemon=True)
        self.thread.start()

    def _grab_loop(self):
        consecutive_failures = 0
        while self.running:
            if not self.cap or not self.cap.isOpened():
                time.sleep(0.5)
                continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 50:
                    self.is_connected = False
                    self.error_msg = "Koneksi stream terputus dari kamera."
                time.sleep(0.02)
                continue

            consecutive_failures = 0
            self.is_connected = True
            with self.lock:
                self.latest_frame = frame
                self.has_frame = True

            # Minimal sleep to let OS scheduler yield
            time.sleep(0.005)

    def read(self):
        with self.lock:
            if not self.has_frame or self.latest_frame is None:
                return False, None
            return True, self.latest_frame.copy()

    def release(self):
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap and self.cap.isOpened():
            self.cap.release()


class VideoCaptureThread(QThread):
    frame_ready = pyqtSignal(QImage, dict)
    connection_status = pyqtSignal(bool, str)

    def __init__(self, source_url: str, conf_threshold: float = 0.25, show_ai: bool = True):
        super().__init__()
        self.source_url = source_url.strip()
        self.conf_threshold = conf_threshold
        self.show_ai = show_ai
        self.running = True
        self.reader = None

    def run(self):
        detector = FireDetector.get_instance()

        if self.source_url.isdigit():
            source = int(self.source_url)
        else:
            source = self.source_url

        self.connection_status.emit(False, f"Menghubungkan ke: {self.source_url}...")
        self.reader = RTSPStreamReader(source)

        # Wait briefly for first frame
        for _ in range(30):
            if self.reader.has_frame:
                break
            time.sleep(0.1)

        if not self.reader.is_connected and not self.reader.has_frame:
            msg = self.reader.error_msg or "Gagal membaca frame pertama dari CCTV."
            self.connection_status.emit(False, msg)
            self.reader.release()
            return

        self.connection_status.emit(True, f"Stream aktif dan stabil: {self.source_url}")

        frame_idx = 0
        cached_detections = []
        cached_summary = {
            'has_fire': False,
            'has_smoke': False,
            'max_fire_conf': 0.0,
            'max_smoke_conf': 0.0,
            'inference_ms': 0.0,
            'timestamp': ''
        }
        fps_timer = time.time()
        fps_counter = 0
        current_fps = 0.0

        while self.running:
            ret, frame = self.reader.read()
            if not ret or frame is None:
                if not self.reader.is_connected:
                    self.connection_status.emit(False, "Koneksi terputus dari kamera. Menunggu stream...")
                time.sleep(0.04)
                continue

            frame_idx += 1
            fps_counter += 1

            if time.time() - fps_timer >= 1.0:
                current_fps = fps_counter / (time.time() - fps_timer)
                fps_counter = 0
                fps_timer = time.time()

            # Run YOLO Detection periodically (e.g. every 3 frames or when cache empty)
            if self.show_ai and (frame_idx % Config.AI_SKIP_FRAMES == 0 or not cached_detections):
                try:
                    cached_detections, cached_summary = detector.detect(
                        frame, conf_thres=self.conf_threshold
                    )
                except Exception as e:
                    print(f"[DesktopStream] Detection error: {e}")

            if self.show_ai:
                display_frame = detector.annotate(
                    frame, cached_detections, cached_summary,
                    camera_id="CCTV-LIVE", fps=current_fps
                )
            else:
                display_frame = frame

            # Convert BGR -> RGB -> QImage
            rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w
            q_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()

            cached_summary['fps'] = current_fps
            cached_summary['raw_frame'] = display_frame
            self.frame_ready.emit(q_img, cached_summary)

            # Smooth target rendering (~25-30 FPS)
            time.sleep(0.03)

        if self.reader:
            self.reader.release()
        self.connection_status.emit(False, "Stream dihentikan.")

    def stop(self):
        self.running = False
        if self.reader:
            self.reader.release()
        self.wait(1500)


class DesktopStreamWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DEWaS CCTV - Fire & Smoke AI Live Desktop Monitor")
        self.resize(1340, 840)
        self.video_thread = None
        self.latest_raw_frame = None

        self._init_ui()
        self._apply_dark_theme()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        # Left / Center Area: Video Monitor
        video_container = QWidget()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(8)

        # Header bar
        header_bar = QHBoxLayout()
        title_label = QLabel("DEWaS CCTV FIRE AI MONITOR")
        title_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #38bdf8; letter-spacing: 1px;")

        self.status_badge = QLabel("READY")
        self.status_badge.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; font-weight: bold; "
            "padding: 5px 14px; border-radius: 6px; border: 1px solid #334155;"
        )
        header_bar.addWidget(title_label)
        header_bar.addStretch()
        header_bar.addWidget(self.status_badge)
        video_layout.addLayout(header_bar)

        # Video Display Label
        self.video_label = QLabel("Memuat stream CCTV... Klik 'Hubungkan' untuk memulai.")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #090d16; border: 2px solid #1e293b; "
            "border-radius: 10px; color: #64748b; font-size: 15px;"
        )
        self.video_label.setMinimumSize(800, 500)
        video_layout.addWidget(self.video_label, 1)

        # Bottom Bar below video
        bottom_bar = QHBoxLayout()
        self.fps_label = QLabel("FPS: 0.0")
        self.fps_label.setStyleSheet("color: #94a3b8; font-weight: bold;")
        self.latency_label = QLabel("Inference: 0ms")
        self.latency_label.setStyleSheet("color: #94a3b8; font-weight: bold;")
        self.res_label = QLabel("Resolusi: -")
        self.res_label.setStyleSheet("color: #94a3b8;")

        bottom_bar.addWidget(self.fps_label)
        bottom_bar.addSpacing(20)
        bottom_bar.addWidget(self.latency_label)
        bottom_bar.addSpacing(20)
        bottom_bar.addWidget(self.res_label)
        bottom_bar.addStretch()

        self.snap_btn = QPushButton("📸 Ambil Snapshot")
        self.snap_btn.clicked.connect(self.save_snapshot)
        self.snap_btn.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; padding: 6px 16px; border-radius: 6px;"
        )
        bottom_bar.addWidget(self.snap_btn)
        video_layout.addLayout(bottom_bar)

        main_layout.addWidget(video_container, 3)

        # Right Area: Control & Telemetry Sidebar
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(12)
        sidebar.setMaximumWidth(420)

        # 1. Connection Card
        conn_group = QGroupBox("KONEKSI SUMBER RTSP")
        conn_layout = QVBoxLayout(conn_group)
        conn_layout.setSpacing(8)

        conn_layout.addWidget(QLabel("Preset Sumber Kamera:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "Substream 102 (Ringan & Stabil - Rekomendasi)",
            "Mainstream 101 (Resolusi Tinggi 1080p/4K)",
            "CCTV XM/NetIP (192.168.1.64:554/live/ch0)",
            "Webcam Laptop Default (Device 0)",
            "Kustom RTSP URL"
        ])
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        conn_layout.addWidget(self.preset_combo)

        conn_layout.addWidget(QLabel("RTSP Stream URL:"))
        env_rtsp = Config.get_camera_rtsp('cam-fire-01') or Config.get_camera_rtsp('DEFAULT_RTSP')
        default_rtsp = env_rtsp if env_rtsp else "rtsp://admin:admin123@192.168.10.64:554/Streaming/channels/101"
        self.url_input = QLineEdit(default_rtsp)
        self.url_input.setPlaceholderText("rtsp://user:pass@ip:port/path atau 0")
        conn_layout.addWidget(self.url_input)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("▶ Hubungkan")
        self.connect_btn.setStyleSheet(
            "background-color: #10b981; color: white; font-weight: bold; padding: 9px; border-radius: 6px;"
        )
        self.connect_btn.clicked.connect(self.toggle_stream)
        btn_row.addWidget(self.connect_btn)

        self.stop_btn = QPushButton("⏹ Berhenti")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "background-color: #ef4444; color: white; font-weight: bold; padding: 9px; border-radius: 6px;"
        )
        self.stop_btn.clicked.connect(self.stop_stream)
        btn_row.addWidget(self.stop_btn)
        conn_layout.addLayout(btn_row)

        sidebar_layout.addWidget(conn_group)

        # 2. AI Telemetry Card
        telemetry_group = QGroupBox("TELEMETRI DETEKSI AI")
        telemetry_layout = QVBoxLayout(telemetry_group)
        telemetry_layout.setSpacing(8)

        fire_row = QHBoxLayout()
        fire_row.addWidget(QLabel("🔥 Indikator Api (Fire):"))
        self.fire_val_label = QLabel("0.0%")
        self.fire_val_label.setStyleSheet("color: #ef4444; font-weight: bold;")
        fire_row.addWidget(self.fire_val_label)
        telemetry_layout.addLayout(fire_row)

        self.fire_progress = QProgressBar()
        self.fire_progress.setRange(0, 100)
        self.fire_progress.setValue(0)
        self.fire_progress.setStyleSheet(
            "QProgressBar { background-color: #1e293b; border-radius: 4px; height: 12px; text-align: center; color: transparent; }"
            "QProgressBar::chunk { background-color: #ef4444; border-radius: 4px; }"
        )
        telemetry_layout.addWidget(self.fire_progress)

        smoke_row = QHBoxLayout()
        smoke_row.addWidget(QLabel("💨 Indikator Asap (Smoke):"))
        self.smoke_val_label = QLabel("0.0%")
        self.smoke_val_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
        smoke_row.addWidget(self.smoke_val_label)
        telemetry_layout.addLayout(smoke_row)

        self.smoke_progress = QProgressBar()
        self.smoke_progress.setRange(0, 100)
        self.smoke_progress.setValue(0)
        self.smoke_progress.setStyleSheet(
            "QProgressBar { background-color: #1e293b; border-radius: 4px; height: 12px; text-align: center; color: transparent; }"
            "QProgressBar::chunk { background-color: #f59e0b; border-radius: 4px; }"
        )
        telemetry_layout.addWidget(self.smoke_progress)

        sidebar_layout.addWidget(telemetry_group)

        # 3. Settings Card
        settings_group = QGroupBox("PENGATURAN DETEKSI")
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(8)

        self.ai_checkbox = QCheckBox("Tampilkan Bounding Box & HUD AI")
        self.ai_checkbox.setChecked(True)
        settings_layout.addWidget(self.ai_checkbox)

        conf_header = QHBoxLayout()
        conf_header.addWidget(QLabel("Ambang Batas Conf:"))
        self.conf_slider_label = QLabel(f"{int(Config.AI_CONF_THRESHOLD * 100)}%")
        self.conf_slider_label.setStyleSheet("color: #38bdf8; font-weight: bold;")
        conf_header.addWidget(self.conf_slider_label)
        settings_layout.addLayout(conf_header)

        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(10, 90)
        self.conf_slider.setValue(int(Config.AI_CONF_THRESHOLD * 100))
        self.conf_slider.valueChanged.connect(self._on_conf_changed)
        settings_layout.addWidget(self.conf_slider)

        sidebar_layout.addWidget(settings_group)

        # 4. Activity Log Card
        log_group = QGroupBox("LOG AKTIVITAS")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "background-color: #090d16; color: #94a3b8; font-family: Consolas, monospace; "
            "font-size: 11px; border: 1px solid #1e293b; border-radius: 6px;"
        )
        log_layout.addWidget(self.log_text)
        sidebar_layout.addWidget(log_group, 1)

        main_layout.addWidget(sidebar, 1)

        self._log("DEWaS CCTV Fire Monitor Desktop siap (Async Buffer Mode).")
        self._log("Tips: Gunakan Substream (channels/102) untuk performa 100% mulus.")

    def _on_preset_changed(self, idx: int):
        env_url = Config.get_camera_rtsp('cam-fire-01') or "rtsp://admin:admin123@192.168.10.64:554/Streaming/channels/101"
        url_101 = env_url.replace('/102', '/101')
        url_102 = env_url.replace('/101', '/102')
        presets = [
            url_101,
            url_102,
            "rtsp://admin:Dewas2026@192.168.1.64:554/live/ch0",
            "0",
            ""
        ]
        if idx < len(presets) and presets[idx]:
            self.url_input.setText(presets[idx])

    def _on_conf_changed(self, val: int):
        self.conf_slider_label.setText(f"{val}%")
        if self.video_thread:
            self.video_thread.conf_threshold = val / 100.0

    def toggle_stream(self):
        if self.video_thread and self.video_thread.isRunning():
            self.stop_stream()
        else:
            self.start_stream()

    def start_stream(self):
        url = self.url_input.text().strip()
        if not url:
            self._log("[PERINGATAN] Masukkan URL RTSP.")
            return

        conf_val = self.conf_slider.value() / 100.0
        show_ai = self.ai_checkbox.isChecked()

        self.video_thread = VideoCaptureThread(url, conf_threshold=conf_val, show_ai=show_ai)
        self.video_thread.frame_ready.connect(self._on_frame_ready)
        self.video_thread.connection_status.connect(self._on_connection_status)
        self.video_thread.start()

        self.connect_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_badge.setText("CONNECTING")
        self.status_badge.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; padding: 5px 14px; border-radius: 6px;"
        )

    def stop_stream(self):
        if self.video_thread:
            self.video_thread.stop()
            self.video_thread = None

        self.connect_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_badge.setText("STOPPED")
        self.status_badge.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; font-weight: bold; padding: 5px 14px; border-radius: 6px;"
        )
        self.fire_progress.setValue(0)
        self.smoke_progress.setValue(0)
        self.fire_val_label.setText("0.0%")
        self.smoke_val_label.setText("0.0%")
        self.fps_label.setText("FPS: 0.0")
        self.latency_label.setText("Inference: 0ms")

    def _on_frame_ready(self, q_img: QImage, summary: dict):
        self.latest_raw_frame = summary.get('raw_frame')

        scaled_pixmap = QPixmap.fromImage(q_img).scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.video_label.setPixmap(scaled_pixmap)

        fps = summary.get('fps', 0.0)
        ms = summary.get('inference_ms', 0.0)
        has_fire = summary.get('has_fire', False)
        has_smoke = summary.get('has_smoke', False)
        max_fire = summary.get('max_fire_conf', 0.0)
        max_smoke = summary.get('max_smoke_conf', 0.0)

        self.fps_label.setText(f"FPS: {fps:.1f}")
        self.latency_label.setText(f"Inference: {ms:.0f}ms")
        self.res_label.setText(f"Resolusi: {q_img.width()}x{q_img.height()}")

        self.fire_progress.setValue(int(max_fire * 100))
        self.fire_val_label.setText(f"{max_fire*100:.1f}%")

        self.smoke_progress.setValue(int(max_smoke * 100))
        self.smoke_val_label.setText(f"{max_smoke*100:.1f}%")

        if has_fire:
            self.status_badge.setText("🚨 CRITICAL: FIRE")
            self.status_badge.setStyleSheet(
                "background-color: #ef4444; color: white; font-weight: bold; padding: 5px 14px; border-radius: 6px;"
            )
        elif has_smoke:
            self.status_badge.setText("⚠️ WARNING: SMOKE")
            self.status_badge.setStyleSheet(
                "background-color: #f59e0b; color: black; font-weight: bold; padding: 5px 14px; border-radius: 6px;"
            )
        else:
            self.status_badge.setText("● NORMAL")
            self.status_badge.setStyleSheet(
                "background-color: #10b981; color: white; font-weight: bold; padding: 5px 14px; border-radius: 6px;"
            )

    def _on_connection_status(self, is_connected: bool, msg: str):
        self._log(msg)
        if not is_connected and not self.stop_btn.isEnabled():
            self.connect_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def save_snapshot(self):
        if self.latest_raw_frame is None:
            self._log("[ERROR] Tidak ada frame video untuk disimpan.")
            return

        snapshot_dir = os.path.join(BASE_DIR, "snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        filename = f"snapshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        target_path = os.path.join(snapshot_dir, filename)

        try:
            cv2.imwrite(target_path, self.latest_raw_frame)
            self._log(f"[SUKSES] Snapshot tersimpan: {filename}")
        except Exception as e:
            self._log(f"[ERROR] Gagal menyimpan snapshot: {e}")

    def _log(self, text: str):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{now_str}] {text}")

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0b0f19; }
            QWidget { color: #e2e8f0; font-family: 'Segoe UI', Arial, sans-serif; }
            QGroupBox {
                background-color: #111827; border: 1px solid #1f2937;
                border-radius: 8px; margin-top: 14px; padding-top: 10px;
                font-weight: bold; font-size: 11px; color: #93c5fd;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QLineEdit, QComboBox {
                background-color: #090d16; border: 1px solid #374151;
                border-radius: 6px; padding: 6px 10px; color: #f8fafc; font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #38bdf8; }
            QComboBox::drop-down { border: none; }
            QCheckBox { spacing: 8px; font-size: 12px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px;
                border: 1px solid #4b5563; background-color: #1f2937;
            }
            QCheckBox::indicator:checked { background-color: #10b981; border: 1px solid #10b981; }
            QSlider::groove:horizontal { height: 6px; background: #1f2937; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #38bdf8; border-radius: 3px; }
            QSlider::handle:horizontal {
                background: #f8fafc; border: 1px solid #94a3b8;
                width: 14px; margin-top: -4px; margin-bottom: -4px; border-radius: 7px;
            }
        """)

    def closeEvent(self, event):
        self.stop_stream()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = DesktopStreamWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
