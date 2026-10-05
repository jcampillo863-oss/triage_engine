import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional
from db import get_db

class SettlementState(str, Enum):
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    CAPTURE_REQUESTED  = "CAPTURE_REQUESTED"
    OUTCOME_UNKNOWN    = "OUTCOME_UNKNOWN"
    RECONCILING        = "RECONCILING"
    PROVIDER_CONFIRMED = "PROVIDER_CONFIRMED"
    REVENUE_RECORDED   = "REVENUE_RECORDED"
    FAILED             = "FAILED"

@dataclass
class SettlementRecord:
    task_id: str
    amount_cents: int
    currency: str = "USD"
    state: SettlementState = SettlementState.PAYMENT_AUTHORIZED
    provider_tx_id: Optional[str] = None
    decision_id: Optional[str] = None
    settlement_id: str = field(default_factory=lambda: f"stl_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class RevenueEngine:
    def create_settlement_intent(
        self, task_id: str, amount_cents: int, decision_id: Optional[str] = None, currency: str = "USD"
    ) -> SettlementRecord:
        record = SettlementRecord(
            task_id=task_id,
            amount_cents=amount_cents,
            currency=currency,
            decision_id=decision_id,
            state=SettlementState.PAYMENT_AUTHORIZED
        )
        self._save_record(record)
        return record

    def transition_state(
        self, settlement_id: str, new_state: SettlementState, provider_tx_id: Optional[str] = None
    ) -> SettlementRecord:
        """Transitions state, updates timestamps, and preserves record history."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, amount_cents, currency, state, provider_tx_id, decision_id, created_at FROM settlement_records WHERE settlement_id = ?",
                (settlement_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Settlement record {settlement_id} not found.")

            task_id, amount_cents, currency, _, existing_provider_tx, decision_id, created_at = row
            updated_provider_tx = provider_tx_id or existing_provider_tx
            now_iso = datetime.now(timezone.utc).isoformat()

            cursor.execute("""
                UPDATE settlement_records
                SET state = ?, provider_tx_id = ?, updated_at = ?
                WHERE settlement_id = ?
            """, (new_state.value, updated_provider_tx, now_iso, settlement_id))

            conn.commit()

            return SettlementRecord(
                settlement_id=settlement_id,
                task_id=task_id,
                amount_cents=amount_cents,
                currency=currency,
                state=new_state,
                provider_tx_id=updated_provider_tx,
                decision_id=decision_id,
                created_at=created_at,
                updated_at=now_iso
            )

    def process_provider_webhook(self, settlement_id: str, provider_payload: Dict[str, Any]) -> SettlementRecord:
        """Parses raw webhook status payloads and handles crash recovery reconciliation."""
        status = str(provider_payload.get("status", "")).upper()
        provider_tx_id = provider_payload.get("id") or provider_payload.get("transaction_id")

        if status in ("COMPLETED", "CAPTURED", "SUCCESS", "PAID"):
            self.transition_state(settlement_id, SettlementState.PROVIDER_CONFIRMED, provider_tx_id)
            return self.transition_state(settlement_id, SettlementState.REVENUE_RECORDED, provider_tx_id)
        elif status in ("PENDING", "PROCESSING"):
            return self.transition_state(settlement_id, SettlementState.OUTCOME_UNKNOWN, provider_tx_id)
        else:
            return self.transition_state(settlement_id, SettlementState.FAILED, provider_tx_id)

    def _save_record(self, record: SettlementRecord):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO settlement_records (
                    settlement_id, task_id, decision_id, amount_cents, currency, state, provider_tx_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.settlement_id,
                record.task_id,
                record.decision_id,
                record.amount_cents,
                record.currency,
                record.state.value,
                record.provider_tx_id,
                record.created_at,
                record.updated_at
            ))
            conn.commit()

if __name__ == "__main__":
    engine = RevenueEngine()

    # 1. Authorize settlement
    intent = engine.create_settlement_intent(task_id="task_live_001", amount_cents=1500, decision_id="dec_sample_001")
    print(f"Settlement Intent: ID={intent.settlement_id}, State={intent.state.value}")

    # 2. Transition to Capture Requested
    captured = engine.transition_state(intent.settlement_id, SettlementState.CAPTURE_REQUESTED)
    print(f"Transition: State={captured.state.value}")

    # 3. Simulate Provider Webhook callback
    mock_webhook = {"id": "PAYID-M123456789", "status": "COMPLETED"}
    final_record = engine.process_provider_webhook(intent.settlement_id, mock_webhook)
    print(f"Provider Payload Processed: State={final_record.state.value}, TxID={final_record.provider_tx_id}")