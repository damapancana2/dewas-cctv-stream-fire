import os
import time
import glob
import threading
import subprocess
import cv2
import numpy as np
from config import Config
from detector import FireDetector

def get_ffmpeg_bin():
    local_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(local_bin):
        return local_bin
    import shutil
    which = shutil.which("ffmpeg")
    if which:
        return which
    return "ffmpeg"


class AIStreamWorker:
    """
    Worker that ingests an RTSP camera stream, runs YOLOv5 fire/smoke detection,
    burns visual bounding boxes & HUD into the video stream, and pipes raw frames
    into FFmpeg to output Low-Latency HLS (index.m3u8 + .ts segments).

    Includes a Circular DVR Storage Janitor that automatically deletes old .ts chunks
    so disk storage NEVER fills up (only keeps the latest ~5 segments = ~3MB max total).
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
        keeping the folder strictly within ~3MB max per camera.
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
                    if (now - os.path.getmtime(f)) > 10:
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
        self.thread = threading.Thread(target=self._run_loop, name=f"AI-Stream-{self.camera_id}", daemon=True)
        self.thread.start()
        print(f"[AIStreamWorker] Started AI streaming for {self.camera_id} with Circular DVR storage (max ~3MB).")

    def stop(self):
        print(f"[AIStreamWorker] Stopping AI streaming for {self.camera_id}")
        self.stop_requested = True
        self.is_running = False

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

    def _open_capture(self):
        if not self.rtsp_url:
            return None

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000|reorder_queue_size;0|stimeout;5000000"
        )
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        return None

    def _generate_fallback_frame(self, w=1280, h=720):
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

        target_fps = Config.AI_STREAM_FPS
        skip_frames = Config.AI_SKIP_FRAMES
        conf_thres = Config.AI_CONF_THRESHOLD
        iou_thres = Config.AI_IOU_THRESHOLD

        cap = self._open_capture()
        use_fallback = (cap is None)

        if use_fallback:
            print(f"[AIStreamWorker] RTSP offline for {self.camera_id}. Running in simulation mode.")
            width, height = 1280, 720
        else:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720

        segment_pattern = os.path.join(self.output_dir, "segment_%03d.ts")
        ffmpeg_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24",
            "-r", str(target_fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "26",
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

        failed_reads = 0
        last_reconnect_attempt = time.time()
        reconnect_interval = 3.0  # coba reconnect ke RTSP setiap 3 detik jika koneksi kamera terputus

        while not self.stop_requested:
            loop_start = time.time()

            frame = None
            if not use_fallback and cap is not None:
                ret, frame = cap.read()
                if not ret or frame is None:
                    failed_reads += 1
                    # Toleransi jitter sementara (5 frame drop berturut-turut sebelum switch fallback)
                    if failed_reads >= 5:
                        print(f"[AIStreamWorker] RTSP stream interrupted for {self.camera_id} ({failed_reads} read failures). Retrying in background...")
                        use_fallback = True
                        try:
                            cap.release()
                        except Exception:
                            pass
                        cap = None
                        last_reconnect_attempt = time.time()
                else:
                    failed_reads = 0

            if use_fallback or frame is None:
                frame = self._generate_fallback_frame(width, height)
                now = time.time()
                # Auto-reconnect periodik ke kamera RTSP fisik
                if self.rtsp_url and (now - last_reconnect_attempt >= reconnect_interval):
                    last_reconnect_attempt = now
                    try:
                        new_cap = self._open_capture()
                        if new_cap is not None:
                            ret_test, frame_test = new_cap.read()
                            if ret_test and frame_test is not None:
                                cap = new_cap
                                use_fallback = False
                                failed_reads = 0
                                frame = frame_test
                                print(f"[AIStreamWorker] Successfully reconnected to live RTSP for {self.camera_id}!")
                            else:
                                try:
                                    new_cap.release()
                                except Exception:
                                    pass
                    except Exception as e:
                        print(f"[AIStreamWorker] RTSP reconnect error for {self.camera_id}: {e}")

            frame_idx += 1

            if frame_idx % skip_frames == 0 or not cached_detections:
                try:
                    cached_detections, cached_summary = detector.detect(
                        frame, conf_thres=conf_thres, iou_thres=iou_thres
                    )
                    self.latest_detections = cached_detections
                    self.latest_summary = cached_summary
                except Exception as e:
                    print(f"[AIStreamWorker] Detection error: {e}")

            annotated = detector.annotate(
                frame,
                cached_detections,
                cached_summary,
                camera_id=self.camera_id,
                fps=self.current_fps
            )
            self.latest_frame_annotated = annotated

            if frame_idx % (target_fps * 2) == 0:
                try:
                    cv2.imwrite(self.snapshot_path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
                except Exception:
                    pass

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

        if cap is not None:
            cap.release()

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
