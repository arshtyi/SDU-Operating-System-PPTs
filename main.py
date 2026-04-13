import base64
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse
import requests
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


URL = "https://kg-run-student.zhihuishu.com/student/gateway/t/stu/resources/v4/list/stu-resources"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12; BVL-AN16 Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/110.0.5481.154 Mobile Safari/537.36; zhihuishu"
)
LOGIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
JWT_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
LOGIN_PAGE = (
    "https://passport.zhihuishu.com/login"
    "?service=https://onlineservice-api.zhihuishu.com/login/gologin"
)
QR_PAGE = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg"
QR_QUERY_PAGE = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"
QR_IMAGE_FILE = Path("qr_login.png")
RESOURCE_PAYLOAD = {
    "secretStr": "UxP3XcazdgPcsmuCl4WFhwJBKnvsXQ57zO5KMTiqQg2tBjGU9+fUAnw/zb1Lz955GAM1ZtsrD0QmJGIPuCwHqmtmcDiq7aXV9rZcT0kpGyzQ6qF1A508LPaRHxcSg//lh3sCT9Fy6scRPLmX7bOHmY/vxePtA+q2LqH6laOxAm0AsQEpgxL4ffExTeinidzI",
    "date": 1775196956865,
}
DOWNLOAD_DIR = Path("text")
SUMMARY_FILE = Path("download_summary.md")
disable_warnings(InsecureRequestWarning)


def read_authorization_from_env() -> str:
    token = os.getenv("AUTHORIZATION", "").strip()
    if token:
        return token

    env_file = Path(".env")
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "AUTHORIZATION":
                continue
            return value.strip().strip('"').strip("'")

    return ""


def upsert_authorization_to_env(token: str) -> None:
    env_file = Path(".env")
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()

    replaced = False
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _ = line.split("=", 1)
        if key.strip() == "AUTHORIZATION":
            lines[idx] = f'AUTHORIZATION="{token}"'
            replaced = True
            break

    if not replaced:
        lines.append(f'AUTHORIZATION="{token}"')

    env_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out.extend(_iter_strings(k))
            out.extend(_iter_strings(v))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_strings(item))
        return out
    return []


def _extract_jwt_candidates_from_value(value: Any) -> list[str]:
    found: list[str] = []
    for text in _iter_strings(value):
        found.extend(JWT_PATTERN.findall(text))
    return found


def _extract_jwt_from_response(response: requests.Response) -> str:
    for candidate in _extract_jwt_candidates_from_response(response):
        return candidate
    return ""


def _extract_jwt_candidates_from_response(response: requests.Response) -> list[str]:
    candidates: list[str] = []
    for _, value in response.headers.items():
        candidates.extend(_extract_jwt_candidates_from_value(value))

    for cookie in response.cookies:
        candidates.extend(_extract_jwt_candidates_from_value(cookie.value))

    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    candidates.extend(_extract_jwt_candidates_from_value(payload))
    return list(dict.fromkeys(candidates))


def _extract_qr_json_field(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        nested = payload.get("data")
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return default


def _is_valid_resource_authorization(token: str) -> bool:
    if not JWT_PATTERN.fullmatch(token):
        return False
    try:
        response = requests.post(
            URL,
            headers={
                "Authorization": token,
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
            },
            json=RESOURCE_PAYLOAD,
            timeout=20,
            verify=False,
        )
    except requests.RequestException:
        return False

    if response.status_code != 200:
        return False
    try:
        data = response.json()
    except ValueError:
        return False

    return data.get("code") == 200


def _discover_authorization(session: requests.Session) -> str:
    test_calls: list[tuple[str, str, dict[str, str] | None]] = [
        ("GET", "https://onlineservice-api.zhihuishu.com/login/getLoginUserInfo", None),
        (
            "GET",
            "https://onlineservice-api.zhihuishu.com/gateway/f/v1/login/getLoginUserInfo",
            None,
        ),
        (
            "GET",
            "https://onlineservice-api.zhihuishu.com/login/gologin",
            {"fromurl": "https://onlineweb.zhihuishu.com/"},
        ),
    ]

    candidates: list[str] = []

    # First pass: token might already be inside cookie values.
    for cookie in session.cookies:
        candidates.extend(_extract_jwt_candidates_from_value(cookie.value))

    for method, url, params in test_calls:
        try:
            response = session.request(
                method=method,
                url=url,
                params=params,
                timeout=15,
                allow_redirects=True,
                verify=False,
            )
        except requests.RequestException:
            continue

        for item in [*response.history, response]:
            candidates.extend(_extract_jwt_candidates_from_response(item))

    for token in dict.fromkeys(candidates):
        if _is_valid_resource_authorization(token):
            return token

    return ""


def _cleanup_qr_file() -> None:
    if QR_IMAGE_FILE.exists():
        QR_IMAGE_FILE.unlink()


def qr_login_get_authorization() -> str:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": LOGIN_USER_AGENT,
            "Origin": "https://passport.zhihuishu.com",
            "Referer": LOGIN_PAGE,
            "Accept": "*/*",
        }
    )

    session.get(LOGIN_PAGE, timeout=15, verify=False)
    qr_data = session.get(QR_PAGE, timeout=15, verify=False).json()
    qr_token = _extract_qr_json_field(qr_data, "qrToken")
    qr_img_b64 = _extract_qr_json_field(qr_data, "img")
    if not qr_token or not qr_img_b64:
        raise RuntimeError(f"Failed to get QR payload: {qr_data}")

    qr_img_bytes = base64.b64decode(qr_img_b64)
    QR_IMAGE_FILE.write_bytes(qr_img_bytes)
    print(f"QR saved to: {QR_IMAGE_FILE.resolve()}")
    print("Please scan and confirm login in Zhihuishu app.")

    scanned = False
    for _ in range(360):
        time.sleep(0.5)
        qr_status_data = session.get(
            QR_QUERY_PAGE,
            params={"qrToken": qr_token},
            timeout=15,
            verify=False,
        ).json()
        status = _extract_qr_json_field(qr_status_data, "status", -1)
        message = _extract_qr_json_field(qr_status_data, "msg", "")

        if status == -1:
            continue
        if status == 0:
            if not scanned:
                scanned = True
                print("QR scanned, waiting for confirmation...")
            continue
        if status == 1:
            once_password = _extract_qr_json_field(qr_status_data, "oncePassword")
            if not once_password:
                raise RuntimeError(f"QR confirmed but no oncePassword: {qr_status_data}")

            login_response = session.get(
                LOGIN_PAGE,
                params={"pwd": once_password},
                timeout=20,
                allow_redirects=True,
                verify=False,
            )

            for item in [*login_response.history, login_response]:
                token = _extract_jwt_from_response(item)
                if token and _is_valid_resource_authorization(token):
                    _cleanup_qr_file()
                    return token

            token = _discover_authorization(session)
            if token:
                _cleanup_qr_file()
                return token

            raise RuntimeError("QR login succeeded, but AUTHORIZATION token was not found")
        if status == 2:
            raise RuntimeError(f"QR expired: {message}")
        if status == 3:
            raise RuntimeError(f"QR canceled: {message}")
        raise RuntimeError(f"Unknown QR status: {status}, payload={qr_status_data}")

    raise RuntimeError("QR login timeout")


def get_authorization() -> str:
    token = read_authorization_from_env()
    if token:
        return token

    print("AUTHORIZATION not found, starting QR login...")
    token = qr_login_get_authorization()
    upsert_authorization_to_env(token)
    masked = f"{token[:12]}...{token[-8:]}" if len(token) > 24 else "***"
    print(f"AUTHORIZATION captured: {masked}")
    print("AUTHORIZATION has been written to .env")
    return token


AUTHORIZATION = get_authorization()

HEADERS = {
    "Authorization": AUTHORIZATION,
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}

PAYLOAD = RESOURCE_PAYLOAD


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned or "unnamed"


def guess_filename(item: dict) -> str:
    name = str(item.get("resourcesName") or "").strip()
    if name:
        return safe_filename(name)
    url = str(item.get("resourcesUrl") or "")
    path_name = Path(urlparse(url).path).name
    if path_name:
        return safe_filename(path_name)
    return "resource.bin"


def download_file(url: str, dest: Path) -> None:
    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=60,
        verify=False,
    ) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def write_summary_markdown(records: list[dict[str, str]]) -> None:
    lines = [
        "# Download Summary",
        "",
        f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Total downloaded: `{len(records)}`",
        "",
        "| # | Resource Name | Saved As | Size | URL |",
        "|---|---|---|---|---|",
    ]

    for idx, rec in enumerate(records, start=1):
        resource_name = rec["resource_name"].replace("|", "\\|")
        saved_as = rec["saved_as"].replace("|", "\\|")
        size = rec["size"].replace("|", "\\|")
        url = rec["url"].replace("|", "\\|")
        lines.append(
            f"| {idx} | {resource_name} | `{saved_as}` | {size} | {url} |"
        )

    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    response = requests.post(
        URL,
        headers=HEADERS,
        json=PAYLOAD,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    data = response.json()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    items = data.get("data", {}).get("list", [])
    records: list[dict[str, str]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("resourcesUrl") or "").strip()
        if not url:
            continue
        filename = guess_filename(item)
        target = DOWNLOAD_DIR / filename
        download_file(url, target)
        print(f"[{idx}/{len(items)}] downloaded: {target}")
        records.append(
            {
                "resource_name": str(item.get("resourcesName") or filename),
                "saved_as": str(target),
                "size": str(item.get("resourcesSize") or "-"),
                "url": url,
            }
        )

    write_summary_markdown(records)
    print(f"summary saved: {SUMMARY_FILE}")


if __name__ == "__main__":
    main()
