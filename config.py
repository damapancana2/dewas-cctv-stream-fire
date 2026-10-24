import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Server Port for Local Streaming Gateway
    PORT = int(os.environ.get('PORT', 8554))
    HOST = os.environ.get('HOST', '0.0.0.0')

    # Directory for temporary HLS chunk storage (.m3u8 and .ts files)
    HLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hls_storage')

    # VPS Backend URL (for metadata sync or heartbeat)
    VPS_API_URL = os.environ.get('VPS_API_URL', 'https://api.dewas.id/api')

    # Local Public Tunnel URL (e.g. https://cctv-edge.dewas.id or Cloudflare Tunnel)
    TUNNEL_URL = os.environ.get('TUNNEL_URL', '')

    # Default camera RTSP sources on the local LAN
    CAMERA_SOURCES = {
        'cam-sea-001': os.environ.get('CAM_SEA_001_RTSP', ''),
        'cam-01': os.environ.get('CAM_01_RTSP', os.environ.get('CAM_SEA_001_RTSP', '')),
        'CAM-01': os.environ.get('CAM_01_RTSP', os.environ.get('CAM_SEA_001_RTSP', '')),
        'cam-02': os.environ.get('CAM_02_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'CAM-02': os.environ.get('CAM_02_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'cam-flood-01': os.environ.get('CAM_FLOOD_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'CAM-FLOOD-01': os.environ.get('CAM_FLOOD_01_RTSP', os.environ.get('DEFAULT_RTSP', '')),
        'cam-sea-002': os.environ.get('CAM_SEA_002_RTSP', ''),
    }

    @classmethod
    def get_camera_rtsp(cls, camera_id: str) -> str:
        """
        Dynamically finds the RTSP URL for any given camera_id (case-insensitive, supports aliases).
        """
        if not camera_id:
            return os.environ.get('DEFAULT_RTSP', '')

        # Check exact key first
        if camera_id in cls.CAMERA_SOURCES and cls.CAMERA_SOURCES[camera_id]:
            return cls.CAMERA_SOURCES[camera_id]

        cid_lower = camera_id.lower().strip()
        for k, v in cls.CAMERA_SOURCES.items():
            if k.lower() == cid_lower and v:
                return v

        # Check env variable variants, e.g. CAM_01_RTSP, CAM_02_RTSP, CAM_SEA_001_RTSP
        env_slug = cid_lower.replace('-', '_').upper()
        if f"{env_slug}_RTSP" in os.environ and os.environ[f"{env_slug}_RTSP"]:
            return os.environ[f"{env_slug}_RTSP"]

        # Fallback to DEFAULT_RTSP if defined
        return os.environ.get('DEFAULT_RTSP', '')

    # Inactivity timeout (seconds) before stopping FFmpeg transcode if no viewer requests chunks
    STREAM_IDLE_TIMEOUT = int(os.environ.get('STREAM_IDLE_TIMEOUT', 60))

    # Low-Latency HLS parameters
    HLS_SEGMENT_DURATION = int(os.environ.get('HLS_SEGMENT_DURATION', 1))
    HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', 5))
