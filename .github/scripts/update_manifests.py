import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]


def version_tuple(value):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("Only stable semantic versions are supported")
    return tuple(map(int, value.split(".")))


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def release_info(repo, tag):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CapsDeck-manifests",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Could not read release (HTTP {error.code})") from None
    except urllib.error.URLError:
        raise RuntimeError("Could not reach GitHub API") from None
    if release["tag_name"] != tag or release["draft"] or release["prerelease"]:
        raise ValueError("Manifest updates require a published stable release")
    return {asset["name"]: asset for asset in release["assets"]}


def checksum(asset, repo, tag, name):
    expected = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    if asset["browser_download_url"] != expected:
        raise ValueError(f"Unexpected download URL for {name}")
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(expected, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Could not download {name} (HTTP {error.code})") from None
    except urllib.error.URLError:
        raise RuntimeError(f"Could not download {name}") from None
    if size == 0 or size != asset["size"]:
        raise ValueError(f"Downloaded file is incomplete: {name}")
    value = digest.hexdigest()
    if asset.get("digest") and asset["digest"] != "sha256:" + value:
        raise ValueError(f"Downloaded checksum does not match GitHub: {name}")
    return value


def main():
    tag = os.environ["RELEASE_TAG"]
    if not tag.startswith("v"):
        raise ValueError("Release tag must start with v")
    version = tag[1:]
    requested = version_tuple(version)
    repo = os.environ["RELEASE_REPO"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid release repository")
    scoop_path = ROOT / "bucket/capsdeck.json"
    scoop = json.loads(scoop_path.read_text(encoding="utf-8"))
    if requested < version_tuple(scoop["version"]):
        output(changed="false", tag=tag, status="Skipped older release")
        return
    assets = release_info(repo, tag)
    name = f"CapsDeck_{version}_x64-portable.zip"
    if name not in assets:
        raise ValueError(f"Release is missing {name}")
    package = scoop["architecture"]["64bit"]
    package["hash"] = checksum(assets[name], repo, tag, name)
    package["url"] = assets[name]["browser_download_url"]
    scoop["version"] = version
    new_scoop = json.dumps(scoop, indent=4, ensure_ascii=False) + "\n"
    changed = new_scoop != scoop_path.read_text(encoding="utf-8")
    if changed:
        scoop_path.write_text(new_scoop, encoding="utf-8", newline="\n")
    output(changed=str(changed).lower(), tag=tag, status="Updated" if changed else "Already current")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from None
