from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
import json
import os
import hmac
import hashlib
import secrets
import unicodedata
import base64
import binascii
import uvicorn
import asyncio
import copy
import re
import uuid
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from pymongo.write_concern import WriteConcern

# 뽀모 시트 주소는 MongoDB가 아니라 main.py 옆의 작은 설정 파일에 저장합니다.
DEFAULT_POMO_URL = "https://docs.google.com/spreadsheets/d/1biXQLjPWNip4XyXCxLB6uyO-x8sah0CR5JT-fGk_GU0/edit?gid=1626585636#gid=1626585636"
POMO_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pomo_config.json")
# 암호는 소스에 두지 않고 실행 환경에서 받습니다. 역할별로 닫힙니다(실수로 열리지 않게):
# 방장 암호가 비면 방장 로그인만, 방 암호가 비면 멤버 로그인만 거절합니다. 둘이 같으면 방장 로그인을 거절합니다.
# 비었을 때 예전 암호나 다른 인증으로 넘어가는 길은 두지 않습니다.
ADMIN_PASSWORD_SERVER = os.environ.get("GOGOGO_ADMIN_PASSWORD", "")
ROOM_PASSWORD_SERVER = os.environ.get("GOGOGO_ROOM_PASSWORD", "")
if not ADMIN_PASSWORD_SERVER:
    print("경고: GOGOGO_ADMIN_PASSWORD 가 비어 있어 방장 로그인이 거절됩니다.")
if not ROOM_PASSWORD_SERVER:
    print("경고: GOGOGO_ROOM_PASSWORD 가 비어 있어 멤버 로그인이 거절됩니다.")
# (둘 다 해시로 넣으면 소금이 달라 같은 암호인지 알 수 없습니다 — 다른 암호를 쓰는 것은 운영 절차로 지킵니다.)
# 이 서버는 프로세스 하나(uvicorn 워커 1개)로만 돌려야 합니다. 이름 잠금·열쇠 캐시·실패 기록이 프로세스 안에 있어,
# 여러 워커·인스턴스면 재발급이 다른 프로세스에 바로 반영되지 않습니다. 워커 수를 늘리려면 이 부분을 DB 기반으로 바꿔야 합니다.
if os.environ.get("WEB_CONCURRENCY", "1").strip() not in ("", "1"):
    print("경고: WEB_CONCURRENCY 가 1이 아닙니다. 이 서버는 워커 1개로만 돌려야 합니다(이름 열쇠 잠금·캐시가 프로세스 안에 있음).")
if ADMIN_PASSWORD_SERVER and ADMIN_PASSWORD_SERVER == ROOM_PASSWORD_SERVER:
    print("경고: 방장 암호와 방 암호가 같아 방장 로그인이 거절됩니다.")
    ADMIN_PASSWORD_SERVER = ""

# env 값은 암호 그대로이거나, make_password_hash.py 가 만든 "pbkdf2_sha256$반복$소금$해시" 입니다.
# 해시를 넣으면 서버 설정(docker 설정 파일 등)에 암호 원문이 남지 않습니다.
def password_ok(given, expected):
    if not expected or not isinstance(given, str):
        return False
    if expected.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = expected.split("$")
            got = hashlib.pbkdf2_hmac("sha256", given.encode("utf-8"), base64.b64decode(salt), int(rounds))
            return hmac.compare_digest(base64.b64encode(got).decode("ascii"), digest)
        except (ValueError, TypeError, binascii.Error):
            return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))

async def password_ok_async(given, expected):
    # 해시 확인은 0.1초 넘게 걸릴 수 있어 방 처리와 따로 돌립니다.
    return await asyncio.to_thread(password_ok, given, expected)

def identity_hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

# 닉네임은 NFC로 맞추고, 보이지 않는 글자·제어 글자·시스템 이름은 받지 않습니다(같아 보이는 다른 이름 방지).
RESERVED_NICKNAMES = {"연결중...", "익명", "📢시스템", "🎉시스템"}

def normalize_nickname(raw):
    if not isinstance(raw, str):
        return ""
    name = unicodedata.normalize("NFC", raw).strip()
    if not 1 <= len(name) <= 20 or name in RESERVED_NICKNAMES or name.startswith("자리"):
        return ""
    for ch in name:
        if ch in NICKNAME_FILLERS:
            return ""
        if unicodedata.category(ch)[0] in "LN":
            continue
        if ch in NICKNAME_SYMBOLS:
            continue
        return ""
    if "  " in name:
        return ""
    return name

# 글자(L*)·숫자(N*)와 몇 가지 기호만 받습니다. 한글 채움 글자처럼 글자로 분류되지만 보이지 않는 것은 뺍니다.
NICKNAME_SYMBOLS = set(" _-.·~!?()[]'")
NICKNAME_FILLERS = {"\u3164", "\uffa0", "\u115f", "\u1160", "\u2800", "\u17b4", "\u17b5", "\u180b", "\u180c", "\u180d", "\u180e"}

def name_id(nickname):
    # DB 필드 이름으로 쓰므로 닉네임 대신 해시를 씁니다('.'·'$' 같은 글자가 경로로 해석되지 않게).
    return hashlib.sha256(nickname.encode("utf-8")).hexdigest()

ENROLL_TTL_SECONDS = 24 * 3600
ENROLL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def new_enroll_code():
    return "".join(secrets.choice(ENROLL_ALPHABET) for _ in range(12))

def normalize_enroll_code(raw):
    return "".join(ch for ch in raw.upper() if ch in ENROLL_ALPHABET) if isinstance(raw, str) else ""

# 카드 배경은 그림 data URL만 받습니다(따옴표·괄호가 들어갈 수 없는 모양).
CARD_BG_RE = re.compile(r"data:image/(?:png|jpeg|webp|bmp|avif);base64,[A-Za-z0-9+/]+={0,2}")

MAX_CARD_BG_CHARS = 3_000_000  # 약 2.2MB 그림

def is_safe_card_bg(value):
    return value is None or (isinstance(value, str) and len(value) <= MAX_CARD_BG_CHARS and CARD_BG_RE.fullmatch(value) is not None)

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
MONTH_RE = re.compile(r"\d{4}-\d{2}")

def bounded_int(value, top):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= top else None

def clean_tracker_fields(incoming):
    # 집필 기록에서 받는 값의 모양과 크기를 정합니다(문서가 커지거나 CSS가 섞이지 않게).
    out = {}
    if "targetChars" in incoming and bounded_int(incoming["targetChars"], 10_000_000) is not None:
        out["targetChars"] = bounded_int(incoming["targetChars"], 10_000_000)
    if isinstance(incoming.get("themeColor"), str) and HEX_COLOR_RE.fullmatch(incoming["themeColor"]):
        out["themeColor"] = incoming["themeColor"]
    if isinstance(incoming.get("todos"), list):
        todos = []
        for todo in incoming["todos"][:200]:
            if isinstance(todo, dict) and isinstance(todo.get("text"), str):
                todos.append({"text": todo["text"][:500], "done": todo.get("done") is True, "time": str(todo.get("time") or "")[:40]})
        out["todos"] = todos
    if isinstance(incoming.get("monthlyGoals"), dict):
        goals = {}
        for month, goal in list(incoming["monthlyGoals"].items())[:120]:
            if isinstance(month, str) and MONTH_RE.fullmatch(month) and bounded_int(goal, 100_000_000) is not None:
                goals[month] = bounded_int(goal, 100_000_000)
        out["monthlyGoals"] = goals
    return out

# 화면이 로그인 뒤 서버로 보내는 type 목록. 이 밖의 type(서버만 보내는 auth_ok·kicked 등 포함)은 받지 않습니다.
RELAY_TYPES = {"username_change", "card_bg_change", "toggle_mosaic", "start_share", "stop_share", "update_notice",
               "status_update", "request_existing_shares", "request_offer", "offer", "answer", "ice"}
CLIENT_TYPES = RELAY_TYPES | {"seat_reservation", "goal_celebration_chat", "timer_action", "delete_chat", "clear_chat",
                              "kick", "issue_enrollment", "chat", "attendance", "tracker_update"}
# 화면 공유 신호는 받을 사람(target)에게만 보냅니다.
TARGETED_TYPES = {"request_offer", "offer", "answer", "ice"}

def is_valid_pomo_url(url):
    if not isinstance(url, str):
        return False
    url = url.strip()
    return url.startswith("https://docs.google.com/spreadsheets/")

def load_pomo_url():
    try:
        if os.path.exists(POMO_CONFIG_PATH):
            with open(POMO_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = data.get("url", "") if isinstance(data, dict) else ""
            if is_valid_pomo_url(url):
                return url.strip()
    except Exception as e:
        print("뽀모 시트 설정 읽기 실패, 기본 주소 사용:", e)
    return DEFAULT_POMO_URL

def save_pomo_url(url):
    url = str(url or "").strip()
    if not is_valid_pomo_url(url):
        raise ValueError("구글 스프레드시트 주소만 저장할 수 있습니다.")
    temp_path = POMO_CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"url": url}, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, POMO_CONFIG_PATH)
    return url

# [디오 최종 방어막 수정: 0.5초 기다렸다가 변수를 읽어오게 해서 Empty host 원천 차단!]
import time
time.sleep(0.5)

collection = None
MONGO_URI = os.environ.get("MONGO_URI", "").strip()

try:
    if MONGO_URI:
        client = MongoClient(
            MONGO_URI,
            maxPoolSize=50, 
            waitQueueTimeoutMS=5000, 
            socketTimeoutMS=30000, 
            serverSelectionTimeoutMS=3000, 
            retryWrites=True
        )
        db = client["dashboard_db"]
        collection = db["dashboard_data"]
        client.admin.command('ping')
    else:
        print("경고: MONGO_URI 환경 변수가 비어 있습니다.")
except Exception as e:
    print("망고로드 첫 연결 실패 (나중에 다시 시도됨):", e)

def load_data():
    initial_data = {
        "_id": "main_state",
        "cards": [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": "", "reserved_by": None} for i in range(16)],
        "chat_history": [],
        "global_notice": "📌 다 함께 모여서 열심히 마감해 봅시다!",
        "attendance": {},
        "admin_log": [],
        "trackers": {}
    }
    try:
        if collection is not None:
            data = collection.find_one({"_id": "main_state"}, {"cards.card_bg": 0})
            if data:
                cards = data.get("cards", [])
                if len(cards) > 16:
                    data["cards"] = cards[:16]
                elif len(cards) < 16:
                    new_cards = [{"id": i, "user": f"자리{i+1}", "card_bg": None, "is_mosaic": False, "is_large": False, "status": "", "reserved_by": None} for i in range(len(cards), 16)]
                    data["cards"].extend(new_cards)
                for i, card in enumerate(data["cards"]):
                    card["user"] = card.get("user") if isinstance(card.get("user"), str) and card.get("user") else f"자리{i+1}"
                    card["is_mosaic"] = card.get("is_mosaic", False)
                    card["is_large"] = card.get("is_large", False)
                    raw_status = card.get("status", "")
                    card["status"] = raw_status.strip()[:30] if isinstance(raw_status, str) else ""
                    reserved_by = card.get("reserved_by")
                    card["reserved_by"] = reserved_by if isinstance(reserved_by, str) and reserved_by.strip() else None
                    if card["reserved_by"]:
                        card["user"] = card["reserved_by"]
                if "global_notice" not in data:
                    data["global_notice"] = "📌 다 함께 모여서 열심히 마감해 봅시다!"
                if "attendance" not in data:
                    data["attendance"] = {}
                if "admin_log" not in data:
                    data["admin_log"] = []
                if "trackers" not in data:
                    data["trackers"] = {}
                return data
            else:
                collection.update_one({"_id": "main_state"}, {"$set": initial_data}, upsert=True)
                return initial_data
        else:
            return initial_data
    except Exception as e:
        print("망고로드 초기화 에러 (하지만 서버는 죽지 않습니다!):", e)
        raise RuntimeError("DB를 읽지 못해 기존 기록 보호를 위해 시작을 중단합니다.") from e

IDENTITY_FIELDS = ("identity_keys", "identity_enroll")

# --- 신원 등록·초기화: DB가 저장을 확인(majority·journal)해야 승인합니다. 실패하면 로그인시키지 않습니다. ---
def durable_collection():
    if collection is None:
        raise RuntimeError("DB 연결이 없어 신원을 저장할 수 없습니다.")
    return collection.with_options(write_concern=WriteConcern(w="majority", j=True, wtimeout=5000))

def db_claim_identity(nid, key_hash):
    # 새 이름: 열쇠도 등록 대기도 없을 때만 한 번 저장됩니다(동시 요청 중 하나만 성공).
    result = durable_collection().update_one(
        {"_id": "main_state", f"identity_keys.{nid}": {"$exists": False}, f"identity_enroll.{nid}": {"$exists": False}},
        {"$set": {f"identity_keys.{nid}": key_hash}})
    return result.modified_count == 1

def db_enroll_identity(nid, code_hash, key_hash, now):
    # 등록 코드: 맞고 기한 안일 때 열쇠를 저장하며 코드를 지웁니다(한 번만 쓰임).
    result = durable_collection().update_one(
        {"_id": "main_state", f"identity_enroll.{nid}.h": code_hash, f"identity_enroll.{nid}.exp": {"$gt": now}},
        {"$set": {f"identity_keys.{nid}": key_hash}, "$unset": {f"identity_enroll.{nid}": ""}})
    return result.modified_count == 1

def db_issue_enrollment(nid, code_hash, expires):
    # 방장: 기존 열쇠를 지우고 새 등록 코드를 둡니다.
    result = durable_collection().update_one(
        {"_id": "main_state"},
        {"$unset": {f"identity_keys.{nid}": ""}, "$set": {f"identity_enroll.{nid}": {"h": code_hash, "exp": expires}}})
    return result.matched_count == 1

def has_member_data(nickname):
    # 이미 기록이 있는 이름(집필 기록·출석·고정석·채팅)은 방장 등록 코드 없이 새로 차지할 수 없습니다.
    return nickname in member_names()

identity_locks = {}

def identity_lock(nickname):
    return identity_locks.setdefault(name_id(nickname), asyncio.Lock())

def loose_name(raw):
    # 기존 기록 키 비교용(옛 데이터가 NFC가 아니어도 같은 이름으로 봄)
    return unicodedata.normalize("NFC", raw).strip() if isinstance(raw, str) else ""

def skeleton(name):
    # 띄어쓰기·기호·대소문자를 뺀 뼈대. 뼈대가 같은 다른 이름은 새로 만들 수 없습니다(부엉·부 엉·부엉. 등).
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name)).casefold()

def member_names():
    names = set()
    for name in server_state.get("trackers", {}):
        names.add(loose_name(name))
    for month in server_state.get("attendance", {}).values():
        if isinstance(month, dict):
            names.update(loose_name(n) for n in month)
    for card in server_state.get("cards", []):
        names.add(loose_name(card.get("reserved_by")))
    for c in server_state.get("chat_history", []):
        names.add(loose_name(c.get("senderName")))
    names.discard("")
    return names

MAX_SELF_CLAIMS = 500

async def check_member_identity(nickname, packet):
    """멤버 이름 열쇠 판정. (실패 사유, 새로 준 열쇠)를 돌려줍니다. 호출자는 이름별 잠금을 잡고 있어야 합니다."""
    stored_keys = server_state.setdefault("identity_keys", {})
    pending_enrolls = server_state.setdefault("identity_enroll", {})
    nid = name_id(nickname)
    if nid in stored_keys:
        given_key = packet.get("identity_key")
        if isinstance(given_key, str) and hmac.compare_digest(identity_hash(given_key), stored_keys[nid]):
            return None, None
        return "identity", None
    new_key = secrets.token_urlsafe(32)
    try:
        if nid in pending_enrolls or nickname in member_names():
            code = normalize_enroll_code(packet.get("enroll_code"))
            if not code:
                return "enroll_required", None
            if await asyncio.to_thread(db_enroll_identity, nid, identity_hash(code), identity_hash(new_key), time.time()):
                stored_keys[nid] = identity_hash(new_key)
                pending_enrolls.pop(nid, None)
                return None, new_key
            return "enroll_invalid", None
        # 아무 기록도 없는 새 이름: 기존 이름·방장과 뼈대가 같으면 거절, 너무 많으면 방장 코드로
        bone = skeleton(nickname)
        if not bone or bone == skeleton(ADMIN_NICKNAME) or bone in {skeleton(n) for n in member_names()}:
            return "nickname", None
        if len(stored_keys) >= MAX_SELF_CLAIMS:
            return "enroll_required", None
        if await asyncio.to_thread(db_claim_identity, nid, identity_hash(new_key)):
            stored_keys[nid] = identity_hash(new_key)
            return None, new_key
        return "identity", None
    except Exception as exc:
        print("신원 저장 실패(로그인 거절):", type(exc).__name__)
        return "storage", None

# 로그인 실패 제한(무차별 대입 늦추기). 둘 다 메모리에만 있고, 맞는 암호인지 알려 주기 전에 판정합니다.
# - 출처별: 같은 출처가 5분에 10번 틀리면 그 출처만 5분 쉼. 출처는 프록시가 붙인 X-Forwarded-For 의 마지막 값,
#   없으면 접속 주소입니다. X-Forwarded-For 가 없으면 모든 외부 접속이 한 출처로 보이므로 출처별 제한을 쓰지 않습니다.
# - 전역 상한: 모든 출처를 합쳐 1분에 300번 넘게 틀리면 1분 쉼(위조 출처로 피하는 대입의 상한).
# 회복: 출처별 제한은 5분, 전역 상한은 1분 뒤 저절로 풀립니다. 운영 쪽 컨테이너 재시작으로도 바로 풀립니다(설정은 유지).
AUTH_FAIL_SOURCE_WINDOW = 300
AUTH_FAIL_SOURCE_LIMIT = 10
AUTH_FAIL_GLOBAL_WINDOW = 60
AUTH_FAIL_GLOBAL_LIMIT = 300
AUTH_SOURCES_MAX = 5000
auth_failures = []           # 전역 실패 시각
auth_failures_by_source = {}  # 출처 → 실패 시각들

def auth_source(websocket):
    forwarded = websocket.headers.get("x-forwarded-for", "")
    last = forwarded.split(",")[-1].strip()[:64] if forwarded else ""
    return ("fwd:" + last) if last else None

def note_auth_failure(source=None):
    now = time.time()
    auth_failures.append(now)
    if source:
        if source not in auth_failures_by_source and len(auth_failures_by_source) >= AUTH_SOURCES_MAX:
            auth_failures_by_source.pop(next(iter(auth_failures_by_source)))
        auth_failures_by_source.setdefault(source, []).append(now)

def auth_throttled(source=None):
    now = time.time()
    while auth_failures and auth_failures[0] < now - AUTH_FAIL_GLOBAL_WINDOW:
        auth_failures.pop(0)
    if len(auth_failures) >= AUTH_FAIL_GLOBAL_LIMIT:
        return True
    if source and source in auth_failures_by_source:
        recent = [t for t in auth_failures_by_source[source] if t >= now - AUTH_FAIL_SOURCE_WINDOW]
        if recent:
            auth_failures_by_source[source] = recent
        else:
            auth_failures_by_source.pop(source, None)
        return len(recent) >= AUTH_FAIL_SOURCE_LIMIT
    return False

SEND_TIMEOUT_SECONDS = 5

async def send_limited(conn, text):
    # 읽지 않는 연결 하나가 방 전체를 붙잡지 않게 합니다.
    await asyncio.wait_for(conn.send_text(text), SEND_TIMEOUT_SECONDS)

def save_data(data):
    try:
        if collection is not None:
            # Patch individual card fields: an unloaded background must remain in MongoDB.
            fields = {key: value for key, value in data.items() if key not in ("_id", "cards") + IDENTITY_FIELDS}
            for index, card in enumerate(data.get("cards", [])):
                for key, value in card.items():
                    fields[f"cards.{index}.{key}"] = value
            collection.update_one({"_id": "main_state"}, {"$set": fields}, upsert=False)
    except Exception as e:
        print("망고로드 저장 에러:", e)
        raise

server_state = load_data()
for message in server_state.get("chat_history", []):
    message.setdefault("id", uuid.uuid4().hex)
save_task = None
save_dirty = False
save_error = None

async def save_worker():
    """One writer, bounded pending work, retry without blocking room traffic."""
    global save_dirty, save_error
    while save_dirty:
        save_dirty = False
        snapshot = copy.deepcopy(server_state)
        try:
            if collection is None:
                raise RuntimeError("MONGO_URI 연결이 없어 기록을 DB에 저장할 수 없습니다.")
            await asyncio.to_thread(save_data, snapshot)
            save_error = None
        except Exception as exc:
            save_error = str(exc)
            save_dirty = True
            print("DB 저장 재시도 예정:", exc)
            await asyncio.sleep(2)

async def persist_state():
    global save_dirty, save_task
    save_dirty = True
    if save_task is None or save_task.done():
        save_task = asyncio.create_task(save_worker())

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # 자동 API 문서 숨김

def require_session(request):
    if manager.sessions.get(request.headers.get("x-gogogo-session", "")) not in manager.authed_connections:
        raise HTTPException(status_code=403, detail="로그인 뒤에만 볼 수 있습니다.")

@app.get("/pomo-config")
def get_pomo_config(request: Request):
    require_session(request)
    return {"ok": True, "url": load_pomo_url()}

@app.post("/pomo-config")
async def update_pomo_config(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="요청 형식이 올바르지 않습니다.")
    conn = manager.sessions.get(request.headers.get("x-gogogo-session", ""))
    if conn not in manager.authed_connections or conn not in admin_sockets:
        raise HTTPException(status_code=403, detail="방장만 뽀모 시트 주소를 바꿀 수 있습니다.")
    try:
        url = save_pomo_url(payload.get("url", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print("뽀모 시트 설정 저장 실패:", e)
        raise HTTPException(status_code=500, detail="시트 주소를 저장하지 못했습니다.")
    return {"ok": True, "url": url}

@app.on_event("shutdown")
async def flush_pending_save():
    shutdown_at = time.time()
    shutdown_dt = datetime.fromtimestamp(shutdown_at, KST)
    paused_any = False
    for nickname in set(manager.active_users.values()):
        if not nickname or nickname == "연결중...":
            continue
        tracker = normalize_tracker(nickname, shutdown_dt)
        timer = tracker["timer"]
        if timer.get("started"):
            timer["seconds"] += max(0, shutdown_at - timer["started"])
            timer["started"] = None
            normalize_tracker(nickname, shutdown_dt)
            paused_any = True
    if paused_any:
        await persist_state()
    if save_task is not None and not save_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(save_task), timeout=25)
        except asyncio.TimeoutError:
            print("종료 전 DB 저장을 완료하지 못했습니다:", save_error)

class ConnectionManager:
    def __init__(self):
        self.active_connections = []
        self.active_shares = {}
        self.active_users = {}
        self.active_slots = {}
        self.authed_connections = set()  # 암호 확인을 마친 연결. 방송·개별 갱신은 여기에만 보냄
        self.sessions = {}  # HTTP 조회용 세션 값 → 연결(메모리에만)

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.active_users[websocket] = "연결중..."

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self.active_users:
            del self.active_users[websocket]
        self.authed_connections.discard(websocket)
        for token in [t for t, conn in self.sessions.items() if conn is websocket]:
            del self.sessions[token]
            
        disconnected_client = str(id(websocket))
        freed_indexes = []
        to_remove = [idx for idx, cid in self.active_shares.items() if cid == disconnected_client]
        for idx in to_remove:
            del self.active_shares[idx]
            freed_indexes.append(idx)
        return freed_indexes

    async def broadcast(self, message, exclude=None):
        async def send_to_client(conn):
            if conn != exclude:
                try:
                    await send_limited(conn, message)
                except Exception:
                    self.disconnect(conn)

        tasks = [asyncio.create_task(send_to_client(conn)) for conn in list(self.authed_connections)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_to_client_id(self, client_id, message):
        for conn in list(self.authed_connections):
            if str(id(conn)) == client_id:
                try:
                    await send_limited(conn, message)
                except Exception:
                    self.disconnect(conn)
                return

    async def broadcast_user_list(self):
        users_info = [{"clientId": str(id(ws)), "nickname": name} for ws, name in self.active_users.items() if ws in self.authed_connections]
        msg = json.dumps({"type": "user_list", "count": len(self.authed_connections), "users": users_info})
        
        async def send_to_client(conn):
            try:
                await send_limited(conn, msg)
            except Exception:
                self.disconnect(conn)

        tasks = [asyncio.create_task(send_to_client(conn)) for conn in list(self.authed_connections)]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

manager = ConnectionManager()

KST = timezone(timedelta(hours=9))
ADMIN_NICKNAME = "부엉"
PRESENCE_RECONNECT_GRACE_SECONDS = 5
pending_presence_leaves = {}
KICK_BLOCK_SECONDS = 600
kicked_until = {}  # 강퇴된 닉네임 → 다시 들어올 수 있는 시각(메모리에만)

async def send_seat_error(websocket, index, message):
    card = server_state["cards"][index]
    await websocket.send_text(json.dumps({
        "type": "seat_reservation_error",
        "index": index,
        "user": card["user"],
        "reserved_by": card.get("reserved_by"),
        "message": message
    }))

async def post_presence_chat(message):
    chat_obj = {
        "id": uuid.uuid4().hex,
        "senderName": "📢시스템",
        "msg": message,
        "time": datetime.now(KST).strftime("%m/%d %H:%M")
    }
    server_state.setdefault("chat_history", []).append(chat_obj)
    if len(server_state["chat_history"]) > 100:
        server_state["chat_history"].pop(0)
    await manager.broadcast(json.dumps({"type": "chat", **chat_obj}))
    await persist_state()

async def post_leave_after_reconnect_grace(nickname, disconnected_at):
    current_task = asyncio.current_task()
    try:
        await asyncio.sleep(PRESENCE_RECONNECT_GRACE_SECONDS)
        if any(name == nickname for name in manager.active_users.values()):
            return
        await set_tracker_timer_running(nickname, False, disconnected_at)
        await post_presence_chat(f"🚪 «{nickname}» 작가님이 퇴장하셨습니다.")
    except asyncio.CancelledError:
        return
    finally:
        if pending_presence_leaves.get(nickname) is current_task:
            pending_presence_leaves.pop(nickname, None)

def normalize_tracker(nickname, now=None):
    now = now or datetime.now(KST)
    day = f"{now.year}-{now.month}-{now.day}"
    data = server_state.setdefault("trackers", {}).setdefault(nickname, {})
    timer = data.setdefault("timer", {"date": data.get("lastDate", day), "seconds": 0, "started": None})
    if not timer.get("migrated"):
        old = data.get("calendar", {}).get(now.strftime("%Y-%m"), {}).get(str(now.day), {})
        if timer["date"] == day:
            timer["seconds"] = old.get("seconds", 0)
        timer["migrated"] = True
    if timer["date"] != day:
        old_date = timer["date"]
        try:
            y, m, d = map(int, old_date.split("-"))
            boundary = datetime(y, m, d, tzinfo=KST) + timedelta(days=1)
            elapsed = timer["seconds"] + (max(0, boundary.timestamp() - timer["started"]) if timer.get("started") else 0)
            record = data.setdefault("calendar", {}).setdefault(f"{y}-{m:02d}", {}).setdefault(str(d), {})
            record["seconds"] = int(elapsed)
        except (ValueError, TypeError):
            pass
        timer.update(date=day, seconds=0, started=None)
        data["doneChars"] = 0
    data["lastDate"] = day
    elapsed = timer["seconds"] + (max(0, now.timestamp() - timer["started"]) if timer.get("started") else 0)
    record = data.setdefault("calendar", {}).setdefault(now.strftime("%Y-%m"), {}).setdefault(str(now.day), {})
    record.update(target=data.get("targetChars", 5000), done=data.get("doneChars", 0), seconds=int(elapsed))
    return data

admin_sockets = set()

def state_for_conn(nickname, is_admin):
    result = state_for(nickname)
    if not is_admin:
        result.pop("admin_log", None)
    return result

REVOKE_TIMEOUT_SECONDS = 2
revoke_tasks = set()

async def revoke_connection(conn, notice=None):
    # 권한(인증·방장·HTTP 세션)은 기다림 없이 바로 거둡니다.
    manager.authed_connections.discard(conn)
    admin_sockets.discard(conn)
    for token in [t for t, c in manager.sessions.items() if c is conn]:
        del manager.sessions[token]

    async def finish():
        if notice:
            try:
                await asyncio.wait_for(conn.send_text(json.dumps(notice)), REVOKE_TIMEOUT_SECONDS)
            except Exception:
                pass
        try:
            await asyncio.wait_for(conn.close(), REVOKE_TIMEOUT_SECONDS)
        except Exception:
            pass

    # 알림과 닫기는 따로 돌려, 느린 연결이 호출한 쪽(이름 잠금 포함)을 붙잡지 않게 합니다. 최대 약 4초 안에 끝납니다.
    task = asyncio.create_task(finish())
    revoke_tasks.add(task)
    task.add_done_callback(revoke_tasks.discard)

pending_revocations = {}  # nid → 닉네임: 열쇠 해제가 아직 DB에 저장되지 않은 이름
revocation_task = None

async def add_admin_log(message):
    log_entry = {"msg": message, "time": time.time()}
    server_state.setdefault("admin_log", []).append(log_entry)
    if len(server_state["admin_log"]) > 100:
        server_state["admin_log"].pop(0)
    await persist_state()
    await send_admin_log(log_entry)

async def retry_revocations():
    """재발급 때 DB 저장이 실패한 열쇠 해제를 5초마다 다시 저장합니다(프로세스가 살아 있는 동안).
    저장되기 전에 서버가 다시 켜지면 DB의 옛 열쇠가 되살아나므로, 방장은 '해제 저장됨' 기록을 확인해야 합니다."""
    while pending_revocations:
        await asyncio.sleep(5)
        for nid, nick in list(pending_revocations.items()):
            async with identity_lock(nick):
                if server_state.get("identity_enroll", {}).get(nid, {}).get("h"):
                    pending_revocations.pop(nid, None)  # 그사이 코드 발급이 저장됨
                    continue
                try:
                    saved = await asyncio.to_thread(db_issue_enrollment, nid, "", 0)  # 열쇠 지움 + 코드 없는 등록 대기
                except Exception:
                    saved = False
                if saved:
                    pending_revocations.pop(nid, None)
                    await add_admin_log(f"🔑 {nick} 열쇠 해제가 DB에 저장됨 — 이제 등록 코드를 다시 만들어 주세요")

async def send_admin_log(log_entry):
    packet = json.dumps({"type": "admin_log_update", "log": log_entry})
    for conn in list(admin_sockets):
        try:
            await send_limited(conn, packet)
        except Exception:
            pass

def public_tracker(data):
    return {k: copy.deepcopy(v) for k, v in data.items() if k != "todos"}

def state_for(nickname):
    result = {k: copy.deepcopy(v) for k, v in server_state.items() if k not in ("trackers",) + IDENTITY_FIELDS}
    result["trackers"] = {name: (copy.deepcopy(normalize_tracker(name)) if name == nickname else public_tracker(normalize_tracker(name))) for name in list(server_state.get("trackers", {}))}
    return result
async def publish_tracker(nickname):
    current = normalize_tracker(nickname)
    async def send(conn):
        name = manager.active_users.get(conn)
        packet = {"type":"tracker_update", "nickname":nickname, "tracker_data":copy.deepcopy(current) if name == nickname else public_tracker(current)}
        try:
            await send_limited(conn, json.dumps(packet))
        except Exception:
            pass
    await asyncio.gather(*(send(conn) for conn in list(manager.authed_connections)))

async def set_tracker_timer_running(nickname, should_run, event_time=None):
    if not nickname or nickname == "연결중...":
        return
    timestamp = event_time if event_time is not None else time.time()
    event_dt = datetime.fromtimestamp(timestamp, KST)
    current = normalize_tracker(nickname, event_dt)
    timer = current["timer"]
    changed = False
    if should_run:
        if timer.get("started") is None:
            timer["started"] = timestamp
            changed = True
    elif timer.get("started") is not None:
        timer["seconds"] += max(0, timestamp - timer["started"])
        timer["started"] = None
        changed = True
    normalize_tracker(nickname, event_dt)
    if changed:
        await persist_state()
        await publish_tracker(nickname)

@app.on_event("startup")
async def pause_stale_timers_before_first_login():
    startup_at = time.time()
    startup_dt = datetime.fromtimestamp(startup_at, KST)
    changed = False
    for nickname in list(server_state.get("trackers", {})):
        tracker = normalize_tracker(nickname, startup_dt)
        timer = tracker["timer"]
        if timer.get("started") is not None:
            timer["seconds"] += max(0, startup_at - timer["started"])
            timer["started"] = None
            normalize_tracker(nickname, startup_dt)
            changed = True
    if changed:
        await persist_state()

background_read_lock = asyncio.Lock()

def read_card_background(index):
    if collection is None:
        raise RuntimeError("DB connection unavailable")
    pipeline = [
        {"$match": {"_id": "main_state"}},
        {"$project": {"_id": 0, "card": {"$arrayElemAt": ["$cards", index]}}},
        {"$project": {"_id": 0, "card_bg": "$card.card_bg"}},
    ]
    rows = list(collection.aggregate(pipeline))
    if not rows:
        raise RuntimeError("Room data unavailable")
    return rows[0].get("card_bg")

@app.get("/card-background/{index}")
async def card_background(index: int, request: Request):
    require_session(request)
    if not 0 <= index < 16:
        return {"ok": False}
    # Serialize large reads; chat and screen-sharing handlers remain available.
    async with background_read_lock:
        card = server_state["cards"][index]
        if "card_bg" in card:
            return {"ok": True, "card_bg": card["card_bg"] if is_safe_card_bg(card["card_bg"]) else None}
        try:
            value = await asyncio.to_thread(read_card_background, index)
            # A concurrent upload takes precedence over the older DB value.
            value = card.get("card_bg", value)
            return {"ok": True, "card_bg": value if is_safe_card_bg(value) else None}
        except Exception as exc:
            print("Background read deferred:", index, exc)
            return {"ok": False}


@app.get("/user_count")
def get_user_count():
    return {"count": len(manager.active_connections)}

@app.get("/", response_class=HTMLResponse)
def read_root():
    return r"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>🍀심사 합격 & 돈 긁어모으는 방🏆</title>
        <style>
            :root {
                --rec-primary: #d87093;
                --rec-bg: #ffffff;
                --rec-box: #ffffff;
                --rec-sec: #fff5f8;
                --rec-border: #ffb6c1;
                --rec-done: #ffe4e1;
            }
            
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Arial', sans-serif; }
            body, html { width: 100%; height: 100%; overflow-x: hidden; overflow-y: scroll; background: #111; touch-action: pan-y; }
            body::-webkit-scrollbar { width: 8px; }
            body::-webkit-scrollbar-track { background: #111; }
            body::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
            body::-webkit-scrollbar-thumb:hover { background: #666; }
            .login-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #111; z-index: 9999; display: flex; align-items: center; justify-content: center; flex-direction: column; }
            .login-box { background: rgba(30, 30, 40, 0.9); padding: 30px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.2); text-align: center; color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .login-box input { padding: 10px; margin-top: 10px; border-radius: 5px; border: none; width: 220px; text-align: center; }
            .login-box button { padding: 10px 20px; margin-top: 15px; border: none; border-radius: 5px; background: #ff7675; color: white; cursor: pointer; font-weight: bold; width: 100%; }
            .video-background { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; overflow: hidden; pointer-events: none; background: #000; }
            #bgMediaWrapper { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }
            #bgMediaWrapper img, #bgMediaWrapper iframe { width: 100vw; height: 100vh; object-fit: cover; display: block; border: none; pointer-events: none; }
            .overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.05); z-index: 1; pointer-events: none; }
            
            .main-container { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 15px; padding: 15px; min-height: 100vh; color: white; position: relative; z-index: 2; align-items: start; max-width: 1800px; margin: 0 auto; transition: grid-template-columns 0.3s ease; }
            
            .card-grid { display: grid; gap: 10px; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-flow: dense; width: 100%; align-content: start; min-width: 0; }
            /* 오른쪽 패널 320px를 뺀 실제 카드 영역 기준으로 열 수를 일찍 줄여 카드 내용이 잘리지 않게 함 */
            @media (max-width: 1500px) { .card-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
            @media (max-width: 1220px) { .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
            @media (max-width: 900px) {
                .main-container { grid-template-columns: 1fr !important; }
                .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
                .side-panel { position: static !important; top: auto; height: auto; width: 100%; }
                .chat-box { min-height: 360px; }
            }
            @media (max-width: 650px) { .card-grid { grid-template-columns: 1fr; } }
            .timer-card { background: rgba(20, 20, 30, 0.85); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255, 255, 255, 0.25); min-height: 250px; min-width: 0; position: relative; overflow: hidden; background-size: cover; background-position: center; transition: all 0.3s ease; container-type: inline-size; }
            .card-large { grid-column: span 2; grid-row: span 2; min-height: 510px; }
            @media (max-width: 650px) { .card-large { grid-column: span 1; grid-row: span 1; min-height: 510px; } }
            .card-header { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 20; width: 100%; min-width: 0; }

            /* 카드 폭이 줄면 상단 컨트롤 자체가 같이 작아지고, 줄바꿈은 하지 않음 */
            .card-title-row { display: flex; gap: 4px; align-items: center; width: 100%; min-width: 0; flex-wrap: nowrap; }
            .card-title-row .card-name-input { flex: 1 1 0; min-width: 58px !important; width: 0; }
            .card-title-row .share-btn { flex: 0 1 auto; min-width: 0; padding: 4px 5px; font-size: 10px; }

            .btn-group { display: flex; gap: 2px; width: 100%; justify-content: center; flex-wrap: nowrap; overflow: visible; min-width: 0; }
            .share-btn { padding: 4px 3px; font-size: 10px; color: white; border: none; border-radius: 3px; cursor: pointer; white-space: nowrap; font-weight: bold; text-align: center; max-width: 100%; transition: opacity 0.2s; line-height: 1.2; }
            .btn-group .share-btn { flex: 1 1 0; min-width: 0; padding-left: 2px; padding-right: 2px; }
            .share-btn:hover { opacity: 0.8; }

            /* 오늘 완료는 정확한 숫자를 쓰는 칸이라 입력 폭을 우선 보장 */
            .quick-done-row { display: none; align-items: center; gap: 5px; width: 100%; min-width: 0; flex-wrap: nowrap; margin-top: 4px; padding: 5px 6px; background: rgba(0,0,0,0.34); border: 1px solid rgba(255,255,255,0.16); border-radius: 5px; font-size: 11px; color: #fff; position: relative; }
            .quick-done-row input { flex: 1 1 120px; min-width: 105px; width: 120px; padding: 4px 6px; border: 1px solid rgba(255,255,255,0.35); border-radius: 4px; background: rgba(255,255,255,0.92); color: #222; text-align: right; font-size: 11px; font-weight: bold; }
            .quick-done-row button { flex: 0 0 auto; border: none; border-radius: 4px; padding: 4px 7px; background: #6c5ce7; color: white; font-size: 10px; font-weight: bold; cursor: pointer; }
            .quick-done-row button:hover { opacity: .85; }
            .quick-done-saved { position: absolute; right: 6px; bottom: -13px; color: #81ecec; font-size: 9px; white-space: nowrap; pointer-events: none; text-shadow: 0 1px 2px #000; }

            /* 실제 카드 폭 기준으로 버튼 글자/패딩을 단계적으로 축소 */
            @container (max-width: 360px) {
                .card-title-row { gap: 3px; }
                .card-title-row .card-name-input { min-width: 52px !important; font-size: 10px !important; padding-left: 2px !important; padding-right: 2px !important; }
                .card-title-row .share-btn { font-size: 9px; padding: 4px 3px !important; }
                .btn-group { gap: 1px; }
                .btn-group .share-btn { font-size: 9px; padding: 4px 1px; letter-spacing: -0.2px; }
                .quick-done-row { gap: 4px; padding-left: 5px; padding-right: 5px; font-size: 10px; }
                .quick-done-row input { min-width: 105px; font-size: 10px; }
                .quick-done-row button { font-size: 9px; padding: 4px 6px; }
            }
            @container (max-width: 300px) {
                .card-title-row .card-name-input { min-width: 46px !important; font-size: 9px !important; }
                .card-title-row .share-btn { font-size: 8px; padding: 3px 2px !important; }
                .btn-group .share-btn { font-size: 8px; padding: 3px 0; letter-spacing: -0.35px; }
                .quick-done-row { gap: 3px; padding: 4px; font-size: 9px; }
                .quick-done-row input { min-width: 100px; padding: 4px; font-size: 10px; }
                .quick-done-row button { font-size: 8px; padding: 4px 5px; }
            }
            @container (max-width: 245px) {
                .card-title-row .card-name-input { min-width: 42px !important; font-size: 8px !important; }
                .card-title-row .share-btn { font-size: 7px; padding: 3px 1px !important; }
                .btn-group .share-btn { font-size: 7px; padding: 3px 0; letter-spacing: -0.45px; }
                .quick-done-row { font-size: 8px; gap: 2px; }
                .quick-done-row input { min-width: 92px; font-size: 9px; }
                .quick-done-row button { font-size: 7px; padding: 3px 4px; }
            }
            .card-stream-box { width: 100%; flex-grow: 1; min-height: 135px; background: rgba(0, 0, 0, 0.15); border-radius: 8px; overflow: hidden; position: relative; margin-top: 6px; display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 2; pointer-events: none; transition: visibility 0.2s; }
            .card-stream-box video { width: 100%; height: 100%; object-fit: contain; background: transparent; position: absolute; top: 0; left: 0; z-index: 10; transition: filter 0.2s ease-in-out; pointer-events: auto; }
            .side-panel { display: flex; flex-direction: column; gap: 15px; position: sticky; top: 15px; height: calc(100vh - 30px); min-width: 0; }
            .panel-box { background: rgba(30, 30, 40, 0.85); border-radius: 12px; padding: 12px; border: 1px solid rgba(255, 255, 255, 0.2); min-width: 0; word-break: keep-all; overflow-wrap: break-word; }
            .settings-toggle-btn { border: none; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: bold; white-space: nowrap; }
            .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0, 0, 0, 0.7); z-index: 10000; align-items: center; justify-content: center; }
            .modal-box { background: rgba(30, 30, 40, 0.95); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 12px; padding: 20px; width: 350px; max-height: 80vh; overflow-y: auto; color: white; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
            .close-btn { position: absolute; top: 12px; right: 15px; background: transparent; border: none; color: white; font-size: 14px; cursor: pointer; font-weight: bold; transition: 0.2s; z-index: 100;}
            .close-btn:hover { color: #ff7675; }
            .chat-box { display: flex; flex-direction: column; flex-grow: 1; min-height: 0; height: 100%; transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease; }
            #chatHistory { flex-grow: 1; overflow-y: auto; margin-top: 8px; font-size: 14px; color: #ddd; line-height: 1.65; word-break: break-all; padding-right: 5px; }
            #chatHistory > div { padding: 4px 2px; border-bottom: 1px solid rgba(255,255,255,0.07); }
            .chat-input { display: flex; margin-top: 8px; gap: 4px; }
            .chat-input input { flex-grow: 1; padding: 9px; border-radius: 5px; border: none; background: rgba(255, 255, 255, 0.9); color: black; min-width: 0; font-size: 13px; }
            .chat-input button { padding: 9px 12px; background: #ff7675; border: none; color: white; border-radius: 5px; cursor: pointer; flex-shrink: 0; font-size: 13px; }
            .chat-box.chat-light { background: rgba(255, 253, 248, 0.96); color: #2d3436; border-color: rgba(70, 70, 70, 0.2); }
            .chat-box.chat-light h3, .chat-box.chat-light #chatHistory { color: #2d3436; }
            .chat-box.chat-light #chatHistory > div { border-bottom-color: rgba(45, 52, 54, 0.1); }
            .chat-box.chat-light #chatHistory > div > span,
            .chat-box.chat-light #chatHistory > div > span span { color: #777 !important; }
            .chat-box.chat-light .chat-input input { background: #fff; color: #2d3436; border: 1px solid #ddd; }
            .chat-box.chat-light .chat-delete-btn { color: #a06a6a !important; }
            .chat-box.chat-light .level-up-name { color: #c48a00 !important; text-shadow: none !important; }
            #dashboardPanel.dashboard-light { background: rgba(255, 253, 248, 0.96); color: #2d3436; border-color: rgba(70, 70, 70, 0.2); }
            #dashboardPanel.dashboard-light h3 { color: #2d3436; }
            #dashboardPanel.dashboard-light p { color: #666 !important; }
            #dashboardPanel.dashboard-light #userListStr > span { background: rgba(45, 52, 54, 0.08) !important; }
            #dashboardPanel.dashboard-light #userListStr b { color: #2d3436 !important; }
            #dashboardPanel.dashboard-light #userListStr b.level-up-name { color: #c48a00 !important; text-shadow: none !important; }
            .status-indicator { font-size: 11px; padding: 2px 6px; border-radius: 3px; display: inline-block; margin-left: 5px; }
            .status-online { background: #00b894; color: white; }
            .status-offline { background: #d63031; color: white; }
            .recovery-btn { background: #d63031; color: white; border: none; border-radius: 4px; padding: 3px 6px; font-size: 10px; cursor: pointer; font-weight: bold; white-space: nowrap; }
            .level-up-name { color: #ffd54a !important; text-shadow: 0 0 7px rgba(255, 213, 74, 0.75) !important; }
            
            .calendar-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; margin-bottom: 4px; }
            .cal-day { background: rgba(0,0,0,0.5); padding: 5px 0; text-align: center; border-radius: 3px; font-size: 11px; font-weight: bold; }
            .cal-day.today { border: 1px solid #ff7675; cursor: pointer; background: rgba(255, 118, 117, 0.2); }
            .cal-day.today:hover { background: rgba(255, 118, 117, 0.5); }
            .cal-day.stamped { background: rgba(39, 174, 96, 0.4); border: none; cursor: default; }
            
            .rec-container { background-color: var(--rec-bg); font-family: 'Malgun Gothic', sans-serif; color: #4a4a4a; padding: 24px; width: 100%; border-radius: 10px; box-sizing: border-box; }
            .rec-header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; flex-wrap: wrap; gap: 12px; border-bottom: 2px solid var(--rec-primary); padding-bottom: 14px; padding-right: 35px; }
            .rec-header-bar h1 { margin: 0; color: var(--rec-primary); font-size: 22px; font-weight: bold; }
            .rec-color-picker-box { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: bold; color: var(--rec-primary); }
            .rec-color-picker-box input[type="color"] { width: 25px; height: 25px; border: none; border-radius: 50%; cursor: pointer; padding: 0; background: none; }
            
            .rec-section { margin-bottom: 22px; padding: 20px; background: var(--rec-sec); border-radius: 12px; border: 1px solid var(--rec-border); display: flex; flex-direction: column; gap: 16px; }
            .rec-section h3 { margin: 0; color: var(--rec-primary); font-size: 17px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 12px; border-bottom: 1px dashed var(--rec-border); }
            
            .rec-record-box { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 24px; width: 100%; }
            .rec-timer-container { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; min-width: 190px; }
            .rec-timer-display { font-size: 30px; font-weight: bold; color: var(--rec-primary); line-height: 1; }
            .rec-input-edit { width: 110px; padding: 8px; border: 1px solid var(--rec-border); border-radius: 6px; font-size: 15px; text-align: right; background: #fff; color: #333; }
            
            .rec-banner { display: none; margin-top: 10px; padding: 8px; background: #fffacd; border: 1px solid #ffd700; border-radius: 5px; text-align: center; font-weight: bold; color: #b8860b; font-size: 13px; }
            .rec-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
            .rec-list li { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
            .rec-completed { text-decoration: line-through; color: #aaa; }
            .rec-time-tag { font-size: 12px; color: #888; margin-left: 10px; }
            
            .rec-todo-input-box { display: flex; gap: 5px; width: 100%; margin-top: 5px; }
            .rec-todo-input-box input { flex: 1; padding: 8px; border: 1px solid var(--rec-border); border-radius: 5px; font-size: 13px; background: #fff; color: #333; }
            
            .chat-delete-btn { display: inline; margin-left: 4px; padding: 0 2px; border: none; background: transparent; color: #b2bec3; font: inherit; font-size: 10px; line-height: 1.2; vertical-align: baseline; white-space: nowrap; cursor: pointer; }
            .chat-delete-btn:hover { color: #ff9999; text-decoration: underline; }
            .chat-delete-btn:focus-visible { outline: 1px solid #b2bec3; outline-offset: 2px; }
            .rec-btn { background: var(--rec-border); border: none; padding: 6px 12px; border-radius: 5px; cursor: pointer; font-weight: bold; color: #4a4a4a; font-size: 12px; white-space: nowrap; }
            .rec-btn:hover { opacity: 0.8; }
            .rec-btn-del { background: #ff9999; color: #fff; }
            
            .rec-cal-wrap { display: none; margin-top: 12px; overflow-x: auto; padding: 2px 2px 10px; }
            .rec-cal-grid { display: grid; grid-template-columns: repeat(7, minmax(105px, 1fr)); gap: 8px; text-align: center; font-size: 13px; min-width: 820px; }
            .rec-cal-header { font-weight: bold; color: var(--rec-primary); font-size: 14px; padding: 4px 0 8px; }
            .rec-cal-day { background: #fff; border: 1px solid var(--rec-border); border-radius: 7px; padding: 10px 7px; min-height: 104px; display: flex; flex-direction: column; justify-content: flex-start; gap: 4px; overflow: hidden; color: #333; line-height: 1.35; }
            .rec-cal-day.done { background: var(--rec-done); }
            .rec-cal-day .date-num { font-weight: bold; font-size: 14px; margin-bottom: 3px; }
            .rec-cal-day .goal { font-size: 12px; color: #666; white-space: nowrap; }
            .rec-cal-day .act { font-size: 12px; font-weight: bold; color: var(--rec-primary); white-space: nowrap; }
            .rec-cal-day .time { font-size: 12px; color: #555; white-space: nowrap; margin-top: 2px; }
            @media (max-width: 600px) {
                .rec-container { padding: 14px; }
                .rec-section { padding: 14px; margin-bottom: 16px; }
            }
        </style>
    </head>
    <body>
        <script>
            document.addEventListener('contextmenu', function(e) { e.preventDefault(); });
            document.addEventListener('keydown', function(e) {
                if (e.keyCode === 123 || (e.ctrlKey && e.shiftKey && (e.keyCode === 73 || e.keyCode === 74 || e.keyCode === 67)) || (e.ctrlKey && e.keyCode === 85)) { e.preventDefault(); return false; }
            });
            document.addEventListener('wheel', function(e) { if (e.ctrlKey) { e.preventDefault(); } }, { passive: false });
            document.addEventListener('touchstart', function(e) { if (e.touches.length > 1) { e.preventDefault(); } }, { passive: false });
        </script>
        
        <div class="login-overlay" id="loginOverlay">
            <div class="login-box">
                <h2>🔒 행운방 입장</h2>
                <p style="font-size: 13px; color: #aaa; margin-top: 5px; margin-bottom: 10px;">닉네임은 한 번만 적으면 저장 돼!</p>
                <div id="loginUserCount" style="margin-bottom: 15px; font-size: 14px; font-weight: bold; color: #00b894; background: rgba(0, 184, 148, 0.15); padding: 8px; border-radius: 6px; border: 1px solid rgba(0, 184, 148, 0.4);">🔥 현재 달리고 있는 작가님: 확인 중...</div>
                <input type="text" id="nickInput" placeholder="내 닉네임 (예: 부엉)" onkeypress="if(event.key==='Enter') login()"><br>
                <p id="loginStatus" role="status" style="max-width:300px;color:#ffeaa7;margin:8px auto;"></p>
                <input type="password" id="pwInput" placeholder="비밀번호" onkeypress="if(event.key==='Enter') login()"><br>
                <button onclick="login()">입장하기</button>
            </div>
        </div>
        
        <div id="noticeModal" class="modal-overlay" onclick="if(event.target===this) closeModal('noticeModal')">
            <div class="modal-box">
                <button class="close-btn" onclick="closeModal('noticeModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px;">📢 공지사항</h3>
                <div style="text-align: right; margin-bottom: 10px;">
                    <button onclick="addNotice()" style="background:#0984e3; color:white; border:none; border-radius:3px; padding:4px 8px; font-size:10px; cursor:pointer; font-weight:bold; margin-right: 3px;">➕ 추가</button>
                    <button onclick="editNotice()" style="background:#ff7675; color:white; border:none; border-radius:3px; padding:4px 8px; font-size:10px; cursor:pointer; font-weight:bold;">✏️ 수정/삭제</button>
                </div>
                <div id="noticeText" style="font-size: 13px; color: #fff; line-height: 1.6; word-break: break-all; background: rgba(0,0,0,0.3); padding: 10px; border-radius: 6px;">공지사항 로딩 중...</div>
            </div>
        </div>
        
        <div id="attendanceModal" class="modal-overlay" onclick="if(event.target===this) closeModal('attendanceModal')">
            <div class="modal-box" style="width: 380px;">
                <button class="close-btn" onclick="closeModal('attendanceModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px; text-align: center;">🏆 출석 현황</h3>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px; margin-bottom: 12px;">
                    <div style="font-size: 13px; font-weight: bold; color: #ffeaa7; margin-bottom: 8px; text-align: center;" id="calMonthTitle">🍀 내 출석부</div>
                    <div class="calendar-grid" id="calendarGrid"></div>
                    <div style="font-size: 10px; color: #aaa; text-align: center; margin-top: 6px;">일주일마다 출석 매달 등급이 바뀜!</div>
                </div>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px; margin-bottom: 12px;">
                    <div style="font-size: 13px; font-weight: bold; color: #ffeaa7; margin-bottom: 8px;" id="rankTitle">🏆 이번 주 출석 랭킹</div>
                    <div id="attRankingList" style="font-size: 12px; color: #ddd; line-height: 1.6; max-height: 100px; overflow-y: auto;">랭킹 로딩 중...</div>
                </div>
                <div style="background: rgba(0,0,0,0.4); padding: 12px; border-radius: 8px;">
                    <div style="font-size: 13px; font-weight: bold; color: #81ecec; margin-bottom: 8px;">✅ 오늘 출석한 사람</div>
                    <div id="attTodayList" style="font-size: 12px; color: #ddd; line-height: 1.6; display: flex; flex-wrap: wrap; gap: 4px;">대기 중...</div>
                </div>
            </div>
        </div>
        
        <div id="settingsModal" class="modal-overlay" onclick="if(event.target===this) closeModal('settingsModal')">
            <div class="modal-box">
                <button class="close-btn" onclick="closeModal('settingsModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px;">🖼️ 나만의 배경 (나에게만 보여요)</h3>
                <div>
                    <div style="font-size: 11px; color: #ccc; margin-bottom: 5px;">일반 사진 (움짤X):</div>
                    <input type="file" id="bgFileInput" accept="image/jpeg, image/png, image/webp" style="font-size:11px; width:100%; margin-bottom: 15px;" onchange="setLocalBackground(event)">
                    <div style="font-size: 11px; color: #ccc; margin-bottom: 5px;">유튜브 링크:</div>
                    <div style="display:flex; gap:5px;">
                        <input type="text" id="bgYoutubeInput" placeholder="URL 입력" style="flex-grow:1; font-size:11px; padding:5px; background:rgba(255,255,255,0.9); color:black; border:none; border-radius:3px; min-width:0;">
                        <button onclick="setYoutubeBackground()" style="font-size:11px; padding:5px 10px; background:#ff7675; border:none; color:white; border-radius:3px; cursor:pointer;">적용</button>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="adminLogModal" class="modal-overlay" onclick="if(event.target===this) closeModal('adminLogModal')">
            <div class="modal-box" style="width: 350px;">
                <button class="close-btn" onclick="closeModal('adminLogModal')">❌</button>
                <h3 style="margin-bottom: 15px; font-size: 16px; color: #ff7675;">👑 비밀 출입 기록</h3>
                <div id="adminLogContent" style="background: rgba(0,0,0,0.5); padding: 10px; border-radius: 8px; height: 250px; overflow-y: auto; font-size: 12px; color: #ddd; line-height: 1.6;">기록이 없습니다.</div>
                <button onclick="issueEnrollmentPrompt()" style="margin-top: 10px; padding: 6px 10px; border: none; border-radius: 5px; background: #636e72; color: white; cursor: pointer; font-size: 12px;">🔑 등록 코드 만들기(열쇠 재발급)</button>
            </div>
        </div>

        <div id="recordModal" class="modal-overlay" onclick="if(event.target===this) closeModal('recordModal')">
            <div class="modal-box" style="width: 980px; max-width: 96vw; max-height: 90vh; padding: 0; border: 2px solid var(--rec-border); background: #ffffff; overflow-x: hidden;">
                <button class="close-btn" onclick="closeModal('recordModal')" style="color: var(--rec-primary); text-shadow: 0 0 3px #fff; top: 18px;">❌</button>
                
                <div class="rec-container">
                    <div class="rec-header-bar">
                        <h1 id="rec-title">✨ 집필 기록방 ✨</h1>
                        <div class="rec-color-picker-box">
                            <div>
                                <label for="themeColorPicker">🎨 테마 색상:</label>
                                <input type="color" id="themeColorPicker" value="#d87093" oninput="changeThemeColor(this.value)" onchange="flushRecordAutoSave()">
                            </div>
                        </div>
                    </div>

                    <div class="rec-section">
                        <h3>
                            ⏱️ 오늘의 집필 기록
                            <span id="rec-autosave-status" style="font-size:12px; color:#777; font-weight:normal;">☁️ 자동 저장</span>
                        </h3>
                        <div class="rec-record-box">
                            <div style="display: flex; flex-direction: column; gap: 8px;">
                                <div>목표: <input type="number" id="rec-target-chars" class="rec-input-edit" oninput="checkGoalAchievement(); scheduleRecordAutoSave()" onchange="flushRecordAutoSave()">자</div>
                                <div>완료: <input type="number" id="rec-done-chars" class="rec-input-edit" oninput="checkGoalAchievement(); scheduleRecordAutoSave()" onchange="flushRecordAutoSave()">자</div>
                            </div>
                            <div class="rec-timer-container" id="rec-my-timer-area">
                                <div class="rec-timer-display" id="rec-main-timer">00:00:00</div>
                                <div style="font-size:12px; color:#777;">🟢 접속 중 자동 기록</div>
                            </div>
                        </div>
                        <div id="rec-congrats-banner" class="rec-banner">
                            <div id="rec-congrats-message">🎉 축하합니다! 오늘의 목표 분량을 모두 달성했습니다!</div>
                            <div id="rec-congrats-actions" style="display:none; justify-content:center; gap:8px; margin-top:10px; flex-wrap:wrap;">
                                <button type="button" class="rec-btn" onclick="chooseGoalCelebration(true)" style="background:var(--rec-primary); color:#fff;">💬 채팅창에서 축하받기</button>
                                <button type="button" class="rec-btn" onclick="chooseGoalCelebration(false)">안 받을래</button>
                            </div>
                        </div>
                    </div>

                    <div class="rec-section" id="rec-todo-section">
                        <h3>📝 나만 보는 할 일 · 삭제 전까지 유지</h3>
                        <ul class="rec-list" id="rec-todo-list"></ul>
                        <div class="rec-todo-input-box" id="rec-todo-input-area">
                            <input type="text" id="rec-new-todo-text" placeholder="새로운 할 일을 입력하세요..." onkeypress="if(event.key==='Enter') addRecTodo()">
                            <button class="rec-btn" onclick="addRecTodo()">추가</button>
                        </div>
                    </div>

                    <div class="rec-section"><h3>🏆 이번 달 목표</h3><label>월 목표 <input id="rec-month-goal" type="number" min="0" step="1000" class="rec-input-edit" oninput="scheduleRecordAutoSave()" onchange="flushRecordAutoSave()"> 자</label><div id="rec-month-progress"></div></div>
                        <div class="rec-section" id="rec-pomo-section">
                        <h3>
                            🍅 개인 뽀모도로
                        </h3>
                        <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center; gap: 10px; font-size: 13px;">
                                집필: <input type="number" id="rec-pomo-work" value="25" style="width: 50px;" class="rec-input-edit"> 분 / 
                                휴식: <input type="number" id="rec-pomo-rest" value="5" style="width: 50px;" class="rec-input-edit"> 분
                            </div>
                            <div style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap; justify-content: flex-end;">
                                <span id="rec-pomo-display" style="font-size: 18px; font-weight: bold; color: var(--rec-primary);">25:00 (대기 중)</span>
                                <div style="display: flex; gap: 5px;">
                                    <button class="rec-btn" onclick="startPomodoro()">뽀모 시작</button>
                                    <button class="rec-btn" onclick="pausePomodoro()" style="background: #dda7a7; color:#fff;">멈춤</button>
                                    <button class="rec-btn" onclick="resetPomodoro()" style="background: #ccc;">리셋</button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="rec-section">
                        <h3 id="rec-cal-month-title">📅 월간 집필 통계
                            <button class="rec-btn" onclick="toggleRecCalendar()" id="rec-cal-toggle-btn" style="font-size: 11px; padding: 4px 8px;">접기 🔼</button>
                        </h3>
                        <div class="rec-cal-wrap" id="rec-calendar-wrap" style="display: block;">
                            <div class="rec-cal-grid" id="rec-calendar-grid"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="video-background" id="bgContainer"><div id="bgMediaWrapper"></div></div>
        <div class="overlay"></div>
        
        <button id="restorePanelBtn" onclick="toggleDashboard()" style="display:none; position:fixed; right:20px; bottom:20px; z-index:9999; background:#6c5ce7; color:white; border:none; border-radius:50px; padding:12px 18px; font-size:14px; font-weight:bold; cursor:pointer; box-shadow:0 4px 10px rgba(0,0,0,0.5); transition: transform 0.2s;">💬 패널 열기</button>

        <div class="main-container">
            <div class="card-grid" id="cardGrid"></div>
            <div class="side-panel">
                <div class="panel-box" id="dashboardPanel" style="flex-shrink: 0;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 4px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <h3 style="margin: 0; font-size: 15px; line-height: 20px;">👑 대시보드</h3>
                            <button onclick="toggleDashboard()" style="background:#636e72; color:white; border:none; border-radius:3px; padding:3px 6px; font-size:10px; cursor:pointer; font-weight:bold;" id="dashboardToggle" title="대시보드만 접기">➡️ 접기</button>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 4px; width: 100%; margin-top: 5px;">
                            <div style="display: flex; gap: 4px; width: 100%;">
                                <button id="empty-slot-toggle-btn" class="settings-toggle-btn" style="background:#27ae60; color:white; flex: 1; padding: 6px 0;" onclick="toggleEmptySlots()">👀 빈자리</button>
                                <button id="chat-theme-btn" class="settings-toggle-btn" style="background:#f5f5f5; color:#2d3436; flex: 1; padding: 6px 0;" onclick="toggleChatTheme()">☀️ 패널 흰색</button>
                            </div>
                            <div style="display: flex; gap: 4px; width: 100%;">
                                <button class="settings-toggle-btn" style="background:#636e72; color:white; flex: 1; padding: 6px 0;" onclick="openModal('settingsModal')">⚙️ 내 배경</button>
                                <button class="settings-toggle-btn" style="background:#e1b12c; color:white; flex: 1; padding: 6px 0;" onclick="openModal('attendanceModal')">🏆 출석현황</button>
                            </div>
                            <div style="display: flex; gap: 4px; width: 100%;">
                                <button id="pomoSheetBtn" class="settings-toggle-btn" style="background:#d35400; color:white; flex: 1; padding: 6px 0;" onclick="openPomoSheet()">🍅 뽀모 시트</button>
                                <button id="pomoEditBtn" class="settings-toggle-btn" style="background:#7f8c8d; color:white; flex: 1; padding: 6px 0; display:none;" onclick="editPomoSheetUrl()">🔗 주소 변경</button>
                            </div>
                            <button class="settings-toggle-btn" style="background:#ff7675; color:white; width: 100%; text-align: center; padding: 6px 0;" onclick="openModal('noticeModal')">📢 공지</button>
                            <button id="adminLogBtn" class="settings-toggle-btn" style="background:#8e44ad; color:white; width: 100%; text-align: center; padding: 6px 0; margin-top: 4px; display: none;" onclick="openModal('adminLogModal')">👑 출입 기록</button>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 8px;">
                        <div>
                            <span id="connStatus" class="status-indicator status-offline" style="margin-left: 0;">연결 중...</span>
                            <span style="font-size:12px; margin-left: 5px;">인원: <b id="userCount" style="color:#ff7675;">0명</b></span>
                        </div>
                    </div>
                    <p style="margin-top:4px; font-size:11px; color:#aaa; line-height:1.5;">명단:<br><span id="userListStr" style="display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;"></span></p>
                </div>
                <div class="panel-box chat-box">
                    <div style="display:flex; justify-content:space-between; align-items:center; gap: 4px; flex-wrap: wrap;">
                        <h3 style="font-size: 14px; white-space: nowrap;">💬 실시간 채팅</h3>
                        <div style="display:flex; gap: 2px;">
                            <button onclick="forceRecoverWebRTC()" class="recovery-btn" style="font-size:9px; padding:2px 4px;">🔄 복구</button>
                            <button id="chatToggle" onclick="toggleChatPanel()" class="recovery-btn">접기</button><button onclick="clearChat()" style="font-size:9px; padding:2px 4px; background:#636e72; border:none; color:white; border-radius:3px; cursor:pointer;">청소</button>
                        </div>
                    </div>
                    <div id="chatHistory"></div>
                    <div class="chat-input">
                        <input type="text" id="chatInput" placeholder="메시지 입력..." onkeypress="if(event.key==='Enter') sendChat()">
                        <button onclick="sendChat()">전송</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let sessionPassword = ""; // 로그인 때 입력한 값. 서버가 확인하며 저장소에 남기지 않음
            let sessionToken = ""; // 로그인 뒤 서버가 준 조회용 값(메모리에만)
            let pendingEnrollCode = ""; // 방장에게 받은 일회용 등록 코드(메모리에만, 쓰면 지움)
            function identityKeyFor(nick) { try { return localStorage.getItem('gogogoIdentity:' + nick) || ""; } catch (e) { return ""; } }
            function saveIdentityKey(nick, key) { try { localStorage.setItem('gogogoIdentity:' + nick, key); } catch (e) {} }
            function safeBg(value) { return typeof value === 'string' && value.length <= 3000000 && /^data:image\/(?:png|jpeg|webp|bmp|avif);base64,[A-Za-z0-9+\/]+={0,2}$/.test(value) ? value : ''; }
            function issueEnrollmentPrompt() { const nick = prompt('등록 코드를 만들 닉네임을 적어줘. (그 이름의 기존 열쇠는 해제되고, 코드를 받은 사람만 다시 등록할 수 있어)'); if (nick && ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "issue_enrollment", target_nick: nick.trim() })); } }
            const ADMIN_NICKNAME = "부엉";
            window.rawNotice = ""; 
            window.isHideEmpty = false; 
            window.attendanceData = {}; 
            window.adminLogData = []; 
            window.pomoSheetUrl = "";
            
            window.trackersData = {}; 
            window.currentViewingUser = "";
            window.goalAchieved = false;
            window.goalCelebrationChoiceMade = false;
            let recTotalSeconds = 0;
            let recMainTimerInterval = null;
            let recordAutoSaveTimer = null;
            let recordAutoSaveNeedsSync = false;
            let recPomoInterval = null;
            let recPomoSeconds = 25 * 60;
            let recIsWorking = true;
            window.recViewYear = new Date().getFullYear();
            window.recViewMonth = new Date().getMonth() + 1;

            function setMediaBitrate(sdp, bitrate) {
                let lines = sdp.split('\n');
                let line = -1;
                for (let i = 0; i < lines.length; i++) {
                    if (lines[i].indexOf('m=video') === 0) { line = i; break; }
                }
                if (line === -1) return sdp;
                lines.splice(line + 1, 0, 'b=AS:' + bitrate);
                return lines.join('\n');
            }

            function toggleSidePanel() {
                const container = document.querySelector('.main-container');
                const panel = document.querySelector('.side-panel');
                const restoreBtn = document.getElementById('restorePanelBtn');
                
                if (panel.style.display === 'none') {
                    panel.style.display = 'flex';
                    container.style.gridTemplateColumns = 'minmax(0, 1fr) 320px';
                    restoreBtn.style.display = 'none';
                } else {
                    panel.style.display = 'none';
                    container.style.gridTemplateColumns = '1fr'; 
                    restoreBtn.style.display = 'block';
                }
            }

            async function updateLoginUserCount() {
                try {
                    const res = await fetch('/user_count');
                    const data = await res.json();
                    const countEl = document.getElementById('loginUserCount');
                    if(countEl) countEl.innerText = `🔥 현재 달리고 있는 작가님: ${data.count}명`;
                } catch(e) {
                    const countEl = document.getElementById('loginUserCount');
                    if(countEl) countEl.innerText = `🔥 현재 달리고 있는 작가님: ?명`;
                }
            }
            updateLoginUserCount();
            let countInterval = setInterval(() => {
                if(document.getElementById('loginOverlay').style.display !== 'none') {
                    updateLoginUserCount();
                } else {
                    clearInterval(countInterval); 
                }
            }, 10000);

            function openModal(modalId) { 
                document.getElementById(modalId).style.display = 'flex'; 
                if (modalId === 'attendanceModal') { renderAttendanceBoard(); } 
                if (modalId === 'adminLogModal') { renderAdminLog(); } 
            }
            function closeModal(modalId) { 
                if (modalId === 'recordModal') flushRecordAutoSave();
                document.getElementById(modalId).style.display = 'none'; 
                if (modalId === 'recordModal' && window.myNickname && window.trackersData[window.myNickname]) {
                    changeThemeColor(window.trackersData[window.myNickname].themeColor || "#d87093");
                }
            }

            function formatLogTime(ts) { const now = new Date(ts * 1000); const m = now.getMonth() + 1; const d = now.getDate(); return `${m}/${d} ${now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`; }
            function renderAdminLog() { const container = document.getElementById('adminLogContent'); if (!window.adminLogData || window.adminLogData.length === 0) { container.innerHTML = "기록이 없습니다."; return; } container.innerHTML = window.adminLogData.map(log => `<div style="margin-bottom: 4px;">[${formatLogTime(log.time)}] <b style="color:#ffeaa7;">${escapeText(log.msg)}</b></div>`).join(""); container.scrollTop = container.scrollHeight; }
            
            function renderAttendanceBoard() {
                const now = kstNow(); const y = now.getFullYear(); const m = now.getMonth() + 1; const today = now.getDate(); const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                const calTitleEl = document.getElementById('calMonthTitle'); if (calTitleEl) calTitleEl.innerText = `🍀 ${y}년 ${m}월 내 출석부`;
                const grid = document.getElementById('calendarGrid');
                if (grid) {
                    const firstDay = new Date(y, m - 1, 1).getDay(); const daysInMonth = new Date(y, m, 0).getDate(); let html = '';
                    const daysOfWeek = ['일','월','화','수','목','금','토']; daysOfWeek.forEach(d => { html += `<div style="text-align:center; font-size:10px; color:#ffeaa7; margin-bottom: 4px;">${d}</div>`; });
                    for(let i=0; i<firstDay; i++) { html += `<div></div>`; }
                    const myName = window.myNickname || "익명"; const myAtt = (window.attendanceData[monthStr] && window.attendanceData[monthStr][myName]) || [];
                    for(let i=1; i<=daysInMonth; i++) {
                        const isToday = (i === today); const isStamped = myAtt.includes(i); let cls = "cal-day";
                        if (isToday && !isStamped) cls += " today"; if (isStamped) cls += " stamped"; const content = isStamped ? '🍀' : i;
                        if (isToday && !isStamped) { html += `<div class="${cls}" onclick="stampAttendance(${y}, ${m}, ${i})" title="오늘 출석 도장 찍기!">${content}</div>`; } else { html += `<div class="${cls}">${content}</div>`; }
                    }
                    grid.innerHTML = html;
                }
                const titleEl = document.getElementById('rankTitle'); if (titleEl) titleEl.innerText = '🏆 이번 주 모두의 랭킹';
                const monthData = window.attendanceData[monthStr] || {}; const weeklyCounts = weeklyAttendanceCounts(); let rankArr = []; let todayAttendees = [];
                for (let user in weeklyCounts) { rankArr.push({ name: user, count: weeklyCounts[user] }); }
                for (let user in monthData) { if ((monthData[user] || []).includes(today)) { todayAttendees.push(user); } }
                rankArr.sort((a, b) => b.count - a.count); let rankHtml = '';
                if (rankArr.length === 0) { rankHtml = '아직 이번 주에 출석한 사람이 없어!'; } else { rankArr.forEach((item) => { const rank = 1 + rankArr.filter(other => other.count > item.count).length; let medal = ({1:'🥇',2:'🥈',3:'🥉'})[rank] || '🏅'; rankHtml += `<div style="${(rank > 0 && rank <= 3) ? 'font-weight:bold; color:#fff;' : ''} margin-bottom: 4px;">${medal} ${rank}등 ${escapeText(item.name)} : ${item.count}일</div>`; }); }
                let todayHtml = todayAttendees.length === 0 ? '아직 오늘 출석한 사람이 없어! 빨리 1빠 찍어!' : todayAttendees.map(u => `<span style="background:rgba(39, 174, 96, 0.6); padding:4px 8px; border-radius:4px; font-weight:bold;">🍀 ${escapeText(u)}</span>`).join('');
                const rankEl = document.getElementById('attRankingList'); const todayEl = document.getElementById('attTodayList');
                if (rankEl) rankEl.innerHTML = rankHtml; if (todayEl) todayEl.innerHTML = todayHtml;
            }
            function stampAttendance(y, m, d) { const monthStr = `${y}-${String(m).padStart(2, '0')}`; const myName = window.myNickname || "익명"; if (window.attendanceData && window.attendanceData[monthStr] && window.attendanceData[monthStr][myName] && window.attendanceData[monthStr][myName].includes(d)) return; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "attendance", month: monthStr, day: d, nickname: myName })); } }
            function autoStampToday() { const now = kstNow(); const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`; const myName = window.myNickname; if (!myName) return; const myAtt = (window.attendanceData[monthStr] && window.attendanceData[monthStr][myName]) || []; if (!myAtt.includes(now.getDate())) { stampAttendance(now.getFullYear(), now.getMonth() + 1, now.getDate()); } }
            function loadLocalBackground() { const bgType = localStorage.getItem('myBgType'); const bgData = localStorage.getItem('myBgData'); if (bgType === 'image' && bgData) { document.getElementById('bgMediaWrapper').innerHTML = `<img src="${bgData}" alt="Full Background">`; } else if (bgType === 'youtube' && bgData) { document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${bgData}?autoplay=1&mute=1&loop=1&playlist=${bgData}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`; } }
            function makeLinksClickable(text) { return text.replace(/(https?:\/\/[^\s"'<>]+)/g, '<a href="$1" target="_blank" style="color: #ffeaa7; text-decoration: underline; padding: 0 4px;" onclick="event.stopPropagation()">$1</a>'); }
            function formatNotice(text) { return !text ? "" : makeLinksClickable(escapeText(text)).replace(/\n/g, '<br>'); }
            function addNotice() { const newVal = prompt("새로 추가할 공지를 적어주세요!\n(새 공지는 맨 위로 올라갑니다)"); if (newVal !== null && newVal.trim() !== "") { const combined = window.rawNotice ? ("📌 " + newVal + "\n\n" + window.rawNotice) : ("📌 " + newVal); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "update_notice", notice: combined })); window.rawNotice = combined; document.getElementById('noticeText').innerHTML = formatNotice(combined); } } }
            function editNotice() { const newVal = prompt("기존 공지를 전부 지우고 새로 쓰거나, 직접 글을 수정하세요!", window.rawNotice); if (newVal !== null) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "update_notice", notice: newVal })); window.rawNotice = newVal; document.getElementById('noticeText').innerHTML = formatNotice(newVal); } } }
            window.activeNicknames = new Set();
            function toggleEmptySlots() {
                window.isHideEmpty = !window.isHideEmpty;
                const button = document.getElementById('empty-slot-toggle-btn');
                if (button) button.textContent = window.isHideEmpty ? '🪑 전체 자리' : '👀 빈자리';
                applyEmptySlotVisibility();
            }
            function applyEmptySlotVisibility() {
                const activeNicknames = window.activeNicknames || new Set();
                cardData.forEach((card, index) => {
                    const cardEl = document.getElementById(`card-card-${index}`);
                    if (!cardEl) return;
                    const isConnectedUser = !!card.user && !card.user.startsWith("자리") && activeNicknames.has(card.user);
                    cardEl.style.display = (window.isHideEmpty && !isConnectedUser) ? "none" : "flex";
                });
            }
            async function loadPomoSheetUrl() {
                try {
                    const res = await fetch('/pomo-config', { cache: 'no-store', headers: { 'X-Gogogo-Session': sessionToken } });
                    const data = await res.json();
                    if (res.ok && data.url) window.pomoSheetUrl = data.url;
                } catch (e) {
                    console.warn('뽀모 시트 주소를 불러오지 못했습니다:', e);
                }
            }

            function openPomoSheet() {
                if (!window.pomoSheetUrl) {
                    alert('뽀모 시트 주소를 불러오는 중이야. 잠깐 뒤에 다시 눌러줘!');
                    loadPomoSheetUrl();
                    return;
                }
                window.open(window.pomoSheetUrl, '_blank', 'noopener,noreferrer');
            }

            async function editPomoSheetUrl() {
                if (!window.isAdmin) { alert('방장만 시트 주소를 바꿀 수 있어!'); return; }
                const nextUrl = prompt('새 뽀모 구글시트 주소를 붙여넣어줘.', window.pomoSheetUrl || '');
                if (nextUrl === null) return;
                const trimmed = nextUrl.trim();
                if (!trimmed.startsWith('https://docs.google.com/spreadsheets/')) {
                    alert('구글 스프레드시트 주소만 넣어줘!');
                    return;
                }
                try {
                    const res = await fetch('/pomo-config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-Gogogo-Session': sessionToken },
                        body: JSON.stringify({ url: trimmed })
                    });
                    const data = await res.json();
                    if (!res.ok || !data.ok) throw new Error(data.detail || '저장 실패');
                    window.pomoSheetUrl = data.url;
                    alert('🍅 뽀모 시트 주소를 바꿨어!');
                } catch (e) {
                    alert(`시트 주소를 저장하지 못했어: ${e.message || e}`);
                }
            }

            function checkLogin() { document.getElementById('loginOverlay').style.display = 'flex'; const savedNick = localStorage.getItem('mySavedNickname'); if (savedNick) { document.getElementById('nickInput').value = savedNick; document.getElementById('pwInput').focus(); } }
            
            function login() { 
                const inputPw = document.getElementById('pwInput').value; 
                const inputNick = document.getElementById('nickInput').value.trim(); 
                if (!inputNick) { alert("누군지 알 수 있게 닉네임을 적어줘 누나!"); return; } 
                if (!inputPw) { alert("비밀번호를 적어줘!"); return; }
                sessionPassword = inputPw; // 맞는지는 서버가 확인함(auth_ok / auth_failed)
                if (inputNick === ADMIN_NICKNAME) {
                    window.isAdmin = true; document.getElementById('adminLogBtn').style.display = 'block'; document.getElementById('pomoEditBtn').style.display = 'block'; 
                } else {
                    window.isAdmin = false; document.getElementById('pomoEditBtn').style.display = 'none'; 
                } 
                window.myNickname = inputNick;
                window.activeNicknames.add(inputNick);
                localStorage.setItem('mySavedNickname', inputNick); 
                document.getElementById('loginOverlay').style.display = 'none'; 
                initCards(); 
                connectWebSocket(); 
                loadLocalBackground(); 
            }
            
            function kickUser(nickname) { if(confirm(`${nickname} 님을 방에서 강제로 쫓아낼까?`)) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "kick", target_nick: nickname })); } } }

            let ws = null; let pingInterval = null;
            let loginWaitTimer = null; let reconnectTimer = null;
            function loginNotice(message) {
                document.getElementById('loginOverlay').style.display='flex';
                document.getElementById('loginStatus').textContent=message;
                const status=document.getElementById('connStatus');
                status.textContent='입장 대기'; status.className='status-indicator status-offline';
            } 
            const cardData = Array.from({length: 16}, (_, i) => ({ id: i+1, user: `자리${i+1}`, card_bg: null, is_mosaic: false, is_large: false, status: "", reserved_by: null, is_local_hidden: false }));
            const myStreams = {}; const peerConnections = {}; const candidateBuffers = {}; const expectedShares = {}; const myOwnedSlots = new Set(); 
            const rtcConfig = { iceServers: [ { urls: 'stun:stun.l.google.com:19302' }, { urls: 'stun:stun1.l.google.com:19302' } ] };

            function getEmptySlotHTML(card) {
                const username = card.user || "";
                const nameColor = isMonthlyLevelUp(username) ? '#ffd54a' : '#fff';
                if (card.reserved_by) {
                    return `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:2; text-align:center; padding:10px; width:100%; height:100%;"><span style="font-size:22px; font-weight:900; color:${nameColor}; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:4px;">${escapeText(username)}</span><span style="font-size:12px; color:#ffeaa7; font-weight:bold;">🔒 ${escapeText(card.reserved_by)} 작가님 고정석</span><span style="font-size:10px; color:#aaa; margin-top:3px;">화면 미공유 중</span></div>`;
                }
                if (!username || username.startsWith("자리")) { return `<div style="position:relative; z-index:2; width:100%; text-align:center;"><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`; }
                return `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:2; text-align:center; padding:10px; width:100%; height:100%;"><span style="font-size:22px; font-weight:900; color:${nameColor}; text-shadow: 2px 2px 5px rgba(0,0,0,0.9); margin-bottom:4px;">${escapeText(username)}</span><span style="font-size:11px; color:#aaa;">화면 미공유 중</span></div>`;
            }

            function applySeatReservationUI(index) {
                const card = cardData[index]; if (!card) return;
                const owner = card.reserved_by;
                const myName = window.myNickname || "";
                const isMine = !!owner && owner === myName;
                const button = document.getElementById(`seat-lock-btn-${index}`);
                if (button) {
                    if (isMine) { button.textContent = "🔓 고정 해제"; button.style.background = "#00b894"; button.disabled = false; button.title = "내 고정석을 해제합니다"; }
                    else if (owner && window.isAdmin) { button.textContent = "🔓 강제 해제"; button.style.background = "#d63031"; button.disabled = false; button.title = `${owner} 작가님의 고정석을 방장 권한으로 해제합니다`; }
                    else if (owner) { button.textContent = "🔒 고정석"; button.style.background = "#636e72"; button.disabled = true; button.title = `${owner} 작가님의 고정석입니다`; }
                    else { button.textContent = "📌 자리 고정"; button.style.background = "#e1a400"; button.disabled = false; button.title = "이 자리를 내 고정석으로 저장합니다"; }
                    button.style.opacity = button.disabled ? "0.72" : "1";
                    button.style.cursor = button.disabled ? "not-allowed" : "pointer";
                }
                const input = document.getElementById(`username-${index}`);
                if (input) {
                    input.readOnly = !!owner;
                    input.title = owner ? `${owner} 작가님의 고정석이라 이름을 바꿀 수 없습니다` : "";
                    input.style.borderColor = owner ? "#ffeaa7" : "rgba(255,255,255,0.4)";
                }
                const cardEl = document.getElementById(`card-card-${index}`);
                if (cardEl) cardEl.style.boxShadow = owner ? "0 0 0 2px rgba(255,234,167,0.9), 0 8px 18px rgba(0,0,0,0.25)" : "";
            }

            function applySeatCardState(index, user, reservedBy) {
                if (!cardData[index]) return;
                cardData[index].user = user;
                cardData[index].reserved_by = reservedBy || null;
                const input = document.getElementById(`username-${index}`); if (input) input.value = user;
                const myName = window.myNickname || "익명";
                if (user === myName) myOwnedSlots.add(index); else myOwnedSlots.delete(index);
                const cardEl = document.getElementById(`card-card-${index}`); if (cardEl) cardEl.style.order = (user === myName && myName) ? -1 : 0;
                applySeatReservationUI(index);
                renderQuickDoneRow(index);
                const box = document.getElementById(`stream-box-${index}`); if (box && !box.querySelector('video')) renderBox(index);
                applyEmptySlotVisibility();
            }

            function getTodayDoneForCard(nickname) {
                if (!nickname || nickname.startsWith("자리")) return 0;
                const data = window.trackersData?.[nickname];
                if (!data || data.lastDate !== dayKey()) return 0;
                return Math.max(0, Math.floor(Number(data.doneChars) || 0));
            }

            function renderQuickDoneRow(index) {
                const row = document.getElementById(`quick-done-row-${index}`);
                const card = cardData[index];
                if (!row || !card) return;
                const nickname = card.user || "";
                const isMe = !!window.myNickname && nickname === window.myNickname;
                if (!isMe) {
                    row.style.display = 'none';
                    row.innerHTML = '';
                    return;
                }
                const done = getTodayDoneForCard(nickname);
                row.style.display = 'flex';
                row.innerHTML = `
                    <span style="font-weight:bold; white-space:nowrap;">✍️ 오늘 완료</span>
                    <input type="number" min="0" step="1" inputmode="numeric" id="quick-done-input-${index}" value="${done}" aria-label="오늘 완료 글자수" onkeydown="if(event.key==='Enter'){event.preventDefault(); saveQuickDone(${index});}">
                    <span style="white-space:nowrap;">자</span>
                    <button type="button" onclick="saveQuickDone(${index})">저장</button>
                    <span class="quick-done-saved" id="quick-done-saved-${index}"></span>
                `;
            }

            function refreshQuickDoneRows() {
                cardData.forEach((_, index) => renderQuickDoneRow(index));
            }

            function saveQuickDone(index) {
                const card = cardData[index];
                const nickname = window.myNickname || "";
                if (!card || !nickname || card.user !== nickname) {
                    alert('자기 카드에서만 오늘 완료 글자수를 적을 수 있어!');
                    return;
                }
                if (!ws || ws.readyState !== WebSocket.OPEN) {
                    alert('서버에 연결된 뒤 저장해줘!');
                    return;
                }
                const input = document.getElementById(`quick-done-input-${index}`);
                if (!input) return;
                const raw = String(input.value || '').trim();
                if (raw === '' || !/^\d+$/.test(raw)) {
                    alert('오늘 완료 글자수를 0 이상의 숫자로 적어줘!');
                    input.focus();
                    return;
                }
                const done = Math.max(0, Math.floor(Number(raw)));
                const now = kstNow();
                const todayStr = dayKey();
                const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                const dayStr = String(now.getDate());

                if (!window.trackersData) window.trackersData = {};
                if (!window.trackersData[nickname]) {
                    window.trackersData[nickname] = { targetChars: 5000, doneChars: 0, themeColor: "#d87093", calendar: {}, todos: [], monthlyGoals: {}, lastDate: todayStr };
                }
                const myData = window.trackersData[nickname];
                myData.lastDate = todayStr;
                myData.doneChars = done;
                myData.calendar = myData.calendar || {};
                myData.calendar[monthStr] = myData.calendar[monthStr] || {};
                const currentRecord = myData.calendar[monthStr][dayStr] || {};
                myData.calendar[monthStr][dayStr] = {
                    target: myData.targetChars || currentRecord.target || 5000,
                    done: done,
                    seconds: currentRecord.seconds || 0
                };

                localStorage.setItem('doneChars', String(done));
                localStorage.setItem('lastDate', todayStr);
                input.value = done;

                if (window.currentViewingUser === nickname && document.getElementById('recordModal').style.display === 'flex') {
                    const modalDone = document.getElementById('rec-done-chars');
                    if (modalDone) modalDone.value = done;
                    buildRecordCalendar(nickname, myData.calendar);
                    updateMonthlyProgress(nickname);
                    checkGoalAchievement();
                }

                ws.send(JSON.stringify({ type: "tracker_update", nickname: nickname, tracker_data: myData }));
                const saved = document.getElementById(`quick-done-saved-${index}`);
                if (saved) {
                    saved.textContent = '저장됨 ✓';
                    setTimeout(() => { if (saved) saved.textContent = ''; }, 1400);
                }
            }

            function toggleSeatReservation(index) {
                const card = cardData[index];
                const myName = window.myNickname || "";
                if (!card || !myName) return;
                if (!ws || ws.readyState !== WebSocket.OPEN) { alert("서버에 연결된 뒤 다시 눌러줘!"); return; }
                if (card.reserved_by && card.reserved_by !== myName && !window.isAdmin) { alert(`${card.reserved_by} 작가님의 고정석이야!`); return; }
                if (card.reserved_by && card.reserved_by !== myName && window.isAdmin && !confirm(`${card.reserved_by} 작가님의 고정석을 강제로 해제할까?`)) return;
                if (!card.reserved_by && card.user !== myName) { alert("자기 카드에서만 자리를 고정할 수 있어!"); return; }
                ws.send(JSON.stringify({ type: "seat_reservation", index: index, reserved: !card.reserved_by }));
            }
            
            function renderBox(index) { 
                const box = document.getElementById(`stream-box-${index}`); if (!box) return; 
                const existingVideo = box.querySelector('video'); if (existingVideo) existingVideo.remove(); 
                const card = cardData[index]; 
                if (hasCustomStatus(card.status)) {
                    box.innerHTML = `<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; height:100%; background: rgba(0,0,0,0.7); z-index: 5; position: absolute; top:0; left:0; padding:18px;"><div style="font-size: 26px; line-height:1.35; text-align:center; word-break:keep-all; overflow-wrap:anywhere; font-weight: 900; color: #fff; text-shadow: 2px 2px 6px rgba(0,0,0,0.8);">${escapeText(card.status)}</div></div>`;
                } else { 
                    box.innerHTML = getEmptySlotHTML(card); 
                } 
            }
            
            function hasCustomStatus(status) { return typeof status === 'string' && status.trim().length > 0; }
            function normalizeCustomStatus(value) { return Array.from(String(value || '').trim()).slice(0, 30).join(''); }
            function updateStatusUI(index, status) {
                const btn = document.getElementById(`share-btn-status-${index}`);
                if (!btn) return;
                const active = hasCustomStatus(status);
                btn.innerText = active ? "수정" : "상태";
                btn.style.background = active ? "#00b894" : "#8e44ad";
                btn.title = active ? `현재 상태: ${status} (눌러서 수정·삭제)` : "눌러서 내 상태를 직접 입력";
            }
            function editCustomStatus(index) {
                const isMine = ((cardData[index].user === window.myNickname) && window.myNickname) || window.isAdmin;
                if (!isMine) { alert("자기 자리 상태만 바꿀 수 있어 누나!"); return; }
                const current = hasCustomStatus(cardData[index].status) ? cardData[index].status : '';
                const entered = prompt("지금 상태를 적어주세요. (최대 30자)\n비워두고 확인하면 상태가 해제됩니다.", current);
                if (entered === null) return;
                const status = normalizeCustomStatus(entered);
                setCustomStatus(index, status);
            }
            function setCustomStatus(index, status) {
                cardData[index].status = status;
                updateStatusUI(index, status);
                if (hasCustomStatus(status) && myStreams[index]) stopShare(index);
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "status_update", index: index, status: status }));
                renderBox(index);
            }
            
            function logChat(sender, msg, timeStr, id) {
                const history = document.getElementById('chatHistory');
                const row = document.createElement('div');
                const name = document.createElement('b');
                name.dataset.name = sender;
                name.classList.toggle('level-up-name', isMonthlyLevelUp(sender));
                name.textContent = badgeFor(sender) + sender;
                row.append(name, document.createTextNode(': ' + msg));
                const metadata = document.createElement('span');
                metadata.style.cssText = 'display:inline-block;margin-left:5px;font-size:9px;color:rgba(180,190,200,0.38);font-weight:normal;white-space:nowrap;';
                const timestamp = document.createElement('span');
                timestamp.textContent = timeStr || '';
                timestamp.style.cssText = 'font-size:9px;color:rgba(180,190,200,0.38);font-weight:normal;';
                metadata.append(timestamp);
                if (id && sender === window.myNickname) {
                    const button = document.createElement('button'); button.textContent = '삭제'; button.className = 'chat-delete-btn'; button.title = '이 메시지 삭제';
                    button.onclick = () => { if(confirm('이 메시지를 삭제할까요?')) ws.send(JSON.stringify({type:'delete_chat', id})); };
                    button.style.cssText = 'font-size:9px;color:rgba(180,190,200,0.38);background:transparent;border:0;padding:0 2px;margin-left:4px;line-height:1.2;font-weight:normal;cursor:pointer;';
                    metadata.append(button);
                }
                row.append(metadata);
                history.append(row); history.scrollTop = history.scrollHeight;
            }
            function renderChatHistory(messages) { document.getElementById('chatHistory').replaceChildren(); messages.forEach(c => logChat(c.senderName,c.msg,c.time,c.id)); }
            function clearChat() { if(confirm('채팅 전체를 삭제할까요?')) ws.send(JSON.stringify({type:'clear_chat'})); }


            function clearChat() { if (confirm("채팅창을 전부 깨끗하게 지울까?")) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "clear_chat" })); } } }
            
            function forceRecoverWebRTC() { 
                if (ws && ws.readyState === WebSocket.OPEN) { 
                    const ownedArr = Array.from(myOwnedSlots);
                    ws.send(JSON.stringify({ type: "set_nickname", nickname: window.myNickname, owned: ownedArr, password: sessionPassword, identity_key: identityKeyFor(window.myNickname), enroll_code: pendingEnrollCode }));
                    ws.send(JSON.stringify({ type: "request_existing_shares" })); 
                    for (let idx in expectedShares) { 
                        const sharerId = expectedShares[idx]; 
                        if (sharerId && sharerId !== ws.clientId) { 
                            ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId })); 
                        } 
                    } 
                } 
                alert("화면 공유 통신선을 강제로 다시 뚫고 있습니다! 2~3초만 기다려주세요!"); 
            }

            function toggleLocalHide(index) {
                cardData[index].is_local_hidden = !cardData[index].is_local_hidden;
                const btn = document.getElementById(`hide-btn-${index}`);
                const streamBox = document.getElementById(`stream-box-${index}`);
                if (cardData[index].is_local_hidden) {
                    if(btn) { btn.innerText = "보기"; btn.style.background = "#e84393"; }
                    if(streamBox) { streamBox.style.visibility = "hidden"; }
                } else {
                    if(btn) { btn.innerText = "가리기"; btn.style.background = "#2d3436"; }
                    if(streamBox) { streamBox.style.visibility = "visible"; }
                }
            }

            function initCards() {
                const grid = document.getElementById('cardGrid'); grid.innerHTML = '';
                cardData.forEach((card, index) => {
                    let bgStyle = safeBg(card.card_bg) ? `background-image: url('${safeBg(card.card_bg)}');` : ''; let mosaicBtnBg = card.is_mosaic ? '#e17055' : '#636e72'; let mosaicBtnText = card.is_mosaic ? '해제' : '모자이크'; let sizeBtnText = card.is_large ? '작게' : '크게'; let sizeBtnBg = card.is_large ? '#e67e22' : '#f39c12'; let largeClass = card.is_large ? ' card-large' : ''; let statusBtnText = hasCustomStatus(card.status) ? '수정' : '상태'; let statusBtnBg = hasCustomStatus(card.status) ? '#00b894' : '#8e44ad';
                    let hideBtnText = card.is_local_hidden ? '보기' : '가리기'; let hideBtnBg = card.is_local_hidden ? '#e84393' : '#2d3436'; let boxVisibility = card.is_local_hidden ? 'hidden' : 'visible';
                    let myOrder = (card.user === window.myNickname && window.myNickname) ? -1 : 0;
                    grid.innerHTML += `
                        <div class="timer-card${largeClass}" id="card-card-${index}" style="${bgStyle} order: ${myOrder};">
                            <div class="card-header">
	                                <div class="card-title-row">
	                                    <input class="card-name-input" type="text" id="username-${index}" value="${escapeText(card.user)}" style="flex-grow: 1; min-width: 0; padding: 4px; font-size: 11px; font-weight: bold; text-align: center; background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; border-radius: 3px;" oninput="updateUsername(${index}, this.value)">
	                                    <button onclick="openRecordModalByIndex(${index})" class="share-btn" style="background:#6c5ce7; padding:4px 6px; flex-grow:0;">✍️ 집필기록</button>
	                                    <button onclick="toggleSeatReservation(${index})" id="seat-lock-btn-${index}" class="share-btn" style="background:#e1a400; padding:4px 6px; flex-grow:0; white-space:nowrap;">📌 자리 고정</button>
	                                </div>
                                <div class="quick-done-row" id="quick-done-row-${index}"></div>
                                <div class="btn-group" style="margin-top: 4px;">
                                    <button class="share-btn" id="share-btn-screen-${index}" style="background:#ff7675;" onclick="toggleShare(${index}, 'screen')">화공</button>
                                    <button class="share-btn" id="share-btn-cam-${index}" style="background:#0984e3;" onclick="toggleShare(${index}, 'cam')">캠</button>
                                    <button class="share-btn" id="share-btn-status-${index}" style="background:${statusBtnBg};" onclick="editCustomStatus(${index})" title="눌러서 내 상태를 직접 입력">${statusBtnText}</button>
                                    <button class="share-btn" id="share-btn-mosaic-${index}" style="background:${mosaicBtnBg};" onclick="handleMosaicClick(${index})">${mosaicBtnText}</button>
                                    <button class="share-btn" id="size-btn-${index}" style="background:${sizeBtnBg};" onclick="toggleSize(${index})">${sizeBtnText}</button>
                                    <button class="share-btn" id="sound-toggle-btn-${index}" style="background:#00b894; display:none;" onclick="toggleViewerSound(${index})">음소거</button>
                                    <button class="share-btn" id="hide-btn-${index}" style="background:${hideBtnBg};" onclick="toggleLocalHide(${index})">${hideBtnText}</button>
                                    <button class="share-btn" style="background:#b2bec3; color:#2d3436; flex-grow:1;" onclick="document.getElementById('card-file-${index}').click()" title="카드 배경화면 변경">🖼️ 배경</button>
                                    <input type="file" id="card-file-${index}" style="display:none;" accept="image/jpeg, image/png, image/webp" onchange="setCardBackground(${index}, event)">
                                </div>
                            </div>
                            <div style="position: relative; flex-grow: 1; display: flex; flex-direction: column; border-radius: 8px; overflow: hidden; margin-top: 6px;">
                                <div class="card-stream-box" id="stream-box-${index}" style="margin-top:0; border-radius:0; visibility:${boxVisibility};"></div>
                            </div>
                        </div>
                    `;
                });
                cardData.forEach((_, i) => { renderBox(i); applySeatReservationUI(i); renderQuickDoneRow(i); }); applyEmptySlotVisibility();
            }

            function toggleSize(index) { const newState = !cardData[index].is_large; cardData[index].is_large = newState; applySizeUI(index, newState); }
            function applySizeUI(index, isLarge) { const cardEl = document.getElementById(`card-card-${index}`); const btn = document.getElementById(`size-btn-${index}`); if (cardEl) { if (isLarge) cardEl.classList.add('card-large'); else cardEl.classList.remove('card-large'); } if (btn) { btn.innerText = isLarge ? "작게" : "크게"; btn.style.background = isLarge ? "#e67e22" : "#f39c12"; } }
            function handleMosaicClick(index) { const isMyStream = !!myStreams[index]; if (!isMyStream && !window.isAdmin) { alert("본인이 화면 공유 중이 아니면 만질 수 없어!"); return; } const newState = !cardData[index].is_mosaic; cardData[index].is_mosaic = newState; applyMosaicUI(index, newState); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "toggle_mosaic", index: index, is_mosaic: newState })); } }
            function toggleViewerSound(index) { const vid = document.getElementById(`remote-video-${index}`); const btn = document.getElementById(`sound-toggle-btn-${index}`); if (!vid) return; vid.muted = !vid.muted; if (vid.muted) { btn.innerText = "소리켜기"; btn.style.background = "#b2bec3"; } else { btn.innerText = "음소거"; btn.style.background = "#00b894"; } }
            function applyMosaicUI(index, isMosaic) { const btn = document.getElementById(`share-btn-mosaic-${index}`); if (btn) { btn.innerText = isMosaic ? "해제" : "모자이크"; btn.style.background = isMosaic ? "#e17055" : "#636e72"; } const remoteVideo = document.getElementById(`remote-video-${index}`); const localVideo = document.getElementById(`video-${index}`); const activeFilter = isMosaic ? 'blur(5px)' : 'none'; if (remoteVideo) { remoteVideo.style.filter = activeFilter; } if (localVideo) { localVideo.style.filter = activeFilter; } }
            function updateUsername(index, val) { 
                const myName = window.myNickname || "익명";
                const reservationOwner = cardData[index].reserved_by;
                if (reservationOwner) {
                    const inputEl = document.getElementById(`username-${index}`);
                    if (reservationOwner === myName && val === myName) {
                        cardData[index].user = myName;
                        myOwnedSlots.add(index);
                        if (inputEl) inputEl.value = myName;
                        const cardEl = document.getElementById(`card-card-${index}`);
                        if (cardEl) cardEl.style.order = -1;
                        return;
                    }
                    if (inputEl) inputEl.value = cardData[index].user;
                    alert(reservationOwner === myName ? "고정석은 먼저 고정을 해제해야 이름을 바꿀 수 있어!" : `${reservationOwner} 작가님의 고정석이야!`);
                    return;
                }
                cardData[index].user = val; 
                if (val === myName) { myOwnedSlots.add(index); } else { myOwnedSlots.delete(index); } 
                const cardEl = document.getElementById(`card-card-${index}`);
                if (cardEl) { cardEl.style.order = (val === myName && myName) ? -1 : 0; }
                const box = document.getElementById(`stream-box-${index}`); 
                if (box && !box.querySelector('video')) { renderBox(index); } 
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "username_change", index: index, user: val })); } 
                renderQuickDoneRow(index);
                applyEmptySlotVisibility(); 
            }
            let backgroundLoading = false;
            const knownBackgrounds = new Set();
            async function loadSavedBackgrounds() {
                if (backgroundLoading) return;
                backgroundLoading = true;
                try {
                    for (let i=0; i<cardData.length; i++) {
                        if (knownBackgrounds.has(i)) continue;
                        const previous = cardData[i].card_bg;
                        try {
                            const response = await fetch(`/card-background/${i}`, {cache:'no-store', headers: { 'X-Gogogo-Session': sessionToken }});
                            const result = await response.json();
                            if (!result.ok || cardData[i].card_bg !== previous) continue;
                            cardData[i].card_bg = result.card_bg;
                            knownBackgrounds.add(i);
                            const element = document.getElementById(`card-card-${i}`);
                            if (element) element.style.backgroundImage = safeBg(result.card_bg) ? `url('${safeBg(result.card_bg)}')` : '';
                        } catch (error) { console.warn('Background deferred', i); }
                    }
                } finally { backgroundLoading = false; }
            }
            function setCardBackground(index, event) { const file = event.target.files[0]; if (!file) return; if (file.type === "image/gif") { alert("움짤(GIF)은 올릴 수 없어 누나!"); event.target.value = ""; return; } const reader = new FileReader(); reader.onload = function(e) { const dataUrl = e.target.result; if (!safeBg(dataUrl)) { alert("PNG·JPG·WEBP 그림만 카드 배경으로 쓸 수 있어!"); return; } cardData[index].card_bg = dataUrl; const cardEl = document.getElementById(`card-card-${index}`); if (cardEl) { cardEl.style.backgroundImage = `url('${dataUrl}')`; } if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "card_bg_change", index: index, dataUrl: dataUrl })); } }; reader.readAsDataURL(file); }
            function setLocalBackground(event) { const file = event.target.files[0]; if (!file) return; if (file.type === "image/gif") { alert("움짤(GIF)은 올릴 수 없어 누나!"); event.target.value = ""; return; } const reader = new FileReader(); reader.onload = function(e) { const dataUrl = e.target.result; document.getElementById('bgMediaWrapper').innerHTML = `<img src="${dataUrl}" alt="Full Background">`; localStorage.setItem('myBgType', 'image'); try { localStorage.setItem('myBgData', dataUrl); } catch (err) { alert("사진 용량이 커서 다음 접속 시 풀릴 수 있어!"); } }; reader.readAsDataURL(file); }
            function setYoutubeBackground() { const inputVal = document.getElementById('bgYoutubeInput').value; const videoId = extractYoutubeId(inputVal); if (!videoId) { alert("유튜브 링크가 올바르지 않습니다."); return; } document.getElementById('bgMediaWrapper').innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?autoplay=1&mute=1&loop=1&playlist=${videoId}&controls=0&showinfo=0&rel=0" allow="autoplay; encrypted-media" allowfullscreen></iframe>`; localStorage.setItem('myBgType', 'youtube'); localStorage.setItem('myBgData', videoId); }
            
            async function toggleShare(index, type) {
                const box = document.getElementById(`stream-box-${index}`); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`);
                if (myStreams[index]) { stopShare(index); return; }
                if (cardData[index].reserved_by && cardData[index].reserved_by !== window.myNickname) { alert(`${cardData[index].reserved_by} 작가님의 고정석이라 사용할 수 없어!`); return; }
                if (hasCustomStatus(cardData[index].status)) { cardData[index].status = ""; updateStatusUI(index, ""); if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "status_update", index: index, status: "" })); } }
                try {
                    let stream;
                    if (type === 'screen') { 
                        stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always", frameRate: 10 }, audio: true }); 
                        btnScreen.innerText = "중지"; btnScreen.style.background = "#d63031"; btnCam.style.display = "none"; 
                    }
                    else { 
                        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 20, max: 24 } }, audio: false }); 
                        btnCam.innerText = "중지"; btnCam.style.background = "#d63031"; btnScreen.style.display = "none"; 
                    }
                    myStreams[index] = stream; const myName = window.myNickname || "익명"; const userEl = document.getElementById(`username-${index}`); if (userEl) userEl.value = myName; updateUsername(index, myName);
                    let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : ''; box.innerHTML = `<video id="video-${index}" autoplay playsinline muted disablePictureInPicture style="${filterStyle}"></video>`;
                    const localVideo = document.getElementById(`video-${index}`); localVideo.srcObject = stream; localVideo.play().catch(e => console.log(e));
                    if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "start_share", index: index })); }
                    stream.getVideoTracks()[0].onended = () => { stopShare(index); };
                } catch (err) { console.error("미디어 캡처 에러:", err); }
            }

            function stopShare(index) {
                if (myStreams[index]) { myStreams[index].getTracks().forEach(track => track.stop()); delete myStreams[index]; }
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "stop_share", index: index })); }
                renderBox(index); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`);
                if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; }
                if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; }
                const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "none"; }
            }

            setInterval(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN || !ws.clientId) return;
                for (let idx in expectedShares) {
                    const sharerId = expectedShares[idx]; if (sharerId === ws.clientId) continue; 
                    const pcKey = `${idx}_${sharerId}`; const pc = peerConnections[pcKey]; let isConnectionDead = false;
                    if (!pc) { isConnectionDead = true; } else { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { isConnectionDead = true; } }
                    if (isConnectionDead) { if (pc) { try { pc.close(); } catch(e) {} } delete peerConnections[pcKey]; ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId })); }
                }
            }, 5000);

            function extractYoutubeId(url) { if (!url) return null; url = url.trim(); if (url.length === 11) return url; const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/; const match = url.match(regExp); return (match && match[2].length === 11) ? match[2] : null; }

            function connectWebSocket() {
                const loc = window.location; let wsProtocol = loc.protocol === "https:" ? "wss://" : "ws://"; const wsUrl = wsProtocol + loc.host + "/ws";
                try {
                    ws = new WebSocket(wsUrl);
                    ws.onopen = function() {
                        const statusEl = document.getElementById('connStatus'); statusEl.innerText = "연결됨"; statusEl.className = "status-indicator status-online";
                        const myNick = window.myNickname || "익명"; const ownedArr = Array.from(myOwnedSlots);
                        ws.send(JSON.stringify({ type: "set_nickname", nickname: myNick, owned: ownedArr, password: sessionPassword, identity_key: identityKeyFor(myNick), enroll_code: pendingEnrollCode })); autoStampToday();
                        if (recordAutoSaveNeedsSync) flushRecordAutoSave();
                        
                        for (let idx in myStreams) {
                            if (myStreams[idx]) {
                                ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx) }));
                            }
                        }
                        ws.send(JSON.stringify({ type: "request_existing_shares" }));
                        for (let idx in expectedShares) {
                            const sharerId = expectedShares[idx];
                            if (sharerId && sharerId !== ws.clientId) {
                                ws.send(JSON.stringify({ type: "request_offer", index: parseInt(idx), target: sharerId }));
                            }
                        }

                        if (pingInterval) clearInterval(pingInterval); pingInterval = setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ping" })); } }, 5000); 
                    };
                    ws.onmessage = async function(event) {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.type === "private_tracker_state") { window.trackersData=data.trackers; loadMyLocalTrackerData(); refreshBadges(); refreshQuickDoneRows(); return; }
                            if (data.type === "chat_history") { renderChatHistory(data.messages); return; }
                            if (data.type === "pong") { return; }
                            if (data.type === "auth_ok") { window.isAdmin = !!data.is_admin; if (data.session) sessionToken = data.session; if (data.identity_key) saveIdentityKey(window.myNickname, data.identity_key); pendingEnrollCode = ""; loadPomoSheetUrl(); return; }
                            if (data.type === "auth_failed") {
                                if (pingInterval) clearInterval(pingInterval); ws.onclose = null; ws.close();
                                if (data.reason === "enroll_required" || data.reason === "enroll_invalid") {
                                    const code = prompt(data.reason === "enroll_invalid" ? "등록 코드가 맞지 않거나 기한(24시간)이 지났어. 방장에게 새 코드를 받아 적어줘." : "이 닉네임은 이미 기록이 있거나 방장이 등록 코드를 만든 이름이라, 방장에게 받은 등록 코드가 필요해. 코드를 적어줘.");
                                    if (code && code.trim()) { pendingEnrollCode = code.trim(); connectWebSocket(); return; }
                                }
                                sessionPassword = ""; sessionToken = ""; pendingEnrollCode = "";
                                const reasons = { identity: "이 닉네임은 다른 기기(브라우저)에 등록된 이름이야. 그 기기로 들어오거나 방장에게 등록 코드를 받아줘!", kicked: "방장이 내보낸 이름이라 잠시 뒤에 다시 들어와줘.", nickname: "쓸 수 없는 닉네임이야(다른 작가님과 너무 비슷하거나, 글자·숫자 말고 다른 기호가 있어). 다른 이름으로 들어와줘.", storage: "서버가 등록을 저장하지 못했어. 잠시 뒤에 다시 들어와줘.", busy: "지금은 로그인 시도가 너무 많아. 1분 뒤에 다시 해줘.", enroll_required: "등록 코드가 있어야 들어올 수 있어.", enroll_invalid: "등록 코드가 있어야 들어올 수 있어." };
                                alert(reasons[data.reason] || "비밀번호가 틀렸어! 다시 확인해봐."); window.location.reload(); return;
                            }
                            if (data.type === "enrollment_issued") { alert(data.ok ? `${data.target_nick} 등록 코드: ${data.code}\n24시간 안에 한 번만 쓸 수 있어. 본인에게만 따로 전해줘.` : data.retrying ? `${data.target_nick} 열쇠는 지금 막았지만 서버 저장이 안 됐어. 서버가 자동으로 다시 저장하고 있어.\n출입 기록에 '해제 저장됨'이 뜨면 등록 코드를 다시 만들어줘.` : "등록 코드를 만들지 못했어(이름을 확인하거나 잠시 뒤 다시)."); return; }
                            if (data.type === "identity_revoked") { try { localStorage.removeItem('gogogoIdentity:' + window.myNickname); } catch (e) {} alert("방장이 이 이름의 열쇠를 해제했어. 등록 코드를 받아 다시 들어와줘."); window.location.reload(); return; }
                            else if (data.type === "kicked") { alert("방장에 의해 방에서 쫓겨났어!"); localStorage.removeItem('mySavedNickname'); window.location.reload(); }
                            else if (data.type === "duplicate_kicked") {
                                alert("다른 기기(또는 창)에서 동일한 닉네임이 접속되어 이전 창은 얌전하게 종료할게 누나!");
                                if (pingInterval) clearInterval(pingInterval);
                                ws.onclose = null;
                                ws.close();
                            }
                            else if (data.type === "chat_cleared") { document.getElementById('chatHistory').innerHTML = ""; }
                            else if (data.type === "user_list") {
                                document.getElementById('userCount').innerText = data.count + "명";
                                window.activeNicknames = new Set(data.users.map(u => u.nickname));
                                let listHtml = data.users.map(u => { let kickBtn = ''; if (window.isAdmin && u.nickname !== window.myNickname) { kickBtn = `<button data-name="${escapeText(u.nickname)}" onclick="kickUser(this.dataset.name)" style="background:#d63031; border:none; color:white; border-radius:3px; padding:1px 4px; font-size:9px; cursor:pointer; margin-left:4px;">강퇴</button>`; } return `<span style="background:rgba(255,255,255,0.1); padding:3px 8px; border-radius:4px; display:inline-flex; align-items:center;"><b data-name="${escapeText(u.nickname)}" style="color:white;">${escapeText(u.nickname)}</b>${kickBtn}</span>`; }).join("");
                                document.getElementById('userListStr').innerHTML = listHtml;
                                refreshBadges();
                                applyEmptySlotVisibility();
                            }
                            else if (data.type === "chat") { logChat(data.senderName, data.msg, data.time, data.id); } 
                            else if (data.type === "update_notice") { window.rawNotice = data.notice; document.getElementById('noticeText').innerHTML = formatNotice(data.notice); }
                            else if (data.type === "status_update") { cardData[data.index].status = data.status; updateStatusUI(data.index, data.status); const box = document.getElementById(`stream-box-${data.index}`); if (box && !box.querySelector('video')) { renderBox(data.index); } }
                            else if (data.type === "seat_reservation_update") { applySeatCardState(data.index, data.user, data.reserved_by); }
                            else if (data.type === "seat_reservation_error") { applySeatCardState(data.index, data.user, data.reserved_by); alert(data.message || "고정석이라 사용할 수 없어!"); }
                            else if (data.type === "init_state") {
                                const state = data.state;
                                if (state.global_notice) { window.rawNotice = state.global_notice; document.getElementById('noticeText').innerHTML = formatNotice(state.global_notice); }
                                if (state.attendance) { window.attendanceData = state.attendance; }
                                if (state.admin_log) { window.adminLogData = state.admin_log; if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') { renderAdminLog(); } }
                                if (state.trackers) { window.trackersData = state.trackers; }
                                if (state.cards) {
                                    state.cards.forEach((card, i) => {
                                        if (cardData[i]) {
                                            cardData[i].user = card.user; if (Object.prototype.hasOwnProperty.call(card,"card_bg")) { cardData[i].card_bg=card.card_bg; knownBackgrounds.add(i); } cardData[i].is_mosaic = card.is_mosaic || false; cardData[i].is_large = card.is_large || false; cardData[i].status = typeof card.status === 'string' ? card.status : ''; cardData[i].reserved_by = card.reserved_by || null;
                                            cardData[i].is_local_hidden = cardData[i].is_local_hidden || false; 
                                            
                                            applyMosaicUI(i, cardData[i].is_mosaic); applySizeUI(i, cardData[i].is_large); updateStatusUI(i, cardData[i].status); applySeatReservationUI(i);
                                            const userEl = document.getElementById(`username-${i}`); if (userEl) userEl.value = card.user;
                                            const cardEl = document.getElementById(`card-card-${i}`); 
                                            if (cardEl) {
                                                if (safeBg(card.card_bg)) { cardEl.style.backgroundImage = `url('${safeBg(card.card_bg)}')`; }
                                                cardEl.style.order = (card.user === window.myNickname && window.myNickname) ? -1 : 0;
                                            }
                                            const hideBtn = document.getElementById(`hide-btn-${i}`);
                                            if(hideBtn) {
                                                hideBtn.innerText = cardData[i].is_local_hidden ? "보기" : "가리기";
                                                hideBtn.style.background = cardData[i].is_local_hidden ? "#e84393" : "#2d3436";
                                            }
                                            const box = document.getElementById(`stream-box-${i}`);
                                            if(box) box.style.visibility = cardData[i].is_local_hidden ? "hidden" : "visible";
                                            if (box && !box.querySelector('video')) { renderBox(i); }
                                        }
                                    });
                                }
                                if (state.chat_history) { renderChatHistory(state.chat_history); }
                                loadMyLocalTrackerData(); refreshBadges(); refreshQuickDoneRows(); loadSavedBackgrounds();
                                applyEmptySlotVisibility();
                            }
                            else if (data.type === "tracker_update") { 
                                if (!window.trackersData) window.trackersData = {};
                                window.trackersData[data.nickname] = data.tracker_data; if(data.nickname===window.myNickname) restoreTimer(); refreshBadges(); refreshQuickDoneRows();
                                if (window.currentViewingUser === data.nickname && document.getElementById('recordModal').style.display === 'flex') {
                                    loadRecordDataIntoUI(data.nickname);
                                }
                            }
                            else if (data.type === "attendance_update") { window.attendanceData = data.attendance; refreshBadges(); if (document.getElementById('attendanceModal').style.display === 'flex') { renderAttendanceBoard(); } }
                            else if (data.type === "admin_log_update") { if (!window.adminLogData) window.adminLogData = []; window.adminLogData.push(data.log); if (window.adminLogData.length > 100) window.adminLogData.shift(); if (window.isAdmin && document.getElementById('adminLogModal').style.display === 'flex') { renderAdminLog(); } }
                            else if (data.type === "username_change") { applySeatCardState(data.index, data.user, cardData[data.index].reserved_by); } 
                            else if (data.type === "card_bg_change") { if (!safeBg(data.dataUrl)) return; cardData[data.index].card_bg = data.dataUrl; const cardEl = document.getElementById(`card-card-${data.index}`); if (cardEl) { cardEl.style.backgroundImage = `url('${data.dataUrl}')`; } } 
                            else if (data.type === "toggle_mosaic") { if (cardData[data.index]) { cardData[data.index].is_mosaic = data.is_mosaic; applyMosaicUI(data.index, data.is_mosaic); } }
                            else if (data.type === "start_share") { const targetIndex = data.index; const sharerId = data.sender; if (data.target && data.target !== ws.clientId) return; expectedShares[targetIndex] = sharerId; if (ws.clientId && sharerId !== ws.clientId && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "request_offer", index: targetIndex, target: sharerId })); } }
                            else if (data.type === "request_offer" && data.target === ws.clientId) { const targetIndex = data.index; const viewerId = data.sender; if (myStreams[targetIndex]) { await createOfferForViewer(targetIndex, viewerId); } }
                            else if (data.type === "offer" && data.target === ws.clientId) {
                                const index = data.index; const senderId = data.sender; const pcKey = `${index}_${senderId}`;
                                if (peerConnections[pcKey]) { try { peerConnections[pcKey].close(); } catch(e) {} }
                                const pc = new RTCPeerConnection(rtcConfig); peerConnections[pcKey] = pc;
                                pc.oniceconnectionstatechange = () => { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { try { pc.close(); } catch(e) {} delete peerConnections[pcKey]; } };
                                pc.ontrack = (e) => { const box = document.getElementById(`stream-box-${index}`); let filterStyle = cardData[index].is_mosaic ? `filter: blur(5px);` : ''; box.innerHTML = `<video id="remote-video-${index}" autoplay playsinline disablePictureInPicture style="${filterStyle}"></video>`; const remoteVideo = document.getElementById(`remote-video-${index}`); remoteVideo.srcObject = e.streams[0]; remoteVideo.play().catch(err => console.log(err)); const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "inline-block"; soundBtn.innerText = "소리끄기"; soundBtn.style.background = "#00b894"; } };
                                pc.onicecandidate = (e) => { if (e.candidate && ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ice", index: index, target: senderId, candidate: e.candidate })); } };
                                await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
                                if (candidateBuffers[pcKey]) { for (const cand of candidateBuffers[pcKey]) { await pc.addIceCandidate(new RTCIceCandidate(cand)).catch(e => console.log(e)); } delete candidateBuffers[pcKey]; }
                                const answer = await pc.createAnswer(); 
                                const sdpWithBitrate = setMediaBitrate(answer.sdp, 50);
                                await pc.setLocalDescription({ type: answer.type, sdp: sdpWithBitrate });
                                
                                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "answer", index: index, target: senderId, sdp: pc.localDescription })); }
                            } 
                            else if (data.type === "answer" && data.target === ws.clientId) { const pcKey = `${data.index}_${data.sender}`; const pc = peerConnections[pcKey]; if (pc) await pc.setRemoteDescription(new RTCSessionDescription(data.sdp)); } 
                            else if (data.type === "ice" && data.target === ws.clientId) { const pcKey = `${data.index}_${data.sender}`; const pc = peerConnections[pcKey]; if (pc && pc.remoteDescription && pc.remoteDescription.type) { await pc.addIceCandidate(new RTCIceCandidate(data.candidate)).catch(e => console.log(e)); } else { if (!candidateBuffers[pcKey]) candidateBuffers[pcKey] = []; if (candidateBuffers[pcKey].length < 50) candidateBuffers[pcKey].push(data.candidate); } } 
                            else if (data.type === "stop_share") { const index = data.index; delete expectedShares[index]; for (let key in peerConnections) { if (key.startsWith(`${index}_`)) { try { peerConnections[key].getSenders().forEach(sender => peerConnections[key].removeTrack(sender)); peerConnections[key].close(); } catch(e) {} delete peerConnections[key]; } } renderBox(index); const btnScreen = document.getElementById(`share-btn-screen-${index}`); const btnCam = document.getElementById(`share-btn-cam-${index}`); if(btnScreen) { btnScreen.innerText = "화공"; btnScreen.style.background = "#ff7675"; btnScreen.style.display = "inline-block"; } if(btnCam) { btnCam.innerText = "캠"; btnCam.style.background = "#0984e3"; btnCam.style.display = "inline-block"; } const soundBtn = document.getElementById(`sound-toggle-btn-${index}`); if (soundBtn) { soundBtn.style.display = "none"; } }
                            else if (data.type === "welcome") { ws.clientId = data.clientId; setTimeout(() => { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "request_existing_shares" })); for (let idx in myStreams) { ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx) })); } } }, 800); }
                            else if (data.type === "request_existing_shares") { for (let idx in myStreams) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "start_share", index: parseInt(idx), target: data.sender })); } } }
                        } catch(e) { console.error("데이터 처리 에러:", e); }
                    };
                    
                    ws.onclose = function() { 
                        if (pingInterval) clearInterval(pingInterval); 
                        const statusEl = document.getElementById('connStatus'); 
                        if (statusEl) { 
                            statusEl.innerText = "서버 재연결 중..."; 
                            statusEl.className = "status-indicator status-offline"; 
                        } 
                        for (let key in peerConnections) { try { peerConnections[key].close(); } catch(e) {} delete peerConnections[key]; }
                        for (let k in expectedShares) delete expectedShares[k];
                        setTimeout(connectWebSocket, 3000); 
                    };
                } catch(e) { setTimeout(connectWebSocket, 2000); }
            }

            async function createOfferForViewer(index, viewerId) {
                const pcKey = `${index}_${viewerId}`; if (peerConnections[pcKey]) { try { peerConnections[pcKey].close(); } catch(e) {} }
                const pc = new RTCPeerConnection(rtcConfig); peerConnections[pcKey] = pc;
                pc.oniceconnectionstatechange = () => { const state = pc.iceConnectionState; if (state === 'disconnected' || state === 'failed' || state === 'closed') { try { pc.getSenders().forEach(sender => pc.removeTrack(sender)); pc.close(); } catch(e) {} delete peerConnections[pcKey]; } };
                const stream = myStreams[index]; if (stream) { stream.getTracks().forEach(track => pc.addTrack(track, stream)); }
                pc.onicecandidate = (e) => { if (e.candidate && ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "ice", index: index, target: viewerId, candidate: e.candidate })); } };
                
                const offer = await pc.createOffer(); 
                const sdpWithBitrate = setMediaBitrate(offer.sdp, 50);
                await pc.setLocalDescription({ type: offer.type, sdp: sdpWithBitrate });
                
                if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "offer", index: index, target: viewerId, sdp: pc.localDescription })); }
            }

            function sendChat() { const input = document.getElementById('chatInput'); const msgText = input.value.trim(); if (!msgText) return; const myName = window.myNickname || "익명"; const now = kstNow(); const month = now.getMonth() + 1; const date = now.getDate(); const timeString = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }); const timeStr = `${month}/${date} ${timeString}`; if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ type: "chat", senderName: myName, msg: msgText, time: timeStr })); input.value = ''; } }
            
            function openRecordModalByIndex(index) {
                const cardUser = cardData[index].user;
                if (!cardUser || cardUser.startsWith("자리")) {
                    alert("아직 자리에 작가님이 앉지 않았어요! 😅");
                    return;
                }
                openRecordModal(cardUser);
            }

            function openRecordModal(nickname) {
                if (!nickname || nickname.startsWith("자리")) {
                    alert("아직 자리에 작가님이 앉지 않았어요! 😅");
                    return;
                }
                window.currentViewingUser = nickname;
                
                if (!window.trackersData[nickname]) {
                    window.trackersData[nickname] = { targetChars: 5000, doneChars: 0, themeColor: "#d87093", calendar: {}, todos: [], lastDate: "" };
                }
                
                loadRecordDataIntoUI(nickname);
                const modal = document.getElementById('recordModal');
                if (modal) modal.style.display = 'flex';
            }

            function loadRecordDataIntoUI(nickname) {
                const isMe = (nickname === window.myNickname);
                const now = kstNow();
                const todayStr = `${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;
                
                window.recViewYear = now.getFullYear();
                window.recViewMonth = now.getMonth() + 1;
                
                if (isMe) {
                    if (window.trackersData && window.trackersData[nickname]) {
                        if (window.trackersData[nickname].lastDate && window.trackersData[nickname].lastDate !== todayStr) {
                            window.trackersData[nickname].doneChars = 0;
                            // Todos persist across dates.
                            window.trackersData[nickname].lastDate = todayStr;
                            recTotalSeconds = 0;
                            localStorage.setItem('doneChars', '0');
                            // Keep private todos.
                            localStorage.setItem('lastDate', todayStr);
                            
                            if (ws && ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: "tracker_update", nickname: nickname, tracker_data: window.trackersData[nickname] }));
                            }
                        }
                    }
                }
                
                let data = window.trackersData[nickname] || { targetChars: 5000, doneChars: 0, themeColor: "#d87093", calendar: {}, todos: [], lastDate: todayStr };

                if (!isMe && data.lastDate && data.lastDate !== todayStr) {
                    data = { ...data, doneChars: 0, todos: [] };
                }

                document.getElementById('rec-title').innerText = `✨ ${nickname} 작가님의 집필 기록방 ✨`;

                document.getElementById('rec-target-chars').value = data.targetChars || 5000;
                document.getElementById('rec-done-chars').value = data.doneChars || 0;
                
                const savedColor = data.themeColor || "#d87093";
                document.getElementById('themeColorPicker').value = savedColor;
                changeThemeColor(savedColor);

                const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                const dayStr = now.getDate().toString();

                if (isMe && recTotalSeconds === 0) {
                    if (data.lastDate === todayStr && data.calendar && data.calendar[monthStr] && data.calendar[monthStr][dayStr] && data.calendar[monthStr][dayStr].seconds) {
                        recTotalSeconds = data.calendar[monthStr][dayStr].seconds;
                        updateMainTimerDisplay();
                    }
                }

                document.getElementById('rec-target-chars').readOnly = !isMe;
                document.getElementById('rec-done-chars').readOnly = !isMe;
                document.getElementById('themeColorPicker').disabled = !isMe;
                
                const autoSaveStatus = document.getElementById('rec-autosave-status');
                if (autoSaveStatus) {
                    autoSaveStatus.style.display = isMe ? "inline" : "none";
                    autoSaveStatus.textContent = "☁️ 자동 저장";
                    autoSaveStatus.style.color = "#777";
                }
                document.getElementById('rec-my-timer-area').style.display = isMe ? "flex" : "none";
                
                const todoInputArea = document.getElementById('rec-todo-input-area');
                if (todoInputArea) todoInputArea.style.display = isMe ? "flex" : "none";
                
                document.getElementById('rec-pomo-section').style.display = isMe ? "block" : "none";

                const ul = document.getElementById('rec-todo-list');
                ul.innerHTML = '';
                document.getElementById("rec-todo-list").closest(".rec-section").style.display = isMe ? "flex" : "none";
                const todos = isMe ? (data.todos || []) : [];
                todos.forEach(todo => {
                    const li = document.createElement('li');
                    const checked = todo.done ? "checked" : "";
                    const compClass = todo.done ? "rec-completed" : "";
                    const disabled = !isMe ? "disabled" : "";
                    const delBtn = isMe ? `<button class="rec-btn rec-btn-del" onclick="removeRecTodo(this)">삭제</button>` : "";
                    li.innerHTML = `
                        <label><input type="checkbox" onchange="toggleRecTodo(this)" ${checked} ${disabled}> <span class="${compClass}">${escapeText(todo.text)}</span></label>
                        <div><span class="rec-time-tag">${escapeText(todo.time || '')}</span> ${delBtn}</div>
                    `;
                    ul.appendChild(li);
                });

                buildRecordCalendar(nickname, data.calendar || {});
                const goalMonth = monthKey();
                document.getElementById('rec-month-goal').value = (data.monthlyGoals || {})[goalMonth] || 0;
                document.getElementById('rec-month-goal').disabled = !isMe;
                updateMonthlyProgress(nickname);
                if (isMe) restoreTimer();
                checkGoalAchievement();
            }

            function buildRecordCalendar(nickname, calendarData) {
                const now = kstNow();
                const y = window.recViewYear;
                const m = window.recViewMonth;
                const today = (y === now.getFullYear() && m === now.getMonth() + 1) ? now.getDate() : -1;
                const monthStr = `${y}-${String(m).padStart(2, '0')}`;
                
                document.getElementById('rec-cal-month-title').innerHTML = `
                    <div style="display:flex; justify-content:space-between; width:100%; align-items:center;">
                        <span>
                            <button class="rec-btn" data-name="${escapeText(nickname)}" onclick="changeRecMonth(-1, this.dataset.name)" style="padding: 2px 6px;">◀️</button>
                            📅 ${y}년 ${m}월 집필 통계
                            <button class="rec-btn" data-name="${escapeText(nickname)}" onclick="changeRecMonth(1, this.dataset.name)" style="padding: 2px 6px;">▶️</button>
                        </span>
                        <button class="rec-btn" onclick="toggleRecCalendar()" id="rec-cal-toggle-btn" style="font-size: 11px; padding: 3px 8px;">접기 🔼</button>
                    </div>
                `;
                
                const grid = document.getElementById('rec-calendar-grid');
                let html = '';
                const daysOfWeek = ['일','월','화','수','목','금','토']; 
                daysOfWeek.forEach(d => { html += `<div class="rec-cal-header">${d}</div>`; });
                
                const firstDay = new Date(y, m - 1, 1).getDay(); 
                const daysInMonth = new Date(y, m, 0).getDate(); 
                
                for(let i=0; i<firstDay; i++) { html += `<div class="rec-cal-day" style="background:transparent; border:none;"></div>`; }
                
                const monthData = calendarData[monthStr] || {};
                
                for(let i=1; i<=daysInMonth; i++) {
                    const dayData = monthData[i.toString()];
                    let isDone = false;
                    let inner = `<span class="date-num">${i}일${i===today?'(오늘)':''}</span>`;
                    
                    if (dayData) {
                        isDone = (parseInt(dayData.done) >= parseInt(dayData.target)) && parseInt(dayData.target) > 0;
                        inner += `<span class="goal">목표:${Number(dayData.target).toLocaleString()}</span>`;
                        inner += `<span class="act" ${isDone?'style="font-weight:bold;"':''}>완료:${Number(dayData.done).toLocaleString()}</span>`;
                        
                        if (dayData.seconds !== undefined) {
                            let h = Math.floor(dayData.seconds / 3600);
                            let min = Math.floor((dayData.seconds % 3600) / 60);
                            let s = dayData.seconds % 60;
                            let timeStr = `${String(h).padStart(2,'0')}:${String(min).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
                            inner += `<span class="time">⏱️${timeStr}</span>`;
                        }
                    }
                    
                    let cls = "rec-cal-day";
                    if (isDone) cls += " done";
                    if (i === today) {
                        html += `<div class="${cls}" style="border: 2px solid var(--rec-primary);">${inner}</div>`;
                    } else {
                        html += `<div class="${cls}">${inner}</div>`;
                    }
                }
                grid.innerHTML = html;
            }

            function loadMyLocalTrackerData() { restoreTimer(); }


            function goalCelebrationChoiceKey(target) {
                if (!window.myNickname) return '';
                return `goalCelebrationChoice:${window.myNickname}:${dayKey()}:${target}`;
            }

            function chooseGoalCelebration(receiveInChat) {
                const target = parseInt(document.getElementById('rec-target-chars').value) || 0;
                const done = parseInt(document.getElementById('rec-done-chars').value) || 0;
                if (window.currentViewingUser !== window.myNickname || target <= 0 || done < target) return;

                if (receiveInChat && (!ws || ws.readyState !== WebSocket.OPEN)) {
                    alert('서버에 다시 연결된 뒤 축하받기를 눌러줘!');
                    return;
                }

                const choiceKey = goalCelebrationChoiceKey(target);
                if (choiceKey) localStorage.setItem(choiceKey, receiveInChat ? 'chat' : 'none');
                window.goalCelebrationChoiceMade = true;
                document.getElementById('rec-congrats-banner').style.display = 'none';

                if (receiveInChat) {
                    ws.send(JSON.stringify({ type: 'goal_celebration_chat' }));
                }
            }


            function checkGoalAchievement() {
                const target = parseInt(document.getElementById('rec-target-chars').value) || 0;
                const done = parseInt(document.getElementById('rec-done-chars').value) || 0;
                const banner = document.getElementById('rec-congrats-banner');
                const message = document.getElementById('rec-congrats-message');
                const actions = document.getElementById('rec-congrats-actions');
                const isMe = (window.currentViewingUser === window.myNickname);

                if (done >= target && target > 0) {
                    window.goalAchieved = isMe ? true : window.goalAchieved;
                    if (isMe) {
                        const savedChoice = localStorage.getItem(goalCelebrationChoiceKey(target));
                        window.goalCelebrationChoiceMade = Boolean(savedChoice);
                        if (savedChoice) {
                            banner.style.display = 'none';
                            return;
                        }
                        message.textContent = '🎉 축하합니다! 오늘의 목표 분량을 모두 달성했습니다!';
                        actions.style.display = 'flex';
                    } else {
                        message.textContent = `🎉 우와! ${window.currentViewingUser} 작가님이 오늘의 목표를 달성했습니다!`;
                        actions.style.display = 'none';
                    }
                    banner.style.display = 'block';
                } else {
                    banner.style.display = 'none';
                    actions.style.display = 'none';
                    if (isMe) {
                        window.goalAchieved = false;
                        window.goalCelebrationChoiceMade = false;
                    }
                }
            }

            function setRecordAutoSaveStatus(message, color = '#777') {
                const status = document.getElementById('rec-autosave-status');
                if (!status) return;
                status.textContent = message;
                status.style.color = color;
            }

            function scheduleRecordAutoSave() {
                if (!window.myNickname || window.currentViewingUser !== window.myNickname) return;
                setRecordAutoSaveStatus('⏳ 저장 중…', '#a66b00');
                if (recordAutoSaveTimer) clearTimeout(recordAutoSaveTimer);
                recordAutoSaveTimer = setTimeout(() => {
                    recordAutoSaveTimer = null;
                    saveRecordData(true);
                }, 500);
            }

            function flushRecordAutoSave() {
                if (!window.myNickname || window.currentViewingUser !== window.myNickname) return;
                if (recordAutoSaveTimer) {
                    clearTimeout(recordAutoSaveTimer);
                    recordAutoSaveTimer = null;
                }
                saveRecordData(true);
            }

            window.addEventListener('pagehide', flushRecordAutoSave);

            function saveRecordData(isSilent = false) {
                if (!window.myNickname || window.currentViewingUser !== window.myNickname) return;
                
                const now = kstNow();
                const todayStr = `${now.getFullYear()}-${now.getMonth()+1}-${now.getDate()}`;

                if (!window.trackersData) window.trackersData = {};
                if (!window.trackersData[window.myNickname]) window.trackersData[window.myNickname] = { calendar: {}, todos: [], lastDate: todayStr };
                let myData = window.trackersData[window.myNickname];

                if (myData.lastDate && myData.lastDate !== todayStr) {
                     document.getElementById('rec-done-chars').value = 0;
                     // Keep private todos in the UI.
                     recTotalSeconds = 0;
                     updateMainTimerDisplay();
                     myData.lastDate = todayStr;
                     myData.doneChars = 0;
                     // Keep private todos.
                     localStorage.setItem('doneChars', '0');
                     // Keep private todos.
                     localStorage.setItem('lastDate', todayStr);
                     alert("자정이 지나 날짜가 변경되었습니다! 오늘의 완료량과 타이머가 초기화되었습니다. 할 일은 유지됩니다. 🌱");
                }

                const target = document.getElementById('rec-target-chars').value;
                const done = document.getElementById('rec-done-chars').value;
                const color = document.getElementById('themeColorPicker').value;
                const totalSecs = recTotalSeconds;

                localStorage.setItem('targetChars', target);
                localStorage.setItem('doneChars', done);
                localStorage.setItem('themeColor', color);
                localStorage.setItem('lastDate', todayStr);
                
                const monthStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
                const dayStr = now.getDate().toString();
                
                myData.targetChars = target;
                myData.doneChars = done;
                myData.themeColor = color;
                myData.lastDate = todayStr;
                myData.monthlyGoals = myData.monthlyGoals || {};
                myData.monthlyGoals[monthStr] = Math.max(0, Number(document.getElementById("rec-month-goal").value) || 0);

                const ul = document.getElementById('rec-todo-list');
                const lis = ul.querySelectorAll('li');
                let currentTodos = [];
                lis.forEach(li => {
                    const textEl = li.querySelector('label span');
                    if(!textEl) return;
                    const text = textEl.innerText;
                    const doneBox = li.querySelector('input[type="checkbox"]');
                    const isDone = doneBox ? doneBox.checked : false;
                    const timeTag = li.querySelector('.rec-time-tag');
                    const timeStr = timeTag ? timeTag.innerText : '';
                    currentTodos.push({ text: text, done: isDone, time: timeStr });
                });
                myData.todos = currentTodos;
                localStorage.setItem('myTodos', JSON.stringify(currentTodos));

                if (!myData.calendar) myData.calendar = {};
                if (!myData.calendar[monthStr]) myData.calendar[monthStr] = {};
                myData.calendar[monthStr][dayStr] = { target: target, done: done, seconds: totalSecs };

                let syncedToServer = false;
                if (ws && ws.readyState === WebSocket.OPEN) {
                    try {
                        ws.send(JSON.stringify({ type: "tracker_update", nickname: window.myNickname, tracker_data: myData }));
                        syncedToServer = true;
                    } catch (error) {
                        console.warn('집필 기록 자동 저장 재시도 대기:', error);
                    }
                }
                recordAutoSaveNeedsSync = !syncedToServer;

                buildRecordCalendar(window.myNickname, myData.calendar);
                updateMonthlyProgress(window.myNickname);
                refreshBadges();
                refreshQuickDoneRows();
                checkGoalAchievement();
                setRecordAutoSaveStatus(syncedToServer ? '☁️ 자동 저장됨' : '⚠️ 재연결 후 다시 저장', syncedToServer ? '#2e7d32' : '#b45309');
            }

            function changeThemeColor(hex) {
                if (!/^#[0-9a-fA-F]{6}$/.test(String(hex))) hex = '#d87093'; // 남이 저장한 값이 CSS로 섞이지 않게
                const root = document.documentElement;
                root.style.setProperty('--rec-primary', hex);
                root.style.setProperty('--rec-border', hex);
                root.style.setProperty('--rec-bg', '#ffffff');
                root.style.setProperty('--rec-box', '#ffffff');
                root.style.setProperty('--rec-sec', hex + '10');
                root.style.setProperty('--rec-done', hex + '30');
            }

            function updateMainTimerDisplay() {
                const hrs = String(Math.floor(recTotalSeconds / 3600)).padStart(2, '0');
                const mins = String(Math.floor((recTotalSeconds % 3600) / 60)).padStart(2, '0');
                const secs = String(recTotalSeconds % 60).padStart(2, '0');
                document.getElementById('rec-main-timer').textContent = `${hrs}:${mins}:${secs}`;
            }

            function startPomodoro() {
                if (recPomoInterval) return;
                const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                if (recPomoSeconds === 25 * 60) recPomoSeconds = workMins * 60;
                recPomoInterval = setInterval(() => {
                    if (recPomoSeconds > 0) {
                        recPomoSeconds--;
                        const mins = String(Math.floor(recPomoSeconds / 60)).padStart(2, '0');
                        const secs = String(recPomoSeconds % 60).padStart(2, '0');
                        const status = recIsWorking ? "(집필 중 ✍️)" : "(휴식 중 ☕)";
                        document.getElementById('rec-pomo-display').textContent = `${mins}:${secs} ${status}`;
                    } else {
                        playBipSound();
                        if (recIsWorking) {
                            recIsWorking = false;
                            const restMins = parseInt(document.getElementById('rec-pomo-rest').value) || 5;
                            recPomoSeconds = restMins * 60;
                            alert('집필 시간 종료! 휴식 시작 ☕');
                        } else {
                            recIsWorking = true;
                            const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                            recPomoSeconds = workMins * 60;
                            alert('휴식 시간이 종료되었습니다! 다시 집필 시작 ✍️');
                        }
                    }
                }, 1000);
            }

            function pausePomodoro() {
                clearInterval(recPomoInterval);
                recPomoInterval = null;
            }

            function resetPomodoro() {
                pausePomodoro();
                const workMins = parseInt(document.getElementById('rec-pomo-work').value) || 25;
                recPomoSeconds = workMins * 60;
                recIsWorking = true;
                document.getElementById('rec-pomo-display').textContent = "25:00 (대기 중)";
            }

            function toggleRecCalendar() {
                const wrap = document.getElementById('rec-calendar-wrap');
                const btn = document.getElementById('rec-cal-toggle-btn');
                if (wrap.style.display === 'block') {
                    wrap.style.display = 'none';
                    btn.textContent = '펼치기 🔽';
                } else {
                    wrap.style.display = 'block';
                    btn.textContent = '접기 🔼';
                }
            }

            function addRecTodo() {
                const input = document.getElementById('rec-new-todo-text');
                const text = input.value.trim();
                if (!text) return;
                const ul = document.getElementById('rec-todo-list');
                const li = document.createElement('li');
                li.innerHTML = `
                    <label><input type="checkbox" onchange="toggleRecTodo(this)"> <span>${escapeText(text)}</span></label>
                    <div><span class="rec-time-tag"></span> <button class="rec-btn rec-btn-del" onclick="removeRecTodo(this)">삭제</button></div>
                `;
                ul.appendChild(li);
                input.value = '';
                saveRecordData(true);
            }

            function removeRecTodo(btn) { 
                btn.closest('li').remove(); 
                saveRecordData(true);
            }

            function toggleRecTodo(checkbox) {
                const li = checkbox.closest('li');
                const span = li.querySelector('label span');
                const timeTag = li.querySelector('.rec-time-tag');
                if (checkbox.checked) {
                    span.classList.add('rec-completed');
                    const now = kstNow();
                    timeTag.textContent = `(완료: ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')})`;
                } else {
                    span.classList.remove('rec-completed');
                    timeTag.textContent = '';
                }
                saveRecordData(true);
            }

            function playBipSound() {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                let beepCount = 0;
                const interval = setInterval(() => {
                    if (beepCount >= 4) { clearInterval(interval); return; }
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.type = 'sine';
                    osc.frequency.setValueAtTime(880, audioCtx.currentTime);
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.start();
                    osc.stop(audioCtx.currentTime + 0.15);
                    beepCount++;
                }, 200);
            }

            function changeRecMonth(offset, nickname) {
                window.recViewMonth += offset;
                if (window.recViewMonth < 1) { window.recViewMonth = 12; window.recViewYear--; }
                else if (window.recViewMonth > 12) { window.recViewMonth = 1; window.recViewYear++; }
                
                const data = window.trackersData[nickname] || { calendar: {} };
                buildRecordCalendar(nickname, data.calendar || {});
            }

            function kstNow() {
                const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Seoul', year:'numeric',month:'numeric',day:'numeric',hour:'numeric',minute:'numeric',second:'numeric',hourCycle:'h23'}).formatToParts(new Date());
                const p = Object.fromEntries(parts.map(x => [x.type,x.value]));
                return new Date(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second);
            }
            function monthKey() { const d=kstNow(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; }
            function previousMonthKey() {
                const d = kstNow();
                d.setDate(1);
                d.setMonth(d.getMonth() - 1);
                return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
            }
            function dayKey() { const d=kstNow(); return `${d.getFullYear()}-${d.getMonth()+1}-${d.getDate()}`; }
            function escapeText(value) { return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]); }
            function lastSevenAttendanceDates() {
                const dates = [];
                const today = kstNow();
                today.setHours(12, 0, 0, 0);

                // 이번 주 월요일부터 오늘까지만 계산
                const daysSinceMonday = (today.getDay() + 6) % 7;

                for (let offset = 0; offset <= daysSinceMonday; offset++) {
                    const date = new Date(today);
                    date.setDate(today.getDate() - offset);
                    dates.push({
                        month: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`,
                        day: date.getDate()
                    });
                }
                return dates;
            }
            function weeklyAttendanceCounts() {
                const counts = {};
                for (const date of lastSevenAttendanceDates()) {
                    const month = window.attendanceData?.[date.month] || {};
                    for (const [name, days] of Object.entries(month)) {
                        if (new Set(days || []).has(date.day)) counts[name] = (counts[name] || 0) + 1;
                    }
                }
                return counts;
            }
            function timerAction(action) {
                if (!ws || ws.readyState!==WebSocket.OPEN) { alert('재연결 후 타이머를 조작해주세요.'); return; }
                ws.send(JSON.stringify({type:'timer_action',action}));
            }
            function restoreTimer() {
                const data=window.trackersData[window.myNickname];
                if (!data) return;
                const t=data.timer;
                if (!t || t.date!==dayKey()) recTotalSeconds=0;
                else recTotalSeconds=Math.floor((Number(t.seconds)||0)+(t.started ? Math.max(0,Date.now()/1000-t.started):0));
                updateMainTimerDisplay();
            }
            let observedDay=dayKey();
            setInterval(() => {
                restoreTimer(); refreshBadges();
                if(observedDay!==dayKey()) {
                    observedDay=dayKey(); window.goalAchieved=false;
                    const mine=window.trackersData[window.myNickname];
                    if(mine) { mine.doneChars=0; mine.lastDate=observedDay; if(mine.timer) mine.timer={date:observedDay,seconds:0,started:null}; }
                    if(window.currentViewingUser===window.myNickname && mine) loadRecordDataIntoUI(window.myNickname);
                    refreshQuickDoneRows();
                    if(ws && ws.readyState===WebSocket.OPEN) { timerAction('sync'); autoStampToday(); }
                }
            },1000);
            document.addEventListener('visibilitychange', () => { if(!document.hidden) restoreTimer(); });
            function monthlyDoneFor(name, targetMonth) {
                const month=(window.trackersData[name]?.calendar || {})[targetMonth] || {};
                return Object.values(month).reduce((sum,r)=>sum+Math.max(0,Number(r.done)||0),0);
            }
            function monthlyDone(name) { return monthlyDoneFor(name, monthKey()); }
            function isMonthlyLevelUp(name) {
                const earnedMonth = previousMonthKey();
                const goal = Number(window.trackersData[name]?.monthlyGoals?.[earnedMonth]) || 0;
                return goal > 0 && monthlyDoneFor(name, earnedMonth) >= goal;
            }
            function updateMonthlyProgress(name) {
                const progress = document.getElementById('rec-month-progress');
                if (!progress) return;
                const done = monthlyDone(name);
                const goal = Number(window.trackersData[name]?.monthlyGoals?.[monthKey()]) || 0;
                const goalText = goal > 0 ? ` / 목표 ${goal.toLocaleString()}자` : ' / 월 목표 미설정';
                const nextMonthText = goal > 0 && done >= goal ? ' · 다음 달 노란 닉네임 확정 ✨' : '';
                progress.textContent = `이번 달 완료: ${done.toLocaleString()}자${goalText}${nextMonthText}`;
            }
            function attendanceRank(name) {
                const counts=weeklyAttendanceCounts();
                const count=counts[name] || 0;
                if(!count) return 0;
                return 1+Object.values(counts).filter(value=>value>count).length;
            }
            function attendanceMedalGroup(name) {
                return attendanceRank(name);
            }
            function badgeFor(name) {
                const rank=attendanceMedalGroup(name);
                return ({1:'🥇',2:'🥈',3:'🥉'})[rank] || '';
            }
            function refreshBadges() {
                cardData.forEach((card,i)=>{
                    const input=document.getElementById(`username-${i}`);
                    if(!input) return;
                    const levelUp=isMonthlyLevelUp(card.user);
                    const levelChanged=input.dataset.levelUp!==String(levelUp);
                    input.dataset.levelUp=String(levelUp);
                    input.classList.toggle('level-up-name',levelUp);
                    let badge=document.getElementById(`badge-${i}`);
                    if(!badge) { badge=document.createElement('span'); badge.id=`badge-${i}`; input.before(badge); }
                    badge.textContent=badgeFor(card.user); badge.title='이번 주 출석 순위';
                    if(levelChanged) {
                        const box=document.getElementById(`stream-box-${i}`);
                        if(box && !box.querySelector('video') && !hasCustomStatus(card.status)) renderBox(i);
                    }
                });
                document.querySelectorAll('#userListStr b').forEach(el=>{
                    const name=el.dataset.name || el.textContent; el.dataset.name=name;
                    el.classList.toggle('level-up-name',isMonthlyLevelUp(name));
                    el.textContent=badgeFor(name)+name;
                });
                document.querySelectorAll('#chatHistory b[data-name]').forEach(el=>{
                    const name=el.dataset.name;
                    el.classList.toggle('level-up-name',isMonthlyLevelUp(name));
                    el.textContent=badgeFor(name)+name;
                });
            }
            function toggleDashboard() {
                const panel=document.getElementById('dashboardPanel');
                const collapsed=panel.dataset.collapsed!=='true'; panel.dataset.collapsed=String(collapsed);
                const heading=panel.firstElementChild;
                Array.from(heading.children).slice(1).forEach(el=>el.style.display=collapsed?'none':'flex');
                Array.from(panel.children).slice(1).forEach(el=>el.style.display=collapsed?'none':'');
                document.getElementById('dashboardToggle').textContent=collapsed?'펼치기':'접기';
                localStorage.setItem('dashboardCollapsed',String(collapsed));
            }
            function toggleChatPanel() {
                const panel=document.querySelector('.chat-box');
                const collapsed=panel.dataset.collapsed!=='true'; panel.dataset.collapsed=String(collapsed);
                document.getElementById('chatHistory').style.display=collapsed?'none':'';
                panel.querySelector('.chat-input').style.display=collapsed?'none':'flex';
                panel.style.flexGrow=collapsed?'0':'1'; panel.style.height=collapsed?'auto':'100%';
                document.getElementById('chatToggle').textContent=collapsed?'펼치기':'접기';
                localStorage.setItem('chatCollapsed',String(collapsed));
            }
            function applyChatTheme(theme) {
                const panel=document.querySelector('.chat-box');
                const dashboard=document.getElementById('dashboardPanel');
                const button=document.getElementById('chat-theme-btn');
                const isLight=theme==='light';
                if(panel) panel.classList.toggle('chat-light',isLight);
                if(dashboard) dashboard.classList.toggle('dashboard-light',isLight);
                if(button) {
                    button.textContent=isLight?'🌙 패널 검정':'☀️ 패널 흰색';
                    button.style.background=isLight?'#2d3436':'#f5f5f5';
                    button.style.color=isLight?'#fff':'#2d3436';
                    button.title=isLight?'대시보드와 채팅창을 검정색으로 바꾸기':'대시보드와 채팅창을 흰색으로 바꾸기';
                }
                localStorage.setItem('chatTheme',isLight?'light':'dark');
            }
            function toggleChatTheme() {
                const isLight=document.querySelector('.chat-box')?.classList.contains('chat-light');
                applyChatTheme(isLight?'dark':'light');
            }
            applyChatTheme(localStorage.getItem('chatTheme')==='light'?'light':'dark');
            if(localStorage.getItem('dashboardCollapsed')==='true') toggleDashboard();
            if(localStorage.getItem('chatCollapsed')==='true') toggleChatPanel();

            checkLogin();
        </script>
    </body>
    </html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    client_id = str(id(websocket))
    authed = False
    is_admin = False
    session_token = None
    recent_messages = []

    try:
        await websocket.send_text(json.dumps({"type": "welcome", "clientId": client_id}))

        while True:
            data = await websocket.receive_text()
            now_mono = time.monotonic()
            recent_messages.append(now_mono)
            while recent_messages and now_mono - recent_messages[0] > 2:
                recent_messages.pop(0)
            if len(recent_messages) > 120:
                break  # 너무 많이 보내는 연결은 끊음
            packet = json.loads(data)
            if not isinstance(packet, dict):
                continue
            p_type = packet.get("type")

            if p_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if p_type == "set_nickname":
                nickname = normalize_nickname(packet.get("nickname"))
                wants_admin = nickname == ADMIN_NICKNAME
                expected = ADMIN_PASSWORD_SERVER if wants_admin else ROOM_PASSWORD_SERVER
                # 방 암호는 모두가 같으므로 멤버는 이름 열쇠로 구분합니다.
                # - 열쇠가 있는 이름: 맞는 열쇠만
                # - 기록이 있거나 방장이 등록 코드를 만든 이름: 방장이 준 일회용 등록 코드만
                # - 아무 기록도 없는 새 이름: 처음 온 사람이 등록
                # 등록은 DB 저장 확인 뒤에만 승인하고, 그 전에는 어떤 방 정보도 보내지 않습니다.
                stored_keys = server_state.setdefault("identity_keys", {})
                pending_enrolls = server_state.setdefault("identity_enroll", {})
                fail_reason = None
                issued_key = None
                if not nickname:
                    fail_reason = "nickname"
                elif auth_throttled(auth_source(websocket)):
                    fail_reason = "busy"  # 암호 맞고 틀림을 알려 주기 전에 멈춤(무차별 대입 중 판정 숨김)
                elif not await password_ok_async(packet.get("password"), expected):
                    fail_reason = "password"
                elif authed and nickname != manager.active_users.get(websocket):
                    fail_reason = "nickname"  # 인증된 연결은 이름을 바꿀 수 없음
                elif time.time() < kicked_until.get(nickname, 0):
                    fail_reason = "kicked"
                to_close = []
                if not fail_reason:
                    # 같은 이름의 등록·재발급·승인은 하나씩. 승인과 이름 연결을 잠금 안에서 끝내서,
                    # 그 뒤의 강퇴·재발급·중복 접속 처리가 이 연결을 반드시 찾게 합니다.
                    async with identity_lock(nickname):
                        if not wants_admin:
                            fail_reason, issued_key = await check_member_identity(nickname, packet)
                        if not fail_reason and time.time() < kicked_until.get(nickname, 0):
                            fail_reason = "kicked"  # DB를 기다리는 사이 강퇴된 경우
                        if not fail_reason:
                            previous_name = manager.active_users.get(websocket, "연결중...")
                            is_first_identity = previous_name == "연결중..."
                            was_already_present = any(name == nickname and existing_ws != websocket for existing_ws, name in manager.active_users.items())
                            to_close = [existing_ws for existing_ws, name in list(manager.active_users.items()) if name == nickname and existing_ws != websocket]
                            manager.active_users[websocket] = nickname
                            manager.authed_connections.add(websocket)
                            first_auth = not authed
                            authed = True
                            is_admin = wants_admin
                            if is_admin:
                                admin_sockets.add(websocket)
                            else:
                                admin_sockets.discard(websocket)
                            if session_token is None:
                                session_token = secrets.token_urlsafe(24)
                                manager.sessions[session_token] = websocket
                if fail_reason:
                    if fail_reason in ("password", "identity", "enroll_invalid"):
                        note_auth_failure(auth_source(websocket))
                        await asyncio.sleep(1)
                    await websocket.send_text(json.dumps({"type": "auth_failed", "reason": fail_reason}))
                    await revoke_connection(websocket)
                    break
                for old_ws in to_close:
                    await revoke_connection(old_ws, {"type": "duplicate_kicked"})
                auth_packet = {"type": "auth_ok", "is_admin": is_admin, "session": session_token}
                if issued_key:
                    auth_packet["identity_key"] = issued_key
                await websocket.send_text(json.dumps(auth_packet))
                if websocket not in manager.authed_connections:
                    break  # 보내는 사이 강퇴·재발급됨
                if first_auth:  # 예전에는 연결 직후(암호 확인 전) 보내던 것
                    await websocket.send_text(json.dumps({"type": "init_state", "state": state_for_conn(nickname, is_admin)}))
                owned = packet.get("owned", [])
                pending_leave = pending_presence_leaves.pop(nickname, None)
                resumed_during_grace = pending_leave is not None and not pending_leave.done()
                if pending_leave is not None:
                    pending_leave.cancel()
                if issued_key and not wants_admin and packet.get("enroll_code"):
                    await post_presence_chat(f"🔑 «{nickname}» 작가님이 방장 등록 코드로 이름 열쇠를 받았습니다.")
                await set_tracker_timer_running(nickname, True)
                await websocket.send_text(json.dumps({"type":"private_tracker_state", "trackers": state_for(nickname)["trackers"]}))
                await manager.broadcast_user_list()
                if client_id not in manager.active_slots:
                    manager.active_slots[client_id] = []
                log_entry = {"msg": f"{nickname} 님이 입장했습니다.", "time": __import__('time').time()}
                server_state.setdefault("admin_log", []).append(log_entry)
                if len(server_state["admin_log"]) > 100: server_state["admin_log"].pop(0)
                await persist_state()
                await send_admin_log(log_entry)
                if is_first_identity and not was_already_present and not resumed_during_grace:
                    await post_presence_chat(f"👋 «{nickname}» 작가님이 입장하셨습니다.")
                
                recovered = False
                
                if isinstance(owned, list):
                    for idx in owned:
                        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < 16:
                            card = server_state["cards"][idx]
                            current_user = card["user"]
                            reservation_owner = card.get("reserved_by")
                            if (not reservation_owner or reservation_owner == nickname) and (current_user.startswith("자리") or current_user == nickname):
                                if idx not in manager.active_slots[client_id]:
                                    manager.active_slots[client_id].append(idx)
                                card["user"] = nickname
                                recovered = True
                                change_packet = json.dumps({"type": "username_change", "index": idx, "user": nickname})
                                await manager.broadcast(change_packet)
                                await websocket.send_text(change_packet)
                
                for i, card in enumerate(server_state["cards"]):
                    if card.get("reserved_by") == nickname or card["user"] == nickname:
                        card["user"] = nickname
                        if i not in manager.active_slots[client_id]:
                            manager.active_slots[client_id].append(i)
                            recovered = True
                            change_packet = json.dumps({"type": "username_change", "index": i, "user": nickname})
                            await manager.broadcast(change_packet)
                            await websocket.send_text(change_packet)

                if recovered:
                    await persist_state()
                    continue

                assigned_idx = None
                for i, card in enumerate(server_state["cards"]):
                    if card["user"].startswith("자리") and not card.get("reserved_by"):
                        assigned_idx = i
                        break
                if assigned_idx is not None:
                    manager.active_slots[client_id].append(assigned_idx)
                    server_state["cards"][assigned_idx]["user"] = nickname
                    await persist_state()
                    change_packet = json.dumps({"type": "username_change", "index": assigned_idx, "user": nickname})
                    await manager.broadcast(change_packet)
                    await websocket.send_text(change_packet)
                continue

            if not authed or websocket not in manager.authed_connections:
                continue  # 인증 전이거나 강퇴·중복 접속으로 권한이 거둬진 연결
            if p_type not in CLIENT_TYPES:
                continue  # 서버 전용 type(auth_ok·kicked·init_state 등)이나 모르는 type은 받지도 중계하지도 않음
            nickname = manager.active_users.get(websocket, "")
            if p_type == "seat_reservation":
                try:
                    idx = int(packet.get("index"))
                except (TypeError, ValueError):
                    continue
                if not 0 <= idx < len(server_state["cards"]):
                    continue
                card = server_state["cards"][idx]
                wants_reservation = packet.get("reserved") is True
                reservation_owner = card.get("reserved_by")

                if wants_reservation:
                    owns_active_slot = idx in manager.active_slots.get(client_id, [])
                    if reservation_owner and reservation_owner != nickname:
                        await send_seat_error(websocket, idx, f"{reservation_owner} 작가님의 고정석입니다.")
                        continue
                    if card["user"] != nickname or not owns_active_slot:
                        await send_seat_error(websocket, idx, "자기 카드에서만 자리를 고정할 수 있습니다.")
                        continue
                    if not is_admin and any(i != idx and c.get("reserved_by") == nickname for i, c in enumerate(server_state["cards"])):
                        await send_seat_error(websocket, idx, "고정석은 한 사람에 하나만 둘 수 있습니다.")
                        continue
                    card["reserved_by"] = nickname
                    card["user"] = nickname
                else:
                    if reservation_owner != nickname and not is_admin:
                        await send_seat_error(websocket, idx, "본인의 고정석만 해제할 수 있습니다.")
                        continue
                    card["reserved_by"] = None

                await persist_state()
                await manager.broadcast(json.dumps({
                    "type": "seat_reservation_update",
                    "index": idx,
                    "user": card["user"],
                    "reserved_by": card.get("reserved_by")
                }))
                continue

            if p_type == "goal_celebration_chat":
                chat_obj = {
                    "id": uuid.uuid4().hex,
                    "senderName": "🎉시스템",
                    "msg": f"🎊 «{nickname}» 작가님이 오늘의 목표 분량을 모두 완료했습니다! 축하해주세요! 🎊",
                    "time": datetime.now(KST).strftime("%m/%d %H:%M")
                }
                server_state["chat_history"].append(chat_obj)
                if len(server_state["chat_history"]) > 100:
                    server_state["chat_history"].pop(0)
                await manager.broadcast(json.dumps({"type": "chat", **chat_obj}))
                await persist_state()
                continue

            if p_type == "timer_action":
                # 집필 시간은 접속 중 자동 기록됩니다. 구버전 화면의 수동 명령도
                # 기록을 멈추거나 초기화하지 않고 현재 접속 상태만 동기화합니다.
                await set_tracker_timer_running(nickname, True)
                continue

            if p_type == "delete_chat":
                message_id = packet.get("id")
                server_state["chat_history"] = [c for c in server_state["chat_history"] if not (c.get("id") == message_id and c.get("senderName") == nickname)]
                await persist_state()
                await manager.broadcast(json.dumps({"type": "chat_history", "messages": server_state["chat_history"]}))
                continue

            if p_type == "clear_chat":
                if not is_admin:
                    continue
                server_state["chat_history"] = []
                await persist_state()
                await manager.broadcast(json.dumps({"type": "chat_cleared"}))
                await websocket.send_text(json.dumps({"type": "chat_cleared"}))
                continue

            if p_type == "kick":
                if not is_admin:
                    continue
                target_nick = normalize_nickname(packet.get("target_nick"))
                if not target_nick or target_nick == ADMIN_NICKNAME:
                    continue
                kicked_until[target_nick] = time.time() + KICK_BLOCK_SECONDS
                for ws_conn, name in list(manager.active_users.items()):
                    if name == target_nick:
                        await revoke_connection(ws_conn, {"type": "kicked"})  # 인증·방장 권한·세션을 먼저 거두고 닫음
                continue

            if p_type == "issue_enrollment":
                # 방장: 이 이름의 열쇠를 지우고(잃어버린·빼앗긴 기기 포함) 24시간 일회용 등록 코드를 만듭니다.
                if not is_admin:
                    continue
                target_nick = normalize_nickname(packet.get("target_nick"))
                reply = {"type": "enrollment_issued", "target_nick": target_nick, "ok": False}
                if target_nick and target_nick != ADMIN_NICKNAME:
                    nid = name_id(target_nick)
                    async with identity_lock(target_nick):
                        # 메모리 열쇠부터 지우고 등록 대기로 표시합니다. DB 저장이 실패해도 이 이름은 닫힌 채로 남습니다.
                        server_state.setdefault("identity_keys", {}).pop(nid, None)
                        server_state.setdefault("identity_enroll", {})[nid] = {"h": "", "exp": 0}
                        for ws_conn, name in list(manager.active_users.items()):
                            if name == target_nick:
                                await revoke_connection(ws_conn, {"type": "identity_revoked"})
                        code = new_enroll_code()
                        expires = time.time() + ENROLL_TTL_SECONDS
                        try:
                            if await asyncio.to_thread(db_issue_enrollment, nid, identity_hash(code), expires):
                                server_state["identity_enroll"][nid] = {"h": identity_hash(code), "exp": expires}
                                reply.update(ok=True, code=code, expires=expires)
                                pending_revocations.pop(nid, None)
                        except Exception as exc:
                            print("등록 코드 저장 실패:", type(exc).__name__)
                        if not reply["ok"]:
                            # 메모리에서는 이미 막혔지만 DB에는 옛 열쇠가 남아 있습니다 → 저장될 때까지 다시 시도
                            pending_revocations[nid] = target_nick
                            reply["retrying"] = True
                            global revocation_task
                            if revocation_task is None or revocation_task.done():
                                revocation_task = asyncio.create_task(retry_revocations())
                    await add_admin_log(f"🔑 {target_nick} 이름 열쇠 해제·등록 코드 발급" if reply["ok"] else
                                        f"🔑 {target_nick} 열쇠는 지금 막았지만 DB 저장 실패 — 자동 재시도 중. '해제 저장됨' 기록 전에는 서버가 다시 켜지면 옛 열쇠가 살아날 수 있음")
                    if reply["ok"]:
                        await post_presence_chat(f"🔑 «{target_nick}» 작가님의 이름 열쇠를 방장이 다시 발급했습니다.")
                await websocket.send_text(json.dumps(reply))
                continue

            if p_type == "chat":
                chat_obj = {"id":uuid.uuid4().hex, "senderName":nickname, "msg":str(packet.get("msg", ""))[:4000], "time":datetime.now(KST).strftime("%m/%d %H:%M")}
                server_state["chat_history"].append(chat_obj)
                if len(server_state["chat_history"]) > 100: server_state["chat_history"].pop(0)
                await manager.broadcast(json.dumps({"type":"chat", **chat_obj}))
                await persist_state()
                
            elif p_type == "attendance":
                month = packet.get("month")
                day = packet.get("day")
                # 출석은 접속한 본인 이름으로만 찍습니다(요청 속 nickname 은 쓰지 않음).
                now_kst = datetime.now(KST)
                last_month = (now_kst.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
                if not isinstance(month, str) or month not in (now_kst.strftime("%Y-%m"), last_month):
                    continue  # 이번 달과 지난달만(출석 키가 끝없이 늘지 않게)
                if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
                    continue
                if "attendance" not in server_state: server_state["attendance"] = {}
                if month not in server_state["attendance"]: server_state["attendance"][month] = {}
                if nickname not in server_state["attendance"][month]: server_state["attendance"][month][nickname] = []
                if day not in server_state["attendance"][month][nickname]:
                    server_state["attendance"][month][nickname].append(day)
                await persist_state()
                await manager.broadcast(json.dumps({"type": "attendance_update", "attendance": server_state["attendance"]}))
                
            elif p_type == "tracker_update":
                current = normalize_tracker(nickname)
                incoming = packet.get("tracker_data", {})
                if not isinstance(incoming, dict): continue
                current.update(clean_tracker_fields(incoming))
                if incoming.get("lastDate") == current["lastDate"] and bounded_int(incoming.get("doneChars"), 10_000_000) is not None:
                    current["doneChars"] = bounded_int(incoming.get("doneChars"), 10_000_000)
                now = datetime.now(KST)
                timer = current["timer"]
                seconds = timer["seconds"] + (max(0, time.time() - timer["started"]) if timer.get("started") else 0)
                current.setdefault("calendar", {}).setdefault(now.strftime("%Y-%m"), {})[str(now.day)] = {"target": current.get("targetChars", 5000), "done": current.get("doneChars", 0), "seconds": int(seconds)}
                await persist_state()
                await publish_tracker(nickname)

            else:
                packet["sender"] = client_id
                if p_type == "update_notice" and not is_admin:
                    continue
                protected_card_types = {"username_change", "card_bg_change", "toggle_mosaic", "start_share", "stop_share", "status_update"}
                if p_type in protected_card_types | TARGETED_TYPES:
                    try:
                        scoped_idx = int(packet.get("index"))
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= scoped_idx < len(server_state["cards"]):
                        continue
                    packet["index"] = scoped_idx
                    reservation_owner = server_state["cards"][scoped_idx].get("reserved_by")
                    if p_type in TARGETED_TYPES:
                        pass  # 신호는 아래에서 공유자·상대 기준으로 확인
                    elif reservation_owner and reservation_owner != nickname:
                        await send_seat_error(websocket, scoped_idx, f"{reservation_owner} 작가님의 고정석이라 사용할 수 없습니다.")
                        continue

                sharer = manager.active_shares.get(packet.get("index")) if isinstance(packet.get("index"), int) else None
                if p_type in TARGETED_TYPES:
                    # 화면 공유 신호: 공유 중인 카드에 대해서만, 공유자와 시청자 사이에서만, 정해진 모양만 넘깁니다.
                    target = packet.get("target")
                    if not isinstance(target, str) or target == client_id or sharer is None:
                        continue
                    if p_type == "request_offer" and target != sharer:
                        continue
                    if p_type == "offer" and client_id != sharer:
                        continue
                    if p_type in ("answer", "ice") and sharer not in (client_id, target):
                        continue
                    clean = {"type": p_type, "index": packet["index"], "target": target, "sender": client_id}
                    if p_type in ("offer", "answer"):
                        sdp = packet.get("sdp")
                        if not (isinstance(sdp, dict) and sdp.get("type") == p_type and isinstance(sdp.get("sdp"), str) and len(sdp["sdp"]) <= 20000):
                            continue
                        clean["sdp"] = {"type": p_type, "sdp": sdp["sdp"]}
                    elif p_type == "ice":
                        cand = packet.get("candidate")
                        if not (isinstance(cand, dict) and len(json.dumps(cand)) <= 2000):
                            continue
                        clean["candidate"] = cand
                    packet = clean
                elif p_type in ("toggle_mosaic", "stop_share") and sharer != client_id and not is_admin:
                    continue  # 모자이크·공유 중지는 공유하는 본인이나 방장만(화면 규칙과 같게)
                elif p_type == "start_share" and (server_state["cards"][packet["index"]]["user"] != nickname or sharer not in (None, client_id)):
                    continue  # 자기 이름 카드에서, 남이 공유 중이 아닐 때만
                elif p_type == "username_change" and sharer not in (None, client_id) and not is_admin:
                    await send_seat_error(websocket, packet["index"], "다른 작가님이 화면 공유 중인 카드입니다.")
                    continue

                if p_type == "username_change":
                    idx = packet["index"]
                    val = packet.get("user")
                    if not isinstance(val, str) or not 1 <= len(val) <= 20:
                        continue
                    if sharer == client_id and val != nickname:
                        continue  # 화면 공유 중인 카드는 공유자 이름표만
                    if val != nickname and not re.fullmatch(r"자리\d{1,2}", val) and skeleton(val) in ({skeleton(n) for n in member_names() if n != nickname} | {skeleton(ADMIN_NICKNAME)}):
                        await send_seat_error(websocket, idx, "다른 작가님 이름으로는 카드 이름을 바꿀 수 없습니다.")
                        continue
                    reservation_owner = server_state["cards"][idx].get("reserved_by")
                    if reservation_owner == nickname and val != nickname:
                        await send_seat_error(websocket, idx, "고정을 해제한 뒤 이름을 바꿔주세요.")
                        continue
                    server_state["cards"][idx]["user"] = val
                    if not val.startswith("자리"):
                        if client_id not in manager.active_slots: manager.active_slots[client_id] = []
                        if idx not in manager.active_slots[client_id]: manager.active_slots[client_id].append(idx)
                    await persist_state()
                elif p_type == "card_bg_change":
                    if not is_safe_card_bg(packet.get("dataUrl")):
                        continue
                    server_state["cards"][packet["index"]]["card_bg"] = packet.get("dataUrl")
                    await persist_state()
                elif p_type == "toggle_mosaic":
                    packet["is_mosaic"] = packet.get("is_mosaic") is True
                    server_state["cards"][packet["index"]]["is_mosaic"] = packet["is_mosaic"]
                    await persist_state()
                elif p_type == "start_share":
                    manager.active_shares[packet["index"]] = client_id
                elif p_type == "stop_share":
                    idx = packet.get("index")
                    if idx in manager.active_shares: del manager.active_shares[idx]
                elif p_type == "update_notice":
                    notice = packet.get("notice", "")
                    server_state["global_notice"] = notice[:4000] if isinstance(notice, str) else ""
                    packet["notice"] = server_state["global_notice"]
                    await persist_state()
                elif p_type == "status_update":
                    idx = packet["index"]
                    card = server_state["cards"][idx]
                    if card.get("user") != nickname and not is_admin:
                        continue
                    raw_status = packet.get("status", "")
                    status = raw_status.strip()[:30] if isinstance(raw_status, str) else ""
                    card["status"] = status
                    packet["status"] = status
                    await persist_state()
                
                relay = json.dumps(packet)
                target = packet.get("target")
                if p_type in TARGETED_TYPES or (p_type == "start_share" and target is not None):
                    if isinstance(target, str):
                        await manager.send_to_client_id(target, relay)
                    continue
                await manager.broadcast(relay, exclude=websocket)

    except (WebSocketDisconnect, Exception):
        pass

    finally:
        client_id = str(id(websocket))
        admin_sockets.discard(websocket)
        nickname = manager.active_users.get(websocket, "")
        disconnected_at = time.time()
        
        if client_id in manager.active_slots:
            del manager.active_slots[client_id]
            await persist_state()

        freed_indexes = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        
        if nickname and nickname != "연결중...":
            log_entry = {"msg": f"{nickname} 님이 잠시 튕겼거나 퇴장했습니다.", "time": __import__('time').time()}
            server_state.setdefault("admin_log", []).append(log_entry)
            if len(server_state["admin_log"]) > 100: server_state["admin_log"].pop(0)
            await persist_state()
            await send_admin_log(log_entry)
            still_connected = any(name == nickname for name in manager.active_users.values())
            if not still_connected:
                old_pending_leave = pending_presence_leaves.pop(nickname, None)
                if old_pending_leave is not None:
                    old_pending_leave.cancel()
                pending_presence_leaves[nickname] = asyncio.create_task(post_leave_after_reconnect_grace(nickname, disconnected_at))
        
        for idx in freed_indexes:
            await manager.broadcast(json.dumps({"type": "stop_share", "index": idx}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
