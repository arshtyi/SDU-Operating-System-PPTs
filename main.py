import json
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
AUTHORIZATION = (
    "eyJraWQiOiJFRjRGMjJDMC01Q0IwLTQzNDgtOTY3Qi0wMjY0OTVFN0VGQzgiLCJhbGciOiJFUzI1NiJ9."
    "eyJpc3MiOiJjb20uemhpaHVpc2h1IiwiYXVkIjoiWkRfQSIsInN1YiI6IuW9remdlui9qSIsImlhdCI6"
    "MTc3NTE5NjY5NywiZXhwIjoxNzc1MjA3NDk3LCJqdGkiOiIzZTExZDc1NC0zNTdjLTRmOGYtOTVkNC03"
    "MTczYzA4YWRlNzgiLCJ1aWQiOjg2NDU3NTE5OX0.brLxdFZeCIK4KL-caf0APEz5sK_5Iq4sOyre5ZFpp"
    "MrCgMGxDMlV6MhKw7qThhGE7Ib50CV3XUkEeBlgf2eavg"
)

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


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


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
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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
        target = unique_path(DOWNLOAD_DIR / filename)
        download_file(url, target)
        print(f"[{idx}/{len(items)}] downloaded: {target}")


if __name__ == "__main__":
    main()
