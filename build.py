"""
빌드 스크립트
  1. 버전 변경(선택)
  2. PyInstaller 빌드 (onedir)
  3. Inno Setup으로 인스톨러 생성
  4. 앱 리소스 메타데이터 갱신
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ENTRY = "main.py"
APP_NAME = "부산실시간버스안내"
UPDATER_FILE = "updater.py"
ISS_FILE = "installer.iss"
ISCC = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
MANIFEST_FILE = "app_update_manifest.json"

GIST_ID = "442d1081b589d3caf3669ef9668bea00"
GIST_FILENAME = "Busan_config.json"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"


def _python_for_build():
    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


def _read_current_version():
    with open(UPDATER_FILE, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "1.0.0"


def _write_version(version):
    with open(UPDATER_FILE, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r'(CURRENT_VERSION\s*=\s*)"[^"]+"', f'\\1"{version}"', text)
    with open(UPDATER_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def _bump_patch(version):
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def _build_exe():
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    manifest = os.path.join(os.path.dirname(os.path.abspath(__file__)), MANIFEST_FILE)
    python_exe = _python_for_build()
    cmd = [
        python_exe, "-m", "PyInstaller",
        "--onedir",
        "--windowed",
        f"--name={APP_NAME}",
        f"--icon={icon}",
        f"--add-data={icon};.",
        f"--add-data={manifest};.",
        "--hidden-import=edge_tts",
        "--hidden-import=edge_playback",
        "--clean",
        ENTRY,
    ]
    print("\n[빌드] PyInstaller 실행 중..")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[오류] PyInstaller 빌드 실패")
        sys.exit(1)
    exe_path = os.path.join("dist", APP_NAME, f"{APP_NAME}.exe")
    if not os.path.exists(exe_path):
        print(f"[오류] 결과물을 찾을 수 없습니다: {exe_path}")
        sys.exit(1)
    return exe_path


def _build_installer(version):
    if not os.path.exists(ISCC):
        print(f"[경고] Inno Setup을 찾을 수 없습니다: {ISCC}")
        print("  https://jrsoftware.org/isdl.php 에서 설치 후 다시 실행하세요.")
        return None

    os.makedirs("installer", exist_ok=True)
    print("\n[인스톨러] Inno Setup 컴파일 중..")
    result = subprocess.run([
        ISCC,
        f"/DAppVersion={version}",
        ISS_FILE,
    ])
    if result.returncode != 0:
        print("[오류] 인스톨러 빌드 실패")
        sys.exit(1)

    installer_path = os.path.join("installer", f"BusanBus_Setup_v{version}.exe")
    if not os.path.exists(installer_path):
        print(f"[오류] 인스톨러를 찾을 수 없습니다: {installer_path}")
        sys.exit(1)

    print(f"[인스톨러] 완료: {installer_path}")
    return installer_path


def _load_manifest():
    if not os.path.exists(MANIFEST_FILE):
        return {}
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _save_manifest(data):
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sync_manifest_to_dist():
    dist_manifest = os.path.join("dist", APP_NAME, MANIFEST_FILE)
    if os.path.isdir(os.path.dirname(dist_manifest)):
        shutil.copyfile(MANIFEST_FILE, dist_manifest)


def _compute_sha256(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _upload_hash_to_gist(sha256_hash, version):
    token = ""
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build_token")
    try:
        with open(token_file, encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        pass

    if not token:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        token = input("GitHub Personal Access Token (gist 권한 필요): ").strip()
    if not token:
        print("[경고] 토큰 없음 — Gist 업로드를 건너뜁니다.")
        return False

    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    existing = {}
    try:
        req = urllib.request.Request(GIST_API_URL, headers=auth_headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            gist_data = json.loads(resp.read().decode())
            file_content = gist_data["files"][GIST_FILENAME]["content"]
            parsed = json.loads(file_content)
            if isinstance(parsed, dict):
                existing = parsed
    except Exception as exc:
        print(f"[경고] 기존 Gist 내용 로드 실패 (새로 작성): {exc}")

    for legacy_key in ("sha256", "hash", "version"):
        existing.pop(legacy_key, None)
    existing[version] = sha256_hash

    payload = json.dumps({
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(existing, indent=2)
            }
        }
    }).encode()

    req = urllib.request.Request(
        GIST_API_URL,
        data=payload,
        method="PATCH",
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print(f"[Gist] 해시 업로드 완료: {version} → {sha256_hash}")
                return True
            print(f"[경고] Gist 응답 코드: {resp.status}")
            return False
    except Exception as exc:
        print(f"[경고] Gist 업로드 실패: {exc}")
        return False


def _update_manifest(version, installer_path=None):
    manifest = _load_manifest()
    manifest["version"] = version
    manifest["latest_version"] = version
    manifest.setdefault("release_notes", "")
    manifest.setdefault("download_url", "")
    manifest.setdefault("installer_url", "")
    manifest["installer_name"] = os.path.basename(installer_path) if installer_path else ""
    _save_manifest(manifest)
    _sync_manifest_to_dist()


def main():
    current = _read_current_version()
    print(f"\n현재 버전: v{current}")

    ans = input("버전을 올리겠습니까? [y/N]: ").strip().lower()
    if ans == "y":
        suggested = _bump_patch(current)
        new_version = input(f"새 버전 [{suggested}]: ").strip() or suggested
        _write_version(new_version)
        print(f"[버전] v{current} -> v{new_version}")
        current = new_version

    exe_path = _build_exe()

    print("\n[해시] SHA-256 계산 중..")
    exe_hash = _compute_sha256(exe_path)
    print(f"[해시] {exe_hash}")
    _upload_hash_to_gist(exe_hash, current)

    installer = _build_installer(current)
    _update_manifest(current, installer)
    print(f"[리소스] {MANIFEST_FILE} 업데이트 완료")

    print(f"\n빌드 완료: v{current}")
    if installer:
        print(f"  인스톨러: {installer}")
    print(f"  exe:      {exe_path}")


if __name__ == "__main__":
    main()
