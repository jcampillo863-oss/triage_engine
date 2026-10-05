"""Add durable attempt claims; quarantine previously prepared captures for reconciliation."""
import db
from paypal_capture_service import _ensure_capture_attempt_schema

def migrate():
    with db.get_db() as conn:
        _ensure_capture_attempt_schema(conn)
        conn.commit()
    print("Capture attempt guard schema ready; existing prepared captures require reconciliation")

if __name__ == '__main__':
    migrate()
