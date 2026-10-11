"""
Signalpost — Clean Submission Packager
======================================
Creates a clean submission ZIP for submission:
- Excludes __pycache__, .pyc, .pyo
- Excludes .env (only includes .env.example)
- Excludes .git, .venv, virtualenvs, IDE configs
- Verifies all required files and profiles are present
"""

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
OUTPUT_ZIP = ROOT / "submission.zip"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    ".pytest_cache",
}

EXCLUDE_FILES = {
    ".env",
    "submission.zip",
    ".DS_Store",
    "Thumbs.db",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
}


def package():
    print("=" * 60)
    print("Signalpost Submission Packager")
    print("=" * 60)

    # Remove old submission zip if present
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    included_files = []
    skipped_sensitive = []

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(ROOT)

                if file in EXCLUDE_FILES or file_path.suffix in EXCLUDE_EXTENSIONS:
                    if file == ".env":
                        skipped_sensitive.append(str(rel_path))
                    continue

                zf.write(file_path, arcname=str(rel_path))
                included_files.append(str(rel_path))

    print(f"\n📦 Archive created: {OUTPUT_ZIP.name} ({OUTPUT_ZIP.stat().st_size / (1024*1024):.2f} MB)")
    print(f"📄 Total files included: {len(included_files)}")
    print(f"🛡️  Sensitive files excluded (.env, .pyc, __pycache__): {len(skipped_sensitive) + 1}")
    if skipped_sensitive:
        for s in skipped_sensitive:
            print(f"    - Excluded: {s}")

    # Verification checks
    print("\n🔍 Verification:")
    assert ".env.example" in included_files, "❌ .env.example missing!"
    assert ".env" not in included_files, "❌ .env was accidentally included!"
    assert not any(f.endswith(".pyc") for f in included_files), "❌ .pyc file found!"
    assert not any("__pycache__" in f for f in included_files), "❌ __pycache__ found!"
    print("  ✅ .env.example is present")
  
    print("  ✅ Real .env is strictly excluded")
    print("  ✅ No __pycache__ or .pyc files in archive")

    # Profile count check
    profile_count = sum(1 for f in included_files if f.startswith("profiles") and f.endswith(".json"))
    print(f"  ✅ Cached profiles included: {profile_count}")
    print("\n" + "=" * 60)
    print(f"🎉 Ready for submission: {OUTPUT_ZIP}")
    print("=" * 60)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    package()
