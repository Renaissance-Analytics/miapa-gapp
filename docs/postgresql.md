# Running an install on PostgreSQL

An install keeps its records in the SQLite file `maipa.sqlite3` in its data folder unless its own start
command opts in to PostgreSQL. Connection variables supplied by the hosting environment never switch an
install by themselves.

## Opt in

Set both in the install's start command, next to `MAIPA_DATA_DIR`:

- `MAIPA_DATABASE=postgres`
- `MAIPA_DATABASE_NAME=<this install's own database>`: lower-case letters, digits and underscores.
  Each install needs its own database; two installs must never share one.

`PGHOST`, `PGPORT`, `PGUSER` and `PGPASSWORD` come from the hosting environment. A missing or malformed
value stops the server with a configuration error. It never falls back to SQLite, and the error never
contains a connection value.

## Move an existing install

With the site stopped:

1. Create the empty database.
2. `python backend/manage.py migrate` with the opt-in set.
3. `python backend/manage.py copy_database --source sqlite_file --target default`

The copy runs in one transaction, resets id sequences, and compares every model in both databases by row
count and by a hash of every row. It prints model names and counts only, and ends with `IDENTICAL`. If
anything differs, or the target already holds records, nothing is written. `--verify-only` repeats the
comparison at any time before the install is used again.

The SQLite file is only read. It stays in the data folder as the fallback: removing the two variables
returns the install to it, without anything recorded since the move.

## Back up

The backup tool (`backend/install.py`) copies the data folder, including `maipa.sqlite3`. For an install on
PostgreSQL that file is stale unless it is refreshed first. With the site stopped:

1. `python backend/manage.py export_database_file`
2. Run the backup as usual.

The export builds a complete SQLite database from the current records under a temporary name, verifies it
row by row, and only then replaces `maipa.sqlite3`. A failed export leaves the previous file untouched.

**The backup tool does not check that the export was run.** Skipping step 1 produces a backup of old
records without any warning. This is a known gap.

## Restore

Restore the data folder with the backup tool, create a new empty database, then repeat steps 2 and 3 of
"Move an existing install". The copy refuses a database that already holds records.

## What is and is not verified

- The backend test suite passes on PostgreSQL 18, apart from the process-level workflow tests and the
  install storage tests, which use a SQLite file by design.
- The real-browser suite runs on SQLite only.
- Where SQLite made a second simultaneous request wait and then return the first one's result, PostgreSQL
  can refuse it as already decided. Nothing runs twice.
