#!/usr/bin/env python3
"""ShiftLedger — self-hosted employee scheduling and gross-pay estimation.

Python standard library only. SQLite database lives in DATA_DIR.
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_FILE = DATA_DIR / "shiftledger.sqlite3"
PORT = int(os.environ.get("PORT", "8080"))
SECURE_COOKIES = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 64 * 1024
LOGIN_GUARD = {}
LOGIN_LOCK = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    con = sqlite3.connect(DB_FILE, timeout=15, isolation_level="IMMEDIATE")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_password(password, stored):
    try:
        salt, value = stored.split(":", 1)
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split(":")[1], value)
    except (TypeError, ValueError):
        return False


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'owner',
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_stores (
            user_id INTEGER NOT NULL REFERENCES users(id),
            store_id INTEGER NOT NULL REFERENCES stores(id),
            PRIMARY KEY(user_id,store_id)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#3c8d82', active INTEGER NOT NULL DEFAULT 1,
            open_time TEXT NOT NULL DEFAULT '09:00', close_time TEXT NOT NULL DEFAULT '17:00',
            slot_minutes INTEGER NOT NULL DEFAULT 480, break_minutes INTEGER NOT NULL DEFAULT 30,
            staffing_json TEXT NOT NULL DEFAULT '[1,1,1,1,1,1,1]',
            shift_templates_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
            rate_cents INTEGER NOT NULL CHECK(rate_cents >= 0),
            max_days INTEGER NOT NULL DEFAULT 5, availability_json TEXT NOT NULL DEFAULT '[1,1,1,1,1,1,1]',
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employee_stores (
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            store_id INTEGER NOT NULL REFERENCES stores(id),
            rate_cents INTEGER CHECK(rate_cents >= 0),
            PRIMARY KEY(employee_id, store_id)
        );
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
            store_id INTEGER NOT NULL REFERENCES stores(id), shift_date TEXT NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT NOT NULL,
            break_minutes INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft', generated INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS shifts_date_idx ON shifts(shift_date, store_id);
        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
            store_id INTEGER NOT NULL REFERENCES stores(id), entry_date TEXT NOT NULL,
            category TEXT NOT NULL, start_time TEXT, end_time TEXT,
            break_minutes INTEGER NOT NULL DEFAULT 0,
            minutes INTEGER NOT NULL CHECK(minutes > 0),
            rate_cents INTEGER NOT NULL CHECK(rate_cents >= 0),
            notes TEXT NOT NULL DEFAULT '', shift_id INTEGER UNIQUE REFERENCES shifts(id),
            deleted_at TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS entries_date_idx ON time_entries(entry_date, employee_id);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL, action TEXT NOT NULL,
            entity TEXT NOT NULL, entity_id INTEGER, details TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        columns = [row[1] for row in con.execute("PRAGMA table_info(users)")]
        if "role" not in columns:
            con.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'owner'")
        if "active" not in columns:
            con.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        store_columns = [row[1] for row in con.execute("PRAGMA table_info(employee_stores)")]
        if "rate_cents" not in store_columns:
            con.execute("ALTER TABLE employee_stores ADD COLUMN rate_cents INTEGER")
        employee_columns = [row[1] for row in con.execute("PRAGMA table_info(employees)")]
        if "max_days" not in employee_columns:
            con.execute("ALTER TABLE employees ADD COLUMN max_days INTEGER NOT NULL DEFAULT 5")
        if "availability_json" not in employee_columns:
            con.execute("ALTER TABLE employees ADD COLUMN availability_json TEXT NOT NULL DEFAULT '[1,1,1,1,1,1,1]'")
        store_columns = [row[1] for row in con.execute("PRAGMA table_info(stores)")]
        for column, ddl in (
            ("open_time", "TEXT NOT NULL DEFAULT '09:00'"),
            ("close_time", "TEXT NOT NULL DEFAULT '17:00'"),
            ("slot_minutes", "INTEGER NOT NULL DEFAULT 480"),
            ("break_minutes", "INTEGER NOT NULL DEFAULT 30"),
            ("staffing_json", "TEXT NOT NULL DEFAULT '[1,1,1,1,1,1,1]'"),
            ("shift_templates_json", "TEXT"),
        ):
            if column not in store_columns:
                con.execute(f"ALTER TABLE stores ADD COLUMN {column} {ddl}")
        if "generated" not in [row[1] for row in con.execute("PRAGMA table_info(shifts)")]:
            con.execute("ALTER TABLE shifts ADD COLUMN generated INTEGER NOT NULL DEFAULT 0")
        if "template_key" not in [row[1] for row in con.execute("PRAGMA table_info(shifts)")]:
            con.execute("ALTER TABLE shifts ADD COLUMN template_key TEXT")
        existing = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if not existing:
            username = os.environ.get("ADMIN_USERNAME", "admin").strip()
            password = os.environ.get("ADMIN_PASSWORD", "")
            if not username or not password:
                raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD before first start")
            con.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                        (username, hash_password(password), utc_now()))
            con.execute("INSERT INTO stores(name,location,color,created_at) VALUES(?,?,?,?)",
                        ("Main Store", "", "#3c8d82", utc_now()))


class InputError(Exception):
    pass


def require_string(value, label, length=120, optional=False):
    if not isinstance(value, str):
        raise InputError(f"{label} must be text")
    result = value.strip()
    if (not result and not optional) or len(result) > length:
        raise InputError(f"{label} must be {'0' if optional else '1'}–{length} characters")
    return result


def positive_id(value, label):
    if isinstance(value, bool):
        raise InputError(f"Invalid {label}")
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise InputError(f"Invalid {label}")
    if result <= 0:
        raise InputError(f"Invalid {label}")
    return result


def boolean(value):
    if type(value) is not bool:
        raise InputError("Expected true or false")
    return int(value)


def cents(value, label="Hourly rate"):
    try:
        dec = Decimal(str(value))
        if not dec.is_finite() or dec < 0 or dec > 100000:
            raise InputError(f"Invalid {label}")
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise InputError(f"Invalid {label}")


def as_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise InputError("Invalid date")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except ValueError:
        raise InputError("Invalid date")


def as_time(value):
    if not isinstance(value, str) or len(value) != 5:
        raise InputError("Invalid time")
    try:
        d = datetime.strptime(value, "%H:%M")
        if d.strftime("%H:%M") != value:
            raise ValueError()
        return d.hour * 60 + d.minute
    except ValueError:
        raise InputError("Invalid time")


def integer_range(value, label, lower=0, upper=1440):
    if isinstance(value, bool):
        raise InputError(f"Invalid {label}")
    try:
        number = int(value)
        if number != float(value) or number < lower or number > upper:
            raise ValueError()
        return number
    except (ValueError, TypeError, OverflowError):
        raise InputError(f"Invalid {label}")


def shift_minutes(start, end, pause):
    st, en = as_time(start), as_time(end)
    total = (en - st) % 1440
    if total == 0 or pause >= total:
        raise InputError("Shift duration must be positive and under 24 hours")
    return total - pause


def ensure_record(con, table, record_id, active=False):
    if table not in ("stores", "employees", "shifts", "time_entries"):
        raise InputError("Invalid record type")
    suffix = " AND active=1" if active else (" AND deleted_at IS NULL" if table in ("shifts", "time_entries") else "")
    found = con.execute(f"SELECT * FROM {table} WHERE id=?" + suffix, (record_id,)).fetchone()
    if not found:
        raise InputError(f"{table[:-1].capitalize()} not found or inactive")
    return dict(found)


def ensure_employee_store(con, employee_id, store_id):
    ensure_record(con, "employees", employee_id, True)
    ensure_record(con, "stores", store_id, True)
    if not con.execute("SELECT 1 FROM employee_stores WHERE employee_id=? AND store_id=?", (employee_id, store_id)).fetchone():
        raise InputError("Employee is not assigned to this store")


def availability_payload(value):
    if not isinstance(value, list) or any(type(w) is not int or w not in range(7) for w in value):
        raise InputError("Available weekdays must be selected from Monday through Sunday")
    return json.dumps([int(day in value) for day in range(7)])


def can_manage_employee(con, user, employee_id):
    if user["role"] == "owner":
        return True
    return bool(con.execute("SELECT 1 FROM employee_stores WHERE employee_id=? AND store_id IN ("+
                            ",".join("?" for _ in user["store_ids"])+")",
                            (employee_id, *user["store_ids"])).fetchone()) if user["store_ids"] else False


def validate_employee_shift(con, employee_id, day, start, end, skip_id=None):
    emp = ensure_record(con, "employees", employee_id, True)
    weekday = as_date(day).weekday()
    if not json.loads(emp["availability_json"])[weekday]:
        raise InputError(f"Employee is unavailable on {as_date(day).strftime('%A')}")
    previous = rows(con, """SELECT id,shift_date,start_time,end_time FROM shifts
                   WHERE employee_id=? AND deleted_at IS NULL AND shift_date BETWEEN ? AND ?""",
                    (employee_id, (week_monday(as_date(day))-timedelta(days=1)).isoformat(),
                     (week_monday(as_date(day))+timedelta(days=6)).isoformat()))
    previous = [shift for shift in previous if shift["id"] != skip_id]
    week_begin = week_monday(as_date(day)).isoformat()
    days = {shift["shift_date"] for shift in previous if shift["shift_date"] >= week_begin}
    if day not in days and len(days) >= emp["max_days"]:
        raise InputError(f"Employee reached their maximum of {emp['max_days']} days this week")
    date_base = as_date(day).toordinal()*1440
    a1 = date_base+as_time(start)
    a2 = date_base+as_time(end)+(1440 if as_time(end) <= as_time(start) else 0)
    # Include the following day even when the new shift starts on Sunday: a
    # Sunday overnight shift can overlap a Monday shift in the next week.
    overlaps = rows(con, """SELECT id,shift_date,start_time,end_time FROM shifts
                   WHERE employee_id=? AND deleted_at IS NULL AND shift_date BETWEEN ? AND ?""",
                    (employee_id, (as_date(day)-timedelta(days=1)).isoformat(),
                     (as_date(day)+timedelta(days=1)).isoformat()))
    for shift in overlaps:
        if shift["id"] == skip_id:
            continue
        prior_base = as_date(shift["shift_date"]).toordinal()*1440
        b1 = prior_base+as_time(shift["start_time"])
        b2 = prior_base+as_time(shift["end_time"])+(1440 if as_time(shift["end_time"]) <= as_time(shift["start_time"]) else 0)
        if a1 < b2 and b1 < a2:
            raise InputError("Employee already has an overlapping shift, possibly at another store")


def audit(con, user, action, entity, record_id, detail):
    con.execute("INSERT INTO audit_log(username,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (user, action, entity, record_id, json.dumps(detail, sort_keys=True), utc_now()))


def rows(con, query, args=()):
    return [dict(row) for row in con.execute(query, args).fetchall()]


def payload_for_shift(body):
    d = as_date(body.get("date"))
    start = body.get("start_time")
    end = body.get("end_time")
    pause = integer_range(body.get("break_minutes", 0), "Break", 0, 600)
    shift_minutes(start, end, pause)
    return (positive_id(body.get("employee_id"), "employee"), positive_id(body.get("store_id"), "store"),
            d.isoformat(), start, end, pause, require_string(body.get("notes", ""), "Notes", 500, True),
            body.get("status", "draft"))


def entry_payload(body, existing=None):
    d = as_date(body.get("date")).isoformat()
    category = require_string(body.get("category", "Shift"), "Category", 60)
    start = body.get("start_time") or None
    end = body.get("end_time") or None
    pause = integer_range(body.get("break_minutes", 0), "Break", 0, 600)
    if start or end:
        if not (start and end):
            raise InputError("Provide both start and end times")
        minutes = shift_minutes(start, end, pause)
    else:
        if pause:
            raise InputError("Break is only available for timed entries")
        try:
            hrs = Decimal(str(body.get("hours")))
            if not hrs.is_finite() or hrs <= 0 or hrs > 23.99:
                raise ValueError()
            minutes = int((hrs * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError, ValueError):
            raise InputError("Hours must be between 0.01 and 23.99")
        if minutes < 1:
            raise InputError("Hours must be at least one minute")
    rate = existing["rate_cents"] if existing else None
    if "rate" in body and body["rate"] not in (None, ""):
        rate = cents(body["rate"])
    return (positive_id(body.get("employee_id"), "employee"), positive_id(body.get("store_id"), "store"),
            d, category, start, end, pause, minutes, rate, require_string(body.get("notes", ""), "Notes", 500, True))


def list_data(user):
    with db() as con:
        if user["role"] == "owner":
            return {
                "stores": rows(con, "SELECT * FROM stores ORDER BY active DESC, name"),
                "employees": rows(con, "SELECT * FROM employees ORDER BY active DESC, name"),
                "employee_stores": rows(con, "SELECT * FROM employee_stores ORDER BY employee_id,store_id"),
                "shifts": rows(con, "SELECT * FROM shifts WHERE deleted_at IS NULL ORDER BY shift_date,start_time,id"),
                "entries": rows(con, "SELECT * FROM time_entries WHERE deleted_at IS NULL ORDER BY entry_date DESC,id DESC"),
            }
        allowed = user["store_ids"]
        if not allowed:
            return {"stores": [], "employees": [], "employee_stores": [], "shifts": [], "entries": []}
        placeholders = ",".join("?" for _ in allowed)
        employees = rows(con, f"""SELECT DISTINCT e.id,e.name,e.role,e.rate_cents,e.max_days,e.availability_json,e.active,e.created_at FROM employees e
                         JOIN employee_stores es ON es.employee_id=e.id WHERE es.store_id IN ({placeholders})
                         ORDER BY e.active DESC,e.name""", allowed)
        return {
            "stores": rows(con, f"SELECT * FROM stores WHERE id IN ({placeholders}) ORDER BY active DESC,name", allowed),
            "employees": employees,
            "employee_stores": rows(con, f"""SELECT es.employee_id,es.store_id,COALESCE(es.rate_cents,e.rate_cents) AS rate_cents
                           FROM employee_stores es JOIN employees e ON e.id=es.employee_id
                           WHERE es.store_id IN ({placeholders})""", allowed),
            "shifts": rows(con, f"SELECT * FROM shifts WHERE deleted_at IS NULL AND store_id IN ({placeholders}) ORDER BY shift_date,start_time,id", allowed),
            "entries": rows(con, f"""SELECT id,employee_id,store_id,entry_date,category,start_time,end_time,break_minutes,
                         minutes,notes,shift_id,created_at FROM time_entries WHERE deleted_at IS NULL
                         AND store_id IN ({placeholders}) ORDER BY entry_date DESC,id DESC""", allowed),
        }


def authorize_store(user, store_id):
    if user["role"] != "owner" and store_id not in user["store_ids"]:
        raise InputError("You do not manage this store")


def owner_required(user):
    if user["role"] != "owner":
        raise PermissionError("Owner access required")


def store_rate(con, employee_id, store_id):
    item = con.execute("""SELECT COALESCE(es.rate_cents,e.rate_cents) AS rate FROM employee_stores es
                          JOIN employees e ON e.id=es.employee_id WHERE es.employee_id=? AND es.store_id=?""",
                       (employee_id, store_id)).fetchone()
    if not item:
        raise InputError("Employee is not assigned to the store")
    return item["rate"]


def week_monday(d):
    return d - timedelta(days=d.weekday())


def staffing_settings(body):
    opening = require_string(body.get("open_time", ""), "Opening time", 5)
    closing = require_string(body.get("close_time", ""), "Closing time", 5)
    open_min, close_min = as_time(opening), as_time(closing)
    if close_min == open_min:
        raise InputError("Store opening and closing times cannot be the same")
    span = (close_min - open_min) % 1440
    duration = integer_range(body.get("slot_minutes"), "Shift length", 30, 1380)
    pause = integer_range(body.get("break_minutes"), "Unpaid break", 0, 600)
    if duration > span or pause >= duration:
        raise InputError("Shift length must fit store hours and exceed the unpaid break")
    staffing = body.get("staffing")
    if not isinstance(staffing, list) or len(staffing) != 7:
        raise InputError("Set staffing counts for all seven weekdays")
    staffing = [integer_range(count, "Daily staffing count", 0, 20) for count in staffing]
    templates = body.get("templates")
    if templates is not None:
        templates = validate_shift_templates(templates, opening, closing)
        staffing = [sum(block["staffing"][d] for block in templates) for d in range(7)]
    return opening, closing, duration, pause, json.dumps(staffing), (json.dumps(templates) if templates is not None else None)


def anchored_shift_range(opening, start, end):
    """Shift times on the store's operating day; after-midnight is day + 1."""
    opening_minute = as_time(opening)
    first = as_time(start)
    last = as_time(end)
    first += 1440 if first < opening_minute else 0
    last += 1440 if last < opening_minute or last <= as_time(start) else 0
    return first, last


def validate_shift_templates(value, opening, closing):
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise InputError("Add between 1 and 12 shift types")
    close_abs = as_time(opening) + (as_time(closing)-as_time(opening)) % 1440
    normalized, seen = [], set()
    for block in value:
        if not isinstance(block, dict):
            raise InputError("Invalid shift type")
        key = require_string(block.get("id", ""), "Shift type ID", 40)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in seen:
            raise InputError("Every shift type needs a unique ID")
        seen.add(key)
        name = require_string(block.get("name", ""), "Shift name", 60)
        st = require_string(block.get("start_time", ""), "Shift start", 5)
        en = require_string(block.get("end_time", ""), "Shift end", 5)
        pause = integer_range(block.get("break_minutes", 0), "Unpaid break", 0, 600)
        shift_minutes(st, en, pause)
        a, b = anchored_shift_range(opening, st, en)
        if a < as_time(opening) or b > close_abs or b <= a:
            raise InputError(f"{name} must fit within store hours ({opening}–{closing}, closing may be next day)")
        counts = block.get("staffing")
        if not isinstance(counts, list) or len(counts) != 7:
            raise InputError(f"Set all seven daily staffing counts for {name}")
        counts = [integer_range(c, f"{name} employee count", 0, 20) for c in counts]
        normalized.append({"id": key, "name": name, "start_time": st, "end_time": en,
                           "break_minutes": pause, "staffing": counts})
    if any(sum(block["staffing"][d] for block in normalized) > 100 for d in range(7)):
        raise InputError("Maximum 100 total shift assignments per day")
    return normalized


def coverage_count(store, shifts, day_index):
    """Count assigned shifts against named shift types, preserving manager edits."""
    templates = json.loads(store.get("shift_templates_json") or "null")
    if not templates:
        return min(len(shifts), json.loads(store["staffing_json"])[day_index])
    capacity = {block["id"]: block["staffing"][day_index] for block in templates}
    covered = {block["id"]: 0 for block in templates}
    pending = []
    for sh in shifts:
        key = sh.get("template_key")
        if key in covered:
            covered[key] += 1
        else:
            pending.append(sh)
    # Older/manual shifts have no template ID. Match to the block with most
    # clock-time overlap (not merely any shift on the same calendar date).
    for sh in pending:
        a, b = anchored_shift_range(store["open_time"], sh["start_time"], sh["end_time"])
        matches = []
        for block in templates:
            if covered[block["id"]] >= capacity[block["id"]]:
                continue
            x, y = anchored_shift_range(store["open_time"], block["start_time"], block["end_time"])
            overlap = max(0, min(b, y)-max(a, x))
            if overlap:
                matches.append((overlap, block["id"]))
        if matches:
            _, key = max(matches)
            covered[key] += 1
    return sum(min(covered[key], capacity[key]) for key in capacity)


def schedule_estimate(user, week_start, store_filter=None):
    monday_date = as_date(week_start)
    if monday_date.weekday() != 0:
        raise InputError("Week start must be a Monday")
    last = (monday_date + timedelta(days=6)).isoformat()
    start = monday_date.isoformat()
    if store_filter:
        authorize_store(user, store_filter)
    allowed = user["store_ids"] if user["role"] != "owner" else None
    with db() as con:
        shifts = rows(con, """SELECT sh.*,COALESCE(es.rate_cents,e.rate_cents) AS hourly_rate_cents
            FROM shifts sh JOIN employees e ON e.id=sh.employee_id
            LEFT JOIN employee_stores es ON es.employee_id=sh.employee_id AND es.store_id=sh.store_id
            WHERE sh.deleted_at IS NULL AND sh.shift_date BETWEEN ? AND ?""", (start, last))
        stores = rows(con, "SELECT id,staffing_json,shift_templates_json,open_time FROM stores WHERE active=1")
    if allowed is not None:
        shifts = [s for s in shifts if s["store_id"] in allowed]
        stores = [s for s in stores if s["id"] in allowed]
    if store_filter:
        shifts = [s for s in shifts if s["store_id"] == store_filter]
        stores = [s for s in stores if s["id"] == store_filter]
    minutes = sum(shift_minutes(s["start_time"], s["end_time"], s["break_minutes"]) for s in shifts)
    wages = sum(money_round(Decimal(shift_minutes(s["start_time"], s["end_time"], s["break_minutes"]))
                            * Decimal(s["hourly_rate_cents"]) / Decimal(60)) for s in shifts)
    desired = sum(sum(json.loads(s["staffing_json"])) for s in stores)
    filled = 0
    for store in stores:
        for day_idx in range(7):
            day = (monday_date + timedelta(days=day_idx)).isoformat()
            day_shifts = [sh for sh in shifts if sh["store_id"] == store["id"] and sh["shift_date"] == day]
            filled += coverage_count(store, day_shifts, day_idx)
    return {"week_start": start, "week_end": last, "store_id": store_filter,
            "shift_count": len(shifts), "planned_minutes": minutes, "estimated_base_cents": wages,
            "staffing_target": desired, "unfilled_slots": max(0, desired-filled),
            "draft_count": sum(s["status"] == "draft" for s in shifts)}


def generate_week(user, store_id, week_start):
    start = as_date(week_start)
    if start.weekday() != 0:
        raise InputError("Week start must be a Monday")
    authorize_store(user, store_id)
    week_end = (start + timedelta(days=6)).isoformat()
    with db() as con:
        with con:
            store = ensure_record(con, "stores", store_id, True)
            needs = json.loads(store["staffing_json"])
            employees = rows(con, """SELECT e.id,e.name,e.max_days,e.availability_json FROM employees e JOIN employee_stores es
                                     ON es.employee_id=e.id WHERE es.store_id=? AND e.active=1
                                     ORDER BY e.id""", (store_id,))
            existing = rows(con, """SELECT employee_id,store_id,shift_date,start_time,end_time,break_minutes,template_key
                                    FROM shifts WHERE deleted_at IS NULL AND shift_date BETWEEN ? AND ?""",
                            (start.isoformat(), week_end))
            total_mins = {}
            day_people = {}
            day_store_count = {}
            for sh in existing:
                emp = sh["employee_id"]
                total_mins[emp] = total_mins.get(emp, 0) + shift_minutes(sh["start_time"], sh["end_time"], sh["break_minutes"])
                day_people.setdefault(sh["shift_date"], set()).add(emp)
                if sh["store_id"] == store_id:
                    day_store_count[sh["shift_date"]] = day_store_count.get(sh["shift_date"], 0) + 1
            templates = json.loads(store["shift_templates_json"] or "null")
            open_min, close_min = as_time(store["open_time"]), as_time(store["close_time"])
            span = (close_min - open_min) % 1440
            duration = min(store["slot_minutes"], span)
            pause = store["break_minutes"]
            if span <= 0 or duration <= pause:
                raise InputError("Update store staffing hours before generating the schedule")
            paid_mins = duration - pause
            created, unfilled = [], []
            for weekday in range(7):
                day = (start + timedelta(days=weekday)).isoformat()
                if templates:
                    store_shifts = [sh for sh in existing if sh["store_id"] == store_id and sh["shift_date"] == day]
                    # Account for manually assigned people and already-generated
                    # shifts separately in each day/evening coverage block.
                    counted = {block["id"]: 0 for block in templates}
                    for sh in store_shifts:
                        if sh.get("template_key") in counted:
                            counted[sh["template_key"]] += 1
                    for sh in store_shifts:
                        if sh.get("template_key") in counted:
                            continue
                        a, b = anchored_shift_range(store["open_time"], sh["start_time"], sh["end_time"])
                        matches = []
                        for block in templates:
                            if counted[block["id"]] >= block["staffing"][weekday]:
                                continue
                            x, y = anchored_shift_range(store["open_time"], block["start_time"], block["end_time"])
                            overlap = max(0, min(b, y) - max(a, x))
                            if overlap:
                                matches.append((overlap, block["id"]))
                        if matches:
                            counted[max(matches)[1]] += 1
                    busy = day_people.setdefault(day, set())
                    for block in templates:
                        missing = max(0, block["staffing"][weekday] - counted[block["id"]])
                        block_mins = shift_minutes(block["start_time"], block["end_time"], block["break_minutes"])
                        for slot in range(missing):
                            eligible = []
                            for candidate in employees:
                                if candidate["id"] in busy or total_mins.get(candidate["id"], 0) + block_mins > 2400:
                                    continue
                                try:
                                    validate_employee_shift(con, candidate["id"], day, block["start_time"], block["end_time"])
                                except InputError:
                                    continue
                                eligible.append(candidate)
                            if not eligible:
                                unfilled.append({"date": day, "shift": block["name"], "remaining": missing - slot})
                                break
                            emp = min(eligible, key=lambda item: (total_mins.get(item["id"], 0), item["id"]))
                            rid = con.execute("""INSERT INTO shifts(employee_id,store_id,shift_date,start_time,end_time,
                                break_minutes,notes,status,generated,template_key,created_at)
                                VALUES(?,?,?,?,?,?,?,'draft',1,?,?)""",
                                (emp["id"], store_id, day, block["start_time"], block["end_time"],
                                 block["break_minutes"], "Auto-generated: " + block["name"] + "; review before publishing",
                                 block["id"], utc_now())).lastrowid
                            total_mins[emp["id"]] = total_mins.get(emp["id"], 0) + block_mins
                            busy.add(emp["id"])
                            created.append(rid)
                    continue
                needed = max(0, needs[weekday] - day_store_count.get(day, 0))
                busy = day_people.setdefault(day, set())
                for slot in range(needed):
                    # Stagger shifts across the operating hours when multiple people are needed.
                    offset = 0 if needs[weekday] <= 1 else round((span-duration) *
                                     (day_store_count.get(day, 0) + slot) / max(1, needs[weekday]-1))
                    start_min = open_min + offset
                    start_time = f"{(start_min//60)%24:02d}:{start_min%60:02d}"
                    end_min = start_min + duration
                    end_time = f"{(end_min//60)%24:02d}:{end_min%60:02d}"
                    eligible = []
                    for candidate in employees:
                        if candidate["id"] in busy or total_mins.get(candidate["id"], 0) + paid_mins > 2400:
                            continue
                        try:
                            validate_employee_shift(con, candidate["id"], day, start_time, end_time)
                        except InputError:
                            continue
                        eligible.append(candidate)
                    if not eligible:
                        unfilled.append({"date": day, "remaining": needed - slot})
                        break
                    emp = min(eligible, key=lambda item: (total_mins.get(item["id"], 0), item["id"]))
                    rid = con.execute("""INSERT INTO shifts(employee_id,store_id,shift_date,start_time,end_time,
                        break_minutes,notes,status,generated,created_at) VALUES(?,?,?,?,?,?,'Auto-generated; review before publishing','draft',1,?)""",
                        (emp["id"], store_id, day, start_time, end_time, pause, utc_now())).lastrowid
                    total_mins[emp["id"]] = total_mins.get(emp["id"], 0) + paid_mins
                    busy.add(emp["id"])
                    created.append(rid)
            audit(con, user["username"], "generate_week", "store", store_id,
                  {"week_start": start.isoformat(), "created_shift_ids": created, "unfilled": unfilled})
    return {"created": len(created), "unfilled": unfilled, "week_start": start.isoformat()}


def money_round(num):
    return int(Decimal(num).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_payroll(start, end, store_filter=None):
    first, last = as_date(start), as_date(end)
    if first > last or (last - first).days > 366:
        raise InputError("Pay range must be 1–367 days")
    week_start = week_monday(first)
    week_end = week_monday(last) + timedelta(days=6)
    with db() as con:
        all_entries = rows(con, """SELECT te.*, e.name AS employee_name, s.name AS store_name
          FROM time_entries te JOIN employees e ON e.id=te.employee_id
          JOIN stores s ON s.id=te.store_id
          WHERE te.deleted_at IS NULL AND te.entry_date BETWEEN ? AND ?
          ORDER BY te.employee_id,te.entry_date,COALESCE(te.start_time,'12:00'),te.id""",
                           (week_start.isoformat(), week_end.isoformat()))
    weeks = {}
    for item in all_entries:
        key = (item["employee_id"], week_monday(as_date(item["entry_date"])).isoformat())
        weeks.setdefault(key, []).append(item)
    totals = {}
    for (employee_id, _), entries in weeks.items():
        weekly_minutes = sum(x["minutes"] for x in entries)
        weekly_base = sum(Decimal(x["minutes"] * x["rate_cents"]) / 60 for x in entries)
        weighted_cents_hour = (weekly_base * 60 / weekly_minutes) if weekly_minutes else Decimal(0)
        elapsed = 0
        for x in entries:
            after = elapsed + x["minutes"]
            ot = max(0, after - 2400) - max(0, elapsed - 2400)
            elapsed = after
            if not (start <= x["entry_date"] <= end) or (store_filter and x["store_id"] != store_filter):
                continue
            pay = Decimal(x["minutes"] * x["rate_cents"]) / 60
            premium = weighted_cents_hour * Decimal(ot) / 120
            key = employee_id
            t = totals.setdefault(key, {"employee_id": employee_id, "name": x["employee_name"],
                                        "minutes": 0, "overtime_minutes": 0, "base_cents": 0,
                                        "premium_cents": 0, "gross_cents": 0, "entry_count": 0,
                                        "categories": {}, "stores": {}})
            t["minutes"] += x["minutes"]
            t["overtime_minutes"] += ot
            t["base_cents"] += money_round(pay)
            t["premium_cents"] += money_round(premium)
            t["entry_count"] += 1
            t["categories"][x["category"]] = t["categories"].get(x["category"], 0) + x["minutes"]
            t["stores"][x["store_name"]] = t["stores"].get(x["store_name"], 0) + x["minutes"]
    output = sorted(totals.values(), key=lambda x: x["name"].casefold())
    for entry in output:
        entry["gross_cents"] = entry["base_cents"] + entry["premium_cents"]
    return {"start": start, "end": end, "store_id": store_filter, "employees": output,
            "total_minutes": sum(x["minutes"] for x in output),
            "total_overtime_minutes": sum(x["overtime_minutes"] for x in output),
            "total_gross_cents": sum(x["gross_cents"] for x in output),
            "disclaimer": "Gross-pay estimate only; no taxes, benefits, tips, bonuses, state-specific overtime or deductions. Review before payroll."}


ASSET_VERSIONS = {}


def asset_version(name):
    if name not in ASSET_VERSIONS:
        ASSET_VERSIONS[name] = hashlib.sha256((APP_DIR / "static" / name).read_bytes()).hexdigest()[:12].encode()
    return ASSET_VERSIONS[name]


class Handler(BaseHTTPRequestHandler):
    server_version = "StaffWeek-ShiftLedger/1.3"

    def log_message(self, fmt, *args):
        print("[%s] %s %s" % (utc_now(), self.client_address[0], fmt % args), flush=True)

    def send_bytes(self, status, raw, content_type="application/json; charset=utf-8", cookie=None, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if headers:
            for key, val in headers.items():
                self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def json(self, status, data, cookie=None):
        self.send_bytes(status, json.dumps(data, separators=(",", ":")).encode(), cookie=cookie)

    def read_json(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise InputError("Expected application/json")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise InputError("Invalid request length")
        if size < 1 or size > MAX_BODY:
            raise InputError("Request body missing or too large")
        try:
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict):
                raise ValueError()
            return obj
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            raise InputError("Invalid JSON object")

    def current_user(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["shiftledger_session"].value
        except (KeyError, ValueError):
            return None
        if len(token) != 64:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db() as con:
            row = con.execute("""SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id
                                 WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""", (digest, int(time.time()))).fetchone()
            if not row:
                return None
            user = dict(row)
            user["store_ids"] = [r[0] for r in con.execute("SELECT store_id FROM user_stores WHERE user_id=?", (user["id"],))]
        return user

    def cookie_header(self, token, age=SESSION_TTL):
        secure = "; Secure" if SECURE_COOKIES else ""
        return f"shiftledger_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin == "http://" + host or origin == "https://" + host

    def api(self, method):
        parsed = urlsplit(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        try:
            if path == "/api/health" and method == "GET":
                return self.json(200, {"ok": True, "version": "1.3.0"})
            if method != "GET" and not self.same_origin():
                return self.json(403, {"error": "Cross-origin request rejected"})
            if path == "/api/login" and method == "POST":
                body = self.read_json()
                username = require_string(body.get("username", ""), "Username", 80)
                password = body.get("password", "")
                if not isinstance(password, str):
                    raise InputError("Invalid credentials")
                remote = self.client_address[0]
                now = time.monotonic()
                with LOGIN_LOCK:
                    recent = [t for t in LOGIN_GUARD.get(remote, []) if now - t < 300]
                    if len(recent) >= 10:
                        return self.json(429, {"error": "Too many sign-in attempts. Try again in five minutes."})
                with db() as con:
                    row = con.execute("SELECT id,password_hash,role FROM users WHERE username=? AND active=1", (username,)).fetchone()
                    if not row or not check_password(password, row["password_hash"]):
                        with LOGIN_LOCK:
                            LOGIN_GUARD[remote] = recent + [now]
                        return self.json(401, {"error": "Incorrect username or password"})
                    with LOGIN_LOCK:
                        LOGIN_GUARD.pop(remote, None)
                    token, csrf = secrets.token_hex(32), secrets.token_urlsafe(32)
                    con.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
                    con.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)",
                                (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, int(time.time()) + SESSION_TTL))
                return self.json(200, {"username": username, "role": row["role"], "csrf": csrf}, self.cookie_header(token))
            user = self.current_user()
            if path == "/api/session" and method == "GET":
                return self.json(200, {"authenticated": bool(user), **(user or {})})
            if not user:
                return self.json(401, {"error": "Sign in required"})
            if method != "GET" and self.headers.get("X-CSRF-Token") != user["csrf"]:
                return self.json(403, {"error": "Security token expired. Refresh the page."})
            if path == "/api/logout" and method == "POST":
                cookie = SimpleCookie()
                cookie.load(self.headers.get("Cookie", ""))
                token = cookie.get("shiftledger_session")
                if token:
                    with db() as con:
                        con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.value.encode()).hexdigest(),))
                return self.json(200, {"ok": True}, self.cookie_header("", 0))
            if path == "/api/data" and method == "GET":
                return self.json(200, list_data(user))
            if path == "/api/schedule/estimate" and method == "GET":
                store = positive_id(params["store_id"][0], "store") if params.get("store_id") else None
                return self.json(200, schedule_estimate(user, params.get("week_start", [""])[0], store))
            if path == "/api/schedule/generate-week" and method == "POST":
                body = self.read_json()
                store = positive_id(body.get("store_id"), "store")
                return self.json(200, generate_week(user, store, body.get("week_start")))
            if path == "/api/store-staffing" and method == "POST":
                body = self.read_json()
                store = positive_id(body.get("store_id"), "store")
                authorize_store(user, store)
                opening, closing, duration, pause, staffing, templates = staffing_settings(body)
                with db() as con:
                    with con:
                        ensure_record(con, "stores", store, True)
                        con.execute("""UPDATE stores SET open_time=?,close_time=?,slot_minutes=?,
                                      break_minutes=?,staffing_json=?,shift_templates_json=? WHERE id=?""",
                                    (opening, closing, duration, pause, staffing, templates, store))
                        audit(con, user["username"], "set_staffing", "store", store, body)
                return self.json(200, {"ok": True})
            if path == "/api/payroll" and method == "GET":
                owner_required(user)
                store = positive_id(params["store_id"][0], "store") if params.get("store_id") and params["store_id"][0] else None
                return self.json(200, calculate_payroll(params.get("start", [""])[0], params.get("end", [""])[0], store))
            if path == "/api/payroll.csv" and method == "GET":
                owner_required(user)
                store = positive_id(params["store_id"][0], "store") if params.get("store_id") and params["store_id"][0] else None
                report = calculate_payroll(params.get("start", [""])[0], params.get("end", [""])[0], store)
                out = io.StringIO()
                writer = csv.writer(out)
                writer.writerow(["Employee", "Actual hours", "Overtime hours", "Straight pay", "Overtime premium", "Estimated gross pay", "Entries"])
                for row in report["employees"]:
                    safe_name = "'" + row["name"] if row["name"].startswith(("=", "+", "-", "@", "\t", "\r")) else row["name"]
                    writer.writerow([safe_name, f'{row["minutes"]/60:.2f}', f'{row["overtime_minutes"]/60:.2f}',
                                     f'{row["base_cents"]/100:.2f}', f'{row["premium_cents"]/100:.2f}',
                                     f'{row["gross_cents"]/100:.2f}', row["entry_count"]])
                return self.send_bytes(200, out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                                       headers={"Content-Disposition": f'attachment; filename="shiftledger-pay-{report["start"]}-{report["end"]}.csv"'})
            if path == "/api/stores" and method == "POST":
                body = self.read_json()
                name = require_string(body.get("name", ""), "Store name", 100)
                location = require_string(body.get("location", ""), "Location", 200, True)
                color = body.get("color", "#3c8d82")
                if not isinstance(color, str) or len(color) != 7 or color[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in color[1:]):
                    raise InputError("Invalid color")
                with db() as con:
                    with con:
                        if body.get("id"):
                            rid = positive_id(body["id"], "store")
                            authorize_store(user, rid)
                            ensure_record(con, "stores", rid)
                            active = boolean(body.get("active", True))
                            con.execute("UPDATE stores SET name=?,location=?,color=?,active=? WHERE id=?", (name, location, color, active, rid))
                            audit(con, user["username"], "update", "store", rid, body)
                        else:
                            rid = con.execute("INSERT INTO stores(name,location,color,created_at) VALUES(?,?,?,?)",
                                              (name, location, color, utc_now())).lastrowid
                            if user["role"] != "owner":
                                # A manager automatically gets access to stores they create.
                                con.execute("INSERT INTO user_stores(user_id,store_id) VALUES(?,?)", (user["id"], rid))
                            audit(con, user["username"], "create", "store", rid, body)
                return self.json(200, {"id": rid})
            if path == "/api/employees" and method == "POST":
                body = self.read_json()
                name = require_string(body.get("name", ""), "Employee name", 120)
                role = require_string(body.get("role", ""), "Role", 100, True)
                rate = cents(body.get("rate"))
                max_days = integer_range(body.get("max_days", 5), "Maximum days per week", 1, 7)
                store_ids = body.get("store_ids", [])
                if not isinstance(store_ids, list) or not store_ids or len(store_ids) > 100:
                    raise InputError("Select at least one store")
                store_ids = sorted(set(positive_id(s, "store") for s in store_ids))
                manager = user["role"] != "owner"
                for s in store_ids:
                    authorize_store(user, s)
                with db() as con:
                    with con:
                        for s in store_ids:
                            ensure_record(con, "stores", s, True)
                        if body.get("id"):
                            rid = positive_id(body["id"], "employee")
                            old_employee = ensure_record(con, "employees", rid)
                            if not can_manage_employee(con, user, rid):
                                raise PermissionError("You do not manage this employee")
                            if manager:
                                # Keep assignments at stores this manager cannot see.
                                store_ids = sorted(set(store_ids) | {r["store_id"] for r in rows(con,
                                    "SELECT store_id FROM employee_stores WHERE employee_id=?", (rid,))
                                    if r["store_id"] not in user["store_ids"]})
                            prior_rates = {r["store_id"]:r["rate_cents"] for r in rows(con, "SELECT store_id,rate_cents FROM employee_stores WHERE employee_id=?", (rid,))}
                            active = boolean(body.get("active", True))
                            con.execute("UPDATE employees SET name=?,role=?,rate_cents=?,max_days=?,active=? WHERE id=?", (name, role, rate, max_days, active, rid))
                            con.execute("DELETE FROM employee_stores WHERE employee_id=?", (rid,))
                            action = "update"
                        else:
                            rid = con.execute("INSERT INTO employees(name,role,rate_cents,max_days,created_at) VALUES(?,?,?,?,?)",
                                              (name, role, rate, max_days, utc_now())).lastrowid
                            action = "create"
                            prior_rates = {}
                        changed_rate = action == "update" and rate != old_employee["rate_cents"]
                        def new_rate(s):
                            # A manager's default-rate change only reprices their own stores.
                            if changed_rate and (not manager or s in user["store_ids"]):
                                return rate
                            if manager and action == "update" and s not in user["store_ids"] and prior_rates.get(s) is None:
                                return old_employee["rate_cents"]  # freeze other stores' effective rate
                            return prior_rates.get(s, rate)
                        con.executemany("INSERT INTO employee_stores(employee_id,store_id,rate_cents) VALUES(?,?,?)",
                                        ((rid, s, new_rate(s)) for s in store_ids))
                        audit(con, user["username"], action, "employee", rid, {"name": name, "role": role, "rate_cents": rate, "max_days": max_days, "store_ids": store_ids})
                return self.json(200, {"id": rid})
            if path == "/api/availability" and method == "POST":
                body = self.read_json()
                employee_id = positive_id(body.get("employee_id"), "employee")
                availability = availability_payload(body.get("weekdays"))
                with db() as con:
                    with con:
                        ensure_record(con, "employees", employee_id)
                        if not can_manage_employee(con, user, employee_id):
                            raise PermissionError("You do not manage this employee")
                        previous = con.execute("SELECT availability_json FROM employees WHERE id=?", (employee_id,)).fetchone()[0]
                        con.execute("UPDATE employees SET availability_json=? WHERE id=?", (availability, employee_id))
                        audit(con, user["username"], "update_availability", "employee", employee_id,
                              {"previous": json.loads(previous), "new": json.loads(availability)})
                return self.json(200, {"ok": True})
            if path == "/api/store-rate" and method == "POST":
                body = self.read_json()
                employee_id = positive_id(body.get("employee_id"), "employee")
                store_id = positive_id(body.get("store_id"), "store")
                rate = cents(body.get("rate"))
                authorize_store(user, store_id)
                with db() as con:
                    with con:
                        ensure_employee_store(con, employee_id, store_id)
                        previous = store_rate(con, employee_id, store_id)
                        con.execute("UPDATE employee_stores SET rate_cents=? WHERE employee_id=? AND store_id=?",
                                    (rate, employee_id, store_id))
                        audit(con, user["username"], "update_store_rate", "employee", employee_id,
                              {"store_id": store_id, "before_cents": previous, "after_cents": rate})
                return self.json(200, {"ok": True})
            if path == "/api/shifts" and method == "POST":
                body = self.read_json()
                emp, store, day, start, end, pause, notes, status = payload_for_shift(body)
                if status not in ("draft", "published"):
                    raise InputError("Invalid schedule status")
                with db() as con:
                    with con:
                        authorize_store(user, store)
                        ensure_employee_store(con, emp, store)
                        if body.get("id"):
                            rid = positive_id(body["id"], "shift")
                            old = ensure_record(con, "shifts", rid)
                            authorize_store(user, old["store_id"])
                            validate_employee_shift(con, emp, day, start, end, skip_id=rid)
                            con.execute("""UPDATE shifts SET employee_id=?,store_id=?,shift_date=?,start_time=?,end_time=?,
                                           break_minutes=?,notes=?,status=? WHERE id=?""",
                                        (emp, store, day, start, end, pause, notes, status, rid))
                            action = "update"
                        else:
                            validate_employee_shift(con, emp, day, start, end)
                            rid = con.execute("""INSERT INTO shifts(employee_id,store_id,shift_date,start_time,end_time,
                                                    break_minutes,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                                              (emp, store, day, start, end, pause, notes, status, utc_now())).lastrowid
                            action = "create"
                        audit(con, user["username"], action, "shift", rid, body)
                return self.json(200, {"id": rid})
            if path == "/api/shifts/delete" and method == "POST":
                body = self.read_json()
                rid = positive_id(body.get("id"), "shift")
                with db() as con:
                    with con:
                        old = ensure_record(con, "shifts", rid)
                        authorize_store(user, old["store_id"])
                        con.execute("UPDATE shifts SET deleted_at=? WHERE id=?", (utc_now(), rid))
                        audit(con, user["username"], "archive", "shift", rid, old)
                return self.json(200, {"ok": True})
            if path == "/api/shifts/bulk" and method == "POST":
                body = self.read_json()
                start = as_date(body.get("start_date"))
                end = as_date(body.get("end_date"))
                if end < start or (end - start).days > 183:
                    raise InputError("Repeating schedules can cover up to 184 days")
                weekdays = body.get("weekdays", [])
                if not isinstance(weekdays, list) or not weekdays or any(w not in range(7) or type(w) is not int for w in weekdays):
                    raise InputError("Select at least one weekday")
                emp, store, _, st, en, pause, notes, status = payload_for_shift({**body, "date": start.isoformat()})
                if status not in ("draft", "published"):
                    raise InputError("Invalid status")
                count = 0
                with db() as con:
                    with con:
                        authorize_store(user, store)
                        ensure_employee_store(con, emp, store)
                        current = start
                        while current <= end:
                            if current.weekday() in weekdays:
                                dup = con.execute("""SELECT id FROM shifts WHERE employee_id=? AND store_id=? AND shift_date=?
                                      AND start_time=? AND end_time=? AND deleted_at IS NULL""",
                                                  (emp, store, current.isoformat(), st, en)).fetchone()
                                if not dup:
                                    validate_employee_shift(con, emp, current.isoformat(), st, en)
                                    con.execute("""INSERT INTO shifts(employee_id,store_id,shift_date,start_time,end_time,break_minutes,
                                                 notes,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                                                (emp, store, current.isoformat(), st, en, pause, notes, status, utc_now()))
                                    count += 1
                            current += timedelta(days=1)
                        audit(con, user["username"], "bulk_create", "shift", None, {"created": count, **body})
                return self.json(200, {"created": count})
            if path == "/api/shifts/publish" and method == "POST":
                body = self.read_json()
                start, end = as_date(body.get("start")), as_date(body.get("end"))
                store = positive_id(body.get("store_id"), "store")
                authorize_store(user, store)
                if end < start or (end - start).days > 366:
                    raise InputError("Invalid publish date range")
                with db() as con:
                    with con:
                        cursor = con.execute("""UPDATE shifts SET status='published' WHERE shift_date BETWEEN ? AND ? AND
                                          store_id=? AND deleted_at IS NULL AND status='draft'""",
                                             (start.isoformat(), end.isoformat(), store))
                        count = cursor.rowcount
                        audit(con, user["username"], "publish", "shift", None, {"start": start.isoformat(), "end": end.isoformat(), "store_id": store, "count": count})
                return self.json(200, {"published": count})
            if path == "/api/entries" and method == "POST":
                body = self.read_json()
                with db() as con:
                    with con:
                        old = ensure_record(con, "time_entries", positive_id(body["id"], "entry")) if body.get("id") else None
                        emp, store, day, category, start, end, pause, minutes, rate, notes = entry_payload(body, old)
                        authorize_store(user, store)
                        if old:
                            authorize_store(user, old["store_id"])
                        if user["role"] != "owner" and "rate" in body:
                            raise InputError("Use the employee pay-rate editor to change rates")
                        ensure_employee_store(con, emp, store)
                        if old and (old["employee_id"] != emp or old["store_id"] != store):
                            rate = store_rate(con, emp, store)
                        if rate is None:
                            rate = store_rate(con, emp, store)
                        if old:
                            rid = old["id"]
                            con.execute("""UPDATE time_entries SET employee_id=?,store_id=?,entry_date=?,category=?,start_time=?,
                                end_time=?,break_minutes=?,minutes=?,rate_cents=?,notes=? WHERE id=?""",
                                        (emp, store, day, category, start, end, pause, minutes, rate, notes, rid))
                            action = "update"
                        else:
                            rid = con.execute("""INSERT INTO time_entries(employee_id,store_id,entry_date,category,start_time,end_time,
                                break_minutes,minutes,rate_cents,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                              (emp, store, day, category, start, end, pause, minutes, rate, notes, utc_now())).lastrowid
                            action = "create"
                        audit(con, user["username"], action, "time_entry", rid, {"before": old, "after": {**body, "minutes": minutes, "rate_cents": rate}})
                return self.json(200, {"id": rid})
            if path == "/api/entries/from-shift" and method == "POST":
                body = self.read_json()
                rid = positive_id(body.get("shift_id"), "shift")
                with db() as con:
                    with con:
                        shift = ensure_record(con, "shifts", rid)
                        authorize_store(user, shift["store_id"])
                        ensure_employee_store(con, shift["employee_id"], shift["store_id"])
                        if con.execute("SELECT id FROM time_entries WHERE shift_id=?", (rid,)).fetchone():
                            raise InputError("This shift already has a time entry; edit it in Time & tasks")
                        rate = store_rate(con, shift["employee_id"], shift["store_id"])
                        minutes = shift_minutes(shift["start_time"], shift["end_time"], shift["break_minutes"])
                        eid = con.execute("""INSERT INTO time_entries(employee_id,store_id,entry_date,category,start_time,end_time,
                              break_minutes,minutes,rate_cents,notes,shift_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                          (shift["employee_id"], shift["store_id"], shift["shift_date"], "Shift",
                                           shift["start_time"], shift["end_time"], shift["break_minutes"], minutes, rate,
                                           shift["notes"], rid, utc_now())).lastrowid
                        audit(con, user["username"], "record_shift", "time_entry", eid, {"shift_id": rid, "minutes": minutes})
                return self.json(200, {"id": eid})
            if path == "/api/entries/delete" and method == "POST":
                body = self.read_json()
                rid = positive_id(body.get("id"), "entry")
                with db() as con:
                    with con:
                        old = ensure_record(con, "time_entries", rid)
                        authorize_store(user, old["store_id"])
                        con.execute("UPDATE time_entries SET deleted_at=? WHERE id=?", (utc_now(), rid))
                        audit(con, user["username"], "archive", "time_entry", rid, old)
                return self.json(200, {"ok": True})
            if path == "/api/users" and method == "GET":
                owner_required(user)
                with db() as con:
                    return self.json(200, {"users": rows(con, "SELECT id,username,role,active,created_at FROM users ORDER BY role,username"),
                                           "user_stores": rows(con, "SELECT * FROM user_stores")})
            if path == "/api/users" and method == "POST":
                owner_required(user)
                body = self.read_json()
                username = require_string(body.get("username", ""), "Username", 80)
                if not username.replace("_", "").replace("-", "").isalnum():
                    raise InputError("Username may contain letters, numbers, _ and -")
                store_ids = body.get("store_ids", [])
                if not isinstance(store_ids, list):
                    raise InputError("Invalid store assignments")
                store_ids = sorted(set(positive_id(x, "store") for x in store_ids))
                if not store_ids:
                    raise InputError("Assign at least one store to the manager")
                password = body.get("password", "")
                if not isinstance(password, str):
                    raise InputError("Invalid password")
                with db() as con:
                    with con:
                        for store in store_ids:
                            ensure_record(con, "stores", store, True)
                        if body.get("id"):
                            rid = positive_id(body["id"], "manager")
                            old = con.execute("SELECT id,role FROM users WHERE id=?", (rid,)).fetchone()
                            if not old or old["role"] != "manager":
                                raise InputError("Only managers can be edited here")
                            active = boolean(body.get("active", True))
                            con.execute("UPDATE users SET username=?,active=? WHERE id=?", (username, active, rid))
                            if password:
                                con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), rid))
                                con.execute("DELETE FROM sessions WHERE user_id=?", (rid,))
                            if not active:
                                con.execute("DELETE FROM sessions WHERE user_id=?", (rid,))
                            con.execute("DELETE FROM user_stores WHERE user_id=?", (rid,))
                            action = "update"
                        else:
                            if not password:
                                raise InputError("Set an initial password")
                            rid = con.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,'manager',?)",
                                              (username, hash_password(password), utc_now())).lastrowid
                            action = "create"
                        con.executemany("INSERT INTO user_stores(user_id,store_id) VALUES(?,?)", ((rid, sid) for sid in store_ids))
                        audit(con, user["username"], action, "manager", rid, {"username": username, "store_ids": store_ids})
                return self.json(200, {"id": rid})
            if path == "/api/change-password" and method == "POST":
                body = self.read_json()
                old_pw = body.get("current_password", "")
                new_pw = body.get("new_password", "")
                if not isinstance(new_pw, str) or not new_pw:
                    raise InputError("Enter a new password")
                with db() as con:
                    with con:
                        row = con.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
                        if not isinstance(old_pw, str) or not check_password(old_pw, row["password_hash"]):
                            raise InputError("Current password is incorrect")
                        con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_pw), user["id"]))
                        con.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                        audit(con, user["username"], "password_change", "user", user["id"], {})
                return self.json(200, {"ok": True, "sign_in_again": True}, self.cookie_header("", 0))
            return self.json(404, {"error": "Not found"})
        except InputError as error:
            return self.json(400, {"error": str(error)})
        except sqlite3.IntegrityError:
            return self.json(409, {"error": "Conflicting or duplicate record"})
        except PermissionError as error:
            return self.json(403, {"error": str(error)})
        except Exception as error:
            print(f"API exception: {error!r}", flush=True)
            return self.json(500, {"error": "Internal server error"})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            return self.api("GET")
        filename = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js"}.get(path)
        if not filename:
            return self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
        content = (APP_DIR / "static" / filename).read_bytes()
        if filename == "index.html":
            # Cache-bust: give app.js/app.css a content-hash URL so CDNs/tunnels
            # (e.g. Cloudflare) can never serve stale code after an update.
            content = content.replace(b'"/app.css"', b'"/app.css?v=' + asset_version("app.css") + b'"')
            content = content.replace(b'"/app.js"', b'"/app.js?v=' + asset_version("app.js") + b'"')
        ctype = "text/html; charset=utf-8" if filename.endswith("html") else ("text/css; charset=utf-8" if filename.endswith("css") else "text/javascript; charset=utf-8")
        return self.send_bytes(200, content, ctype)

    def do_POST(self):
        return self.api("POST") if urlsplit(self.path).path.startswith("/api/") else self.send_bytes(404, b"Not found", "text/plain")


def serve():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"ShiftLedger listening on 0.0.0.0:{PORT}; database in {DB_FILE}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve()
