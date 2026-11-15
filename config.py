import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Server Port for Local Streaming Gateway (Default 8555 to avoid collision with flood 8554)
    PORT = int(os.environ.get('PORT', 8555))
    HOST = os.environ.get('HOST', '0.0.0.0')

    # Directory for temporary HLS chunk storage (.m3u8 and .ts files)
    HLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hls_storage')

    # VPS Backend URL
    VPS_API_URL = os.environ.get('VPS_API_URL', 'https://api-hub.damapancana2.com/api')

    # Tunnel URL (e.g. Cloudflare tunnel or ngrok)
    TUNNEL_URL = os.environ.get('TUNNEL_URL', '')

    # AI Detection Configurations
    AI_ENABLED = os.environ.get('AI_ENABLED', 'true').lower() in ('1', 'true', 'yes')
    AI_CONF_THRESHOLD = float(os.environ.get('AI_CONF_THRESHOLD', '0.25'))
    AI_IOU_THRESHOLD = float(os.environ.get('AI_IOU_THRESHOLD', '0.45'))
    AI_STREAM_FPS = int(os.environ.get('AI_STREAM_FPS', '12'))
    AI_SKIP_FRAMES = int(os.environ.get('AI_SKIP_FRAMES', '3'))  # Run YOLO every 3 frames

    # Auto-start otomatis saat komputer reboot/restart (true/false)
    AUTO_STARTUP = os.environ.get('AUTO_STARTUP', 'true').lower() in ('1', 'true', 'yes')

    # Camera RTSP mapping
    CAMERA_SOURCES = {
        'cam-sea-001': os.environ.get('CAM_SEA_001_RTSP', ''),
        'cam-01': os.environ.get('CAM_01_RTSP', os.environ.get('CAM_SEA_001_RTSP', '')),
        'CAM-01': os.environ.get('CAM_01_RTSP', os.environ.get('CAM_SEA_001_RTSP', '')),
        'cam-02': os.environ.get('CAM_02_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'CAM-02': os.environ.get('CAM_02_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'cam-flood-01': os.environ.get('CAM_FLOOD_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'CAM-FLOOD-01': os.environ.get('CAM_FLOOD_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'cam-fire-01': os.environ.get('CAM_FIRE_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'CAM-FIRE-01': os.environ.get('CAM_FIRE_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
    }

    @classmethod
    def get_camera_rtsp(cls, camera_id: str) -> str:
        if not camera_id:
            return os.environ.get('DEFAULT_RTSP', '')

        if camera_id in cls.CAMERA_SOURCES and cls.CAMERA_SOURCES[camera_id]:
            return cls.CAMERA_SOURCES[camera_id]

        cid_lower = camera_id.lower().strip()
        for k, v in cls.CAMERA_SOURCES.items():
            if k.lower() == cid_lower and v:
                return v

        env_slug = cid_lower.replace('-', '_').upper()
        if f"{env_slug}_RTSP" in os.environ and os.environ[f"{env_slug}_RTSP"]:
            return os.environ[f"{env_slug}_RTSP"]

        return os.environ.get('DEFAULT_RTSP', '')

    STREAM_IDLE_TIMEOUT = int(os.environ.get('STREAM_IDLE_TIMEOUT', 60))
    HLS_SEGMENT_DURATION = int(os.environ.get('HLS_SEGMENT_DURATION', 1))
    HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', 5))
