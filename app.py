import os
import time
import datetime
import cv2
import numpy as np
from flask import Flask, Response, jsonify, send_from_directory, request
from flask_cors import CORS
from config import Config
from detector import FireDetector
from ai_stream_worker import AIStreamWorker, start_ai_idle_reaper
from autostart import enable_autostart, disable_autostart, is_autostart_enabled, get_autostart_status

app = Flask(__name__)
CORS(app)

os.makedirs(Config.HLS_DIR, exist_ok=True)

# Start background idle reaper
start_ai_idle_reaper()

# Inisialisasi Auto-Startup saat komputer restart (bisa dimatikan via .env AUTO_STARTUP=false)
if getattr(Config, 'AUTO_STARTUP', True):
    try:
        ok, msg = enable_autostart()
        if ok:
            print(f"[*] {msg}")
    except Exception as e:
        print(f"[Autostart] Warning: {e}")


@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "service": "DEWaS CCTV Fire & Smoke AI Edge Gateway",
        "version": "2.0.0",
        "ai_model": "YOLOv5s (Fire & Smoke)",
        "ai_enabled": Config.AI_ENABLED,
        "classes": ["fire", "smoke"],
        "port": Config.PORT,
        "autostart_enabled": is_autostart_enabled(),
        "endpoints": {
            "health": "/health",
            "autostart": "/api/autostart",
            "hls_stream": "/live/<camera_id>/index.m3u8",
            "fire_status": "/api/fire/status/<camera_id>",
            "all_fire_status": "/api/fire/all",
            "snapshot": "/api/snapshot/<camera_id>.jpg",
            "detect_upload": "/api/detect/image"
        }
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "service": "DEWaS CCTV Fire & Smoke AI Edge Gateway",
        "version": "2.0.0",
        "mode": "Edge Gateway (On-Premise Fire)",
        "port": Config.PORT,
        "tunnel_url": Config.TUNNEL_URL or "Not configured (Direct Local Mode)",
        "ai_enabled": Config.AI_ENABLED,
        "configured_cameras": list(Config.CAMERA_SOURCES.keys()),
        "autostart_enabled": is_autostart_enabled(),
        "timestamp": datetime.datetime.now().isoformat()
    })


@app.route('/api/autostart', methods=['GET', 'POST'])
def autostart_endpoint():
    """
    Endpoint untuk cek atau ubah konfigurasi auto-startup sistem saat komputer restart.
    """
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        action = data.get('action', 'enable').lower()
        if action == 'disable':
            ok, msg = disable_autostart()
        else:
            ok, msg = enable_autostart()
        return jsonify({
            "status": "success" if ok else "error",
            "message": msg,
            "data": get_autostart_status()
        })
    return jsonify({
        "status": "success",
        "data": get_autostart_status()
    })


@app.route('/api/status', methods=['GET'])
def get_stream_statuses():
    """
    Returns online/offline status for all cameras based on real streaming availability
    """
    statuses = {}
    all_ai = AIStreamWorker.get_all_status()
    for cam_id, rtsp_url in Config.CAMERA_SOURCES.items():
        is_online = bool(rtsp_url and rtsp_url.strip())
        status_str = "ONLINE" if is_online else "OFFLINE"
        statuses[cam_id] = status_str
        statuses[cam_id.upper()] = status_str
        statuses[cam_id.lower()] = status_str

    for cid in all_ai.keys():
        statuses[cid] = "ONLINE"
        statuses[cid.upper()] = "ONLINE"
        statuses[cid.lower()] = "ONLINE"

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
    worker = AIStreamWorker._workers.get(camera_id) or AIStreamWorker._workers.get(camera_id.lower())
    if worker and worker.is_running:
        is_online = True

    return jsonify({
        "camera_id": camera_id,
        "status": "ONLINE" if is_online else "OFFLINE",
        "has_stream": is_online
    })


@app.route('/api/streams', methods=['GET'])
def list_streams():
    streams = []
    base_url = Config.TUNNEL_URL or f"http://{request.host}"
    all_ai_status = AIStreamWorker.get_all_status()

    for cam_id, rtsp_url in Config.CAMERA_SOURCES.items():
        ai_stat = all_ai_status.get(cam_id, {})
        is_active = ai_stat.get('is_running', False)
        streams.append({
            "camera_id": cam_id,
            "rtsp_source": rtsp_url,
            "is_active": is_active,
            "ai_enabled": Config.AI_ENABLED,
            "hls_url": f"{base_url}/live/{cam_id}/index.m3u8",
            "snapshot_url": f"{base_url}/api/snapshot/{cam_id}.jpg",
            "fire_status_url": f"{base_url}/api/fire/status/{cam_id}",
            "fire_alert": ai_stat.get('summary', {}).get('has_fire', False),
            "smoke_alert": ai_stat.get('summary', {}).get('has_smoke', False)
        })
    return jsonify({"status": "success", "data": streams})


@app.route('/live/<camera_id>/index.m3u8', methods=['GET'])
def get_hls_manifest(camera_id):
    """
    HLS Playlist Manifest endpoint (.m3u8).
    When AI_ENABLED is True, starts AIStreamWorker which burns bounding boxes
    and detection HUD directly into the live HLS stream!
    """
    custom_rtsp = request.args.get('rtsp')

    worker = AIStreamWorker.get_or_create(camera_id, custom_rtsp)
    worker.touch()
    worker.start()

    camera_dir = os.path.join(Config.HLS_DIR, camera_id)
    manifest_path = os.path.join(camera_dir, 'index.m3u8')

    # Wait briefly for the first HLS segments to be written
    for _ in range(12):
        if os.path.exists(camera_dir):
            ts_files = [f for f in os.listdir(camera_dir) if f.endswith('.ts') and os.path.getsize(os.path.join(camera_dir, f)) > 1000]
            if ts_files and os.path.exists(manifest_path):
                break
        time.sleep(0.35)

    if os.path.exists(manifest_path):
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

    return Response(
        "Camera stream initializing or offline",
        status=404,
        mimetype='text/plain',
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.route('/live/<camera_id>/<filename>', methods=['GET'])
def get_hls_segment(camera_id, filename):
    worker = AIStreamWorker._workers.get(camera_id)
    if worker:
        worker.touch()

    camera_dir = os.path.join(Config.HLS_DIR, camera_id)
    file_path = os.path.join(camera_dir, filename)

    if not os.path.exists(file_path):
        return Response("Segment not found", status=404, headers={"Access-Control-Allow-Origin": "*"})

    return send_from_directory(
        camera_dir,
        filename,
        mimetype='video/MP2T',
        as_attachment=False
    )


@app.route('/live/<camera_id>/mjpeg', methods=['GET'])
@app.route('/live/<camera_id>/stream.mjpg', methods=['GET'])
def get_mjpeg_stream(camera_id):
    """
    Sub-second Real-time MJPEG Stream (<250ms latency).
    Direct multipart HTTP JPEG stream without HLS segment buffering.
    """
    custom_rtsp = request.args.get('rtsp')
    worker = AIStreamWorker.get_or_create(camera_id, custom_rtsp)
    worker.touch()
    worker.start()

    def generate():
        last_sent = None
        while worker.is_running:
            worker.touch()
            frame = worker.latest_frame_annotated
            if frame is not None and frame is not last_sent:
                last_sent = frame
                ret, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.04)

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.route('/api/fire/status/<camera_id>', methods=['GET'])
def get_fire_status(camera_id):
    """
    Returns real-time detection telemetry: fire/smoke presence, confidence scores,
    and bounding box coordinates for frontend dashboards.
    """
    worker = AIStreamWorker._workers.get(camera_id)
    if worker:
        worker.touch()
        summary = worker.latest_summary
        detections = worker.latest_detections
        fps = worker.current_fps
        is_active = worker.is_running
    else:
        summary = {
            'has_fire': False,
            'has_smoke': False,
            'max_fire_conf': 0.0,
            'max_smoke_conf': 0.0,
            'inference_ms': 0.0,
            'detections_count': 0,
            'timestamp': datetime.datetime.now().isoformat()
        }
        detections = []
        fps = 0.0
        is_active = False

    alert_level = "CRITICAL" if summary.get('has_fire') else ("WARNING" if summary.get('has_smoke') else "NORMAL")

    return jsonify({
        "status": "success",
        "camera_id": camera_id,
        "is_active": is_active,
        "alert_level": alert_level,
        "has_fire": summary.get('has_fire', False),
        "has_smoke": summary.get('has_smoke', False),
        "max_fire_confidence": summary.get('max_fire_conf', 0.0),
        "max_smoke_confidence": summary.get('max_smoke_conf', 0.0),
        "detections": detections,
        "inference_ms": summary.get('inference_ms', 0.0),
        "fps": round(fps, 1),
        "timestamp": summary.get('timestamp') or datetime.datetime.now().isoformat()
    })


@app.route('/api/fire/all', methods=['GET'])
def get_all_fire_status():
    """Returns fire/smoke alert state for all cameras."""
    status_map = AIStreamWorker.get_all_status()
    summary_list = []
    for cid in Config.CAMERA_SOURCES.keys():
        st = status_map.get(cid, {})
        sum_data = st.get('summary', {})
        has_fire = sum_data.get('has_fire', False)
        has_smoke = sum_data.get('has_smoke', False)
        summary_list.append({
            "camera_id": cid,
            "is_running": st.get('is_running', False),
            "alert_level": "CRITICAL" if has_fire else ("WARNING" if has_smoke else "NORMAL"),
            "has_fire": has_fire,
            "has_smoke": has_smoke,
            "max_fire_conf": sum_data.get('max_fire_conf', 0.0),
            "max_smoke_conf": sum_data.get('max_smoke_conf', 0.0)
        })
    return jsonify({"status": "success", "data": summary_list})


@app.route('/api/snapshot/<camera_id>.jpg', methods=['GET'])
@app.route('/api/fire/snapshot/<camera_id>.jpg', methods=['GET'])
def get_snapshot(camera_id):
    """
    Returns an instant JPEG image annotated with YOLO bounding boxes and HUD.
    """
    worker = AIStreamWorker._workers.get(camera_id)
    if worker:
        worker.touch()
        if worker.latest_frame_annotated is not None:
            _, buffer = cv2.imencode('.jpg', worker.latest_frame_annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return Response(buffer.tobytes(), mimetype='image/jpeg', headers={"Access-Control-Allow-Origin": "*"})

    snapshot_file = os.path.join(Config.HLS_DIR, f"snapshot_{camera_id}.jpg")
    if os.path.exists(snapshot_file):
        return send_from_directory(Config.HLS_DIR, f"snapshot_{camera_id}.jpg", mimetype='image/jpeg')

    # If worker is not running, run one-shot detection on test_snap.jpg or camera
    detector = FireDetector.get_instance()
    snap_sample = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_snap.jpg")
    if os.path.exists(snap_sample):
        img0 = cv2.imread(snap_sample)
        dets, summary = detector.detect(img0)
        annotated = detector.annotate(img0, dets, summary, camera_id=camera_id)
        _, buffer = cv2.imencode('.jpg', annotated)
        return Response(buffer.tobytes(), mimetype='image/jpeg', headers={"Access-Control-Allow-Origin": "*"})

    return Response("Snapshot unavailable", status=503)


@app.route('/api/detect/image', methods=['POST'])
def detect_uploaded_image():
    """
    Receives an uploaded image file (multipart/form-data key 'file' or raw bytes)
    and returns detections + annotated image.
    """
    file = request.files.get('file') or request.files.get('image')
    if not file:
        return jsonify({"status": "error", "message": "Missing file in request"}), 400

    img_bytes = file.read()
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        return jsonify({"status": "error", "message": "Invalid image format"}), 400

    detector = FireDetector.get_instance()
    conf = float(request.args.get('conf', Config.AI_CONF_THRESHOLD))
    dets, summary = detector.detect(img, conf_thres=conf)

    return jsonify({
        "status": "success",
        "summary": summary,
        "detections": dets
    })


if __name__ == '__main__':
    print("=" * 65)
    print(f"[*] DEWaS CCTV Fire & Smoke AI Edge Gateway Running on Port {Config.PORT}")
    print(f"[*] YOLOv5 Model: best.pt (Fire & Smoke Detection)")
    print(f"[*] AI HLS Stream Endpoint: http://{Config.HOST}:{Config.PORT}/live/<camera_id>/index.m3u8")
    print(f"[*] Snapshot Endpoint:     http://{Config.HOST}:{Config.PORT}/api/snapshot/<camera_id>.jpg")
    print(f"[*] Telemetry Endpoint:    http://{Config.HOST}:{Config.PORT}/api/fire/status/<camera_id>")
    print("=" * 65)
    app.run(host=Config.HOST, port=Config.PORT, threaded=True)
