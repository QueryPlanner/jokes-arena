"""Import a private generation receipt without replacing existing votes."""
import argparse
import os
import tempfile
from pathlib import Path
from arena.config import load_config, active_profiles
from arena.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    profiles = active_profiles(load_config())
    if args.database.exists():
        count = Store(args.database, profiles).import_receipt(args.receipt)
    else:
        descriptor, filename = tempfile.mkstemp(prefix='.arena-seed-', dir=args.database.parent)
        os.close(descriptor)
        staging = Path(filename)
        try:
            store = Store(staging, profiles)
            count = store.import_receipt(args.receipt)
            if not store.readiness():
                raise ValueError('The seed must contain at least one active pair')
            with store.connection() as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            # Exclusive publication also protects a database created concurrently.
            os.link(staging, args.database)
        finally:
            staging.unlink()
            for suffix in ('-wal', '-shm'):
                Path(filename + suffix).unlink(missing_ok=True)
    print(f'Imported {count} complete pairs; existing votes preserved.')


if __name__ == '__main__':
    main()
