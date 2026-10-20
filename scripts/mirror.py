
import os
import re
import hashlib
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

START = "https://storage.googleapis.com/yubico/study.html"
OUT = "mirror"
ALLOWED_HOST = urlparse(START).hostname
MAX_FILES = 500
MAX_BYTES = 25 * 1024 * 1024

session = requests.Session()
session.headers["User-Agent"] = "WebsiteMirror/1.0 (static asset collector)"
seen = set()
queue = deque([START])
url_to_path = {}


def local_path(url, content_type=""):
    parsed = urlparse(url)
    path = parsed.path.lstrip("/") or "index.html"

    if path.endswith("/"):
        path += "index.html"

    if parsed.query:
        suffix = hashlib.sha256(
            parsed.query.encode()
        ).hexdigest()[:10]
        root, ext = os.path.splitext(path)
        path = f"{root}_{suffix}{ext}"

    if not os.path.splitext(path)[1]:
        if "text/css" in content_type:
            path += ".css"
        else:
            path += ".bin"

    return path


def save_file(path, content):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full) or OUT, exist_ok=True)

    with open(full, "wb") as f:
        f.write(content)


def download(url):
    try:
        response = session.get(
            url, timeout=30, stream=True
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].lower()

        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                print("Skipped oversized file:", url)
                return None

        return bytes(data), content_type
    except requests.RequestException as exc:
        print("Download failed:", url, exc)
        return None


def is_local(url):
    parsed = urlparse(url)
    return (
        parsed.scheme in ("http", "https")
        and parsed.hostname == ALLOWED_HOST
    )


os.makedirs(OUT, exist_ok=True)

while queue and len(seen) < MAX_FILES:
    url = queue.popleft()
    url, _ = urldefrag(url)

    if url in seen or not is_local(url):
        continue

    seen.add(url)
    result = download(url)

    if result is None:
        continue

    data, content_type = result
    path = local_path(url, content_type)
    url_to_path[url] = path

    if content_type in ("text/html", "application/xhtml+xml"):
        soup = BeautifulSoup(data, "html.parser")

        # Collect common HTML resource references.
        attrs = [
            ("script", "src"),
            ("link", "href"),
            ("img", "src"),
            ("source", "src"),
            ("video", "src"),
            ("audio", "src"),
            ("iframe", "src"),
        ]

        for tag_name, attr in attrs:
            for tag in soup.find_all(tag_name):
                ref = tag.get(attr)
                if not ref:
                    continue

                absolute = urldefrag(urljoin(url, ref))[0]
                if is_local(absolute) and absolute not in seen:
                    queue.append(absolute)

        # Preserve query strings in local filenames where possible.
        for tag_name, attr in attrs:
            for tag in soup.find_all(tag_name):
                ref = tag.get(attr)
                if not ref:
                    continue
                absolute = urldefrag(urljoin(url, ref))[0]
                if absolute in url_to_path:
                    tag[attr] = "/" + url_to_path[absolute]

        save_file(path, soup.encode("utf-8"))

    elif content_type == "text/css":
        css = data.decode("utf-8", errors="replace")

        refs = re.findall(
            r'url\(\s*["\']?(.*?)["\']?\s*\)'
            r'|@import\s+["\'](.*?)["\']',
            css,
            re.IGNORECASE,
        )

        for first, second in refs:
            ref = first or second
            if not ref or ref.startswith("data:"):
                continue

            absolute = urldefrag(urljoin(url, ref))[0]
            if is_local(absolute) and absolute not in seen:
                queue.append(absolute)

        save_file(path, data)

    else:
        save_file(path, data)

    print("Saved:", url, "->", path)

print(f"Collected {len(seen)} URLs.")
print("Review mirror/ for missing or externally hosted resources.")
