#!/usr/bin/env python3
"""Digest-preserving Docker Hub -> GHCR mirror for the configured Mihomo tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


USER_AGENT = "mihomo-ghcr-mirror/1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_changed(path: Path, value: dict[str, Any]) -> bool:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return True


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def docker_hub_tags(repository: str, tag_pattern: re.Pattern[str]) -> dict[str, str]:
    repository = repository.removeprefix("docker.io/")
    url = f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100"
    tags: dict[str, str] = {}
    while url:
        payload = fetch_json(url)
        for item in payload.get("results", []):
            name = item.get("name")
            digest = item.get("digest")
            if isinstance(name, str) and isinstance(digest, str) and tag_pattern.fullmatch(name):
                tags[name] = digest
        url = payload.get("next")
    return tags


def run_skopeo(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["skopeo", *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown skopeo error"
        raise RuntimeError(f"skopeo {' '.join(arguments)} failed: {detail}")
    return result


def inspect_digest(reference: str, authfile: Path | None = None) -> str | None:
    arguments = ["inspect"]
    if authfile is not None:
        arguments += ["--authfile", str(authfile)]
    arguments += ["--format", "{{.Digest}}", f"docker://{reference}"]
    result = run_skopeo(arguments, check=False)
    if result.returncode != 0:
        return None
    digest = result.stdout.strip()
    return digest or None


def inspect_raw(reference: str, authfile: Path | None = None) -> dict[str, Any]:
    arguments = ["inspect"]
    if authfile is not None:
        arguments += ["--authfile", str(authfile)]
    arguments += ["--raw", f"docker://{reference}"]
    result = run_skopeo(arguments)
    return json.loads(result.stdout)


def manifest_platforms(manifest: dict[str, Any]) -> set[str]:
    platforms: set[str] = set()
    for child in manifest.get("manifests", []):
        platform = child.get("platform") or {}
        operating_system = platform.get("os")
        architecture = platform.get("architecture")
        variant = platform.get("variant")
        if (
            not operating_system
            or not architecture
            or operating_system == "unknown"
            or architecture == "unknown"
        ):
            continue
        name = f"{operating_system}/{architecture}"
        if variant:
            name += f"/{variant}"
        platforms.add(name)
    return platforms


def verify_platforms(reference: str, expected: set[str], authfile: Path | None = None) -> set[str]:
    actual = manifest_platforms(inspect_raw(reference, authfile))
    if actual != expected:
        raise RuntimeError(
            f"unexpected platforms for {reference}: expected={sorted(expected)} actual={sorted(actual)}"
        )
    return actual


def copy_image(source: str, destination: str, authfile: Path, attempts: int = 3) -> None:
    arguments = [
        "copy",
        "--all",
        "--preserve-digests",
        "--dest-authfile",
        str(authfile),
        f"docker://{source}",
        f"docker://{destination}",
    ]
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        result = run_skopeo(arguments, check=False)
        if result.returncode == 0:
            return
        last_error = result.stderr.strip() or result.stdout.strip() or last_error
        if attempt < attempts:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"copy failed after {attempts} attempts: {last_error}")


def last_commit_epoch(path: Path) -> int | None:
    try:
        git_path = str(path.relative_to(Path.cwd()))
    except ValueError:
        git_path = str(path)
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", git_path],
        text=True,
        capture_output=True,
        check=False,
    )
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None


def maybe_refresh_heartbeat(path: Path, heartbeat_days: int) -> bool:
    last = last_commit_epoch(path)
    now = int(time.time())
    if last is not None and now - last < heartbeat_days * 86400:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"Last successful mirror verification: {iso_now()}\n",
        encoding="utf-8",
    )
    return True


def version_key(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        raise ValueError(f"not a stable version tag: {tag}")
    return tuple(int(part) for part in match.groups())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--authfile", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = load_json(args.config)
    source = config["source"]
    destination = config["destination"]
    tag_pattern = re.compile(config["tag_pattern"])
    immutable_tag_pattern = re.compile(
        config.get("immutable_tag_pattern", r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    )
    expected_platforms = set(config["expected_platforms"])
    heartbeat_days = int(config["heartbeat_days"])
    state_path = root / "state/mirror.json"
    heartbeat_path = root / "maintenance/heartbeat.txt"
    state = load_json(state_path) if state_path.exists() else {
        "source": source,
        "destination": destination,
        "tags": {},
    }
    state.setdefault("tags", {})

    tags = docker_hub_tags(source, tag_pattern)
    if not tags:
        raise RuntimeError("Docker Hub returned no tags matching the configured policy")
    newest_tag = max(tags, key=version_key)
    tags = {newest_tag: tags[newest_tag]}
    print(f"SELECTED_TAG {newest_tag}")

    state["tags"] = {
        newest_tag: state["tags"][newest_tag]
    } if newest_tag in state["tags"] else {}
    changed_tags: list[str] = []
    for tag in sorted(tags):
        source_digest = tags[tag]
        source_ref = f"{source}@{source_digest}"
        destination_ref = f"{destination}:{tag}"

        verify_platforms(source_ref, expected_platforms)
        destination_digest = inspect_digest(destination_ref, args.authfile)
        previous = state["tags"].get(tag, {})
        immutable = immutable_tag_pattern.fullmatch(tag) is not None

        if destination_digest == source_digest:
            print(f"UNCHANGED {tag} {source_digest}")
            if previous.get("destination_digest") != destination_digest:
                state["tags"][tag] = {
                    "source_digest": source_digest,
                    "destination_digest": destination_digest,
                    "platforms": sorted(expected_platforms),
                    "synced_at": previous.get("synced_at", iso_now()),
                }
            continue

        if immutable and destination_digest is not None and not previous.get("source_digest"):
            raise RuntimeError(
                f"destination already has a different digest for untracked immutable tag {tag}: "
                f"destination={destination_digest} source={source_digest}"
            )

        if immutable and previous.get("source_digest") and previous["source_digest"] != source_digest:
            raise RuntimeError(
                f"source digest changed for existing immutable tag {tag}: "
                f"previous={previous['source_digest']} current={source_digest}"
            )

        print(f"COPY {tag} {source_digest}")
        copy_image(source_ref, destination_ref, args.authfile)
        destination_digest = inspect_digest(destination_ref, args.authfile)
        if destination_digest != source_digest:
            raise RuntimeError(
                f"digest mismatch for {tag}: source={source_digest} destination={destination_digest}"
            )
        verify_platforms(destination_ref, expected_platforms, args.authfile)
        state["tags"][tag] = {
            "source_digest": source_digest,
            "destination_digest": destination_digest,
            "platforms": sorted(expected_platforms),
            "synced_at": iso_now(),
        }
        changed_tags.append(tag)

    state_changed = write_json_if_changed(state_path, state)
    heartbeat_changed = maybe_refresh_heartbeat(heartbeat_path, heartbeat_days)

    print(f"STABLE_TAG_COUNT {len(tags)}")
    print(f"CHANGED_TAGS {','.join(changed_tags) if changed_tags else 'none'}")
    print(f"STATE_CHANGED {str(state_changed).lower()}")
    print(f"HEARTBEAT_CHANGED {str(heartbeat_changed).lower()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
