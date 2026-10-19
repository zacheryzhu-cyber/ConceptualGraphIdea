"""One-time setup: fetch Microsoft's Azure P&ID sample code (MIT licence) into pdf_pipeline/azure_pid.

Uses the exact version this PoC was tested with, and makes one mechanical change: the sample was
written for pydantic 1, while pyDEXPI and RF-DETR use pydantic 2, so `from pydantic import` becomes
`from pydantic.v1 import` (pydantic 2's built-in compatibility layer). Nothing else is changed.

    python -m pdf_pipeline.vendor_azure                    (downloads from GitHub)
    python -m pdf_pipeline.vendor_azure --from-dir PATH    (use a folder you cloned/unzipped yourself)
"""
import argparse
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile

COMMIT = "c51302e3ec34147c676ef6eedbdd7551ea908b05"
REPO = "Azure-Samples/digitization-of-piping-and-instrument-diagrams"
URL = f"https://github.com/{REPO}/archive/{COMMIT}.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "azure_pid")


def fetch(tmp):
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as r:
        data = r.read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    return os.path.join(tmp, f"digitization-of-piping-and-instrument-diagrams-{COMMIT}")


def install(repo_dir):
    src = os.path.join(repo_dir, "src")
    if not os.path.isdir(os.path.join(src, "app")):
        raise SystemExit(f"Not the Azure sample: {src}/app not found")
    if os.path.exists(TARGET):
        shutil.rmtree(TARGET)
    shutil.copytree(os.path.join(src, "app"), os.path.join(TARGET, "app"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(src, "logger_config.py"), TARGET)
    shutil.copy(os.path.join(repo_dir, "LICENSE.md"), TARGET)
    changed = 0
    for root, _, files in os.walk(TARGET):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if "from pydantic import" in text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text.replace("from pydantic import", "from pydantic.v1 import"))
                changed += 1
    with open(os.path.join(TARGET, "SOURCE.txt"), "w", encoding="utf-8") as f:
        f.write(f"https://github.com/{REPO} at commit {COMMIT}\n"
                f"Copied: src/app, src/logger_config.py, LICENSE.md (MIT)\n"
                f"Changed: 'from pydantic import' -> 'from pydantic.v1 import' in {changed} files\n")
    print(f"Installed Azure sample code into {TARGET} ({changed} files switched to pydantic.v1)")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", help="folder of a clone or unzipped copy of the Azure sample")
    args = ap.parse_args()
    if args.from_dir:
        install(args.from_dir)
        return
    with tempfile.TemporaryDirectory() as tmp:
        install(fetch(tmp))


if __name__ == "__main__":
    main()
