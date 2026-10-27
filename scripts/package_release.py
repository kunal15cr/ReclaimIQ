"""Package the exact committed source and its checksum for local distribution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / "ReclaimIQ-source.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", "--output", str(archive), "HEAD"],
        cwd=ROOT,
        check=True,
    )
    with ZipFile(archive) as package:
        if any(
            Path(name).name in {".env", ".env.local"} for name in package.namelist()
        ):
            archive.unlink()
            raise RuntimeError(
                "refusing to distribute tracked runtime environment files"
            )
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    metadata = {
        "project": "ReclaimIQ",
        "commit": commit,
        "ref": os.getenv("GITHUB_REF_NAME", "local"),
        "artifact": "committed source; no runtime secrets or deployment",
    }
    (output / "BUILD.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Packaged committed source {commit[:12]} in dist/{archive.name} with SHA-256 checksum."
    )


if __name__ == "__main__":
    main()
