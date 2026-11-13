#!/usr/bin/env python3
"""Create a source-only mainline driver archive and verify its exact contents."""

from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from emdoorctl import MAINLINE_VERSION as VERSION

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "driver"
FILES = ("emdoor-wmi.c", "Makefile", "dkms.conf", "Kconfig", "README.md", "ABI.md", "COPYING")


def main():
    dist = BASE / "dist"
    dist.mkdir(exist_ok=True)
    destination = dist / f"emdoor-wmi-{VERSION}.tar.gz"
    prefix = f"emdoor-wmi-{VERSION}"
    manifest = {}
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in FILES:
                    path = SOURCE / name
                    if path.is_symlink() or not path.is_file():
                        raise RuntimeError(f"Expected an ordinary reviewed source file: {path}")
                    data = path.read_bytes()
                    manifest[name] = hashlib.sha256(data).hexdigest()
                    entry = tarfile.TarInfo(f"{prefix}/{name}")
                    entry.size = len(data)
                    entry.mode = 0o644
                    archive.addfile(entry, io.BytesIO(data))
    with tarfile.open(destination, "r:gz") as archive:
        assert sorted(archive.getnames()) == sorted(f"{prefix}/{name}" for name in FILES)
        for name, expected in manifest.items():
            member = archive.getmember(f"{prefix}/{name}")
            assert member.isfile()
            assert hashlib.sha256(archive.extractfile(member).read()).hexdigest() == expected
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".gz.sha256").write_text(f"{digest}  {destination.name}\n")
    report = {"version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_archive": str(destination), "sha256": digest, "source_manifest": manifest,
              "contains_binaries_or_vendor_programs": False}
    (dist / f"emdoor-wmi-{VERSION}-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: {destination} (7 exact source files, SHA-256 verified)")


if __name__ == "__main__":
    main()
