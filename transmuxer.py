import os
import shutil
import subprocess
import threading
import time
import datetime
from config import Config

def get_ffmpeg_bin():
    """
    Finds working ffmpeg binary in local directory, WinGet links, or system PATH.
    """
    local_bin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(local_bin):
        return local_bin

    which_bin = shutil.which("ffmpeg")
    if which_bin:
        return which_bin

    winget_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe"),
    ]
    for wp in winget_paths:
        if os.path.exists(wp):
            return wp

    return None

class StreamTransmuxer:
    """
    Manages on-demand FFmpeg transmuxing processes from local RTSP to Low-Latency HLS.
    """
    _instances = {}
    _lock = threading.Lock()

    def __init__(self, camera_id: str, rtsp_url: str):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.output_dir = os.path.join(Config.HLS_DIR, camera_id)
        self.process = None
        self.last_accessed = time.time()
        self.is_running = False

    @classmethod
    def get_or_create(cls, camera_id: str, custom_rtsp: str = None):
        with cls._lock:
            key = camera_id.strip()
            if key not in cls._instances:
                rtsp = custom_rtsp or Config.get_camera_rtsp(key)
                cls._instances[key] = cls(key, rtsp)
            cls._instances[key].touch()
            return cls._instances[key]

    def touch(self):
        self.last_accessed = time.time()

    def start(self):
        if self.is_running and self.process and self.process.poll() is None:
            return

        os.makedirs(self.output_dir, exist_ok=True)
        manifest_path = os.path.join(self.output_dir, "index.m3u8")
        segment_pattern = os.path.join(self.output_dir, "segment_%03d.ts")

        # Check if ffmpeg is available and RTSP URL is provided
        ffmpeg_bin = get_ffmpeg_bin()
        if not ffmpeg_bin or not self.rtsp_url:
            self._create_mock_manifest(manifest_path)
            self.is_running = True
            return

        cmd = [
            ffmpeg_bin,
            "-y",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-c:v", "copy",
            "-an",
            "-f", "hls",
            "-hls_time", str(Config.HLS_SEGMENT_DURATION),
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments+append_list+omit_endlist+temp_file",
            "-hls_segment_filename", segment_pattern,
            manifest_path
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.is_running = True
        except Exception as e:
            print(f"[Transmuxer] Error starting FFmpeg for {self.camera_id}: {e}")
            self._create_mock_manifest(manifest_path)
            self.is_running = True

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                self.process.kill()
        self.process = None
        self.is_running = False

    def _create_mock_manifest(self, manifest_path: str):
        now = datetime.datetime.now()
        seq = int(now.timestamp())
        content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:{seq}
#EXT-X-PROGRAM-DATE-TIME:{now.isoformat()}Z
#EXTINF:2.0,
segment_001.ts
#EXTINF:2.0,
segment_002.ts
"""
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(content)

    @classmethod
    def cleanup_idle_streams(cls):
        now = time.time()
        with cls._lock:
            for cam_id, transmuxer in list(cls._instances.items()):
                if now - transmuxer.last_accessed > Config.STREAM_IDLE_TIMEOUT:
                    print(f"[Transmuxer] Stopping idle stream: {cam_id}")
                    transmuxer.stop()

def start_idle_reaper():
    def loop():
        while True:
            time.sleep(15)
            StreamTransmuxer.cleanup_idle_streams()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
