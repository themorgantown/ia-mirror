#!/usr/bin/env python3
"""
Dependency check script for ia-mirror project.
Checks for updates to the internetarchive PyPI package and the current Python
Docker base image declared in docker/Dockerfile.
If updates are available, updates docker/Dockerfile and docker/requirements.txt.
Shows desktop notification on macOS.
"""

import json
import re
import sys
import os
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta

# Project root (one level up from scripts directory)
PROJECT_ROOT = Path(__file__).parent.parent
DOCKERFILE_PATH = PROJECT_ROOT / "docker" / "Dockerfile"
REQUIREMENTS_PATH = PROJECT_ROOT / "docker" / "requirements.txt"

# Docker tag parsing and comparison
class PythonVersion:
    """Simple Python version parser for Docker tags."""
    def __init__(self, tag: str):
        self.tag = tag
        # Parse version from tag like "3.13-slim", "3.13.5-slim", "3.13-slim-bullseye"
        self.major = 0
        self.minor = 0
        self.patch = 0
        self.suffix = ""
        self._parse(tag)

    def _parse(self, tag: str):
        # Remove suffixes after slim
        if '-slim' in tag:
            base = tag.split('-slim')[0]
            self.suffix = '-slim' + tag.split('-slim', 1)[1] if '-slim' in tag and tag.split('-slim', 1)[1] else '-slim'
        else:
            base = tag
            self.suffix = ""

        # Parse version numbers
        parts = base.split('.')
        try:
            self.major = int(parts[0]) if len(parts) > 0 else 0
            self.minor = int(parts[1]) if len(parts) > 1 else 0
            self.patch = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            # Handle non-numeric versions (like "latest")
            pass

    def __str__(self):
        return self.tag

    def __repr__(self):
        return f"PythonVersion({self.tag})"

    def __lt__(self, other: 'PythonVersion') -> bool:
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        return self.patch < other.patch

    def __eq__(self, other: 'PythonVersion') -> bool:
        return (self.major == other.major and
                self.minor == other.minor and
                self.patch == other.patch)

    def is_same_major_minor(self, other: 'PythonVersion') -> bool:
        return self.major == other.major and self.minor == other.minor

    def to_tag(self) -> str:
        """Convert version back to Docker tag format."""
        version_str = f"{self.major}.{self.minor}"
        if self.patch > 0:
            version_str += f".{self.patch}"
        return version_str + self.suffix

def get_current_versions() -> Tuple[Optional[str], Optional[str]]:
    """Extract current versions from Dockerfile and requirements.txt."""
    ia_version_docker = None
    ia_version_req = None

    # Read Dockerfile
    if DOCKERFILE_PATH.exists():
        with open(DOCKERFILE_PATH, 'r') as f:
            content = f.read()
            # Match ARG IA_PYPI_VERSION="X.X.X"
            match = re.search(r'ARG IA_PYPI_VERSION="([^"]+)"', content)
            if match:
                ia_version_docker = match.group(1)

    # Read requirements.txt
    if REQUIREMENTS_PATH.exists():
        with open(REQUIREMENTS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith("internetarchive=="):
                    ia_version_req = line.split("==")[1]
                    break

    return ia_version_docker, ia_version_req

def get_current_python_tag() -> Optional[str]:
    """Extract current Python Docker tag from Dockerfile."""
    if not DOCKERFILE_PATH.exists():
        return None

    with open(DOCKERFILE_PATH, 'r') as f:
        content = f.read()
        # Match FROM python:X.Y.Z-slim or python:X.Y-slim
        match = re.search(r'FROM python:([^\s]+)', content)
        if match:
            return match.group(1)
    return None

def get_latest_pypi_version(package_name: str) -> Optional[str]:
    """Fetch latest version of a package from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
            return data.get("info", {}).get("version")
    except urllib.error.URLError as e:
        sys.stderr.write(f"Error fetching PyPI data: {e}\n")
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Error parsing PyPI response: {e}\n")
    except Exception as e:
        sys.stderr.write(f"Unexpected error: {e}\n")
    return None

def fetch_docker_tags(image: str, limit: int = 100) -> List[Dict]:
    """
    Fetch Docker tags from Docker Hub API with pagination.
    Returns list of tag objects.
    """
    tags = []
    url = f"https://hub.docker.com/v2/repositories/library/{image}/tags/?page_size={min(limit, 100)}"

    try:
        while url and len(tags) < limit:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.load(response)
                tags.extend(data.get("results", []))
                url = data.get("next") if len(tags) < limit else None
    except Exception as e:
        sys.stderr.write(f"Error fetching Docker tags: {e}\n")

    return tags[:limit]

def get_recommended_python_upgrade(current_tag: str) -> Optional[str]:
    """
    Analyze available Python Docker tags and recommend an upgrade.
    Returns recommended tag if an upgrade is advised, None otherwise.
    """
    # Fetch all Python tags
    tags = fetch_docker_tags("python", limit=200)
    if not tags:
        return None

    # Filter for slim tags and parse versions
    slim_versions = []
    for tag_obj in tags:
        tag_name = tag_obj.get("name", "")
        if "-slim" in tag_name and not any(x in tag_name for x in ["alpine", "window", "nanoserver"]):
            try:
                version = PythonVersion(tag_name)
                if version.major > 0:  # Valid version
                    slim_versions.append((version, tag_name, tag_obj.get("last_updated", "")))
            except Exception:
                continue

    if not slim_versions:
        return None

    # Parse current version
    current_version = PythonVersion(current_tag)

    # Group by major.minor
    from collections import defaultdict
    version_groups = defaultdict(list)
    for version, tag_name, last_updated in slim_versions:
        key = (version.major, version.minor)
        version_groups[key].append((version, tag_name, last_updated))

    # Find latest patch for current major.minor
    current_key = (current_version.major, current_version.minor)
    if current_key in version_groups:
        current_group = version_groups[current_key]
        # Sort by patch descending
        current_group.sort(key=lambda x: x[0].patch, reverse=True)
        latest_in_group = current_group[0][0]
        if latest_in_group.patch > current_version.patch:
            # New patch available in same minor version
            return current_group[0][1]

    # Check for newer minor versions (same major)
    newer_minors = []
    for (major, minor), versions in version_groups.items():
        if major == current_version.major and minor > current_version.minor:
            # Get latest patch for this minor
            versions.sort(key=lambda x: x[0].patch, reverse=True)
            newest = versions[0]
            newer_minors.append((minor, newest))

    if newer_minors:
        # Sort by minor version, take highest
        newer_minors.sort(key=lambda x: x[0], reverse=True)
        # For now, recommend latest minor (could add stability check here)
        return newer_minors[0][1][1]

    # Check for newer major versions (conservative: maybe not auto-upgrade)
    newer_majors = []
    for (major, minor), versions in version_groups.items():
        if major > current_version.major:
            versions.sort(key=lambda x: x[0].patch, reverse=True)
            newest = versions[0]
            newer_majors.append((major, newest))

    if newer_majors:
        # Major version upgrade - log but don't auto-recommend
        sys.stderr.write(f"New major Python version available: {newer_majors[0][1][1]}\n")
        # Could add policy: if major version has been out for >180 days, recommend

    return None

def get_docker_tag_version(image: str, tag: str) -> Optional[str]:
    """
    Check if a Docker tag exists and get its digest.
    Returns the tag if it exists (we just want to know if it exists).
    Actually, we want to know if there's a newer tag like 3.13-slim-YYYYMMDD or newer patch.
    For simplicity, we'll check if the tag exists (i.e., image is still maintained).
    We could also check for newer Python version (3.14, etc.) but that's a major update.
    """
    # Docker Hub API v2
    # https://hub.docker.com/v2/repositories/library/{image}/tags/{tag}
    url = f"https://hub.docker.com/v2/repositories/library/{image}/tags/{tag}/"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
            # If we get a 200, tag exists
            return tag
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.stderr.write(f"Docker tag {image}:{tag} not found (may have been updated)\n")
            return None
        else:
            sys.stderr.write(f"Error checking Docker tag: {e}\n")
    except Exception as e:
        sys.stderr.write(f"Unexpected error checking Docker tag: {e}\n")
    return None

def update_dockerfile(new_version: str) -> bool:
    """Update ARG IA_PYPI_VERSION in Dockerfile."""
    try:
        with open(DOCKERFILE_PATH, 'r') as f:
            content = f.read()

        # Replace ARG IA_PYPI_VERSION="X.X.X"
        new_content = re.sub(
            r'(ARG IA_PYPI_VERSION=")[^"]+(")',
            rf'\g<1>{new_version}\g<2>',
            content
        )

        if new_content != content:
            with open(DOCKERFILE_PATH, 'w') as f:
                f.write(new_content)
            print(f"Updated Dockerfile IA_PYPI_VERSION to {new_version}")
            return True
        else:
            sys.stderr.write("Dockerfile version unchanged\n")
            return False
    except Exception as e:
        sys.stderr.write(f"Error updating Dockerfile: {e}\n")
        return False

def update_requirements(new_version: str) -> bool:
    """Update internetarchive version in requirements.txt."""
    try:
        with open(REQUIREMENTS_PATH, 'r') as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.strip().startswith("internetarchive=="):
                lines[i] = f"internetarchive=={new_version}\n"
                updated = True
                break

        if updated:
            with open(REQUIREMENTS_PATH, 'w') as f:
                f.writelines(lines)
            print(f"Updated requirements.txt internetarchive to {new_version}")
            return True
        else:
            sys.stderr.write("Could not find internetarchive line in requirements.txt\n")
            return False
    except Exception as e:
        sys.stderr.write(f"Error updating requirements.txt: {e}\n")
        return False

def show_notification(title: str, message: str) -> None:
    """Show desktop notification on macOS."""
    if sys.platform == "darwin":
        script = f'display notification "{message}" with title "{title}"'
        try:
            subprocess.run(["osascript", "-e", script], check=False)
        except Exception as e:
            sys.stderr.write(f"Failed to show notification: {e}\n")
    else:
        sys.stderr.write(f"Notification not supported on {sys.platform}\n")
        sys.stderr.write(f"{title}: {message}\n")

def main():
    sys.stderr.write("Checking for dependency updates...\n")

    # Get current versions
    docker_version, req_version = get_current_versions()
    current_python_tag = get_current_python_tag()
    sys.stderr.write(f"Current Dockerfile IA_PYPI_VERSION: {docker_version}\n")
    sys.stderr.write(f"Current requirements.txt internetarchive: {req_version}\n")
    sys.stderr.write(f"Current Docker base image: python:{current_python_tag}\n")

    # Check PyPI for latest internetarchive
    latest_ia = get_latest_pypi_version("internetarchive")
    sys.stderr.write(f"Latest PyPI internetarchive version: {latest_ia}\n")

    recommended_python_tag = None
    docker_status = "unknown"
    if current_python_tag:
        docker_tag_exists = get_docker_tag_version("python", current_python_tag)
        docker_status = "exists" if docker_tag_exists else "not found"
        sys.stderr.write(f"Docker tag python:{current_python_tag}: {docker_status}\n")
        recommended_python_tag = get_recommended_python_upgrade(current_python_tag)
        if recommended_python_tag and recommended_python_tag != current_python_tag:
            sys.stderr.write(
                f"New Python base image available: {current_python_tag} -> {recommended_python_tag}\n"
            )
    else:
        docker_tag_exists = None
        sys.stderr.write("Could not determine current Python Docker tag from Dockerfile.\n")

    updates_made = False
    notification_messages = []

    # Update internetarchive if newer version available
    if latest_ia and req_version and latest_ia != req_version:
        # Compare version strings (simple)
        # For proper version comparison, we could use packaging.version
        # but for simplicity, we'll assume if strings differ, it's an update
        sys.stderr.write(f"New internetarchive version available: {req_version} -> {latest_ia}\n")
        if update_requirements(latest_ia):
            updates_made = True
            notification_messages.append(f"internetarchive updated to {latest_ia}")
        # Also update Dockerfile ARG if it's different
        if docker_version and latest_ia != docker_version:
            if update_dockerfile(latest_ia):
                updates_made = True
    elif docker_version and latest_ia and docker_version != latest_ia:
        # Dockerfile version might be out of sync with requirements
        sys.stderr.write(f"Dockerfile IA_PYPI_VERSION ({docker_version}) differs from latest ({latest_ia})\n")
        if update_dockerfile(latest_ia):
            updates_made = True
            notification_messages.append(f"Dockerfile IA_PYPI_VERSION updated to {latest_ia}")

    if current_python_tag and not docker_tag_exists:
        notification_messages.append(f"Docker base image python:{current_python_tag} may need update")
    elif recommended_python_tag and recommended_python_tag != current_python_tag:
        notification_messages.append(
            f"Docker base image upgrade available: python:{current_python_tag} -> python:{recommended_python_tag}"
        )

    if updates_made:
        print("Updates were made to project files.")
        show_notification(
            "ia-mirror Dependencies Updated",
            ", ".join(notification_messages)
        )
    else:
        sys.stderr.write("No updates needed.\n")
        show_notification(
            "ia-mirror Dependency Check",
            "All dependencies are up to date."
        )

if __name__ == "__main__":
    main()