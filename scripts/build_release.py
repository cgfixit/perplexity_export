#!/usr/bin/env python3
"""Build a deterministic source ZIP from an explicit non-secret file allowlist."""
import argparse
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("pplx_export.py", "live_verify.py", "public_verify.py", "requirements.txt", "requirements-ci.txt",
         "README.md", "USER_GUIDE.md", "CI.md", ".gitignore", "test_export.py",
         "test_ci.py", "test_resilience.py", "scripts/build_release.py",
         "scripts/verify_release.py", "tests/__init__.py", "tests/test_distribution.py",
         "tests/test_public.py")


def atomic_bytes(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build(root, output):
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "perplexity_export.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=archive.name + ".", suffix=".tmp", dir=output)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name in FILES:
                path = root / name
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Refusing package input outside the source directory")
                data = path.read_bytes()
                info = zipfile.ZipInfo("perplexity_export/" + name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                package.writestr(info, data)
        with open(temporary, "r+b") as package:
            os.fsync(package.fileno())
        checksum = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        os.replace(temporary, archive)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    atomic_bytes(output / "SHA256SUMS.txt", f"{checksum}  {archive.name}\n".encode())
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(ROOT, args.output).name)
