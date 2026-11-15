import os
import time
import glob
import threading
import subprocess
import cv2
import numpy as np
from config import Config
from detector import FireDetector

STREAM_WIDTH = 1280
STREAM_HEIGHT = 720

def get_ffmpeg_bin():
    local_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(local_bin):
        return local_bin
    import shutil
    which = shutil.which("ffmpeg")
    if which:
        return which
    return "ffmpeg"


class RTSPStreamReader:
    """
    Dedicated background thread that drains the RTSP buffer at full camera FPS.
    Prevents buffer bloat, buffer overrun, and connection dropouts caused by AI processing delays.
    (Adopted directly from desktop_stream.py for rock-solid stability)
    """
    def __init__(self, source_url: str):
        self.source_url = source_url.strip() if source_url else ""
        self.lock = threading.Lock()
        self.latest_frame = None
        self.has_frame = False
        self.running = True
        self.is_connected = False
        self.cap = None

        self.thread = threading.Thread(target=self._grab_loop, daemon=True)
        self.thread.start()

    def _open_capture(self):
        if not self.source_url:
            return None
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000|reorder_queue_size;0|stimeout;5000000"
        )
        try:
            cap = cv2.VideoCapture(self.source_url, cv2.CAP_FFMPEG)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
        except Exception as e:
            print(f"[RTSPStreamReader] Error opening RTSP: {e}")
        return None

    def _grab_loop(self):
        consecutive_failures = 0
        while self.running:
            if not self.source_url:
                time.sleep(1.0)
                continue

            if self.cap is None or not self.cap.isOpened():
                self.cap = self._open_capture()
                if self.cap is None or not self.cap.isOpened():
                    self.is_connected = False
                    time.sleep(2.0)
                    continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    self.is_connected = False
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    time.sleep(1.0)
                else:
                    time.sleep(0.02)
                continue

            consecutive_failures = 0
            self.is_connected = True
            with self.lock:
                self.latest_frame = frame
                self.has_frame = True

            time.sleep(0.005)

        if self.cap and self.cap.isOpened():
            try:
                self.cap.release()
            except Exception:
                pass

    def read(self):
        with self.lock:
            if not self.has_frame or self.latest_frame is None:
                return False, None
            return True, self.latest_frame.copy()

    def release(self):
        self.running = False


class AIStreamWorker:
    """
    Worker that ingests camera frames, runs YOLOv5 fire/smoke detection,
    and produces BOTH:
      1. Low-Latency HLS (index.m3u8 + .ts segments) strictly sized to 1280x720 (NEVER green scanlines)
      2. Instant Sub-second MJPEG stream (/live/<camera_id>/mjpeg)
    """
    _workers = {}
    _lock = threading.Lock()

    def __init__(self, camera_id: str, rtsp_url: str):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.output_dir = os.path.join(Config.HLS_DIR, camera_id)
        self.manifest_path = os.path.join(self.output_dir, "index.m3u8")
        self.snapshot_path = os.path.join(Config.HLS_DIR, f"snapshot_{camera_id}.jpg")

        self.is_running = False
        self.stop_requested = False
        self.last_accessed = time.time()
        self.thread = None
        self.ffmpeg_process = None
        self.reader = None

        self.current_fps = 0.0
        self.latest_summary = {
            'has_fire': False,
            'has_smoke': False,
            'max_fire_conf': 0.0,
            'max_smoke_conf': 0.0,
            'inference_ms': 0.0,
            'detections_count': 0,
            'timestamp': ''
        }
        self.latest_detections = []
        self.latest_frame_annotated = None

    @classmethod
    def get_or_create(cls, camera_id: str, custom_rtsp: str = None):
        with cls._lock:
            key = camera_id.strip()
            if key not in cls._workers:
                rtsp = custom_rtsp or Config.get_camera_rtsp(key)
                cls._workers[key] = cls(key, rtsp)
            cls._workers[key].touch()
            return cls._workers[key]

    @classmethod
    def get_all_status(cls):
        with cls._lock:
            results = {}
            for cid, worker in cls._workers.items():
                results[cid] = {
                    'camera_id': cid,
                    'is_running': worker.is_running,
                    'last_accessed': worker.last_accessed,
                    'summary': worker.latest_summary,
                    'detections': worker.latest_detections,
                    'fps': worker.current_fps
                }
            return results

    def touch(self):
        self.last_accessed = time.time()

    def _clean_dvr_storage(self):
        """
        DVR Circular Buffer Janitor:
        Deletes older .ts segments not present in the current playlist,
        keeping disk storage strictly under ~3MB per camera.
        """
        if not os.path.exists(self.output_dir):
            return

        active_segments = set()
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.endswith(".ts"):
                            active_segments.add(line)
            except Exception:
                pass

        now = time.time()
        for f in glob.glob(os.path.join(self.output_dir, "*.ts*")):
            fname = os.path.basename(f)
            if fname not in active_segments:
                try:
                    if (now - os.path.getmtime(f)) > 8:
                        os.remove(f)
                except Exception:
                    pass

    def start(self):
        if self.is_running:
            return

        os.makedirs(self.output_dir, exist_ok=True)
        # Purge any stale files on startup
        for f in glob.glob(os.path.join(self.output_dir, "*.ts*")):
            try:
                os.remove(f)
            except Exception:
                pass

        self.stop_requested = False
        self.is_running = True
        self.reader = RTSPStreamReader(self.rtsp_url)
        self.thread = threading.Thread(target=self._run_loop, name=f"AI-Stream-{self.camera_id}", daemon=True)
        self.thread.start()
        print(f"[AIStreamWorker] Started AI streaming for {self.camera_id} with Zero-Lag Reader + Guaranteed Resolution ({STREAM_WIDTH}x{STREAM_HEIGHT}).")

    def stop(self):
        print(f"[AIStreamWorker] Stopping AI streaming for {self.camera_id}")
        self.stop_requested = True
        self.is_running = False

        if self.reader:
            self.reader.release()
            self.reader = None

        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=2)
            except Exception:
                try:
                    self.ffmpeg_process.kill()
                except Exception:
                    pass
        self.ffmpeg_process = None

    def _generate_fallback_frame(self, w=STREAM_WIDTH, h=STREAM_HEIGHT):
        snap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_snap.jpg")
        if os.path.exists(snap_path):
            img = cv2.imread(snap_path)
            if img is not None:
                if img.shape[1] != w or img.shape[0] != h:
                    img = cv2.resize(img, (w, h))
                return img

        frame = np.full((h, w, 3), (25, 28, 35), dtype=np.uint8)
        for y in range(0, h, 60):
            cv2.line(frame, (0, y), (w, y), (35, 40, 50), 1)
        for x in range(0, w, 60):
            cv2.line(frame, (x, 0), (x, h), (35, 40, 50), 1)

        cv2.putText(frame, f"DEWaS CCTV SIMULATION FEED: {self.camera_id.upper()}",
                    (50, h // 2 - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 220, 240), 2, cv2.LINE_AA)
        cv2.putText(frame, "Waiting for live RTSP stream...",
                    (50, h // 2 + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 140, 160), 1, cv2.LINE_AA)
        return frame

    def _run_loop(self):
        detector = FireDetector.get_instance()
        ffmpeg_bin = get_ffmpeg_bin()

        target_fps = Config.AI_STREAM_FPS or 12
        skip_frames = Config.AI_SKIP_FRAMES or 3
        conf_thres = Config.AI_CONF_THRESHOLD
        iou_thres = Config.AI_IOU_THRESHOLD

        segment_pattern = os.path.join(self.output_dir, "segment_%03d.ts")
        ffmpeg_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{STREAM_WIDTH}x{STREAM_HEIGHT}",
            "-pix_fmt", "bgr24",
            "-r", str(target_fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "25",
            "-g", str(target_fps * 2),
            "-an",
            "-f", "hls",
            "-hls_time", str(Config.HLS_SEGMENT_DURATION),
            "-hls_list_size", str(Config.HLS_LIST_SIZE),
            "-hls_flags", "delete_segments+append_list+omit_endlist+temp_file",
            "-hls_segment_filename", segment_pattern,
            self.manifest_path
        ]

        try:
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"[AIStreamWorker] Failed to launch FFmpeg: {e}")
            self.is_running = False
            return

        frame_interval = 1.0 / target_fps
        frame_idx = 0
        cached_detections = []
        cached_summary = self.latest_summary

        fps_timer = time.time()
        fps_counter = 0
        janitor_timer = time.time()

        while not self.stop_requested:
            loop_start = time.time()

            has_real_frame = False
            frame = None
            if self.reader:
                has_real_frame, frame = self.reader.read()

            if not has_real_frame or frame is None:
                frame = self._generate_fallback_frame(STREAM_WIDTH, STREAM_HEIGHT)

            frame_idx += 1

            # AI detection periodically (every skip_frames or when cache is empty)
            if frame_idx % skip_frames == 0 or not cached_detections:
                try:
                    cached_detections, cached_summary = detector.detect(
                        frame, conf_thres=conf_thres, iou_thres=iou_thres
                    )
                    self.latest_detections = cached_detections
                    self.latest_summary = cached_summary
                except Exception as e:
                    print(f"[AIStreamWorker] Detection error: {e}")

            # Annotate with bounding boxes and HUD
            annotated = detector.annotate(
                frame,
                cached_detections,
                cached_summary,
                camera_id=self.camera_id,
                fps=self.current_fps
            )

            # CRITICAL: Always resize to STREAM_WIDTH x STREAM_HEIGHT before writing to FFmpeg
            # This GUARANTEES that rawvideo stride matches 100% and NEVER produces green lines!
            if annotated.shape[1] != STREAM_WIDTH or annotated.shape[0] != STREAM_HEIGHT:
                annotated = cv2.resize(annotated, (STREAM_WIDTH, STREAM_HEIGHT), interpolation=cv2.INTER_AREA)

            self.latest_frame_annotated = annotated

            # Snapshot save every 2 seconds
            if frame_idx % (target_fps * 2) == 0:
                try:
                    cv2.imwrite(self.snapshot_path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
                except Exception:
                    pass

            # Write to FFmpeg stdin
            try:
                if self.ffmpeg_process and self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.write(annotated.tobytes())
            except (BrokenPipeError, OSError):
                print(f"[AIStreamWorker] FFmpeg pipe broken for {self.camera_id}")
                break

            fps_counter += 1
            if time.time() - fps_timer >= 1.0:
                self.current_fps = fps_counter / (time.time() - fps_timer)
                fps_counter = 0
                fps_timer = time.time()

            # DVR Storage Janitor: runs every 8 seconds to enforce circular buffer
            if time.time() - janitor_timer >= 8.0:
                self._clean_dvr_storage()
                janitor_timer = time.time()

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self.ffmpeg_process:
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=2)
            except Exception:
                pass
            self.ffmpeg_process = None

        self.is_running = False
        print(f"[AIStreamWorker] AI streaming loop finished for {self.camera_id}")


def start_ai_idle_reaper():
    """Background cleaner to stop AI pipelines if no viewer touches them, and purge orphan files."""
    def loop():
        while True:
            time.sleep(15)
            now = time.time()
            if Config.STREAM_IDLE_TIMEOUT > 0:
                with AIStreamWorker._lock:
                    for cid, worker in list(AIStreamWorker._workers.items()):
                        if worker.is_running and (now - worker.last_accessed) > Config.STREAM_IDLE_TIMEOUT:
                            print(f"[AIStreamWorker] Stopping idle worker: {cid}")
                            worker.stop()
                            worker._clean_dvr_storage()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
