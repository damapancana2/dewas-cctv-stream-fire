"""
DEWaS CCTV Fire & Smoke AI Edge Gateway - Auto Startup Manager
Mengatur agar gateway CCTV Fire otomatis berjalan saat komputer reboot/restart.
Mendukung Windows (Registry Run & Startup Folder) dan Linux (systemd user service).
"""

import os
import sys
import platform
import argparse

APP_NAME = "DEWaS_CCTV_Stream_Fire"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VBS_LAUNCHER = os.path.join(BASE_DIR, "run_background.vbs")
BAT_LAUNCHER = os.path.join(BASE_DIR, "run_gateway.bat")


def _get_windows_startup_dir() -> str:
    """Mengambil folder Startup Windows pengguna saat ini."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    return ""


def _get_startup_script_path() -> str:
    """Path file .vbs di Startup folder Windows."""
    sdir = _get_windows_startup_dir()
    if sdir and os.path.exists(sdir):
        return os.path.join(sdir, f"{APP_NAME}_Startup.vbs")
    return ""


def enable_autostart() -> tuple:
    """
    Mengaktifkan auto-startup saat komputer restart.
    Returns: (success: bool, message: str)
    """
    system = platform.system()

    if system == "Windows":
        messages = []
        reg_ok = False
        startup_ok = False

        # 1. Daftarkan di Windows Registry (HKCU\Software\Microsoft\Windows\CurrentVersion\Run)
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            # Jalankan wscript run_background.vbs agar silent tanpa command prompt
            cmd = f'wscript.exe "{VBS_LAUNCHER}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            reg_ok = True
            messages.append("Registry HKCU Run berhasil didaftarkan")
        except Exception as e:
            messages.append(f"Registry gagal ({e})")

        # 2. Pasang juga script launcher di folder Windows Startup untuk redundansi
        try:
            startup_file = _get_startup_script_path()
            if startup_file:
                vbs_content = (
                    f"' Auto-start shortcut for {APP_NAME}\n"
                    f'Set WshShell = CreateObject("WScript.Shell")\n'
                    f'WshShell.CurrentDirectory = "{BASE_DIR}"\n'
                    f'WshShell.Run "cmd /c ""{BAT_LAUNCHER}"" --no-pause", 0, False\n'
                )
                with open(startup_file, "w", encoding="utf-8") as f:
                    f.write(vbs_content)
                startup_ok = True
                messages.append(f"Startup folder shortcut dibuat: {os.path.basename(startup_file)}")
        except Exception as e:
            messages.append(f"Startup folder gagal ({e})")

        if reg_ok or startup_ok:
            return True, f"[AutoStartup] Auto-start Fire AKTIF ({'; '.join(messages)})"
        else:
            return False, f"[AutoStartup] Gagal mengaktifkan: {'; '.join(messages)}"

    elif system == "Linux":
        # Untuk environment Linux (misalnya dipindahkan ke server Linux/Raspberry Pi)
        try:
            systemd_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(systemd_dir, exist_ok=True)
            service_path = os.path.join(systemd_dir, f"{APP_NAME.lower()}.service")

            python_bin = os.path.join(BASE_DIR, ".venv", "bin", "python")
            if not os.path.exists(python_bin):
                python_bin = sys.executable

            service_content = f"""[Unit]
Description=DEWaS CCTV Fire & Smoke AI Edge Gateway
After=network.target

[Service]
Type=simple
WorkingDirectory={BASE_DIR}
ExecStart={python_bin} {os.path.join(BASE_DIR, 'app.py')}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
            with open(service_path, "w", encoding="utf-8") as f:
                f.write(service_content)

            os.system("systemctl --user daemon-reload")
            os.system(f"systemctl --user enable {APP_NAME.lower()}.service")
            return True, f"[AutoStartup] systemd service diaktifkan ({service_path})"
        except Exception as e:
            return False, f"[AutoStartup] Gagal mengaktifkan systemd service: {e}"

    return False, f"[AutoStartup] Sistem operasi {system} belum didukung otomatis"


def disable_autostart() -> tuple:
    """
    Menonaktifkan auto-startup.
    Returns: (success: bool, message: str)
    """
    system = platform.system()

    if system == "Windows":
        messages = []

        # 1. Hapus dari Registry
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, APP_NAME)
                messages.append("Registry HKCU Run dihapus")
            except FileNotFoundError:
                messages.append("Registry HKCU Run tidak ditemukan")
            winreg.CloseKey(key)
        except Exception as e:
            messages.append(f"Gagal menghapus registry: {e}")

        # 2. Hapus dari Startup folder
        try:
            startup_file = _get_startup_script_path()
            if startup_file and os.path.exists(startup_file):
                os.remove(startup_file)
                messages.append("File startup dihapus")
            else:
                messages.append("File startup tidak ada")
        except Exception as e:
            messages.append(f"Gagal menghapus file startup: {e}")

        return True, f"[AutoStartup] Auto-start Fire DINONAKTIFKAN ({'; '.join(messages)})"

    elif system == "Linux":
        try:
            os.system(f"systemctl --user disable {APP_NAME.lower()}.service")
            service_path = os.path.expanduser(f"~/.config/systemd/user/{APP_NAME.lower()}.service")
            if os.path.exists(service_path):
                os.remove(service_path)
            return True, "[AutoStartup] systemd service dinonaktifkan dan dihapus"
        except Exception as e:
            return False, f"[AutoStartup] Gagal menonaktifkan systemd service: {e}"

    return False, f"[AutoStartup] Sistem operasi {system} tidak didukung"


def is_autostart_enabled() -> bool:
    """Cek apakah auto-startup saat ini aktif."""
    system = platform.system()

    if system == "Windows":
        # Cek registry
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except Exception:
            pass

        # Cek folder Startup
        startup_file = _get_startup_script_path()
        if startup_file and os.path.exists(startup_file):
            return True

        return False

    elif system == "Linux":
        service_path = os.path.expanduser(f"~/.config/systemd/user/{APP_NAME.lower()}.service")
        return os.path.exists(service_path)

    return False


def get_autostart_status() -> dict:
    """Informasi status auto-startup lengkap."""
    enabled = is_autostart_enabled()
    system = platform.system()
    details = {
        "enabled": enabled,
        "platform": system,
        "app_name": APP_NAME,
        "base_dir": BASE_DIR,
    }

    if system == "Windows":
        details["startup_folder_script"] = _get_startup_script_path()
        details["startup_folder_script_exists"] = bool(
            details["startup_folder_script"] and os.path.exists(details["startup_folder_script"])
        )
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            details["registry_command"] = val
            details["registry_active"] = True
        except Exception:
            details["registry_command"] = None
            details["registry_active"] = False

    return details


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kelola Auto-Startup DEWaS CCTV Fire & Smoke Gateway")
    parser.add_argument("--enable", action="store_true", help="Aktifkan auto-startup saat komputer restart")
    parser.add_argument("--disable", action="store_true", help="Nonaktifkan auto-startup")
    parser.add_argument("--status", action="store_true", help="Cek status auto-startup")

    args = parser.parse_args()

    if args.disable:
        ok, msg = disable_autostart()
        print(msg)
    elif args.enable or (not args.status and not args.disable):
        ok, msg = enable_autostart()
        print(msg)
    else:
        st = get_autostart_status()
        print("Status Auto-Startup:", "AKTIF" if st["enabled"] else "TIDAK AKTIF")
        for k, v in st.items():
            print(f"  - {k}: {v}")

