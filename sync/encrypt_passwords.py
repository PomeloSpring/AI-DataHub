#!/usr/bin/env python3
"""
Migration script: encrypt existing plaintext datasource passwords.

Run this once after deploying the password encryption feature.
All existing plaintext passwords in adh_datasources will be encrypted.

Usage:
    cd chatbi-app/sync
    python encrypt_passwords.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, METADATA_DB_DATABASE,
)
from services.shared.common.crypto import encrypt_password, is_encrypted


def migrate():
    """Encrypt all plaintext passwords in adh_datasources table."""
    print(f"Connecting to database at {DORIS_HOST}:{DORIS_PORT}...")

    conn = pymysql.connect(
        host=DORIS_HOST,
        port=DORIS_PORT,
        user=DORIS_USER,
        password=DORIS_PASSWORD,
        database=METADATA_DB_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )

    try:
        with conn.cursor() as cur:
            # Check if table exists
            cur.execute("SHOW TABLES LIKE 'adh_datasources'")
            if not cur.fetchone():
                print("Table adh_datasources does not exist. Nothing to migrate.")
                return

            cur.execute("SELECT id, name, password FROM adh_datasources")
            rows = cur.fetchall()

            if not rows:
                print("No datasources found. Nothing to migrate.")
                return

            encrypted_count = 0
            skipped_count = 0

            for row in rows:
                if is_encrypted(row["password"]):
                    print(f"  [{row['id']}] {row['name']}: already encrypted, skipping")
                    skipped_count += 1
                else:
                    encrypted = encrypt_password(row["password"])
                    cur.execute(
                        "UPDATE adh_datasources SET password = %s WHERE id = %s",
                        (encrypted, row["id"]),
                    )
                    print(f"  [{row['id']}] {row['name']}: encrypted successfully")
                    encrypted_count += 1

            conn.commit()

            print(f"\nMigration complete!")
            print(f"  Encrypted: {encrypted_count}")
            print(f"  Skipped (already encrypted): {skipped_count}")

    finally:
        conn.close()


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"\nError during migration: {e}", file=sys.stderr)
        sys.exit(1)
