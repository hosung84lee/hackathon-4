"""상품 저장소 — 흐름 1(7단계 시스템 반영)이 저장하고 흐름 2(4단계 판단 항목 식별)가 읽는다."""
from __future__ import annotations

from datetime import datetime

from src.backend.db import connect, from_json, to_json, transaction
from src.schema.common import Product


def save_product(product: Product) -> None:
    """같은 product_code 가 있으면 덮어쓴다."""
    with transaction() as conn:
        conn.execute(
            """INSERT INTO products (product_code, product_name, data, source_workflow_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(product_code) DO UPDATE SET
                 product_name=excluded.product_name, data=excluded.data,
                 source_workflow_id=excluded.source_workflow_id, updated_at=excluded.updated_at""",
            (
                product.product_code,
                product.product_name,
                to_json(product),
                product.source_workflow_id,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def get_product(product_code: str) -> Product | None:
    row = connect().execute("SELECT data FROM products WHERE product_code = ?", (product_code,)).fetchone()
    return Product.model_validate(from_json(row["data"])) if row else None


def list_products() -> list[Product]:
    rows = connect().execute("SELECT data FROM products ORDER BY updated_at DESC").fetchall()
    return [Product.model_validate(from_json(r["data"])) for r in rows]
