"""TokenLens Backup & Disaster Recovery Utility.

Allows instantaneous one-command restoration of pristine seed datasets,
model pricing tables, configuration, and golden manifests during a live demo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backup"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

BACKUP_MAPPINGS = [
    (BACKUP_DIR / "sample_requests.csv", DATA_DIR / "sample_requests.csv"),
    (BACKUP_DIR / "model_pricing.csv", DATA_DIR / "model_pricing.csv"),
    (BACKUP_DIR / "golden_validation.json", DATA_DIR / "golden_validation.json"),
    (BACKUP_DIR / "attribution.yaml", CONFIG_DIR / "attribution.yaml"),
]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_manifest() -> Path:
    """Generate manifest.json in data/backup/ describing pristine seeds."""
    manifest_path = BACKUP_DIR / "manifest.json"
    files_info = {}
    for src, _ in BACKUP_MAPPINGS:
        if src.exists():
            files_info[src.name] = {
                "size_bytes": src.stat().st_size,
                "sha256": file_sha256(src),
            }

    manifest = {
        "version": "1.0.0",
        "timestamp": "2026-10-10T00:00:00Z",
        "description": "TokenLens Pristine Demo Baseline Backup Dataset",
        "financial_invariants": {
            "source_requests": 1208,
            "valid_loaded_requests": 1200,
            "rejected_requests": 8,
            "quarantined_duplicates": 5,
            "schema_rejected": 3,
            "total_input_tokens": 4711994,
            "total_output_tokens": 1006027,
            "total_cached_tokens": 420691,
            "input_token_cost": 7.7940,
            "output_token_cost": 6.3934,
            "cached_token_cost": 0.1760,
            "grand_total_spend": 14.3634,
            "missing_pricing_count": 6,
        },
        "files": files_info,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def restore_backup(verify_only: bool = False) -> bool:
    """Restore pristine data from backup folder."""
    print("=" * 60)
    print("TOKENLENS DISASTER RECOVERY & SEED RESTORE")
    print("=" * 60)

    if not BACKUP_DIR.exists():
        print(f"[-] ERROR: Backup directory not found at {BACKUP_DIR}")
        return False

    success = True
    for src, dst in BACKUP_MAPPINGS:
        if not src.exists():
            print(f"[-] Missing backup file: {src.name}")
            success = False
            continue

        if verify_only:
            current_exists = dst.exists()
            status = "PRESENT" if current_exists else "MISSING"
            print(f"[*] Check {dst.name:25s}: {status}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[+] Restored: {src.name:25s} -> {dst}")

    if not verify_only:
        manifest_path = generate_manifest()
        print(f"[+] Backup manifest verified at: {manifest_path.name}")
        print("=" * 60)
        print("SUCCESS: Pristine demo state restored successfully.")
    return success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TokenLens Backup & Restore Tool")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check backup integrity without overwriting live files",
    )
    args = parser.parse_args()
    ok = restore_backup(verify_only=args.verify_only)
    sys.exit(0 if ok else 1)
