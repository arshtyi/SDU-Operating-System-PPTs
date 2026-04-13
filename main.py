import json
import os
from pathlib import Path
import re
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

    raise ValueError("AUTHORIZATION not found in environment or .env file")


AUTHORIZATION = read_authorization_from_env()

HEADERS = {
    "Authorization": AUTHORIZATION,
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}

PAYLOAD = {
    "secretStr": "UxP3XcazdgPcsmuCl4WFhwJBKnvsXQ57zO5KMTiqQg2tBjGU9+fUAnw/zb1Lz955GAM1ZtsrD0QmJGIPuCwHqmtmcDiq7aXV9rZcT0kpGyzQ6qF1A508LPaRHxcSg//lh3sCT9Fy6scRPLmX7bOHmY/vxePtA+q2LqH6laOxAm0AsQEpgxL4ffExTeinidzI",
    "date": 1775196956865,
}

OUT_FILE = Path("stu_resources_response.json")
DOWNLOAD_DIR = Path("text")


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


def main() -> None:
    disable_warnings(InsecureRequestWarning)

    response = requests.post(
        URL,
        headers=HEADERS,
        json=PAYLOAD,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    data = response.json()
    OUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"saved: {OUT_FILE}")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    items = data.get("data", {}).get("list", [])
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


if __name__ == "__main__":
    main()
