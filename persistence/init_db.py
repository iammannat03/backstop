from sqlalchemy import inspect, text

from persistence.db import Base, engine
from persistence import models  # noqa: F401 (registers models on Base.metadata)


def init_db():
    Base.metadata.create_all(bind=engine)

    # create_all only creates missing tables, not missing columns on an
    # existing one. The Slack-command feature added slack_channel/
    # slack_thread_ts to an already-deployed tickets table, so patch those in
    # directly rather than pulling in a full migration tool for one column pair.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS slack_channel VARCHAR"))
        conn.execute(text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS slack_thread_ts VARCHAR"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tickets_slack_thread_ts ON tickets (slack_thread_ts)")
        )

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    expected = {"tickets", "policy_decisions", "verification_results", "audit_records"}
    missing = expected - set(tables)

    print(f"Connected to: {engine.url}")
    print(f"Tables present: {sorted(tables)}")
    if missing:
        print(f"WARNING: missing expected tables: {missing}")
    else:
        print("All expected tables created successfully.")


if __name__ == "__main__":
    init_db()
