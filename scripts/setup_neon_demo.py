"""Create and seed a new Neon demo database from the supplied local CSV files."""

import os
from pathlib import Path
from typing import Final

import psycopg
from psycopg import Connection

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR: Final = Path(__file__).resolve().parent / "migrations"
CSV_FILES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "customers",
        "id, customer_id, full_name, email, phone, date_of_birth, "
        "customer_tier, status, created_at",
        "customers.csv",
    ),
    (
        "customer_credentials",
        "id, customer_id, password_hash, is_active, created_at",
        "customer_credentials.csv",
    ),
    (
        "accounts",
        "id, account_id, customer_id, account_type, account_number, balance, "
        "currency, status, created_at",
        "accounts.csv",
    ),
    (
        "transactions",
        "id, transaction_id, account_id, transaction_type, amount, merchant, "
        "category, transaction_date, status, description",
        "transactions.csv",
    ),
    (
        "service_requests",
        "id, request_id, customer_id, request_type, status, created_at, "
        "completed_at, metadata",
        "service_requests.csv",
    ),
    (
        "kyc_records",
        "id, customer_id, verification_status, verification_level, verified_at",
        "kyc_records.csv",
    ),
)
SEQUENCE_TABLES: Final[tuple[str, ...]] = (
    "customers",
    "customer_credentials",
    "accounts",
    "transactions",
    "service_requests",
    "kyc_records",
)
APPLICATION_TABLES: Final[tuple[str, ...]] = (
    "customers",
    "customer_credentials",
    "accounts",
    "transactions",
    "service_requests",
    "kyc_records",
    "daily_account_limits",
    "daily_limit_change_requests",
    "transfer_requests",
    "service_request_proposals",
)


def _execute_sql_file(connection: Connection, path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip().upper() != "BEGIN;" or lines[-1].strip().upper() != "COMMIT;":
        raise RuntimeError(f"Expected explicit transaction wrapper in {path.name}")
    with connection.cursor() as cursor:
        cursor.execute("\n".join(lines[1:-1]), prepare=False)


def _assert_empty_application_schema(connection: Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (list(APPLICATION_TABLES),),
        )
        existing = sorted(row[0] for row in cursor.fetchall())
    if existing:
        names = ", ".join(existing)
        raise RuntimeError(
            "Refusing to seed a database that already contains application "
            f"tables: {names}"
        )


def _copy_csv(
    connection: Connection,
    csv_dir: Path,
    table: str,
    columns: str,
    filename: str,
) -> None:
    csv_path = csv_dir / filename
    if not csv_path.is_file():
        raise FileNotFoundError(f"Required demo CSV file not found: {csv_path}")
    command = (
        f"COPY public.{table} ({columns}) "
        "FROM STDIN WITH (FORMAT CSV, HEADER TRUE)"
    )
    with connection.cursor() as cursor, csv_path.open("rb") as source:
        with cursor.copy(command) as copy:
            while chunk := source.read(1024 * 1024):
                copy.write(chunk)
        print(f"Imported {filename} into {table}.")


def _synchronize_sequences(connection: Connection) -> None:
    for table in SEQUENCE_TABLES:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('public.{table}', 'id'),
                    COALESCE(MAX(id), 1),
                    MAX(id) IS NOT NULL
                )
                FROM public.{table}
                """
            )


def setup_demo_database(database_url: str, csv_dir: Path) -> None:
    """Build the full schema, import all fixtures, and apply both workflow migrations."""
    with psycopg.connect(database_url) as connection:
        _assert_empty_application_schema(connection)
        connection.commit()
        with connection.transaction():
            print("Creating the base banking schema...")
            _execute_sql_file(connection, MIGRATIONS_DIR / "000_demo_schema.sql")

            print(f"Importing fixture CSVs from {csv_dir}...")
            for table, columns, filename in CSV_FILES:
                _copy_csv(connection, csv_dir, table, columns, filename)

            print("Synchronizing imported identity sequences...")
            _synchronize_sequences(connection)

            print("Ensuring a same-customer transfer demo account exists...")
            _execute_sql_file(
                connection,
                PROJECT_ROOT / "scripts" / "seeds" / "demo_transfer_account.sql",
            )

            print("Applying daily-limit and write-command migrations...")
            _execute_sql_file(
                connection,
                MIGRATIONS_DIR / "001_daily_limit_workflow.sql",
            )
            _execute_sql_file(
                connection,
                MIGRATIONS_DIR / "002_banking_commands.sql",
            )
            _execute_sql_file(
                connection,
                MIGRATIONS_DIR / "003_chat_sessions.sql",
            )
        print("Neon demo database setup complete.")


def main() -> None:
    database_url = os.environ.get("NEON_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Set NEON_DATABASE_URL to a standard postgresql:// connection URL."
        )
    csv_dir = Path(
        os.environ.get("DEMO_CSV_DIR", str(Path.home() / "Downloads"))
    ).expanduser()
    if not csv_dir.is_dir():
        raise SystemExit(f"CSV directory does not exist: {csv_dir}")
    setup_demo_database(database_url, csv_dir)


if __name__ == "__main__":
    main()
