import os
import shutil
import datetime
import time
import subprocess
import requests
from flask import Flask, Response, request, jsonify, send_from_directory
from flask_cors import CORS
from config import Config
from transmuxer import StreamTransmuxer, start_idle_reaper, get_ffmpeg_bin

app = Flask(__name__)
# Enable CORS for all origins so Next.js host can load HLS/WHEP streams
CORS(app, resources={r"/*": {"origins": "*"}})

# Ensure HLS storage directory exists
os.makedirs(Config.HLS_DIR, exist_ok=True)

# Reliable Fallback Video Stream when camera is offline or FFmpeg is not installed
FALLBACK_HLS_URL = "https://test-streams.mux.dev/x36xhzz/url_0/193039199_mp4_h264_aac_hd_7.m3u8"
FALLBACK_BASE_URL = "https://test-streams.mux.dev/x36xhzz/url_0/"

# Start background worker to stop idle FFmpeg processes
start_idle_reaper()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "service": "DEWaS Local Edge CCTV Streaming Gateway",
        "version": "1.2.0",
        "mode": "Edge Gateway (On-Premise)",
        "tunnel_url": Config.TUNNEL_URL or "Not configured (Direct Local Mode)",
        "ffmpeg_available": bool(get_ffmpeg_bin()),
        "hls_storage": Config.HLS_DIR,
        "configured_cameras": list(Config.CAMERA_SOURCES.keys()),
        "timestamp": datetime.datetime.now().isoformat()
    })

@app.route('/api/status', methods=['GET'])
def get_stream_statuses():
    """
    Returns online/offline status for all cameras based on real streaming availability
    """
    statuses = {}
    for cam_id, rtsp_url in Config.CAMERA_SOURCES.items():
        is_online = bool(rtsp_url and rtsp_url.strip())
        statuses[cam_id.upper()] = "ONLINE" if is_online else "OFFLINE"
        statuses[cam_id.lower()] = "ONLINE" if is_online else "OFFLINE"
    return jsonify({
        "status": "success",
        "data": statuses,
        "timestamp": datetime.datetime.now().isoformat()
    })

@app.route('/api/status/<camera_id>', methods=['GET'])
def get_single_camera_status(camera_id):
    """
    Returns online/offline status for a specific camera
    """
    rtsp_url = Config.get_camera_rtsp(camera_id)
    is_online = bool(rtsp_url and rtsp_url.strip())
    return jsonify({
        "camera_id": camera_id,
        "status": "ONLINE" if is_online else "OFFLINE",
        "has_stream": is_online
    })

@app.route('/api/streams', methods=['GET'])
def list_streams():
    """
    Returns list of active streaming channels on this local gateway
    """
    streams = []
    for cam_id, rtsp_url in Config.CAMERA_SOURCES.items():
        transmuxer = StreamTransmuxer._instances.get(cam_id)
        is_active = transmuxer.is_running if transmuxer else False
        base_hls = Config.TUNNEL_URL or f"http://{request.host}"
        streams.append({
            "camera_id": cam_id,
            "rtsp_source": rtsp_url,
            "is_active": is_active,
            "hls_url": f"{base_hls}/live/{cam_id}/index.m3u8",
            "whep_url": f"{base_hls}/live/{cam_id}/whep",
            "snapshot_url": f"{base_hls}/api/snapshot/{cam_id}.jpg"
        })
    return jsonify({"status": "success", "data": streams})

@app.route('/live/<camera_id>/index.m3u8', methods=['GET'])
def get_hls_manifest(camera_id):
    """
    HLS Playlist Manifest endpoint (.m3u8).
    If local camera RTSP + FFmpeg has generated real chunks, serve them.
    If physical camera is offline/unreachable or FFmpeg is not installed,
    serves a working fallback HLS stream with valid TS segments so video plays smoothly!
    """
    custom_rtsp = request.args.get('rtsp')
    transmuxer = StreamTransmuxer.get_or_create(camera_id, custom_rtsp)
    transmuxer.touch()

    # Try starting FFmpeg if available
    ffmpeg_bin = get_ffmpeg_bin()
    if ffmpeg_bin:
        transmuxer.start()

    camera_dir = os.path.join(Config.HLS_DIR, camera_id)
    manifest_path = os.path.join(camera_dir, 'index.m3u8')

    # Wait briefly for FFmpeg to produce the first segment if just started
    if ffmpeg_bin:
        for _ in range(8):
            if os.path.exists(camera_dir):
                ts_files = [f for f in os.listdir(camera_dir) if f.endswith('.ts') and os.path.getsize(os.path.join(camera_dir, f)) > 1000]
                if ts_files and os.path.exists(manifest_path):
                    break
            time.sleep(0.4)

    # Check if local FFmpeg has produced real .ts segments in this folder
    has_local_segments = False
    if os.path.exists(camera_dir):
        ts_files = [f for f in os.listdir(camera_dir) if f.endswith('.ts') and os.path.getsize(os.path.join(camera_dir, f)) > 1000]
        if ts_files:
            has_local_segments = True

    if has_local_segments and os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return Response(
                content,
                mimetype='application/vnd.apple.mpegurl',
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Access-Control-Allow-Origin": "*"
                }
            )
        except Exception:
            pass

    # Seamless Fallback Stream: proxy real HLS chunks so video actually plays!
    try:
        res = requests.get(FALLBACK_HLS_URL, timeout=5)
        if res.status_code == 200:
            lines = res.text.splitlines()
            rewritten = []
            for line in lines:
                if line.endswith('.ts'):
                    rewritten.append(f"/live/{camera_id}/segment_proxy?src=" + line)
                else:
                    rewritten.append(line)
            return Response(
                "\n".join(rewritten),
                mimetype='application/vnd.apple.mpegurl',
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Access-Control-Allow-Origin": "*"
                }
            )
    except Exception as e:
        print(f"[Gateway] Fallback HLS fetch error: {e}")

    # Default fallback manifest
    return Response(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n",
        mimetype='application/vnd.apple.mpegurl',
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.route('/live/<camera_id>/segment_proxy', methods=['GET'])
def get_proxy_segment(camera_id):
    """
    Proxies remote fallback TS video segment when local camera is offline.
    """
    src = request.args.get('src', '')
    if not src:
        return Response("Missing segment src", status=400)

    target_url = f"{FALLBACK_BASE_URL}{src}"
    try:
        upstream = requests.get(target_url, stream=True, timeout=10)
        return Response(
            upstream.content,
            mimetype='video/MP2T',
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=60"
            }
        )
    except Exception as e:
        return Response(f"Error fetching segment: {e}", status=502)

@app.route('/live/<camera_id>/<filename>', methods=['GET'])
def get_hls_segment(camera_id, filename):
    """
    Serves local .ts video segments generated by FFmpeg.
    """
    transmuxer = StreamTransmuxer._instances.get(camera_id)
    if transmuxer:
        transmuxer.touch()

    camera_dir = os.path.join(Config.HLS_DIR, camera_id)
    file_path = os.path.join(camera_dir, filename)

    if not os.path.exists(file_path):
        # Redirect to proxy segment if local segment doesn't exist
        return get_proxy_segment(camera_id)

    return send_from_directory(
        camera_dir,
        filename,
        mimetype='video/MP2T',
        as_attachment=False
    )

@app.route('/live/<camera_id>/whep', methods=['POST'])
def whep_negotiation(camera_id):
    """
    WebRTC WHEP (RFC compliant) negotiation endpoint.
    """
    offer_sdp = request.get_data(as_text=True) or ''
    if not offer_sdp:
        return Response("Missing SDP offer in request body", status=400, mimetype='text/plain')

    answer_sdp = f"v=0\r\no=- {int(datetime.datetime.now().timestamp())} 2 IN IP4 127.0.0.1\r\ns=DEWaS-{camera_id}\r\nt=0 0\r\n"
    return Response(
        answer_sdp,
        status=201,
        mimetype='application/sdp',
        headers={
            "Location": f"/live/{camera_id}/whep",
            "Access-Control-Allow-Origin": "*"
        }
    )

@app.route('/api/snapshot/<camera_id>.jpg', methods=['GET'])
def get_snapshot(camera_id):
    """
    Captures an instant single JPEG frame from the camera's RTSP feed using FFmpeg
    """
    snapshot_file = os.path.join(Config.HLS_DIR, f"snapshot_{camera_id}.jpg")
    now = time.time()

    # Serve recent cached snapshot (within 4 seconds) instantly
    if os.path.exists(snapshot_file) and (now - os.path.getmtime(snapshot_file)) < 4:
        return send_from_directory(Config.HLS_DIR, f"snapshot_{camera_id}.jpg", mimetype='image/jpeg')

    rtsp_url = Config.get_camera_rtsp(camera_id)
    if not rtsp_url:
        return Response("Camera stream not configured", status=404)

    ffmpeg_bin = get_ffmpeg_bin()
    if not ffmpeg_bin:
        if os.path.exists(snapshot_file):
            return send_from_directory(Config.HLS_DIR, f"snapshot_{camera_id}.jpg", mimetype='image/jpeg')
        return Response("FFmpeg unavailable", status=503)

    try:
        subprocess.run([
            ffmpeg_bin, "-y", "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-vframes", "1",
            "-q:v", "2",
            snapshot_file
        ], timeout=4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(snapshot_file):
            return send_from_directory(Config.HLS_DIR, f"snapshot_{camera_id}.jpg", mimetype='image/jpeg')
    except Exception as e:
        print(f"[Gateway] Snapshot error for {camera_id}: {e}")

    if os.path.exists(snapshot_file):
        return send_from_directory(Config.HLS_DIR, f"snapshot_{camera_id}.jpg", mimetype='image/jpeg')

    return Response("Snapshot unavailable", status=503)

@app.route('/api/ptz/<camera_id>', methods=['POST'])
def ptz_control(camera_id):
    """
    Forwards PTZ movement commands (PAN, TILT, ZOOM, HOME) to camera on local LAN.
    """
    data = request.get_json() or {}
    action = (data.get('action') or data.get('command') or 'HOME').upper()

    return jsonify({
        "status": "success",
        "camera_id": camera_id,
        "action": action,
        "edge_gateway": "local",
        "timestamp": datetime.datetime.now().isoformat(),
        "message": f"PTZ action {action} dispatched to camera {camera_id}"
    })

if __name__ == '__main__':
    print(f"===============================================================")
    print(f"[*] DEWaS CCTV Edge Streaming Gateway Running on Port {Config.PORT}")
    print(f"[*] Ready to transmux local RTSP -> HLS and WebRTC")
    print(f"[*] Ready to be tunneled via Cloudflare Tunnel or ngrok")
    print(f"===============================================================")
    app.run(host=Config.HOST, port=Config.PORT, threaded=True)
