import base64
import os
import re
import time
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse
import requests
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


RESOURCE_URL = "https://kg-run-student.zhihuishu.com/student/gateway/t/stu/resources/v4/list/stu-resources"
LOGIN_PAGE = (
    "https://passport.zhihuishu.com/login"
    "?service=https://onlineservice-api.zhihuishu.com/login/gologin"
)
QR_IMAGE_ENDPOINT = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg"
QR_STATUS_ENDPOINT = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"

ENV_FILE = Path(".env")
QR_IMAGE_FILE = Path("qr_login.png")
DOWNLOAD_DIR = Path("text")
SUMMARY_FILE = Path("download_summary.md")

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
RESOURCE_PAYLOAD = {
    "secretStr": "UxP3XcazdgPcsmuCl4WFhwJBKnvsXQ57zO5KMTiqQg2tBjGU9+fUAnw/zb1Lz955GAM1ZtsrD0QmJGIPuCwHqmtmcDiq7aXV9rZcT0kpGyzQ6qF1A508LPaRHxcSg//lh3sCT9Fy6scRPLmX7bOHmY/vxePtA+q2LqH6laOxAm0AsQEpgxL4ffExTeinidzI",
    "date": 1775196956865,
}

LOGIN_QUERY_ENDPOINTS: list[tuple[str, str, dict[str, str] | None]] = [
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

REQUEST_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 60
QR_POLL_INTERVAL_SECONDS = 0.5
QR_POLL_MAX_ROUNDS = 360


class DownloadRecord(TypedDict):
    resource_name: str
    saved_as: str
    size: str
    url: str


disable_warnings(InsecureRequestWarning)


def build_resource_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }


def read_authorization_from_env() -> str:
    token = os.getenv("AUTHORIZATION", "").strip()
    if token:
        return token

    if not ENV_FILE.exists():
        return ""

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "AUTHORIZATION":
            return value.strip().strip('"').strip("'")
    return ""


def upsert_authorization_to_env(token: str) -> None:
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

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

    ENV_FILE.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out.extend(iter_strings(k))
            out.extend(iter_strings(v))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(iter_strings(item))
        return out
    return []


def extract_jwt_candidates_from_value(value: Any) -> list[str]:
    found: list[str] = []
    for text in iter_strings(value):
        found.extend(JWT_PATTERN.findall(text))
    return found


def extract_jwt_candidates_from_response(response: requests.Response) -> list[str]:
    candidates: list[str] = []

    for _, value in response.headers.items():
        candidates.extend(extract_jwt_candidates_from_value(value))
    for cookie in response.cookies:
        candidates.extend(extract_jwt_candidates_from_value(cookie.value))

    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    candidates.extend(extract_jwt_candidates_from_value(payload))

    return list(dict.fromkeys(candidates))


def extract_first_jwt_from_response(response: requests.Response) -> str:
    for candidate in extract_jwt_candidates_from_response(response):
        return candidate
    return ""


def extract_qr_json_field(payload: Any, key: str, default: Any = None) -> Any:
    if not isinstance(payload, dict):
        return default
    if key in payload:
        return payload[key]
    nested = payload.get("data")
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    return default


def request_resources(token: str) -> dict[str, Any]:
    response = requests.post(
        RESOURCE_URL,
        headers=build_resource_headers(token),
        json=RESOURCE_PAYLOAD,
        timeout=REQUEST_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    return response.json()


def is_valid_resource_authorization(token: str) -> bool:
    if not JWT_PATTERN.fullmatch(token):
        return False
    try:
        data = request_resources(token)
    except (requests.RequestException, ValueError):
        return False
    return data.get("code") == 200


def discover_authorization(session: requests.Session) -> str:
    candidates: list[str] = []

    for cookie in session.cookies:
        candidates.extend(extract_jwt_candidates_from_value(cookie.value))

    for method, url, params in LOGIN_QUERY_ENDPOINTS:
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
            candidates.extend(extract_jwt_candidates_from_response(item))

    for token in dict.fromkeys(candidates):
        if is_valid_resource_authorization(token):
            return token
    return ""


def cleanup_qr_file() -> None:
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
    qr_data = session.get(QR_IMAGE_ENDPOINT, timeout=15, verify=False).json()
    qr_token = extract_qr_json_field(qr_data, "qrToken")
    qr_img_b64 = extract_qr_json_field(qr_data, "img")
    if not qr_token or not qr_img_b64:
        raise RuntimeError(f"Failed to get QR payload: {qr_data}")

    QR_IMAGE_FILE.write_bytes(base64.b64decode(qr_img_b64))
    print(f"QR saved to: {QR_IMAGE_FILE.resolve()}")
    print("Please scan and confirm login in Zhihuishu app.")

    scanned = False
    for _ in range(QR_POLL_MAX_ROUNDS):
        time.sleep(QR_POLL_INTERVAL_SECONDS)
        qr_status_data = session.get(
            QR_STATUS_ENDPOINT,
            params={"qrToken": qr_token},
            timeout=15,
            verify=False,
        ).json()
        status = extract_qr_json_field(qr_status_data, "status", -1)
        message = extract_qr_json_field(qr_status_data, "msg", "")

        if status == -1:
            continue
        if status == 0:
            if not scanned:
                scanned = True
                print("QR scanned, waiting for confirmation...")
            continue
        if status == 1:
            once_password = extract_qr_json_field(qr_status_data, "oncePassword")
            if not once_password:
                raise RuntimeError(
                    f"QR confirmed but no oncePassword: {qr_status_data}"
                )

            login_response = session.get(
                LOGIN_PAGE,
                params={"pwd": once_password},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                verify=False,
            )

            for item in [*login_response.history, login_response]:
                token = extract_first_jwt_from_response(item)
                if token and is_valid_resource_authorization(token):
                    cleanup_qr_file()
                    return token

            token = discover_authorization(session)
            if token:
                cleanup_qr_file()
                return token

            raise RuntimeError(
                "QR login succeeded, but AUTHORIZATION token was not found"
            )
        if status == 2:
            raise RuntimeError(f"QR expired: {message}")
        if status == 3:
            raise RuntimeError(f"QR canceled: {message}")
        raise RuntimeError(f"Unknown QR status: {status}, payload={qr_status_data}")

    raise RuntimeError("QR login timeout")


def get_authorization() -> str:
    token = read_authorization_from_env()
    if token and is_valid_resource_authorization(token):
        return token

    if token:
        print(
            "Existing AUTHORIZATION in .env is invalid or expired, re-login required."
        )
    print("Starting QR login...")

    token = qr_login_get_authorization()
    upsert_authorization_to_env(token)
    masked = f"{token[:12]}...{token[-8:]}" if len(token) > 24 else "***"
    print(f"AUTHORIZATION captured: {masked}")
    print("AUTHORIZATION has been written to .env")
    return token


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned or "unnamed"


def trim_filename_stem(raw_name: str, trim_chars: int = 5) -> str:
    path_obj = Path(raw_name)
    stem = path_obj.stem
    suffix = path_obj.suffix
    if len(stem) > trim_chars:
        stem = stem[:-trim_chars]
    else:
        stem = "unnamed"
    merged = f"{stem}{suffix}"
    return safe_filename(merged)


def guess_filename(item: dict[str, Any]) -> str:
    name = str(item.get("resourcesName") or "").strip()
    if name:
        return trim_filename_stem(name)
    url = str(item.get("resourcesUrl") or "")
    path_name = Path(urlparse(url).path).name
    if path_name:
        return trim_filename_stem(path_name)
    return "resource.bin"


def download_file(url: str, dest: Path) -> None:
    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=DOWNLOAD_TIMEOUT,
        verify=False,
    ) as response:
        response.raise_for_status()
        with dest.open("wb") as file_handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    file_handle.write(chunk)


def human_size(size_raw: str) -> str:
    try:
        size = float(size_raw)
    except (TypeError, ValueError):
        return size_raw or "-"

    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1

    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def write_summary_markdown(records: list[DownloadRecord]) -> None:
    lines = [
        "# Download Summary",
        "",
        f"- Generated at: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Total downloaded: `{len(records)}`",
        "",
        "| # | Resource Name | Saved As | Size | URL |",
        "|:---:|:---:|:---:|:---:|:---:|",
    ]

    for idx, rec in enumerate(records, start=1):
        resource_name = rec["resource_name"].replace("|", "\\|")
        saved_as = rec["saved_as"].replace("|", "\\|")
        size = human_size(rec["size"]).replace("|", "\\|")
        url = rec["url"].replace("|", "\\|")
        lines.append(f"| {idx} | {resource_name} | `{saved_as}` | {size} | {url} |")

    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    token = get_authorization()
    data = request_resources(token)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    items = data.get("data", {}).get("list", [])
    records: list[DownloadRecord] = []

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
