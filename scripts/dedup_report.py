"""Print a read-only summary of the persistent image fingerprint registry."""

from __future__ import annotations

import sqlite3

from config.settings import settings


def main() -> None:
    database = settings.resolved_dedup_database_path
    if not database.exists():
        print(f"No deduplication database exists yet: {database}")
        return
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT source, COUNT(*), SUM(perceptual_hash IS NOT NULL) "
            "FROM image_fingerprints GROUP BY source ORDER BY source"
        ).fetchall()
    print(f"Deduplication registry: {database}")
    if not rows:
        print("No canonical images have been registered.")
        return
    for source, total, with_phash in rows:
        print(f"{source}: canonical={total}, with_phash={with_phash}")


if __name__ == "__main__":
    main()

