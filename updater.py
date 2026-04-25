import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin

import requests

CODERUN_BASE_URL = "https://jiugae-api-779661147142.asia-northeast3.run.app"
HMAC_KEY = "8b85aac9ea6480a62442494dcc505f123d68e0ffc806ede2c9c859057208df19"
CONFIG_CANDIDATE_PATHS = ("/config", "/app-config", "/")
CURRENT_VERSION = "2.2.1"
MANIFEST_FILENAME = "app_update_manifest.json"
GITHUB_RELEASES_URL = "https://github.com/3yearscurry/Busan_Bus_Arrival_Information/releases"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/3yearscurry/Busan_Bus_Arrival_Information/releases/latest"
GIST_CONFIG_URL = (
    "https://gist.githubusercontent.com/3yearscurry/442d1081b589d3caf3669ef9668bea00/raw/Busan_config.json"
)
USEFUL_CONFIG_KEYS = {
    "api_key",
    "bus_api_key",
    "service_key",
    "public_data_api_key",
}

_CACHE_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "부산실시간버스안내",
)
_CACHE_FILE = os.path.join(_CACHE_DIR, "config_cache.json")


def _resource_candidates():
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), MANIFEST_FILENAME))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.join(meipass, MANIFEST_FILENAME))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), MANIFEST_FILENAME))
    return candidates


def _load_resource_manifest():
    for path in _resource_candidates():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data["_resource_path"] = path
                return data
        except Exception:
            continue
    return {}


def _load_cache():
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _hmac_headers(path: str) -> dict:
    if not HMAC_KEY:
        return {}
    ts = str(int(time.time()))
    msg = f"{ts}:{path}".encode()
    sig = hmac.new(HMAC_KEY.encode(), msg, hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Signature": sig}


def _request_json(path):
    url = urljoin(CODERUN_BASE_URL.rstrip("/") + "/", path.lstrip("/"))
    req_path = "/" + path.lstrip("/")
    resp = requests.get(url, timeout=5, headers={"Accept": "application/json", **_hmac_headers(req_path)})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("remote config is not a JSON object")
    data["_source_url"] = url
    return data


def _merge_config(base, override):
    merged = dict(base or {})
    merged.update(override or {})
    merged["proxy_base_url"] = str(
        merged.get("proxy_base_url") or CODERUN_BASE_URL
    ).strip().rstrip("/")
    if HMAC_KEY:
        merged["hmac_key"] = str(HMAC_KEY).strip()
    else:
        merged["hmac_key"] = str(merged.get("hmac_key") or "").strip()
    return merged


def _has_useful_config(data):
    return any(data.get(key) not in (None, "", {}) for key in USEFUL_CONFIG_KEYS)


def get_runtime_config(force_refresh=False):
    bundled = _load_resource_manifest()
    cached = _load_cache()
    if not isinstance(cached, dict):
        cached = {}
    base_config = _merge_config(bundled, cached)

    if not force_refresh:
        if base_config:
            return _merge_config(base_config, {})

    last_error = None
    for path in CONFIG_CANDIDATE_PATHS:
        try:
            data = _request_json(path)
            merged = _merge_config(base_config, data)
            if _has_useful_config(data) or base_config:
                _save_cache(merged)
            return merged
        except Exception as exc:
            last_error = exc

    if base_config:
        return _merge_config(base_config, {})
    if last_error:
        return _merge_config({"_error": str(last_error)}, {})
    return _merge_config({}, {})


def _pick_first(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def get_api_key(fallback=""):
    config = get_runtime_config(force_refresh=True)
    return _pick_first(
        config,
        "api_key",
        "bus_api_key",
        "service_key",
        "public_data_api_key",
    ) or fallback


def get_proxy_base_url():
    config = get_runtime_config()
    return str(config.get("proxy_base_url") or CODERUN_BASE_URL).strip().rstrip("/")


def _version_tuple(version):
    try:
        return tuple(int(x) for x in str(version).lstrip("v").split("."))
    except Exception:
        return (0,)


def check_for_update():
    try:
        resp = requests.get(
            GITHUB_LATEST_RELEASE_API,
            timeout=5,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        latest = str(data.get("tag_name") or "").strip()
        if latest and _version_tuple(latest) > _version_tuple(CURRENT_VERSION):
            asset_url = next(
                (
                    asset.get("browser_download_url")
                    for asset in data.get("assets", [])
                    if str(asset.get("name") or "").lower().endswith(".exe")
                ),
                "",
            )
            return {
                "version": latest,
                "notes": str(data.get("body") or "").strip(),
                "download_url": asset_url,
                "release_url": str(data.get("html_url") or GITHUB_RELEASES_URL),
            }
    except Exception:
        pass

    config = get_runtime_config(force_refresh=False)
    latest = _pick_first(config, "latest_version", "version")
    download_url = _pick_first(config, "download_url", "installer_url")
    if latest and download_url and _version_tuple(latest) > _version_tuple(CURRENT_VERSION):
        return {
            "version": latest,
            "notes": str(config.get("notes") or config.get("release_notes") or "").strip(),
            "download_url": download_url,
            "release_url": GITHUB_RELEASES_URL,
        }
    return None


def show_toast(title, message):
    try:
        safe_title = str(title).replace("'", "''")
        safe_message = str(message).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            f"$n.BalloonTipTitle = '{safe_title}';"
            f"$n.BalloonTipText = '{safe_message}';"
            "$n.ShowBalloonTip(4000);"
            "Start-Sleep -Milliseconds 4500;"
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-NonInteractive", "-Command", script],
            creationflags=0x08000000,
        )
    except Exception:
        pass


def download_and_launch(download_url, on_progress=None, on_done=None):
    try:
        show_toast("업데이트 다운로드", "업데이트 파일을 다운로드하고 있습니다.")
        resp = requests.get(download_url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as fp:
            path = fp.name
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                fp.write(chunk)
                downloaded += len(chunk)
                if on_progress and total:
                    on_progress(downloaded / total)

        show_toast("업데이트 설치 시작", "다운로드 완료. 설치 프로그램을 실행합니다.")
        subprocess.Popen([path])
        if on_done:
            on_done(True)
        return True
    except Exception:
        if on_done:
            on_done(False)
        return False


def check_integrity():
    if not getattr(sys, "frozen", False):
        return True, "dev"

    try:
        sha256 = hashlib.sha256()
        with open(sys.executable, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        exe_hash = sha256.hexdigest()
    except Exception as exc:
        return True, f"hash_error: {exc}"

    try:
        import time
        url = f"{GIST_CONFIG_URL}?t={int(time.time())}"
        resp = requests.get(url, timeout=10, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return False, f"fetch_error: {exc}"

    expected = (
        data.get("sha256")
        or data.get("hash")
        or data.get(CURRENT_VERSION)
        or ""
    )
    if not expected:
        return True, "no_hash"

    if exe_hash.lower() == str(expected).lower().removeprefix("sha256:"):
        return True, "ok"
    return False, "mismatch"
