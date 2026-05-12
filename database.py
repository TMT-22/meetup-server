import sqlite3
from pathlib import Path

DB_PATH = Path("meetup.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                nickname    TEXT UNIQUE NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_events (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                date        TEXT NOT NULL,
                start_hour  INTEGER NOT NULL,
                end_hour    INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS friendships (
                user_id     TEXT NOT NULL,
                friend_id   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, friend_id),
                FOREIGN KEY (user_id)   REFERENCES users(id),
                FOREIGN KEY (friend_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS rooms (
                code        TEXT PRIMARY KEY,
                title       TEXT DEFAULT '새 약속',
                created_by  TEXT,
                date_from   TEXT,
                date_to     TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            -- type: 'full' = 계정 보유(캘린더 자동), 'guest' = 수동 입력
            CREATE TABLE IF NOT EXISTS participants (
                id          TEXT PRIMARY KEY,
                room_code   TEXT NOT NULL,
                name        TEXT NOT NULL,
                user_id     TEXT,
                type        TEXT DEFAULT 'guest',
                joined_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (room_code) REFERENCES rooms(code),
                FOREIGN KEY (user_id)   REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS availability (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id  TEXT NOT NULL,
                room_code       TEXT NOT NULL,
                date            TEXT NOT NULL,
                hour            INTEGER NOT NULL,
                UNIQUE(participant_id, date, hour),
                FOREIGN KEY (participant_id) REFERENCES participants(id)
            );

            -- 매주 반복 일정: day_of_week 0=월 1=화 2=수 3=목 4=금 5=토 6=일
            CREATE TABLE IF NOT EXISTS recurring_events (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_hour  INTEGER NOT NULL,
                end_hour    INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
