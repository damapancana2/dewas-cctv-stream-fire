# 🔥 DEWaS CCTV Fire & Smoke AI Edge Gateway

Project ini merupakan **Edge AI Streaming Gateway** mandiri untuk sistem DEWaS Fire (Disaster Early Warning System - Kebakaran Hutan & Lahan). Gateway ini bertugas menerima stream RTSP dari kamera IP lapangan, menjalankan inferensi deteksi lidah api & kepulan asap (*fire & smoke detection*) secara real-time menggunakan YOLOv5s (`best.pt`), melakukan transmuxing ke format HLS Low-Latency, serta mengekspos endpoint status peringatan dini.

---

## 🏛️ Arsitektur Edge-to-Cloud

```
[ Kamera IP LAN ]                   [ Edge Server Lokal ]                        [ Cloudflare Tunnel / WAN ]                  [ VPS Server ]
  (192.168.10.x)                      (dewas-cctv-stream-fire)                     (Internet Terenkripsi)                      (dewas-demo & dewas-fire)
        |                                       |                                              |                                           |
        |--- RTSP (Port 554) ------------------>|                                              |                                           |
        |                                       |--- Inferensi YOLOv5 (best.pt)                |                                           |
        |                                       |--- Burn Bounding Box & HUD                   |                                           |
        |                                       |--- Transmux ke HLS (.m3u8 & .ts)             |                                           |
        |                                       |--- Expose Port 8555 ------------------------>| (Public Tunnel URL)                       |
        |                                       |                                              |   https://cctv-fire.damapancana2.com      |
        |                                       |                                              |                   |                       |
        |                                       |                                              |                   |<--- Frontend Load     |
```

---

## 🚀 Panduan Instalasi & Clone di PC Baru

### 1. Clone Repository
```bash
git clone https://github.com/damapancana2/dewas-cctv-stream-fire.git
cd dewas-cctv-stream-fire
```

### 2. Buat & Aktifkan Virtual Environment Python
Pastikan Python 3.9 - 3.11 terinstal:
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instal Dependensi
```bash
pip install -r requirements.txt
```

### 4. Konfigurasi Environment (`.env`)
Salin file template:
```bash
copy .env.example .env
```
Sesuaikan alamat RTSP kamera LAN lokal Anda di file `.env`:
```env
PORT=8555
TUNNEL_URL=https://cctv-fire.damapancana2.com
AUTO_STARTUP=true
CAM_FIRE_01_RTSP=rtsp://admin:Password@192.168.10.64:554/Streaming/channels/101
DEFAULT_RTSP=rtsp://admin:Password@192.168.10.64:554/Streaming/channels/101
```

### 5. Menjalankan Gateway
Klik ganda file `run_gateway.bat` atau via terminal:
```bash
python app.py
```
Server gateway akan berjalan di `http://localhost:8555`.

### 6. Menjalankan Desktop GUI Monitor (Opsional)
Untuk melihat tampilan monitor live visual desktop di PC edge:
```bash
run_desktop.bat
# Atau
python desktop_stream.py
```

---

## 🔄 Fitur Auto-Startup (Otomatis Jalan Saat Komputer Reboot)

Gateway dilengkapi fitur auto-start otomatis agar langsung aktif ketika komputer menyala ulang (restart / mati listrik):

1. **Otomatis Aktif**: Saat `app.py` dijalankan, gateway otomatis mendaftarkan *shortcut* di **Windows Registry (`HKCU\...\Run`)** dan **Windows Startup Folder**.
2. **Pengaturan via `.env`**: Atur `AUTO_STARTUP=true` atau `false`.
3. **Shortcut Satu-Klik**:
   - `enable_autostart.bat` : Klik 2x untuk mengaktifkan auto-startup.
   - `disable_autostart.bat` : Klik 2x untuk mematikan auto-startup.
4. **CLI**:
   ```bash
   python autostart.py --enable   # Aktifkan
   python autostart.py --disable  # Nonaktifkan
   python autostart.py --status   # Cek status
   ```

---

## 🔌 Daftar Endpoint REST & HLS Stream (Port 8555)

| Metode | Endpoint | Keterangan |
| :--- | :--- | :--- |
| `GET` | `/live/<camera_id>/index.m3u8` | Playlist manifest HLS video stream dengan overlay AI |
| `GET` | `/live/<camera_id>/<chunk>.ts` | Chunk potongan video HLS |
| `GET` | `/api/snapshot/<camera_id>.jpg` | Tangkapan 1 frame JPEG instan dengan bounding box api/asap |
| `GET` | `/api/fire/status/<camera_id>` | Status deteksi api (*has_fire*, *has_smoke*, confidence, boxes) |
| `GET` | `/api/fire/all` | Status deteksi seluruh kamera yang terkonfigurasi |
| `POST` | `/api/detect/image` | Uji deteksi api/asap dengan mengunggah gambar form-data |
| `GET` | `/api/streams` | Daftar kamera dan status aktif streaming |
| `GET` | `/api/autostart` | Status fitur auto-startup gateway |
| `POST` | `/api/autostart` | Aktifkan/nonaktifkan auto-startup (`{"action": "enable"}`) |
| `GET` | `/health` | Healthcheck gateway |
