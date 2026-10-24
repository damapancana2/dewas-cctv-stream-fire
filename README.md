# 📹 DEWaS CCTV Stream Gateway (Edge / Local Deployment)

Project ini merupakan **Local Streaming Gateway** mandiri untuk sistem DEWaS (Disaster Early Warning System). 

---

## 🏛️ Arsitektur Edge-to-Cloud (VPS vs Local Edge)

```
[ Kamera IP LAN ]                 [ Edge Server Lokal ]                     [ Cloudflare Tunnel / WAN ]                  [ VPS Server ]
  (192.168.10.x)                    (dewas-cctv-stream)                       (Internet Terenkripsi)                      (dewas-be & dewas-demo)
        |                                    |                                           |                                       |
        |--- RTSP (Port 554) --------------->|                                           |                                       |
        |                                    |--- Transmux ke HLS / WebRTC               |                                       |
        |                                    |--- Expose Port 8554 --------------------->| (Public Tunnel URL)                   |
        |                                    |                                           |   https://stream.dewas.id/live/...    |
        |                                    |                                           |                   |                   |
        |                                    |                                           |                   |<--- Frontend Load |
```

### Mengapa Dipisah dari `dewas-be` di VPS?
1. **Isolasi Jaringan Kamera**: Kamera IP berada di jaringan privat lokal (LAN kantor/pelabuhan/sungai) yang tidak memiliki IP publik statis. VPS di cloud tidak dapat mengakses IP `192.168.10.x` secara langsung.
2. **Efisiensi Beban VPS**: Transcoding video RTSP ke HLS memakan CPU & bandwidth sangat besar. Memprosesnya di edge device lokal memastikan VPS tetap ringan, cepat, dan hanya mengurus database/API REST.
3. **Tunneling Aman**: Gateway lokal ini cukup ditunneling menggunakan **Cloudflare Tunnel (`cloudflared`)** atau **ngrok**, sehingga tidak perlu membuka port berbahaya di router (port forwarding tidak diperlukan).

---

## 🚀 Cara Menjalankan

### 1. Persiapan Environment
Pastikan Python 3.9+ dan **FFmpeg** terinstal di PC/Edge lokal.
```bash
pip install -r requirements.txt
```

Salin file konfigurasi:
```bash
copy .env.example .env
```
Sesuaikan alamat IP RTSP kamera Anda di `.env`.

### 2. Jalankan Gateway Lokal
```bash
python app.py
# Atau klik ganda file run_gateway.bat
```
Server akan berjalan di `http://localhost:8554`.

### 3. Jalankan Tunnel ke Internet
Gunakan Cloudflare Tunnel (Gratis, Cepat, Tanpa Port Forwarding):
```bash
cloudflared tunnel --url http://localhost:8554
# Atau klik ganda file run_tunnel_cloudflare.bat
```
Anda akan mendapatkan URL publik HTTPS, misalnya:
`https://random-subdomain.trycloudflare.com`

Masukkan URL ini ke variabel `TUNNEL_URL` di `.env` atau langsung masukkan ke master data kamera di frontend/VPS:
`https://random-subdomain.trycloudflare.com/live/cam-sea-001/index.m3u8`

---

## 🔌 Daftar Endpoint Streaming

| Metode | Endpoint | Keterangan |
| :--- | :--- | :--- |
| `GET` | `/live/<camera_id>/index.m3u8` | Playlist manifest HLS Low-Latency |
| `GET` | `/live/<camera_id>/<file>.ts` | Chunk potongan video HLS |
| `POST` | `/live/<camera_id>/whep` | Endpoint negosiasi SDP WebRTC (Sub-detik) |
| `GET` | `/api/snapshot/<camera_id>.jpg` | Ambil 1 frame gambar JPEG instan dari RTSP |
| `POST` | `/api/ptz/<camera_id>` | Teruskan perintah gerak PTZ ke IP kamera di LAN |
| `GET` | `/api/streams` | Daftar kamera dan status aktif stream |
| `GET` | `/health` | Healthcheck gateway |

---

## 🐳 Opsi Alternatif: Docker MediaMTX
Jika ingin menggunakan engine C++/Go MediaMTX berkecepatan tinggi:
```bash
docker compose up -d
```
HLS tersedia di port `8888` dan WebRTC WHEP di port `8889`.
