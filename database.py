import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path("data/bot.db")


class Database:
    def __init__(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                active  INTEGER DEFAULT 1,
                created TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sent_announcements (
                ann_id  TEXT PRIMARY KEY,
                sent_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self.conn.commit()

    def add_subscriber(self, chat_id):
        self.conn.execute("INSERT OR IGNORE INTO subscribers(chat_id) VALUES(?)", (chat_id,))
        self.conn.execute("UPDATE subscribers SET active=1 WHERE chat_id=?", (chat_id,))
        self.conn.commit()

    def deactivate_subscriber(self, chat_id):
        self.conn.execute("UPDATE subscribers SET active=0 WHERE chat_id=?", (chat_id,))
        self.conn.commit()

    def get_subscriber(self, chat_id):
        row = self.conn.execute("SELECT * FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def get_active_subscribers(self):
        rows = self.conn.execute("SELECT * FROM subscribers WHERE active=1").fetchall()
        return [dict(r) for r in rows]

    def is_sent(self, ann_id):
        return self.conn.execute(
            "SELECT 1 FROM sent_announcements WHERE ann_id=?", (ann_id,)
        ).fetchone() is not None

    def mark_sent(self, ann_id):
        self.conn.execute("INSERT OR IGNORE INTO sent_announcements(ann_id) VALUES(?)", (ann_id,))
        self.conn.commit()
