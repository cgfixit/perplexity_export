#!/usr/bin/env python3
"""Verify a built source ZIP against its checksum and explicit file allowlist."""
import argparse
import hashlib
import stat
import zipfile
from pathlib import Path

try:
    from scripts.build_release import FILES
except ModuleNotFoundError:  # Direct `python scripts/verify_release.py ...` launch.
    from build_release import FILES


def verify(archive, checksum_file):
    archive = Path(archive)
    checksum_file = Path(checksum_file)
    lines = [line.strip() for line in checksum_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Checksum file must contain exactly one entry")
    expected, separator, name = lines[0].partition("  ")
    if not separator or name != archive.name:
        raise ValueError("Checksum entry does not name the archive")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("Archive checksum does not match the checksum file")

    expected_names = {"perplexity_export/" + name for name in FILES}
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if set(names) != expected_names or len(names) != len(expected_names):
            raise ValueError("Archive members do not match the source allowlist")
        for info in package.infolist():
            mode = info.external_attr >> 16
            if not stat.S_ISREG(mode):
                raise ValueError("Archive member is not a regular file")
            # Read every member to validate decompression and CRC, not just names.
            package.read(info)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("checksum", type=Path)
    args = parser.parse_args()
    verify(args.archive, args.checksum)
    print("Release artifact verified.")
