"""Check the old image against the release lock before touching a deployment."""

import json
import platform
import re
import sys
from importlib.metadata import distributions, version

manifest = json.load(sys.stdin)
assert version("ark-gacha-simulator") == manifest["base_version"], "Wrong base image version"
assert platform.machine() == "x86_64" and sys.version_info[:2] == (3, 11), (
    "Requires amd64 Python 3.11"
)
for distribution in distributions():
    name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
    if name == "ark-gacha-simulator":
        continue
    assert name in manifest["locked_versions"], f"Unexpected base dependency: {name}"
    assert distribution.version == manifest["locked_versions"][name], f"Dependency drift: {name}"
print("Base image version, architecture and installed dependency versions verified.")
