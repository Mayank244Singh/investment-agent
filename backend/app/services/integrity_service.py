"""Read-only reconciliation of the persisted portfolio ledger."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class IntegrityResult:
    """Complete integrity outcome with every detected discrepancy."""

    discrepancies: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.discrepancies


@dataclass(frozen=True)
class Movement:
    id: int
    movement_type: str
    amount: int


@dataclass(frozen=True)
class Transaction:
    id: int
    transaction_type: str
    symbol: str
    quantity: int
    price: int
    fees: int
    transacted_at: str


@dataclass(frozen=True)
class Lot:
    id: int
    buy_transaction_id: int
    symbol: str
    acquired_at: str
    original_quantity: int
    remaining_quantity: int
    total_cost: int
    remaining_cost: int


@dataclass(frozen=True)
class Realization:
    id: int
    sell_transaction_id: int
    lot_id: int
    quantity: int
    cost_basis: int
    gross_proceeds: int
    sell_fees: int
    net_proceeds: int
    realized_pnl: int


@dataclass(frozen=True)
class VaultEntry:
    id: int
    sell_transaction_id: int
    amount: int


def check_ledger_integrity(database_path: Path) -> IntegrityResult:
    """Open a SQLite ledger read-only and report every reconciliation failure."""
    path = database_path.expanduser().resolve()
    if not path.is_file():
        return IntegrityResult((f"SQLite database does not exist: {path}",))

    discrepancies: list[str] = []
    try:
        with _open_read_only(path) as connection:
            connection.row_factory = sqlite3.Row
            _check_sqlite_integrity(connection, discrepancies)
            _check_price_data(connection, discrepancies)
            movements = _load_movements(connection)
            transactions = _load_transactions(connection)
            lots = _load_lots(connection)
            realizations = _load_realizations(connection)
            vault_entries = _load_vault_entries(connection)
    except sqlite3.DatabaseError as exc:
        discrepancies.append(f"SQLite database could not be read: {exc}")
        return IntegrityResult(tuple(discrepancies))

    _check_basic_values(
        movements, transactions, lots, realizations, vault_entries, discrepancies
    )
    _check_lots(transactions, lots, realizations, discrepancies)
    _check_sells_and_vault(
        transactions, lots, realizations, vault_entries, discrepancies
    )
    _check_fifo(transactions, lots, realizations, discrepancies)
    _check_cash(movements, transactions, lots, realizations, discrepancies)
    return IntegrityResult(tuple(discrepancies))


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    uri = f"{database_path.as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _check_sqlite_integrity(
    connection: sqlite3.Connection, discrepancies: list[str]
) -> None:
    results = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    if results != ["ok"]:
        discrepancies.extend(f"SQLite quick_check: {item}" for item in results)
    for table, row_id, parent, foreign_key in connection.execute(
        "PRAGMA foreign_key_check"
    ):
        discrepancies.append(
            "Foreign-key violation: "
            f"table={table}, row={row_id}, parent={parent}, key={foreign_key}"
        )


def _check_price_data(connection: sqlite3.Connection, discrepancies: list[str]) -> None:
    """Check stored EQ prices and provenance without modifying SQLite."""
    required = {"instruments", "daily_prices", "import_batches"}
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('instruments', 'daily_prices', 'import_batches')"
        )
    }
    if missing := required - present:
        discrepancies.append(
            f"Price tables missing: {', '.join(sorted(missing))}; run migrations"
        )
        return

    for instrument in connection.execute(
        "SELECT id, symbol, series, isin FROM instruments ORDER BY id"
    ):
        label = f"Instrument {instrument['id']} ({instrument['symbol']})"
        if instrument["series"] != "EQ":
            discrepancies.append(f"{label} has unsupported series")
        isin = instrument["isin"]
        if isin is None or isin == "":
            discrepancies.append(f"{label} has a missing ISIN")
        elif not _valid_isin(str(isin)):
            discrepancies.append(f"{label} has an inconsistent ISIN")

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    for price in connection.execute(
        """SELECT p.id, p.instrument_id, p.import_batch_id, p.trade_date,
                  p.open_paise, p.high_paise, p.low_paise, p.close_paise,
                  p.previous_close_paise, p.volume, p.price_basis,
                  i.id AS valid_instrument, b.id AS valid_batch
           FROM daily_prices AS p
           LEFT JOIN instruments AS i ON i.id = p.instrument_id
           LEFT JOIN import_batches AS b ON b.id = p.import_batch_id
           ORDER BY p.id"""
    ):
        label = f"Daily price {price['id']}"
        if price["valid_instrument"] is None:
            discrepancies.append(f"{label} has no valid instrument")
        if price["valid_batch"] is None:
            discrepancies.append(f"{label} has no valid import batch")
        for column in (
            "open_paise",
            "high_paise",
            "low_paise",
            "close_paise",
            "previous_close_paise",
        ):
            amount = price[column]
            if type(amount) is not int or amount <= 0:
                discrepancies.append(f"{label} has non-positive or invalid {column}")
        low, high = price["low_paise"], price["high_paise"]
        opening, closing = price["open_paise"], price["close_paise"]
        if all(type(value) is int for value in (low, high, opening, closing)):
            if low > high:
                discrepancies.append(f"{label} has low greater than high")
            if not low <= opening <= high:
                discrepancies.append(f"{label} has open outside low-high range")
            if not low <= closing <= high:
                discrepancies.append(f"{label} has close outside low-high range")
        volume = price["volume"]
        if type(volume) is not int or volume < 0:
            discrepancies.append(f"{label} has negative or invalid volume")
        if price["price_basis"] != "unadjusted":
            discrepancies.append(f"{label} has unsupported price basis")
        try:
            trade_date = date.fromisoformat(str(price["trade_date"]))
            if trade_date.isoformat() != price["trade_date"]:
                raise ValueError("noncanonical date")
        except ValueError:
            discrepancies.append(f"{label} has invalid trade date")
        else:
            if trade_date > today:
                discrepancies.append(
                    f"{label} is dated in the future ({trade_date.isoformat()})"
                )

    for instrument_id, trade_date, count in connection.execute(
        """SELECT instrument_id, trade_date, COUNT(*)
           FROM daily_prices
           GROUP BY instrument_id, trade_date HAVING COUNT(*) > 1"""
    ):
        discrepancies.append(
            f"Duplicate daily prices for instrument {instrument_id} "
            f"on {trade_date}: {count} rows"
        )

    for batch in connection.execute(
        """SELECT b.id, b.total_rows, b.imported_rows, b.skipped_rows,
                  b.rejected_rows, COUNT(p.id) AS linked_rows
           FROM import_batches AS b
           LEFT JOIN daily_prices AS p ON p.import_batch_id = b.id
           GROUP BY b.id ORDER BY b.id"""
    ):
        label = f"Import batch {batch['id']}"
        if batch["imported_rows"] != batch["linked_rows"]:
            discrepancies.append(
                f"{label} stores imported_rows={batch['imported_rows']} "
                f"but has {batch['linked_rows']} linked daily prices"
            )
        if batch["total_rows"] != (
            batch["imported_rows"] + batch["skipped_rows"] + batch["rejected_rows"]
        ):
            discrepancies.append(f"{label} row counts do not reconcile")


def _valid_isin(value: str) -> bool:
    """Check the stored ISIN shape and ISO 6166 Luhn check digit."""
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value) is None:
        return False
    expanded = "".join(
        str(ord(char) - 55) if char.isalpha() else char for char in value
    )
    checksum = sum(
        (digit * 2 // 10 + digit * 2 % 10) if index % 2 else digit
        for index, digit in enumerate(int(char) for char in reversed(expanded))
    )
    return checksum % 10 == 0


def _load_movements(connection: sqlite3.Connection) -> list[Movement]:
    return [
        Movement(int(row["id"]), str(row["movement_type"]), int(row["amount_paise"]))
        for row in connection.execute(
            "SELECT id, movement_type, amount_paise FROM capital_movements"
        )
    ]


def _load_transactions(connection: sqlite3.Connection) -> list[Transaction]:
    return [
        Transaction(
            id=int(row["id"]),
            transaction_type=str(row["transaction_type"]),
            symbol=str(row["symbol"]),
            quantity=int(row["quantity"]),
            price=int(row["price_paise"]),
            fees=int(row["fees_paise"]),
            transacted_at=str(row["transacted_at_utc"]),
        )
        for row in connection.execute(
            """
            SELECT id, transaction_type, symbol, quantity, price_paise,
                   fees_paise, transacted_at_utc
            FROM ledger_transactions
            """
        )
    ]


def _load_lots(connection: sqlite3.Connection) -> list[Lot]:
    return [
        Lot(
            id=int(row["id"]),
            buy_transaction_id=int(row["buy_transaction_id"]),
            symbol=str(row["symbol"]),
            acquired_at=str(row["acquired_at_utc"]),
            original_quantity=int(row["original_quantity"]),
            remaining_quantity=int(row["remaining_quantity"]),
            total_cost=int(row["total_cost_paise"]),
            remaining_cost=int(row["remaining_cost_paise"]),
        )
        for row in connection.execute(
            """
            SELECT id, buy_transaction_id, symbol, acquired_at_utc,
                   original_quantity, remaining_quantity, total_cost_paise,
                   remaining_cost_paise
            FROM holding_lots
            """
        )
    ]


def _load_realizations(connection: sqlite3.Connection) -> list[Realization]:
    return [
        Realization(
            id=int(row["id"]),
            sell_transaction_id=int(row["sell_transaction_id"]),
            lot_id=int(row["holding_lot_id"]),
            quantity=int(row["quantity"]),
            cost_basis=int(row["cost_basis_paise"]),
            gross_proceeds=int(row["gross_proceeds_paise"]),
            sell_fees=int(row["sell_fees_paise"]),
            net_proceeds=int(row["net_proceeds_paise"]),
            realized_pnl=int(row["realized_pnl_paise"]),
        )
        for row in connection.execute(
            """
            SELECT id, sell_transaction_id, holding_lot_id, quantity,
                   cost_basis_paise, gross_proceeds_paise, sell_fees_paise,
                   net_proceeds_paise, realized_pnl_paise
            FROM lot_realizations
            """
        )
    ]


def _load_vault_entries(connection: sqlite3.Connection) -> list[VaultEntry]:
    return [
        VaultEntry(
            int(row["id"]),
            int(row["sell_transaction_id"]),
            int(row["amount_paise"]),
        )
        for row in connection.execute(
            "SELECT id, sell_transaction_id, amount_paise FROM profit_vault_entries"
        )
    ]


def _check_basic_values(
    movements: list[Movement],
    transactions: list[Transaction],
    lots: list[Lot],
    realizations: list[Realization],
    vault_entries: list[VaultEntry],
    discrepancies: list[str],
) -> None:
    for movement in movements:
        if movement.movement_type not in {"deposit", "withdrawal"}:
            discrepancies.append(f"Capital movement {movement.id} has invalid type")
        if movement.amount <= 0:
            discrepancies.append(
                f"Capital movement {movement.id} has non-positive amount"
            )
    for transaction in transactions:
        if transaction.transaction_type not in {"buy", "sell"}:
            discrepancies.append(f"Transaction {transaction.id} has invalid type")
        if transaction.quantity <= 0 or transaction.price <= 0:
            discrepancies.append(
                f"Transaction {transaction.id} has invalid quantity or price"
            )
        if transaction.fees < 0:
            discrepancies.append(f"Transaction {transaction.id} has negative fees")
    for lot in lots:
        if not 0 <= lot.remaining_quantity <= lot.original_quantity:
            discrepancies.append(f"Lot {lot.id} has invalid remaining quantity")
        if not 0 <= lot.remaining_cost <= lot.total_cost:
            discrepancies.append(f"Lot {lot.id} has invalid remaining cost")
    for realization in realizations:
        if realization.quantity <= 0 or realization.cost_basis <= 0:
            discrepancies.append(
                f"Realization {realization.id} has invalid quantity or cost"
            )
        if realization.sell_fees < 0:
            discrepancies.append(
                f"Realization {realization.id} has negative allocated fees"
            )
    for entry in vault_entries:
        if entry.amount <= 0:
            discrepancies.append(f"Profit Vault entry {entry.id} is not positive")


def _check_lots(
    transactions: list[Transaction],
    lots: list[Lot],
    realizations: list[Realization],
    discrepancies: list[str],
) -> None:
    transactions_by_id = {item.id: item for item in transactions}
    lots_by_buy: dict[int, list[Lot]] = {}
    realizations_by_lot: dict[int, list[Realization]] = {}
    for lot in lots:
        lots_by_buy.setdefault(lot.buy_transaction_id, []).append(lot)
    for realization in realizations:
        realizations_by_lot.setdefault(realization.lot_id, []).append(realization)

    for transaction in transactions:
        if transaction.transaction_type != "buy":
            continue
        buy_lots = lots_by_buy.get(transaction.id, [])
        if len(buy_lots) != 1:
            discrepancies.append(
                f"Buy transaction {transaction.id} has {len(buy_lots)} "
                "holding lots; expected 1"
            )
            continue
        lot = buy_lots[0]
        expected_cost = transaction.quantity * transaction.price + transaction.fees
        if lot.symbol != transaction.symbol:
            discrepancies.append(f"Lot {lot.id} symbol does not match its buy")
        if lot.acquired_at != transaction.transacted_at:
            discrepancies.append(
                f"Lot {lot.id} acquisition time does not match its buy"
            )
        if lot.original_quantity != transaction.quantity:
            discrepancies.append(
                f"Lot {lot.id} original quantity does not match its buy"
            )
        if lot.total_cost != expected_cost:
            discrepancies.append(
                f"Lot {lot.id} total cost is {lot.total_cost}; expected {expected_cost}"
            )

    for lot in lots:
        referenced_buy = transactions_by_id.get(lot.buy_transaction_id)
        if referenced_buy is None or referenced_buy.transaction_type != "buy":
            discrepancies.append(f"Lot {lot.id} does not reference a valid buy")
        lot_realizations = realizations_by_lot.get(lot.id, [])
        expected_quantity = lot.original_quantity - sum(
            item.quantity for item in lot_realizations
        )
        expected_cost = lot.total_cost - sum(
            item.cost_basis for item in lot_realizations
        )
        if lot.remaining_quantity != expected_quantity:
            discrepancies.append(
                f"Lot {lot.id} remaining quantity is {lot.remaining_quantity}; "
                f"expected {expected_quantity}"
            )
        if lot.remaining_cost != expected_cost:
            discrepancies.append(
                f"Lot {lot.id} remaining cost is {lot.remaining_cost}; "
                f"expected {expected_cost}"
            )


def _check_sells_and_vault(
    transactions: list[Transaction],
    lots: list[Lot],
    realizations: list[Realization],
    vault_entries: list[VaultEntry],
    discrepancies: list[str],
) -> None:
    transactions_by_id = {item.id: item for item in transactions}
    lots_by_id = {item.id: item for item in lots}
    realizations_by_sell: dict[int, list[Realization]] = {}
    vault_by_sell: dict[int, list[VaultEntry]] = {}
    for realization in realizations:
        realizations_by_sell.setdefault(realization.sell_transaction_id, []).append(
            realization
        )
        sell = transactions_by_id.get(realization.sell_transaction_id)
        lot = lots_by_id.get(realization.lot_id)
        if sell is None or sell.transaction_type != "sell":
            discrepancies.append(
                f"Realization {realization.id} does not reference a valid sell"
            )
            continue
        if lot is None:
            discrepancies.append(
                f"Realization {realization.id} does not reference a valid lot"
            )
            continue
        if lot.symbol != sell.symbol:
            discrepancies.append(
                f"Realization {realization.id} lot symbol does not match its sell"
            )
        expected_gross = realization.quantity * sell.price
        expected_net = realization.gross_proceeds - realization.sell_fees
        expected_pnl = realization.net_proceeds - realization.cost_basis
        if realization.gross_proceeds != expected_gross:
            discrepancies.append(
                f"Realization {realization.id} gross proceeds do not reconcile"
            )
        if realization.net_proceeds != expected_net:
            discrepancies.append(
                f"Realization {realization.id} net proceeds do not reconcile"
            )
        if realization.realized_pnl != expected_pnl:
            discrepancies.append(
                f"Realization {realization.id} realised P&L does not reconcile"
            )

    for entry in vault_entries:
        vault_by_sell.setdefault(entry.sell_transaction_id, []).append(entry)
        sell = transactions_by_id.get(entry.sell_transaction_id)
        if sell is None or sell.transaction_type != "sell":
            discrepancies.append(
                f"Profit Vault entry {entry.id} does not reference a valid sell"
            )

    expected_vault_total = 0
    for sell in transactions:
        if sell.transaction_type != "sell":
            continue
        sell_realizations = realizations_by_sell.get(sell.id, [])
        quantity = sum(item.quantity for item in sell_realizations)
        gross = sum(item.gross_proceeds for item in sell_realizations)
        fees = sum(item.sell_fees for item in sell_realizations)
        net = sum(item.net_proceeds for item in sell_realizations)
        pnl = sum(item.realized_pnl for item in sell_realizations)
        if quantity != sell.quantity:
            discrepancies.append(
                f"Sell transaction {sell.id} realized quantity is {quantity}; "
                f"expected {sell.quantity}"
            )
        if gross != sell.quantity * sell.price:
            discrepancies.append(
                f"Sell transaction {sell.id} gross proceeds do not reconcile"
            )
        if fees != sell.fees:
            discrepancies.append(
                f"Sell transaction {sell.id} allocated fees are {fees}; "
                f"expected {sell.fees}"
            )
        if net != gross - fees:
            discrepancies.append(
                f"Sell transaction {sell.id} net proceeds do not reconcile"
            )

        expected_credit = max(pnl, 0)
        expected_vault_total += expected_credit
        entries = vault_by_sell.get(sell.id, [])
        if expected_credit == 0 and entries:
            discrepancies.append(
                f"Sell transaction {sell.id} has a vault entry without positive profit"
            )
        elif expected_credit > 0:
            if len(entries) != 1:
                discrepancies.append(
                    f"Sell transaction {sell.id} has {len(entries)} vault "
                    "entries; expected 1"
                )
            elif entries[0].amount != expected_credit:
                discrepancies.append(
                    f"Sell transaction {sell.id} vault credit is "
                    f"{entries[0].amount}; expected {expected_credit}"
                )

    actual_vault_total = sum(item.amount for item in vault_entries)
    if actual_vault_total != expected_vault_total:
        discrepancies.append(
            f"Profit Vault balance is {actual_vault_total}; "
            f"expected {expected_vault_total}"
        )


def _check_fifo(
    transactions: list[Transaction],
    lots: list[Lot],
    realizations: list[Realization],
    discrepancies: list[str],
) -> None:
    lots_by_buy = {item.buy_transaction_id: item for item in lots}
    transactions_by_id = {item.id: item for item in transactions}
    realizations_by_sell: dict[int, list[Realization]] = {}
    realizations_by_lot: dict[int, list[Realization]] = {}
    for realization in realizations:
        realizations_by_sell.setdefault(realization.sell_transaction_id, []).append(
            realization
        )
        realizations_by_lot.setdefault(realization.lot_id, []).append(realization)

    queues: dict[str, list[list[int]]] = {}
    for transaction in sorted(
        transactions, key=lambda item: (item.transacted_at, item.id)
    ):
        if transaction.transaction_type == "buy":
            lot = lots_by_buy.get(transaction.id)
            if lot is not None:
                queues.setdefault(transaction.symbol, []).append(
                    [lot.id, lot.original_quantity]
                )
            continue
        if transaction.transaction_type != "sell":
            continue
        remaining = transaction.quantity
        expected: list[tuple[int, int]] = []
        for queue_item in queues.setdefault(transaction.symbol, []):
            if remaining == 0:
                break
            matched = min(remaining, queue_item[1])
            if matched:
                expected.append((queue_item[0], matched))
                queue_item[1] -= matched
                remaining -= matched
        if remaining:
            discrepancies.append(
                f"FIFO replay cannot cover sell transaction {transaction.id}; "
                f"short by {remaining}"
            )
        actual = [
            (item.lot_id, item.quantity)
            for item in sorted(
                realizations_by_sell.get(transaction.id, []),
                key=lambda item: item.id,
            )
        ]
        if actual != expected:
            discrepancies.append(
                f"Sell transaction {transaction.id} lot matches are not FIFO: "
                f"{actual}; expected {expected}"
            )

    for lot in lots:
        remaining_quantity = lot.original_quantity
        remaining_cost = lot.total_cost
        lot_realizations = sorted(
            realizations_by_lot.get(lot.id, []),
            key=lambda item: (
                transactions_by_id.get(
                    item.sell_transaction_id,
                    Transaction(0, "", "", 0, 0, 0, ""),
                ).transacted_at,
                item.sell_transaction_id,
                item.id,
            ),
        )
        for realization in lot_realizations:
            if realization.quantity > remaining_quantity:
                discrepancies.append(
                    f"Realization {realization.id} exceeds lot {lot.id} "
                    "remaining quantity"
                )
                continue
            expected_cost = (
                remaining_cost
                if realization.quantity == remaining_quantity
                else remaining_cost * realization.quantity // remaining_quantity
            )
            if realization.cost_basis != expected_cost:
                discrepancies.append(
                    f"Realization {realization.id} FIFO cost is "
                    f"{realization.cost_basis}; expected {expected_cost}"
                )
            remaining_quantity -= realization.quantity
            remaining_cost -= expected_cost


def _check_cash(
    movements: list[Movement],
    transactions: list[Transaction],
    lots: list[Lot],
    realizations: list[Realization],
    discrepancies: list[str],
) -> None:
    capital = sum(
        item.amount if item.movement_type == "deposit" else -item.amount
        for item in movements
        if item.movement_type in {"deposit", "withdrawal"}
    )
    transaction_cash = sum(
        -(item.quantity * item.price + item.fees)
        if item.transaction_type == "buy"
        else item.quantity * item.price - item.fees
        for item in transactions
        if item.transaction_type in {"buy", "sell"}
    )
    final_cash = capital + transaction_cash
    independently_reconciled_cash = (
        capital
        + sum(item.realized_pnl for item in realizations)
        - sum(item.remaining_cost for item in lots)
    )
    if final_cash != independently_reconciled_cash:
        discrepancies.append(
            f"Cash balance is {final_cash}; independently reconciled balance "
            f"is {independently_reconciled_cash}"
        )
    if final_cash < 0:
        discrepancies.append(
            f"Final cash balance is negative ({final_cash} paise); this likely "
            "means a deposit entry is missing"
        )
