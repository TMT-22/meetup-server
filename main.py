import random
import string
import uuid
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import requests

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

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/join/{code}")
def join_page(code: str):
    return FileResponse(os.path.join(static_dir, "join.html"))


# ── 모델 ──────────────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    nickname: str

class UpdateUserRequest(BaseModel):
    nickname: str

class PushTokenRequest(BaseModel):
    token: str

class AddFriendRequest(BaseModel):
    friend_id: str

class AddEventRequest(BaseModel):
    title: str
    date: str
    start_hour: int
    end_hour: int

class RecurringEventRequest(BaseModel):
    title: str
    day_of_week: int  # 0=월 1=화 2=수 3=목 4=금 5=토 6=일
    start_hour: int
    end_hour: int

class ConsentRequest(BaseModel):
    user_id: str

class AcceptRequest(BaseModel):
    user_id: str

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

def _send_push(tokens: list[str], title: str, body: str):
    if not tokens:
        return
    messages = [{"to": t, "title": title, "body": body, "sound": "default"} for t in tokens]
    try:
        requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=messages,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass

def _get_tokens(user_ids: list[str]) -> list[str]:
    if not user_ids:
        return []
    with get_conn() as conn:
        placeholders = ",".join("?" * len(user_ids))
        rows = conn.execute(
            f"SELECT token FROM push_tokens WHERE user_id IN ({placeholders})", user_ids
        ).fetchall()
    return [r["token"] for r in rows]

def _make_code(length=6) -> str:
    chars = string.ascii_uppercase + string.digits
    with get_conn() as conn:
        for _ in range(10):
            code = ''.join(random.choices(chars, k=length))
            if not conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
                return code
    raise RuntimeError("코드 생성 실패")


KOREAN_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-01", "2025-05-05", "2025-05-06", "2025-06-06",
    "2025-08-15", "2025-10-03", "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
    "2025-12-25",
    # 2026
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-03-01", "2026-05-05", "2026-05-25",
    "2026-06-06", "2026-08-17",
    "2026-09-24", "2026-09-25", "2026-09-26",
    "2026-10-03", "2026-10-09", "2026-12-25",
}

def _is_holiday(date_str: str) -> bool:
    return date_str in KOREAN_HOLIDAYS

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


@app.patch("/users/{user_id}")
def update_user(user_id: str, body: UpdateUserRequest):
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(400, "닉네임을 입력해주세요.")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "유저를 찾을 수 없어요.")
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nickname, user_id))
    return {"ok": True}


@app.post("/users/{user_id}/push-token", status_code=201)
def save_push_token(user_id: str, body: PushTokenRequest):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO push_tokens (user_id, token, updated_at) VALUES (?,?, datetime('now'))",
            (user_id, body.token)
        )
    return {"ok": True}


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


@app.get("/users/{user_id}/recurring-events")
def get_recurring_events(user_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, day_of_week, start_hour, end_hour FROM recurring_events "
            "WHERE user_id=? ORDER BY day_of_week, start_hour", (user_id,)
        ).fetchall()
    return {"recurring_events": [dict(r) for r in rows]}


@app.post("/users/{user_id}/recurring-events", status_code=201)
def add_recurring_event(user_id: str, body: RecurringEventRequest):
    if body.start_hour >= body.end_hour:
        raise HTTPException(400, "종료 시간은 시작 시간보다 늦어야 해요.")
    if body.day_of_week < 0 or body.day_of_week > 6:
        raise HTTPException(400, "요일 값이 올바르지 않아요.")
    eid = uuid.uuid4().hex
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recurring_events (id, user_id, title, day_of_week, start_hour, end_hour) VALUES (?,?,?,?,?,?)",
            (eid, user_id, body.title.strip(), body.day_of_week, body.start_hour, body.end_hour)
        )
    return {"event_id": eid}


@app.delete("/users/{user_id}/recurring-events/{event_id}")
def delete_recurring_event(user_id: str, event_id: str):
    with get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM recurring_events WHERE id=? AND user_id=?", (event_id, user_id)
        ).fetchone():
            raise HTTPException(404, "일정을 찾을 수 없어요.")
        conn.execute("DELETE FROM recurring_events WHERE id=?", (event_id,))
    return {"ok": True}


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

@app.get("/users/{user_id}/rooms")
def get_user_rooms(user_id: str):
    """내가 참여 중인 방 목록."""
    with get_conn() as conn:
        rooms = conn.execute("""
            SELECT r.code, r.title, r.date_from, r.date_to, r.created_at,
                   p.accepted as my_accepted
            FROM rooms r
            JOIN participants p ON p.room_code = r.code
            WHERE p.user_id = ?
            ORDER BY r.created_at DESC
        """, (user_id,)).fetchall()

        today = date.today().isoformat()
        result = []
        for room in rooms:
            code = room["code"]
            participants = conn.execute(
                "SELECT name, type, user_id FROM participants WHERE room_code=?", (code,)
            ).fetchall()

            responded = 0
            for pt in participants:
                if pt["type"] == "full":
                    responded += 1
                else:
                    has = conn.execute(
                        "SELECT 1 FROM availability WHERE participant_id = (SELECT id FROM participants WHERE room_code=? AND name=?) LIMIT 1",
                        (code, pt["name"])
                    ).fetchone()
                    if has:
                        responded += 1

            result.append({
                "code": code,
                "title": room["title"],
                "date_from": room["date_from"],
                "date_to": room["date_to"],
                "participants": [p["name"] for p in participants],
                "total": len(participants),
                "responded": responded,
                "my_accepted": bool(room["my_accepted"]),
            })

    # 가까운 미래 날짜 순 정렬, 날짜 없는 건 맨 아래
    def sort_key(r):
        d = r["date_from"]
        if not d:
            return "9999-99-99"
        if d < today:
            return "8888-" + d  # 지난 약속은 날짜 없는 것 위, 미래 아래
        return d

    result.sort(key=sort_key)
    return {"rooms": result}


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
        # 방장 자동 참여 (수락 완료)
        if body.created_by:
            creator = conn.execute(
                "SELECT nickname FROM users WHERE id=?", (body.created_by,)
            ).fetchone()
            if creator:
                conn.execute(
                    "INSERT INTO participants (id, room_code, name, user_id, type, accepted) VALUES (?,?,?,?,?,1)",
                    (uuid.uuid4().hex, code, creator["nickname"], body.created_by, "full")
                )
        # 초대된 친구 (수락 대기)
        invited_ids = []
        for fid in (body.friend_ids or []):
            friend = conn.execute("SELECT nickname FROM users WHERE id=?", (fid,)).fetchone()
            if friend:
                conn.execute(
                    "INSERT OR IGNORE INTO participants (id, room_code, name, user_id, type, accepted) VALUES (?,?,?,?,?,0)",
                    (uuid.uuid4().hex, code, friend["nickname"], fid, "full")
                )
                invited_ids.append(fid)

    # 초대 알림 전송
    if invited_ids and body.created_by:
        with get_conn() as conn:
            creator = conn.execute("SELECT nickname FROM users WHERE id=?", (body.created_by,)).fetchone()
        tokens = _get_tokens(invited_ids)
        creator_name = creator["nickname"] if creator else "누군가"
        _send_push(tokens, "새 약속 초대 📅", f"{creator_name}님이 '{body.title}' 약속에 초대했어요!")

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


@app.post("/rooms/{code}/accept", status_code=200)
def accept_invite(code: str, body: AcceptRequest):
    """초대 수락."""
    code = code.upper()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM participants WHERE room_code=? AND user_id=?",
            (code, body.user_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "초대를 찾을 수 없어요")
        conn.execute("UPDATE participants SET accepted=1 WHERE id=?", (row["id"],))

        # 수락한 유저 닉네임
        accepter = conn.execute(
            "SELECT nickname FROM users WHERE id=?", (body.user_id,)
        ).fetchone()
        # 방 제목 + 방장 user_id
        room = conn.execute(
            "SELECT title, created_by FROM rooms WHERE code=?", (code,)
        ).fetchone()

    if accepter and room and room["created_by"] and room["created_by"] != body.user_id:
        tokens = _get_tokens([room["created_by"]])
        _send_push(tokens, "약속 수락됨 🎉", f"{accepter['nickname']}님이 '{room['title']}' 약속을 수락했어요!")

    return {"ok": True}


@app.post("/rooms/{code}/consent", status_code=201)
def consent_room(code: str, body: ConsentRequest):
    """만료된 방 재사용 동의."""
    code = code.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
            raise HTTPException(404, "방을 찾을 수 없어요")
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (body.user_id,)).fetchone():
            raise HTTPException(404, "유저를 찾을 수 없어요")
        conn.execute(
            "INSERT OR REPLACE INTO room_consents (room_code, user_id) VALUES (?,?)",
            (code, body.user_id)
        )
    return {"ok": True}


@app.get("/rooms/{code}/free-slots")
def get_free_slots(code: str, user_id: Optional[str] = Query(None)):
    """
    풀유저: 개인 캘린더에서 busy 시간 자동 계산
    게스트: 수동 제출한 availability 사용
    교집합 → 모두가 비어있는 시간 반환
    약속 날짜 24시간 경과 후 만료 → 동의한 멤버끼리만 공유
    """
    code = code.upper()
    with get_conn() as conn:
        room = conn.execute(
            "SELECT date_from, date_to FROM rooms WHERE code=?", (code,)
        ).fetchone()
        if not room:
            raise HTTPException(404, "방을 찾을 수 없어요")

        # 만료 체크
        expired = False
        if room["date_to"]:
            expire_date = date.fromisoformat(room["date_to"]) + timedelta(days=1)
            if date.today() > expire_date:
                expired = True

        if expired:
            # 동의한 풀유저 목록
            consented_rows = conn.execute(
                "SELECT u.id, u.nickname FROM room_consents rc JOIN users u ON u.id = rc.user_id WHERE rc.room_code=?",
                (code,)
            ).fetchall()
            consented_ids = {r["id"] for r in consented_rows}
            consented_names = [r["nickname"] for r in consented_rows]

            # 방 전체 풀유저 목록
            all_full = conn.execute(
                "SELECT p.user_id, p.name FROM participants p WHERE p.room_code=? AND p.type='full'",
                (code,)
            ).fetchall()
            pending_consent = [p["name"] for p in all_full if p["user_id"] not in consented_ids]

            user_consented = user_id in consented_ids if user_id else False

            if not user_consented:
                return {
                    "expired": True,
                    "user_consented": False,
                    "consented_users": consented_names,
                    "pending_consent": pending_consent,
                }

            # 동의한 사람들만 대상으로 free-slots 계산
            # 이후 로직에서 participants를 동의자로 필터링
            consented_participant_ids = consented_ids

        all_participants = conn.execute(
            "SELECT id, name, user_id, type, accepted FROM participants WHERE room_code=?", (code,)
        ).fetchall()

        pending_invites = [p["name"] for p in all_participants if not p["accepted"]]

        # 만료된 방: 동의한 풀유저만 포함, 게스트 제외
        if expired:
            participants = [p for p in all_participants
                            if p["type"] == "full" and p["user_id"] in consented_participant_ids]
            pending_consent_names = [p["name"] for p in all_participants
                                     if p["type"] == "full" and p["user_id"] not in consented_participant_ids]
        else:
            # 수락한 참여자만
            participants = [p for p in all_participants if p["accepted"]]
            pending_consent_names = []

        if not participants:
            return {"participants": [], "pending_guests": [], "free_dates": [], "total": 0}

        date_from = room["date_from"]
        date_to   = room["date_to"]

        # 날짜 범위 결정
        if date_from and date_to:
            candidate_dates = _date_range(date_from, date_to)
        else:
            guest_dates = conn.execute(
                "SELECT DISTINCT date FROM availability WHERE room_code=? ORDER BY date", (code,)
            ).fetchall()
            if guest_dates:
                candidate_dates = [r["date"] for r in guest_dates]
            else:
                # 날짜 미지정 → 오늘부터 30일
                candidate_dates = _date_range(
                    date.today().isoformat(),
                    (date.today() + timedelta(days=30)).isoformat()
                )

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
            busy_set = set()

            # 단일 일정
            events = conn.execute(
                "SELECT date, start_hour, end_hour FROM user_events WHERE user_id=? AND date >= ? AND date <= ?",
                (uid, candidate_dates[0], candidate_dates[-1])
            ).fetchall()
            for ev in events:
                for h in range(ev["start_hour"], ev["end_hour"]):
                    busy_set.add((ev["date"], h))

            # 반복 일정 (공휴일 제외)
            recurring = conn.execute(
                "SELECT day_of_week, start_hour, end_hour FROM recurring_events WHERE user_id=?", (uid,)
            ).fetchall()
            for d in candidate_dates:
                if _is_holiday(d):
                    continue
                dt = date.fromisoformat(d)
                dow = dt.weekday()  # 0=월 ... 6=일
                for rec in recurring:
                    if rec["day_of_week"] == dow:
                        for h in range(rec["start_hour"], rec["end_hour"]):
                            busy_set.add((d, h))

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
        "expired": expired,
        "user_consented": True if expired else None,
        "participants": [p["name"] for p in participants],
        "pending_invites": pending_invites,
        "pending_guests": pending_guests,
        "pending_consent": pending_consent_names if expired else [],
        "free_dates": free_dates,
        "total": len(participants),
    }
