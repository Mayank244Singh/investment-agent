"""Add provider observations and temporal mappings without replacing daily bars.

Revision ID: 0004_market_data_foundation
Revises: 0003_daily_prices
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_market_data_foundation"
down_revision: str | None = "0003_daily_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep old keys and values; backfill only facts that were actually stored."""
    op.create_table(
        "instrument_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_instrument_key", sa.String(255)),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("series", sa.String(8), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("identity_status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "length(provider) BETWEEN 1 AND 64", name="ck_mappings_provider"
        ),
        sa.CheckConstraint(
            "length(symbol) BETWEEN 1 AND 20", name="ck_mappings_symbol"
        ),
        sa.CheckConstraint("symbol = upper(symbol)", name="ck_mappings_upper_symbol"),
        sa.CheckConstraint(
            "exchange = upper(exchange)", name="ck_mappings_upper_exchange"
        ),
        sa.CheckConstraint("series = 'EQ'", name="ck_mappings_eq_only"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_mappings_interval",
        ),
        sa.CheckConstraint(
            "identity_status IN ('legacy_unverified', 'verified')",
            name="ck_mappings_status",
        ),
    )
    op.create_index(
        "ix_instrument_mappings_lookup",
        "instrument_mappings",
        ["provider", "exchange", "series", "symbol"],
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("source_sha256", sa.String(64)),
        sa.Column("import_batch_id", sa.Integer(), unique=True),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "provenance_kind",
            sa.String(16),
            server_default=sa.text("'source'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"], ["import_batches.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "length(provider) BETWEEN 1 AND 64", name="ck_runs_provider"
        ),
        sa.CheckConstraint("source_kind IN ('zip', 'provider')", name="ck_runs_kind"),
        sa.CheckConstraint("length(source_ref) BETWEEN 1 AND 512", name="ck_runs_ref"),
        sa.CheckConstraint(
            "source_sha256 IS NULL OR length(source_sha256) = 64",
            name="ck_runs_source_hash",
        ),
        sa.CheckConstraint(
            "provenance_kind IN ('source', 'reconstructed')",
            name="ck_runs_provenance",
        ),
    )
    op.create_table(
        "source_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_instrument_key", sa.String(255)),
        sa.Column("source_record_key", sa.String(512), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("series", sa.String(8), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("event_at_utc", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at_utc", sa.DateTime(timezone=True)),
        sa.Column("available_at_utc", sa.DateTime(timezone=True)),
        sa.Column(
            "ingested_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("open_paise", sa.BigInteger(), nullable=False),
        sa.Column("high_paise", sa.BigInteger(), nullable=False),
        sa.Column("low_paise", sa.BigInteger(), nullable=False),
        sa.Column("close_paise", sa.BigInteger(), nullable=False),
        sa.Column("previous_close_paise", sa.BigInteger()),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("reconciliation", sa.String(16), nullable=False),
        sa.Column("raw_payload_json", sa.Text()),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ingestion_runs.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "provider",
            "source_record_key",
            "payload_sha256",
            name="uq_source_observations_delivery",
        ),
        sa.CheckConstraint(
            "length(provider) BETWEEN 1 AND 64", name="ck_observations_provider"
        ),
        sa.CheckConstraint("length(source_record_key) > 0", name="ck_observations_key"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_observations_hash"),
        sa.CheckConstraint(
            "origin IN ('source', 'reconstructed')", name="ck_observations_origin"
        ),
        sa.CheckConstraint(
            "reconciliation IN ('selected', 'agreement', 'conflict', 'unselected')",
            name="ck_observations_reconciliation",
        ),
        sa.CheckConstraint("open_paise > 0", name="ck_observations_open"),
        sa.CheckConstraint("high_paise > 0", name="ck_observations_high"),
        sa.CheckConstraint("low_paise > 0", name="ck_observations_low"),
        sa.CheckConstraint("close_paise > 0", name="ck_observations_close"),
        sa.CheckConstraint(
            "previous_close_paise IS NULL OR previous_close_paise > 0",
            name="ck_observations_previous_close",
        ),
        sa.CheckConstraint("volume >= 0", name="ck_observations_volume"),
        sa.CheckConstraint("high_paise >= low_paise", name="ck_observations_range"),
        sa.CheckConstraint(
            "open_paise BETWEEN low_paise AND high_paise",
            name="ck_observations_open_range",
        ),
        sa.CheckConstraint(
            "close_paise BETWEEN low_paise AND high_paise",
            name="ck_observations_close_range",
        ),
    )
    op.create_index(
        "ix_source_observations_instrument_date",
        "source_observations",
        ["instrument_id", "trade_date"],
    )
    with op.batch_alter_table("daily_prices") as batch:
        batch.alter_column(
            "previous_close_paise", existing_type=sa.BigInteger(), nullable=True
        )
        batch.alter_column("import_batch_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("source_observation_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_daily_prices_source_observation_id_source_observations",
            "source_observations",
            ["source_observation_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_daily_prices_source_observation_id", ["source_observation_id"]
        )
    connection = op.get_bind()
    for row in connection.execute(
        sa.text("SELECT id, symbol, series FROM instruments")
    ):
        connection.execute(
            sa.text(
                "INSERT INTO instrument_mappings "
                "(instrument_id, provider, symbol, exchange, series, identity_status) "
                "VALUES (:id, 'nse_zip', :symbol, 'NSE', :series, 'legacy_unverified')"
            ),
            {"id": row.id, "symbol": row.symbol, "series": row.series},
        )
    run_ids: dict[int, int] = {}
    for legacy_batch in connection.execute(
        sa.text(
            "SELECT id, file_name, file_sha256, imported_at_utc "
            "FROM import_batches ORDER BY id"
        )
    ):
        result = connection.execute(
            sa.text(
                "INSERT INTO ingestion_runs (provider, source_kind, source_ref, "
                "source_sha256, import_batch_id, ingested_at_utc, provenance_kind) "
                "VALUES ('nse_zip', 'zip', :file_name, :hash, :id, :ingested, "
                "'reconstructed')"
            ),
            {
                "file_name": legacy_batch.file_name,
                "hash": legacy_batch.file_sha256,
                "id": legacy_batch.id,
                "ingested": legacy_batch.imported_at_utc,
            },
        )
        assert result.lastrowid is not None
        run_ids[legacy_batch.id] = result.lastrowid
    legacy_prices = (
        connection.execute(
            sa.text(
                "SELECT p.*, i.symbol, i.series, b.imported_at_utc "
                "FROM daily_prices p JOIN instruments i ON i.id = p.instrument_id "
                "JOIN import_batches b ON b.id = p.import_batch_id ORDER BY p.id"
            )
        )
        .mappings()
        .all()
    )
    for price in legacy_prices:
        normalized = {
            "instrument_id": price["instrument_id"],
            "trade_date": str(price["trade_date"]),
            "open_paise": price["open_paise"],
            "high_paise": price["high_paise"],
            "low_paise": price["low_paise"],
            "close_paise": price["close_paise"],
            "previous_close_paise": price["previous_close_paise"],
            "volume": price["volume"],
        }
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        inserted = connection.execute(
            sa.text(
                "INSERT INTO source_observations "
                "(instrument_id, ingestion_run_id, provider, source_record_key, "
                "payload_sha256, symbol, exchange, series, trade_date, "
                "ingested_at_utc, open_paise, high_paise, low_paise, close_paise, "
                "previous_close_paise, volume, origin, reconciliation) "
                "VALUES (:instrument, :run, 'nse_zip', :record_key, :digest, "
                ":symbol, 'NSE', :series, :day, :ingested, :open, :high, :low, "
                ":close, :previous, :volume, 'reconstructed', 'selected')"
            ),
            {
                "instrument": price["instrument_id"],
                "run": run_ids[price["import_batch_id"]],
                "record_key": f"legacy-canonical:{price['id']}",
                "digest": digest,
                "symbol": price["symbol"],
                "series": price["series"],
                "day": price["trade_date"],
                "ingested": price["imported_at_utc"],
                "open": price["open_paise"],
                "high": price["high_paise"],
                "low": price["low_paise"],
                "close": price["close_paise"],
                "previous": price["previous_close_paise"],
                "volume": price["volume"],
            },
        )
        assert inserted.lastrowid is not None
        connection.execute(
            sa.text(
                "UPDATE daily_prices SET source_observation_id=:observation "
                "WHERE id=:price"
            ),
            {"observation": inserted.lastrowid, "price": price["id"]},
        )
    op.execute(
        "CREATE TRIGGER source_observations_no_update "
        "BEFORE UPDATE ON source_observations "
        "BEGIN SELECT RAISE(ABORT, 'source observations are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER source_observations_no_delete "
        "BEFORE DELETE ON source_observations "
        "BEGIN SELECT RAISE(ABORT, 'source observations are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER ingestion_runs_no_update "
        "BEFORE UPDATE ON ingestion_runs "
        "BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER ingestion_runs_no_delete "
        "BEFORE DELETE ON ingestion_runs "
        "BEGIN SELECT RAISE(ABORT, 'ingestion runs are immutable'); END"
    )


def downgrade() -> None:
    """Refuse a downgrade that would discard newly captured source evidence."""
    connection = op.get_bind()
    unsafe = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM source_observations "
            "WHERE origin != 'reconstructed' OR source_record_key NOT LIKE "
            "'legacy-canonical:%') "
            "OR EXISTS(SELECT 1 FROM ingestion_runs "
            "WHERE provenance_kind != 'reconstructed') "
            "OR EXISTS(SELECT 1 FROM instrument_mappings "
            "WHERE identity_status != 'legacy_unverified') "
            "OR EXISTS(SELECT 1 FROM daily_prices "
            "WHERE previous_close_paise IS NULL OR import_batch_id IS NULL)"
        )
    ).scalar_one()
    if unsafe:
        raise RuntimeError(
            "downgrade would discard new observations or unknown previous closes"
        )
    op.execute("DROP TRIGGER source_observations_no_update")
    op.execute("DROP TRIGGER source_observations_no_delete")
    op.execute("DROP TRIGGER ingestion_runs_no_update")
    op.execute("DROP TRIGGER ingestion_runs_no_delete")
    with op.batch_alter_table("daily_prices") as batch:
        batch.drop_constraint("uq_daily_prices_source_observation_id", type_="unique")
        batch.drop_constraint(
            "fk_daily_prices_source_observation_id_source_observations",
            type_="foreignkey",
        )
        batch.drop_column("source_observation_id")
        batch.alter_column(
            "previous_close_paise", existing_type=sa.BigInteger(), nullable=False
        )
        batch.alter_column(
            "import_batch_id", existing_type=sa.Integer(), nullable=False
        )
    op.drop_index(
        "ix_source_observations_instrument_date", table_name="source_observations"
    )
    op.drop_table("source_observations")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_instrument_mappings_lookup", table_name="instrument_mappings")
    op.drop_table("instrument_mappings")
