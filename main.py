import random
import string
import uuid
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from database import get_conn, init_db

app = FastAPI(title="Meetup API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ── 모델 ──────────────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    nickname: str

class AddFriendRequest(BaseModel):
    friend_id: str

class AddEventRequest(BaseModel):
    title: str
    date: str
    start_hour: int
    end_hour: int

class JoinRequest(BaseModel):
    name: str
    user_id: Optional[str] = None   # 풀유저면 user_id 포함

class CreateRoomRequest(BaseModel):
    title: Optional[str] = '새 약속'
    created_by: Optional[str] = None
    friend_ids: Optional[List[str]] = []
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None

class DaySlot(BaseModel):
    date: str
    hours: List[int]

class AvailabilityRequest(BaseModel):
    participant_id: str
    availability: List[DaySlot]


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _make_code(length=6) -> str:
    chars = string.ascii_uppercase + string.digits
    with get_conn() as conn:
        for _ in range(10):
            code = ''.join(random.choices(chars, k=length))
            if not conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
                return code
    raise RuntimeError("코드 생성 실패")


def _date_range(date_from: str, date_to: str):
    """date_from ~ date_to 사이 모든 날짜 문자열 리스트."""
    start = date.fromisoformat(date_from)
    end   = date.fromisoformat(date_to)
    days  = (end - start).days + 1
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


# ── 유저 ──────────────────────────────────────────────────────────────────────

@app.post("/users", status_code=201)
def create_user(body: CreateUserRequest):
    """닉네임으로 유저 생성. 이미 있으면 기존 유저 반환."""
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(400, "닉네임을 입력해주세요.")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, nickname FROM users WHERE nickname=?", (nickname,)
        ).fetchone()
        if existing:
            return {"user_id": existing["id"], "nickname": existing["nickname"]}
        uid = uuid.uuid4().hex
        conn.execute("INSERT INTO users (id, nickname) VALUES (?,?)", (uid, nickname))
    return {"user_id": uid, "nickname": nickname}


@app.get("/users/search")
def search_user(nickname: str = Query(...)):
    """닉네임으로 유저 검색."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, nickname FROM users WHERE nickname LIKE ? LIMIT 10",
            (f"%{nickname}%",)
        ).fetchall()
    return {"users": [{"user_id": r["id"], "nickname": r["nickname"]} for r in rows]}


@app.get("/users/{user_id}")
def get_user(user_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT id, nickname FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "유저를 찾을 수 없어요.")
    return {"user_id": row["id"], "nickname": row["nickname"]}


# ── 친구 ──────────────────────────────────────────────────────────────────────

@app.post("/users/{user_id}/friends", status_code=201)
def add_friend(user_id: str, body: AddFriendRequest):
    if user_id == body.friend_id:
        raise HTTPException(400, "자기 자신은 추가할 수 없어요.")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "유저를 찾을 수 없어요.")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (body.friend_id,)).fetchone():
            raise HTTPException(404, "친구를 찾을 수 없어요.")
        conn.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?,?)",
                     (user_id, body.friend_id))
        conn.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?,?)",
                     (body.friend_id, user_id))
    return {"ok": True}


@app.get("/users/{user_id}/friends")
def get_friends(user_id: str):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.id, u.nickname FROM users u
            JOIN friendships f ON f.friend_id = u.id
            WHERE f.user_id = ? ORDER BY u.nickname
        """, (user_id,)).fetchall()
    return {"friends": [{"user_id": r["id"], "nickname": r["nickname"]} for r in rows]}


# ── 개인 일정 ─────────────────────────────────────────────────────────────────

@app.get("/users/{user_id}/events")
def get_events(user_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, date, start_hour, end_hour FROM user_events "
            "WHERE user_id=? ORDER BY date, start_hour", (user_id,)
        ).fetchall()
    return {"events": [dict(r) for r in rows]}


@app.post("/users/{user_id}/events", status_code=201)
def add_event(user_id: str, body: AddEventRequest):
    if body.start_hour >= body.end_hour:
        raise HTTPException(400, "종료 시간은 시작 시간보다 늦어야 해요.")
    eid = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_events (id, user_id, title, date, start_hour, end_hour) VALUES (?,?,?,?,?,?)",
            (eid, user_id, body.title.strip(), body.date, body.start_hour, body.end_hour)
        )
    return {"event_id": eid}


@app.delete("/users/{user_id}/events/{event_id}")
def delete_event(user_id: str, event_id: str):
    with get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM user_events WHERE id=? AND user_id=?", (event_id, user_id)
        ).fetchone():
            raise HTTPException(404, "일정을 찾을 수 없어요.")
        conn.execute("DELETE FROM user_events WHERE id=?", (event_id,))
    return {"ok": True}


# ── 약속 방 ───────────────────────────────────────────────────────────────────

@app.post("/rooms", status_code=201)
def create_room(body: CreateRoomRequest = None):
    """
    풀유저가 방 생성: created_by + friend_ids + date_from/date_to 포함.
    게스트 참여용 방: 파라미터 없이 코드만 생성.
    """
    if body is None:
        body = CreateRoomRequest()
    code = _make_code()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO rooms (code, title, created_by, date_from, date_to) VALUES (?,?,?,?,?)",
            (code, body.title, body.created_by, body.date_from, body.date_to)
        )
        # 방장 자동 참여 (풀유저)
        if body.created_by:
            creator = conn.execute(
                "SELECT nickname FROM users WHERE id=?", (body.created_by,)
            ).fetchone()
            if creator:
                conn.execute(
                    "INSERT INTO participants (id, room_code, name, user_id, type) VALUES (?,?,?,?,?)",
                    (uuid.uuid4().hex, code, creator["nickname"], body.created_by, "full")
                )
        # 초대된 친구 자동 참여 (풀유저)
        for fid in (body.friend_ids or []):
            friend = conn.execute("SELECT nickname FROM users WHERE id=?", (fid,)).fetchone()
            if friend:
                conn.execute(
                    "INSERT OR IGNORE INTO participants (id, room_code, name, user_id, type) VALUES (?,?,?,?,?)",
                    (uuid.uuid4().hex, code, friend["nickname"], fid, "full")
                )
    return {"code": code}


@app.get("/rooms/{code}")
def get_room(code: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT code, title, date_from, date_to FROM rooms WHERE code=?", (code.upper(),)
        ).fetchone()
    if not row:
        raise HTTPException(404, "방을 찾을 수 없어요")
    return dict(row)


@app.post("/rooms/{code}/join", status_code=201)
def join_room(code: str, body: JoinRequest):
    """
    게스트: user_id 없이 이름만 → type='guest'
    풀유저: user_id 포함 → type='full'
    """
    code = code.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
            raise HTTPException(404, "방을 찾을 수 없어요")
        existing = conn.execute(
            "SELECT id FROM participants WHERE room_code=? AND name=?",
            (code, body.name.strip())
        ).fetchone()
        if existing:
            return {"participant_id": existing["id"]}
        pid = uuid.uuid4().hex
        p_type = "full" if body.user_id else "guest"
        conn.execute(
            "INSERT INTO participants (id, room_code, name, user_id, type) VALUES (?,?,?,?,?)",
            (pid, code, body.name.strip(), body.user_id, p_type)
        )
    return {"participant_id": pid}


@app.post("/rooms/{code}/availability")
def save_availability(code: str, body: AvailabilityRequest):
    """게스트가 가능한 시간 수동 제출."""
    code = code.upper()
    with get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM participants WHERE id=? AND room_code=?",
            (body.participant_id, code)
        ).fetchone():
            raise HTTPException(404, "참여자를 찾을 수 없어요")
        conn.execute(
            "DELETE FROM availability WHERE participant_id=? AND room_code=?",
            (body.participant_id, code)
        )
        for day in body.availability:
            for hour in day.hours:
                conn.execute(
                    "INSERT OR IGNORE INTO availability (participant_id, room_code, date, hour) VALUES (?,?,?,?)",
                    (body.participant_id, code, day.date, hour)
                )
    return {"ok": True}


@app.get("/rooms/{code}/free-slots")
def get_free_slots(code: str):
    """
    풀유저: 개인 캘린더에서 busy 시간 자동 계산
    게스트: 수동 제출한 availability 사용
    교집합 → 모두가 비어있는 시간 반환
    """
    code = code.upper()
    with get_conn() as conn:
        room = conn.execute(
            "SELECT date_from, date_to FROM rooms WHERE code=?", (code,)
        ).fetchone()
        if not room:
            raise HTTPException(404, "방을 찾을 수 없어요")

        participants = conn.execute(
            "SELECT id, name, user_id, type FROM participants WHERE room_code=?", (code,)
        ).fetchall()

        if not participants:
            return {"participants": [], "pending_guests": [], "free_dates": [], "total": 0}

        date_from = room["date_from"]
        date_to   = room["date_to"]

        # 날짜 범위 결정: 방에 range 없으면 게스트 제출 날짜 기준
        if date_from and date_to:
            candidate_dates = _date_range(date_from, date_to)
        else:
            guest_dates = conn.execute(
                "SELECT DISTINCT date FROM availability WHERE room_code=? ORDER BY date", (code,)
            ).fetchall()
            candidate_dates = [r["date"] for r in guest_dates]

        if not candidate_dates:
            return {
                "participants": [p["name"] for p in participants],
                "pending_guests": [],
                "free_dates": [],
                "total": len(participants),
            }

        full_participants  = [p for p in participants if p["type"] == "full" and p["user_id"]]
        guest_participants = [p for p in participants if p["type"] == "guest"]

        # 게스트 응답 여부 확인
        pending_guests = []
        responded_guests = set()
        for g in guest_participants:
            has_response = conn.execute(
                "SELECT 1 FROM availability WHERE participant_id=? LIMIT 1", (g["id"],)
            ).fetchone()
            if has_response:
                responded_guests.add(g["id"])
            else:
                pending_guests.append(g["name"])

        # 풀유저 busy 슬롯 수집 {user_id: {(date, hour)}}
        full_busy = {}
        for p in full_participants:
            uid = p["user_id"]
            events = conn.execute(
                "SELECT date, start_hour, end_hour FROM user_events WHERE user_id=? AND date >= ? AND date <= ?",
                (uid, candidate_dates[0], candidate_dates[-1])
            ).fetchall()
            busy_set = set()
            for ev in events:
                for h in range(ev["start_hour"], ev["end_hour"]):
                    busy_set.add((ev["date"], h))
            full_busy[uid] = busy_set

        # 게스트 available 슬롯 수집 {participant_id: {(date, hour)}}
        guest_avail = {}
        for g in guest_participants:
            if g["id"] not in responded_guests:
                continue
            rows = conn.execute(
                "SELECT date, hour FROM availability WHERE participant_id=?", (g["id"],)
            ).fetchall()
            guest_avail[g["id"]] = {(r["date"], r["hour"]) for r in rows}

        # 교집합 계산
        free_dates = []
        for d in candidate_dates:
            free_hours = []
            for h in range(8, 23):
                slot = (d, h)
                # 풀유저 체크: busy 아닌지
                full_ok = all(slot not in full_busy[p["user_id"]] for p in full_participants)
                # 게스트 체크 (응답한 게스트만): 가능하다고 표시했는지
                guest_ok = all(slot in guest_avail[g["id"]]
                               for g in guest_participants if g["id"] in responded_guests)
                if full_ok and guest_ok:
                    free_hours.append(h)
            if free_hours:
                free_dates.append({"date": d, "free_hours": free_hours})

    return {
        "participants": [p["name"] for p in participants],
        "pending_guests": pending_guests,   # 아직 응답 안 한 게스트
        "free_dates": free_dates,
        "total": len(participants),
    }
