"""Interactive CLI for reviewers to exercise the Geo Measure API.

Prereqs:
  1. Start the server:  uvicorn app.main:app --reload
  2. Run this script:   python test.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
last_file_id: str | None = None

ROUTES = [
    ("1", "GET  /health", "Health check"),
    ("2", "POST /api/files/", "Upload a .zip (Shapefile) or .kml file"),
    ("3", "GET  /api/files/{id}/", "File information"),
    ("4", "GET  /api/files/{id}/features/", "List extracted features"),
    ("5", "GET  /api/files/{id}/measurements/", "Per-feature area / length measurements"),
    ("q", "Quit", "Exit this menu"),
]


def show_menu() -> None:
    print("\n" + "=" * 60)
    print(f"  Geo Measure API  —  {BASE_URL}")
    if last_file_id:
        print(f"  Last uploaded file id: {last_file_id}")
    print("=" * 60)
    for key, route, desc in ROUTES:
        print(f"  [{key}]  {route:<32}  {desc}")
    print("=" * 60)


def print_response(r: requests.Response) -> None:
    print(f"\n← {r.status_code} {r.reason}")
    try:
        body = r.json()
        print(json.dumps(body, indent=2))
    except ValueError:
        print(r.text or "(empty body)")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_file_id() -> str | None:
    fid = ask("File id", last_file_id)
    if not fid:
        print("! File id is required.")
        return None
    return fid


def ask_pagination() -> dict:
    limit = ask("limit", "1000")
    offset = ask("offset", "0")
    params: dict = {}
    if limit:
        params["limit"] = limit
    if offset:
        params["offset"] = offset
    return params


def do_health() -> None:
    print_response(requests.get(f"{BASE_URL}/health"))


def do_upload() -> None:
    global last_file_id
    path_str = ask("Path to .kml or .zip file", "samples/survey.kml")
    path = Path(path_str)
    if not path.is_file():
        print(f"! File not found: {path.resolve()}")
        return

    default_crs = ask("default_crs (optional, e.g. EPSG:4326 — Enter to skip)", "")
    form = {"default_crs": default_crs} if default_crs else None

    print(f"→ Uploading {path.name} …")
    with path.open("rb") as f:
        r = requests.post(
            f"{BASE_URL}/api/files/",
            files={"file": (path.name, f)},
            data=form,
        )
    print_response(r)

    try:
        body = r.json()
        if "id" in body:
            last_file_id = body["id"]
            print(f"\n(saved file id for next calls: {last_file_id})")
    except ValueError:
        pass


def do_file_info() -> None:
    fid = ask_file_id()
    if not fid:
        return
    print_response(requests.get(f"{BASE_URL}/api/files/{fid}/"))


def do_features() -> None:
    fid = ask_file_id()
    if not fid:
        return
    params = ask_pagination()
    print_response(requests.get(f"{BASE_URL}/api/files/{fid}/features/", params=params))


def do_measurements() -> None:
    fid = ask_file_id()
    if not fid:
        return
    params = ask_pagination()
    print_response(requests.get(f"{BASE_URL}/api/files/{fid}/measurements/", params=params))


HANDLERS = {
    "1": do_health,
    "2": do_upload,
    "3": do_file_info,
    "4": do_features,
    "5": do_measurements,
}


def main() -> None:
    print("Interactive Geo Measure API tester")
    print("Make sure the server is running:  uvicorn app.main:app --reload")
    while True:
        show_menu()
        choice = input("Select an option: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            print("Bye.")
            break
        handler = HANDLERS.get(choice)
        if handler is None:
            print(f"! Unknown option: {choice}")
            continue
        try:
            handler()
        except requests.ConnectionError:
            print(f"\n! Cannot reach {BASE_URL}. Is uvicorn running?")
        except KeyboardInterrupt:
            print("\n(cancelled)")
        except Exception as exc:  # noqa: BLE001 — show anything unexpected to the reviewer
            print(f"\n! Error: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
        sys.exit(0)
